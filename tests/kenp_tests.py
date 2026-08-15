#!/usr/bin/env python3
"""KENP wiring — Kindle Edition Normalized Pages read + royalties on the perf
tables, banked KDP-only, exposed as the DSL `kenp` / `kenp_royalties` fields.

Covers the four seams the wiring touches:
  1. store_targeting_perf maps the two KENP report keys into the columns, and a
     Merch-style row without those keys stores 0 (db._f), never NULL or an error.
  2. _migrate adds the columns to an old DB and is idempotent (run it twice).
  3. entities loads `kenp` for a target from a synthetic targeting_perf row.
  4. runner accepts `kenp` in KNOWN_FIELDS (a rule referencing it validates).
"""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["ADS_MARKET"] = "US"

import db                        # noqa: E402
from rules import entities       # noqa: E402
from rules import runner         # noqa: E402


def temp_conn():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    real = db.DB_PATH
    db.DB_PATH = path
    conn = db.connect()
    db.DB_PATH = real
    return conn, path


class Banking(unittest.TestCase):
    def test_store_targeting_perf_maps_kenp(self):
        """The two KENP report keys land in the kenp columns."""
        conn, path = temp_conn()
        try:
            rows = [{
                "campaignId": "c1", "adGroupId": "g1", "targeting": "kenp kw",
                "matchType": "EXACT", "keywordId": "t9",
                "impressions": 300, "clicks": 15, "cost": 5.0,
                "purchases30d": 0, "sales30d": 0.0,
                "kindleEditionNormalizedPagesRead14d": 812.0,
                "kindleEditionNormalizedPagesRoyalties14d": 3.44,
            }]
            db.store_targeting_perf(conn, rows, "2026-08-10")
            r = conn.execute("SELECT kenp_read, kenp_royalties FROM targeting_perf "
                             "WHERE target_id='t9'").fetchone()
            self.assertAlmostEqual(r[0], 812.0)
            self.assertAlmostEqual(r[1], 3.44)
        finally:
            conn.close()
            os.unlink(path)

    def test_merch_row_without_kenp_stores_zero(self):
        """A Merch pull never requests KENP, so db._f returns 0 — not NULL, not an
        error. This is what keeps the shared store functions safe for all six
        apparel markets."""
        conn, path = temp_conn()
        try:
            merch = [{
                "campaignId": "c1", "adGroupId": "g1", "targeting": "tee kw",
                "matchType": "EXACT", "keywordId": "t10",
                "impressions": 10, "clicks": 1, "cost": 0.5,
                "purchases30d": 0, "sales30d": 0.0,
            }]
            db.store_targeting_perf(conn, merch, "2026-08-10")
            m = conn.execute("SELECT kenp_read, kenp_royalties FROM targeting_perf "
                             "WHERE target_id='t10'").fetchone()
            self.assertEqual(m[0], 0)
            self.assertEqual(m[1], 0)
        finally:
            conn.close()
            os.unlink(path)


class Migration(unittest.TestCase):
    def test_migrate_adds_kenp_idempotently(self):
        """A pre-KENP DB gains the columns; a second migrate is a clean no-op."""
        conn, path = temp_conn()
        try:
            # Simulate an old DB: a perf table without the KENP columns.
            conn.execute("DROP TABLE targeting_perf")
            conn.execute("""CREATE TABLE targeting_perf (
                date TEXT, campaign_id TEXT, ad_group_id TEXT,
                targeting TEXT, match_type TEXT, target_id TEXT,
                impressions INTEGER, clicks INTEGER, cost REAL,
                orders INTEGER, sales REAL, acos REAL,
                PRIMARY KEY (date, campaign_id, ad_group_id, targeting, match_type))""")
            conn.commit()
            before = [r[1] for r in conn.execute("PRAGMA table_info(targeting_perf)")]
            self.assertNotIn("kenp_read", before)

            db._migrate(conn)   # adds them
            db._migrate(conn)   # idempotent: guard skips, no duplicate-column error

            after = [r[1] for r in conn.execute("PRAGMA table_info(targeting_perf)")]
            self.assertIn("kenp_read", after)
            self.assertIn("kenp_royalties", after)
            self.assertEqual(after.count("kenp_read"), 1)
            self.assertEqual(after.count("kenp_royalties"), 1)
        finally:
            conn.close()
            os.unlink(path)


class DSLField(unittest.TestCase):
    def _seed(self, conn):
        conn.executescript("""
            INSERT INTO campaigns(campaign_id,name,state) VALUES ('c1','Book 1','ENABLED');
            INSERT INTO ad_groups(ad_group_id,campaign_id,name,state,default_bid)
              VALUES ('g1','c1','G1','ENABLED',0.4);
            INSERT INTO ad_group_product(ad_group_id,asin,product_type,lifetime_sales)
              VALUES ('g1','B0BOOK','standard_tee',5);
        """)
        # t_read: 0 ad-orders but real KENP page-reads; t_dead: no kenp column set.
        conn.execute("""INSERT INTO targeting_perf(date,campaign_id,ad_group_id,targeting,match_type,
            target_id,impressions,clicks,cost,orders,sales,acos,kenp_read,kenp_royalties)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("2026-08-10", "c1", "g1", "kenp reader", "EXACT", "t_read",
             300, 15, 5.0, 0, 0.0, None, 812.0, 3.44))
        conn.execute("""INSERT INTO targeting_perf(date,campaign_id,ad_group_id,targeting,match_type,
            target_id,impressions,clicks,cost,orders,sales,acos) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("2026-08-10", "c1", "g1", "dead term", "EXACT", "t_dead",
             200, 20, 6.0, 0, 0.0, None))
        conn.commit()

    def test_target_loads_kenp(self):
        conn, path = temp_conn()
        try:
            self._seed(conn)
            rows = {r.field("keyword_text"): r for r in entities.load(conn, "target")}
            self.assertAlmostEqual(rows["kenp reader"].field("kenp"), 812.0)
            self.assertAlmostEqual(rows["kenp reader"].field("kenp_royalties"), 3.44)
            # A row that never set KENP (nullable column) reads 0, never raises.
            self.assertEqual(rows["dead term"].field("kenp"), 0)
            self.assertEqual(rows["dead term"].field("kenp_royalties"), 0)
        finally:
            conn.close()
            os.unlink(path)


class Validation(unittest.TestCase):
    def test_kenp_is_a_known_field(self):
        self.assertIn("kenp", runner.KNOWN_FIELDS)
        self.assertIn("kenp_royalties", runner.KNOWN_FIELDS)

    def test_rule_referencing_kenp_validates(self):
        v = runner.validate(
            'FOR EACH keyword:\n'
            '  IF keyword.clicks >= 20 AND keyword.orders = 0 AND keyword.kenp = 0:\n'
            '    keyword.pause()\n')
        self.assertTrue(v["ok"], v.get("errors"))


if __name__ == "__main__":
    unittest.main()
