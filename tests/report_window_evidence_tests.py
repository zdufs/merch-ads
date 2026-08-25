#!/usr/bin/env python3
"""Every trailing-30 report says what window it asked for and what came back.

docs/open-eu-trailing30-window.md is open on one question: a snapshot whose end
is a day older reconciles against THIRTY-ONE days of `campaign_daily` while a
current one reconciles against thirty. Either Amazon builds a wider window than
it was asked for, or the request is wider than the constants say. Nothing in
the run log could tell those apart.

What the log DID carry was one line per market per pull:

    [DE] profile 337... | window 2026-07-25 → 2026-08-23

That is the module's START and END, printed once. It speaks for the three
trailing-30 snapshots and for nothing else: `targeting_daily` asks for a
shorter window, a RESUMED report was requested on some earlier day whose start
is not stored anywhere, and no line at all recorded what Amazon actually built.

So each report now prints its own REQUESTED window when it is created, and its
RETURNED window — Amazon's own echo — when it completes, beside the row count,
the spend and the date the snapshot is filed under. On STDERR, because stdout
is the narrative log here and the JSON envelope everywhere appctl reaches.

Run from the Ads folder:
    python3 -m unittest tests.report_window_evidence_tests -v
"""

import io
import os
import sys
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ.setdefault("ADS_MARKET", "US")

import phase0_pull as p0  # noqa: E402


class _FakeClient:
    """Just the two report calls, with a scripted answer."""

    def __init__(self, market="DE", info=None):
        self.market = market
        self.created = []
        self._info = info or {}

    def create_report(self, report_type_id, columns, group_by, start, end,
                      time_unit="SUMMARY"):
        self.created.append((report_type_id, start, end, time_unit))
        return f"rid-{len(self.created)}"

    def get_report_info(self, report_id):
        return dict(self._info)

    def download_gzip_json(self, url):
        return self._info.get("_rows", [])


def _fake_db(jobs=None):
    """The three db calls ensure_report_jobs / poll_and_store make."""
    jobs = jobs or {}
    saved = {}
    return types.SimpleNamespace(
        get_report_job=lambda conn, key: jobs.get(key),
        save_report_job=lambda conn, key, rid, end: saved.setdefault(key, (rid, end)),
        set_report_status=lambda *a, **k: None,
        log_pull=lambda *a, **k: None,
        _saved=saved,
    )


class _Capture:
    """Run something with stdout and stderr captured separately."""

    def __init__(self):
        self.out, self.errs = io.StringIO(), io.StringIO()

    def run(self, fn, *a, **k):
        with redirect_stdout(self.out), redirect_stderr(self.errs):
            return fn(*a, **k)

    def window_lines(self):
        return [l for l in self.errs.getvalue().splitlines() if l.startswith("[window]")]


class TheWindowIsMeasuredNotAssumed(unittest.TestCase):

    def test_the_trailing_thirty_constants_really_span_thirty_days(self):
        """START = today-30 with END = today-1, both ends inclusive."""
        self.assertEqual(30, p0._span_days(p0.START, p0.END))

    def test_a_span_of_one_day_is_one_day(self):
        self.assertEqual(1, p0._span_days("2026-08-23", "2026-08-23"))

    def test_the_thirty_one_day_window_the_doc_is_about(self):
        """23 July to 22 August is the span the old request produced."""
        self.assertEqual(31, p0._span_days("2026-07-23", "2026-08-22"))

    def test_a_window_that_cannot_be_read_is_none_not_a_guess(self):
        self.assertIsNone(p0._span_days(None, "2026-08-23"))
        self.assertIsNone(p0._span_days("2026-08-23", "not a date"))

    def test_the_daily_report_asks_for_its_own_shorter_window(self):
        """The market's one `| window` line cannot speak for this report."""
        self.assertEqual(p0.REPORTS["targeting_daily"]["start"], p0.DAILY_START)
        self.assertNotEqual(p0.DAILY_START, p0.START)


