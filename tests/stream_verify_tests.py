#!/usr/bin/env python3
"""Does Stream actually deliver the whole day?

Run from the Ads folder:  python3 -m unittest tests.stream_verify_tests -v
No AWS, no Amazon API, no production database — temp files only.

Every other test on this pipeline proves it reads faithfully what ARRIVED.
None of them can prove Amazon SENT everything, and that is the failure that
hides: the totals stay internally consistent, the queues stay empty, the drain
log stays green, and the number is simply low. That is exactly how the first
live day went wrong.

So these tests do the opposite of the usual thing. They PLANT a gap and check
the detector fires, and they plant a day that is not comparable and check the
detector refuses instead of crying wolf.
"""

import datetime
import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _no_operator_data  # noqa: F401,E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))

import stream_map      # noqa: E402
import stream_store    # noqa: E402
import stream_verify   # noqa: E402

DAY = "2026-08-25"
OFFSET = "-07:00"


class VerifyCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="streamverify")
        self.stream_path = os.path.join(self.dir, "stream_data.sqlite")
        self._real_db_path = stream_store.db_path
        stream_store.db_path = lambda: self.stream_path
        self._real_market_path = stream_map.market_db_path
        stream_map.market_db_path = lambda m: os.path.join(self.dir, f"ads_data_{m}.sqlite")
        self._delivery = 0
        self.addCleanup(self._restore)

    def _restore(self):
        stream_store.db_path = self._real_db_path
        stream_map.market_db_path = self._real_market_path
        for name in os.listdir(self.dir):
            os.unlink(os.path.join(self.dir, name))
        os.rmdir(self.dir)

    # -- fixtures -------------------------------------------------------------

    def market_db(self, market, campaigns, daily=()):
        """campaigns: [(id, name)].  daily: [(date, id, name, cost, impr, clicks)]"""
        path = stream_map.market_db_path(market)
        c = sqlite3.connect(path)
        c.execute("CREATE TABLE campaigns (campaign_id TEXT, name TEXT)")
        c.executemany("INSERT INTO campaigns VALUES (?,?)", campaigns)
        c.execute("""CREATE TABLE campaign_daily (
            date TEXT, campaign_id TEXT, campaign_name TEXT, cost REAL,
            sales REAL, orders INTEGER, impressions INTEGER, clicks INTEGER,
            units INTEGER, pulled_at TEXT, PRIMARY KEY (date, campaign_id))""")
        for date, cid, name, cost, impr, clicks in daily:
            c.execute("INSERT INTO campaign_daily (date,campaign_id,campaign_name,"
                      "cost,sales,orders,impressions,clicks,units) "
                      "VALUES (?,?,?,?,0,0,?,?,0)", (date, cid, name, cost, impr, clicks))
        c.commit()
        c.close()

    def bank(self, rows, dataset="sp-traffic", received_at="2026-01-01T00:00:00"):
        self._delivery += 1
        conn = stream_store.connect()
        banked = [{"message_id": f"{dataset}-d{self._delivery}-{i}",
                   "dataset": dataset, "realm": "NA", "topic_arn": "t",
                   "published_at": p.get("time_window_start"),
                   "time_window_start": p.get("time_window_start"),
                   "profile_id": "p", "payload": json.dumps(p)}
                  for i, p in enumerate(rows)]
        stream_store.store_messages(conn, banked)
        conn.execute("UPDATE stream_message SET received_at=? WHERE message_id IN (%s)"
                     % ",".join("?" * len(banked)),
                     [received_at] + [b["message_id"] for b in banked])
        conn.commit()
        conn.close()

    @staticmethod
    def local_naive(instant):
        """One instant, written the way `received_at` is written: naive LOCAL
        time. Computed rather than hard-coded, so the test means the same thing
        on a machine in any timezone."""
        aware = datetime.datetime.fromisoformat(instant)
        return aware.astimezone().replace(tzinfo=None).isoformat()

    def payload(self, hour, campaign="C1", cost=1.0, impressions=100, clicks=2, day=DAY):
        return {"advertiser_id": "ENTITY_US", "marketplace_id": "ATVPDKIKX0DER",
                "campaign_id": campaign, "ad_group_id": "G1", "ad_id": "A1",
                "keyword_id": "K1", "keyword_text": "cats", "match_type": "BROAD",
                "placement": "Other on-Amazon", "currency": "USD",
                "impressions": impressions, "clicks": clicks, "cost": cost,
                "idempotency_id": f"{day}-{hour}-{campaign}-{cost}-{impressions}",
                "time_window_start": f"{day}T{hour:02d}:00:00{OFFSET}"}

    def whole_day(self, campaigns=("C1",), cost=1.0, hours=range(24), day=DAY,
                  received_at="2026-01-01T00:00:00"):
        """Every hour of a day, so coverage reports the day as complete.

        `received_at` defaults to long before the day, which is what makes the
        hours read as WHOLE. Pass a time inside the day to model subscribing
        partway through it.
        """
        rows = [self.payload(h, campaign=c, cost=cost, day=day)
                for h in hours for c in campaigns]
        self.bank(rows, received_at=received_at)


