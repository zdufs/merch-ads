#!/usr/bin/env python3
"""Accumulated "act everywhere" resolver (MerchDash parity #2, Phase 1).

The read half already summed a keyword/ASIN across campaigns; this resolves a
selection into the concrete instances an "everywhere" action touches — an ASIN's
ad groups, a keyword's target clauses, or the ad groups to negate it in — and
tags the no-ops (already paused) so the apply step skips them.

Run from the Ads folder:  python3 -m unittest tests.everywhere_tests -v
"""

import os
import sqlite3
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["ADS_MARKET"] = "US"

import db  # noqa: E402
import appctl  # noqa: E402

D = "2026-08-07"


def mk_conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(db.SCHEMA)
    conn.executemany("INSERT INTO campaigns (campaign_id, name, state, daily_budget) "
                     "VALUES (?,?,?,?)", [("c1", "C1", "ENABLED", 10),
                                          ("c2", "C2", "ENABLED", 10)])
    conn.executemany("INSERT INTO ad_groups (ad_group_id, campaign_id, name, state, "
                     "default_bid) VALUES (?,?,?,?,?)",
                     [("ag1", "c1", "AG1", "ENABLED", 0.5),
                      ("ag2", "c2", "AG2", "ENABLED", 0.5),
                      ("ag3", "c1", "AG3", "PAUSED", 0.5)])
    conn.executemany("INSERT INTO ad_group_product (ad_group_id, asin, product_type) "
                     "VALUES (?,?,?)", [("ag1", "B1", "tee"), ("ag2", "B1", "tee"),
                                        ("ag3", "B1", "tee")])
    for (cid, agid, tid) in [("c1", "ag1", "t1"), ("c2", "ag2", "t2"), ("c1", "ag3", "t3")]:
        conn.execute(
            "INSERT INTO targeting_perf (date, campaign_id, ad_group_id, targeting, "
            "match_type, target_id, impressions, clicks, cost, orders, sales, acos) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (D, cid, agid, "widget", "EXACT", tid, 100, 5, 3.0, 0, 0, None))
    try:
        conn.executemany("INSERT INTO targets (target_id, bid, state) VALUES (?,?,?)",
                         [("t1", 0.4, "ENABLED"), ("t2", 0.4, "ENABLED"),
                          ("t3", 0.4, "PAUSED")])
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return conn


class Resolver(unittest.TestCase):
    def test_asin_pause_resolves_ad_groups_and_skips_paused(self):
        plan = appctl._everywhere_plan(mk_conn(), "asin", "pause", ["B1"])
        ops = plan["ops"]
        self.assertEqual({o["ad_group_id"] for o in ops}, {"ag1", "ag2", "ag3"})
        self.assertTrue(all(o["op"] == "pause_ad_group" for o in ops))
        skipped = {o["ad_group_id"] for o in ops if o["skip"]}
        self.assertEqual(skipped, {"ag3"})   # already PAUSED -> no-op

    def test_keyword_pause_resolves_targets_and_skips_paused(self):
        plan = appctl._everywhere_plan(mk_conn(), "keyword", "pause", ["widget"])
        ops = plan["ops"]
        self.assertEqual({o["target_id"] for o in ops}, {"t1", "t2", "t3"})
        self.assertTrue(all(o["op"] == "pause_target" for o in ops))
        self.assertEqual({o["target_id"] for o in ops if o["skip"]}, {"t3"})

    def test_keyword_negate_is_one_per_ad_group(self):
        plan = appctl._everywhere_plan(mk_conn(), "keyword", "negate", ["widget"])
        ops = plan["ops"]
        self.assertTrue(all(o["op"] == "add_negative" and o["match"] == "exact" for o in ops))
        self.assertEqual(sorted(o["ad_group_id"] for o in ops), ["ag1", "ag2", "ag3"])
        self.assertTrue(all(o["search_term"] == "widget" for o in ops))

    def test_keyword_setbid_resolves_targets_with_current_bid(self):
        plan = appctl._everywhere_plan(mk_conn(), "keyword", "setbid", ["widget"])
        ops = plan["ops"]
        self.assertTrue(all(o["op"] == "set_bid" for o in ops))
        self.assertEqual({o["target_id"] for o in ops}, {"t1", "t2", "t3"})
        byid = {o["target_id"]: o for o in ops}
        self.assertEqual(byid["t1"]["current_bid"], 0.4)   # from the targets mirror
        self.assertTrue(byid["t3"]["skip"])                 # t3 PAUSED -> skip

    def test_negate_honors_phrase_match(self):
        plan = appctl._everywhere_plan(mk_conn(), "keyword", "negate", ["widget"], match="phrase")
        self.assertTrue(all(o["match"] == "phrase" for o in plan["ops"]))

    def test_negate_defaults_to_exact(self):
        plan = appctl._everywhere_plan(mk_conn(), "keyword", "negate", ["widget"])
        self.assertTrue(all(o["match"] == "exact" for o in plan["ops"]))

    def test_bad_negate_match_raises(self):
        with self.assertRaises(ValueError):
            appctl._everywhere_plan(mk_conn(), "keyword", "negate", ["widget"], match="broad")

    def test_unsupported_combo_raises(self):
        with self.assertRaises(ValueError):
            appctl._everywhere_plan(mk_conn(), "asin", "negate", ["B1"])


if __name__ == "__main__":
    unittest.main()
