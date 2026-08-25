#!/usr/bin/env python3
"""Marketing Stream -> per-market picture of today.

Run from the Ads folder:  python3 -m unittest tests.stream_map_tests -v
No AWS, no Amazon API, no production database — temp files only.

The two things worth guarding here are both about telling the truth:
a message must find the RIGHT advertising account, and a day with holes in it
must not be presented as a day.
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

import stream_map     # noqa: E402
import stream_store   # noqa: E402


class StreamMapCase(unittest.TestCase):
    """A temp stream database plus temp market databases, wired in by patch."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="streammap")
        self.stream_path = os.path.join(self.dir, "stream_data.sqlite")
        self._real_db_path = stream_store.db_path
        stream_store.db_path = lambda: self.stream_path
        self._real_market_path = stream_map.market_db_path
        stream_map.market_db_path = lambda m: os.path.join(self.dir, f"ads_data_{m}.sqlite")
        self.addCleanup(self._restore)

    def _restore(self):
        stream_store.db_path = self._real_db_path
        stream_map.market_db_path = self._real_market_path
        for name in os.listdir(self.dir):
            os.unlink(os.path.join(self.dir, name))
        os.rmdir(self.dir)

    def market_db(self, market, campaigns):
        """campaigns: [(campaign_id, name)]"""
        path = stream_map.market_db_path(market)
        c = sqlite3.connect(path)
        c.execute("CREATE TABLE campaigns (campaign_id TEXT, name TEXT)")
        c.executemany("INSERT INTO campaigns VALUES (?,?)", campaigns)
        c.commit()
        c.close()

    def bank(self, rows, dataset="sp-traffic", received_at="2026-01-01T00:00:00"):
        """rows: payload dicts.

        Each call is its own delivery, so its message ids must be unique — a
        restatement arrives as a NEW SNS message carrying the same
        idempotency_id, and reusing the id here would just hit the message-id
        primary key and test nothing.

        `received_at` defaults to long before any day under test, so hours read
        as WHOLE unless a test says otherwise. Left at the real clock, every
        banked hour would be flagged partial — correct behaviour for a
        subscription created moments ago, and useless as a fixture.
        """
        self._delivery = getattr(self, "_delivery", 0) + 1
        conn = stream_store.connect()
        banked = []
        for i, payload in enumerate(rows):
            banked.append({
                "message_id": f"{dataset}-d{self._delivery}-{i}",
                "dataset": dataset, "realm": "NA", "topic_arn": "t",
                "published_at": payload.get("time_window_start"),
                "time_window_start": payload.get("time_window_start"),
                "profile_id": "p", "payload": json.dumps(payload)})
        stream_store.store_messages(conn, banked)
        if received_at:
            conn.execute("UPDATE stream_message SET received_at=? WHERE message_id IN "
                         "(%s)" % ",".join("?" * len(banked)),
                         [received_at] + [b["message_id"] for b in banked])
        conn.commit()
        conn.close()

    def payload(self, **kw):
        base = {"advertiser_id": "ENTITY_US", "marketplace_id": "ATVPDKIKX0DER",
                "campaign_id": "C1", "ad_group_id": "G1", "ad_id": "A1",
                "keyword_id": "K1", "keyword_text": "cats", "match_type": "BROAD",
                "placement": "Other on-Amazon", "currency": "USD",
                "impressions": 10, "clicks": 1, "cost": 0.5,
                "time_window_start": "2026-08-21T07:00:00-07:00",
                "idempotency_id": "i1"}
        base.update(kw)
        return base

    def conversion(self, **kw):
        """A real sp-conversion shape: every attribution window side by side.

        The values differ per window on purpose, so a test that reads the wrong
        one fails loudly instead of coincidentally matching.
        """
        base = {"advertiser_id": "ENTITY_US", "marketplace_id": "ATVPDKIKX0DER",
                "campaign_id": "C1", "ad_group_id": "G1", "ad_id": "A1",
                "keyword_id": "K1", "placement": "Detail Page on-Amazon",
                "currency": "USD",
                "time_window_start": "2026-08-21T08:00:00-07:00",
                "idempotency_id": "c1",
                "sales_1d": 5.0, "purchases_1d": 1, "units_sold_1d": 1,
                "sales_7d": 10.0, "purchases_7d": 2, "units_sold_7d": 2,
                "sales_14d": 15.0, "purchases_14d": 3, "units_sold_14d": 3,
                "sales_30d": 19.99, "purchases_30d": 4, "units_sold_30d": 4}
        base.update(kw)
        return base


