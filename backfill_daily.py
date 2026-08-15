#!/usr/bin/env python3
"""
Backfill daily_totals from ONE per-day campaign report (time_unit=DAILY), as far
back as the Reporting API allows (~95 days). Gives the calendar-month / YTD views
real per-day history instead of the single banked day per nightly run.

Also worth re-running weekly: sales use the 30-day attribution window, so a day's
sales keep growing for a month — a refresh trues-up the recent weeks.

Read-only vs Amazon (it's a report); writes only the local daily_totals table.

Run:  python3 backfill_daily.py            # last 92 days for the active market
      ADS_MARKET=DE python3 backfill_daily.py --days 60
"""

import datetime
import sys
import time
import traceback

import db
from ads_client import AdsClient
from daily_metrics import _seattle_today, COLS

POLL_SECS = 30
MAX_WAIT = 2400
MAX_DAYS = 92          # SP reporting retention is ~95 days; stay inside it


def main():
    args = sys.argv[1:]
    days = MAX_DAYS
    if "--days" in args:
        try:
            days = min(MAX_DAYS, int(args[args.index("--days") + 1]))
        except (IndexError, ValueError):
            pass
    today = _seattle_today()
    window_end = today - datetime.timedelta(days=1)
    window_start = today - datetime.timedelta(days=days)

    # Amazon caps DAILY reports at 31 days — chunk the window, poll all in parallel
    chunks = []
    cursor = window_start
    while cursor <= window_end:
        chunk_end = min(cursor + datetime.timedelta(days=30), window_end)
        chunks.append((cursor.isoformat(), chunk_end.isoformat()))
        cursor = chunk_end + datetime.timedelta(days=1)

    client = AdsClient()
    conn = db.connect()
    print(f"Backfill daily_totals [{client.market}]: {window_start} → {window_end} "
          f"({len(chunks)} DAILY reports, ≤31 days each)")

    active = {}   # (start, end) -> report id
    for start, end in chunks:
        key = f"backfill_{start}"
        job = db.get_report_job(conn, key)
        if job and job[4] == end and job[5] == 0 and job[2] not in ("FAILED", "CANCELLED"):
            active[(start, end)] = job[1]
            print(f"  {start}→{end}: resuming {job[1]}")
        else:
            # campaignId/campaignName are needed for the per-campaign bank; they only
            # appear in the output when explicitly requested as columns.
            rid = client.create_report("spCampaigns",
                                       COLS + ["date", "campaignId", "campaignName"],
                                       ["campaign"], start, end, time_unit="DAILY")
            db.save_report_job(conn, key, rid, end)
            active[(start, end)] = rid
            print(f"  {start}→{end}: requested ({rid})")

    waited = 0
    banked = 0
    pending = dict(active)
    while pending and waited <= MAX_WAIT:
        for span in list(pending):
            start, end = span
            key = f"backfill_{start}"
            status, url = client.get_report(pending[span])
            db.set_report_status(conn, key, status)
            if status == "COMPLETED":
                per_day = {}
                per_camp = {}   # (date, campaign_id) -> [cost,sales,orders,impr,clicks,units, name]
                for r in client.download_gzip_json(url):
                    d = r.get("date")
                    if not d:
                        continue
                    cost = r.get("cost") or 0
                    sales = r.get("sales30d") or r.get("sales") or 0
                    orders = int(r.get("purchases30d") or r.get("purchases") or 0)
                    impr = int(r.get("impressions") or 0)
                    clicks = int(r.get("clicks") or 0)
                    units = int(r.get("unitsSoldClicks30d") or r.get("unitsSold") or r.get("units") or 0)
                    agg = per_day.setdefault(d, [0.0, 0.0, 0, 0, 0, 0])
                    agg[0] += cost; agg[1] += sales; agg[2] += orders
                    agg[3] += impr; agg[4] += clicks; agg[5] += units
                    cid = r.get("campaignId")
                    if cid is not None:
                        c = per_camp.setdefault((d, str(cid)),
                                                [0.0, 0.0, 0, 0, 0, 0, r.get("campaignName")])
                        c[0] += cost; c[1] += sales; c[2] += orders
                        c[3] += impr; c[4] += clicks; c[5] += units
                for d, (cost, sales, orders, impressions, clicks, units) in sorted(per_day.items()):
                    db.store_daily_total(conn, d, cost, sales, orders,
                                         impressions=impressions, clicks=clicks, units=units)
                db.store_campaign_daily(conn, [
                    (d, cid, v[6], round(v[0], 2), round(v[1], 2), v[2], v[3], v[4], v[5])
                    for (d, cid), v in per_camp.items()])
                db.set_report_status(conn, key, "COMPLETED", downloaded=1)
                banked += len(per_day)
                print(f"  {start}→{end}: banked {len(per_day)} days")
                del pending[span]
            elif status in ("FAILED", "CANCELLED"):
                print(f"  {start}→{end}: {status}")
                del pending[span]
        if pending:
            print(f"  …generating {len(pending)} report(s) ({waited}s)")
            time.sleep(POLL_SECS)
            waited += POLL_SECS

    if pending:
        print(f"  {len(pending)} report(s) still generating — saved; re-run to resume.")
    print(f"Done: {banked} days banked.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
