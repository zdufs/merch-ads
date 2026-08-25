#!/usr/bin/env python3
"""Inline baseline / trend windows: `<metric> IN <window>` (the MerchDash
trend-comparison parity). A rule can compare a recent window to an older
baseline for the same entity in one pass — something CURRENT/LIFETIME/IN LAST n
DAYS alone could not express.

Run from the Ads folder:  python3 -m unittest tests.rules_baseline_tests -v
"""

import datetime
import os
import sqlite3
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ["ADS_MARKET"] = "US"

import db  # noqa: E402
from rules import ast_nodes as A  # noqa: E402
from rules import runner  # noqa: E402
from rules.parser import parse  # noqa: E402

TODAY = datetime.date(2026, 8, 9)


class Parsing(unittest.TestCase):
    def _let_expr(self, src):
        prog = parse(src)
        return prog.rules[0].body[0].expr

    def test_from_to_parses_to_a_range_window(self):
        expr = self._let_expr(
            "FOR EACH keyword:\n"
            "  LET b = keyword.acos IN FROM 8 DAYS AGO TO 60 DAYS AGO\n"
            "  keyword.note(\"x\")\n")
        self.assertIsInstance(expr, A.Windowed)
        self.assertEqual(expr.window, ("range", 8, 60))

    def test_n_days_ago_and_yesterday_and_last(self):
        cases = {
            "3 DAYS AGO": ("day", 3),
            "YESTERDAY": ("yesterday",),
            "LAST 14 DAYS": ("rolling", 14),
        }
        for text, spec in cases.items():
            expr = self._let_expr(
                f"FOR EACH target:\n  LET b = target.acos IN {text}\n"
                f"  target.note(\"x\")\n")
            self.assertEqual(expr.window, spec, text)

    def test_membership_in_a_list_is_not_a_window(self):
        prog = parse('FOR EACH keyword:\n'
                     '  IF keyword.match_type IN ["EXACT","PHRASE"]:\n'
                     '    keyword.note("x")\n')
        cond = prog.rules[0].body[0].cond
        self.assertEqual(cond.__class__.__name__, "Compare")
        self.assertEqual(cond.op, "IN")


def mk_conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(db.SCHEMA)
    conn.execute("INSERT INTO campaigns (campaign_id, name, state, daily_budget) "
                 "VALUES ('c1','C','ENABLED',20)")
    conn.execute("INSERT INTO ad_groups (ad_group_id, campaign_id, name, state, "
                 "default_bid) VALUES ('ag1','c1','AG','ENABLED',0.5)")
    conn.execute("INSERT INTO ad_group_product (ad_group_id, asin, product_type) "
                 "VALUES ('ag1','B0TEST','standard_tee')")
    try:
        conn.execute("INSERT INTO targets (target_id, bid, state) VALUES ('t1',0.18,'ENABLED')")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return conn


def _bank(conn, target_id, targeting, day_from, day_to, clicks, cost, sales):
    """Insert one target_daily row per day in [TODAY-day_from .. TODAY-day_to]."""
    lo, hi = min(day_from, day_to), max(day_from, day_to)
    for n in range(lo, hi + 1):
        d = (TODAY - datetime.timedelta(days=n)).isoformat()
        conn.execute(
            "INSERT INTO target_daily (date, campaign_id, ad_group_id, targeting, "
            "match_type, target_id, impressions, clicks, cost, orders, sales, acos) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (d, "c1", "ag1", targeting, "EXACT", target_id,
             clicks * 10, clicks, cost, 1, sales, (cost / sales if sales else None)))
    conn.commit()


TREND = ("FOR EACH keyword IN LAST 7 DAYS:\n"
         "  LET baseline = keyword.acos IN FROM 9 DAYS AGO TO 60 DAYS AGO\n"
         "  IF keyword.clicks >= 10 AND keyword.acos > baseline * 1.5:\n"
         "    keyword.pause()\n"
         "    keyword.note(\"recent {acos:percent} vs baseline\")\n")


class EndToEnd(unittest.TestCase):
    def test_fires_when_recent_acos_exceeds_baseline(self):
        conn = mk_conn()
        # recent (LAST 7 DAYS ends TODAY-2): high ACOS 0.20, 21 clicks
        _bank(conn, "t1", "widget", 2, 8, clicks=3, cost=3, sales=15)
        # baseline (9..60 days ago): low ACOS 0.05
        _bank(conn, "t1", "widget", 9, 60, clicks=1, cost=1, sales=20)
        res = runner.preview(conn, TREND, today=TODAY)
        self.assertTrue(res["ok"], res.get("errors"))
        self.assertEqual(res["matched"], 1)
        self.assertEqual(res["changes"][0]["action"], "pause")

    def test_quiet_when_recent_matches_baseline(self):
        conn = mk_conn()
        _bank(conn, "t1", "widget", 2, 8, clicks=3, cost=1, sales=20)   # recent ACOS 0.05
        _bank(conn, "t1", "widget", 9, 60, clicks=1, cost=1, sales=20)  # baseline ACOS 0.05
        res = runner.preview(conn, TREND, today=TODAY)
        self.assertTrue(res["ok"], res.get("errors"))
        self.assertEqual(res["matched"], 0)

    def test_fails_closed_with_no_baseline_data(self):
        conn = mk_conn()
        _bank(conn, "t1", "widget", 2, 8, clicks=3, cost=3, sales=15)   # recent only
        res = runner.preview(conn, TREND, today=TODAY)
        self.assertTrue(res["ok"], res.get("errors"))
        # baseline metric is NONE -> comparison never matches -> no write
        self.assertEqual(res["matched"], 0)


if __name__ == "__main__":
    unittest.main()