class MarketResolution(StreamMapCase):
    """Merch US and KDP US share marketplace ATVPDKIKX0DER — confirmed against
    the profiles endpoint. So the marketplace id cannot say which account a
    message belongs to, and resolution goes through the campaign ids instead."""

    def test_the_match_threshold_actually_bites(self):
        """`MIN_CAMPAIGN_MATCHES = 1` has to MEAN something.

        Mutation testing flipped it to 0 and not one test noticed, because
        `_market_holding` drops markets that hold nothing, so `best` is never
        below 1 and the two values behave identically. The constant reads like
        a guard, so it must actually guard: raising it has to refuse an
        advertiser whose evidence is that thin, rather than sit there as
        decoration that someone later trusts.
        """
        self.market_db("US", [("C1", "US Tees"), ("C2", "US Hoodies")])
        self.bank([self.payload(campaign_id="C1")])
        real = stream_map.MIN_CAMPAIGN_MATCHES
        try:
            stream_map.MIN_CAMPAIGN_MATCHES = 2
            thin = stream_map.learn_advertisers(stream_store.connect(ro=True))
            entry = thin["ENTITY_US"]
            self.assertIsNone(entry["market"])
            self.assertIn("too few", entry["reason"])
        finally:
            stream_map.MIN_CAMPAIGN_MATCHES = real
        # …and at the shipped value, one match is enough.
        ok = stream_map.learn_advertisers(stream_store.connect(ro=True))
        self.assertEqual(ok["ENTITY_US"]["market"], "US")

    def test_same_marketplace_two_accounts_are_kept_apart(self):
        self.market_db("US", [("C1", "SCAVENGER - US Tees 1")])
        self.market_db("USKDP", [("K9", "Book - Exact")])
        self.bank([
            self.payload(advertiser_id="ENTITY_MERCH", campaign_id="C1", idempotency_id="a"),
            self.payload(advertiser_id="ENTITY_KDP", campaign_id="K9", idempotency_id="b"),
        ])
        conn = stream_store.connect()
        resolved = stream_map.learn_advertisers(conn)
        conn.close()
        self.assertEqual(resolved["ENTITY_MERCH"]["market"], "US")
        self.assertEqual(resolved["ENTITY_KDP"]["market"], "USKDP")

    def test_an_advertiser_no_database_claims_is_reported_not_guessed(self):
        self.market_db("US", [("C1", "Something else")])
        self.bank([self.payload(advertiser_id="ENTITY_NEW", campaign_id="UNKNOWN")])
        conn = stream_store.connect()
        resolved = stream_map.learn_advertisers(conn)
        conn.close()
        entry = resolved["ENTITY_NEW"]
        self.assertIsNone(entry["market"])
        self.assertIn("are in any market database", entry["reason"])

    def test_a_contested_advertiser_is_refused_rather_than_guessed(self):
        """Two databases holding the same campaign id is a bug. Picking one at
        random would put a market's spend under another market's name."""
        self.market_db("US", [("C1", "US copy")])
        self.market_db("UK", [("C1", "UK copy")])
        self.bank([self.payload(advertiser_id="ENTITY_X", campaign_id="C1")])
        conn = stream_store.connect()
        resolved = stream_map.learn_advertisers(conn)
        conn.close()
        self.assertIsNone(resolved["ENTITY_X"]["market"])
        self.assertIn("more than one market", resolved["ENTITY_X"]["reason"])

    def test_the_resolved_map_is_cached_and_reused(self):
        self.market_db("US", [("C1", "US Tees")])
        self.bank([self.payload(advertiser_id="ENTITY_US")])
        conn = stream_store.connect()
        stream_map.advertiser_map(conn)
        cached = stream_map.cached_advertisers(conn)
        conn.close()
        self.assertEqual(cached["ENTITY_US"]["market"], "US")

    def test_unknown_advertisers_are_not_cached_so_they_get_retried(self):
        """A campaign created this morning is not in the nightly-pulled table
        yet. Caching "unknown" would freeze that answer for good."""
        self.bank([self.payload(advertiser_id="ENTITY_NEW", campaign_id="NOPE")])
        conn = stream_store.connect()
        stream_map.advertiser_map(conn)
        self.assertEqual(stream_map.cached_advertisers(conn), {})
        conn.close()


