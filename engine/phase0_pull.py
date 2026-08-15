#!/usr/bin/env python3
"""
PHASE 0 — read-only pull. Makes ZERO changes to the Amazon account.

Pulls (US Merch profile):
  1. campaign structure (campaigns, ad groups)
  2. 30-day performance reports: campaigns, targeting, search terms
Stores everything in ads_data.sqlite and prints a summary of your real numbers.

Big accounts: reports can take a while to generate server-side. This script
requests all three at once (they generate in parallel), then polls together.
It is RESUMABLE — if reports aren't ready, just run it again and it picks up
the saved report IDs instead of starting over.

Run:  python3 phase0_pull.py
First time:  pip3 install requests --break-system-packages
"""

import datetime
import sys
import time
import traceback

import db
import markets
from ads_client import AdsClient

END = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
START = (datetime.date.today() - datetime.timedelta(days=31)).isoformat()

# The DAILY targeting report banks TRUE per-day rows into target_daily, which
# is what the rules language needs for rolling windows. Seven days rather than
# one, so a night this job dies heals itself on the next run instead of leaving
# a permanent hole in the window.
DAILY_START = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()

SP = "application/vnd.sp{}.v3+json"

REPORTS = {
    "campaigns": dict(
        report_type_id="spCampaigns", group_by=["campaign"],
        columns=["campaignId", "campaignName", "campaignStatus",
                 "impressions", "clicks", "cost", "purchases30d", "sales30d"]),
    "targeting": dict(
        report_type_id="spTargeting", group_by=["targeting"],
        columns=["campaignId", "adGroupId", "keywordId", "targeting", "matchType",
                 "impressions", "clicks", "cost", "purchases30d", "sales30d"]),
    "searchterm": dict(
        report_type_id="spSearchTerm", group_by=["searchTerm"],
        columns=["campaignId", "adGroupId", "searchTerm", "targeting", "matchType",
                 "impressions", "clicks", "cost", "purchases30d", "sales30d"]),
    # MEASURED cross-purchase: which ASIN was bought after clicking which ad.
    # Rows where purchasedAsin != advertisedAsin are halo Amazon attributes but
    # the campaign/targeting reports never surface.
    "purchased": dict(
        report_type_id="spPurchasedProduct", group_by=["asin"],
        columns=["campaignId", "adGroupId", "keywordId", "keyword", "keywordType",
                 "matchType", "advertisedAsin", "purchasedAsin",
                 "unitsSoldClicks30d", "sales30d", "purchases30d",
                 "unitsSoldOtherSku30d", "salesOtherSku30d", "purchasesOtherSku30d"]),
    # True per-day per-target rows. Every other report here is a SUMMARY over
    # the trailing 31 days; this one asks for the days themselves. It rides in
    # the same batch because reports generate in parallel server-side, so a
    # fifth costs almost no extra wall-clock — and a separate serial step would
    # add about 25 minutes per market to a run that polls for only 25.
    "targeting_daily": dict(
        report_type_id="spTargeting", group_by=["targeting"],
        time_unit="DAILY", start=DAILY_START, end=END,
        columns=["date", "campaignId", "adGroupId", "keywordId", "targeting",
                 "matchType", "impressions", "clicks", "cost",
                 "purchases30d", "sales30d"]),
}

STORERS = {
    "campaigns": db.store_campaign_perf,
    "targeting": db.store_targeting_perf,
    "searchterm": db.store_search_term_perf,
    "purchased": db.store_purchased_product,
    "targeting_daily": db.store_target_daily,
}

# KENP = Kindle Edition Normalized Pages read through Kindle Unlimited, plus the
# royalty they earn. KDP-ONLY: apparel profiles have no KENP, so we request these
# columns only for the KDP profile and leave every Merch pull exactly as it was
# (you cannot regress a request you never change). The daily targeting report is
# deliberately NOT here — target_daily has no kenp column this pass, so rolling
# `kenp IN LAST N DAYS` windows are future work.
KENP_COLUMNS = ["kindleEditionNormalizedPagesRead14d",
                "kindleEditionNormalizedPagesRoyalties14d"]
KENP_REPORTS = {"campaigns", "targeting", "searchterm"}

POLL_SECS = 30
MAX_WAIT = 1500   # 25 min; can Ctrl-C and re-run later (resumable)


def section(t):
    print("\n" + "=" * 64 + f"\n{t}\n" + "=" * 64)


