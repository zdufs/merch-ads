#!/usr/bin/env python3
"""Rules DSL economics field resolver (Spec B Layer 1, Task 5). Reuses the phase
economics (products/db) — verified fail-closed on transition/cohort/unmapped."""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ["ADS_MARKET"] = "US"

import db          # noqa: E402
import products    # noqa: E402
from rules import entities, econ_fields  # noqa: E402


def temp_conn():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    real = db.DB_PATH
    db.DB_PATH = path
    conn = db.connect()
    db.DB_PATH = real
    return conn, path


def seed_target(conn, agid, asin, list_price, orders=1, sales=20.0, cost=5.0):
    conn.execute("INSERT OR IGNORE INTO campaigns(campaign_id,name,state) VALUES ('c1','C1','ENABLED')")
    conn.execute("INSERT INTO ad_groups(ad_group_id,campaign_id,name,state,default_bid) VALUES (?,?,?,?,?)",
                 (agid, "c1", "G-" + agid, "ENABLED", 0.4))
    conn.execute("INSERT INTO ad_group_product(ad_group_id,asin,product_type,list_price) VALUES (?,?,?,?)",
                 (agid, asin, products.TEE, list_price))
    conn.execute("""INSERT INTO targeting_perf(date,campaign_id,ad_group_id,targeting,match_type,
        target_id,impressions,clicks,cost,orders,sales,acos) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("2026-07-31", "c1", agid, "term-" + agid, "EXACT", "t-" + agid,
         100, 10, cost, orders, sales, None))
    conn.commit()


class Econ(unittest.TestCase):
    def _resolved(self, conn, agid):
        ctx = econ_fields.Context(conn)
        row = next(r for r in entities.load(conn, "target") if r.field("ad_group_id") == agid)
        econ_fields.resolve(ctx, row)
        return row

    def test_mapped_tee_resolves_break_even_and_profit(self):
        conn, path = temp_conn()
        try:
            seed_target(conn, "g1", "B0AAA", "21.99", orders=2, sales=40.0, cost=5.0)
            row = self._resolved(conn, "g1")
            self.assertTrue(row.field("econ_available"))
            self.assertAlmostEqual(row.field("break_even"), 0.3129)
            self.assertAlmostEqual(row.field("royalty"), 6.88)
            # profit = royalty*orders - spend = 6.88*2 - 5 = 8.76
            self.assertAlmostEqual(row.field("profit"), 8.76, places=2)
            self.assertFalse(row.field("in_transition"))
            self.assertFalse(row.field("is_cohort"))
        finally:
            conn.close()
            os.unlink(path)

    def test_transition_fails_closed(self):
        conn, path = temp_conn()
        try:
            seed_target(conn, "g2", "B0BBB", "21.99")
            # open a 30-day price-transition window for this ASIN
            db.log_price_changes(conn, [("B0BBB", "g2", None, 2299)])
            row = self._resolved(conn, "g2")
            self.assertTrue(row.field("in_transition"))
            self.assertFalse(row.field("econ_available"))
            self.assertIsNone(row.field("break_even"))
            self.assertIsNone(row.field("profit"))
        finally:
            conn.close()
            os.unlink(path)

    def test_cohort_null_asin_fails_closed(self):
        conn, path = temp_conn()
        try:
            conn.execute("INSERT INTO campaigns(campaign_id,name,state) VALUES ('c1','C1','ENABLED')")
            conn.execute("INSERT INTO ad_groups(ad_group_id,campaign_id,name,state) VALUES ('g3','c1','G3','ENABLED')")
            conn.execute("INSERT INTO ad_group_product(ad_group_id,asin,product_type) VALUES ('g3',NULL,?)",
                         (products.TEE,))
            conn.execute("""INSERT INTO targeting_perf(date,campaign_id,ad_group_id,targeting,match_type,
                target_id,impressions,clicks,cost,orders,sales,acos) VALUES
                ('2026-07-31','c1','g3','coh','EXACT','tc',100,10,5.0,1,20.0,NULL)""")
            conn.commit()
            row = self._resolved(conn, "g3")
            self.assertTrue(row.field("is_cohort"))
            self.assertFalse(row.field("econ_available"))
            self.assertIsNone(row.field("break_even"))
        finally:
            conn.close()
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
