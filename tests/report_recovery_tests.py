#!/usr/bin/env python3
"""Recovering reports Amazon finished after a run stopped polling.

A report that outran MAX_WAIT was deferred, and the next run requested a
fresh window — silently abandoning the old job even when Amazon completed it
minutes after we hung up. UK/DE stayed days stale exactly this way. These
tests pin: one status check recovers a completed old-window report, live
leftovers are left alone, and zombies past 48h are EXPIRED so health stops
counting them forever.

Run from the Ads folder:  python3 -m unittest tests.report_recovery_tests -v"""

import datetime
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ["ADS_MARKET"] = "US"

import db           # noqa: E402
import phase0_pull  # noqa: E402


def temp_conn():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    real = db.DB_PATH
    db.DB_PATH = path
    conn = db.connect()
    db.DB_PATH = real
    return conn, path


class OneShotClient:
    """get_report_info answers from a canned map; download returns canned rows.

    `get_report_info` rather than `get_report` because that is the call the
    pull makes now: the report metadata carries the window Amazon actually
    built, which the run log records beside the one that was asked for.
    """
    market = "US"

    def __init__(self, statuses, rows=None):
        self.statuses = statuses          # report_id -> (status, url)
        self.rows = rows or []
        self.status_calls = []

    def get_report_info(self, rid):
        self.status_calls.append(rid)
        status, url = self.statuses[rid]
        return {"status": status, "url": url}

    def get_report(self, rid):
        info = self.get_report_info(rid)
        return info.get("status"), info.get("url")

    def download_gzip_json(self, url):
        return self.rows


class Recovery(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = temp_conn()
        self._stored = []
        self._stored_daily = []
        self._orig_storer = phase0_pull.STORERS.get("campaigns")
        self._orig_daily_storer = phase0_pull.STORERS.get("targeting_daily")
        phase0_pull.STORERS["campaigns"] = \
            lambda conn, rows, end: self._stored.append((rows, end)) or len(rows)
        phase0_pull.STORERS["targeting_daily"] = \
            lambda conn, rows, end: self._stored_daily.append((rows, end)) or len(rows)

    def tearDown(self):
        phase0_pull.STORERS["campaigns"] = self._orig_storer
        phase0_pull.STORERS["targeting_daily"] = self._orig_daily_storer
        self.conn.close()
        os.unlink(self.path)

    def test_completed_old_window_report_is_banked_with_its_own_date(self):
        db.save_report_job(self.conn, "campaigns", "r-old", "2026-07-30")
        client = OneShotClient({"r-old": ("COMPLETED", "http://x")},
                               rows=[{"campaignId": 1}])
        n = phase0_pull.recover_leftover_reports(client, self.conn)
        self.assertEqual(n, 1)
        self.assertEqual(self._stored, [([{"campaignId": 1}], "2026-07-30")])
        job = db.get_report_job(self.conn, "campaigns")
        self.assertEqual(job[5], 1)        # downloaded
        self.assertEqual(job[2], "COMPLETED")

    def test_still_generating_leftover_is_left_for_tomorrow(self):
        db.save_report_job(self.conn, "campaigns", "r-slow", "2026-07-30")
        client = OneShotClient({"r-slow": ("PROCESSING", None)})
        n = phase0_pull.recover_leftover_reports(client, self.conn)
        self.assertEqual(n, 0)
        job = db.get_report_job(self.conn, "campaigns")
        self.assertEqual(job[5], 0)
        self.assertNotEqual(job[2], "EXPIRED")

    def test_todays_own_window_is_not_touched(self):
        db.save_report_job(self.conn, "campaigns", "r-today", phase0_pull.END)
        client = OneShotClient({})
        phase0_pull.recover_leftover_reports(client, self.conn)
        self.assertEqual(client.status_calls, [])   # resume path owns it

    def test_zombie_past_48h_is_expired_and_leaves_pending_count(self):
        db.save_report_job(self.conn, "campaigns", "r-dead", "2026-08-01")
        old = (datetime.datetime.now() - datetime.timedelta(hours=60)).isoformat(timespec="seconds")
        self.conn.execute("UPDATE report_jobs SET requested_at=?", (old,))
        self.conn.commit()
        client = OneShotClient({"r-dead": ("FAILED", None)})
        phase0_pull.recover_leftover_reports(client, self.conn)
        db.expire_stale_report_jobs(self.conn, hours=48)
        job = db.get_report_job(self.conn, "campaigns")
        self.assertIn(job[2], ("FAILED", "EXPIRED"))
        # health's pending count must not include dead rows forever
        cutoff = (datetime.datetime.now() - datetime.timedelta(hours=26)).isoformat()
        pending = self.conn.execute(
            """SELECT COUNT(*) FROM report_jobs
               WHERE downloaded=0 AND requested_at < ?
                 AND status NOT IN ('FAILED','CANCELLED','EXPIRED')""",
            (cutoff,)).fetchone()[0]
        self.assertEqual(pending, 0)

    def test_daily_reports_own_end_is_used_not_the_global_one(self):
        """Regression guard for the per-report window fix. targeting_daily's
        `end` happens to equal the global END today, so a test that reads it
        without forcing a difference would pass even if recovery reverted to
        comparing against phase0_pull.END directly. Patch it apart here so
        the test actually proves recovery reads EACH report's own end."""
        orig_end = phase0_pull.REPORTS["targeting_daily"]["end"]
        patched_end = "2026-07-15"
        self.assertNotEqual(patched_end, phase0_pull.END)
        phase0_pull.REPORTS["targeting_daily"]["end"] = patched_end
        try:
            db.save_report_job(self.conn, "targeting_daily", "r-daily-current", patched_end)
            client = OneShotClient({})
            phase0_pull.recover_leftover_reports(client, self.conn)
            self.assertEqual(client.status_calls, [])   # resume path owns it
        finally:
            phase0_pull.REPORTS["targeting_daily"]["end"] = orig_end

    def test_daily_report_from_an_older_window_is_recovered(self):
        orig_end = phase0_pull.REPORTS["targeting_daily"]["end"]
        patched_end = "2026-07-15"
        phase0_pull.REPORTS["targeting_daily"]["end"] = patched_end
        try:
            db.save_report_job(self.conn, "targeting_daily", "r-daily-old", "2026-07-08")
            client = OneShotClient({"r-daily-old": ("COMPLETED", "http://x")},
                                   rows=[{"date": "2026-07-08", "campaignId": 1}])
            n = phase0_pull.recover_leftover_reports(client, self.conn)
            self.assertEqual(n, 1)
            self.assertEqual(self._stored_daily,
                             [([{"date": "2026-07-08", "campaignId": 1}], "2026-07-08")])
            job = db.get_report_job(self.conn, "targeting_daily")
            self.assertEqual(job[5], 1)        # downloaded
            self.assertEqual(job[2], "COMPLETED")
        finally:
            phase0_pull.REPORTS["targeting_daily"]["end"] = orig_end

    def test_unknown_type_zombie_is_expired_by_the_sweep(self):
        # a type no run requests anymore — recovery never visits it, the
        # sweep still retires it
        db.save_report_job(self.conn, "purchased-legacy", "r-zombie", "2026-07-01")
        old = (datetime.datetime.now() - datetime.timedelta(hours=200)).isoformat(timespec="seconds")
        self.conn.execute("UPDATE report_jobs SET requested_at=?", (old,))
        self.conn.commit()
        db.expire_stale_report_jobs(self.conn, hours=48)
        self.assertEqual(db.get_report_job(self.conn, "purchased-legacy")[2], "EXPIRED")


if __name__ == "__main__":
    unittest.main()
