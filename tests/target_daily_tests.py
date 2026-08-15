#!/usr/bin/env python3
"""Per-target daily banking: storer, window maths, and the fail-closed gate.

Run from the Ads folder:  python3 -m unittest tests.target_daily_tests -v
"""

import datetime
import os
import sqlite3
import sys
import types
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["ADS_MARKET"] = "US"

import db  # noqa: E402


def memory_conn():
    """An in-memory DB carrying only the table under test."""
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE target_daily (
        date TEXT, campaign_id TEXT, ad_group_id TEXT,
        targeting TEXT, match_type TEXT, target_id TEXT,
        impressions INTEGER, clicks INTEGER, cost REAL,
        orders INTEGER, sales REAL, acos REAL, pulled_at TEXT,
        PRIMARY KEY (date, campaign_id, ad_group_id, targeting, match_type))""")
    return conn


def report_row(date="2026-08-02", targeting="50s shirt", cost=1.5, sales=0.0,
               clicks=2, impressions=30, purchases=0, campaign=900000000000001,
               ad_group=900000000000002, keyword=900000000000003, match="EXACT"):
    """One row shaped like Amazon's DAILY spTargeting output."""
    return {"date": date, "campaignId": campaign, "adGroupId": ad_group,
            "keywordId": keyword, "targeting": targeting, "matchType": match,
            "impressions": impressions, "clicks": clicks, "cost": cost,
            "purchases30d": purchases, "sales30d": sales}


class Storer(unittest.TestCase):

    def test_row_is_dated_from_its_own_date_field(self):
        """The DAILY report carries one row per day. The row's own `date` is
        the truth — never the report's end date, which would collapse every
        day of the window onto one date."""
        conn = memory_conn()
        db.store_target_daily(conn, [report_row(date="2026-08-02"),
                                     report_row(date="2026-08-03")],
                              end_date="2026-08-04")
        dates = [r[0] for r in conn.execute(
            "SELECT date FROM target_daily ORDER BY date")]
        self.assertEqual(dates, ["2026-08-02", "2026-08-03"])

    def test_acos_is_derived_and_none_without_sales(self):
        conn = memory_conn()
        db.store_target_daily(conn, [
            report_row(targeting="sells", cost=2.0, sales=10.0),
            report_row(targeting="dead", cost=2.0, sales=0.0)])
        got = dict(conn.execute("SELECT targeting, acos FROM target_daily"))
        self.assertEqual(got["sells"], 0.2)
        self.assertIsNone(got["dead"])

    def test_rebanking_replaces_rather_than_duplicates(self):
        """The Monday true-up re-reads days already banked. Attribution has
        grown since, so the new figures must win and the row count must not."""
        conn = memory_conn()
        db.store_target_daily(conn, [report_row(sales=0.0)])
        db.store_target_daily(conn, [report_row(sales=19.99)])
        rows = conn.execute("SELECT COUNT(*), SUM(sales) FROM target_daily").fetchone()
        self.assertEqual(rows[0], 1)
        self.assertEqual(rows[1], 19.99)

    def test_rows_without_a_date_are_skipped(self):
        """A row with no date cannot be banked as a day. Dropping it is
        correct; banking it under a guessed date is not."""
        conn = memory_conn()
        written = db.store_target_daily(conn, [report_row(), {"campaignId": 1}])
        self.assertEqual(written, 1)


class Constants(unittest.TestCase):

    def test_lag_and_cap_are_defined_once(self):
        self.assertEqual(db.DAILY_ATTRIBUTION_LAG_DAYS, 2)
        self.assertEqual(db.MAX_DAILY_WINDOW_DAYS, 92)


TODAY = datetime.date(2026, 8, 6)


def banked(conn, dates, targeting="50s shirt"):
    """Put one row on each named day."""
    for d in dates:
        db.store_target_daily(conn, [report_row(date=d, targeting=targeting)])


