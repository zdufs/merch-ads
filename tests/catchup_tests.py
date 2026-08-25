#!/usr/bin/env python3
"""The unattended catch-up: what it counts as "still pending" and what it reports.

Run from the Ads folder:  python3 -m unittest tests.catchup_tests -v

Amazon's reports routinely outrun the 25-minute poll, so asking and collecting
have always been two separate runs. catchup.py asks for every market first and
then collects in rounds. These tests pin the two judgements it makes without
touching Amazon: which reports are still worth waiting for, and what it reports
back at the end.
"""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ.setdefault("ADS_MARKET", "US")

import db        # noqa: E402
import catchup   # noqa: E402


def temp_market_db():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    real = db.DB_PATH
    db.DB_PATH = path
    conn = db.connect()
    db.DB_PATH = real
    return conn, path


class MarketPathTests(unittest.TestCase):
    def test_us_keeps_the_original_filename(self):
        self.assertTrue(catchup.market_db_path("US").endswith("ads_data.sqlite"))

    def test_other_markets_are_suffixed(self):
        self.assertTrue(catchup.market_db_path("DE").endswith("ads_data_DE.sqlite"))


class PendingReportTests(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = temp_market_db()
        self.real_path_fn = catchup.market_db_path
        catchup.market_db_path = lambda code: self.path

    def tearDown(self):
        catchup.market_db_path = self.real_path_fn
        self.conn.close()
        os.remove(self.path)

    def add(self, report_type, status, downloaded=0):
        self.conn.execute(
            """INSERT INTO report_jobs(report_type,report_id,status,requested_at,
                                       window_end,downloaded)
               VALUES (?,?,?,?,?,?)""",
            (report_type, "r-" + report_type, status, "2026-08-20T10:00:00",
             "2026-08-19", downloaded))
        self.conn.commit()

    def test_a_generating_report_is_pending(self):
        self.add("campaigns", "PENDING")
        self.assertEqual(catchup.pending_reports("US"), 1)

    def test_a_collected_report_is_not(self):
        self.add("campaigns", "COMPLETED", downloaded=1)
        self.assertEqual(catchup.pending_reports("US"), 0)

    def test_dead_jobs_are_history_not_work(self):
        """Counting these would spin the loop to its deadline every time."""
        for status in ("FAILED", "CANCELLED", "EXPIRED"):
            self.add(f"job_{status}", status)
        self.assertEqual(catchup.pending_reports("US"), 0)

    def test_a_market_with_no_database_is_not_pending(self):
        catchup.market_db_path = lambda code: "/tmp/no_such_market_9f3a1c.sqlite"
        self.assertEqual(catchup.pending_reports("XX"), 0)


class BankedTests(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = temp_market_db()
        self.real_path_fn = catchup.market_db_path
        catchup.market_db_path = lambda code: self.path

    def tearDown(self):
        catchup.market_db_path = self.real_path_fn
        self.conn.close()
        os.remove(self.path)

    def test_it_reports_the_worst_perf_table_not_the_best(self):
        """The three perf tables are filled by independent report jobs and drift
        apart. campaign_perf alone stayed green through both freezes this engine
        has had, so the best of the three is the one number that lies."""
        self.conn.execute("INSERT INTO campaign_perf(date,campaign_id,cost,sales) "
                          "VALUES ('2026-08-19','c1',1.0,5.0)")
        self.conn.execute("INSERT INTO targeting_perf(date,campaign_id,ad_group_id,"
                          "target_id,cost,sales) VALUES ('2026-08-15','c1','g1','t1',1.0,5.0)")
        self.conn.execute("INSERT INTO search_term_perf(date,campaign_id,ad_group_id,"
                          "search_term,cost,sales) VALUES ('2026-08-19','c1','g1','tee',1.0,5.0)")
        self.conn.execute("INSERT INTO daily_totals(date,cost,sales) VALUES ('2026-08-19',1.0,5.0)")
        self.conn.commit()
        perf, day = catchup.banked("US")
        self.assertEqual(perf, "2026-08-15")
        self.assertEqual(day, "2026-08-19")

    def test_an_empty_market_reports_dashes_rather_than_a_date(self):
        perf, day = catchup.banked("US")
        self.assertEqual((perf, day), ("—", "—"))


class ArgumentTests(unittest.TestCase):
    def test_defaults_are_the_unattended_ones(self):
        args = catchup.parse_args([])
        self.assertIsNone(args.markets)
        self.assertEqual(args.round_wait, 240)
        self.assertEqual(args.deadline_mins, 90)
        self.assertFalse(args.skip_daily)

    def test_markets_can_be_scoped(self):
        self.assertEqual(catchup.parse_args(["--markets", "UK", "DE"]).markets, ["UK", "DE"])


class PollBudgetTests(unittest.TestCase):
    """The flags catchup drives the two scripts with must exist and mean this."""

    def test_phase0_pull_takes_a_poll_budget_and_a_reports_only_round(self):
        import phase0_pull
        args = phase0_pull.parse_args(["--max-wait", "0", "--reports-only"])
        self.assertEqual(args.max_wait, 0)
        self.assertTrue(args.reports_only)
        self.assertEqual(phase0_pull.parse_args([]).max_wait, phase0_pull.MAX_WAIT)

    def test_daily_metrics_takes_a_poll_budget(self):
        import daily_metrics
        self.assertEqual(daily_metrics.parse_args(["--max-wait", "0"]).max_wait, 0)
        self.assertEqual(daily_metrics.parse_args([]).max_wait, daily_metrics.MAX_WAIT)


if __name__ == "__main__":
    unittest.main()
