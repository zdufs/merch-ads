#!/usr/bin/env python3
"""Rules DSL read-only runner + validate (Spec B Layer 1, Task 6)."""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ["ADS_MARKET"] = "US"

import db          # noqa: E402
import products    # noqa: E402
from rules import runner  # noqa: E402


def temp_conn():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    real = db.DB_PATH
    db.DB_PATH = path
    conn = db.connect()
    db.DB_PATH = real
    return conn, path


def seed(conn):
    conn.execute("INSERT INTO campaigns(campaign_id,name,state) VALUES ('c1','C1','ENABLED')")
    conn.execute("INSERT INTO ad_groups(ad_group_id,campaign_id,name,state,default_bid) VALUES ('g1','c1','G1','ENABLED',0.4)")
    conn.execute("INSERT INTO ad_group_product(ad_group_id,asin,product_type,list_price) VALUES ('g1','B0AAA',?, '21.99')",
                 (products.TEE,))
    # t1: dead (20 clicks, 0 sales); t2: converting
    conn.execute("""INSERT INTO targeting_perf(date,campaign_id,ad_group_id,targeting,match_type,
        target_id,impressions,clicks,cost,orders,sales,acos) VALUES
        ('2026-07-31','c1','g1','dead term','EXACT','t1',200,20,6.0,0,0.0,NULL),
        ('2026-07-31','c1','g1','good term','EXACT','t2',100,10,4.0,2,40.0,0.10)""")
    conn.commit()