class Window(unittest.TestCase):

    def test_window_is_lagged_two_days_and_inclusive(self):
        start, end = db.daily_window(7, today=TODAY)
        self.assertEqual(end, "2026-08-04")      # two days before today
        self.assertEqual(start, "2026-07-29")    # seven days inclusive

    def test_one_day_window_is_a_single_date(self):
        start, end = db.daily_window(1, today=TODAY)
        self.assertEqual((start, end), ("2026-08-04", "2026-08-04"))

    def test_lag_is_overridable(self):
        start, end = db.daily_window(7, lag=0, today=TODAY)
        self.assertEqual((start, end), ("2026-07-31", "2026-08-06"))


class Gate(unittest.TestCase):

    def test_complete_window_passes(self):
        conn = memory_conn()
        banked(conn, ["2026-07-29", "2026-07-30", "2026-07-31", "2026-08-01",
                      "2026-08-02", "2026-08-03", "2026-08-04"])
        gate = db.daily_window_gate(conn, "target_daily", 7, today=TODAY)
        self.assertTrue(gate["ok"], gate["reason"])
        self.assertEqual(gate["days_banked"], 7)

    def test_empty_table_fails_closed(self):
        gate = db.daily_window_gate(memory_conn(), "target_daily", 7, today=TODAY)
        self.assertFalse(gate["ok"])
        self.assertIn("no days banked", gate["reason"])

    def test_a_hole_in_the_middle_fails_closed(self):
        """The failure this gate exists for. Six days summed and called a week
        makes every target look about 14% cheaper and 14% worse-selling than it
        was, and the rules would act on that."""
        conn = memory_conn()
        banked(conn, ["2026-07-29", "2026-07-30", "2026-07-31",
                      "2026-08-02", "2026-08-03", "2026-08-04"])   # 08-01 missing
        gate = db.daily_window_gate(conn, "target_daily", 7, today=TODAY)
        self.assertFalse(gate["ok"])
        self.assertEqual(gate["days_banked"], 6)
        self.assertIn("2026-08-01", gate["missing"])

    def test_stale_newest_day_fails_closed(self):
        """Days present but the newest is old: the report job has been failing.
        The threshold is the shared SNAPSHOT_STALE_AFTER_DAYS plus the lag, so
        the engine keeps one staleness number rather than inventing a second."""
        conn = memory_conn()
        banked(conn, ["2026-07-20", "2026-07-21", "2026-07-22"])
        gate = db.daily_window_gate(conn, "target_daily", 3, today=TODAY)
        self.assertFalse(gate["ok"])
        self.assertIn("stale", gate["reason"])

    def test_window_beyond_retention_fails_closed(self):
        conn = memory_conn()
        banked(conn, ["2026-08-04"])
        gate = db.daily_window_gate(conn, "target_daily", 200, today=TODAY)
        self.assertFalse(gate["ok"])
        self.assertIn("92", gate["reason"])

    def test_gate_reads_the_table_it_is_given(self):
        """campaign rolling windows read campaign_daily, not target_daily."""
        conn = memory_conn()
        conn.execute("CREATE TABLE campaign_daily (date TEXT, campaign_id TEXT, "
                     "cost REAL, PRIMARY KEY (date, campaign_id))")
        conn.executemany("INSERT INTO campaign_daily VALUES (?,?,?)",
                         [("2026-08-03", "c1", 1.0), ("2026-08-04", "c1", 1.0)])
        gate = db.daily_window_gate(conn, "campaign_daily", 2, today=TODAY)
        self.assertTrue(gate["ok"], gate["reason"])


import backfill_target_daily  # noqa: E402


