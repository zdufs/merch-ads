#!/usr/bin/env python3
"""
Account ad totals for the Discord digest's KPIs: yesterday ('daily') and
month-to-date ('mtd'). Each is a small SUMMARY campaign report (same size as
phase0 — NOT a per-day report), so they generate in the usual few minutes even on
a big account. Requested together and polled in parallel.

Stored in period_totals. Resumable: if a report isn't ready in time, the saved id
is reused next run (the digest falls back to the 30-day number meanwhile).

Ad spend (cost) is exact; sales use the 30-day attribution window (consistent with
the rest of the system).

Run:  python3 daily_metrics.py
"""

import datetime
import sys
import time
import traceback

import db
from ads_client import AdsClient

POLL_SECS = 30
MAX_WAIT = 1500
MAX_GAP_DAYS = 14     # nightly gap-fill reaches back this far; Monday's backfill_daily trues up deeper history
COLS = ["cost", "purchases30d", "sales30d", "impressions", "clicks", "unitsSoldClicks30d"]

# Daily report days are anchored to Seattle / Pacific time for EVERY market, so the
# "yesterday" and month-to-date boundaries line up with Amazon's US ad clock no matter
# where the job runs from. (Amazon still composes each profile's day in its own
# marketplace hours; this only fixes which calendar date counts as "yesterday".)
try:
    from zoneinfo import ZoneInfo
    SEATTLE = ZoneInfo("America/Los_Angeles")
except Exception:                       # pragma: no cover — very old Python / no tzdata
    SEATTLE = None


def _seattle_today():
    if SEATTLE is not None:
        return datetime.datetime.now(SEATTLE).date()
    # fallback: fixed PDT/PST offset (no DST) if zoneinfo is unavailable
    return (datetime.datetime.utcnow() - datetime.timedelta(hours=8)).date()


def totals(rows):
    cost = sum((r.get("cost") or 0) for r in rows)
    sales = sum((r.get("sales30d") or r.get("sales") or 0) for r in rows)
    orders = sum((r.get("purchases30d") or r.get("purchases") or 0) for r in rows)
    impressions = sum((r.get("impressions") or 0) for r in rows)
    clicks = sum((r.get("clicks") or 0) for r in rows)
    units = sum((r.get("unitsSoldClicks30d") or r.get("unitsSold") or r.get("units") or 0) for r in rows)
    return cost, sales, int(orders), int(impressions), int(clicks), int(units)


def _campaign_rows(rows, date):
    """Shape the campaign-grouped report rows for db.store_campaign_daily:
    (date, campaign_id, campaign_name, cost, sales, orders, impressions, clicks, units).

    Rows are summed per campaign_id in case Amazon splits one campaign across
    several rows — the same defensive aggregation backfill_daily.py uses. A row
    with no campaignId is skipped."""
    per_camp = {}
    for r in rows:
        cid = r.get("campaignId")
        if cid is None:
            continue
        c = per_camp.setdefault(str(cid), [0.0, 0.0, 0, 0, 0, 0, r.get("campaignName")])
        c[0] += r.get("cost") or 0
        c[1] += r.get("sales30d") or r.get("sales") or 0
        c[2] += int(r.get("purchases30d") or r.get("purchases") or 0)
        c[3] += int(r.get("impressions") or 0)
        c[4] += int(r.get("clicks") or 0)
        c[5] += int(r.get("unitsSoldClicks30d") or r.get("unitsSold") or r.get("units") or 0)
    return [(date, cid, v[6], round(v[0], 2), round(v[1], 2), v[2], v[3], v[4], v[5])
            for cid, v in per_camp.items()]


