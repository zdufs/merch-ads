#!/usr/bin/env python3
"""Archiving a campaign is PERMANENT — Amazon has no un-archive.

So the command refuses without an explicit --confirm (a typo'd id must not be
able to destroy a campaign), and `archive_campaign` is deliberately absent from
UNDOABLE so the Audit Trail never offers an Undo button it cannot honour.

Run from the Ads folder:  python3 -m unittest tests.archive_campaign_tests -v"""

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ["ADS_MARKET"] = "US"

import appctl  # noqa: E402


class Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class ConfirmGuard(unittest.TestCase):
    def test_without_confirm_it_refuses_before_touching_amazon(self):
        buf = io.StringIO()
        with self.assertRaises(SystemExit), redirect_stdout(buf):
            appctl.cmd_archive_campaign(Args(campaign="123", confirm=False))
        payload = json.loads(buf.getvalue())
        self.assertFalse(payload["ok"])
        self.assertIn("permanent", payload["error"].lower())
        self.assertIn("--confirm", payload["error"])

    def test_the_command_exists_and_takes_confirm(self):
        parser = appctl.build_parser()
        ns = parser.parse_args(["archive-campaign", "--campaign", "42", "--confirm"])
        self.assertEqual(ns.cmd, "archive-campaign")
        self.assertEqual(ns.campaign, "42")
        self.assertTrue(ns.confirm)

    def test_confirm_defaults_to_false(self):
        parser = appctl.build_parser()
        ns = parser.parse_args(["archive-campaign", "--campaign", "42"])
        self.assertFalse(ns.confirm)


class NotUndoable(unittest.TestCase):
    def test_archive_is_never_offered_an_undo(self):
        # pause/enable are reversible and listed; archive must never be, or the
        # Audit Trail would show an Undo button that Amazon cannot honour.
        self.assertIn("pause_campaign", appctl.UNDOABLE)
        self.assertNotIn("archive_campaign", appctl.UNDOABLE)


if __name__ == "__main__":
    unittest.main()
