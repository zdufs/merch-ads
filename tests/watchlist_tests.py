#!/usr/bin/env python3
"""Watchlist endpoint: resolve pinned entities into aggregated rows (Spec A f4).
Run from the Ads folder:  python3 -m unittest tests.watchlist_tests -v"""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ["ADS_MARKET"] = "US"

import db        # noqa: E402
import appctl    # noqa: E402


def temp_conn():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    real = db.DB_PATH
    db.DB_PATH = path
    conn = db.connect()
    db.DB_PATH = real
    return conn, path


def seed(conn):
    latest = "2026-07-31"
    conn.executescript("""
        INSERT INTO campaigns(campaign_id,name,state) VALUES ('c1','Lotto 1','ENABLED'),('c2','Lotto 2','ENABLED');
        INSERT INTO ad_groups(ad_group_id,campaign_id,name,state) VALUES ('g1','c1','G1','ENABLED'),('g2','c2','G2','ENABLED');
        INSERT INTO ad_group_product(ad_group_id,asin,product_type) VALUES ('g1','B0AAA','standard_tee'),('g2','B0AAA','standard_tee');
    """)
    conn.execute("""INSERT INTO campaign_perf(date,campaign_id,impressions,clicks,cost,orders,sales,acos)
        VALUES (?,?,?,?,?,?,?,?)""", (latest, "c1", 100, 10, 5.0, 1, 20.0, 0.25))
    conn.execute("""INSERT INTO campaign_perf(date,campaign_id,impressions,clicks,cost,orders,sales,acos)
        VALUES (?,?,?,?,?,?,?,?)""", (latest, "c2", 100, 10, 5.0, 1, 20.0, 0.25))
    for cid, gid in (("c1", "g1"), ("c2", "g2")):
        conn.execute("""INSERT INTO targeting_perf(date,campaign_id,ad_group_id,targeting,match_type,
            target_id,impressions,clicks,cost,orders,sales,acos)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (latest, cid, gid, "auto", "EXACT", "t" + gid, 100, 10, 5.0, 1, 20.0, 0.25))
    conn.commit()


class Watchlist(unittest.TestCase):
    def test_summary_sums_resolved_pins(self):
        conn, path = temp_conn()
        try:
            seed(conn)
            pins = [{"kind": "campaign", "campaign_id": "c1"},
                    {"kind": "campaign", "campaign_id": "c2"}]
            data = appctl._watchlist_rows(conn, pins)
            self.assertEqual(data["summary"]["clicks"], 20)
            self.assertEqual(data["summary"]["orders"], 2)
            self.assertAlmostEqual(data["summary"]["spend"], 10.0)
            self.assertTrue(all(r["resolved"] for r in data["rows"]))
            self.assertEqual(data["rows"][0]["label"], "Lotto 1")
        finally:
            conn.close()
            os.unlink(path)

    def test_asin_pin_sums_across_campaigns(self):
        conn, path = temp_conn()
        try:
            seed(conn)
            data = appctl._watchlist_rows(conn, [{"kind": "asin", "asin": "B0AAA"}])
            row = data["rows"][0]
            self.assertTrue(row["resolved"])
            self.assertEqual(row["clicks"], 20)     # both ad groups
            self.assertEqual(row["orders"], 2)
        finally:
            conn.close()
            os.unlink(path)

    def test_unresolvable_pin_reported_not_crashed(self):
        conn, path = temp_conn()
        try:
            seed(conn)
            data = appctl._watchlist_rows(conn, [{"kind": "campaign", "campaign_id": "ZZZ"}])
            self.assertFalse(data["rows"][0]["resolved"])
            self.assertEqual(data["rows"][0]["clicks"], 0)
        finally:
            conn.close()
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
