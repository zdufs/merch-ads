#!/usr/bin/env python3
"""Debug-trace fields on preview endpoints (Spec A feature 2).
Run from the Ads folder:  python3 -m unittest tests.trace_tests -v"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["ADS_MARKET"] = "US"

import appctl  # noqa: E402


class Cond(unittest.TestCase):
    def test_cond_shape_and_pass(self):
        c = appctl._cond("cvr < floor", 0.06, 0.08, 0.06 < 0.08)
        self.assertEqual(c["condition"], "cvr < floor")
        self.assertEqual(c["actual"], 0.06)
        self.assertEqual(c["threshold"], 0.08)
        self.assertTrue(c["pass"])

    def test_cond_null_actual(self):
        c = appctl._cond("acos > be", None, 0.41, False)
        self.assertIsNone(c["actual"])
        self.assertFalse(c["pass"])


class ResetTrace(unittest.TestCase):
    def test_reset_item_trace(self):
        item = {"targetId": "T", "original": 0.50, "current": 0.90, "new": 0.45}
        traced = appctl._reset_trace(item)
        self.assertEqual(traced["trace"][0]["condition"], "current > original")
        self.assertEqual(traced["trace"][0]["actual"], 0.90)
        self.assertEqual(traced["trace"][0]["threshold"], 0.50)
        self.assertTrue(traced["trace"][0]["pass"])
        # additive: original fields preserved
        self.assertEqual(traced["new"], 0.45)


class NegPauseTrace(unittest.TestCase):
    def test_neg_waste_trace(self):
        t = appctl._neg_trace({"rule": "waste", "clicks": 14, "orders": 0, "min_clicks": 10})
        self.assertEqual([c["condition"] for c in t], ["clicks >= min", "orders == 0"])
        self.assertTrue(all(c["pass"] for c in t))

    def test_neg_acos_trace(self):
        t = appctl._neg_trace({"rule": "acos", "acos": 0.52, "ceiling": 0.30})
        self.assertEqual(t[0]["condition"], "acos > ceiling")
        self.assertTrue(t[0]["pass"])

    def test_pause_acos_cvr_trace(self):
        t = appctl._pause_trace({"rule": "acos_cvr", "acos": 0.40, "target": 0.20,
                                 "cvr": 0.03, "cvr_floor": 0.08})
        self.assertEqual([c["condition"] for c in t], ["acos > target", "cvr < floor"])
        self.assertTrue(all(c["pass"] for c in t))

    def test_none_metrics(self):
        self.assertIsNone(appctl._neg_trace(None))
        self.assertIsNone(appctl._pause_trace(None))


if __name__ == "__main__":
    unittest.main()
