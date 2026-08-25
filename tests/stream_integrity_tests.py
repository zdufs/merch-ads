#!/usr/bin/env python3
"""The Stream database failing its own integrity check must be SAID.

On 2026-08-22 `stream_data.sqlite` corrupted between two hourly drains. It was
caught, but indirectly: `stream_check_failed` fired because the undercount check
raised `DatabaseError: database disk image is malformed`. That alert says the
CHECK could not run, which is a symptom seven markets repeated without any of
them naming the fault or the fix.

`quick_check` names it, and on that exact file it took about a millisecond where
the full `integrity_check` took nine. Both figures were measured before this was
written, because a guard whose cost nobody measured gets removed the first time
something feels slow.

The trap this file exists to document
-------------------------------------
A copy of the database taken WITHOUT its `-wal` sidecar reads as perfectly
healthy. During the incident the corruption lived in the WAL, and checking
`backups/stream_data.sqlite.corrupt-...` on its own returned a confident `ok` —
which nearly led to the conclusion that `quick_check` could not detect this
class of damage at all. It can. It was being pointed at a different database.

Run from the Ads folder:  python3 -m unittest tests.stream_integrity_tests -v
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))

import stream_store  # noqa: E402


def make_db(path, rows=4000):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (a INTEGER PRIMARY KEY, b TEXT)")
    conn.executemany("INSERT INTO t(b) VALUES(?)",
                     [(f"row-{i}" * 20,) for i in range(rows)])
    conn.execute("CREATE INDEX idx_b ON t(b)")
    conn.commit()
    conn.close()
    return path


def scribble(path, nbytes=600):
    """Real corruption: overwrite a page in the middle of the file.

    Deliberately not page 1 — damaging the header makes SQLite refuse to open
    the file at all, which is a different failure and one nobody could miss.
    """
    size = os.path.getsize(path)
    with open(path, "r+b") as fh:
        fh.seek(size // 2)
        fh.write(b"\xEE" * nbytes)
    return path


class ItDetectsRealCorruption(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="merchads-integrity-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_a_healthy_database_is_not_flagged(self):
        p = make_db(os.path.join(self.dir, "ok.sqlite"))
        self.assertEqual(stream_store._integrity(sqlite3.connect(p)), (False, None))

    def test_a_corrupted_database_is_flagged(self):
        p = scribble(make_db(os.path.join(self.dir, "bad.sqlite")))
        corrupt, detail = stream_store._integrity(sqlite3.connect(p))
        self.assertTrue(corrupt, "real page damage was reported as healthy")
        self.assertTrue(detail)

    def test_the_detail_names_the_fault_not_the_banner(self):
        """SQLite's first line is `*** in database main ***`, which names the
        database and never the problem. Showing that to the operator would be
        the same as showing nothing."""
        p = scribble(make_db(os.path.join(self.dir, "bad2.sqlite")))
        _, detail = stream_store._integrity(sqlite3.connect(p))
        self.assertFalse(detail.startswith("***"),
                         f"the banner reached the operator: {detail!r}")

    def test_a_check_that_cannot_RUN_is_unknown_not_healthy(self):
        """"Unknown" and "fine" are different answers and only one is safe to
        show. A closed connection cannot answer, so it must not answer False."""
        conn = sqlite3.connect(os.path.join(self.dir, "closed.sqlite"))
        conn.close()
        corrupt, _ = stream_store._integrity(conn)
        self.assertIsNone(corrupt, "a check that could not run reported healthy")


class TheWalIsPartOfTheDatabase(unittest.TestCase):
    """The mistake that cost a wrong conclusion during the incident."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="merchads-wal-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_a_copy_without_its_wal_is_a_different_database(self):
        p = os.path.join(self.dir, "w.sqlite")
        conn = sqlite3.connect(p)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE t (a INTEGER PRIMARY KEY, b TEXT)")
        conn.commit()
        conn.executemany("INSERT INTO t(b) VALUES(?)",
                         [(f"x{i}" * 40,) for i in range(3000)])
        conn.commit()
        rows_with_wal = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
        self.assertTrue(os.path.exists(p + "-wal"),
                        "no WAL sidecar was produced, so this proves nothing")

        # copy the main file ALONE, the way a careless backup would
        alone = os.path.join(self.dir, "alone.sqlite")
        shutil.copy(p, alone)
        try:
            rows_alone = sqlite3.connect(alone).execute(
                "SELECT COUNT(*) FROM t").fetchone()[0]
        except sqlite3.DatabaseError:
            # Even the schema was still in the WAL — the copy is not merely a
            # stale version of the database, it is an empty one.
            rows_alone = None
        conn.close()

        self.assertNotEqual(
            rows_alone, rows_with_wal,
            "the WAL held nothing, so this test is not exercising the trap it "
            "documents — a copy taken without -wal must be a DIFFERENT database")
        # and the headline consequence: it reads as perfectly healthy
        self.assertEqual(stream_store._integrity(sqlite3.connect(alone))[0], False,
                         "the point of this test is that a sidecar-less copy "
                         "answers a confident 'ok'")


class TheAlertIsRaisedOnce(unittest.TestCase):

    def test_only_the_default_market_reports_it(self):
        """One `stream_data.sqlite` serves every realm, so seven markets would
        raise seven alerts about one file. That is exactly what the incident
        looked like through `stream_check_failed`."""
        import appctl
        import markets
        real = os.environ.get("ADS_MARKET")
        try:
            os.environ["ADS_MARKET"] = "DE"
            markets.current.cache_clear() if hasattr(markets.current, "cache_clear") else None
            self.assertEqual(appctl._stream_corrupt_alerts("DE"), [],
                             "a non-default market raised the shared-database "
                             "alert, so one fault would alert seven times")
        finally:
            if real is None:
                os.environ.pop("ADS_MARKET", None)
            else:
                os.environ["ADS_MARKET"] = real
