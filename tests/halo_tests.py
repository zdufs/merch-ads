#!/usr/bin/env python3
"""Organic-halo estimator: scope, windowing, and the honesty flags.

Run from the Ads folder:  python3 -m unittest tests.halo_tests -v

The estimator used to be scoped to a retired strategy whose campaigns held
exactly one ASIN. These tests pin the generalisation: the unit is the DESIGN, its ad facts
are summed across every ad group advertising it, and a design in a 1,000-ASIN
lottery campaign is treated no differently from one in its own campaign.
"""

import datetime
import os
import sqlite3
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ["ADS_MARKET"] = "US"

import halo  # noqa: E402


def d(s):
    return datetime.date.fromisoformat(s)


def fixture_conn(target_daily_rows, ad_group_product, ad_groups=(), campaigns=()):
    """target_daily_rows: (date, ad_group_id, impressions, clicks, cost)
       ad_group_product:  (ad_group_id, asin)"""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE target_daily (date TEXT, campaign_id TEXT, ad_group_id TEXT, "
                 "impressions INTEGER, clicks INTEGER, cost REAL)")
    conn.execute("CREATE TABLE ad_group_product (ad_group_id TEXT, asin TEXT)")
    conn.execute("CREATE TABLE ad_groups (ad_group_id TEXT, campaign_id TEXT, name TEXT)")
    conn.execute("CREATE TABLE campaigns (campaign_id TEXT, name TEXT)")
    conn.executemany("INSERT INTO target_daily (date, ad_group_id, impressions, clicks, cost) "
                     "VALUES (?,?,?,?,?)", target_daily_rows)
    conn.executemany("INSERT INTO ad_group_product VALUES (?,?)", ad_group_product)
    conn.executemany("INSERT INTO ad_groups VALUES (?,?,?)", ad_groups)
    conn.executemany("INSERT INTO campaigns VALUES (?,?)", campaigns)
    conn.commit()
    return conn


def sales(rows):
    """rows: (date, asin, royalty, purchased, returned) -> traz.load_sales_rows shape."""
    return [{"date": d(dt), "asin": a, "mkt": ".com",
             "royalty": r, "purchased": p, "returned": ret}
            for dt, a, r, p, ret in rows]


class AdFactsSpanEveryAdGroup(unittest.TestCase):
    def test_spend_and_clicks_sum_across_ad_groups(self):
        # One design advertised in TWO ad groups — a lottery campaign and its
        # typed scavenger cohort. It has ONE ad history, not two.
        conn = fixture_conn(
            target_daily_rows=[("2026-05-10", "ag1", 100, 5, 2.00),
                               ("2026-05-11", "ag1", 100, 5, 3.00),
                               ("2026-05-12", "ag2", 50, 2, 1.50)],
            ad_group_product=[("ag1", "B0EXAMPLE1"), ("ag2", "B0EXAMPLE1")])
        facts = halo.ad_facts(conn, d("2026-05-01"), d("2026-05-31"))
        self.assertEqual(list(facts), ["B0EXAMPLE1"])
        self.assertAlmostEqual(facts["B0EXAMPLE1"]["spend"], 6.50)
        self.assertEqual(facts["B0EXAMPLE1"]["clicks"], 12)

    def test_ad_start_is_the_first_day_with_impressions(self):
        # Rows exist before serving begins; ad_start must be the first IMPRESSION,
        # not the first row, or the baseline window is silently truncated.
        conn = fixture_conn(
            target_daily_rows=[("2026-05-08", "ag1", 0, 0, 0.0),
                               ("2026-05-09", "ag1", 0, 0, 0.0),
                               ("2026-05-10", "ag1", 40, 1, 0.30)],
            ad_group_product=[("ag1", "B0EXAMPLE1")])
        facts = halo.ad_facts(conn, d("2026-05-01"), d("2026-05-31"))
        self.assertEqual(facts["B0EXAMPLE1"]["ad_start"], d("2026-05-10"))

    def test_never_served_design_has_no_ad_start(self):
        conn = fixture_conn(
            target_daily_rows=[("2026-05-10", "ag1", 0, 0, 0.0)],
            ad_group_product=[("ag1", "B0EXAMPLE1")])
        facts = halo.ad_facts(conn, d("2026-05-01"), d("2026-05-31"))
        self.assertIsNone(facts["B0EXAMPLE1"]["ad_start"])

    def test_window_excludes_rows_outside_the_report(self):
        conn = fixture_conn(
            target_daily_rows=[("2026-04-01", "ag1", 10, 1, 99.0),   # before
                               ("2026-05-10", "ag1", 10, 1, 1.00),   # inside
                               ("2026-07-01", "ag1", 10, 1, 99.0)],  # after
            ad_group_product=[("ag1", "B0EXAMPLE1")])
        facts = halo.ad_facts(conn, d("2026-05-01"), d("2026-05-31"))
        self.assertAlmostEqual(facts["B0EXAMPLE1"]["spend"], 1.00)