class DayBoundary(StreamMapCase):
    """The advertising day is Amazon's, read out of the message's own offset."""

    def test_day_and_hour_come_from_the_message(self):
        self.assertEqual(stream_map._day_and_hour("2026-08-21T07:00:00-07:00"),
                         ("2026-08-21", 7))
        self.assertEqual(stream_map._offset("2026-08-21T07:00:00-07:00"), "-07:00")

    def test_account_today_uses_the_marketplace_offset_not_the_mac(self):
        """At 05:30 UTC the Mac says the 22nd. A US Pacific account is still on
        the 21st, and that is the day Amazon will bill."""
        self.market_db("US", [("C1", "US Tees")])
        self.bank([self.payload()])
        conn = stream_store.connect()
        advertisers = stream_map.advertiser_map(conn)
        now = datetime.datetime(2026, 8, 22, 5, 30, tzinfo=datetime.timezone.utc)
        day, offset = stream_map.account_today(conn, "US", advertisers, now=now)
        conn.close()
        self.assertEqual(day, "2026-08-21")
        self.assertEqual(offset, "-07:00")


class Deduplication(StreamMapCase):
    def test_a_restated_hour_replaces_it_rather_than_doubling_it(self):
        """SQS redelivery is handled by the message-id key. This is the other
        case: Amazon restating an hour it already sent. Summing both would
        double that hour's spend."""
        self.market_db("US", [("C1", "US Tees")])
        self.bank([self.payload(idempotency_id="same", cost=1.0, clicks=1)])
        self.bank([self.payload(idempotency_id="same", cost=2.0, clicks=3)])
        conn = stream_store.connect()
        advertisers = stream_map.advertiser_map(conn)
        rows = stream_map.traffic_rows(conn, "US", advertisers, "2026-08-21")
        conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cost"], 2.0)