def pull_structure(client, conn):
    """Pull campaigns + ad groups + the target/keyword bid mirror. Returns the
    list of parts that FAILED — the caller exits non-zero on any, so the
    nightly's step tracker hears about it (a printed 'FAILED' with exit 0 is
    exactly the silence the 2026-08 instrumentation work was about)."""
    failures = []
    section("1. Campaign structure")
    try:
        camps = client.list_all("/sp/campaigns/list", SP.format("Campaign"), "campaigns")
        db.upsert_campaigns(conn, camps)
        db.log_pull(conn, "campaigns", len(camps))
        print(f"  campaigns: {len(camps)}")
    except Exception as e:
        print(f"  campaigns FAILED: {e}")
        failures.append("campaigns")
    try:
        ags = client.list_all("/sp/adGroups/list", SP.format("AdGroup"), "adGroups")
        db.upsert_ad_groups(conn, ags)
        db.log_pull(conn, "adGroups", len(ags))
        print(f"  ad groups: {len(ags)}")
    except Exception as e:
        print(f"  ad groups FAILED: {e}")
        failures.append("ad groups")
    try:
        # per-target/keyword bid + state mirror — the reports carry neither.
        # Feeds the DSL's `bid`/`state` fields and the app's Targets bid column.
        clauses = client.list_all("/sp/targets/list", SP.format("TargetingClause"),
                                  "targetingClauses")
        kws = client.list_all("/sp/keywords/list", SP.format("Keyword"), "keywords")
        n = db.store_targets(conn, db.target_mirror_rows(clauses, kws))
        db.log_pull(conn, "targets", n)
        print(f"  targets mirror: {len(clauses)} clauses + {len(kws)} keywords")
    except Exception as e:
        print(f"  targets mirror FAILED: {e}")
        failures.append("targets mirror")
    return failures


def recover_leftover_reports(client, conn):
    """Bank reports Amazon finished AFTER a previous run stopped polling.

    A report that outruns MAX_WAIT is deferred — but the next day's run
    requests a fresh window and used to silently abandon the old job even
    when Amazon completed it minutes after we hung up. UK/DE sat days stale
    exactly this way. One status check per leftover costs seconds and banks
    the missed snapshot under its OWN window date. Dead jobs are left for
    db.expire_stale_report_jobs to retire."""
    recovered = 0
    printed = False
    for key, cfg in REPORTS.items():
        job = db.get_report_job(conn, key)
        if not job:
            continue
        _, rid, status, _requested, window_end, downloaded = job
        if downloaded or status in ("FAILED", "CANCELLED", "EXPIRED"):
            continue
        r_end = cfg.get("end", END)      # each report's OWN window end, per ensure_report_jobs
        if window_end == r_end:
            continue                     # today's own job — the resume path owns it
        if not printed:
            section("0. Leftover reports from a previous window")
            printed = True
        try:
            st, url = client.get_report(rid)
        except Exception as e:
            print(f"  {key} ({window_end}): status check failed: {e}")
            continue
        if st == "COMPLETED":
            try:
                rows = client.download_gzip_json(url)
                n = STORERS[key](conn, rows, window_end)
            except Exception as e:
                print(f"  {key}: RECOVERY STORE FAILED — {type(e).__name__}: {e}")
                continue
            db.set_report_status(conn, key, "COMPLETED", downloaded=1)
            db.log_pull(conn, f"report:{key}:recovered", n)
            print(f"  {key}: recovered the {window_end} snapshot — stored {n} rows")
            recovered += 1
        else:
            db.set_report_status(conn, key, st or "UNKNOWN")
            print(f"  {key} ({window_end}): still {st or 'unreachable'} — retrying tomorrow")
    return recovered


def ensure_report_jobs(client, conn):
    """Reuse a saved, not-yet-downloaded report for this window; otherwise create new."""
    active = {}   # type -> report_id
    for key, cfg in REPORTS.items():
        # Most reports share the 31-day SUMMARY window. targeting_daily asks for
        # a shorter one, so each config may override start/end.
        r_start = cfg.get("start", START)
        r_end = cfg.get("end", END)
        job = db.get_report_job(conn, key)
        if job and job[4] == r_end and job[5] == 0 and job[2] not in ("FAILED", "CANCELLED"):
            active[key] = job[1]
            print(f"  {key}: resuming saved report {job[1]}")
            continue
        try:
            columns = cfg["columns"]
            if markets.is_kdp() and key in KENP_REPORTS:
                columns = columns + KENP_COLUMNS
            rid = client.create_report(cfg["report_type_id"], columns,
                                       cfg["group_by"], r_start, r_end,
                                       time_unit=cfg.get("time_unit", "SUMMARY"))
            db.save_report_job(conn, key, rid, r_end)
            active[key] = rid
            print(f"  {key}: requested ({rid})")
        except Exception as e:
            print(f"  {key} CREATE FAILED: {e}")
    return active


