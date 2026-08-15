#!/usr/bin/env python3
"""
Backfill target_daily from DAILY spTargeting reports, as far back as the
Reporting API allows (about 92 days). Gives the rules language real per-day
per-target history instead of the overlapping trailing-30 snapshots in
targeting_perf.

Also worth re-running weekly: sales use the 30-day attribution window, so a
day's sales keep growing for a month. A refresh trues-up the recent weeks.

Read-only against Amazon — it requests a report. Writes only target_daily.

Reports are SLOW. A three-day report measured 24 minutes to generate on the US
account, so MAX_WAIT is generous and every chunk's report id is saved in
report_jobs. A run that times out resumes instead of starting over.

Run:  python3 backfill_target_daily.py               # last 92 days, active market
      ADS_MARKET=DE python3 backfill_target_daily.py --days 35
"""

import datetime
import sys
import time
import traceback

import db
from ads_client import AdsClient

POLL_SECS = 30
MAX_WAIT = 2400        # 40 min, matching backfill_daily.py
CHUNK_DAYS = 31        # Amazon's cap on a DAILY report window

# `date` must be requested explicitly or a DAILY report comes back without it.
COLUMNS = ["date", "campaignId", "adGroupId", "keywordId", "targeting",
           "matchType", "impressions", "clicks", "cost", "purchases30d", "sales30d"]


def chunk_window(start, end, max_days=CHUNK_DAYS):
    """Split an inclusive ISO date range into consecutive chunks of at most
    `max_days`. The chunks tile the range exactly: a gap would leave a hole the
    window gate later refuses to act on, and an overlap would waste a slow
    report."""
    cursor = datetime.date.fromisoformat(start)
    last = datetime.date.fromisoformat(end)
    chunks = []
    while cursor <= last:
        chunk_end = min(cursor + datetime.timedelta(days=max_days - 1), last)
        chunks.append((cursor.isoformat(), chunk_end.isoformat()))
        cursor = chunk_end + datetime.timedelta(days=1)
    return chunks


def main():
    args = sys.argv[1:]
    days = db.MAX_DAILY_WINDOW_DAYS
    if "--days" in args:
        try:
            days = min(db.MAX_DAILY_WINDOW_DAYS, int(args[args.index("--days") + 1]))
        except (IndexError, ValueError):
            pass

    today = datetime.date.today()
    window_end = today - datetime.timedelta(days=1)
    window_start = today - datetime.timedelta(days=days)
    chunks = chunk_window(window_start.isoformat(), window_end.isoformat())

    client = AdsClient()
    conn = db.connect()
    print(f"Backfill target_daily [{client.market}]: {window_start} → {window_end} "
          f"({len(chunks)} DAILY reports, ≤{CHUNK_DAYS} days each)")

    # Assigned before the first append. A create-stage failure used to append
    # to a name Python had not bound yet, so the whole run died on the error
    # path instead of recording the failed chunk.
    failed = []
    active = {}
    for start, end in chunks:
        key = f"target_daily_{start}"
        job = db.get_report_job(conn, key)
        if job and job[4] == end and job[5] == 0 and job[2] not in ("FAILED", "CANCELLED"):
            active[(start, end)] = job[1]
            print(f"  {start}→{end}: resuming {job[1]}")
            continue
        try:
            rid = client.create_report("spTargeting", COLUMNS, ["targeting"],
                                       start, end, time_unit="DAILY")
        except Exception as e:
            print(f"  {start}→{end}: CREATE FAILED: {e}")
            failed.append((start, end))
            continue
        db.save_report_job(conn, key, rid, end)
        active[(start, end)] = rid
        print(f"  {start}→{end}: requested ({rid})")

    pending = dict(active)
    waited = 0
    banked = 0
    while pending and waited <= MAX_WAIT:
        for span in list(pending):
            start, end = span
            key = f"target_daily_{start}"
            try:
                status, url = client.get_report(pending[span])
            except Exception as e:
                print(f"  {start}→{end}: status check error: {e}")
                continue
            db.set_report_status(conn, key, status)
            if status == "COMPLETED":
                # Isolate per chunk. One failed store must not abandon the
                # other chunks still generating.
                try:
                    rows = client.download_gzip_json(url)
                    n = db.store_target_daily(conn, rows)
                except Exception as e:
                    print(f"  {start}→{end}: STORE FAILED — {type(e).__name__}: {e}")
                    failed.append(span)
                    del pending[span]
                    continue
                db.set_report_status(conn, key, "COMPLETED", downloaded=1)
                db.log_pull(conn, f"target_daily:{start}", n)
                banked += n
                print(f"  {start}→{end}: COMPLETED — stored {n} rows")
                del pending[span]
            elif status in ("FAILED", "CANCELLED"):
                print(f"  {start}→{end}: {status}")
                del pending[span]
        if pending:
            print(f"  …still generating: {len(pending)} chunk(s)  ({waited}s elapsed)")
            time.sleep(POLL_SECS)
            waited += POLL_SECS

    covered = conn.execute(
        "SELECT MIN(date), MAX(date), COUNT(DISTINCT date), COUNT(*) FROM target_daily"
    ).fetchone()
    print(f"\nBanked {banked} rows this run. target_daily now covers "
          f"{covered[0]} → {covered[1]} ({covered[2]} days, {covered[3]} rows).")

    if failed:
        # Both stages land here: a report that could not be created, and one
        # that came back but could not be stored.
        print(f"\n⚠️ Failed: {len(failed)} chunk(s).")
    if pending:
        print(f"\n⚠️ Still generating after {MAX_WAIT // 60} min: {len(pending)} chunk(s).")
        print("   Run this script again in a few minutes — it resumes those reports.")
    if failed or pending:
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
