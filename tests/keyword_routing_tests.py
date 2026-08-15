#!/usr/bin/env python3
"""Keyword vs product-target endpoint routing in the rules executor.

A keyword and a product/auto target are different Amazon entities on different
endpoints. The executor used to send EVERY bid/state write to /sp/targets, so
keyword bids and pauses from the DSL silently failed (0 keyword bids ever
succeeded on the live account). Now it routes by the match type the change
carries.

Run from the Ads folder:  python3 -m unittest tests.keyword_routing_tests -v
"""

import os
import sqlite3
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["ADS_MARKET"] = "US"

import db  # noqa: E402
from rules import executor  # noqa: E402


def mk_conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(db.SCHEMA)
    return conn


class FakeClient:
    def __init__(self):
        self.calls = []
        self.last_clamps = []

    def _ok(self):
        return [{"http": 200, "failed_items": 0, "failed_ids": []}]

    def update_keyword_bids(self, items):
        self.calls.append(("kw_bid", items)); return self._ok()

    def update_target_bids(self, items):
        self.calls.append(("t_bid", items)); return self._ok()

    def set_keywords_state(self, ids, state):
        self.calls.append(("kw_state", list(ids), state)); return self._ok()

    def set_targets_state(self, ids, state):
        self.calls.append(("t_state", list(ids), state)); return self._ok()


def _setbid(match_type):
    return {"action": "setBid", "entity_kind": "target", "entity_id": "e1",
            "args": [0.30], "prev_bid": 0.40,
            "ref": {"target_id": "e1", "match_type": match_type}, "note": "x"}


def _pause(match_type):
    return {"action": "pause", "entity_kind": "target", "entity_id": "e1",
            "prev_state": "ENABLED",
            "ref": {"target_id": "e1", "match_type": match_type}, "note": "x"}


class BidRouting(unittest.TestCase):
    def test_keyword_bid_uses_keyword_endpoint(self):
        conn, c = mk_conn(), FakeClient()
        executor._apply_one(conn, c, _setbid("EXACT"))
        self.assertEqual(c.calls[0][0], "kw_bid")
        self.assertEqual(c.calls[0][1], [{"keywordId": "e1", "bid": 0.30}])

    def test_product_target_bid_uses_target_endpoint(self):
        conn, c = mk_conn(), FakeClient()
        executor._apply_one(conn, c, _setbid("TARGETING_EXPRESSION"))
        self.assertEqual(c.calls[0][0], "t_bid")
        self.assertEqual(c.calls[0][1], [{"targetId": "e1", "bid": 0.30}])


class StateRouting(unittest.TestCase):
    def test_keyword_pause_uses_keyword_endpoint_and_logs_keyword(self):
        conn, c = mk_conn(), FakeClient()
        executor._apply_one(conn, c, _pause("PHRASE"))
        self.assertEqual(c.calls[0][0], "kw_state")
        action = conn.execute(
            "SELECT action FROM writes_log ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        self.assertEqual(action, "pause_keyword")   # so Undo routes to set_keywords_state

    def test_product_target_pause_uses_target_endpoint(self):
        conn, c = mk_conn(), FakeClient()
        executor._apply_one(conn, c, _pause("TARGETING_EXPRESSION_PREDEFINED"))
        self.assertEqual(c.calls[0][0], "t_state")
        action = conn.execute(
            "SELECT action FROM writes_log ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        self.assertEqual(action, "pause_target")


if __name__ == "__main__":
    unittest.main()