def main():
    today = _seattle_today()
    yesterday = (today - datetime.timedelta(days=1)).isoformat()
    month_start = today.replace(day=1).isoformat()
    if yesterday < month_start:
        print("First of the month — no completed days yet."); return

    client = AdsClient()
    conn = db.connect()
    # period -> (key, start, end)
    jobs = {
        "daily": ("daily_day", yesterday, yesterday),
        "mtd":   ("daily_mtd", month_start, yesterday),
    }
    print(f"Daily metrics — daily {yesterday} | mtd {month_start}→{yesterday}")

    # Gap fill for the "days stored" history. daily_metrics banks yesterday, but a
    # market whose freshest-day report outruns the 25-minute poll window (ES/FR on
    # some nights) leaves HOLES in daily_totals — and the abandoned day is never
    # re-fetched, because 'yesterday' advances each run. A hole can sit BELOW the
    # newest banked day (yesterday banks fine while the day before it failed), so
    # look for the earliest MISSING settled day, not just an empty tail. Fill from
    # there to the day before yesterday with one DAILY report; older days generate
    # fast, so it completes even when yesterday's does not. Fires only when a hole
    # exists, reaches back at most MAX_GAP_DAYS (and never before the first banked
    # day, so a recently-launched market doesn't request its pre-launch days);
    # Monday's backfill_daily still trues up deeper history (ON CONFLICT overwrites).
    span = conn.execute("SELECT MIN(date), MAX(date) FROM daily_totals").fetchone()
    if span and span[0]:
        first_banked = datetime.date.fromisoformat(span[0])
        settled_end = today - datetime.timedelta(days=2)   # day before yesterday
        floor = max(first_banked, settled_end - datetime.timedelta(days=MAX_GAP_DAYS))
        have = {r[0] for r in conn.execute(
            "SELECT date FROM daily_totals WHERE date>=? AND date<=?",
            (floor.isoformat(), settled_end.isoformat()))}
        missing, d = [], floor
        while d <= settled_end:
            if d.isoformat() not in have:
                missing.append(d)
            d += datetime.timedelta(days=1)
        if missing:
            gap_start = min(missing).isoformat()
            jobs["backfill"] = ("daily_backfill", gap_start, settled_end.isoformat())
            print(f"  backfill: {len(missing)} missing settled day(s), filling {gap_start}→{settled_end.isoformat()}")

    # request (or resume) both reports
    active = {}
    for period, (key, start, end) in jobs.items():
        job = db.get_report_job(conn, key)
        if job and job[4] == end and job[5] == 0 and job[2] not in ("FAILED", "CANCELLED"):
            active[period] = job[1]
            print(f"  {period}: resuming {job[1]}")
        else:
            # campaignId/campaignName only appear in the report when requested, and
            # the daily report's per-campaign rows bank campaign_daily (mtd ignores
            # them and just sums). Same reason backfill_daily.py adds them. The gap
            # backfill is a per-day DAILY report, so it also needs the date column.
            report_cols = COLS + ["campaignId", "campaignName"]
            time_unit = "SUMMARY"
            if period == "backfill":
                report_cols = COLS + ["date", "campaignId", "campaignName"]
                time_unit = "DAILY"
            rid = client.create_report("spCampaigns", report_cols,
                                       ["campaign"], start, end, time_unit=time_unit)
            db.save_report_job(conn, key, rid, end)
            active[period] = rid
            print(f"  {period}: requested {time_unit} {start}→{end} ({rid})")

    # poll both together
    waited = 0
    pending = dict(active)
    while pending and waited <= MAX_WAIT:
        for period in list(pending):
            key, start, end = jobs[period]
            status, url = client.get_report(pending[period])
            db.set_report_status(conn, key, status)
            if status == "COMPLETED":
                report_rows = client.download_gzip_json(url)
                if period == "backfill":
                    # One row per (day, campaign). Bank each settled day into the
                    # accruing history — the same per-day banking backfill_daily
                    # does, scoped to the recent gap.
                    by_day = {}
                    for r in report_rows:
                        d = r.get("date")
                        if d:
                            by_day.setdefault(d, []).append(r)
                    for d, drows in sorted(by_day.items()):
                        c, s, o, im, cl, u = totals(drows)
                        db.store_daily_total(conn, d, c, s, o,
                                             impressions=im, clicks=cl, units=u)
                        db.store_campaign_daily(conn, _campaign_rows(drows, d))
                    print(f"  backfill: stored {len(by_day)} day(s) {start}→{end}")
                else:
                    cost, sales, orders, impressions, clicks, units = totals(report_rows)
                    db.store_period_total(conn, period, f"{start}→{end}", cost, sales, orders)
                    if period == "daily":     # bank this one day into the accruing history
                        db.store_daily_total(conn, start, cost, sales, orders,
                                             impressions=impressions, clicks=clicks, units=units)
                        # The daily report is already grouped by campaign, so bank the
                        # per-campaign rows too. This keeps campaign_daily fresh every
                        # night instead of only after Monday's backfill — campaign
                        # rolling-window rules (IN LAST n DAYS) read this table and go
                        # silent when it falls behind. Monday's backfill still trues up
                        # 30-day attribution over the same rows (ON CONFLICT overwrites).
                        db.store_campaign_daily(conn, _campaign_rows(report_rows, start))
                    print(f"  {period}: stored — spend ${cost:,.2f}, sales ${sales:,.2f}")
                db.set_report_status(conn, key, "COMPLETED", downloaded=1)
                del pending[period]
            elif status in ("FAILED", "CANCELLED"):
                print(f"  {period}: {status}")
                del pending[period]
        if pending:
            print(f"  …generating: {', '.join(pending)} ({waited}s)")
            time.sleep(POLL_SECS)
            waited += POLL_SECS

    if pending:
        print(f"  still generating: {', '.join(pending)} — saved; next run resumes (digest uses 30-day meanwhile).")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
