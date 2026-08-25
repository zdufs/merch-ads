#!/usr/bin/env python3
"""Reading a WAL database whose sidecars are gone.

Run from the Ads folder:  python3 -m unittest tests.readonly_open_tests -v

Opening `mode=ro` needs the `-shm` shared-memory index, and a read-only
connection may not create one. SQLite deletes `-wal` and `-shm` when the last
connection closes, so a market database sits sidecar-less most of the day.

Whether that is fatal depends on the SQLite BUILD, which is why it looked like
a ghost. Measured on this Mac, 2026-08-20:

    Homebrew 3.53.4 (the engine's python3)   reads it fine — heap-memory WAL
    Apple 3.51.0    (/usr/bin/sqlite3, and
                     the SwiftUI app)        FAILS: "unable to open database
                                             file" (SQLITE_CANTOPEN)

So the engine never noticed and the app was blind: "DB direct" read "—" for
every market and the sidebar footer said "no local data". The nightly runs
under whichever python3 is available, and the documented fallback is PATH
python3, so the engine can land on an older SQLite too.

db.open_readonly probes with a real query — the open itself always succeeds,
only the FIRST QUERY fails — and on failure reopens read-write with
query_only set, which SQLite enforces itself. These tests pin both halves: it
must read, and it must still be unable to write.
"""

import os
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ.setdefault("ADS_MARKET", "US")

import db   # noqa: E402


def wal_db_without_sidecars():
    """A WAL database in the state the app finds one in: cleanly closed."""
    folder = tempfile.mkdtemp()
    path = os.path.join(folder, "market.sqlite")
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE campaigns(campaign_id TEXT, state TEXT)")
    conn.execute("INSERT INTO campaigns VALUES ('c1','ENABLED')")
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    for sidecar in (path + "-wal", path + "-shm"):
        if os.path.exists(sidecar):
            os.remove(sidecar)
    return path


class ReadOnlyOpenTests(unittest.TestCase):
    def setUp(self):
        self.path = wal_db_without_sidecars()

    def test_it_reads_a_cleanly_closed_wal_database(self):
        """The state every market database is in between runs. Under Apple's
        SQLite a plain mode=ro handle cannot read this at all."""
        conn = db.open_readonly(self.path)
        self.assertEqual(conn.execute("SELECT state FROM campaigns").fetchone()[0], "ENABLED")
        conn.close()

    def test_open_readonly_reads_it_anyway(self):
        conn = db.open_readonly(self.path)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0], 1)
        conn.close()

    def test_the_fallback_handle_still_cannot_write(self):
        """query_only is enforced by SQLite, not by our good intentions."""
        conn = db.open_readonly(self.path)
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("INSERT INTO campaigns VALUES ('c2','ENABLED')")
            conn.commit()
        conn.close()
        again = db.open_readonly(self.path)
        self.assertEqual(again.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0], 1)
        again.close()

    def test_it_never_creates_a_database_that_is_not_there(self):
        missing = os.path.join(os.path.dirname(self.path), "no_such_market.sqlite")
        with self.assertRaises(sqlite3.OperationalError):
            db.open_readonly(missing).execute("SELECT 1").fetchone()
        self.assertFalse(os.path.exists(missing))

    def test_a_database_with_its_sidecars_present_takes_the_read_only_path(self):
        keeper = sqlite3.connect(self.path)          # recreates -wal/-shm
        keeper.execute("SELECT COUNT(*) FROM campaigns").fetchone()
        try:
            conn = db.open_readonly(self.path)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO campaigns VALUES ('c3','ENABLED')")
            conn.close()
        finally:
            keeper.close()


if __name__ == "__main__":
    unittest.main()
