#!/usr/bin/env python3
"""A bad date is refused, and "no data" never reads as "nothing to report".

Both halves came out of the 2026-08-24 runtime sweep, and both are the same
failure wearing two hats: a reply that is technically true and reads as a
verdict.

  * `report --start not-a-date` answered ok:true with an all-zero period.
    The string went straight into the SQL and matched nothing, so the Reports
    screen would draw a $0.00 rollup that is visually identical to a genuinely
    quiet fortnight. `--days` and `--limit` already refuse a bad value.
    `digest --since garbage` did the same, and a post-run digest reading
    "the nightly did nothing" is the worst version of it.
  * `killlist` on a folder with no data was indistinguishable from a healthy
    account with nothing worth killing: the two replies differed only in the
    `skipped` counters, which are non-zero today by luck.
  * `stream-today` reported `conversions.available: true` with `sales: 0` for
    any day with no conversion rows, because `available` asked whether the
    DATASET had ever delivered. CLAUDE.md names that exact shape: a zero reads
    as "sold nothing" rather than "cannot see sales yet".

Run from the Ads folder:  python3 -m unittest tests.bad_input_and_no_data_tests -v
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


class AppctlCase(unittest.TestCase):
    """Every call runs against an EMPTY data folder, so nothing can reach
    Amazon: there is no `.env`, and load_env refuses before any client is
    built. Same safety argument as tests/envelope_contract_tests.py."""

    @classmethod
    def setUpClass(cls):
        cls.data = tempfile.mkdtemp(prefix="merchads-badinput-")
        cls.pod = tempfile.mkdtemp(prefix="merchads-badinput-pod-")
        cls.env = dict(os.environ, ADS_MARKET="US",
                       MERCHADS_DATA_DIR=cls.data, MERCHADS_POD_DIR=cls.pod)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.data, ignore_errors=True)
        shutil.rmtree(cls.pod, ignore_errors=True)

    def call(self, *argv):
        p = subprocess.run([sys.executable, APPCTL, *argv],
                           capture_output=True, text=True, env=self.env,
                           cwd=self.data, timeout=120, stdin=subprocess.DEVNULL)
        return json.loads(p.stdout)


class ABadDateIsRefused(AppctlCase):

    def test_report_refuses_a_start_that_is_not_a_date(self):
        r = self.call("report", "--start", "not-a-date")
        self.assertFalse(r["ok"], f"report accepted a garbage date: {r}")
        self.assertIn("--start", r["error"])

    def test_report_refuses_an_end_that_is_not_a_date(self):
        r = self.call("report", "--end", "also-not")
        self.assertFalse(r["ok"], f"report accepted a garbage date: {r}")
        self.assertIn("--end", r["error"])

    def test_report_still_accepts_a_real_range(self):
        """Otherwise the refusal above could be refusing everything."""
        r = self.call("report", "--start", "2026-08-01", "--end", "2026-08-05")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["data"]["start"], "2026-08-01")

    def test_digest_refuses_a_since_that_is_not_a_timestamp(self):
        r = self.call("digest", "--since", "garbage")
        self.assertFalse(r["ok"], f"digest accepted a garbage timestamp: {r}")
        self.assertIn("--since", r["error"])

    def test_digest_still_accepts_a_real_timestamp(self):
        r = self.call("digest", "--since", "2026-08-20T10:00:00")
        self.assertTrue(r["ok"], r)

    def test_stream_today_refuses_a_day_that_is_not_a_date(self):
        r = self.call("stream-today", "--day", "banana")
        self.assertFalse(r["ok"], f"stream-today accepted a garbage day: {r}")
        self.assertIn("--day", r["error"])


class KilllistSaysWhenItJudgedNothing(AppctlCase):

    def test_no_snapshot_is_reported_as_no_snapshot(self):
        r = self.call("killlist")
        self.assertTrue(r["ok"], r)
        data = r["data"]
        self.assertIsNone(data["as_of"],
                          "a market with no targeting snapshot claimed one")
        self.assertEqual(data["evaluated"], 0)
        self.assertEqual(data["count"], 0)
        self.assertTrue(data.get("note"),
                        "nothing in the reply says the list is not a verdict")

    def test_a_real_all_clear_is_distinguishable_from_no_data(self):
        """The point of the fix, measured against a REAL snapshot.

        A market with one banked ad group that passes the thresholds also
        answers `count: 0` and `designs: []`. Before this it was
        byte-identical to the reply above apart from the `skipped` counters,
        which are non-zero today by luck — a day with nothing in transition and
        no cohort groups would have made the two the same reply."""
        folder = tempfile.mkdtemp(prefix="merchads-killlist-")
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        path = os.path.join(folder, "ads_data.sqlite")
        sys.path.insert(0, os.path.join(HERE, "engine"))
        import db  # noqa: E402  (engine import needs the path above)
        conn = sqlite3.connect(path)
        conn.executescript(db.SCHEMA)
        db._migrate(conn)
        conn.execute(
            "INSERT INTO targeting_perf(date,campaign_id,ad_group_id,targeting,"
            "match_type,target_id,impressions,clicks,cost,orders,sales,acos) "
            "VALUES('2026-08-23','C1','A1','shirt','EXACT','T1',900,20,10.0,4,60.0,0.16)")
        conn.execute(
            "INSERT INTO ad_group_product(ad_group_id,asin,product_type,brand,"
            "list_price,lifetime_sales,mapped_at) "
            "VALUES('A1','B01','standard_tshirt','x',19.99,3,'2026-08-23')")
        conn.execute(
            "INSERT INTO ad_groups(ad_group_id,campaign_id,name,state,default_bid,"
            "pulled_at) VALUES('A1','C1','ag','ENABLED',0.3,'2026-08-23')")
        conn.commit()
        conn.close()

        env = dict(self.env, MERCHADS_DATA_DIR=folder, MERCHADS_POD_DIR=folder)
        p = subprocess.run([sys.executable, APPCTL, "killlist"],
                           capture_output=True, text=True, env=env, cwd=folder,
                           timeout=120, stdin=subprocess.DEVNULL)
        judged = json.loads(p.stdout)["data"]
        empty = self.call("killlist")["data"]

        self.assertEqual(judged["count"], 0, "fixture design should pass")
        self.assertEqual(empty["count"], 0)
        self.assertEqual(judged["as_of"], "2026-08-23")
        self.assertEqual(judged["evaluated"], 1)
        self.assertIsNone(empty["as_of"])
        self.assertEqual(empty["evaluated"], 0)


if __name__ == "__main__":
    unittest.main()