class EveryRequestRecordsItsOwnWindow(unittest.TestCase):

    def setUp(self):
        self._db, p0.db = p0.db, _fake_db()
        self.addCleanup(setattr, p0, "db", self._db)

    def test_every_report_prints_the_window_it_was_sent(self):
        cap = _Capture()
        client = _FakeClient(market="DE")
        cap.run(p0.ensure_report_jobs, client, None)
        lines = cap.window_lines()
        self.assertEqual(len(p0.REPORTS), len(lines),
                         "a report was created with no window line")
        for key in p0.REPORTS:
            self.assertTrue(any(f" {key} REQUESTED " in l for l in lines),
                            f"{key} recorded no requested window")

    def test_the_line_carries_the_market_the_start_the_end_and_the_span(self):
        cap = _Capture()
        cap.run(p0.ensure_report_jobs, _FakeClient(market="DE"), None)
        camp = [l for l in cap.window_lines() if " campaigns REQUESTED " in l][0]
        self.assertIn("[window] DE campaigns REQUESTED", camp)
        self.assertIn(f"start={p0.START}", camp)
        self.assertIn(f"end={p0.END}", camp)
        self.assertIn("days=30", camp)
        self.assertIn("unit=SUMMARY", camp)

    def test_the_daily_report_records_its_own_window_not_the_shared_one(self):
        """The whole reason one line per market is not enough."""
        cap = _Capture()
        cap.run(p0.ensure_report_jobs, _FakeClient(), None)
        daily = [l for l in cap.window_lines() if " targeting_daily REQUESTED " in l][0]
        self.assertIn(f"start={p0.DAILY_START}", daily)
        self.assertIn("unit=DAILY", daily)

    def test_none_of_it_reaches_stdout(self):
        """stdout is the narrative log here and the envelope where appctl
        reaches. Evidence goes to stderr or it breaks a contract."""
        cap = _Capture()
        cap.run(p0.ensure_report_jobs, _FakeClient(), None)
        self.assertNotIn("[window]", cap.out.getvalue())

    def test_a_resumed_report_is_recorded_too(self):
        """Its start is not stored locally, so the line says which end it is
        resuming and Amazon's echo supplies the rest."""
        job = ("campaigns", "old-rid", "PENDING", "2026-08-23", p0.END, 0)
        p0.db = _fake_db({"campaigns": job})
        cap = _Capture()
        cap.run(p0.ensure_report_jobs, _FakeClient(), None)
        line = [l for l in cap.window_lines() if " campaigns RESUMED " in l][0]
        self.assertIn(f"end={p0.END}", line)
        self.assertIn("report=old-rid", line)