class ARealGapIsCaught(VerifyCase):
    """The tests that earn this module its place."""

    def test_a_matching_day_is_reported_as_a_match(self):
        self.market_db("US", [("C1", "US Tees")],
                       daily=[(DAY, "C1", "US Tees", 24.0, 2400, 48)])
        self.whole_day()
        got = stream_verify.verify("US", DAY)
        self.assertTrue(got["comparable"], got.get("reason"))
        self.assertEqual(got["stream"]["cost"], 24.0)
        self.assertEqual(got["report"]["cost"], 24.0)
        self.assertIn("MATCH", got["verdict"])

    def test_a_campaign_missing_from_stream_is_named(self):
        """The failure mode that matters: Stream quietly carries fewer
        campaigns than the account runs, and every total stays self-consistent."""
        self.market_db("US", [("C1", "US Tees"), ("C2", "US Hoodies")],
                       daily=[(DAY, "C1", "US Tees", 24.0, 2400, 48),
                              (DAY, "C2", "US Hoodies", 24.0, 2400, 48)])
        self.whole_day(campaigns=("C1",))          # C2 never arrives
        got = stream_verify.verify("US", DAY)
        self.assertTrue(got["comparable"], got.get("reason"))
        self.assertIn("MISMATCH", got["verdict"])
        missing = [c for c in got["campaigns"] if not c["in_stream"]]
        self.assertEqual([c["campaign_id"] for c in missing], ["C2"])
        self.assertEqual(got["delta"]["cost"]["ratio"], 0.5)

    def test_a_shortfall_inside_one_campaign_is_caught(self):
        """No campaign is missing; each one is simply short. A per-campaign
        list would look complete, so the ratio has to carry it."""
        self.market_db("US", [("C1", "US Tees")],
                       daily=[(DAY, "C1", "US Tees", 48.0, 4800, 96)])
        self.whole_day()                            # half the real spend
        got = stream_verify.verify("US", DAY)
        self.assertIn("MISMATCH", got["verdict"])
        self.assertEqual(got["delta"]["cost"]["ratio"], 0.5)
        self.assertEqual(got["delta"]["cost"]["diff"], -24.0)

    def test_a_small_difference_is_not_called_a_mismatch(self):
        """Two Amazon pipelines will not agree to the cent. Crying wolf at 0.5%
        would teach the reader to ignore the check."""
        self.market_db("US", [("C1", "US Tees")],
                       daily=[(DAY, "C1", "US Tees", 24.12, 2400, 48)])
        self.whole_day()
        got = stream_verify.verify("US", DAY)
        self.assertIn("MATCH", got["verdict"])
        self.assertNotIn("MISMATCH", got["verdict"])

    def test_a_campaign_only_stream_saw_is_reported(self):
        self.market_db("US", [("C1", "US Tees"), ("C2", "US Hoodies")],
                       daily=[(DAY, "C1", "US Tees", 24.0, 2400, 48)])
        self.whole_day(campaigns=("C1", "C2"))
        got = stream_verify.verify("US", DAY)
        self.assertEqual(got["only_in_stream"], ["C2"])


