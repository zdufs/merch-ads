#!/usr/bin/env python3
"""A read must not create the market database, and `has_data` must mean data.

Two halves of one failure, found by a runtime sweep on 2026-08-24.

`db.connect(ro=True)` fell through to a read-write open when the file was not
there, and that open runs the whole SCHEMA. So the FIRST read of a fresh data
folder — opening the Dashboard is enough — left a 254 KB schema-only
`ads_data.sqlite` behind. `has_data` was `os.path.exists(path)`, so it then
answered true for an account nobody had ever pulled. The app gates its profile
picker on that field and System Health counts it.

The same `os.path.exists` said yes to two other files that hold nothing: 4 KB of
random bytes, and a DIRECTORY named ads_data.sqlite. Every other command in
those folders answered "file is not a database", so the markets reply was the
one place claiming they were fine.

Run from the Ads folder:  python3 -m unittest tests.read_side_effects_tests -v
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPCTL = os.path.join(HERE, "engine", "appctl.py")
sys.path.insert(0, os.path.join(HERE, "engine"))

import db  # noqa: E402


def _schema_only(path):
    conn = sqlite3.connect(path)
    conn.executescript(db.SCHEMA)
    conn.commit()
    conn.close()


class HasDataMeansData(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="merchads-hasdata-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def path(self, name="ads_data.sqlite"):
        return os.path.join(self.dir, name)

    def test_a_missing_file_holds_nothing(self):
        self.assertFalse(db.has_data(self.path()))

    def test_a_schema_only_database_holds_nothing(self):
        """The exact file a read used to leave behind."""
        _schema_only(self.path())
        self.assertTrue(os.path.exists(self.path()))
        self.assertFalse(db.has_data(self.path()))

    def test_a_populated_database_holds_data(self):
        _schema_only(self.path())
        conn = sqlite3.connect(self.path())
        conn.execute("INSERT INTO campaigns VALUES(?,?,?,?,?,?,?)",
                     ("1", "Lotto 1", "ENABLED", "MANUAL", 5.0, "legacy", "2026-08-24"))
        conn.commit()
        conn.close()
        self.assertTrue(db.has_data(self.path()))

    def test_garbage_bytes_are_not_data(self):
        with open(self.path(), "wb") as fh:
            fh.write(os.urandom(4096))
        self.assertFalse(db.has_data(self.path()))

    def test_a_directory_wearing_the_name_is_not_data(self):
        os.mkdir(self.path())
        self.assertFalse(db.has_data(self.path()))


class ConnectReadOnlyNeverCreatesTheFile(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="merchads-roconnect-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self._saved = db.DB_PATH
        db.DB_PATH = os.path.join(self.dir, "ads_data.sqlite")
        self.addCleanup(self._restore)

    def _restore(self):
        db.DB_PATH = self._saved

    def test_a_read_answers_empty_and_leaves_no_file(self):
        conn = db.connect(ro=True)
        try:
            rows = conn.execute("SELECT * FROM campaign_perf").fetchall()
        finally:
            conn.close()
        self.assertEqual(rows, [])
        self.assertEqual(os.listdir(self.dir), [],
                         "a read created something in the data folder")

    def test_that_read_cannot_write(self):
        """query_only, so a caller that meant to write fails loudly.

        Without it the write would land in a database that vanishes with the
        process, which is worse than either creating the file or refusing."""
        conn = db.connect(ro=True)
        self.addCleanup(conn.close)
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("INSERT INTO campaigns VALUES('1',NULL,NULL,NULL,NULL,NULL,NULL)")


# Documented READ endpoints that touch a database. Deliberately not the whole
# dispatcher: writes and live commands are excluded, and `seasons` is excluded
# because seeding `seasonal.json` is the documented data-loss guard.
READ_COMMANDS = [
    "markets", "metrics", "health", "alerts", "campaigns", "alltargets",
    "daily", "monthly", "periods", "killlist", "stale", "harvest", "bidreport",
    "audit", "synccal", "accumulated-asins", "accumulated-keywords",
    "harvest-prune", "negatives-preview", "econ-gate", "maxbid", "change-cap",
    "portfolio-cap", "prune-snapshots", "stream-fields", "stream-advertisers",
    "stream-today", "catalog-cache", "run-status",
]


class ReadsDoNotCreateDatabases(unittest.TestCase):
    """Run each read against a genuinely empty folder and count what appeared."""

    @classmethod
    def setUpClass(cls):
        cls.data = tempfile.mkdtemp(prefix="merchads-readsweep-")
        cls.pod = tempfile.mkdtemp(prefix="merchads-readsweep-pod-")
        cls.env = dict(os.environ, ADS_MARKET="US",
                       MERCHADS_DATA_DIR=cls.data, MERCHADS_POD_DIR=cls.pod)
        cls.replies = {}
        for cmd in READ_COMMANDS:
            p = subprocess.run([sys.executable, APPCTL, cmd],
                               capture_output=True, text=True, env=cls.env,
                               cwd=cls.data, timeout=120,
                               stdin=subprocess.DEVNULL)
            cls.replies[cmd] = p.stdout

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.data, ignore_errors=True)
        shutil.rmtree(cls.pod, ignore_errors=True)

    def test_the_sweep_actually_ran(self):
        """Otherwise every assertion below passes on nothing."""
        for cmd in ("markets", "metrics", "killlist"):
            payload = json.loads(self.replies[cmd])
            self.assertIn("ok", payload, f"{cmd} did not answer an envelope")

    def test_no_database_was_created_by_a_read(self):
        left = sorted(f for f in os.listdir(self.data) if ".sqlite" in f)
        self.assertEqual(left, [], f"reads created {left} in the data folder")

    def test_markets_still_says_the_account_was_never_pulled(self):
        payload = json.loads(self.replies["markets"])
        flags = {m["code"]: m["has_data"] for m in payload["data"]["markets"]}
        self.assertFalse(any(flags.values()),
                         f"a read made a market look populated: {flags}")


class OneBadMarketFileDoesNotTakeSystemHealthDown(unittest.TestCase):
    """`health` opens seven databases, and it opened them outside its own
    try/except. So one unreadable file raised out of the whole command and the
    screen read "file is not a database" for every market, naming none of them.
    """

    def setUp(self):
        self.data = tempfile.mkdtemp(prefix="merchads-health-")
        self.pod = tempfile.mkdtemp(prefix="merchads-health-pod-")
        self.addCleanup(shutil.rmtree, self.data, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.pod, ignore_errors=True)
        with open(os.path.join(self.data, "ads_data.sqlite"), "wb") as fh:
            fh.write(os.urandom(4096))
        _schema_only(os.path.join(self.data, "ads_data_UK.sqlite"))

    def test_the_broken_market_is_named_and_the_others_still_answer(self):
        env = dict(os.environ, MERCHADS_DATA_DIR=self.data,
                   MERCHADS_POD_DIR=self.pod)
        p = subprocess.run([sys.executable, APPCTL, "health"],
                           capture_output=True, text=True, env=env,
                           cwd=self.data, timeout=120, stdin=subprocess.DEVNULL)
        payload = json.loads(p.stdout)
        self.assertTrue(payload["ok"], f"one bad file broke the whole reply: {payload}")
        rows = {m["market"]: m for m in payload["data"]["markets"]}
        self.assertIsNotNone(rows["US"].get("error"),
                             "the unreadable market was not named")
        self.assertIsNone(rows["UK"].get("error"),
                          "a readable market inherited the other one's fault")
        self.assertFalse(rows["US"]["has_data"])
        self.assertFalse(rows["UK"]["has_data"],
                         "a schema-only database is not data")


if __name__ == "__main__":
    unittest.main()