class Coverage(StreamMapCase):
    """A day with holes sums to an undercount, and nothing else says so."""

    def test_holes_inside_the_delivered_range_are_named(self):
        cov = stream_map._coverage([{"hour": 0}, {"hour": 1}, {"hour": 5}])
        self.assertFalse(cov["complete"])
        self.assertEqual(cov["missing_hours"], [2, 3, 4])
        self.assertIn("UNDERCOUNT", cov["note"])

    def test_the_current_hour_is_never_called_missing(self):
        """Stream runs about an hour behind. Expecting the clock hour would
        raise a false alarm every hour, all day."""
        cov = stream_map._coverage([{"hour": 0}, {"hour": 1}, {"hour": 2}])
        self.assertTrue(cov["complete"])
        self.assertEqual(cov["missing_hours"], [])
        self.assertIsNone(cov["note"])

    def test_no_hours_at_all_is_not_reported_as_complete(self):
        self.assertFalse(stream_map._coverage([])["complete"])

    def test_an_hour_that_began_before_we_listened_is_partial_not_delivered(self):
        """The bug the operator found by eye on day one.

        Amazon says the account spent $7.40 today. Stream said $2.49, and the
        panel called it "5 hours never delivered" — which read as a small,
        explained shortfall. It was not. The subscription was created at 09:07
        that morning, so ten of the eleven hours on the panel held nothing but
        whatever Amazon's short catch-up happened to include. An hour that is
        here but cannot be whole is a third state, and collapsing it into
        "delivered" is what made a two-thirds shortfall look minor.
        """
        since = datetime.datetime.fromisoformat("2026-08-21T09:07:00-07:00")
        cov = stream_map._coverage(
            [{"hour": h} for h in (7, 8, 9, 10)],
            day="2026-08-21", offset="-07:00", since=since)
        # Every hour that began before 09:07 is pre-subscription, whether or
        # not the catch-up carried anything for it. Only hour 10 is whole.
        self.assertEqual(cov["partial_hours"], [0, 1, 2, 3, 4, 5, 6, 7, 8, 9])
        self.assertEqual(cov["missing_hours"], [])
        self.assertFalse(cov["complete"])
        self.assertIn("began before Stream was switched on", cov["note"])

    def test_an_hour_that_began_after_we_listened_is_whole(self):
        since = datetime.datetime.fromisoformat("2026-08-21T09:07:00-07:00")
        cov = stream_map._coverage(
            [{"hour": 10}, {"hour": 11}],
            day="2026-08-21", offset="-07:00", since=since)
        # 10 and 11 both start after 09:07, so neither is a fragment. The
        # earlier hours of the day are pre-subscription and reported as such.
        self.assertNotIn(10, cov["partial_hours"])
        self.assertNotIn(11, cov["partial_hours"])
        self.assertEqual(cov["missing_hours"], [])

    def test_a_settled_day_long_after_subscribing_has_no_partial_hours(self):
        """The flag must not become permanent furniture. Once the subscription
        predates the whole day, every delivered hour is whole again."""
        since = datetime.datetime.fromisoformat("2026-08-21T09:07:00-07:00")
        cov = stream_map._coverage(
            [{"hour": h} for h in range(0, 24)],
            day="2026-08-25", offset="-07:00", since=since)
        self.assertEqual(cov["partial_hours"], [])
        self.assertTrue(cov["complete"])
        self.assertIsNone(cov["note"])

    def test_an_empty_hour_before_we_listened_is_not_called_a_delivery_failure(self):
        """The false alarm this audit found, on the live account.

        The panel said "3 never arrived" for hours 04-06 of the first day. The
        subscription was created at 09:07, so nobody was listening during any
        of them and Amazon dropped nothing. "Never arrived" means data was lost
        and cannot be recovered; that sentence has to stay true, or the operator
        learns to ignore it.
        """
        since = datetime.datetime.fromisoformat("2026-08-21T09:07:00-07:00")
        cov = stream_map._coverage(
            [{"hour": h} for h in (0, 1, 2, 3, 7, 8, 9, 10, 11)],
            day="2026-08-21", offset="-07:00", since=since)
        self.assertEqual(cov["missing_hours"], [])
        self.assertIn(4, cov["partial_hours"])
        self.assertIn(5, cov["partial_hours"])
        self.assertIn(6, cov["partial_hours"])
        self.assertNotIn("never arrived", cov["note"])

    def test_a_hole_after_we_started_listening_is_still_missing(self):
        """The fix must not swallow a REAL gap. An hour we were listening for
        that never came is still gone for good."""
        since = datetime.datetime.fromisoformat("2026-08-21T00:00:00-07:00")
        cov = stream_map._coverage(
            [{"hour": h} for h in (0, 1, 3)],
            day="2026-08-21", offset="-07:00", since=since)
        self.assertEqual(cov["missing_hours"], [2])
        self.assertEqual(cov["partial_hours"], [])
        self.assertIn("never delivered", cov["note"])

    def test_partial_detection_is_skipped_when_nothing_has_arrived(self):
        cov = stream_map._coverage([{"hour": 3}], day="2026-08-21",
                                   offset="-07:00", since=None)
        self.assertEqual(cov["partial_hours"], [])

    def test_an_undrained_queue_stops_the_day_reading_complete(self):
        """The failure the hour counts cannot see, found on the live Dashboard.

        Every hour was delivered and the panel said so, while 958 messages sat
        undrained in SQS and the count was still growing. Those messages belong
        to hours that already read as delivered, so nothing in the hour counts
        can ever notice them. Completeness has to be a claim about the pipeline.
        """
        cov = stream_map._coverage([{"hour": h} for h in (0, 1, 2)],
                                   backlog=["NA/sp-traffic"])
        self.assertEqual(cov["missing_hours"], [])
        self.assertEqual(cov["partial_hours"], [])
        self.assertEqual(cov["backlog_pending"], ["NA/sp-traffic"])
        self.assertFalse(cov["complete"])
        self.assertIn("UNDERCOUNT", cov["note"])

    def test_a_drained_queue_leaves_the_day_alone(self):
        cov = stream_map._coverage([{"hour": h} for h in (0, 1, 2)], backlog=None)
        self.assertIsNone(cov["backlog_pending"])
        self.assertTrue(cov["complete"])
        self.assertIsNone(cov["note"])

    def test_a_backlog_and_a_hole_are_both_named(self):
        """One does not replace the other: an hour that never arrived is gone
        for good, and a queued hour is only late."""
        cov = stream_map._coverage([{"hour": h} for h in (0, 1, 3)],
                                   backlog=["NA/sp-traffic"])
        self.assertEqual(cov["missing_hours"], [2])
        self.assertIn("never delivered", cov["note"])
        self.assertIn("still queued at Amazon", cov["note"])


