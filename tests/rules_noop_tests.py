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


class ProposedMoneyIsTheMoneyThatGetsWritten(unittest.TestCase):
    """A preview must not show a bid the account will never receive.

    `setBid keyword.bid * 1.25` lands on 0.187. The executor rounded it to 0.19
    on the way out and the preview did not, so the Approval Queue offered the
    operator a number Amazon was never going to be sent. Found 2026-08-22 on a
    live auto rule: the queue said 0.187, the write was 0.19.

    Rounding now happens where the change is built, so preview, no-op check,
    queue and write all speak about one number.
    """

    def test_a_bid_is_rounded_to_cents_when_the_change_is_built(self):
        from rules.runner import _round_money
        self.assertEqual(_round_money("setBid", [0.187]), [0.19])
        self.assertEqual(_round_money("setBid", [0.1849]), [0.18])

    def test_a_budget_is_rounded_too(self):
        from rules.runner import _round_money
        self.assertEqual(_round_money("setBudget", [12.3456]), [12.35])

    def test_nothing_else_is_touched(self):
        """A negative's text must survive intact — it is not money.

        The phrase is deliberately nonsense. A fixture written from real data
        publishes a keyword the account actually bids on, which is the most
        commercially useful thing an ads repository can give away and is
        invisible to every other check because it is ordinary English.
        """
        from rules.runner import _round_money
        self.assertEqual(_round_money("addNegative", ["wobbling teapot brigade"]),
                         ["wobbling teapot brigade"])
        self.assertEqual(_round_money("pause", []), [])

    def test_an_unroundable_argument_is_left_alone_rather_than_dropped(self):
        from rules.runner import _round_money
        self.assertEqual(_round_money("setBid", ["not a number"]),
                         ["not a number"])

    def test_the_preview_never_proposes_more_than_two_decimals(self):
        """The end-to-end shape, not just the helper.

        A rule whose arithmetic lands on a third decimal must still reach the
        change list at cents.
        """
        from rules import runner
        conn = mk_conn()
        conn.execute("INSERT INTO campaigns (campaign_id, name, state) "
                     "VALUES ('c1','C','ENABLED')")
        conn.execute("INSERT INTO targets (target_id, campaign_id, ad_group_id, "
                     "kind, text, match_type, state, bid) "
                     "VALUES ('t1','c1','ag1','keyword','cats','EXACT',"
                     "'ENABLED',0.15)")
        conn.execute("INSERT INTO targeting_perf (date, campaign_id, ad_group_id, "
                     "targeting, match_type, target_id, impressions, clicks, "
                     "cost, orders, sales, acos) "
                     "VALUES ('2026-08-20','c1','ag1','cats','EXACT','t1',"
                     "100,5,1.0,1,10.0,0.1)")
        conn.commit()
        res = runner.preview(conn,
                             'FOR EACH keyword:\n'
                             '  IF keyword.state = "ENABLED":\n'
                             '    keyword.setBid(keyword.bid * 1.25)\n')
        self.assertTrue(res.get("ok"), res.get("errors"))
        self.assertTrue(res.get("changes"),
                        "the fixture proposed nothing, so this test would "
                        "pass without ever looking at a bid")
        for ch in res["changes"]:
            if ch["action"] != "setBid":
                continue
            value = float(ch["args"][0])
            self.assertEqual(round(value, 2), value,
                             f"preview proposed {value!r}, which Amazon cannot "
                             f"accept and the executor would silently change")


if __name__ == "__main__":
    unittest.main()
