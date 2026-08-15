#!/usr/bin/env python3
"""One year-to-date, computed one way.

Three endpoints reported YTD and two of them disagreed with the third. On
2026-08-06 for US the Dashboard's period stack and the All Markets screen were
2.3x apart — same label, same market, same year. The cause
was that `periods` supplements banked daily history with the months imported
from the Ads console, and `monthly` / `metrics` / `overview` each ran their own
daily-totals-only query instead.

The console import matters precisely because it is unreachable any other way:
Amazon's reporting retention starts ~95 days back, so months before the first
banked day exist nowhere else. Leaving them out does not make a number
conservative, it makes it wrong.

Two traps this pins down:

  * Double counting. Daily history and the import overlap from April 2026
    onwards and agree to the cent. The supplement must stop at the month before
    the daily data starts.
  * EUR cannot be split. One console export covers every marketplace and came
    back with no country, so DE/FR/ES/IT share a single EUR series. Those
    markets get the daily-only figure, and must SAY so rather than quietly
    reporting a short year.

Run from the Ads folder:  python3 -m unittest tests.ytd_definition_tests -v
"""

import os
import re
import sqlite3
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(HERE, "engine")
sys.path.insert(0, ENGINE)
os.environ["ADS_MARKET"] = "US"

import appctl  # noqa: E402
import db  # noqa: E402

# Daily history starts in April, like the real US DB does.
DAILY = [("2026-04-10", 100.0, 500.0, 20),
         ("2026-05-10", 200.0, 900.0, 30),
         ("2026-08-05", 50.0, 250.0, 10)]

# Console import: January to March sit BEFORE the daily data and are the only
# copy of those months. April overlaps and must never be added on top.
IMPORTED = [("2026-01", "USD", 1000.0, 4000.0, 100),
            ("2026-02", "USD", 1100.0, 4400.0, 110),
            ("2026-03", "USD", 1200.0, 4800.0, 120),
            ("2026-04", "USD", 100.0, 500.0, 20)]

DAILY_SPEND = 350.0          # 100 + 200 + 50
IMPORTED_SPEND = 3300.0      # January through March only


def daily_cursor():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE daily_totals (date TEXT PRIMARY KEY, cost REAL, "
                 "sales REAL, orders INTEGER)")
    conn.executemany("INSERT INTO daily_totals VALUES (?,?,?,?)", DAILY)
    return conn.cursor()


def shared_conn(rows=IMPORTED):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE ads_history_monthly (month TEXT, currency TEXT, "
                 "spend REAL, sales REAL, purchases INTEGER)")
    conn.executemany("INSERT INTO ads_history_monthly VALUES (?,?,?,?,?)", rows)
    return conn


class SharedHistory(unittest.TestCase):
    """Every test needs the account-wide import store stubbed out."""

    def setUp(self):
        self._real = db.connect_shared
        db.connect_shared = lambda ro=False: shared_conn()
        self.addCleanup(lambda: setattr(db, "connect_shared", self._real))


class OneDefinition(SharedHistory):
    def test_ytd_reaches_back_through_the_imported_months(self):
        ytd = appctl._ytd_totals("US", daily_cursor())
        self.assertAlmostEqual(ytd["spend"], DAILY_SPEND + IMPORTED_SPEND, places=2)

    def test_the_overlapping_month_is_counted_once(self):
        # April is in both sources. Adding it twice would inflate the year by
        # exactly one month and nothing would flag it.
        ytd = appctl._ytd_totals("US", daily_cursor())
        self.assertLess(ytd["spend"], DAILY_SPEND + IMPORTED_SPEND + 100.0)

    def test_it_says_when_it_was_supplemented(self):
        ytd = appctl._ytd_totals("US", daily_cursor())
        self.assertTrue(ytd["supplemented"])
        self.assertEqual(ytd["first_month"], "2026-01")

    def test_a_market_whose_history_cannot_be_split_is_daily_only_and_says_so(self):
        # EUR is DE+FR+ES+IT merged, so no per-market supplement is possible.
        ytd = appctl._ytd_totals("DE", daily_cursor())
        self.assertAlmostEqual(ytd["spend"], DAILY_SPEND, places=2)
        self.assertFalse(ytd["supplemented"])

    def test_acos_is_recomputed_over_the_whole_supplemented_year(self):
        # Deriving ACOS from the daily slice alone would describe a different
        # window than the spend and sales printed beside it.
        ytd = appctl._ytd_totals("US", daily_cursor())
        self.assertAlmostEqual(ytd["acos"], ytd["spend"] / ytd["sales"], places=4)

    def test_a_year_that_starts_late_is_labelled_partial(self):
        # UK only began spending in June 2026, so its YTD covers 40 days. That
        # is a partial year, not a small one, and the number is misread without
        # the label.
        db.connect_shared = lambda ro=False: shared_conn(rows=[])
        ytd = appctl._ytd_totals("UK", daily_cursor())
        self.assertTrue(ytd["partial"])
        self.assertEqual(ytd["first_month"], "2026-04")

    def test_a_supplemented_year_reaching_january_is_not_partial(self):
        ytd = appctl._ytd_totals("US", daily_cursor())
        self.assertFalse(ytd["partial"])

    def test_no_history_at_all_still_returns_the_daily_year(self):
        db.connect_shared = lambda ro=False: shared_conn(rows=[])
        ytd = appctl._ytd_totals("US", daily_cursor())
        self.assertAlmostEqual(ytd["spend"], DAILY_SPEND, places=2)
        self.assertFalse(ytd["supplemented"])


class EveryCallerAgrees(SharedHistory):
    def test_monthly_ytd_is_the_helper(self):
        cur = daily_cursor()
        _, ytd, _ = appctl._monthly_rows(cur, market="US")
        self.assertAlmostEqual(ytd["spend"], DAILY_SPEND + IMPORTED_SPEND, places=2)

    def test_only_one_place_in_appctl_computes_a_year_to_date(self):
        # Structural, like tests/snapshot_lint_tests.py: the defect was three
        # copies drifting, so the guard is that there is only ever one.
        src = open(os.path.join(ENGINE, "appctl.py"), encoding="utf-8").read()
        pat = re.compile(r"SELECT SUM\(cost\), SUM\(sales\)(?:, SUM\(orders\))?"
                         r"\s+FROM daily_totals\s+WHERE date>=\?", re.S)
        self.assertEqual(len(pat.findall(src)), 1,
                         "year-to-date must be computed in _ytd_totals and nowhere else")


if __name__ == "__main__":
    unittest.main()
