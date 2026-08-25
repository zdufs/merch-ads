#!/usr/bin/env python3
"""The guard around the seasonal tag map.

seasonal.json holds which design belongs to which season. It is gitignored
operator data, and on 2026-08-15 the public-release commit deleted the working
copy. load_config() then did what it does on a fresh clone — it copied the
shipped example over the gap — and the example has no ASINs. The scheduler ran
as a no-op for six days and nothing said a word.

These tests pin the two halves of the guard:
  * a backup, so a config that HAD tags can always be put back; and
  * a detector, so an empty tag map that used to be full is reported instead of
    passing for a fresh install.

Run from the Ads folder:  python3 -m unittest tests.seasonal_guard_tests -v
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ["ADS_MARKET"] = "US"

import seasonal_pause as sp  # noqa: E402

SEASONS = {"halloween": {"resume": "08-31", "pause": "11-02", "label": "Halloween"}}


def cfg(tags=None):
    return {"seasons": dict(SEASONS), "asins": dict(tags or {})}


def writes_log_conn(rows=()):
    """An in-memory DB carrying only writes_log, in the engine's shape."""
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE writes_log (
        applied_at TEXT, action TEXT, entity_type TEXT, entity_id TEXT,
        detail TEXT, prev_state TEXT, result TEXT)""")
    for at, action, entity_id in rows:
        conn.execute("INSERT INTO writes_log (applied_at, action, entity_type, entity_id)"
                     " VALUES (?,?,'adGroup',?)", (at, action, entity_id))
    conn.commit()
    return conn


class SeasonalPaths(unittest.TestCase):
    """Every test writes into a throwaway folder.

    The operator's real seasonal.json is 15,674 tags of hand-curated work. A
    test that pointed at it even once could empty it, which is the exact
    accident this whole guard exists to prevent.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._conns = []
        self._saved = (sp.CONFIG, sp.CONFIG_BACKUP, sp.CONFIG_EXAMPLE)
        sp.CONFIG = os.path.join(self.tmp, "seasonal.json")
        sp.CONFIG_BACKUP = os.path.join(self.tmp, "seasonal.backup.json")
        sp.CONFIG_EXAMPLE = os.path.join(self.tmp, "seasonal.example.json")
        with open(sp.CONFIG_EXAMPLE, "w") as f:
            json.dump(cfg(), f)          # the shipped example: windows, no ASINs

    def tearDown(self):
        for conn in self._conns:
            conn.close()
        sp.CONFIG, sp.CONFIG_BACKUP, sp.CONFIG_EXAMPLE = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def conn(self, rows=()):
        c = writes_log_conn(rows)
        self._conns.append(c)
        return c

    def write(self, path, data):
        with open(path, "w") as f:
            json.dump(data, f)

    def read(self, path):
        with open(path) as f:
            return json.load(f)


class GenuinelyFreshInstall(SeasonalPaths):
    """No config, no backup, and no example either.

    The example is a REPO file. The app ships standalone now, so its data folder
    is wherever the operator points it and may never have held one. load_config
    fell through to reading a file that was not there, and FileNotFoundError
    travelled all the way out of the bridge: the Seasonal screen showed a
    filesystem path instead of an empty list.
    """

    def setUp(self):
        super().setUp()
        os.remove(sp.CONFIG_EXAMPLE)     # nothing to seed from at all

    def test_load_config_returns_an_empty_config_instead_of_raising(self):
        got = sp.load_config()
        self.assertEqual(got.get("asins"), {})
        self.assertEqual(got.get("seasons"), {})

    def test_it_writes_the_empty_config_so_the_next_read_is_plain(self):
        sp.load_config()
        self.assertTrue(os.path.exists(sp.CONFIG))
        self.assertEqual(self.read(sp.CONFIG)["asins"], {})

    def test_a_backup_still_wins_over_the_empty_fallback(self):
        """The fallback must never outrank a real backup — that ordering is the
        whole point of the guard and cost six dead days to learn."""
        self.write(sp.CONFIG_BACKUP, cfg({"B0AAA": "halloween"}))
        self.assertEqual(sp.load_config()["asins"], {"B0AAA": "halloween"})


class Backup(SeasonalPaths):

    def test_saving_a_tagged_config_writes_a_backup(self):
        sp.save_config(cfg({"B0AAA": "halloween"}))
        self.assertTrue(os.path.exists(sp.CONFIG_BACKUP))
        self.assertEqual(self.read(sp.CONFIG_BACKUP)["asins"], {"B0AAA": "halloween"})

    def test_an_empty_config_never_overwrites_a_tagged_backup(self):
        sp.save_config(cfg({"B0AAA": "halloween"}))
        sp.save_config(cfg())                       # operator untags everything
        self.assertEqual(self.read(sp.CONFIG)["asins"], {})
        self.assertEqual(self.read(sp.CONFIG_BACKUP)["asins"], {"B0AAA": "halloween"},
                         "the backup must survive an empty save, or it cannot restore")

    def test_a_tagged_save_does_replace_an_older_backup(self):
        sp.save_config(cfg({"B0AAA": "halloween"}))
        sp.save_config(cfg({"B0AAA": "halloween", "B0BBB": "halloween"}))
        self.assertEqual(len(self.read(sp.CONFIG_BACKUP)["asins"]), 2)


