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

import argparse
import datetime
import sys
import time
import traceback

import db
import markets
from ads_client import AdsClient

# TRAILING 30, inclusive of both ends. This asked for days=31, which with an
# END of yesterday spans THIRTY-ONE days: on 2026-08-23 it covered 23 July to
# 22 August. Every threshold in the engine — spend floors, click minimums, ACOS
# against break-even — is applied to this window under the name "trailing 30",
# so each was being met on about 3% more evidence than it said.
END = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
START = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()

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


def window_note(market, key, phase, **fields):
    """One greppable evidence line per report, on STDERR.

    The run log carried the market's INTENDED window once per pull — the
    `| window START → END` line — and nothing else. That covers only the three
    trailing-30 snapshot reports; `targeting_daily` asks for a shorter window,
    a RESUMED report was requested on some earlier day whose start is not
    stored, and nothing at all recorded what Amazon actually built.

    docs/open-eu-trailing30-window.md is open on exactly that question: a
    snapshot whose end is a day older reconciles over THIRTY-ONE days of
    `campaign_daily` while a current one reconciles over thirty. Either Amazon
    returns a wider window than it was asked for, or the request is wider than
    the constants say. These lines are what tells the two apart, per market,
    per report, per night.

    STDERR because stdout is the narrative log in a script and the JSON
    envelope everywhere appctl reaches. run_scheduled.sh sends both to the run
    log with PYTHONUNBUFFERED set, so the ordering is the real one.
    """
    bits = " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
    print(f"[window] {market} {key} {phase} {bits}", file=sys.stderr)


def _span_days(start, end):
    """Days a window covers, both ends inclusive — None if either is missing.

    This is the number the whole open question is about: START = today-30 with
    END = today-1 is thirty days, and a snapshot that reconciles over thirty-one
    of them is either a wider report than we asked for or a wider request than
    the constants say.
    """
    try:
        a = datetime.date.fromisoformat(str(start))
        b = datetime.date.fromisoformat(str(end))
    except (TypeError, ValueError):
        return None
    return (b - a).days + 1


def _rows_note(rows):
    """What the returned rows themselves say about the window they cover.

    A SUMMARY report carries no date column, so all it can offer is its row
    count and its spend — and the spend is the number the open doc's table
    compares against `campaign_daily`. A DAILY report carries dates, so it can
    say precisely which days came back.
    """
    note = {"rows": len(rows)}
    try:
        note["cost"] = round(sum(float(r.get("cost") or 0) for r in rows), 2)
    except (TypeError, ValueError, AttributeError):
        pass
    dates = sorted({r.get("date") for r in rows if isinstance(r, dict) and r.get("date")})
    if dates:
        note["row_days"] = len(dates)
        note["row_first"] = dates[0]
        note["row_last"] = dates[-1]
    return note


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
            info = client.get_report_info(rid)
            st, url = info.get("status"), info.get("url")
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
            window_note(client.market, key, "RECOVERED",
                        start=info.get("startDate"), end=info.get("endDate"),
                        days=_span_days(info.get("startDate"), info.get("endDate")),
                        stored_as=window_end, report=rid, **_rows_note(rows))
            recovered += 1
        else:
            db.set_report_status(conn, key, st or "UNKNOWN")
            # NOT "retrying tomorrow". `save_report_job` conflicts on
            # report_type alone, so the moment ensure_report_jobs requests
            # today's window this row is REPLACED and there is nothing left to
            # retry. Whether that happens depends on whether the windows match,
            # and ensure_report_jobs is where that is decided — and where it is
            # now said out loud.
            print(f"  {key} ({window_end}): still {st or 'unreachable'} — "
                  f"left for this run to resume or replace")
    return recovered


def ensure_report_jobs(client, conn):
    """Reuse a saved, not-yet-downloaded report for this window; otherwise create new.

    Returns (active, create_failed). A report Amazon refused to CREATE was
    printed and then forgotten: it is not in `active`, so `poll_and_store` never
    sees it, and it never joined the failed set. Phase 0 printed "Done" and
    exited 0 while the tables that report fills were not updated at all.
    """
    active = {}   # type -> report_id
    create_failed = []
    for key, cfg in REPORTS.items():
        # Most reports share the 31-day SUMMARY window. targeting_daily asks for
        # a shorter one, so each config may override start/end.
        r_start = cfg.get("start", START)
        r_end = cfg.get("end", END)
        job = db.get_report_job(conn, key)
        if job and job[4] == r_end and job[5] == 0 and job[2] not in ("FAILED", "CANCELLED"):
            active[key] = job[1]
            print(f"  {key}: resuming saved report {job[1]}")
            # Only the END is stored with a report job, so the start of a
            # resumed report is not knowable locally. Amazon echoes both when
            # it completes, which is what the RETURNED line reads.
            window_note(client.market, key, "RESUMED", end=job[4], report=job[1])
            continue
        if job and job[5] == 0 and job[2] not in ("FAILED", "CANCELLED") and job[4] != r_end:
            # An older window that Amazon had not finished. The row is keyed on
            # report_type alone, so requesting today's window overwrites it and
            # that report can never be collected. Measured across all seven
            # markets on 2026-08-24 this is not currently happening — every
            # nightly report is COMPLETED and downloaded — but if Amazon ever
            # runs slower than one polling window, this is the line that says
            # so instead of the pipeline going quiet.
            print(f"  {key}: ABANDONING report {job[1]} for window {job[4]} "
                  f"(still {job[2]}) — requesting {r_end} replaces it, and it "
                  f"cannot be collected afterwards")
        try:
            columns = cfg["columns"]
            if markets.is_kdp() and key in KENP_REPORTS:
                columns = columns + KENP_COLUMNS
            unit = cfg.get("time_unit", "SUMMARY")
            rid = client.create_report(cfg["report_type_id"], columns,
                                       cfg["group_by"], r_start, r_end,
                                       time_unit=unit)
            db.save_report_job(conn, key, rid, r_end)
            active[key] = rid
            print(f"  {key}: requested ({rid})")
            # The exact window SENT, per report. The market's `| window` line
            # prints the module constants and so speaks only for the three
            # trailing-30 snapshots; targeting_daily asks for a shorter one.
            window_note(client.market, key, "REQUESTED", start=r_start, end=r_end,
                        unit=unit, days=_span_days(r_start, r_end), report=rid)
        except Exception as e:
            print(f"  {key} CREATE FAILED: {e}")
            create_failed.append(key)
    return active, create_failed