class HaloMaths(unittest.TestCase):
    def setUp(self):
        self._real_sales = halo.traz.load_sales_rows
        self._real_default = halo.markets.is_default
        halo.markets.is_default = lambda: True
        self.addCleanup(setattr, halo.traz, "load_sales_rows", self._real_sales)
        self.addCleanup(setattr, halo.markets, "is_default", self._real_default)

    def run_analyze(self, td, agp, sales_rows, **kw):
        halo.traz.load_sales_rows = lambda: sales(sales_rows)
        conn = fixture_conn(td, agp,
                            ad_groups=[("ag1", "c1", "B0EXAMPLE1_standard_tshirt_A Nice Design")],
                            campaigns=[("c1", "Lotto 3")])
        return halo.analyze(conn=conn, min_spend=0.0, limit=0, **kw)

    def test_lift_over_baseline_is_the_estimate(self):
        # Baseline 10 days at $1/day. Ad starts on the 11th, then $3/day for 10 days.
        # halo_est = (3 - 1) * 10 = 20
        td = [("2026-05-11", "ag1", 10, 1, 5.00)]
        agp = [("ag1", "B0EXAMPLE1")]
        rows = ([("2026-05-%02d" % day, "B0EXAMPLE1", 1.0, 1, 0) for day in range(1, 11)]
                + [("2026-05-%02d" % day, "B0EXAMPLE1", 3.0, 1, 0) for day in range(11, 21)])
        res = self.run_analyze(td, agp, rows)
        got = res["designs"][0]
        self.assertEqual(got["ad_start"], "2026-05-11")
        self.assertAlmostEqual(got["base_rate"], 1.0, places=2)
        self.assertAlmostEqual(got["post_rate"], 3.0, places=2)
        self.assertAlmostEqual(got["halo_est"], 20.0, places=1)
        self.assertAlmostEqual(got["net_halo"], 15.0, places=1)   # 20 - 5 spend

    def test_no_ad_traffic_is_flagged(self):
        """Impressions but zero clicks: any 'post' lift is organic by construction."""
        td = [("2026-05-11", "ag1", 500, 0, 0.0)]
        rows = [("2026-05-%02d" % day, "B0EXAMPLE1", 2.0, 1, 0) for day in range(1, 21)]
        res = self.run_analyze(td, [("ag1", "B0EXAMPLE1")], rows)
        self.assertIn("no-ad-traffic", res["designs"][0]["flags"])

    def test_peak_before_ad_is_flagged_not_reported_as_ad_harm(self):
        """Royalty peaked BEFORE the ad. A negative halo_est here is a baseline
        artifact — usually seasonal — so it must carry the confound flag."""
        td = [("2026-05-11", "ag1", 10, 3, 1.00)]
        rows = ([("2026-05-%02d" % day, "B0EXAMPLE1", 20.0, 1, 0) for day in range(1, 11)]
                + [("2026-05-%02d" % day, "B0EXAMPLE1", 0.5, 1, 0) for day in range(11, 21)])
        res = self.run_analyze(td, [("ag1", "B0EXAMPLE1")], rows)
        got = res["designs"][0]
        self.assertLess(got["halo_est"], 0)
        self.assertIn("peak-before-ad", got["flags"])

    def test_title_comes_from_the_descriptive_ad_group_name(self):
        td = [("2026-05-11", "ag1", 10, 1, 1.00)]
        rows = [("2026-05-11", "B0EXAMPLE1", 1.0, 1, 0)]
        res = self.run_analyze(td, [("ag1", "B0EXAMPLE1")], rows)
        self.assertEqual(res["designs"][0]["title"], "A Nice Design")

    def test_campaign_type_is_reported(self):
        td = [("2026-05-11", "ag1", 10, 1, 1.00)]
        rows = [("2026-05-11", "B0EXAMPLE1", 1.0, 1, 0)]
        res = self.run_analyze(td, [("ag1", "B0EXAMPLE1")], rows)
        self.assertEqual(res["designs"][0]["campaign_types"], "lottery")

    def test_no_daily_history_fails_loudly_not_silently_empty(self):
        """An empty result and 'we have no data' are different answers. Reporting
        zero halo when the per-day table is simply unbanked would read as
        'advertising does nothing'."""
        halo.traz.load_sales_rows = lambda: sales([("2026-05-01", "B0EXAMPLE1", 1.0, 1, 0)])
        conn = fixture_conn([], [("ag1", "B0EXAMPLE1")])
        res = halo.analyze(conn=conn)
        self.assertIn("error", res)
        self.assertIn("target_daily", res["error"])


class NonUsMarkets(unittest.TestCase):
    def test_non_us_returns_none_because_the_sales_report_is_us_only(self):
        real = halo.markets.is_default
        halo.markets.is_default = lambda: False
        self.addCleanup(setattr, halo.markets, "is_default", real)
        self.assertIsNone(halo.analyze())


