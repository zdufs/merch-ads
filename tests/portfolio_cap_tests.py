#!/usr/bin/env python3
"""R8 portfolio cap-alert: the monthly-cap store round-trips, and the alert fires
at the right thresholds off month-to-date pooled spend. Synthetic in-memory DB so
it doesn't depend on any market's banked data."""
import datetime
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db
import appctl


def _conn():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE engine_meta(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
    c.execute("CREATE TABLE daily_totals(date TEXT, cost REAL)")
    return c


def _this_month_day(day="05"):
    return datetime.date.today().strftime("%Y-%m") + "-" + day


class PortfolioCap(unittest.TestCase):
    def test_cap_store_roundtrip(self):
        c = _conn()
        self.assertIsNone(db.get_portfolio_cap(c))
        db.set_portfolio_cap(c, 500)
        self.assertEqual(db.get_portfolio_cap(c), 500.0)
        db.set_portfolio_cap(c, None)
        self.assertIsNone(db.get_portfolio_cap(c))

    def test_no_cap_means_no_alert(self):
        c = _conn()
        c.execute("INSERT INTO daily_totals VALUES(?,?)", (_this_month_day(), 9999))
        self.assertEqual(appctl._portfolio_cap_alerts(c, "USKDP"), [])

    def test_quiet_below_80_percent(self):
        c = _conn(); db.set_portfolio_cap(c, 500)
        c.execute("INSERT INTO daily_totals VALUES(?,?)", (_this_month_day(), 100))  # 20%
        self.assertEqual(appctl._portfolio_cap_alerts(c, "USKDP"), [])

    def test_nearing_at_80_percent(self):
        c = _conn(); db.set_portfolio_cap(c, 500)
        c.execute("INSERT INTO daily_totals VALUES(?,?)", (_this_month_day(), 410))  # 82%
        a = appctl._portfolio_cap_alerts(c, "USKDP")
        self.assertEqual(len(a), 1)
        self.assertEqual(a[0]["kind"], "portfolio_cap")
        self.assertIn("nearing", a[0]["key"])
        self.assertIn("nearing", a[0]["message"])
        self.assertIsNone(a[0].get("campaign_id"))     # portfolio-wide → Dashboard

    def test_over_at_100_percent(self):
        c = _conn(); db.set_portfolio_cap(c, 500)
        c.execute("INSERT INTO daily_totals VALUES(?,?)", (_this_month_day(), 520))
        a = appctl._portfolio_cap_alerts(c, "USKDP")
        self.assertEqual(len(a), 1)
        self.assertIn("over", a[0]["key"])
        self.assertIn("OVER", a[0]["message"])

    def test_only_current_month_counts(self):
        c = _conn(); db.set_portfolio_cap(c, 500)
        # a huge spend in a past month must not count toward this month's cap
        c.execute("INSERT INTO daily_totals VALUES(?,?)", ("2020-01-15", 9999))
        self.assertEqual(appctl._portfolio_cap_alerts(c, "USKDP"), [])

    def test_key_is_stable_per_month_and_level(self):
        # same month + level => same dedup key, so the app notifies once
        c = _conn(); db.set_portfolio_cap(c, 500)
        c.execute("INSERT INTO daily_totals VALUES(?,?)", (_this_month_day("03"), 410))
        c.execute("INSERT INTO daily_totals VALUES(?,?)", (_this_month_day("04"), 0))
        k1 = appctl._portfolio_cap_alerts(c, "USKDP")[0]["key"]
        k2 = appctl._portfolio_cap_alerts(c, "USKDP")[0]["key"]
        self.assertEqual(k1, k2)


if __name__ == "__main__":
    unittest.main()