class ItRefusesRatherThanCryingWolf(VerifyCase):
    """A day that is EXPECTED to read low must never be called a discrepancy."""

    def test_a_day_with_a_missing_hour_is_not_compared(self):
        self.market_db("US", [("C1", "US Tees")],
                       daily=[(DAY, "C1", "US Tees", 24.0, 2400, 48)])
        self.whole_day(hours=[h for h in range(24) if h != 13])
        got = stream_verify.verify("US", DAY)
        self.assertFalse(got["comparable"])
        self.assertIn("never arrived", got["reason"])

    def test_a_day_that_began_before_we_listened_is_not_compared(self):
        """The first live day. Every hour is here, and none of them is whole."""
        self.market_db("US", [("C1", "US Tees")],
                       daily=[(DAY, "C1", "US Tees", 24.0, 2400, 48)])
        self.whole_day()
        conn = stream_store.connect()
        conn.execute("UPDATE stream_message SET received_at=?",
                     (f"{DAY}T23:30:00",))
        conn.commit()
        conn.close()
        got = stream_verify.verify("US", DAY)
        self.assertFalse(got["comparable"])
        self.assertIn("before Stream was switched on", got["reason"])

    def test_a_day_the_report_has_not_banked_is_not_a_stream_failure(self):
        self.market_db("US", [("C1", "US Tees")], daily=[])
        self.whole_day()
        got = stream_verify.verify("US", DAY)
        self.assertFalse(got["comparable"])
        self.assertIn("has not banked", got["reason"])

    def test_a_part_day_is_not_compared_against_a_whole_day_report(self):
        self.market_db("US", [("C1", "US Tees")],
                       daily=[(DAY, "C1", "US Tees", 24.0, 2400, 48)])
        self.whole_day(hours=range(0, 12))
        got = stream_verify.verify("US", DAY)
        self.assertFalse(got["comparable"])

    def test_no_stream_database_says_so(self):
        got = stream_verify.verify("US", DAY)
        self.assertFalse(got["comparable"])
        self.assertIn("never run", got["reason"])

    def test_a_market_with_nothing_banked_is_named(self):
        self.market_db("US", [("C1", "US Tees")])
        self.whole_day()
        got = stream_verify.verify("UK", DAY)
        self.assertFalse(got["comparable"])
        self.assertIn("Nothing banked", got["reason"])


class DayPicking(VerifyCase):
    def test_it_picks_the_newest_WHOLE_day_not_the_newest_day(self):
        """Today is always partial. Defaulting to it would refuse forever."""
        self.market_db("US", [("C1", "US Tees")],
                       daily=[("2026-08-24", "C1", "US Tees", 24.0, 2400, 48)])
        self.whole_day(day="2026-08-24")
        self.whole_day(day="2026-08-25", hours=range(0, 6))   # today, partial
        got = stream_verify.verify("US")
        self.assertEqual(got["day"], "2026-08-24")
        self.assertTrue(got["comparable"], got.get("reason"))

    def test_24_hours_is_not_the_same_as_24_WHOLE_hours(self):
        """Caught by mutation testing, not by thought.

        The first version of this class only proved that a SHORT day is
        skipped. Deleting the completeness check from the day picker still
        passed every test, because the only partial day in the fixtures also
        happened to be short.

        The case that needs it is subscribing just after midnight: the day then
        has all 24 hours present and hour 00 holds only the fragment Amazon's
        catch-up included. By hour count it is indistinguishable from a whole
        day, and picking it would hand the operator a MISMATCH verdict for a
        day that was never comparable.
        """
        self.market_db("US", [("C1", "US Tees")],
                       daily=[(DAY, "C1", "US Tees", 24.0, 2400, 48)])
        self.whole_day(received_at=self.local_naive(f"{DAY}T00:30:00{OFFSET}"))
        conn = stream_store.connect(ro=True)
        try:
            self.assertEqual(
                len(stream_map.hours_for(conn, "US",
                                         stream_map.advertiser_map(conn), DAY)),
                24, "fixture must present all 24 hours, or it proves nothing")
        finally:
            conn.close()
        got = stream_verify.verify("US")
        self.assertIsNone(got["day"])
        self.assertIn("No day has been delivered whole", got["reason"])

    def test_with_no_whole_day_at_all_it_says_so_plainly(self):
        self.market_db("US", [("C1", "US Tees")])
        self.whole_day(hours=range(0, 6))
        got = stream_verify.verify("US")
        self.assertIsNone(got["day"])
        self.assertIn("No day has been delivered whole", got["reason"])


