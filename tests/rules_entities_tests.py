#!/usr/bin/env python3
"""Rules DSL entity loaders + metric fields (Spec B Layer 1, Task 4)."""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["ADS_MARKET"] = "US"

import db          # noqa: E402
from rules import entities  # noqa: E402


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
        INSERT INTO campaigns(campaign_id,name,state,daily_budget) VALUES ('c1','Lotto 1','ENABLED',10.0);
        INSERT INTO ad_groups(ad_group_id,campaign_id,name,state,default_bid) VALUES ('g1','c1','G1','ENABLED',0.4);
        INSERT INTO ad_group_product(ad_group_id,asin,product_type,lifetime_sales) VALUES ('g1','B0AAA','standard_tee',12);
    """)
    conn.execute("""INSERT INTO campaign_perf(date,campaign_id,impressions,clicks,cost,orders,sales,acos)
        VALUES (?,?,?,?,?,?,?,?)""", (latest, "c1", 100, 10, 5.0, 1, 20.0, 0.25))
    # one converting target, one zero-sale target
    conn.execute("""INSERT INTO targeting_perf(date,campaign_id,ad_group_id,targeting,match_type,
        target_id,impressions,clicks,cost,orders,sales,acos) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (latest, "c1", "g1", "funny tee", "EXACT", "t1", 100, 10, 4.0, 1, 20.0, 0.20))
    conn.execute("""INSERT INTO targeting_perf(date,campaign_id,ad_group_id,targeting,match_type,
        target_id,impressions,clicks,cost,orders,sales,acos) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (latest, "c1", "g1", "dead term", "EXACT", "t2", 200, 20, 6.0, 0, 0.0, None))
    conn.commit()


class Entities(unittest.TestCase):
    def test_targets_metric_fields(self):
        conn, path = temp_conn()
        try:
            seed(conn)
            rows = {r.field("keyword_text"): r for r in entities.load(conn, "target")}
            hot = rows["funny tee"]
            self.assertEqual(hot.field("clicks"), 10)
            self.assertAlmostEqual(hot.field("acos"), 0.20)
            self.assertEqual(hot.field("orders"), 1)
            self.assertEqual(hot.field("match_type"), "EXACT")
        finally:
            conn.close()
            os.unlink(path)

    def test_zero_sale_acos_is_none(self):
        conn, path = temp_conn()
        try:
            seed(conn)
            rows = {r.field("keyword_text"): r for r in entities.load(conn, "target")}
            self.assertIsNone(rows["dead term"].field("acos"))
        finally:
            conn.close()
            os.unlink(path)

    def test_campaign_fields(self):
        conn, path = temp_conn()
        try:
            seed(conn)
            c = entities.load(conn, "campaign")[0]
            self.assertEqual(c.field("name"), "Lotto 1")
            self.assertEqual(c.field("clicks"), 10)
            self.assertAlmostEqual(c.field("budget"), 10.0)
        finally:
            conn.close()
            os.unlink(path)

    def test_never_changed_target_has_waited_forever(self):
        """A cooldown must PASS on a target the engine has never touched.

        `days_since_bid_change` used to be None there, and every shipped
        cooldown reads `> 7`, which is false against NONE. That silently
        restricted the cooldown rules to the handful of targets already in
        writes_log — 55 of 43,370 in US — so they read as broken.
        """
        conn, path = temp_conn()
        try:
            seed(conn)
            rows = {r.field("keyword_text"): r for r in entities.load(conn, "target")}
            self.assertEqual(rows["funny tee"].field("days_since_bid_change"),
                             entities.NEVER_CHANGED_DAYS)
            self.assertGreater(rows["dead term"].field("days_since_bid_change"), 7)
        finally:
            conn.close()
            os.unlink(path)

    def test_recent_bid_change_blocks_the_cooldown(self):
        """The other half: a bid we moved today must read as 0 days, not 99999."""
        conn, path = temp_conn()
        try:
            seed(conn)
            db.log_write(conn, "bid_change", "target", "t1", "snap=2026-07-31",
                         "0.40", "ok")
            rows = {r.field("keyword_text"): r for r in entities.load(conn, "target")}
            self.assertEqual(rows["funny tee"].field("days_since_bid_change"), 0)
            self.assertEqual(rows["dead term"].field("days_since_bid_change"),
                             entities.NEVER_CHANGED_DAYS)
        finally:
            conn.close()
            os.unlink(path)

    def test_campaign_budget_cooldown_uses_the_same_rule(self):
        conn, path = temp_conn()
        try:
            seed(conn)
            c = entities.load(conn, "campaign")[0]
            self.assertEqual(c.field("days_since_budget_change"),
                             entities.NEVER_CHANGED_DAYS)
            db.log_write(conn, "budget_change", "campaign", "c1", "10->12", "10.0", "ok")
            c = entities.load(conn, "campaign")[0]
            self.assertEqual(c.field("days_since_budget_change"), 0)
        finally:
            conn.close()
            os.unlink(path)

    def test_unknown_field_raises(self):
        conn, path = temp_conn()
        try:
            seed(conn)
            r = entities.load(conn, "target")[0]
            with self.assertRaises(entities.FieldError):
                r.field("nonsense")
        finally:
            conn.close()
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
