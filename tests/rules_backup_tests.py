#!/usr/bin/env python3
"""The guard around rule_defs/ — every rule the operator has written.

Thirteen rules live there today, and eleven of them are on AUTO across seven
markets. They are the pauses, the bid-downs and the bleeder cuts that run every
night at 10:00.

_load_index() returns {} when index.json is not there, so a missing rule_defs/
means the nightly evaluates ZERO rules, reports success, and writes nothing.
No error, no log line, no alert. That is the same failure that killed the
seasonal tag map on 2026-08-15 and went unnoticed for six days, except this one
would stop every market's automation at once.

Same two-part guard as seasonal: a backup that can put the rules back, and a
detector that says so when it cannot.

Run from the Ads folder:  python3 -m unittest tests.rules_backup_tests -v
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ["ADS_MARKET"] = "US"

from rules import store  # noqa: E402

RULE = 'FOR EACH keyword:\n  IF keyword.clicks >= 20:\n    keyword.pause()\n'


class StorePaths(unittest.TestCase):
    """Every test works in a throwaway directory.

    The operator's real rule_defs/ is thirteen authored rules driving live
    money. A test that wrote to it could empty it, which is the exact accident
    this guard exists to prevent.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._saved = (store.RULES_DIR, store.INDEX)
        store.RULES_DIR = os.path.join(self.tmp, "rule_defs")
        store.INDEX = os.path.join(store.RULES_DIR, "index.json")
        # BACKUP is DERIVED from RULES_DIR, so redirecting the one above is
        # enough. It used to be a module constant, and every test that
        # redirected only RULES_DIR quietly wrote its fake rules over the
        # operator's real backup.
        self.BACKUP = store._backup_path()

    def tearDown(self):
        store.RULES_DIR, store.INDEX = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def backup(self):
        with open(self.BACKUP) as f:
            return json.load(f)


class Isolation(StorePaths):
    """The backup path must follow RULES_DIR, always.

    It started life as a module constant, and the pre-existing store tests
    redirect only RULES_DIR — so every run wrote their two fake rules over the
    operator's real rule_defs.backup.json. The guard would then have cheerfully
    "restored" those instead of the thirteen real ones. Caught by the suite the
    same hour it was written.
    """

    def test_the_backup_is_a_sibling_of_the_rules_directory(self):
        self.assertEqual(store._backup_path(), store.RULES_DIR + ".backup.json")
        self.assertFalse(store._backup_path().startswith(store.RULES_DIR + os.sep),
                         "inside rule_defs/ it would die with the directory")

    def test_redirecting_the_rules_directory_moves_the_backup_with_it(self):
        store.save_rule("R", RULE)
        self.assertTrue(store._backup_path().startswith(self.tmp),
                        "a test must not be able to reach the real backup by "
                        "redirecting RULES_DIR alone")


class Backup(StorePaths):

    def test_saving_a_rule_writes_a_backup_carrying_its_text(self):
        store.save_rule("Pause dead keywords", RULE, enabled=True, mode="auto")
        b = self.backup()
        self.assertIn("Pause dead keywords", b["index"])
        self.assertEqual(b["rules"]["Pause dead keywords"], RULE)
        self.assertTrue(b["index"]["Pause dead keywords"]["enabled"],
                        "the backup keeps enabled/mode too — restoring a rule "
                        "switched off is not restoring it")

    def test_the_backup_follows_a_mode_change(self):
        store.save_rule("R", RULE, enabled=True, mode="review")
        store.save_rule("R", RULE, enabled=True, mode="auto")
        self.assertEqual(self.backup()["index"]["R"]["mode"], "auto")

    def test_deleting_one_rule_of_two_keeps_the_other_in_the_backup(self):
        store.save_rule("Keep", RULE)
        store.save_rule("Drop", RULE)
        store.delete_rule("Drop")
        b = self.backup()
        self.assertIn("Keep", b["rules"])
        self.assertNotIn("Drop", b["rules"],
                         "a deleted rule must leave the backup, or restoring "
                         "would resurrect it")

    def test_deleting_the_last_rule_prunes_it_from_the_backup(self):
        store.save_rule("Only", RULE)
        store.delete_rule("Only")
        self.assertNotIn("Only", self.backup()["index"],
                         "deleting is deliberate — a backup that kept the rule "
                         "would report a loss forever")

    def test_an_ACCIDENTAL_empty_index_does_not_touch_the_backup(self):
        """The other half of the same coin, and the one that matters.

        delete_rule() is intent, so it prunes. A bare _save_index({}) is what a
        bad merge or a half-written file looks like, and it must leave the
        backup alone — otherwise the backup is destroyed by the very accident it
        exists to undo, one save later.
        """
        store.save_rule("A", RULE, enabled=True, mode="auto")
        store.save_rule("B", RULE, enabled=True, mode="auto")
        store._save_index({})
        b = self.backup()
        self.assertEqual(sorted(b["index"]), ["A", "B"])
        self.assertEqual(b["rules"]["A"], RULE)


