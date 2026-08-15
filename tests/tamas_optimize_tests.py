#!/usr/bin/env python3
"""TAMAS optimizer: it must only look at campaigns that can actually serve.

Run from the Ads folder:  python3 -m unittest tests.tamas_optimize_tests -v

The bug these guard against: `build()` selected TAMAS campaigns by NAME alone.
A retired TAMAS account keeps its campaigns in the mirror as ARCHIVED, so the
"nothing to do" early return never fired. Every nightly run then made a live
Amazon /sp/keywords/list call for campaigns that cannot serve, and any bid it
computed would have been submitted against an archived campaign.
"""

import os
import sqlite3
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["ADS_MARKET"] = "US"

import tamas_optimize  # noqa: E402


class ExplodingClient:
    """Any Amazon call is a test failure. The optimizer must not reach one when
    there is nothing enabled to optimize."""

    def list_keywords(self, *a, **kw):
        raise AssertionError("called Amazon /sp/keywords/list with no ENABLED TAMAS campaigns")

    def __getattr__(self, name):
        def boom(*a, **kw):
            raise AssertionError(f"called Amazon ({name}) with no ENABLED TAMAS campaigns")
        return boom


def conn_with(campaigns):
    """In-memory mirror holding just what build() reads before its early return.
    `campaigns` is a list of (campaign_id, name, state)."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE campaigns (campaign_id TEXT, name TEXT, state TEXT)")
    conn.execute("CREATE TABLE campaign_perf (campaign_id TEXT, date TEXT, "
                 "clicks INTEGER, orders INTEGER, cost REAL)")
    conn.execute("CREATE TABLE ad_groups (campaign_id TEXT, name TEXT)")
    conn.executemany("INSERT INTO campaigns VALUES (?,?,?)", campaigns)
    conn.execute("INSERT INTO campaign_perf VALUES ('c1','2026-08-13',0,0,0)")
    conn.commit()
    return conn


class OnlyEnabledCampaigns(unittest.TestCase):
    def test_archived_tamas_campaigns_do_not_reach_amazon(self):
        # The exact shape of a retired TAMAS account: campaigns still mirrored,
        # every one of them ARCHIVED.
        conn = conn_with([(f"c{i}", f"TAMAS - design - B0EXAMPLE{i}", "ARCHIVED")
                          for i in range(27)])
        end, bids, pauses = tamas_optimize.build(conn, ExplodingClient())
        self.assertEqual(bids, [])
        self.assertEqual(pauses, [])
        self.assertEqual(end, "2026-08-13")

    def test_paused_tamas_campaigns_do_not_reach_amazon(self):
        conn = conn_with([("c1", "TAMAS - design - B0EXAMPLE1", "PAUSED")])
        _, bids, pauses = tamas_optimize.build(conn, ExplodingClient())
        self.assertEqual((bids, pauses), ([], []))

    def test_non_tamas_campaigns_are_ignored_even_when_enabled(self):
        conn = conn_with([("c1", "Lotto 3", "ENABLED"),
                          ("c2", "Scavenger Tees", "ENABLED")])
        _, bids, pauses = tamas_optimize.build(conn, ExplodingClient())
        self.assertEqual((bids, pauses), ([], []))

    def test_an_enabled_tamas_campaign_does_reach_amazon(self):
        """The positive control. A guard that never lets anything through would
        pass all three tests above while silently disabling a live account, so
        prove the opposite case too: with one ENABLED TAMAS campaign the
        optimizer must reach Amazon for its keywords."""
        conn = conn_with([("c1", "TAMAS - design - B0EXAMPLE1", "ENABLED")])
        conn.execute("INSERT INTO ad_groups VALUES ('c1','B0EXAMPLE1')")
        conn.commit()
        # Everything between the campaign selection and the Amazon call reads
        # state this fixture has no reason to carry.
        real_map, real_roy = tamas_optimize.db.get_product_map, tamas_optimize.traz.load_asin_royalty
        tamas_optimize.db.get_product_map = lambda c: {}
        tamas_optimize.traz.load_asin_royalty = lambda: {}
        self.addCleanup(setattr, tamas_optimize.db, "get_product_map", real_map)
        self.addCleanup(setattr, tamas_optimize.traz, "load_asin_royalty", real_roy)

        with self.assertRaises(AssertionError) as ctx:
            tamas_optimize.build(conn, ExplodingClient())
        self.assertIn("keywords", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