class Chunking(unittest.TestCase):
    """Amazon caps DAILY reports at 31 days, so the window is split. The
    chunks must tile the range exactly: no gap leaves a hole the gate will
    later refuse to act on, and no overlap wastes a slow report."""

    def test_short_window_is_one_chunk(self):
        self.assertEqual(
            backfill_target_daily.chunk_window("2026-08-01", "2026-08-07"),
            [("2026-08-01", "2026-08-07")])

    def test_exactly_31_days_is_one_chunk(self):
        chunks = backfill_target_daily.chunk_window("2026-07-01", "2026-07-31")
        self.assertEqual(len(chunks), 1)

    def test_32_days_splits(self):
        chunks = backfill_target_daily.chunk_window("2026-07-01", "2026-08-01")
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0], ("2026-07-01", "2026-07-31"))
        self.assertEqual(chunks[1], ("2026-08-01", "2026-08-01"))

    def test_chunks_tile_the_range_without_gap_or_overlap(self):
        chunks = backfill_target_daily.chunk_window("2026-05-07", "2026-08-06")
        self.assertEqual(chunks[0][0], "2026-05-07")
        self.assertEqual(chunks[-1][1], "2026-08-06")
        for (_, prev_end), (next_start, _) in zip(chunks, chunks[1:]):
            gap = (datetime.date.fromisoformat(next_start)
                   - datetime.date.fromisoformat(prev_end)).days
            self.assertEqual(gap, 1)

    def test_columns_include_date(self):
        """Without `date` explicitly requested, a DAILY report returns rows
        the storer cannot place on a day."""
        self.assertIn("date", backfill_target_daily.COLUMNS)


class ExplodingCreateClient:
    """Stands in for AdsClient. Every report request fails. Nothing here
    touches the network — a live call would be a bug in the test, not a
    slow test."""
    market = "US"

    def create_report(self, *a, **kw):
        raise RuntimeError("Amazon said no")

    def get_report(self, *a, **kw):          # pragma: no cover - never reached
        raise AssertionError("no report was ever created")


class CreateFailurePath(unittest.TestCase):
    """The failure path of the weekly backfill. It used to append to `failed`
    before that name was assigned, so ONE rejected report request killed the
    whole run with an UnboundLocalError — before any chunk was polled or
    banked. The path has to record the failure and carry on."""

    def run_main(self, days="3"):
        import contextlib
        import io
        conn = sqlite3.connect(":memory:")
        conn.executescript(db.SCHEMA)
        real_connect, real_client, real_argv = db.connect, backfill_target_daily.AdsClient, sys.argv
        db.connect = lambda ro=False: conn
        backfill_target_daily.AdsClient = ExplodingCreateClient
        sys.argv = ["backfill_target_daily.py", "--days", days]
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                code = backfill_target_daily.main()
        finally:
            db.connect, backfill_target_daily.AdsClient, sys.argv = \
                real_connect, real_client, real_argv
        return code, buf.getvalue()

    def test_a_rejected_report_request_does_not_crash_the_run(self):
        code, output = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("CREATE FAILED", output)

    def test_the_summary_counts_the_failed_chunk(self):
        _, output = self.run_main()
        self.assertIn("Failed: 1 chunk(s)", output)

    def test_every_chunk_is_attempted_not_just_the_first(self):
        """A full 92-day window splits into several reports. One rejection
        must not abandon the rest."""
        today = datetime.date.today()
        expected = len(backfill_target_daily.chunk_window(
            (today - datetime.timedelta(days=92)).isoformat(),
            (today - datetime.timedelta(days=1)).isoformat()))
        self.assertGreater(expected, 1)
        _, output = self.run_main(days="92")
        self.assertEqual(output.count("CREATE FAILED"), expected)
        self.assertIn(f"Failed: {expected} chunk(s)", output)


import phase0_pull  # noqa: E402


class NightlyReport(unittest.TestCase):

    def test_daily_targeting_is_in_the_parallel_batch(self):
        """Not a separate serial step. Six markets at 25 minutes each would
        add about 2.5 hours to a nightly that gives up polling after 25."""
        self.assertIn("targeting_daily", phase0_pull.REPORTS)
        self.assertIn("targeting_daily", phase0_pull.STORERS)

    def test_it_is_the_only_daily_report(self):
        cfg = phase0_pull.REPORTS["targeting_daily"]
        self.assertEqual(cfg["time_unit"], "DAILY")
        self.assertIn("date", cfg["columns"])
        for key, other in phase0_pull.REPORTS.items():
            if key != "targeting_daily":
                self.assertEqual(other.get("time_unit", "SUMMARY"), "SUMMARY")

    def test_its_window_is_seven_days_not_thirty_one(self):
        """Seven rather than one, so a night the job dies heals itself on the
        next run instead of leaving a permanent hole in the window."""
        cfg = phase0_pull.REPORTS["targeting_daily"]
        span = (datetime.date.fromisoformat(cfg["end"])
                - datetime.date.fromisoformat(cfg["start"])).days
        self.assertEqual(span, 6)
        self.assertEqual(cfg["end"], phase0_pull.END)

    def test_it_stores_into_target_daily_not_targeting_perf(self):
        self.assertIs(phase0_pull.STORERS["targeting_daily"], db.store_target_daily)


