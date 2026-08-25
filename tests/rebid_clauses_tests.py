#!/usr/bin/env python3
"""Repairing the close/loose match bid swap must be narrow.

lottery.EXPRESSION_TYPE had Amazon's two query clauses swapped, so every
lottery ad group launched paying the HIGH bid for loose match and the LOW bid
for close match. rebid_clauses.py corrects the ones already live — but ONLY the
ones whose bid was never tuned. A bid the optimizer moved came from that
clause's own sales data and must survive untouched.

Run from the Ads folder:  python3 -m unittest tests.rebid_clauses_tests -v
No Amazon API, no production DB — temp SQLite fixtures only."""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ["ADS_MARKET"] = "US"

import db               # noqa: E402
import rebid_clauses    # noqa: E402

CLOSE = rebid_clauses.CLOSE     # QUERY_HIGH_REL_MATCHES
LOOSE = rebid_clauses.LOOSE     # QUERY_BROAD_REL_MATCHES

# A market whose launch bids were close=0.21 / loose=0.18 under the OLD, wrong
# map: the CLOSE clause was created at 0.18 and the LOOSE one at 0.21.
BIDS = {"close-match": 0.21, "loose-match": 0.18, "substitutes": 0.15}


def temp_conn():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    real = db.DB_PATH
    db.DB_PATH = path
    conn = db.connect()
    db.DB_PATH = real
    return conn, path


def add(conn, tid, text, bid, state="ENABLED", updated="2026-08-19T02:00:00"):
    conn.execute("""INSERT INTO targets(target_id,campaign_id,ad_group_id,kind,text,
        match_type,state,bid,updated_at) VALUES (?,'c1','g1','target',?,NULL,?,?,?)""",
                 (tid, text, state, bid, updated))
    conn.commit()


class SwapDirectionTests(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = temp_conn()

    def tearDown(self):
        self.conn.close()
        os.unlink(self.path)

    def plan(self, **kw):
        return {p["targetId"]: p for p in
                rebid_clauses.build_proposals(self.conn, clause_bids=BIDS, **kw)}

    def test_close_match_is_raised_to_the_close_bid(self):
        add(self.conn, "t1", CLOSE, 0.18)
        p = self.plan()["t1"]
        self.assertEqual(p["new"], 0.21)
        self.assertEqual(p["name"], "close-match")

    def test_loose_match_is_lowered_to_the_loose_bid(self):
        add(self.conn, "t2", LOOSE, 0.21)
        p = self.plan()["t2"]
        self.assertEqual(p["new"], 0.18)
        self.assertEqual(p["name"], "loose-match")

    def test_the_two_swap_places(self):
        add(self.conn, "t1", CLOSE, 0.18)
        add(self.conn, "t2", LOOSE, 0.21)
        plan = self.plan()
        self.assertEqual(plan["t1"]["new"], plan["t2"]["old"])
        self.assertEqual(plan["t2"]["new"], plan["t1"]["old"])


class LeaveTunedBidsAloneTests(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = temp_conn()

    def tearDown(self):
        self.conn.close()
        os.unlink(self.path)

    def plan(self):
        return [p["targetId"] for p in
                rebid_clauses.build_proposals(self.conn, clause_bids=BIDS)]

    def test_a_tuned_bid_is_never_touched(self):
        """0.16 is not a launch value, so sales data put it there."""
        add(self.conn, "t1", CLOSE, 0.16)
        self.assertEqual(self.plan(), [])

    def test_a_bid_moved_since_the_last_pull_is_skipped(self):
        """The mirror can lag a same-day tuning move. Never undo one."""
        add(self.conn, "t1", CLOSE, 0.18)
        db.log_write(self.conn, "bid_change", "target", "t1",
                     "snap=2026-08-19 0.21->0.18 (ACOS 40% > 19%)", "0.21", "submitted")
        self.assertEqual(self.plan(), [])

    def test_a_paused_clause_is_skipped(self):
        add(self.conn, "t1", CLOSE, 0.18, state="PAUSED")
        self.assertEqual(self.plan(), [])

    def test_substitutes_and_complements_are_never_proposed(self):
        """Their mapping was always right — the swap never reached them."""
        add(self.conn, "t3", "ASIN_SUBSTITUTE_RELATED", 0.18)
        add(self.conn, "t4", "ASIN_ACCESSORY_RELATED", 0.21)
        self.assertEqual(self.plan(), [])

    def test_a_clause_already_at_the_right_bid_is_skipped(self):
        add(self.conn, "t1", CLOSE, 0.21)
        add(self.conn, "t2", LOOSE, 0.18)
        self.assertEqual(self.plan(), [])

    def test_nothing_is_proposed_when_the_two_bids_are_equal(self):
        """A market whose schedule rounds both clauses to one value has no swap
        to make — and must not churn every clause writing the same number."""
        add(self.conn, "t1", CLOSE, 0.12)
        add(self.conn, "t2", LOOSE, 0.12)
        flat = {"close-match": 0.12, "loose-match": 0.12, "substitutes": 0.10}
        self.assertEqual(
            rebid_clauses.build_proposals(self.conn, clause_bids=flat), [])


class LimitTests(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = temp_conn()
        for i in range(10):
            add(self.conn, f"t{i}", CLOSE, 0.18)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.path)

    def test_limit_caps_the_plan(self):
        self.assertEqual(
            len(rebid_clauses.build_proposals(self.conn, limit=4, clause_bids=BIDS)), 4)

    def test_no_limit_returns_everything(self):
        self.assertEqual(
            len(rebid_clauses.build_proposals(self.conn, clause_bids=BIDS)), 10)


if __name__ == "__main__":
    unittest.main()