def poll_and_store(client, conn, active, max_wait=MAX_WAIT):
    pending = dict(active)
    failed = []
    waited = 0
    while pending and waited <= max_wait:
        for key in list(pending):
            rid = pending[key]
            try:
                info = client.get_report_info(rid)
                # Reading the reply is part of the status check. Leaving it
                # outside this try turned a 200 that decodes to something other
                # than an object — null, a list — into an AttributeError that
                # aborted the whole pull, where the old code logged it and
                # carried on polling the other reports.
                status, url = info.get("status"), info.get("url")
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
                # What Amazon says it actually built, beside what we asked for.
                # `stored_as` is the date the snapshot is filed under, which is
                # END and not the report's own end — the other half of the open
                # question in docs/open-eu-trailing30-window.md.
                window_note(client.market, key, "RETURNED",
                            start=info.get("startDate"), end=info.get("endDate"),
                            days=_span_days(info.get("startDate"), info.get("endDate")),
                            stored_as=END, report=rid, **_rows_note(rows))
                del pending[key]
            elif status in ("FAILED", "CANCELLED"):
                # Amazon giving up on a report is a FAILURE of this run, not a
                # neutral outcome. It used to be dropped from `pending` and
                # counted nowhere, so the table stayed stale, the process still
                # printed "Done" and exited zero, and the nightly's step tracker
                # recorded a healthy pull.
                print(f"  {key}: {status} — NOT banked this run")
                failed.append(key)
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


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Pull this market's structure and "
                                             "performance reports into its database.")
    # Amazon routinely takes longer than the poll budget, and the report ids are
    # banked either way, so a catch-up wants to ASK for every market first and
    # collect afterwards. Without these two flags that meant editing constants
    # from a wrapper script.
    ap.add_argument("--max-wait", type=int, default=MAX_WAIT, metavar="SECS",
                    help="how long to poll Amazon before deferring the rest "
                         "(default %(default)s; 0 asks and exits)")
    ap.add_argument("--reports-only", action="store_true",
                    help="skip the structure pull and only collect reports — the "
                         "cheap round for a catch-up (the targets mirror is ~9 min)")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    client = AdsClient()
    conn = db.connect()
    print(f"[{client.market}] profile {client.profile_id} | window {START} → {END}")

    structure_failed = pull_structure(client, conn) if not args.reports_only else False
    if args.reports_only:
        print("  (structure pull skipped: --reports-only)")

    recover_leftover_reports(client, conn)
    db.expire_stale_report_jobs(conn)

    section("2. Performance reports (30 days) — request + poll")
    active, create_failed = ensure_report_jobs(client, conn)
    leftover, failed = (poll_and_store(client, conn, active, max_wait=args.max_wait)
                        if active else ({}, []))

    summary(conn)

    if leftover:
        print(f"\n⚠️ Still generating after {args.max_wait//60} min: {', '.join(leftover)}.")
        print("   Just run this script again in a few minutes — it resumes those reports.")
    if create_failed:
        # Never even requested, so poll_and_store never saw these and they used
        # to vanish between the print above and the exit code below.
        print(f"\n⚠️ NOT REQUESTED: {', '.join(create_failed)} — Amazon refused"
              f" the report request itself, so those tables have no new data for"
              f" {END}. Re-run this script to retry.")
    if failed:
        # Loud, because everything downstream reads these tables and a missing
        # snapshot makes the phases refuse to act (db.snapshot_gate).
        print(f"\n⚠️ NOT BANKED: {', '.join(failed)} — those tables are NOT"
              f" updated for {END}. Either the store failed or Amazon marked the"
              f" report FAILED. Re-run this script to retry.")
    print(f"\nDone. Stored in {db.DB_PATH}. Nothing on Amazon was changed.")
    # Everything ran; now tell the truth about how it went. Deferred reports
    # (leftover) are NORMAL — tomorrow's run resumes them. Structure failures
    # and store failures are not, and the nightly's step tracker only hears
    # about them through the exit code.
    if structure_failed or failed or create_failed:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted — report IDs are saved; re-run to resume.")
    except Exception:
        traceback.print_exc()
        sys.exit(1)
