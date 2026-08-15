#!/usr/bin/env python3
"""Per-target bid mirror: the `targets` table banked from /sp/targets/list +
/sp/keywords/list during the nightly pull.

Before this table existed, `bid` on a DSL target row was silently the ad-group
default_bid — `setBid(bid * 0.85)` computed from the wrong base and could
RAISE an under-bid keyword. The mirror gives every entity its own bid and
state, with an honest fallback (bid_inherited) when a clause has no bid.

Run from the Ads folder:  python3 -m unittest tests.target_mirror_tests -v"""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["ADS_MARKET"] = "US"

import db        # noqa: E402
import appctl    # noqa: E402
from rules import entities  # noqa: E402


def temp_conn():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    real = db.DB_PATH
    db.DB_PATH = path
    conn = db.connect()
    db.DB_PATH = real
    return conn, path


def seed_perf(conn):
    conn.execute("INSERT INTO campaigns(campaign_id,name,state) VALUES ('c1','C1','ENABLED')")
    conn.execute("INSERT INTO ad_groups(ad_group_id,campaign_id,name,state,default_bid)"
                 " VALUES ('g1','c1','G1','ENABLED',0.4)")
    conn.execute("""INSERT INTO targeting_perf(date,campaign_id,ad_group_id,targeting,match_type,
        target_id,impressions,clicks,cost,orders,sales) VALUES
        ('2026-07-31','c1','g1','own bid kw','EXACT','t1',100,10,4.0,1,20.0),
        ('2026-07-31','c1','g1','inherited kw','EXACT','t2',100,10,4.0,1,20.0)""")
    conn.commit()


class StoreTargets(unittest.TestCase):
    def test_store_replaces_the_previous_mirror(self):
        conn, path = temp_conn()
        try:
            db.store_targets(conn, [
                ("t1", "c1", "g1", "keyword", "old kw", "EXACT", "ENABLED", 0.30, "2026-08-04"),
                ("t9", "c1", "g1", "target", "close-match", None, "ENABLED", None, "2026-08-04"),
            ])
            db.store_targets(conn, [
                ("t1", "c1", "g1", "keyword", "old kw", "EXACT", "PAUSED", 0.25, "2026-08-05"),
            ])
            rows = conn.execute("SELECT target_id, state, bid FROM targets").fetchall()
            self.assertEqual(rows, [("t1", "PAUSED", 0.25)])   # t9 is gone, not stale
        finally:
            conn.close(); os.unlink(path)


class MirrorRows(unittest.TestCase):
    def test_clauses_and_keywords_become_rows(self):
        clauses = [{"targetId": 11, "campaignId": 1, "adGroupId": 2, "state": "ENABLED",
                    "expression": [{"type": "QUERY_HIGH_REL_MATCHES"}]},
                   {"targetId": 12, "campaignId": 1, "adGroupId": 2, "state": "ENABLED",
                    "bid": 0.55,
                    "expression": [{"type": "ASIN_SAME_AS", "value": "B0AAA"}]}]
        keywords = [{"keywordId": 21, "campaignId": 1, "adGroupId": 3, "state": "PAUSED",
                     "bid": 0.30, "keywordText": "funny tee", "matchType": "EXACT"}]
        rows = db.target_mirror_rows(clauses, keywords, now="2026-08-05T10:00:00")
        by_id = {r[0]: r for r in rows}
        self.assertEqual(set(by_id), {"11", "12", "21"})
        # auto clause with no own bid stays NULL = inherits the ad-group default
        self.assertEqual(by_id["11"][3:8], ("target", "QUERY_HIGH_REL_MATCHES", None, "ENABLED", None))
        self.assertEqual(by_id["12"][4], "ASIN_SAME_AS=B0AAA")
        self.assertEqual(by_id["12"][7], 0.55)
        self.assertEqual(by_id["21"][3:8], ("keyword", "funny tee", "EXACT", "PAUSED", 0.30))


class DslReadsMirror(unittest.TestCase):
    def test_target_bid_and_state_come_from_the_mirror(self):
        conn, path = temp_conn()
        try:
            seed_perf(conn)
            db.store_targets(conn, [
                ("t1", "c1", "g1", "keyword", "own bid kw", "EXACT", "PAUSED", 0.23, "2026-08-05"),
            ])
            rows = {r.id: r for r in entities.load(conn, "target")}
            own = rows["t1"].fields
            self.assertEqual(own["bid"], 0.23)          # NOT the 0.4 default
            self.assertFalse(own["bid_inherited"])
            self.assertEqual(own["state"], "PAUSED")     # target state, not ad group's
            self.assertEqual(own["default_bid"], 0.4)
        finally:
            conn.close(); os.unlink(path)

    def test_unmirrored_target_falls_back_to_ad_group_default(self):
        conn, path = temp_conn()
        try:
            seed_perf(conn)   # no mirror rows at all (pre-first-pull DB)
            rows = {r.id: r for r in entities.load(conn, "target")}
            inh = rows["t2"].fields
            self.assertEqual(inh["bid"], 0.4)
            self.assertTrue(inh["bid_inherited"])
            self.assertEqual(inh["state"], "ENABLED")    # ad group fallback
        finally:
            conn.close(); os.unlink(path)


class BidReport(unittest.TestCase):
    def test_changes_enriched_from_the_mirror_without_full_history_scan(self):
        conn, path = temp_conn()
        try:
            seed_perf(conn)
            db.store_targets(conn, [
                ("t1", "c1", "g1", "keyword", "own bid kw", "EXACT", "ENABLED", 0.23, "2026-08-05"),
            ])
            db.log_write(conn, "bid_change", "target", "t1",
                         "snap=2026-08-04 0.25->0.23 (tee: ACOS 40% > 30%)", "0.25", "submitted")
            db.log_write(conn, "bid_change", "target", "t-unknown",
                         "snap=2026-08-04 0.30->0.26 (manual)", "0.30", "submitted")
            data = appctl._bidreport_data(conn.cursor(), days=7)
            by_id = {c["target_id"]: c for c in data["changes"]}
            self.assertEqual(data["count"], 2)
            self.assertEqual(data["downs"], 2)
            self.assertEqual(by_id["t1"]["targeting"], "own bid kw")   # from the mirror
            self.assertEqual(by_id["t1"]["ad_group_id"], "g1")
            self.assertIsNone(by_id["t-unknown"]["targeting"])         # unknown id: no crash
            self.assertEqual(by_id["t1"]["old"], 0.25)
            self.assertEqual(by_id["t1"]["new"], 0.23)
        finally:
            conn.close(); os.unlink(path)


class AllTargetsBid(unittest.TestCase):
    def test_alltargets_rows_carry_the_bid(self):
        conn, path = temp_conn()
        try:
            seed_perf(conn)
            db.store_targets(conn, [
                ("t1", "c1", "g1", "keyword", "own bid kw", "EXACT", "ENABLED", 0.23, "2026-08-05"),
            ])
            rows = appctl._alltargets_rows(conn.cursor(), limit=10)
            by_id = {r["target_id"]: r for r in rows}
            self.assertEqual(by_id["t1"]["bid"], 0.23)
            self.assertEqual(by_id["t2"]["bid"], 0.4)    # fallback to default
            self.assertTrue(by_id["t2"]["bid_inherited"])
        finally:
            conn.close(); os.unlink(path)


if __name__ == "__main__":
    unittest.main()