class SummaryHonesty(StreamMapCase):
    def test_no_stream_database_says_so_instead_of_showing_zeroes(self):
        got = stream_map.summary("US")
        self.assertFalse(got["supported"])
        self.assertIn("never run", got["note"])

    def test_a_market_with_nothing_banked_is_unsupported_not_empty(self):
        """Zero spend and no data are different answers. Only one of them means
        'stop worrying'."""
        self.market_db("US", [("C1", "US Tees")])
        self.bank([self.payload()])
        got = stream_map.summary("UK")
        self.assertFalse(got["supported"])
        self.assertIn("Nothing banked", got["note"])

    def test_sales_and_acos_are_refused_while_conversions_are_empty(self):
        """sp-traffic carries no orders. Showing ACOS from cost alone, or a zero
        for sales, would invent a return on spend."""
        self.market_db("US", [("C1", "SCAVENGER - US Tees 1")])
        self.bank([self.payload()])
        now = datetime.datetime(2026, 8, 21, 18, 0, tzinfo=datetime.timezone.utc)
        got = stream_map.summary("US", now=now)
        self.assertTrue(got["supported"])
        self.assertFalse(got["conversions"]["available"])
        self.assertIn("cannot be shown", got["conversions"]["note"])
        for banned in ("sales", "orders", "acos", "cvr"):
            self.assertNotIn(banned, got["totals"])

    def test_totals_placements_and_campaigns_add_up(self):
        self.market_db("US", [("C1", "SCAVENGER - US Tees 1"), ("C2", "Retro Hoodies")])
        self.bank([
            self.payload(idempotency_id="a", campaign_id="C1", cost=1.0,
                         clicks=2, impressions=100, placement="Top of Search on-Amazon"),
            self.payload(idempotency_id="b", campaign_id="C2", cost=3.0,
                         clicks=1, impressions=50, placement="Detail Page on-Amazon"),
        ])
        now = datetime.datetime(2026, 8, 21, 18, 0, tzinfo=datetime.timezone.utc)
        got = stream_map.summary("US", now=now)
        self.assertEqual(got["totals"]["cost"], 4.0)
        self.assertEqual(got["totals"]["clicks"], 3)
        self.assertEqual(got["totals"]["impressions"], 150)
        self.assertEqual(sum(p["cost"] for p in got["placements"]), 4.0)
        self.assertEqual(sum(c["cost"] for c in got["campaigns"]), 4.0)
        self.assertAlmostEqual(sum(p["share"] for p in got["placements"]), 1.0, places=3)

    def test_campaigns_are_named_from_the_market_database(self):
        self.market_db("US", [("C1", "SCAVENGER - US Tees 1")])
        self.bank([self.payload(campaign_id="C1")])
        now = datetime.datetime(2026, 8, 21, 18, 0, tzinfo=datetime.timezone.utc)
        got = stream_map.summary("US", now=now)
        self.assertEqual(got["campaigns"][0]["campaign"], "SCAVENGER - US Tees 1")

    def test_a_campaign_too_new_to_be_pulled_keeps_its_id_as_a_label(self):
        """Created this morning, not in the nightly-pulled table. Its spend is
        real and must appear, even without a name."""
        self.market_db("US", [("C1", "Known")])
        self.bank([self.payload(idempotency_id="a", campaign_id="C1"),
                   self.payload(idempotency_id="b", campaign_id="C_NEW", cost=9.0)])
        now = datetime.datetime(2026, 8, 21, 18, 0, tzinfo=datetime.timezone.utc)
        got = stream_map.summary("US", now=now)
        labels = {c["campaign"] for c in got["campaigns"]}
        self.assertIn("C_NEW", labels)
        self.assertEqual(got["totals"]["cost"], 9.5)


