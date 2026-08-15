#!/usr/bin/env python3
"""Rules DSL expression/condition evaluator (Spec B Layer 1, Task 3)."""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from rules.parser import parse_expr             # noqa: E402
from rules.evaluator import eval_expr, eval_condition  # noqa: E402


def E(src, scope=None):
    return eval_expr(parse_expr(src), scope or {})


def C(src, scope=None):
    return eval_condition(parse_expr(src), scope or {})


class Eval(unittest.TestCase):
    def test_arithmetic_units(self):
        self.assertAlmostEqual(E("$0.10 * 2"), 0.20)
        self.assertAlmostEqual(E("45% + 5%"), 0.50)

    def test_none_comparison_is_false(self):
        self.assertFalse(C("x > 0.45", {"x": None}))
        self.assertFalse(C("x < 0.45", {"x": None}))
        self.assertFalse(C("x >= 0", {"x": None}))

    def test_none_equality(self):
        self.assertTrue(C("x = NONE", {"x": None}))
        self.assertFalse(C("x = NONE", {"x": 0.1}))

    def test_functions(self):
        self.assertEqual(E("MAX($0.05, 0.02)"), 0.05)
        self.assertEqual(E("MIN(1.5, 9)"), 1.5)
        self.assertEqual(E("CLAMP(9, 0, 1.5)"), 1.5)
        self.assertEqual(E("ROUND(0.126, 2)"), 0.13)
        self.assertEqual(E('IF(TRUE, 1, 2)'), 1)

    def test_text_ops_case_insensitive(self):
        self.assertTrue(C('name CONTAINS "xmas"', {"name": "Merry XMAS tee"}))
        self.assertTrue(C('name STARTS WITH "merry"', {"name": "Merry XMAS tee"}))
        self.assertFalse(C('name CONTAINS "santa"', {"name": "Merry XMAS tee"}))

    def test_in_list(self):
        self.assertTrue(C('m IN ["EXACT", "PHRASE"]', {"m": "exact"}))
        self.assertTrue(C('m NOT IN ["EXACT"]', {"m": "broad"}))

    def test_and_or_not(self):
        self.assertTrue(C("a AND (b OR NOT c)", {"a": True, "b": False, "c": False}))
        self.assertFalse(C("a AND b", {"a": True, "b": False}))

    def test_field_access_on_dict(self):
        self.assertAlmostEqual(E("keyword.acos", {"keyword": {"acos": 0.32}}), 0.32)


if __name__ == "__main__":
    unittest.main()
