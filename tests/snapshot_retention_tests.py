#!/usr/bin/env python3
"""The perf snapshot tables grew for ever and nothing ever deleted a row.

Each pull writes one row per entity per table. On 2026-08-22 US `targeting_perf`
held 2.0M rows over 45 snapshot dates — about 52,000 a night — and the seven
databases came to 2.0 GB. Nothing pruned. Years from mattering on a disk with
75 GB free, and still unbounded, which is the kind of thing that is only ever
fixed before it hurts.

Why 400 days
------------
The deepest table on the day this was written spanned 67 days, so the window
deletes NOTHING; it caps the future instead of reclaiming the present. It leaves
a year plus a month, enough for a year-over-year look at the drift series.
Amazon's own reporting retention is about 95 days, and the TRUE per-day history
lives in `target_daily` / `campaign_daily`, so no snapshot here is the only copy
of anything.

Deleting does not shrink the file. SQLite reuses the freed pages, which is what
bounds growth, and no VACUUM runs against a database the app holds open.

Run from the Ads folder:  python3 -m unittest tests.snapshot_retention_tests -v
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

TODAY = "2026-08-22"


def conn_with(rows):
    """rows: {table: [dates]}"""
    conn = sqlite3.connect(":memory:")
    conn.executescript(db.SCHEMA)
    for table, dates in rows.items():
        for i, d in enumerate(dates):
            if table == "campaign_perf":
                conn.execute("INSERT INTO campaign_perf(date,campaign_id,cost,sales)"
                             " VALUES(?,?,?,?)", (d, f"c{i}", 1.0, 2.0))
            elif table == "targeting_perf":
                conn.execute("INSERT INTO targeting_perf(date,campaign_id,ad_group_id,"
                             "targeting,target_id,cost,sales) VALUES(?,?,?,?,?,?,?)",
                             (d, "c1", "g1", f"kw{i}", f"t{i}", 1.0, 2.0))
            else:
                conn.execute("INSERT INTO search_term_perf(date,campaign_id,ad_group_id,"
                             "search_term,cost,sales) VALUES(?,?,?,?,?,?)",
                             (d, "c1", "g1", f"term{i}", 1.0, 2.0))
    conn.commit()
    return conn


def days_before(n, today=TODAY):
    return (datetime.date.fromisoformat(today) - datetime.timedelta(days=n)).isoformat()


class PreviewChangesNothing(unittest.TestCase):

    def test_a_preview_counts_without_deleting(self):
        conn = conn_with({"targeting_perf": [days_before(500), days_before(10)]})
        res = db.prune_snapshots(conn, days=400, today=TODAY)
        self.assertFalse(res["applied"])
        self.assertEqual(res["tables"]["targeting_perf"], 1)
        left = conn.execute("SELECT COUNT(*) FROM targeting_perf").fetchone()[0]
        self.assertEqual(left, 2, "a preview deleted rows")

    def test_apply_deletes_only_what_the_preview_counted(self):
        conn = conn_with({"targeting_perf": [days_before(500), days_before(401),
                                             days_before(10)]})
        preview = db.prune_snapshots(conn, days=400, today=TODAY)
        applied = db.prune_snapshots(conn, days=400, apply=True, today=TODAY)
        self.assertEqual(preview["total"], applied["total"])
        left = conn.execute("SELECT COUNT(*) FROM targeting_perf").fetchone()[0]
        self.assertEqual(left, 1)


class TheBoundaryIsInclusive(unittest.TestCase):
    """`date < cutoff`, so a row exactly at the window's edge is KEPT.

    Off by one here quietly deletes a day nobody meant to lose, and there is no
    second copy of a snapshot.
    """

    def test_a_row_exactly_at_the_cutoff_survives(self):
        conn = conn_with({"campaign_perf": [days_before(400)]})
        res = db.prune_snapshots(conn, days=400, apply=True, today=TODAY)
        self.assertEqual(res["total"], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM campaign_perf")
                         .fetchone()[0], 1)

    def test_a_row_one_day_past_it_goes(self):
        conn = conn_with({"campaign_perf": [days_before(401)]})
        res = db.prune_snapshots(conn, days=400, apply=True, today=TODAY)
        self.assertEqual(res["total"], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM campaign_perf")
                         .fetchone()[0], 0)


class EachTableIsJudgedOnItsOwnDates(unittest.TestCase):
    """The three tables are filled by independent report jobs and drift apart.

    Taking a cutoff from one and applying it to another is the standing mistake
    this engine has been burned by twice. Here the cutoff comes from the CLOCK,
    which every table shares, and each table is counted separately so the reply
    says which one held what.
    """

    def test_every_table_is_reported_separately(self):
        conn = conn_with({"campaign_perf": [days_before(500)],
                          "targeting_perf": [days_before(500), days_before(500)],
                          "search_term_perf": [days_before(10)]})
        res = db.prune_snapshots(conn, days=400, today=TODAY)
        self.assertEqual(res["tables"]["campaign_perf"], 1)
        self.assertEqual(res["tables"]["targeting_perf"], 2)
        self.assertEqual(res["tables"]["search_term_perf"], 0)
        self.assertEqual(res["total"], 3)

    def test_a_missing_table_is_skipped_rather_than_fatal(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE campaign_perf (date TEXT, cost REAL)")
        conn.execute("INSERT INTO campaign_perf VALUES (?,1.0)", (days_before(500),))
        conn.commit()
        res = db.prune_snapshots(conn, days=400, today=TODAY)
        self.assertEqual(res["tables"].get("campaign_perf"), 1)
        self.assertNotIn("targeting_perf", res["tables"])


class TheWindowCannotBeAbsurd(unittest.TestCase):

    def test_a_window_under_a_day_is_refused(self):
        conn = conn_with({"campaign_perf": [days_before(1)]})
        for bad in (0, -1):
            with self.subTest(days=bad):
                with self.assertRaises(ValueError):
                    db.prune_snapshots(conn, days=bad, today=TODAY)

    def test_the_shipped_window_is_far_past_anything_amazon_will_serve(self):
        """Amazon's reporting retention is ~95 days and backfill reaches ~92.

        A window near those would delete history that can never be re-fetched.
        """
        self.assertGreater(db.SNAPSHOT_RETENTION_DAYS, 365,
                           "the retention window was lowered below a year — "
                           "snapshots cannot be re-fetched once deleted")