class Conversions(StreamMapCase):
    """Sales arrive in a second dataset, dated to the CLICK hour, late, and
    restated. Every one of those facts changes what the panel may claim."""

    def setUp(self):
        super().setUp()
        self.market_db("US", [("C1", "SCAVENGER - US Tees 1")])
        self.now = datetime.datetime(2026, 8, 21, 18, 0, tzinfo=datetime.timezone.utc)

    def test_the_thirty_day_window_is_used_because_the_reports_use_it(self):
        """phase0_pull and daily_metrics read sales30d / purchases30d. Reading a
        different window here would put two numbers in the app that disagree for
        no reason the reader could see."""
        self.bank([self.payload()])
        self.bank([self.conversion()], dataset="sp-conversion")
        got = stream_map.summary("US", now=self.now)
        self.assertEqual(stream_map.ATTRIBUTION, "30d")
        self.assertEqual(got["conversions"]["attribution"], "30d")
        self.assertEqual(got["conversions"]["sales"], 19.99)
        self.assertEqual(got["conversions"]["orders"], 4)
        self.assertEqual(got["conversions"]["units"], 4)

    def test_a_conversion_dated_to_an_earlier_click_is_not_todays_sale(self):
        """A message that arrives tonight carrying a window six days old is
        normal: somebody clicked then and bought now. Counting it as today's
        sale would inflate today and never correct itself."""
        self.bank([self.payload()])
        self.bank([self.conversion(idempotency_id="old",
                                   time_window_start="2026-08-15T03:00:00-07:00",
                                   sales_30d=999.0, purchases_30d=99)],
                  dataset="sp-conversion")
        got = stream_map.summary("US", now=self.now)
        # NULL, not 0. A zero here reads as "sold nothing today", and the day it
        # is describing has simply had no conversion attributed to it yet.
        # `available` is about THIS DAY for the same reason — it used to be true
        # whenever the market had ever received a conversion message, which put
        # sales: 0 on the panel for every day that had none.
        self.assertFalse(got["conversions"]["available"])
        self.assertIsNone(got["conversions"]["sales"])
        self.assertIsNone(got["conversions"]["orders"])
        self.assertEqual(got["conversions"]["rows"], 0)
        # The messages ARE banked, and the reply still says so — that is what
        # separates "cannot see sales yet" from "this day has none yet".
        self.assertEqual(got["conversions"]["messages"], 1)
        # …but it IS that day's sale.
        back = stream_map.summary("US", day="2026-08-15", now=self.now)
        self.assertEqual(back["conversions"]["sales"], 999.0)

    def test_acos_is_withheld_for_a_day_in_progress(self):
        """The spend for an hour is final about an hour later; its sales are
        not. Dividing one by the other is always alarming and always wrong."""
        self.bank([self.payload(time_window_start="2026-08-21T00:00:00-07:00")])
        self.bank([self.conversion(time_window_start="2026-08-21T00:00:00-07:00")],
                  dataset="sp-conversion")
        got = stream_map.summary("US", now=self.now)
        self.assertTrue(got["coverage"]["complete"])
        self.assertIn("still in progress", got["conversions"]["acos_withheld"])
        for banned in ("acos", "roas", "cvr"):
            self.assertNotIn(banned, got["totals"])
            self.assertNotIn(banned, got["conversions"])

    def test_a_day_with_holes_says_the_two_numbers_are_not_comparable(self):
        """Spend and sales are both partial, but in OPPOSITE directions: a
        missing hour of spend is gone for good while sales keep arriving. Side
        by side that reads far better than the day really is, so the refusal has
        to name the holes rather than give the generic reason."""
        self.bank([self.payload(time_window_start="2026-08-21T07:00:00-07:00")])
        self.bank([self.conversion()], dataset="sp-conversion")
        got = stream_map.summary("US", now=self.now)
        self.assertFalse(got["coverage"]["complete"])
        withheld = got["conversions"]["acos_withheld"]
        self.assertIn("missing or incomplete", withheld)
        self.assertIn("not comparable", withheld)

    def test_a_backlogged_queue_does_not_claim_hours_are_missing(self):
        """The refusal has to name the real reason.

        The sentence was chosen on `complete`, so a day whose only problem was
        an undrained queue would have read "0 hours of spend are missing or
        incomplete" — a number the reader can see is nonsense, which teaches
        them to ignore the whole line.
        """
        self.bank([self.payload(time_window_start="2026-08-21T00:00:00-07:00")])
        self.bank([self.conversion(time_window_start="2026-08-21T00:00:00-07:00")],
                  dataset="sp-conversion")
        conn = stream_store.connect()
        stream_store.log_drain(conn, "NA", "sp-traffic", 3000, 3000, 0, 0,
                               note="time budget 300s ran out, queue not empty")
        conn.close()
        got = stream_map.summary("US", now=self.now)
        withheld = got["conversions"]["acos_withheld"]
        self.assertFalse(got["coverage"]["complete"])
        self.assertNotIn("0 hours", withheld)
        self.assertIn("still queued", withheld)

    def test_no_conversion_message_at_all_reports_no_sales_not_zero_sales(self):
        """Zero sales and no visibility of sales are different claims."""
        self.bank([self.payload()])
        got = stream_map.summary("US", now=self.now)
        self.assertFalse(got["conversions"]["available"])
        self.assertIsNone(got["conversions"]["sales"])
        self.assertIsNone(got["conversions"]["acos_withheld"])
        self.assertIn("cannot be shown", got["conversions"]["note"])

    def test_traffic_deltas_that_share_a_grain_are_all_kept(self):
        """sp-traffic messages are DELTAS, not snapshots.

        Read off the wire: `impressions` is 1 or 2 and a correction arrives as
        -1, so many messages share the same hour, ad, keyword and placement on
        purpose. Collapsing traffic on that shape — which is what conversions
        correctly do — would throw most of an hour away and produce exactly the
        quiet undercount this whole pipeline is built to make impossible.
        """
        same = dict(time_window_start="2026-08-21T08:00:00-07:00",
                    ad_id="A1", keyword_id="K1", placement="Detail Page on-Amazon")
        for n in range(4):
            self.bank([self.payload(idempotency_id=f"delta-{n}", impressions=1,
                                    clicks=0, cost=0.0, **same)])
        got = stream_map.summary("US", now=self.now)
        self.assertEqual(got["totals"]["impressions"], 4)

    def test_a_traffic_message_with_no_id_is_kept_and_counted(self):
        """Never silently collapse a delta. An overcount shows up the moment
        stream-verify compares a day; an undercount shows up nowhere."""
        same = dict(time_window_start="2026-08-21T08:00:00-07:00",
                    ad_id="A1", keyword_id="K1", placement="Detail Page on-Amazon")
        self.bank([self.payload(idempotency_id=None, impressions=1, clicks=0,
                                cost=0.0, **same)])
        self.bank([self.payload(idempotency_id=None, impressions=1, clicks=0,
                                cost=0.0, **same)])
        got = stream_map.summary("US", now=self.now)
        self.assertEqual(got["totals"]["impressions"], 2)
        self.assertEqual(got["unkeyed_messages"], 2)

    def test_a_restated_hour_under_a_new_id_still_counts_once(self):
        """Amazon's documented dedupe key is idempotency_id. If a restatement
        ever carries a fresh one, keying on the id alone would count the hour
        twice — so the row's natural grain is the fallback key."""
        self.bank([self.payload()])
        self.bank([self.conversion(idempotency_id=None, sales_30d=19.99)],
                  dataset="sp-conversion")
        self.bank([self.conversion(idempotency_id=None, sales_30d=25.00)],
                  dataset="sp-conversion")
        got = stream_map.summary("US", now=self.now)
        self.assertEqual(got["conversions"]["rows"], 1)
        self.assertEqual(got["conversions"]["sales"], 25.00)


