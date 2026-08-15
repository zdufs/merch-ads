#!/usr/bin/env python3
"""Rules DSL parser + AST (Spec B Layer 1, Task 2)."""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from rules.parser import parse, ParseError  # noqa: E402


class Parser(unittest.TestCase):
    def test_minimal_for_each_if_action(self):
        p = parse("FOR EACH keyword:\n  IF keyword.acos > 45%:\n    keyword.pause()\n")
        self.assertEqual(len(p.rules), 1)
        fe = p.rules[0]
        self.assertEqual(fe.entity, "keyword")
        self.assertEqual(fe.window, "CURRENT")
        self.assertEqual(fe.body[0].__class__.__name__, "If")
        act = fe.body[0].body[0]
        self.assertEqual(act.__class__.__name__, "Action")
        self.assertEqual(act.verb, "pause")

    def test_rolling_window_parses(self):
        # Rolling windows used to be a parse error — the only per-entity data
        # was a cumulative trailing-30 snapshot. target_daily and
        # campaign_daily changed that (Task 6), so keyword IN LAST 7 DAYS is
        # now legal grammar. Which entities may actually use it is checked
        # later, in rules.runner (see tests/rules_rolling_tests.py).
        p = parse("FOR EACH keyword IN LAST 7 DAYS:\n  keyword.pause()\n")
        self.assertEqual(p.rules[0].window, "ROLLING")
        self.assertEqual(p.rules[0].window_days, 7)

    def test_bare_window_number_still_rejected(self):
        with self.assertRaises(ParseError):
            parse("FOR EACH keyword IN 7:\n  keyword.pause()\n")

    def test_lifetime_window(self):
        p = parse('FOR EACH product IN LIFETIME:\n  product.note("x")\n')
        self.assertEqual(p.rules[0].window, "LIFETIME")

    def test_alias(self):
        p = parse("FOR EACH keyword AS k:\n  IF k.acos > 20%:\n    k.pause()\n")
        self.assertEqual(p.rules[0].alias, "k")

    def test_let_and_action_with_call_args(self):
        src = ("FOR EACH keyword:\n"
               "  LET b = keyword.bid * 0.85\n"
               "  IF keyword.orders >= 1 AND keyword.acos > break_even:\n"
               "    keyword.setBid(MAX($0.05, b))\n")
        p = parse(src)
        body = p.rules[0].body
        self.assertEqual(body[0].__class__.__name__, "Let")
        self.assertEqual(body[0].name, "b")
        setbid = body[1].body[0]
        self.assertEqual(setbid.verb, "setBid")
        self.assertEqual(setbid.args[0].__class__.__name__, "Call")

    def test_parse_error_has_line(self):
        with self.assertRaises(ParseError) as cm:
            parse("FOR EACH keyword:\n  IF keyword.acos >:\n    keyword.pause()\n")
        self.assertEqual(cm.exception.line, 2)


if __name__ == "__main__":
    unittest.main()
