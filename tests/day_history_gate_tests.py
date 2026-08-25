#!/usr/bin/env python3
"""The banked-day tables go stale on their OWN nightly step.

Run from the Ads folder:  python3 -m unittest tests.day_history_gate_tests -v

daily_totals is filled by daily_metrics.py, campaign_daily by the same step,
target_daily by phase 0. None of them is filled by the perf pull. In Aug 2026
the nightly ran US-only for five nights: the EU perf tables were caught up by
hand, read fresh, and System Health went green — while daily_totals still
stopped at 14.08. The dashboard's day grid greyed out and no screen said a
step had stopped running.

db.daily_bank_gate is the one rule for "this day table is too old", set to the
same limit daily_window_gate already refuses to write past. appctl's health
reply carries its verdict so the app can raise it.
"""

import datetime
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ.setdefault("ADS_MARKET", "US")

import db        # noqa: E402
import appctl    # noqa: E402

TODAY = datetime.date(2026, 8, 20)
LIMIT = db.DAILY_ATTRIBUTION_LAG_DAYS + db.SNAPSHOT_STALE_AFTER_DAYS   # 5


def temp_conn():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    real = db.DB_PATH
    db.DB_PATH = path
    conn = db.connect()
    db.DB_PATH = real
    return conn, path


def bank_days(conn, last, count=3):
    """`count` consecutive banked days ending on `last`."""
    day = datetime.date.fromisoformat(last)
    for i in range(count):
        d = (day - datetime.timedelta(days=i)).isoformat()
        conn.execute("INSERT INTO daily_totals(date,cost,sales) VALUES (?,?,?)",
                     (d, 1.0, 5.0))
    conn.commit()


class DailyBankGateTests(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = temp_conn()

    def tearDown(self):
        self.conn.close()
        os.remove(self.path)

    def test_yesterday_is_fresh(self):
        bank_days(self.conn, "2026-08-19")
        gate = db.daily_bank_gate(self.conn, "daily_totals", today=TODAY)
        self.assertTrue(gate["ok"], gate["reason"])
        self.assertEqual(gate["age_days"], 1)

    def test_the_limit_itself_still_passes(self):
        """The EU markets sit behind by design; the alarm must not cry wolf."""
        last = (TODAY - datetime.timedelta(days=LIMIT)).isoformat()
        bank_days(self.conn, last)
        self.assertTrue(db.daily_bank_gate(self.conn, "daily_totals", today=TODAY)["ok"])

    def test_one_day_past_the_limit_fails(self):
        last = (TODAY - datetime.timedelta(days=LIMIT + 1)).isoformat()
        bank_days(self.conn, last)
        gate = db.daily_bank_gate(self.conn, "daily_totals", today=TODAY)
        self.assertFalse(gate["ok"])
        self.assertIn(last, gate["reason"])

    def test_an_empty_table_fails_closed(self):
        gate = db.daily_bank_gate(self.conn, "daily_totals", today=TODAY)
        self.assertFalse(gate["ok"])
        self.assertIn("no days banked", gate["reason"])

    def test_a_missing_table_fails_closed_instead_of_raising(self):
        gate = db.daily_bank_gate(self.conn, "table_that_does_not_exist", today=TODAY)
        self.assertFalse(gate["ok"])
        self.assertIn("does not exist", gate["reason"])

    def test_the_limit_is_the_one_rolling_windows_already_use(self):
        """One threshold. A second number here would let a rule refuse to write
        over data the health screen still called fine."""
        last = (TODAY - datetime.timedelta(days=LIMIT + 1)).isoformat()
        bank_days(self.conn, last, count=10)
        window = db.daily_window_gate(self.conn, "daily_totals", 7, today=TODAY)
        self.assertFalse(window["ok"])
        self.assertEqual(window["reason"],
                         db.daily_bank_gate(self.conn, "daily_totals", today=TODAY)["reason"])


class HealthCarriesTheVerdictTests(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = temp_conn()

    def tearDown(self):
        self.conn.close()
        os.remove(self.path)

    def test_a_stale_market_is_reported_stale_with_its_age(self):
        bank_days(self.conn, "2026-08-14")
        cov = appctl._daily_totals_coverage(self.conn, today=TODAY)
        self.assertTrue(cov["stale"])
        self.assertEqual(cov["behind_days"], 6)
        self.assertEqual(cov["last"], "2026-08-14")

    def test_a_caught_up_market_is_not_stale(self):
        bank_days(self.conn, "2026-08-19")
        cov = appctl._daily_totals_coverage(self.conn, today=TODAY)
        self.assertFalse(cov["stale"])

    def test_no_banked_days_reports_nothing_rather_than_a_false_alarm(self):
        """A market that has never advertised is not an incident."""
        self.assertIsNone(appctl._daily_totals_coverage(self.conn, today=TODAY))


if __name__ == "__main__":
    unittest.main()