class Runner(unittest.TestCase):
    def test_preview_pause_dead_terms(self):
        conn, path = temp_conn()
        try:
            seed(conn)
            src = ('FOR EACH target:\n'
                   '  IF target.clicks >= 15 AND target.orders = 0:\n'
                   '    target.pause()\n'
                   '    target.note("{clicks} clicks 0 sales")\n')
            res = runner.preview(conn, src)
            self.assertTrue(res["ok"])
            self.assertEqual(res["evaluated"], 2)
            self.assertEqual(res["matched"], 1)
            ch = res["changes"][0]
            self.assertEqual(ch["action"], "pause")
            self.assertEqual(ch["label"], "dead term")
            self.assertIn("20 clicks", ch["note"])
            self.assertTrue(any(c["pass"] for c in ch["trace"]))
        finally:
            conn.close()
            os.unlink(path)

    def test_economics_rule_skips_when_unavailable(self):
        conn, path = temp_conn()
        try:
            seed(conn)
            # profit rule; g1 tee is mapped @21.99 so econ IS available here.
            # good term: profit = 6.88*2 - 4 = 9.76 (>0, no pause); dead: profit
            # = 0 - 6 = -6 (<0, pause). Both econ-available.
            src = ('FOR EACH target:\n'
                   '  IF target.profit < 0:\n'
                   '    target.pause()\n')
            res = runner.preview(conn, src)
            self.assertTrue(res["ok"])
            self.assertEqual(res["matched"], 1)
            self.assertEqual(res["changes"][0]["label"], "dead term")
        finally:
            conn.close()
            os.unlink(path)

    def test_econ_in_let_marks_econ_driven(self):
        """LET floor = break_even*0.9 … setBid(floor) is an economics-driven
        bid write — the econ gate must see it even though the IF condition
        itself never mentions an economics field."""
        conn, path = temp_conn()
        try:
            seed(conn)
            src = ('FOR EACH target:\n'
                   '  LET floor = break_even * 0.9\n'
                   '  IF target.clicks >= 15:\n'
                   '    target.setBid(floor)\n')
            res = runner.preview(conn, src)
            self.assertTrue(res["ok"])
            self.assertEqual(res["matched"], 1)
            self.assertTrue(res["changes"][0]["econ_driven"])
        finally:
            conn.close()
            os.unlink(path)

    def test_econ_in_action_args_marks_econ_driven(self):
        conn, path = temp_conn()
        try:
            seed(conn)
            src = ('FOR EACH target:\n'
                   '  IF target.clicks >= 15:\n'
                   '    target.setBid(break_even)\n')
            res = runner.preview(conn, src)
            self.assertTrue(res["ok"])
            self.assertEqual(res["matched"], 1)
            self.assertTrue(res["changes"][0]["econ_driven"])
        finally:
            conn.close()
            os.unlink(path)

    def test_plain_metric_rule_stays_non_econ(self):
        conn, path = temp_conn()
        try:
            seed(conn)
            src = ('FOR EACH target:\n'
                   '  LET step = bid * 0.85\n'
                   '  IF target.clicks >= 15:\n'
                   '    target.setBid(step)\n')
            res = runner.preview(conn, src)
            self.assertTrue(res["ok"])
            self.assertEqual(res["matched"], 1)
            self.assertFalse(res["changes"][0]["econ_driven"])
        finally:
            conn.close()
            os.unlink(path)

    def test_null_bid_row_skips_fail_closed_instead_of_crashing(self):
        """The shipped template MAX($0.05, bid*0.85) meets a NULL default_bid:
        the row must be skipped (fail closed, like NONE conditions), the other
        rows still evaluated — not a TypeError aborting the whole preview."""
        conn, path = temp_conn()
        try:
            seed(conn)
            conn.execute("INSERT INTO ad_groups(ad_group_id,campaign_id,name,state,default_bid)"
                         " VALUES ('g2','c1','G2','ENABLED',NULL)")
            conn.execute("""INSERT INTO targeting_perf(date,campaign_id,ad_group_id,targeting,
                match_type,target_id,impressions,clicks,cost,orders,sales,acos) VALUES
                ('2026-07-31','c1','g2','null bid term','EXACT','t3',200,20,6.0,0,0.0,NULL)""")
            conn.commit()
            src = ('FOR EACH target:\n'
                   '  IF target.clicks >= 15 AND target.orders = 0:\n'
                   '    target.setBid(MAX($0.05, target.bid * 0.85))\n')
            res = runner.preview(conn, src)
            self.assertTrue(res["ok"])
            self.assertEqual(res["evaluated"], 3)
            self.assertEqual(res["matched"], 1)
            self.assertEqual(res["changes"][0]["label"], "dead term")
            self.assertAlmostEqual(res["changes"][0]["args"][0], 0.34)
        finally:
            conn.close()
            os.unlink(path)

    def test_row_error_is_recorded_not_fatal(self):
        conn, path = temp_conn()
        try:
            seed(conn)
            src = ('FOR EACH target:\n'
                   '  IF LENGTH(target.bid) > 1:\n'      # TypeError on every row
                   '    target.pause()\n')
            res = runner.preview(conn, src)
            self.assertTrue(res["ok"])          # the preview survives
            self.assertEqual(res["evaluated"], 2)
            self.assertEqual(res["changes"], [])
            self.assertTrue(res["errors"])      # ...and says what went wrong
        finally:
            conn.close()
            os.unlink(path)

    def test_lifetime_window_nulls_snapshot_metrics(self):
        """IN LIFETIME has no per-entity spend/clicks/acos (the spec is
        explicit: lifetime_sales units only) — snapshot metrics must resolve
        to NONE so conditions on them skip every row, instead of silently
        evaluating the trailing-30 snapshot as if it were lifetime data."""
        conn, path = temp_conn()
        try:
            seed(conn)
            src = ('FOR EACH target IN LIFETIME:\n'
                   '  IF target.clicks >= 1:\n'
                   '    target.pause()\n')
            res = runner.preview(conn, src)
            self.assertTrue(res["ok"])
            self.assertEqual(res["evaluated"], 2)
            self.assertEqual(res["matched"], 0)    # clicks is NONE in LIFETIME
        finally:
            conn.close()
            os.unlink(path)

    def test_lifetime_window_keeps_lifetime_sales(self):
        conn, path = temp_conn()
        try:
            seed(conn)
            conn.execute("UPDATE ad_group_product SET lifetime_sales=50 WHERE ad_group_id='g1'")
            conn.commit()
            src = ('FOR EACH target IN LIFETIME:\n'
                   '  IF lifetime_sales > 10:\n'
                   '    target.pause()\n')
            res = runner.preview(conn, src)
            self.assertTrue(res["ok"])
            self.assertEqual(res["matched"], 2)    # both g1 targets qualify
        finally:
            conn.close()
            os.unlink(path)

    def test_validate_reports_syntax_error(self):
        res = runner.validate("FOR EACH target:\n  IF target.acos >:\n    target.pause()\n")
        self.assertFalse(res["ok"])
        self.assertEqual(res["errors"][0]["line"], 2)

    def test_validate_accepts_rolling_window_on_target(self):
        # target_daily (Task 1-4) gave targets a true per-day source, so this
        # used to be rejected and now is not (Task 6). Entities with no
        # per-day table, like searchTerm, still are — see
        # tests/rules_rolling_tests.py for the full matrix.
        res = runner.validate("FOR EACH target IN LAST 7 DAYS:\n  target.pause()\n")
        self.assertTrue(res["ok"])

    def test_validate_rejects_unknown_verb(self):
        """keyword.paws() must die at validate time, not as a nightly
        'unsupported' surprise weeks later."""
        res = runner.validate("FOR EACH target:\n  target.paws()\n")
        self.assertFalse(res["ok"])
        self.assertIn("paws", res["errors"][0]["message"])
        self.assertIn("pause", res["errors"][0]["message"])   # lists what IS allowed

    def test_validate_flags_not_yet_executable_verb(self):
        res = runner.validate('FOR EACH target:\n  target.setState("PAUSED")\n')
        self.assertFalse(res["ok"])
        self.assertIn("not executable", res["errors"][0]["message"])

    def test_validate_rejects_unknown_field(self):
        res = runner.validate("FOR EACH target:\n"
                              "  IF target.clickz > 5:\n"
                              "    target.pause()\n")
        self.assertFalse(res["ok"])
        self.assertIn("clickz", res["errors"][0]["message"])
        self.assertEqual(res["errors"][0]["line"], 2)

    def test_validate_accepts_alias_lets_and_known_fields(self):
        res = runner.validate("FOR EACH target AS t:\n"
                              "  LET floor = t.bid * 0.9\n"
                              "  IF t.clicks > 5 AND floor > $0.10:\n"
                              "    t.setBid(floor)\n")
        self.assertTrue(res["ok"], res["errors"])


if __name__ == "__main__":
    unittest.main()
