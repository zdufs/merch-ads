#!/usr/bin/env python3
"""Every alert kind the engine can emit must be one the app KNOWS.

The app has two safety nets for an alert kind it has never heard of: the
notification title falls back to "Merch Ads", and "Review →" falls back to the
Dashboard. Both are the right behaviour for an engine that is newer than the
app. Both are also SILENT, so a kind that was never wired up looks exactly like
one that was — a real warning arrives wearing the app's own name and lands on a
screen that has nothing to do with it.

That is not hypothetical. `aws_plan_expiry` was added to the engine on
2026-08-21 and neither Swift file learned about it; the review on 2026-08-22
found it by listing both sides and diffing them by hand. Doing that by hand is
the part that does not survive, so it is done here instead.

`MerchAdsTests/AlertRoutingTests.swift` pins the ROUTE each kind takes — that is
a judgement about where the operator should land, and it belongs in Swift. This
test asks the cheaper question that no Swift test can: is there any kind at all
the app has never been told about? It reads the engine as the source of truth,
so adding a kind to `appctl.py` and nothing else fails here.

Run from the Ads folder:  python3 -m unittest tests.app_alert_contract_tests -v
"""

import ast
import os
import re
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPCTL = os.path.join(HERE, "engine", "appctl.py")
APPSTATE = os.path.join(HERE, "MerchAds", "State", "AppState.swift")
ISSUECENTER = os.path.join(HERE, "MerchAds", "State", "IssueCenter.swift")


def engine_alert_kinds():
    """Every `"kind": "..."` literal built inside an alert builder.

    Scoped to `cmd_alerts` and the `_*_alerts` helpers it calls, because "kind"
    is a common key elsewhere in appctl — markets have a kind, harvest winners
    have a kind, the everywhere-plan has a kind. Widening the scan to the whole
    file would collect those and this test would demand the app handle
    "keyword" as an alert.
    """
    with open(APPCTL, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    kinds = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if not (node.name == "cmd_alerts" or node.name.endswith("_alerts")):
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Dict):
                continue
            for k, v in zip(sub.keys, sub.values):
                if (isinstance(k, ast.Constant) and k.value == "kind"
                        and isinstance(v, ast.Constant)
                        and isinstance(v.value, str)):
                    kinds.add(v.value)
    return kinds


def swift_case_literals(path):
    """The string literals of `case "...":` arms in one Swift file."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    return set(re.findall(r'case\s+"([a-z_]+)"\s*:', text))


class TheAppKnowsEveryAlertKindTheEngineSends(unittest.TestCase):

    def test_every_kind_has_a_notification_title(self):
        missing = sorted(engine_alert_kinds() - swift_case_literals(APPSTATE))
        self.assertEqual(
            missing, [],
            "AppState.title(for:) has no case for these alert kinds, so each "
            "one reaches the operator titled \"Merch Ads\": "
            f"{missing}")

    def test_every_kind_has_a_review_destination(self):
        missing = sorted(engine_alert_kinds() - swift_case_literals(ISSUECENTER))
        self.assertEqual(
            missing, [],
            "IssueDerivation.alertRoute has no case for these alert kinds, so "
            "\"Review →\" drops the operator on the Dashboard instead of the "
            f"screen that shows the problem: {missing}")


class TheLintCannotQuietlyBecomeANoOp(unittest.TestCase):
    """Both halves of this check read code by pattern, and a pattern that stops
    matching passes forever while saying nothing."""

    def test_the_engine_side_found_real_kinds(self):
        kinds = engine_alert_kinds()
        self.assertGreaterEqual(len(kinds), 8,
                                "the alert-builder scan found almost nothing — "
                                "the function naming changed, not the engine")
        for expected in ("data_stale", "kill_candidate", "aws_plan_expiry"):
            self.assertIn(expected, kinds,
                          f"{expected} is emitted by appctl but the scan missed it")

    def test_the_swift_side_found_real_cases(self):
        for path in (APPSTATE, ISSUECENTER):
            with self.subTest(file=os.path.basename(path)):
                self.assertTrue(os.path.exists(path), f"{path} moved")
                cases = swift_case_literals(path)
                self.assertGreaterEqual(
                    len(cases), 8,
                    "the Swift case scan found almost nothing, so the "
                    "comparison above is against an empty set and passes "
                    "whatever the engine does")

    def test_an_unhandled_kind_would_actually_fail(self):
        """The check itself, run against a kind the app cannot know."""
        invented = engine_alert_kinds() | {"kind_the_app_has_never_seen"}
        self.assertTrue(invented - swift_case_literals(APPSTATE),
                        "a kind absent from Swift did not show up as missing — "
                        "the set difference is not doing anything")
