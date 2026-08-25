#!/usr/bin/env python3
"""A detector that watches for a silent failure must not fail silently itself.

Two alerts exist because operator DATA went missing and nothing said so:

  `seasonal_tags_lost` — the tag map was deleted on 2026-08-15 and the
  scheduler ran as a silent no-op for six days.
  `rules_lost` — an empty `rule_defs/` reads exactly like a fresh install: the
  nightly evaluates nothing, reports success, and writes nothing.

Both builders wrapped their own call in `except Exception: return []`. An empty
list is what the feed carries when everything is FINE, so a renamed column or
any bug inside the detector would take it off duty for good and nothing
anywhere would say so. The alert built to stop a six-day silent failure could
itself fail silently, in precisely the same shape.

The project had already reasoned this through once, for
`stream_check_failed`: "the check that watches for a SILENT failure must not
fail silently itself." That reasoning was never carried across to these two —
found by the 2026-08-23 audit, third pass, by listing every `_*_alerts`
builder and asking what each does with its own exception. Six of the eight were
already right.

Run from the Ads folder:  python3 -m unittest tests.detector_failure_tests -v
"""

import os
import sqlite3
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ["ADS_MARKET"] = "US"

import db       # noqa: E402
import appctl   # noqa: E402


def mk_conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(db.SCHEMA)
    return conn


class _Boom(Exception):
    """Stands in for a renamed column, a schema change, or any bug at all."""


class _Swap:
    """Replace an attribute for the duration of one test."""

    def __init__(self):
        self._undo = []

    def set(self, obj, name, value):
        self._undo.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def restore(self):
        for obj, name, old in reversed(self._undo):
            setattr(obj, name, old)


class SeasonalDetectorSpeaksWhenItBreaks(unittest.TestCase):

    def setUp(self):
        self.swap = _Swap()
        self.addCleanup(self.swap.restore)
        import seasonal_pause
        self.mod = seasonal_pause

    def test_an_exception_raises_an_alert_rather_than_nothing(self):
        self.swap.set(self.mod, "tags_lost",
                      lambda *a, **k: (_ for _ in ()).throw(_Boom("column gone")))
        alerts = appctl._seasonal_tags_alerts(mk_conn(), "US")
        self.assertEqual(len(alerts), 1,
                         "an empty list here is what a HEALTHY account looks "
                         "like, so a broken detector would be invisible")
        self.assertEqual(alerts[0]["kind"], "guard_check_failed")
        self.assertIn("_Boom", alerts[0]["key"],
                      "the exception type belongs in the key, so a persistent "
                      "fault alerts once instead of on every poll")
        self.assertIn("column gone", alerts[0]["message"])

    def test_a_working_detector_with_nothing_lost_still_says_nothing(self):
        self.swap.set(self.mod, "tags_lost", lambda *a, **k: None)
        self.assertEqual(appctl._seasonal_tags_alerts(mk_conn(), "US"), [])

    def test_a_real_loss_still_raises_its_own_alert_not_this_one(self):
        self.swap.set(self.mod, "tags_lost",
                      lambda *a, **k: {"reason": "the tag map is empty",
                                       "stranded": 3})
        alerts = appctl._seasonal_tags_alerts(mk_conn(), "US")
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["kind"], "seasonal_tags_lost",
                         "the failure kind must not swallow the real one")


