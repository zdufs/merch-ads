#!/usr/bin/env python3
"""`keyword` and `target` are two entity kinds, and they must load two sets.

`_load_targets(conn, kind, ...)` took `kind` and used it only as a LABEL on the
row it built. It never reached the query. So `FOR EACH keyword` and
`FOR EACH target` returned the identical set — measured on the live US database
on 2026-08-23: 51,631 rows each, of which 49,901 were auto targeting
expressions and 1,711 were keywords.

That is worse than it sounds in one direction. A rule an operator writes for
KEYWORDS reaches every auto clause in the account, and the executor happily
routes each one to the right Amazon endpoint, so nothing fails and nothing
warns. The rule simply does about thirty times more than it says.

It also defeated the cross-rule conflict guard, which keys on
(entity_kind, entity_id): the same underlying clause proposed by a keyword rule
and a target rule looked like two different entities, so both wrote and
whichever ran last silently won.

The split is the one Amazon makes and that the executor already routes on:
a keyword has match type BROAD, EXACT or PHRASE; everything else is a
product or automatic target.

Run from the Ads folder:
    python3 -m unittest tests.rules_entity_kind_tests -v
"""

import os
import sqlite3
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
sys.path.insert(0, HERE)
os.environ.setdefault("ADS_MARKET", "US")

import db                                    # noqa: E402
from rules import entities                   # noqa: E402

LATEST = "2026-08-22"

ROWS = [
    # (targeting, match_type, target_id)   -- three keywords, three targets
    ("funny tee",            "EXACT",                            "k1"),
    ("dog shirt",            "PHRASE",                           "k2"),
    ("vintage sunset",       "BROAD",                            "k3"),
    ("asin=\"B0EXAMPLE1\"",  "TARGETING_EXPRESSION",             "t1"),
    ("loose-match",          "TARGETING_EXPRESSION_PREDEFINED",  "t2"),
    ("substitutes",          "TARGETING_EXPRESSION_PREDEFINED",  "t3"),
]


def fixture():
    conn = sqlite3.connect(":memory:")
    conn.executescript(db.SCHEMA)
    for targeting, mt, tid in ROWS:
        conn.execute(
            """INSERT INTO targeting_perf(date,campaign_id,ad_group_id,targeting,
                    match_type,target_id,impressions,clicks,cost,orders,sales,acos)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (LATEST, "c1", "g1", targeting, mt, tid, 100, 10, 4.0, 0, 0.0, None))
    conn.commit()
    return conn


class TheTwoKindsAreNotTheSameSet(unittest.TestCase):

    def setUp(self):
        self.conn = fixture()

    def tearDown(self):
        self.conn.close()

    def test_keyword_loads_only_keyword_match_types(self):
        got = sorted(r.field("match_type") for r in entities.load(self.conn, "keyword"))
        self.assertEqual(got, ["BROAD", "EXACT", "PHRASE"])

    def test_target_loads_only_product_and_auto_targeting(self):
        got = sorted(r.field("match_type") for r in entities.load(self.conn, "target"))
        self.assertEqual(got, ["TARGETING_EXPRESSION",
                               "TARGETING_EXPRESSION_PREDEFINED",
                               "TARGETING_EXPRESSION_PREDEFINED"])

    def test_the_two_sets_do_not_overlap(self):
        kw = {r.id for r in entities.load(self.conn, "keyword")}
        tg = {r.id for r in entities.load(self.conn, "target")}
        self.assertEqual(kw & tg, set(),
                         "a row reached BOTH kinds, so one rule can act twice on it")

    def test_together_they_still_cover_every_row(self):
        """The split must lose nothing. Narrowing one kind is only correct if the
        other picks the rest up — otherwise a clause stops being managed by any
        rule at all, silently."""
        kw = {r.id for r in entities.load(self.conn, "keyword")}
        tg = {r.id for r in entities.load(self.conn, "target")}
        self.assertEqual(kw | tg, {tid for _, _, tid in ROWS})

    def test_a_null_match_type_is_a_target_not_a_keyword(self):
        """Fail towards the kind whose rules are the more conservative. An
        unknown clause is not a keyword, so a keyword rule never reaches it."""
        self.conn.execute(
            """INSERT INTO targeting_perf(date,campaign_id,ad_group_id,targeting,
                    match_type,target_id,impressions,clicks,cost,orders,sales,acos)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (LATEST, "c1", "g1", "mystery", None, "x1", 1, 1, 0.1, 0, 0.0, None))
        self.conn.commit()
        self.assertNotIn("x1", {r.id for r in entities.load(self.conn, "keyword")})
        self.assertIn("x1", {r.id for r in entities.load(self.conn, "target")})


class TheSplitAlsoHoldsForRollingWindows(unittest.TestCase):
    """The rolling branch reads target_daily and had its own copy of the query,
    so fixing only the CURRENT branch would have left `IN LAST N DAYS` rules
    conflating the two kinds — the exact half-fix this codebase keeps finding."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(db.SCHEMA)
        window = db.daily_window(7)
        for targeting, mt, tid in ROWS:
            self.conn.execute(
                """INSERT INTO target_daily(date,campaign_id,ad_group_id,targeting,
                        match_type,target_id,impressions,clicks,cost,orders,sales,acos)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (window[0], "c1", "g1", targeting, mt, tid, 10, 2, 1.0, 0, 0.0, None))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _kinds(self, kind):
        return sorted(r.field("match_type") for r in
                      entities.load(self.conn, kind, window="ROLLING", window_days=7))

    def test_rolling_keyword_is_keywords_only(self):
        self.assertEqual(self._kinds("keyword"), ["BROAD", "EXACT", "PHRASE"])

    def test_rolling_target_is_targets_only(self):
        self.assertEqual(self._kinds("target"),
                         ["TARGETING_EXPRESSION",
                          "TARGETING_EXPRESSION_PREDEFINED",
                          "TARGETING_EXPRESSION_PREDEFINED"])


class TheEngineHasOneDefinitionOfAKeyword(unittest.TestCase):
    """Three files decide keyword-vs-target and they must agree. The executor
    routes the WRITE on its own copy, so a loader that disagreed would hand a
    keyword rule a clause the executor then sent to the target endpoint."""

    def test_the_loader_agrees_with_appctl(self):
        import appctl
        self.assertEqual(sorted(entities.KEYWORD_MATCH_TYPES),
                         sorted(appctl.KEYWORD_MATCH_TYPES))

    def test_the_loader_agrees_with_the_executor(self):
        from rules import executor
        for mt in entities.KEYWORD_MATCH_TYPES:
            self.assertTrue(executor._is_keyword({"ref": {"match_type": mt}}), mt)
        for mt in ("TARGETING_EXPRESSION", "TARGETING_EXPRESSION_PREDEFINED"):
            self.assertFalse(executor._is_keyword({"ref": {"match_type": mt}}), mt)


if __name__ == "__main__":
    unittest.main()
