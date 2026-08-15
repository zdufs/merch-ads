#!/usr/bin/env python3
"""Accumulated loop entities in the DSL (MerchDash parity #2, Phase 2).

`FOR EACH accumulated_keyword` / `accumulated_asin` lets a nightly REVIEW rule act
on the cross-campaign rollup — pauseEverywhere / setBidEverywhere / negateEverywhere
fan one change out to every instance. This guards the loader, the preview, the
executor fan-out, and the entity/verb validation.

Run from the Ads folder:  python3 -m unittest tests.rules_accumulated_tests -v
"""

import os
import sqlite3
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ["ADS_MARKET"] = "US"

import db  # noqa: E402
from rules import entities, executor, runner  # noqa: E402

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
                      ("ag3", "c1", "AG3", "ENABLED", 0.5)])
    conn.executemany("INSERT INTO ad_group_product (ad_group_id, asin, product_type) "
                     "VALUES (?,?,?)", [("ag1", "B1", "tee"), ("ag2", "B1", "tee"),
                                        ("ag3", "B1", "tee")])
    for (cid, agid, tid) in [("c1", "ag1", "t1"), ("c2", "ag2", "t2"), ("c1", "ag3", "t3")]:
        conn.execute(
            "INSERT INTO targeting_perf (date, campaign_id, ad_group_id, targeting, "
            "match_type, target_id, impressions, clicks, cost, orders, sales, acos) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (D, cid, agid, "widget", "EXACT", tid, 100, 5, 3.0, 0, 0, None))
    conn.commit()
    return conn


class FakeClient:
    def __init__(self):
        self.calls = []

    def _ok(self, n=1):
        return [{"http": 200, "failed_items": 0, "failed_ids": [], "created_ids": ["neg1"]}]

    def create_negative_keywords(self, items):
        self.calls.append(("negate", items)); return self._ok()

    def pause_ad_groups(self, ids):
        self.calls.append(("pause_ag", list(ids))); return self._ok()

    def set_targets_state(self, ids, state):
        self.calls.append(("pause_t", list(ids), state)); return self._ok()

    def update_target_bids(self, items):
        self.last_clamps = []
        self.calls.append(("bid", items)); return self._ok()


class Loader(unittest.TestCase):
    def test_accumulated_keyword_rolls_up_with_counts(self):
        rows = entities.load(mk_conn(), "accumulated_keyword")
        self.assertEqual(len(rows), 1)
        f = rows[0].fields
        self.assertEqual(rows[0].id, "widget")
        self.assertEqual(f["campaigns"], 2)     # c1, c2
        self.assertEqual(f["ad_groups"], 3)
        self.assertEqual(f["spend"], 9.0)       # 3 rows x 3.0

    def test_accumulated_asin_rolls_up(self):
        rows = entities.load(mk_conn(), "accumulated_asin")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].id, "B1")
        self.assertEqual(rows[0].fields["ad_groups"], 3)


class PreviewAndFanout(unittest.TestCase):
    NEG = ("FOR EACH accumulated_keyword:\n"
           "  IF accumulated_keyword.spend > $5:\n"
           "    accumulated_keyword.negateEverywhere()\n"
           "    accumulated_keyword.note(\"{spend:money} over {campaigns} campaigns\")\n")

    def test_preview_matches_and_emits_the_everywhere_verb(self):
        res = runner.preview(mk_conn(), self.NEG)
        self.assertTrue(res["ok"], res.get("errors"))
        self.assertEqual(res["matched"], 1)
        self.assertEqual(res["changes"][0]["action"], "negateEverywhere")
        self.assertEqual(res["changes"][0]["entity_id"], "widget")

    def test_executor_fans_out_to_every_ad_group(self):
        conn, client = mk_conn(), FakeClient()
        ch = {"action": "negateEverywhere", "entity_kind": "accumulated_keyword",
              "entity_id": "widget", "args": [], "note": "x"}
        res = executor._apply_one(conn, client, ch)
        self.assertTrue(res["ok"])
        self.assertEqual(res["fanout"]["applied"], 3)      # one negative per ad group
        negate_calls = [c for c in client.calls if c[0] == "negate"]
        self.assertEqual(len(negate_calls), 3)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM writes_log WHERE action='add_negative'").fetchone()[0], 3)


class Validation(unittest.TestCase):
    def test_everywhere_verb_rejected_on_a_normal_entity(self):
        v = runner.validate("FOR EACH target:\n  IF target.spend > $5:\n"
                            "    target.negateEverywhere()\n")
        self.assertFalse(v["ok"])

    def test_bare_pause_rejected_on_accumulated(self):
        v = runner.validate("FOR EACH accumulated_keyword:\n"
                            "  IF accumulated_keyword.spend > $5:\n"
                            "    accumulated_keyword.pause()\n")
        self.assertFalse(v["ok"])

    def test_negate_rejected_on_accumulated_asin(self):
        v = runner.validate("FOR EACH accumulated_asin:\n"
                            "  IF accumulated_asin.spend > $5:\n"
                            "    accumulated_asin.negateEverywhere()\n")
        self.assertFalse(v["ok"])

    def test_pauseeverywhere_on_asin_is_ok(self):
        v = runner.validate("FOR EACH accumulated_asin:\n"
                            "  IF accumulated_asin.spend > $5 AND accumulated_asin.orders = 0:\n"
                            "    accumulated_asin.pauseEverywhere()\n")
        self.assertTrue(v["ok"], v.get("errors"))


if __name__ == "__main__":
    unittest.main()
