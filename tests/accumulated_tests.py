#!/usr/bin/env python3
"""Cross-campaign accumulated rollups (Spec A feature 3).
Run from the Ads folder:  python3 -m unittest tests.accumulated_tests -v"""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
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
    """ASIN B0AAA in two campaigns/ad groups on the latest date; plus an older
    date (must be ignored) and a NULL-asin cohort ad group."""
    conn.executescript("""
        INSERT INTO campaigns(campaign_id,name,state) VALUES
            ('c1','C1','ENABLED'),('c2','C2','ENABLED'),('c3','C3','ENABLED');
        INSERT INTO ad_groups(ad_group_id,campaign_id,name,state) VALUES
            ('g1','c1','G1','ENABLED'),('g2','c2','G2','ENABLED'),('g3','c3','G3','ENABLED');
        INSERT INTO ad_group_product(ad_group_id,asin,product_type) VALUES
            ('g1','B0AAA','standard_tee'),('g2','B0AAA','standard_tee'),('g3',NULL,'scavenger');
    """)
    latest, older = "2026-07-31", "2026-07-01"
    rows = [
        (latest, "c1", "g1", "auto", "EXACT", "tg1", 100, 10, 5.0, 1, 20.0, 0.25),
        (latest, "c2", "g2", "auto", "EXACT", "tg2", 100, 10, 5.0, 1, 20.0, 0.25),
        (latest, "c3", "g3", "cohort", "EXACT", "tg3", 50, 5, 2.0, 0, 0.0, None),
        (older,  "c1", "g1", "auto", "EXACT", "tg1", 999, 99, 99.0, 9, 99.0, 1.0),
    ]
    for r in rows:
        conn.execute("""INSERT INTO targeting_perf(date,campaign_id,ad_group_id,targeting,
            match_type,target_id,impressions,clicks,cost,orders,sales,acos)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", r)
    conn.commit()


class AccumAsins(unittest.TestCase):
    def test_sums_across_two_campaigns_latest_only(self):
        conn, path = temp_conn()
        try:
            seed(conn)
            data = appctl._accumulated_asins(conn, limit=500)
            row = next(r for r in data["rows"] if r["asin"] == "B0AAA")
            self.assertEqual(row["campaigns"], 2)
            self.assertEqual(row["ad_groups"], 2)
            self.assertEqual(row["clicks"], 20)        # 10+10, NOT the older 99
            self.assertEqual(row["spend"], 10.0)
            self.assertEqual(row["orders"], 2)
            self.assertAlmostEqual(row["sales"], 40.0)
            self.assertAlmostEqual(row["acos"], 0.25)  # 10/40
        finally:
            conn.close()
            os.unlink(path)

    def test_null_asin_cohort_excluded_from_asin_rows(self):
        conn, path = temp_conn()
        try:
            seed(conn)
            data = appctl._accumulated_asins(conn, limit=500)
            self.assertTrue(all(r["asin"] is not None for r in data["rows"]))
        finally:
            conn.close()
            os.unlink(path)


class Truncation(unittest.TestCase):
    """`count` used to be the FULL total while `rows` was silently cut to the
    limit — the screen's header said 31,814 ASINs above a 500-row table, and
    680 ASINs that actually spent money were missing with no indication."""

    def seed_many(self, conn, n=5):
        conn.execute("INSERT INTO campaigns(campaign_id,name,state) VALUES ('c1','C1','ENABLED')")
        for i in range(n):
            conn.execute("INSERT INTO ad_groups(ad_group_id,campaign_id,name,state)"
                         " VALUES (?,'c1',?,'ENABLED')", (f"g{i}", f"G{i}"))
            conn.execute("INSERT INTO ad_group_product(ad_group_id,asin,product_type)"
                         " VALUES (?,?,'standard_tee')", (f"g{i}", f"B0{i:03d}"))
            conn.execute("""INSERT INTO targeting_perf(date,campaign_id,ad_group_id,targeting,
                match_type,target_id,impressions,clicks,cost,orders,sales,acos)
                VALUES ('2026-07-31','c1',?,?,'EXACT',?,10,1,?,0,0.0,NULL)""",
                         (f"g{i}", f"kw{i}", f"t{i}", float(n - i)))
        conn.commit()

    def test_truncated_result_says_so_and_reports_what_it_returned(self):
        conn, path = temp_conn()
        try:
            self.seed_many(conn, n=5)
            data = appctl._accumulated_asins(conn, limit=2)
            self.assertEqual(data["count"], 5)        # the true total
            self.assertEqual(data["returned"], 2)     # what the caller actually got
            self.assertTrue(data["truncated"])
            self.assertEqual(len(data["rows"]), 2)
        finally:
            conn.close(); os.unlink(path)

    def test_untruncated_result_is_not_flagged(self):
        conn, path = temp_conn()
        try:
            self.seed_many(conn, n=5)
            data = appctl._accumulated_asins(conn, limit=500)
            self.assertEqual(data["returned"], 5)
            self.assertFalse(data["truncated"])
        finally:
            conn.close(); os.unlink(path)

    def test_limit_zero_means_everything(self):
        conn, path = temp_conn()
        try:
            self.seed_many(conn, n=5)
            data = appctl._accumulated_asins(conn, limit=0)
            self.assertEqual(len(data["rows"]), 5)
            self.assertFalse(data["truncated"])
        finally:
            conn.close(); os.unlink(path)

    def test_keywords_report_the_same_shape(self):
        conn, path = temp_conn()
        try:
            self.seed_many(conn, n=5)
            data = appctl._accumulated_keywords(conn, limit=2)
            self.assertEqual(data["count"], 5)
            self.assertEqual(data["returned"], 2)
            self.assertTrue(data["truncated"])
            self.assertFalse(appctl._accumulated_keywords(conn, limit=0)["truncated"])
        finally:
            conn.close(); os.unlink(path)


class AccumKeywords(unittest.TestCase):
    def test_groups_by_targeting(self):
        conn, path = temp_conn()
        try:
            seed(conn)
            data = appctl._accumulated_keywords(conn, limit=500)
            row = next(r for r in data["rows"] if r["targeting"] == "auto")
            self.assertEqual(row["match_type"], "EXACT")
            self.assertEqual(row["campaigns"], 2)
            self.assertEqual(row["clicks"], 20)
        finally:
            conn.close()
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
