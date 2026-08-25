#!/usr/bin/env python3
"""A count a screen prints must not be capped by what the screen happened to
fetch.

Two of these were found on 2026-08-24, in the same hour, on live data:

  * The Audit Trail's "Writes this week" card read 500 — the fetch page size —
    where the true seven-day count was 10,635. The screen exists to catch a
    runaway rule, and 2026-08-20 alone logged 9,663 writes in this account. A
    21x understatement reads as a quiet week.
  * `import-preview` returned `count` as the true total of a route and
    `designs` as the first 2000, with nothing saying so. The app builds the
    designs it was SENT: a route of 5,000 headlines 5,000, shows 2,000 and
    gives the other 3,000 no ads at all.

Both have the same shape. A number that is really "as many as I happened to
load" is printed as if it were "how many there are", and the two agree right up
until the day they matter.

Run from the Ads folder:  python3 -m unittest tests.screen_counts_tests -v
No Amazon API.
"""

import datetime
import os
import sqlite3
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ.setdefault("ADS_MARKET", "US")

import appctl  # noqa: E402
import db      # noqa: E402


def _day(offset):
    return (datetime.date.today() + datetime.timedelta(days=offset)).isoformat()


class AuditTotalsAreCountedInSQL(unittest.TestCase):
    """The counts come from the whole log, never from the loaded page."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(db.SCHEMA)

    def tearDown(self):
        self.conn.close()

    def _log(self, day, action="bid_change", detail="snap=0.40->0.45", n=1):
        self.conn.executemany(
            """INSERT INTO writes_log(applied_at,action,entity_type,entity_id,
                                      detail,prev_state,result)
               VALUES(?,?,?,?,?,?,?)""",
            [(f"{day}T10:0{i % 10}:00", action, "target", f"t{i}", detail,
              None, "submitted") for i in range(n)])
        self.conn.commit()

    def test_the_week_count_reaches_past_one_page(self):
        # 700 rows in one day, which no 500-row page could ever hold.
        self._log(_day(-2), n=700)
        totals = appctl._audit_totals(self.conn)
        self.assertEqual(totals["week"], 700)
        self.assertEqual(totals["today"], 0)

    def test_today_and_the_week_are_separate_windows(self):
        self._log(_day(0), n=3)
        self._log(_day(-6), n=5)
        self._log(_day(-7), n=9)      # outside the seven-day window
        totals = appctl._audit_totals(self.conn)
        self.assertEqual(totals["today"], 3)
        self.assertEqual(totals["week"], 8)
        self.assertEqual(totals["window_days"], 7)

    def test_no_op_rows_are_not_writes(self):
        self._log(_day(0), action="scav_add_ads", detail="0 ASINs", n=4)
        self._log(_day(0), n=2)
        totals = appctl._audit_totals(self.conn)
        self.assertEqual(totals["today"], 2)
        self.assertEqual(totals["no_ops_today"], 4)

    def test_undoable_asks_the_same_rule_the_rows_do(self):
        """Not a second copy of the rule written in SQL.

        A negative logged before reversible negatives existed carries no
        `negid=`, so there is nothing to delete and the row is honestly not
        undoable. `_row_undoable` is the one place that knows this.
        """
        self._log(_day(-1), action="add_negative", detail="negid=123", n=2)
        self._log(_day(-1), action="add_negative", detail="tee shirt", n=3)
        self._log(_day(-1), action="scav_add_ads", detail="7 ASINs", n=6)
        totals = appctl._audit_totals(self.conn)
        self.assertEqual(totals["undoable"], 2)


class IntakeRoutesSayWhatTheyLeftOut(unittest.TestCase):
    """A route carries every design it routed, and reports if it ever cannot."""

    def _designs(self, n):
        return [{"asin": f"B{i:09d}", "ad_asins": [f"B{i:09d}"], "series": "Tees"}
                for i in range(n)]

    def test_a_big_route_is_returned_whole(self):
        route = appctl._intake_route("Scavenger Tees", self._designs(2500))
        self.assertEqual(route["count"], 2500)
        self.assertEqual(route["returned"], 2500)
        self.assertEqual(len(route["designs"]), 2500,
                         "the route was silently sliced — the designs it did not "
                         "carry get no ads and nothing says so")
        self.assertFalse(route["truncated"])

    def test_returned_and_truncated_describe_a_cap_that_does_bite(self):
        """The reporting half, proved with a cap rather than assumed."""
        real = appctl.INTAKE_ROUTE_CAP
        appctl.INTAKE_ROUTE_CAP = 10
        try:
            route = appctl._intake_route("Scavenger Tees", self._designs(25))
        finally:
            appctl.INTAKE_ROUTE_CAP = real
        self.assertEqual(route["count"], 25)
        self.assertEqual(route["returned"], 10)
        self.assertTrue(route["truncated"])

    def test_the_shipped_cap_is_off(self):
        self.assertEqual(appctl.INTAKE_ROUTE_CAP, 0,
                         "a cap here is invisible to the operator: the app ticks "
                         "and builds exactly the designs it was sent")


if __name__ == "__main__":
    unittest.main()
