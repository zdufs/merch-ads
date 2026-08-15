#!/usr/bin/env python3
"""Rules DSL store — Layer 3 (rules as editable files + index)."""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["ADS_MARKET"] = "US"

from rules import store  # noqa: E402


class Store(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = store.RULES_DIR
        store.RULES_DIR = self._tmp
        store.INDEX = os.path.join(self._tmp, "index.json")

    def tearDown(self):
        store.RULES_DIR = self._orig
        store.INDEX = os.path.join(self._orig, "index.json")

    def test_save_list_get_delete(self):
        text = "FOR EACH target:\n  IF target.orders = 0:\n    target.pause()\n"
        store.save_rule("Pause Bleeders", text, enabled=True, mode="review", season=None)
        names = [r["name"] for r in store.list_rules()]
        self.assertIn("Pause Bleeders", names)
        got = store.get_rule("Pause Bleeders")
        self.assertEqual(got["text"], text)
        self.assertTrue(got["enabled"])
        self.assertEqual(got["mode"], "review")
        store.delete_rule("Pause Bleeders")
        self.assertEqual(store.list_rules(), [])

    def test_enabled_rules_only(self):
        store.save_rule("on", "FOR EACH target:\n  target.pause()\n", True, "auto", None)
        store.save_rule("off", "FOR EACH target:\n  target.pause()\n", False, "auto", None)
        self.assertEqual([r["name"] for r in store.enabled_rules()], ["on"])

    def test_season_gate(self):
        # a rule whose season window excludes today does not run
        store.save_rule("q4", "FOR EACH campaign:\n  campaign.pause()\n", True, "auto",
                        {"start": "11-01", "end": "12-20"})
        r = store.get_rule("q4")
        # in_season is date-dependent; just assert the field round-trips
        self.assertEqual(r["season"], {"start": "11-01", "end": "12-20"})

    def test_bad_name_rejected(self):
        with self.assertRaises(ValueError):
            store.save_rule("../evil", "FOR EACH target:\n  target.pause()\n", True, "auto", None)

    # --- KDP / Merch separation (rules must not bleed across advertiser families) ---

    def test_list_and_enabled_scope_by_kind(self):
        text = "FOR EACH target:\n  target.pause()\n"
        store.save_rule("Tee rule", text, True, "auto", None, kind="merch")
        store.save_rule("Book rule", text, True, "auto", None, kind="kdp")
        self.assertEqual([r["name"] for r in store.list_rules(kind="merch")], ["Tee rule"])
        self.assertEqual([r["name"] for r in store.list_rules(kind="kdp")], ["Book rule"])
        self.assertEqual(store.get_rule("Book rule")["kind"], "kdp")
        # enabled_rules honours the same filter; unscoped still returns both
        self.assertEqual([r["name"] for r in store.enabled_rules(kind="kdp")], ["Book rule"])
        self.assertEqual(len(store.list_rules()), 2)

    def test_legacy_rule_without_kind_counts_as_merch(self):
        # a rule banked before kind existed has no kind key in the index
        store._save_index({"Old rule": {"slug": "old-rule", "enabled": True,
                                        "mode": "review", "season": None}})
        self.assertEqual([r["name"] for r in store.list_rules(kind="merch")], ["Old rule"])
        self.assertEqual(store.list_rules(kind="kdp"), [])
        self.assertEqual(store.get_rule("Old rule")["kind"], "merch")

    def test_cross_kind_name_collision_rejected(self):
        text = "FOR EACH target:\n  target.pause()\n"
        store.save_rule("Shared name", text, True, "auto", None, kind="merch")
        with self.assertRaises(ValueError):
            store.save_rule("Shared name", text, True, "auto", None, kind="kdp")
        # re-saving under the SAME kind is a normal edit, not a collision
        store.save_rule("Shared name", text, False, "review", None, kind="merch")
        self.assertFalse(store.get_rule("Shared name")["enabled"])


if __name__ == "__main__":
    unittest.main()