class BulkTitles(unittest.TestCase):
    """`titles_by_asin` replaced one query per design. It must agree with the
    single-ASIN path it replaced, or the Organic Halo screen would get faster
    and wrong at the same time."""

    def test_bulk_map_matches_the_single_asin_lookup(self):
        conn = fixture_conn(
            target_daily_rows=[("2026-01-01", "g1", 10, 1, 1.0)],
            ad_group_product=[("g1", "B01"), ("g2", "B01"), ("g3", "B02")],
            ad_groups=[("g1", "c1", "B01"),
                       ("g2", "c1", "B01_standard_tshirt_Big Cat Energy"),
                       ("g3", "c1", "B02_hoodie_Quiet Please")])
        bulk = halo.titles_by_asin(conn)
        self.assertEqual(bulk["B01"], "Big Cat Energy")
        self.assertEqual(bulk["B02"], "Quiet Please")
        for asin in ("B01", "B02"):
            self.assertEqual(bulk.get(asin), halo.design_title(conn, asin))

    def test_bare_asin_name_carries_no_title(self):
        """An ad group named just the ASIN has no title in it. Absent, not blank."""
        conn = fixture_conn(
            target_daily_rows=[("2026-01-01", "g1", 10, 1, 1.0)],
            ad_group_product=[("g1", "B01")],
            ad_groups=[("g1", "c1", "B01")])
        self.assertNotIn("B01", halo.titles_by_asin(conn))
        self.assertIsNone(halo.design_title(conn, "B01"))


class CacheKeyIsTheInputs(unittest.TestCase):
    """The cache is keyed on what the answer depends on, never on a clock.

    Halo is recomputed on every screen open and took seven seconds. Caching it
    is only safe if a changed input misses. These tests pin that, and pin the
    two refusals: a caller-supplied database is never cached, and an
    unreadable input yields no key rather than a key that looks unchanged.
    """

    def setUp(self):
        self.conn = fixture_conn(
            target_daily_rows=[("2026-01-01", "g1", 10, 1, 1.0)],
            ad_group_product=[("g1", "B01")],
            ad_groups=[("g1", "c1", "B01_standard_tshirt_Title")])
        self.report = os.path.join(HERE, "outputs", "_halo_key_probe.csv")
        os.makedirs(os.path.dirname(self.report), exist_ok=True)
        with open(self.report, "w") as fh:
            fh.write("x")
        real = halo.traz.sales_report_path
        halo.traz.sales_report_path = lambda: self.report
        self.addCleanup(setattr, halo.traz, "sales_report_path", real)
        self.addCleanup(lambda: os.path.exists(self.report) and os.remove(self.report))

    def test_a_new_banked_day_changes_the_key(self):
        before = halo._cache_key(self.conn, 1.0)
        self.conn.execute("INSERT INTO target_daily (date, ad_group_id, impressions, clicks, cost) "
                          "VALUES ('2026-01-02','g1',1,0,0.5)")
        after = halo._cache_key(self.conn, 1.0)
        self.assertNotEqual(before, after)

    def test_min_spend_is_part_of_the_key(self):
        self.assertNotEqual(halo._cache_key(self.conn, 1.0),
                            halo._cache_key(self.conn, 5.0))

    def test_a_replaced_sales_report_changes_the_key(self):
        before = halo._cache_key(self.conn, 1.0)
        with open(self.report, "w") as fh:
            fh.write("a much longer report body")
        self.assertNotEqual(before, halo._cache_key(self.conn, 1.0))

    def test_missing_report_yields_no_key_so_nothing_is_cached(self):
        os.remove(self.report)
        self.assertIsNone(halo._cache_key(self.conn, 1.0))

    def test_a_none_key_never_reads_or_writes(self):
        self.assertIsNone(halo._cache_read(None))
        halo._cache_write(None, {"designs": []})   # must not raise

    def test_caller_supplied_connection_is_never_served_from_cache(self):
        """The rules DSL under test passes its own database. Serving it the
        operator's cached answer would be the same bug `conn` exists to stop."""
        seen = []
        real = halo._cache_key
        halo._cache_key = lambda conn, ms: seen.append(1) or real(conn, ms)
        self.addCleanup(setattr, halo, "_cache_key", real)
        # Stub the sales rows. Left real, analyze() opens the OPERATOR's database
        # to read them — which is both the thing this suite is supposed to never
        # do, and a way to block on a lock the running app is holding.
        real_rows = halo.traz.load_sales_rows
        halo.traz.load_sales_rows = lambda *a, **k: []
        self.addCleanup(setattr, halo.traz, "load_sales_rows", real_rows)
        halo.analyze(conn=self.conn)
        self.assertEqual(seen, [], "_cache_key was consulted for a caller's own connection")


if __name__ == "__main__":
    unittest.main()
