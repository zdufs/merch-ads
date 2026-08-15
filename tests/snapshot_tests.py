#!/usr/bin/env python3
"""Perf-snapshot freshness + resilient bulk writes.

Regression cover for the Aug 2026 outage: the nightly targeting report kept
dying with "disk I/O error", targeting_perf froze at 2026-07-29, and phase 2/3
kept querying it with campaign_perf's newer MAX(date) — matching zero rows and
reporting "no changes" instead of "no data". US bids and pauses were a silent
no-op for four nights and the dashboard's Estimated profit sat frozen.

Run from the Ads folder:  python3 -m unittest tests.snapshot_tests -v
"""

import datetime
import os
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["ADS_MARKET"] = "US"

import db  # noqa: E402


def temp_conn():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    real = db.DB_PATH
    db.DB_PATH = path
    conn = db.connect()
    db.DB_PATH = real
    return conn, path


class SnapshotDates(unittest.TestCase):
    """Each perf table owns its own date. Never borrow another table's."""

    def setUp(self):
        self.conn, self.path = temp_conn()
        # The exact shape of the outage: campaign_perf moved on, targeting_perf
        # stalled five days earlier.
        self.conn.executescript("""
            INSERT INTO campaign_perf(date,campaign_id,cost,sales,orders,clicks) VALUES
                ('2026-08-03','c1',10.0,50.0,2,20);
            INSERT INTO targeting_perf(date,campaign_id,ad_group_id,target_id,cost,sales,orders,clicks)
                VALUES ('2026-07-29','c1','g1','t1',10.0,50.0,2,20);
        """)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        os.unlink(self.path)

    def test_latest_snapshot_is_per_table(self):
        self.assertEqual(db.latest_snapshot(self.conn, "campaign_perf"), "2026-08-03")
        self.assertEqual(db.latest_snapshot(self.conn, "targeting_perf"), "2026-07-29")

    def test_borrowing_the_other_tables_date_matches_nothing(self):
        """The original bug, pinned: campaign_perf's date finds zero targeting rows."""
        borrowed = db.latest_snapshot(self.conn, "campaign_perf")
        rows = self.conn.execute(
            "SELECT * FROM targeting_perf WHERE date=?", (borrowed,)).fetchall()
        self.assertEqual(rows, [], "expected the cross-table date to match nothing")
        own = db.latest_snapshot(self.conn, "targeting_perf")
        rows = self.conn.execute(
            "SELECT * FROM targeting_perf WHERE date=?", (own,)).fetchall()
        self.assertEqual(len(rows), 1, "the table's own date must find its rows")

    def test_empty_table_returns_none(self):
        self.assertIsNone(db.latest_snapshot(self.conn, "search_term_perf"))


class SnapshotGate(unittest.TestCase):
    """Fail closed on stale evidence, like the econ gate."""

    def setUp(self):
        self.conn, self.path = temp_conn()
        self.today = datetime.date(2026, 8, 4)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.path)

    def _seed(self, date):
        self.conn.execute(
            "INSERT INTO targeting_perf(date,campaign_id,ad_group_id,target_id,cost)"
            " VALUES(?,'c1','g1','t1',1.0)", (date,))
        self.conn.commit()

    def test_fresh_snapshot_passes(self):
        self._seed("2026-08-03")          # the normal case: END = today-1
        gate = db.snapshot_gate(self.conn, "targeting_perf", today=self.today)
        self.assertTrue(gate["ok"])
        self.assertEqual(gate["age_days"], 1)
        self.assertEqual(gate["date"], "2026-08-03")

    def test_boundary_day_still_passes(self):
        self._seed("2026-08-01")          # exactly at the 3-day limit
        gate = db.snapshot_gate(self.conn, "targeting_perf", today=self.today)
        self.assertTrue(gate["ok"])
        self.assertEqual(gate["age_days"], 3)

    def test_stale_snapshot_fails_closed(self):
        self._seed("2026-07-29")          # the outage: 6 days behind
        gate = db.snapshot_gate(self.conn, "targeting_perf", today=self.today)
        self.assertFalse(gate["ok"])
        self.assertEqual(gate["age_days"], 6)
        self.assertIn("2026-07-29", gate["reason"])

    def test_empty_table_fails_closed(self):
        gate = db.snapshot_gate(self.conn, "targeting_perf", today=self.today)
        self.assertFalse(gate["ok"])
        self.assertIn("no snapshots", gate["reason"])

    def test_age_helper_handles_junk(self):
        self.assertIsNone(db.snapshot_age_days(None))
        self.assertIsNone(db.snapshot_age_days("not-a-date"))
        self.assertEqual(db.snapshot_age_days("2026-08-04", today=self.today), 0)


class FlakyConn:
    """Delegates to a real connection but can fail chosen executemany calls.

    sqlite3.Connection attributes are read-only, so the I/O error can only be
    injected through a wrapper. __enter__/__exit__ forward to the real
    connection so bulk_write's transaction semantics are the ones under test.
    """

    def __init__(self, conn, should_fail):
        self._conn = conn
        self._should_fail = should_fail
        self.calls = 0

    def executemany(self, sql, rows):
        self.calls += 1
        if self._should_fail(self.calls):
            raise sqlite3.OperationalError("disk I/O error")
        return self._conn.executemany(sql, rows)

    def __enter__(self):
        return self._conn.__enter__()

    def __exit__(self, *exc):
        return self._conn.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._conn, name)


class BulkWrite(unittest.TestCase):
    """Chunked, retried, all-or-nothing."""

    def setUp(self):
        self.conn, self.path = temp_conn()
        self.sql = ("INSERT OR REPLACE INTO targeting_perf"
                    "(date,campaign_id,ad_group_id,target_id,cost) VALUES(?,?,?,?,?)")

    def tearDown(self):
        self.conn.close()
        os.unlink(self.path)

    def _rows(self, n):
        return [("2026-08-03", "c1", f"g{i}", f"t{i}", 1.0) for i in range(n)]

    def _count(self):
        return self.conn.execute("SELECT COUNT(*) FROM targeting_perf").fetchone()[0]

    def test_writes_every_row_across_chunks(self):
        n = db.bulk_write(self.conn, self.sql, self._rows(12_000), "t", chunk=1000)
        self.assertEqual(n, 12_000)
        self.assertEqual(self._count(), 12_000)

    def test_retries_then_succeeds_on_transient_error(self):
        flaky = FlakyConn(self.conn, lambda call: call == 1)
        n = db.bulk_write(flaky, self.sql, self._rows(10), "t", chunk=5)
        self.assertEqual(n, 10)
        self.assertEqual(self._count(), 10)

    def test_raises_after_exhausting_retries_and_stores_nothing(self):
        flaky = FlakyConn(self.conn, lambda call: True)
        with self.assertRaises(sqlite3.OperationalError):
            db.bulk_write(flaky, self.sql, self._rows(10), "t", chunk=5, retries=2)
        self.assertEqual(self._count(), 0)

    def test_partial_failure_rolls_the_whole_snapshot_back(self):
        """A half-stored snapshot would read as real data downstream."""
        flaky = FlakyConn(self.conn, lambda call: call == 2)
        with self.assertRaises(sqlite3.OperationalError):
            db.bulk_write(flaky, self.sql, self._rows(10), "t", chunk=5, retries=1)
        self.assertEqual(self._count(), 0,
                         "first chunk must not survive a failed snapshot")

    def test_error_detail_never_raises(self):
        self.assertIsInstance(
            db._sqlite_error_detail(sqlite3.OperationalError("disk I/O error")), str)


if __name__ == "__main__":
    unittest.main()
