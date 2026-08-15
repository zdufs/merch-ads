#!/usr/bin/env python3
"""Rules DSL pending-approval store (Review mode → Approval queue)."""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ["ADS_MARKET"] = "US"

from rules import pending  # noqa: E402


def _change(kind, eid, action, rule="R"):
    return {"entity_kind": kind, "entity_id": eid, "label": eid, "action": action,
            "args": [], "note": "why", "trace": [], "econ_driven": False,
            "ref": {"campaign_id": "c1", "ad_group_id": "g1", "target_id": eid, "asin": None}}


class Pending(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = pending.OUTDIR
        pending.OUTDIR = self._tmp

    def tearDown(self):
        pending.OUTDIR = self._orig

    def test_set_rule_tags_id_and_rule(self):
        pending.set_rule("US", "Pause Bleeders", [_change("target", "t1", "pause")])
        data = pending.load("US")
        self.assertEqual(len(data["changes"]), 1)
        c = data["changes"][0]
        self.assertEqual(c["rule"], "Pause Bleeders")
        self.assertTrue(c["id"])

    def test_set_rule_replaces_only_that_rule(self):
        pending.set_rule("US", "A", [_change("target", "t1", "pause")])
        pending.set_rule("US", "B", [_change("target", "t2", "pause")])
        pending.set_rule("US", "A", [_change("target", "t9", "pause")])   # replace A
        ids = {c["entity_id"] for c in pending.load("US")["changes"]}
        self.assertEqual(ids, {"t9", "t2"})   # A's t1 replaced by t9; B's t2 kept

    def test_remove_by_id(self):
        pending.set_rule("US", "A", [_change("target", "t1", "pause"),
                                     _change("target", "t2", "pause")])
        ids = [c["id"] for c in pending.load("US")["changes"]]
        pending.remove("US", [ids[0]])
        left = [c["entity_id"] for c in pending.load("US")["changes"]]
        self.assertEqual(left, ["t2"])

    def test_id_changes_when_args_change(self):
        """A re-collect that changes setBid 0.30 → 0.45 must mint a NEW id.
        If the id stayed stable, an operator approving the id they saw would
        apply whatever value is on disk now — approval must bind the VALUE."""
        a = _change("target", "t1", "setBid"); a["args"] = [0.30]
        b = _change("target", "t1", "setBid"); b["args"] = [0.45]
        pending.set_rule("US", "A", [a])
        id_a = pending.load("US")["changes"][0]["id"]
        pending.set_rule("US", "A", [b])
        id_b = pending.load("US")["changes"][0]["id"]
        self.assertNotEqual(id_a, id_b)

    def test_per_market_isolation(self):
        pending.set_rule("US", "A", [_change("target", "t1", "pause")])
        self.assertEqual(pending.load("DE")["changes"], [])

    def test_remove_rule_drops_only_that_rules_proposals(self):
        """A deleted rule must not leave a write the operator can still approve."""
        pending.set_rule("US", "A", [_change("target", "t1", "pause")])
        pending.set_rule("US", "B", [_change("target", "t2", "pause")])
        pending.remove_rule("US", "A")
        left = pending.load("US")["changes"]
        self.assertEqual([c["rule"] for c in left], ["B"])

    def test_keep_only_prunes_rules_that_stopped_being_collected(self):
        """set_rule replaces its own rule's rows and nothing else, so a rule that
        is disabled, switched to auto, out of season or deleted used to leave its
        last proposals in the queue for good."""
        pending.set_rule("US", "A", [_change("target", "t1", "pause")])
        pending.set_rule("US", "B", [_change("target", "t2", "pause")])
        pending.set_rule("US", "C", [_change("target", "t3", "pause")])
        pruned = pending.keep_only("US", ["B"])
        self.assertEqual(pruned, 2)
        self.assertEqual([c["rule"] for c in pending.load("US")["changes"]], ["B"])

    def test_keep_only_with_nothing_collected_empties_the_queue(self):
        pending.set_rule("US", "A", [_change("target", "t1", "pause")])
        self.assertEqual(pending.keep_only("US", []), 1)
        self.assertEqual(pending.load("US")["changes"], [])

    def test_keep_only_leaves_a_matching_queue_alone(self):
        pending.set_rule("US", "A", [_change("target", "t1", "pause")])
        self.assertEqual(pending.keep_only("US", ["A"]), 0)
        self.assertEqual(len(pending.load("US")["changes"]), 1)


if __name__ == "__main__":
    unittest.main()