class RulesDetectorSpeaksWhenItBreaks(unittest.TestCase):

    def setUp(self):
        self.swap = _Swap()
        self.addCleanup(self.swap.restore)
        from rules import store
        self.store = store

    def test_an_exception_raises_an_alert_rather_than_nothing(self):
        self.swap.set(self.store, "rules_lost",
                      lambda *a, **k: (_ for _ in ()).throw(_Boom("index unreadable")))
        alerts = appctl._rules_lost_alerts("US")
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["kind"], "guard_check_failed")
        self.assertIn("rules", alerts[0]["key"])
        self.assertIn("index unreadable", alerts[0]["message"])

    def test_a_working_detector_with_nothing_lost_still_says_nothing(self):
        self.swap.set(self.store, "rules_lost", lambda *a, **k: None)
        self.assertEqual(appctl._rules_lost_alerts("US"), [])

    def test_a_real_loss_still_raises_its_own_alert_not_this_one(self):
        self.swap.set(self.store, "rules_lost",
                      lambda *a, **k: {"reason": "no rules load"})
        alerts = appctl._rules_lost_alerts("US")
        self.assertEqual(alerts[0]["kind"], "rules_lost")

    def test_it_is_reported_from_the_default_market_only(self):
        """rule_defs/ is one global directory. Seven markets raising seven
        copies of one sentence is how an alert gets muted."""
        self.swap.set(self.store, "rules_lost",
                      lambda *a, **k: (_ for _ in ()).throw(_Boom("x")))
        self.assertEqual(appctl._rules_lost_alerts("DE"), [])


def _handler_names(node):
    """The exception names an `except` clause catches, as a set."""
    import ast
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Tuple):
        return {e.id for e in node.elts if isinstance(e, ast.Name)}
    return set()


class NoDetectorSwallowsItsOwnFailure(unittest.TestCase):
    """The sweep that found this, kept as a test.

    Every `_*_alerts` builder is read, and any that answers its own exception
    with a bare empty list fails here. That is the shape that makes a broken
    detector indistinguishable from a healthy account.
    """

    @staticmethod
    def _silent_builders():
        import ast
        with open(os.path.join(HERE, "engine", "appctl.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        bad = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or not node.name.endswith("_alerts"):
                continue
            for h in ast.walk(node):
                if not isinstance(h, ast.ExceptHandler):
                    continue
                # Two different shapes, judged differently.
                #
                # RETURNING an empty collection ends the whole detector, so it
                # is refused whatever the handler catches. The first version
                # only recognised `Exception`/`BaseException` BY NAME, so
                # `except (TypeError, ValueError): return []` was invisible —
                # and that was exactly the AWS plan-expiry detector's shape,
                # sitting inside this file's own subject matter while this test
                # reported the class closed (found by review, 2026-08-23).
                # Narrowing the exception type does not make it safe: the reply
                # is still the one the feed carries when all is well.
                #
                # `pass` / `continue` is only refused for a BROAD handler. In a
                # per-item loop a narrow, named catch skips one item and lets
                # the rest run, which is a deliberate decision and not a
                # detector going quiet — `_staleness_alerts` does exactly that
                # for a perf table missing from an older database, and says so.
                broad = h.type is None or _handler_names(h.type) & {
                    "Exception", "BaseException"}
                body = h.body
                empty = (len(body) == 1 and isinstance(body[0], ast.Return)
                         and isinstance(body[0].value, (ast.List, ast.Dict))
                         and not (getattr(body[0].value, "elts", None)
                                  or getattr(body[0].value, "keys", None)))
                nothing = len(body) == 1 and isinstance(body[0], (ast.Pass, ast.Continue))
                if empty or (nothing and broad):
                    bad.append(f"{node.name}:{h.lineno}")
        return sorted(bad)

    def test_the_scan_finds_the_builders(self):
        import ast
        with open(os.path.join(HERE, "engine", "appctl.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        names = [n.name for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name.endswith("_alerts")]
        self.assertGreater(len(names), 5,
                           "found almost no alert builders — the scan has "
                           "stopped matching how they are named")

    def test_none_of_them_returns_a_bare_empty_list_on_exception(self):
        self.assertEqual(
            self._silent_builders(), [],
            "these alert builders answer their own exception with an empty "
            "list, which is exactly what the feed carries when everything is "
            "fine. Raise a `guard_check_failed` naming the fault instead — the "
            "reasoning is written out at `stream_check_failed`.")


if __name__ == "__main__":
    unittest.main()