class Restore(SeasonalPaths):

    def test_a_missing_config_comes_back_from_the_backup_not_the_example(self):
        self.write(sp.CONFIG_BACKUP, cfg({"B0AAA": "halloween"}))
        loaded = sp.load_config()
        self.assertEqual(loaded["asins"], {"B0AAA": "halloween"})
        self.assertTrue(os.path.exists(sp.CONFIG), "the restore must land on disk")
        self.assertEqual(self.read(sp.CONFIG)["asins"], {"B0AAA": "halloween"})

    def test_the_restore_message_goes_to_stderr(self):
        """appctl promises exactly one JSON object on stdout, and cmd_seasons /
        season-tag all call load_config(). A restore line on stdout would turn
        those reads into "the app couldn't decode it"."""
        import contextlib, io
        self.write(sp.CONFIG_BACKUP, cfg({"B0AAA": "halloween"}))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            sp.load_config()
        self.assertEqual(out.getvalue(), "",
                         "anything on stdout corrupts appctl's JSON envelope")
        self.assertIn("restored", err.getvalue())

    def test_a_fresh_install_still_seeds_from_the_example(self):
        loaded = sp.load_config()          # no config, no backup
        self.assertEqual(loaded["asins"], {})
        self.assertTrue(os.path.exists(sp.CONFIG))

    def test_an_existing_config_is_never_overwritten_by_the_backup(self):
        self.write(sp.CONFIG, cfg({"B0CCC": "halloween"}))
        self.write(sp.CONFIG_BACKUP, cfg({"B0AAA": "halloween"}))
        self.assertEqual(sp.load_config()["asins"], {"B0CCC": "halloween"})

    def test_an_empty_config_is_left_alone_because_untagging_may_be_deliberate(self):
        self.write(sp.CONFIG, cfg())
        self.write(sp.CONFIG_BACKUP, cfg({"B0AAA": "halloween"}))
        self.assertEqual(sp.load_config()["asins"], {},
                         "silently re-tagging designs would be a write nobody asked for")


class Detector(SeasonalPaths):

    def test_a_tagged_config_is_never_reported_lost(self):
        self.write(sp.CONFIG, cfg({"B0AAA": "halloween"}))
        self.assertIsNone(sp.tags_lost(self.conn()))

    def test_a_fresh_install_is_not_reported_lost(self):
        self.write(sp.CONFIG, cfg())
        self.assertIsNone(sp.tags_lost(self.conn()),
                          "no backup and no seasonal writes = nothing was ever tagged")

    def test_an_empty_config_beside_a_tagged_backup_is_reported(self):
        self.write(sp.CONFIG, cfg())
        self.write(sp.CONFIG_BACKUP, cfg({"B0AAA": "halloween", "B0BBB": "halloween"}))
        lost = sp.tags_lost(self.conn())
        self.assertIsNotNone(lost)
        self.assertEqual(lost["backup_tags"], 2)

    def test_ad_groups_we_paused_and_can_no_longer_release_are_reported(self):
        self.write(sp.CONFIG, cfg())
        conn = self.conn([("2026-07-10T10:00:00", "seasonal_pause", "111"),
                          ("2026-07-10T10:00:00", "seasonal_pause", "222")])
        lost = sp.tags_lost(conn)
        self.assertIsNotNone(lost)
        self.assertEqual(lost["stranded"], 2)

    def test_a_pause_we_already_released_is_not_stranded(self):
        self.write(sp.CONFIG, cfg())
        conn = self.conn([("2026-07-10T10:00:00", "seasonal_pause", "111"),
                          ("2026-08-31T10:00:00", "seasonal_enable", "111")])
        self.assertIsNone(sp.tags_lost(conn))

    def test_the_detector_survives_a_database_with_no_writes_log(self):
        self.write(sp.CONFIG, cfg())
        self.write(sp.CONFIG_BACKUP, cfg({"B0AAA": "halloween"}))
        bare = sqlite3.connect(":memory:")
        self._conns.append(bare)
        lost = sp.tags_lost(bare)
        self.assertIsNotNone(lost, "the file-level signal must not need the DB")
        self.assertEqual(lost["stranded"], 0)

    def test_the_detector_works_with_no_connection_at_all(self):
        self.write(sp.CONFIG, cfg())
        self.write(sp.CONFIG_BACKUP, cfg({"B0AAA": "halloween"}))
        self.assertIsNotNone(sp.tags_lost(None))

    def test_the_reason_names_what_it_found(self):
        self.write(sp.CONFIG, cfg())
        self.write(sp.CONFIG_BACKUP, cfg({"B0AAA": "halloween"}))
        conn = self.conn([("2026-07-10T10:00:00", "seasonal_pause", "111")])
        lost = sp.tags_lost(conn)
        self.assertIn("seasonal.backup.json", lost["reason"])
        self.assertIn("1 ad group", lost["reason"])


if __name__ == "__main__":
    unittest.main()