class TheAlertFires(unittest.TestCase):
    """A check nobody runs is not a check.

    The verifier only helps if it speaks by itself. These pin that the alert
    fires on a real mismatch, stays quiet on a match, and — the part that
    matters most — stays quiet on every day it cannot judge, because an alarm
    that cries wolf on the first day of every subscription gets muted and then
    the real one is missed too.
    """

    def setUp(self):
        sys.path.insert(0, os.path.join(HERE, "engine"))
        import appctl
        self.appctl = appctl
        import stream_verify as sv
        self._real = sv.verify
        self.addCleanup(setattr, sv, "verify", self._real)
        self.sv = sv

    def _alerts(self, reply):
        self.sv.verify = lambda market=None, day=None: reply
        return self.appctl._stream_undercount_alerts("US")

    def test_a_mismatch_raises_one_alert(self):
        got = self._alerts({"comparable": True, "day": "2026-08-25",
                            "verdict": "MISMATCH. Stream saw 50.0% of the spend."})
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["kind"], "stream_undercount")
        self.assertIn("2026-08-25", got[0]["key"])
        self.assertIn("50.0%", got[0]["message"])

    def test_a_match_is_silent(self):
        self.assertEqual(self._alerts(
            {"comparable": True, "day": "2026-08-25",
             "verdict": "MATCH. Stream saw 99.8% of the spend."}), [])

    def test_a_day_that_cannot_be_judged_is_silent(self):
        """The first day of any subscription lands here. An alarm then would be
        wrong every single time, and a wrong alarm gets muted."""
        self.assertEqual(self._alerts(
            {"comparable": False, "day": "2026-08-25",
             "reason": "not whole", "verdict": None}), [])

    def test_the_key_carries_the_day_so_it_fires_once(self):
        a = self._alerts({"comparable": True, "day": "2026-08-25",
                          "verdict": "MISMATCH."})[0]
        b = self._alerts({"comparable": True, "day": "2026-08-26",
                          "verdict": "MISMATCH."})[0]
        self.assertNotEqual(a["key"], b["key"])

    def test_a_broken_verifier_never_breaks_the_alert_feed(self):
        """Alerts drive the app's notifications. One raising module must not
        take the whole feed down with it."""
        def boom(market=None, day=None):
            raise RuntimeError("stream database is a directory today")
        self.sv.verify = boom
        got = self.appctl._stream_undercount_alerts("US")
        self.assertIsInstance(got, list, "the feed itself must survive")

    def test_a_broken_verifier_SAYS_SO_rather_than_going_quiet(self):
        """This used to return [] and that was the bug.

        stream-verify is the ONLY check that can see Stream dropping data. A
        bug inside it — a renamed column, a schema change — would have switched
        the detector off for good, and the alerts feed would have stayed clean,
        which is exactly what it looks like when everything is fine. The
        watcher for a silent failure cannot be allowed to fail silently.

        A market with no Stream data does not come through here: verify()
        returns comparable:false with a reason for that (checked live against
        UK, DE and USKDP on 2026-08-22), so an exception is a real fault.
        """
        def boom(market=None, day=None):
            raise RuntimeError("no such column: idempotency_id")
        self.sv.verify = boom
        got = self.appctl._stream_undercount_alerts("US")
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["kind"], "stream_check_failed")
        self.assertIn("no such column", got[0]["message"])
        self.assertIn("RuntimeError", got[0]["key"],
                      "the key must carry the fault type so a persistent "
                      "fault alerts once, not on every poll")

    def test_two_different_faults_are_two_different_alerts(self):
        def boom_a(market=None, day=None):
            raise RuntimeError("a")
        def boom_b(market=None, day=None):
            raise ValueError("b")
        self.sv.verify = boom_a
        a = self.appctl._stream_undercount_alerts("US")[0]["key"]
        self.sv.verify = boom_b
        b = self.appctl._stream_undercount_alerts("US")[0]["key"]
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()