class WhatAmazonReturnedIsRecordedBesideWhatWasAsked(unittest.TestCase):

    def setUp(self):
        self._db, p0.db = p0.db, _fake_db()
        self.addCleanup(setattr, p0, "db", self._db)
        self._storers = dict(p0.STORERS)
        p0.STORERS = {k: (lambda conn, rows, date: len(rows)) for k in p0.STORERS}
        self.addCleanup(setattr, p0, "STORERS", self._storers)
        self._sleep, p0.time.sleep = p0.time.sleep, lambda s: None
        self.addCleanup(setattr, p0.time, "sleep", self._sleep)

    def _completed(self, rows, start="2026-07-25", end="2026-08-23"):
        return {"status": "COMPLETED", "url": "https://example/report.gz",
                "startDate": start, "endDate": end, "_rows": rows}

    def test_the_echoed_window_is_recorded(self):
        rows = [{"campaignId": "1", "cost": 1.5}, {"campaignId": "2", "cost": 2.25}]
        client = _FakeClient(market="ES", info=self._completed(rows))
        cap = _Capture()
        cap.run(p0.poll_and_store, client, None, {"campaigns": "rid-1"})
        line = [l for l in cap.window_lines() if " campaigns RETURNED " in l][0]
        self.assertIn("start=2026-07-25", line)
        self.assertIn("end=2026-08-23", line)
        self.assertIn("days=30", line)

    def test_a_wider_window_than_asked_for_shows_up_as_a_wider_span(self):
        """The whole point. If Amazon builds 07-23 → 08-22 for a request of
        07-24 → 08-22, this line says days=31 and the question is answered."""
        client = _FakeClient(info=self._completed([], start="2026-07-23",
                                                  end="2026-08-22"))
        cap = _Capture()
        cap.run(p0.poll_and_store, client, None, {"campaigns": "rid-1"})
        self.assertIn("days=31", cap.window_lines()[0])

    def test_the_date_the_snapshot_is_filed_under_is_recorded(self):
        """The snapshot is stored under END, not under the report's own end."""
        client = _FakeClient(info=self._completed([]))
        cap = _Capture()
        cap.run(p0.poll_and_store, client, None, {"campaigns": "rid-1"})
        self.assertIn(f"stored_as={p0.END}", cap.window_lines()[0])

    def test_the_rows_own_spend_is_recorded(self):
        """The number the open doc's table compares against campaign_daily."""
        rows = [{"cost": 1.5}, {"cost": 2.25}, {"cost": None}]
        client = _FakeClient(info=self._completed(rows))
        cap = _Capture()
        cap.run(p0.poll_and_store, client, None, {"campaigns": "rid-1"})
        line = cap.window_lines()[0]
        self.assertIn("rows=3", line)
        self.assertIn("cost=3.75", line)

    def test_a_daily_report_records_which_days_actually_came_back(self):
        rows = [{"date": "2026-08-20", "cost": 1}, {"date": "2026-08-22", "cost": 1},
                {"date": "2026-08-20", "cost": 1}]
        client = _FakeClient(info=self._completed(rows))
        cap = _Capture()
        cap.run(p0.poll_and_store, client, None, {"targeting_daily": "rid-5"})
        line = cap.window_lines()[0]
        self.assertIn("row_days=2", line)
        self.assertIn("row_first=2026-08-20", line)
        self.assertIn("row_last=2026-08-22", line)

    def test_a_summary_report_claims_no_days_it_cannot_see(self):
        """SUMMARY rows carry no date column, so there is nothing to report."""
        client = _FakeClient(info=self._completed([{"cost": 1}]))
        cap = _Capture()
        cap.run(p0.poll_and_store, client, None, {"campaigns": "rid-1"})
        self.assertNotIn("row_days=", cap.window_lines()[0])

    def test_a_missing_echo_is_left_blank_rather_than_invented(self):
        info = {"status": "COMPLETED", "url": "u", "_rows": []}
        client = _FakeClient(info=info)
        cap = _Capture()
        cap.run(p0.poll_and_store, client, None, {"campaigns": "rid-1"})
        line = cap.window_lines()[0]
        self.assertNotIn("start=", line)
        self.assertIn(f"stored_as={p0.END}", line)

    def test_none_of_it_reaches_stdout(self):
        client = _FakeClient(info=self._completed([]))
        cap = _Capture()
        cap.run(p0.poll_and_store, client, None, {"campaigns": "rid-1"})
        self.assertNotIn("[window]", cap.out.getvalue())

    def test_metadata_that_is_not_an_object_is_a_status_error_not_a_crash(self):
        """Reading the reply is part of the status check. Left outside the try,
        a 200 that decodes to null or a list would abort the whole pull where
        the old code logged it and kept polling the other four reports."""
        class _Malformed(_FakeClient):
            def get_report_info(self, report_id):
                return ["not", "an", "object"]

        cap = _Capture()
        pending, failed = cap.run(p0.poll_and_store, _Malformed(), None,
                                  {"campaigns": "rid-1"}, max_wait=0)
        self.assertIn("campaigns", pending, "the report was abandoned")
        self.assertEqual([], failed)
        self.assertIn("status check error", cap.out.getvalue())


class TheExistingCallersAreUnchanged(unittest.TestCase):
    """`get_report` still answers (status, url); four other modules call it."""

    def test_get_report_still_returns_the_pair(self):
        import ads_client
        c = ads_client.AdsClient.__new__(ads_client.AdsClient)
        c.get_report_info = lambda rid: {"status": "COMPLETED", "url": "u",
                                         "startDate": "2026-08-01"}
        self.assertEqual(("COMPLETED", "u"), ads_client.AdsClient.get_report(c, "r"))

    def test_it_reads_the_same_call_rather_than_making_a_second_one(self):
        import inspect
        import ads_client
        src = inspect.getsource(ads_client.AdsClient.get_report)
        self.assertIn("self.get_report_info", src)
        self.assertNotIn("requests.get", src)


if __name__ == "__main__":
    unittest.main()