class Restore(StorePaths):

    def test_a_deleted_rule_defs_comes_back_from_the_backup(self):
        store.save_rule("Pause dead keywords", RULE, enabled=True, mode="auto")
        shutil.rmtree(store.RULES_DIR)                    # the accident

        rules = store.list_rules()
        self.assertEqual([r["name"] for r in rules], ["Pause dead keywords"])
        self.assertTrue(rules[0]["enabled"])
        self.assertEqual(rules[0]["mode"], "auto")
        self.assertEqual(store.get_rule("Pause dead keywords")["text"], RULE,
                         "the .rule text has to come back too, not just the index")
        self.assertTrue(os.path.exists(store.INDEX), "the restore must land on disk")

    def test_a_fresh_install_stays_empty(self):
        self.assertEqual(store.list_rules(), [],
                         "no backup means nothing was ever written — not a loss")

    def test_an_existing_index_is_never_overwritten(self):
        store.save_rule("A", RULE)
        store.save_rule("B", RULE)
        os.unlink(store._path("B"))          # torn state, index intact
        names = [r["name"] for r in store.list_rules()]
        self.assertEqual(names, ["A", "B"], "an index that exists is the truth")

    def test_deleting_every_rule_on_purpose_is_not_undone(self):
        store.save_rule("Only", RULE)
        store.delete_rule("Only")
        self.assertEqual(store.list_rules(), [],
                         "the index still exists and says zero — putting rules "
                         "back would be a write nobody asked for")


class StdoutContract(StorePaths):
    """appctl promises EXACTLY ONE JSON object on stdout and the app decodes it
    with Codable. A restore that announces itself on stdout turns every read
    that triggers it into "appctl replied, but the app couldn't decode it".

    That is not hypothetical: the first cut of this guard printed to stdout and
    broke `rules-list` the moment it fired.
    """

    def test_the_restore_message_goes_to_stderr(self):
        import contextlib, io
        store.save_rule("R", RULE, enabled=True, mode="auto")
        shutil.rmtree(store.RULES_DIR)

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            store.list_rules()
        self.assertEqual(out.getvalue(), "",
                         "anything on stdout corrupts appctl's JSON envelope")
        self.assertIn("restored", err.getvalue(),
                      "and it still has to be said — silence is the bug we are fixing")


class Detector(StorePaths):

    def test_a_healthy_store_is_not_reported_lost(self):
        store.save_rule("R", RULE)
        self.assertIsNone(store.rules_lost())

    def test_a_fresh_install_is_not_reported_lost(self):
        self.assertIsNone(store.rules_lost())

    def test_an_empty_index_beside_a_backup_with_rules_is_reported(self):
        store.save_rule("A", RULE, enabled=True, mode="auto")
        store.save_rule("B", RULE, enabled=True, mode="auto")
        # index emptied WITHOUT going through delete_rule — a hand edit, a bad
        # merge, a half-finished sync. The backup still holds both.
        with open(store.INDEX, "w") as f:
            json.dump({}, f)
        lost = store.rules_lost()
        self.assertIsNotNone(lost)
        self.assertEqual(lost["backup_rules"], 2)
        self.assertEqual(lost["backup_auto"], 2)
        self.assertIn(".backup.json", lost["reason"])

    def test_deliberate_deletion_of_every_rule_is_not_reported(self):
        store.save_rule("Only", RULE)
        store.delete_rule("Only")
        self.assertIsNone(store.rules_lost(),
                          "delete_rule prunes the backup, so there is nothing "
                          "left to prove a loss — which is correct")

    def test_the_reason_counts_the_auto_rules_separately(self):
        store.save_rule("Auto one", RULE, enabled=True, mode="auto")
        store.save_rule("Off one", RULE, enabled=False, mode="review")
        with open(store.INDEX, "w") as f:
            json.dump({}, f)
        lost = store.rules_lost()
        self.assertEqual(lost["backup_rules"], 2)
        self.assertEqual(lost["backup_auto"], 1,
                         "only the enabled auto rules were writing to Amazon")

    def test_a_corrupt_backup_does_not_raise(self):
        store.save_rule("R", RULE)
        with open(self.BACKUP, "w") as f:
            f.write("{not json")
        with open(store.INDEX, "w") as f:
            json.dump({}, f)
        self.assertIsNone(store.rules_lost(),
                          "an unreadable backup proves nothing — it must not "
                          "take down the alerts path either")


if __name__ == "__main__":
    unittest.main()