class HistoryBasis(unittest.TestCase):
    """The chart must say which kind of number it is drawing. A trailing-30
    snapshot series and a true per-day series look identical and mean very
    different things."""

    def test_basis_names_the_source(self):
        import appctl
        self.assertTrue(hasattr(appctl, "_history_basis"))
        conn = memory_conn()
        self.assertEqual(appctl._history_basis(conn, "t1"), "trailing30_snapshot")
        banked(conn, ["2026-08-03", "2026-08-04"])
        conn.execute("UPDATE target_daily SET target_id='t1'")
        self.assertEqual(appctl._history_basis(conn, "t1"), "daily")


def bare_conn():
    """An in-memory DB with NO target_daily table at all. memory_conn()
    always creates the table, so it can't stand in for an older DB, or a
    market whose seed hasn't started — the case these tests are for."""
    return sqlite3.connect(":memory:")


def run_cmd_history(conn, campaign=None, adgroup=None, target=None):
    """Call the real cmd_history against a given connection, capturing what
    it would have printed instead of printing it. Patches db.connect and
    appctl.out for the duration of the call only."""
    import appctl
    args = types.SimpleNamespace(campaign=campaign, adgroup=adgroup, target=target)
    captured = {}
    real_connect, real_out = db.connect, appctl.out
    db.connect = lambda ro=True: conn
    appctl.out = lambda data: captured.update(data)
    try:
        appctl.cmd_history(args)
    finally:
        db.connect = real_connect
        appctl.out = real_out
    return captured


class AbsentDailyTables(unittest.TestCase):
    """target_daily (and, for campaigns, campaign_daily) can be entirely
    absent: an older DB, or a market whose seed hasn't started. Every basis
    check must fall back to the snapshot answer and cmd_health must report
    None — never raise, and never claim 'daily' on a table that isn't
    there. memory_conn()'s table-always-exists shape can't exercise this,
    so these tests use a bare connection instead."""

    def test_history_basis_falls_back_when_table_is_absent(self):
        import appctl
        self.assertEqual(appctl._history_basis(bare_conn(), "t1"),
                         "trailing30_snapshot")

    def test_health_coverage_is_none_when_table_is_absent(self):
        import appctl
        self.assertIsNone(appctl._target_daily_coverage(bare_conn()))

    def test_campaign_branch_falls_back_when_campaign_daily_is_absent(self):
        """cmd_history's campaign path checks campaign_daily inline,
        separately from _history_basis. Same failure mode, same
        requirement: it must fall back, not raise or say 'daily'."""
        conn = bare_conn()
        conn.execute("""CREATE TABLE campaign_perf (date TEXT, campaign_id TEXT,
            impressions INTEGER, clicks INTEGER, cost REAL,
            orders INTEGER, sales REAL)""")
        data = run_cmd_history(conn, campaign="c1")
        self.assertEqual(data["basis"], "trailing30_snapshot")


