#!/usr/bin/env python3
"""System Health carries each market's bid ceiling.

The ceiling is the engine's third safety rail, after the kill switch and
approval mode. It is stored PER MARKET, but the only place to read one was
Settings, which shows the market the profile picker happens to be on. So
answering "am I capped everywhere?" meant loading seven screens, and nobody did.

US had a ceiling. UK, DE, FR, ES and IT had none for months, while running the
same six auto bid-writing rules every night. Neither the app nor the engine ever
said so — it took a hand audit on 2026-08-21 to find it.

`health` already returns one row per market, so it is the one place a gap can
show itself. These tests pin that the row carries the ceiling and that an unset
surface is reported as null rather than dropped.

Run from the Ads folder:  python3 -m unittest tests.health_bid_ceiling_tests -v
"""

import os
import sqlite3
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ["ADS_MARKET"] = "US"

import appctl  # noqa: E402
import db  # noqa: E402


def meta_conn():
    """An in-memory DB carrying only the table the ceiling lives in."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE engine_meta (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
    conn.commit()
    return conn


class Shape(unittest.TestCase):

    def setUp(self):
        self.conn = meta_conn()

    def tearDown(self):
        self.conn.close()

    def test_a_market_with_no_ceiling_reports_null_on_every_surface(self):
        got = appctl._bid_ceilings(self.conn)
        self.assertEqual(got, {"target": None, "keyword": None, "budget": None},
                         "an unset surface must be null, not missing — a dropped key "
                         "reads to the app exactly like a capped market")

    def test_the_set_surfaces_come_back_as_numbers(self):
        db.set_bid_ceiling(self.conn, "target", 0.35)
        db.set_bid_ceiling(self.conn, "keyword", 0.35)
        got = appctl._bid_ceilings(self.conn)
        self.assertEqual(got["target"], 0.35)
        self.assertEqual(got["keyword"], 0.35)
        self.assertIsNone(got["budget"], "budget was never set, so it stays null")

    def test_one_surface_capped_and_another_not_is_reported_honestly(self):
        db.set_bid_ceiling(self.conn, "target", 0.50)
        got = appctl._bid_ceilings(self.conn)
        self.assertEqual(got["target"], 0.50)
        self.assertIsNone(got["keyword"],
                          "a half-capped market is a real state and must be visible")

    def test_a_database_without_the_meta_table_does_not_raise(self):
        bare = sqlite3.connect(":memory:")
        self.addCleanup(bare.close)
        self.assertEqual(appctl._bid_ceilings(bare),
                         {"target": None, "keyword": None, "budget": None},
                         "health opens every market DB, including old ones — it must "
                         "never fail the whole screen over a missing table")


if __name__ == "__main__":
    unittest.main()