class DrainBacklogReachesTheDay(StreamMapCase):
    """The Dashboard said the day was complete while the queue behind it grew.

    On 2026-08-24 the US panel read "through 07:00 Amazon time", coverage
    complete, 1,67 US$ — and the NA sp-traffic queue held 958 undrained
    messages, 990 twenty minutes later. System Health said so two clicks away.
    The signal was in `stream_store.health()` and never reached the day.
    """

    def setUp(self):
        super().setUp()
        self.market_db("US", [("C1", "SCAVENGER - US Tees 1")])
        self.now = datetime.datetime(2026, 8, 21, 18, 0, tzinfo=datetime.timezone.utc)
        # Hour 00, so the hour counts are whole and the only thing that can make
        # this day incomplete is the drain.
        self.bank([self.payload(time_window_start="2026-08-21T00:00:00-07:00")])

    def _log_drain(self, realm, note):
        conn = stream_store.connect()
        stream_store.log_drain(conn, realm, "sp-traffic", 3000, 3000, 0, 0, note=note)
        conn.close()

    def test_a_queue_that_did_not_empty_is_named_in_the_days_coverage(self):
        self._log_drain("NA", "time budget 300s ran out, queue not empty")
        cov = stream_map.summary("US", now=self.now)["coverage"]
        self.assertEqual(["NA/sp-traffic"], cov["backlog_pending"])
        self.assertFalse(cov["complete"])

    def test_a_drain_that_finished_leaves_the_day_complete(self):
        self._log_drain("NA", "")
        cov = stream_map.summary("US", now=self.now)["coverage"]
        self.assertIsNone(cov["backlog_pending"])
        self.assertTrue(cov["complete"])

    def test_another_realms_backlog_is_not_this_markets_undercount(self):
        """One queue serves a whole realm, so EU's backlog says nothing about
        a US day. Reporting it here would be a false alarm on every EU
        catch-up."""
        self._log_drain("EU", "time budget 300s ran out, queue not empty")
        cov = stream_map.summary("US", now=self.now)["coverage"]
        self.assertIsNone(cov["backlog_pending"])
        self.assertTrue(cov["complete"])


