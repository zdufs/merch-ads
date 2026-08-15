#!/usr/bin/env python3
"""Unit tests for the per-market max-bid ceiling (Spec A feature 1).
Run from the Ads folder:  python3 -m unittest tests.maxbid_tests -v
No Amazon API, no production DB — temp SQLite fixtures only."""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["ADS_MARKET"] = "US"

import db  # noqa: E402


def temp_conn():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    real = db.DB_PATH
    db.DB_PATH = path
    conn = db.connect()                        # migrates: creates engine_meta
    db.DB_PATH = real
    return conn, path


class Ceiling(unittest.TestCase):
    def test_roundtrip_and_default_none(self):
        conn, path = temp_conn()
        try:
            self.assertIsNone(db.get_bid_ceiling(conn, "target"))
            db.set_bid_ceiling(conn, "target", 1.20)
            self.assertEqual(db.get_bid_ceiling(conn, "target"), 1.20)
            self.assertIsNone(db.get_bid_ceiling(conn, "keyword"))
            db.set_bid_ceiling(conn, "keyword", 0.90)
            self.assertEqual(db.get_bid_ceiling(conn, "keyword"), 0.90)
        finally:
            conn.close()
            os.unlink(path)

    def test_clear(self):
        conn, path = temp_conn()
        try:
            db.set_bid_ceiling(conn, "target", 1.0)
            db.set_bid_ceiling(conn, "target", None)
            self.assertIsNone(db.get_bid_ceiling(conn, "target"))
        finally:
            conn.close()
            os.unlink(path)

    def test_bad_surface_raises(self):
        conn, path = temp_conn()
        try:
            with self.assertRaises(ValueError):
                db.get_bid_ceiling(conn, "campaign")
        finally:
            conn.close()
            os.unlink(path)


class Clamp(unittest.TestCase):
    def _client_with(self, target_cap=None, keyword_cap=None):
        # Build an AdsClient without touching .env / Amazon: bypass __init__.
        import ads_client
        c = ads_client.AdsClient.__new__(ads_client.AdsClient)
        c._ceilings = {"target": target_cap, "keyword": keyword_cap}
        c.last_clamps = []
        return c

    def test_clamp_math_caps_above_and_passes_below(self):
        c = self._client_with(target_cap=1.20)
        self.assertEqual(c._apply_ceiling("target", "T1", 3.00), 1.20)
        self.assertEqual(c._apply_ceiling("target", "T2", 0.80), 0.80)
        self.assertEqual(len(c.last_clamps), 1)
        self.assertEqual(c.last_clamps[0], {"id": "T1", "requested": 3.00, "cap": 1.20})

    def test_no_ceiling_no_clamp(self):
        c = self._client_with(target_cap=None)
        self.assertEqual(c._apply_ceiling("target", "T1", 9.99), 9.99)
        self.assertEqual(c.last_clamps, [])


class DetailSuffix(unittest.TestCase):
    def test_prefix_strips_cap_suffix(self):
        human = "snap=live 2.00->1.20 (manual) [adjusted]"
        full = human + ' cap_v1={"req":2.0,"cap":1.2}'
        self.assertEqual(db.detail_prefix(full), human)

    def test_prefix_still_strips_econ_suffix(self):
        human = "snap=app 0.80->0.68 (bid down)"
        full = human + ' econ_v1={"price":2199}'
        self.assertEqual(db.detail_prefix(full), human)

    def test_prefix_noop_on_plain(self):
        self.assertEqual(db.detail_prefix("snap=app 1.00->0.90 (x)"),
                         "snap=app 1.00->0.90 (x)")


if __name__ == "__main__":
    unittest.main()
