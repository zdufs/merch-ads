#!/usr/bin/env python3
"""Cross-rule conflict guard.

Before this existed, two enabled rules that both moved one target's bid both
wrote, and whichever ran last silently won."""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["ADS_MARKET"] = "US"

from rules import conflicts as rc  # noqa: E402


def change(rule, action="setBid", entity_id="t1", kind="target", args=None, label=None):
    return {"rule": rule, "action": action, "entity_kind": kind,
            "entity_id": entity_id, "args": args or [0.20],
            "label": label or entity_id}


class Conflicts(unittest.TestCase):

    def test_two_rules_on_one_target_conflict(self):
        found = rc.find([change("A"), change("B")])
        self.assertEqual(len(found), 1)
        self.assertEqual(len(found[("target", "t1")]), 2)

    def test_one_rule_with_two_statements_is_not_a_conflict(self):
        """A rule that emits setBid and pause for one entity meant to."""
        found = rc.find([change("A"), change("A", action="pause")])
        self.assertEqual(found, {})

    def test_different_entities_do_not_conflict(self):
        found = rc.find([change("A", entity_id="t1"), change("B", entity_id="t2")])
        self.assertEqual(found, {})

    def test_a_pause_conflicts_with_a_bid_move_on_the_same_entity(self):
        """Bidding up something another rule is pausing is still a clash."""
        found = rc.find([change("A", action="setBid"), change("B", action="pause")])
        self.assertEqual(len(found), 1)

    def test_first_rule_in_order_wins(self):
        kept, skipped = rc.resolve([change("A"), change("B")])
        self.assertEqual([c["rule"] for c in kept], ["A"])
        self.assertEqual([c["rule"] for c in skipped], ["B"])
        self.assertEqual(skipped[0]["conflict"]["winner"], "A")
        self.assertEqual(skipped[0]["conflict"]["with"], ["A"])

    def test_the_winner_keeps_all_of_its_own_statements(self):
        """A rule that legitimately emits two changes for one entity must not be
        cut in half by its conflict with someone else."""
        kept, skipped = rc.resolve([
            change("A", action="setBid"),
            change("A", action="pause"),
            change("B", action="setBid"),
        ])
        self.assertEqual([c["action"] for c in kept], ["setBid", "pause"])
        self.assertEqual([c["rule"] for c in skipped], ["B"])

    def test_uncontested_changes_pass_through_untouched(self):
        kept, skipped = rc.resolve([change("A", entity_id="t1"),
                                    change("B", entity_id="t2")])
        self.assertEqual(len(kept), 2)
        self.assertEqual(skipped, [])
        self.assertNotIn("conflict", kept[0])

    def test_annotate_counts_contested_entities_not_rows(self):
        _, count = rc.annotate([change("A", entity_id="t1"), change("B", entity_id="t1"),
                                change("C", entity_id="t1"), change("A", entity_id="t2")])
        self.assertEqual(count, 1)

    def test_annotate_marks_every_side_of_the_clash(self):
        rows, _ = rc.annotate([change("A"), change("B"), change("C")])
        self.assertTrue(all("conflict" in r for r in rows))
        self.assertTrue(rows[0]["conflict"]["kept"])
        self.assertFalse(rows[1]["conflict"]["kept"])
        self.assertEqual(rows[1]["conflict"]["with"], ["A", "C"])

    def test_annotate_does_not_mutate_the_input(self):
        source = [change("A"), change("B")]
        rc.annotate(source)
        self.assertNotIn("conflict", source[0])

    def test_campaign_budget_clash_is_caught_too(self):
        kept, skipped = rc.resolve([
            change("A", action="setBudget", kind="campaign", entity_id="c1"),
            change("B", action="setBudget", kind="campaign", entity_id="c1"),
        ])
        self.assertEqual(len(kept), 1)
        self.assertEqual(skipped[0]["conflict"]["surface"], "budget")

    def test_describe_names_the_rule_the_entity_and_the_winner(self):
        _, skipped = rc.resolve([change("A", label="close-match"),
                                 change("B", label="close-match")])
        d = rc.describe(skipped[0])
        self.assertEqual(d["rule"], "B")
        self.assertEqual(d["winner"], "A")
        self.assertIn("close-match", d["message"])
        self.assertIn("A", d["message"])

    def test_changes_with_no_rule_name_are_left_alone(self):
        """A single rule's own preview has no rule tags — never flag it."""
        found = rc.find([{"action": "setBid", "entity_kind": "target", "entity_id": "t1"},
                         {"action": "setBid", "entity_kind": "target", "entity_id": "t1"}])
        self.assertEqual(found, {})


if __name__ == "__main__":
    unittest.main()
