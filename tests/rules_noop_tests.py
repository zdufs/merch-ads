#!/usr/bin/env python3
"""No-op protection + honest prev_state (the MerchDash "skip phantom changes"
gap we were missing).

Before this, a rule that paused an already-paused entity still called Amazon,
burned the change cap, and logged prev_state="ENABLED" (a guess) — so Undo would
ENABLE something that was deliberately off. Now the change is dropped at preview
(runner._is_noop) and guarded again at apply (executor), and a real change logs
the entity's actual previous state.

Run from the Ads folder:  python3 -m unittest tests.rules_noop_tests -v
"""

import os
import sqlite3
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ["ADS_MARKET"] = "US"

import db  # noqa: E402
from rules import executor  # noqa: E402
from rules.runner import _is_noop  # noqa: E402


def mk_conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(db.SCHEMA)
    conn.execute("INSERT INTO ad_groups (ad_group_id, campaign_id, name, state, "
                 "default_bid) VALUES ('ag1','c1','AG','ENABLED',0.5)")
    conn.commit()
    return conn


class FakeClient:
    """Records every Amazon call so a no-op can be proven to make none."""
    def __init__(self):
        self.calls = []
        self.last_clamps = []

    def _ok(self, ids):
        return [{"http": 200, "failed_items": 0, "failed_ids": []}]

    def set_ad_groups_state(self, ids, state):
        self.calls.append(("adgroup", list(ids), state)); return self._ok(ids)

    def set_targets_state(self, ids, state):
        self.calls.append(("target", list(ids), state)); return self._ok(ids)

    def update_target_bids(self, items):
        self.calls.append(("bid", items)); return self._ok(items)


class IsNoop(unittest.TestCase):
    def test_pause_already_paused_is_a_noop(self):
        self.assertTrue(_is_noop("pause", [], "PAUSED", None))

    def test_pause_enabled_is_not_a_noop(self):
        self.assertFalse(_is_noop("pause", [], "ENABLED", None))

    def test_enable_already_enabled_is_a_noop(self):
        self.assertTrue(_is_noop("enable", [], "ENABLED", None))

    def test_unknown_state_is_never_a_noop(self):
        # Better a redundant write than a silently dropped one.
        self.assertFalse(_is_noop("pause", [], None, None))

    def test_same_bid_is_a_noop_different_is_not(self):
        self.assertTrue(_is_noop("setBid", [0.50], None, 0.50))
        self.assertFalse(_is_noop("setBid", [0.55], None, 0.50))
        self.assertFalse(_is_noop("setBid", [0.50], None, None))


class ExecutorGuard(unittest.TestCase):
    def _pause(self, prev_state):
        return {"action": "pause", "entity_kind": "adgroup", "entity_id": "ag1",
                "prev_state": prev_state, "ref": {"ad_group_id": "ag1"}, "note": "x"}

    def test_pausing_already_paused_makes_no_call_and_no_log(self):
        conn, client = mk_conn(), FakeClient()
        res = executor._apply_one(conn, client, self._pause("PAUSED"))
        self.assertTrue(res.get("noop"))
        self.assertEqual(client.calls, [])
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM writes_log").fetchone()[0], 0)

    def test_real_pause_logs_the_true_prev_state(self):
        conn, client = mk_conn(), FakeClient()
        res = executor._apply_one(conn, client, self._pause("ENABLED"))
        self.assertTrue(res.get("ok") and not res.get("noop"))
        self.assertEqual(client.calls[0][0], "adgroup")
        prev = conn.execute(
            "SELECT prev_state FROM writes_log ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        self.assertEqual(prev, "ENABLED")

    def test_unknown_prev_state_still_writes(self):
        conn, client = mk_conn(), FakeClient()
        res = executor._apply_one(conn, client, self._pause(None))
        self.assertTrue(res.get("ok") and not res.get("noop"))
        self.assertEqual(client.calls[0][0], "adgroup")

    def test_setbid_to_same_value_is_a_noop(self):
        conn, client = mk_conn(), FakeClient()
        ch = {"action": "setBid", "entity_kind": "target", "entity_id": "t1",
              "args": [0.50], "prev_bid": 0.50, "ref": {"target_id": "t1"}, "note": "x"}
        res = executor._apply_one(conn, client, ch)
        self.assertTrue(res.get("noop"))
        self.assertEqual(client.calls, [])

    def test_real_setbid_records_old_to_new_for_undo(self):
        conn, client = mk_conn(), FakeClient()
        ch = {"action": "setBid", "entity_kind": "target", "entity_id": "t1",
              "args": [0.60], "prev_bid": 0.50, "ref": {"target_id": "t1"}, "note": "x"}
        res = executor._apply_one(conn, client, ch)
        self.assertTrue(res.get("ok"))
        detail = conn.execute("SELECT detail FROM writes_log WHERE action='bid_change' "
                              "ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        self.assertIn("0.5->0.6", detail)   # old->new, so restore_bid can parse it


if __name__ == "__main__":
    unittest.main()