class HistorySpan(unittest.TestCase):
    """basis says WHICH series a chart is drawing. days_banked/first/last
    say how much of it there is — a thinly-banked daily series must not be
    charted as if it carried a full trailing history."""

    def test_span_matches_the_banked_days_when_daily(self):
        conn = memory_conn()
        banked(conn, ["2026-08-01", "2026-08-02", "2026-08-03"])
        conn.execute("UPDATE target_daily SET target_id='t1'")
        data = run_cmd_history(conn, target="t1")
        self.assertEqual(data["basis"], "daily")
        self.assertEqual(data["days_banked"], 3)
        self.assertEqual(data["first"], "2026-08-01")
        self.assertEqual(data["last"], "2026-08-03")

    def test_one_banked_day_falls_back_to_the_snapshot_series(self):
        """A single banked day wins the "do we have daily data" question but
        loses the "can this be drawn" one — both chart call sites need more
        than one point. Preferring it blanked 18,062 of the live account's
        67,763 target charts. One point is unchartable either way, so the
        snapshot series must win here."""
        conn = memory_conn()
        conn.execute("""CREATE TABLE targeting_perf (date TEXT, target_id TEXT,
            impressions INTEGER, clicks INTEGER, cost REAL,
            orders INTEGER, sales REAL)""")
        conn.executemany(
            "INSERT INTO targeting_perf VALUES (?,?,?,?,?,?,?)",
            [("2026-07-%02d" % d, "t1", 100, 5, 2.0, 0, 0.0) for d in range(1, 20)])
        banked(conn, ["2026-08-03"])
        conn.execute("UPDATE target_daily SET target_id='t1'")
        data = run_cmd_history(conn, target="t1")
        self.assertEqual(data["basis"], "trailing30_snapshot")
        self.assertEqual(data["days_banked"], 19)

    def test_two_banked_days_use_the_daily_series(self):
        """The other side of the boundary. Two points draw a line, and true
        per-day numbers beat trailing-30 drift the moment they can."""
        conn = memory_conn()
        conn.execute("""CREATE TABLE targeting_perf (date TEXT, target_id TEXT,
            impressions INTEGER, clicks INTEGER, cost REAL,
            orders INTEGER, sales REAL)""")
        conn.executemany(
            "INSERT INTO targeting_perf VALUES (?,?,?,?,?,?,?)",
            [("2026-07-%02d" % d, "t1", 100, 5, 2.0, 0, 0.0) for d in range(1, 20)])
        banked(conn, ["2026-08-03", "2026-08-04"])
        conn.execute("UPDATE target_daily SET target_id='t1'")
        data = run_cmd_history(conn, target="t1")
        self.assertEqual(data["basis"], "daily")
        self.assertEqual(data["days_banked"], 2)

    def test_the_same_boundary_applies_to_ad_groups_and_campaigns(self):
        """All three branches of cmd_history share one rule. An ad group or a
        campaign with one banked day must fall back too."""
        conn = memory_conn()
        conn.execute("""CREATE TABLE targeting_perf (date TEXT, ad_group_id TEXT,
            impressions INTEGER, clicks INTEGER, cost REAL,
            orders INTEGER, sales REAL)""")
        conn.execute("""CREATE TABLE campaign_daily (date TEXT, campaign_id TEXT,
            impressions INTEGER, clicks INTEGER, cost REAL,
            orders INTEGER, sales REAL, PRIMARY KEY (date, campaign_id))""")
        conn.execute("""CREATE TABLE campaign_perf (date TEXT, campaign_id TEXT,
            impressions INTEGER, clicks INTEGER, cost REAL,
            orders INTEGER, sales REAL)""")
        conn.execute("INSERT INTO targeting_perf VALUES "
                     "('2026-07-01','ag1',100,5,2.0,0,0.0)")
        conn.execute("INSERT INTO campaign_perf VALUES "
                     "('2026-07-01','c1',100,5,2.0,0,0.0)")
        conn.execute("INSERT INTO campaign_daily VALUES "
                     "('2026-08-03','c1',100,5,2.0,0,0.0)")
        banked(conn, ["2026-08-03"])
        conn.execute("UPDATE target_daily SET ad_group_id='ag1'")
        self.assertEqual(run_cmd_history(conn, adgroup="ag1")["basis"],
                         "trailing30_snapshot")
        self.assertEqual(run_cmd_history(conn, campaign="c1")["basis"],
                         "trailing30_snapshot")

    def test_span_is_null_when_there_are_no_points(self):
        conn = memory_conn()
        conn.execute("""CREATE TABLE targeting_perf (date TEXT, target_id TEXT,
            impressions INTEGER, clicks INTEGER, cost REAL,
            orders INTEGER, sales REAL)""")
        data = run_cmd_history(conn, target="unknown-target")
        self.assertEqual(data["basis"], "trailing30_snapshot")
        self.assertEqual(data["days_banked"], 0)
        self.assertIsNone(data["first"])
        self.assertIsNone(data["last"])