class CampaignRollup(StreamMapCase):
    """The campaign list must reconcile with the headline, or say why it does not.

    It used to do neither. `summary` returned the twelve biggest SPENDERS with
    no count and no flag, so on 2026-08-21 the reply carried 12 of 51 campaigns
    holding 2,478 of 4,465 impressions — and nothing in the shape said the other
    39 existed. The comment three lines above it already explained why a cost
    sort is wrong early in a day; the campaign list did it anyway.
    """

    NOW = datetime.datetime(2026, 8, 21, 18, 0, tzinfo=datetime.timezone.utc)

    def _day(self):
        """Three campaigns: the big spender, the big server, and a quiet one."""
        self.market_db("US", [("C1", "Big Spender"), ("C2", "Big Server"),
                              ("C3", "Quiet")])
        self.bank([
            self.payload(campaign_id="C1", impressions=50, clicks=5, cost=5.0,
                         idempotency_id="a"),
            self.payload(campaign_id="C2", impressions=900, clicks=0, cost=0.0,
                         idempotency_id="b"),
            self.payload(campaign_id="C3", impressions=10, clicks=0, cost=0.0,
                         idempotency_id="c"),
        ])

    def test_every_campaign_is_returned_by_default(self):
        self._day()
        got = stream_map.summary("US", now=self.NOW)
        self.assertEqual(len(got["campaigns"]), 3)
        self.assertEqual(got["campaign_count"], 3)
        self.assertFalse(got["campaigns_truncated"])

    def test_the_campaign_rows_add_up_to_the_headline(self):
        """The one check that catches a silent cap, whatever causes it."""
        self._day()
        got = stream_map.summary("US", now=self.NOW)
        for field in ("impressions", "clicks"):
            self.assertEqual(sum(c[field] for c in got["campaigns"]),
                             got["totals"][field], field)
        self.assertAlmostEqual(sum(c["cost"] for c in got["campaigns"]),
                               got["totals"]["cost"], places=2)

    def test_a_campaign_that_serves_but_has_not_spent_outranks_a_spender(self):
        """Early in a day almost nothing has spent. Ranking on cost then buries
        the fact that a campaign is running at all."""
        self._day()
        got = stream_map.summary("US", now=self.NOW)
        self.assertEqual(got["campaigns"][0]["campaign"], "Big Server")
        self.assertEqual(got["campaigns"][0]["cost"], 0.0)

    def test_a_cap_the_caller_asks_for_is_reported_not_silent(self):
        self._day()
        got = stream_map.summary("US", now=self.NOW, top=1)
        self.assertEqual(len(got["campaigns"]), 1)
        self.assertEqual(got["campaign_count"], 3)
        self.assertTrue(got["campaigns_truncated"])


if __name__ == "__main__":
    unittest.main()