def poll_and_store(client, conn, active):
    pending = dict(active)
    failed = []
    waited = 0
    while pending and waited <= MAX_WAIT:
        for key in list(pending):
            rid = pending[key]
            try:
                status, url = client.get_report(rid)
            except Exception as e:
                print(f"  {key}: status check error: {e}")
                continue
            db.set_report_status(conn, key, status)
            if status == "COMPLETED":
                # Isolate per report. A failed store used to raise straight out of
                # main(), so when the big targeting write died with "disk I/O
                # error" the still-pending search-term report was abandoned too and
                # BOTH tables went stale. One bad report must not cost the others.
                try:
                    rows = client.download_gzip_json(url)
                    n = STORERS[key](conn, rows, END)
                except Exception as e:
                    print(f"  {key}: STORE FAILED — {type(e).__name__}: {e}")
                    print(f"     {key} is NOT banked this run; other reports continue.")
                    failed.append(key)
                    del pending[key]
                    continue
                db.set_report_status(conn, key, "COMPLETED", downloaded=1)
                db.log_pull(conn, f"report:{key}", n)
                print(f"  {key}: COMPLETED — stored {n} rows")
                del pending[key]
            elif status in ("FAILED", "CANCELLED"):
                print(f"  {key}: {status}")
                del pending[key]
        if pending:
            print(f"  …still generating: {', '.join(pending)}  ({waited}s elapsed)")
            time.sleep(POLL_SECS)
            waited += POLL_SECS
    return pending, failed


def summary(conn):
    section("3. Your numbers (last 30 days)")
    cur = conn.cursor()
    row = cur.execute(
        "SELECT COUNT(*),SUM(cost),SUM(sales),SUM(clicks),SUM(orders) FROM campaign_perf WHERE date=?",
        (END,)).fetchone()
    n, cost, sales, clicks, orders = (row or (0, 0, 0, 0, 0))
    cost = cost or 0; sales = sales or 0; clicks = clicks or 0; orders = orders or 0
    print(f"  campaigns with data : {n}")
    print(f"  ad spend            : ${cost:,.2f}")
    print(f"  ad sales            : ${sales:,.2f}")
    print(f"  blended ACOS        : {(cost/sales*100):.1f}%" if sales else "  blended ACOS        : n/a")
    print(f"  clicks / orders     : {clicks:,} / {orders:,}")
    print("\n  Top campaigns by spend:")
    for r in cur.execute(
        "SELECT campaign_name,cost,sales,acos FROM campaign_perf WHERE date=? ORDER BY cost DESC LIMIT 8",
        (END,)):
        ac = f"{r[3]*100:.0f}%" if r[3] is not None else "—"
        print(f"    {(r[0] or '')[:42]:42}  spend ${r[1] or 0:8.2f}  sales ${r[2] or 0:8.2f}  ACOS {ac}")
    bleed = cur.execute(
        "SELECT COUNT(*),SUM(cost) FROM search_term_perf WHERE date=? AND orders=0 AND cost>0",
        (END,)).fetchone()
    if bleed and bleed[0]:
        print(f"\n  Search terms spending with 0 sales: {bleed[0]}  (${bleed[1] or 0:,.2f} — negative candidates)")


def main():
    client = AdsClient()
    conn = db.connect()
    print(f"[{client.market}] profile {client.profile_id} | window {START} → {END}")

    structure_failed = pull_structure(client, conn)

    recover_leftover_reports(client, conn)
    db.expire_stale_report_jobs(conn)

    section("2. Performance reports (30 days) — request + poll")
    active = ensure_report_jobs(client, conn)
    leftover, failed = poll_and_store(client, conn, active) if active else ({}, [])

    summary(conn)

    if leftover:
        print(f"\n⚠️ Still generating after {MAX_WAIT//60} min: {', '.join(leftover)}.")
        print("   Just run this script again in a few minutes — it resumes those reports.")
    if failed:
        # Loud, because everything downstream reads these tables and a missing
        # snapshot makes the phases refuse to act (db.snapshot_gate).
        print(f"\n⚠️ STORE FAILED for: {', '.join(failed)} — those tables are NOT"
              f" updated for {END}. Re-run this script to retry.")
    print(f"\nDone. Stored in {db.DB_PATH}. Nothing on Amazon was changed.")
    # Everything ran; now tell the truth about how it went. Deferred reports
    # (leftover) are NORMAL — tomorrow's run resumes them. Structure failures
    # and store failures are not, and the nightly's step tracker only hears
    # about them through the exit code.
    if structure_failed or failed:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted — report IDs are saved; re-run to resume.")
    except Exception:
        traceback.print_exc()
        sys.exit(1)
