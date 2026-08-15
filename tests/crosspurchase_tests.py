#!/usr/bin/env python3
"""Measured cross-purchase from the spPurchasedProduct report.

A shopper clicks the ad for one design and buys a different one. The campaign
and targeting reports credit that sale nowhere, so a design can look like it
loses money while quietly selling the rest of the catalogue.

Unlike `halo`, which infers lift correlationally from the Merch
sales report, this is Amazon's own attribution — so the tests below pin the
own-ASIN vs other-ASIN split, which is the whole point of the table.

Run from the Ads folder:  python3 -m unittest tests.crosspurchase_tests -v
"""

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["ADS_MARKET"] = "US"

import db  # noqa: E402


def temp_conn():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    real = db.DB_PATH
    db.DB_PATH = path
    conn = db.connect()
    db.DB_PATH = real
    return conn, path


def row(adv, pur, sales, units=1, **kw):
    base = dict(campaignId="c1", adGroupId="g1", keywordId="k1", keyword="tee",
                keywordType="KEYWORD", matchType="BROAD",
                advertisedAsin=adv, purchasedAsin=pur,
                unitsSoldClicks30d=units, sales30d=sales, purchases30d=units,
                unitsSoldOtherSku30d=0 if adv == pur else units,
                salesOtherSku30d=0.0 if adv == pur else sales,
                purchasesOtherSku30d=0 if adv == pur else units)
    base.update(kw)
    return base


class StorePurchasedProduct(unittest.TestCase):

    def setUp(self):
        self.conn, self.path = temp_conn()

    def tearDown(self):
        self.conn.close()
        os.unlink(self.path)

    def test_stores_every_row(self):
        rows = [row("A", "A", 80.0, 4), row("A", "B", 60.0, 3), row("D", "D", 30.0, 2)]
        self.assertEqual(db.store_purchased_product(self.conn, rows, "2026-08-03"), 3)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM purchased_product").fetchone()[0], 3)

    def test_own_and_other_asin_rows_are_distinguishable(self):
        db.store_purchased_product(
            self.conn, [row("A", "A", 80.0, 4), row("A", "B", 60.0, 3)], "2026-08-03")
        own = self.conn.execute(
            "SELECT SUM(sales) FROM purchased_product"
            " WHERE advertised_asin = purchased_asin").fetchone()[0]
        other = self.conn.execute(
            "SELECT SUM(sales) FROM purchased_product"
            " WHERE advertised_asin <> purchased_asin").fetchone()[0]
        self.assertEqual(own, 80.0)
        self.assertEqual(other, 60.0)

    def test_rerunning_the_same_snapshot_replaces_rather_than_doubles(self):
        """The report is re-pulled nightly; a re-store must not inflate sales."""
        rows = [row("A", "B", 60.0, 3)]
        db.store_purchased_product(self.conn, rows, "2026-08-03")
        db.store_purchased_product(self.conn, rows, "2026-08-03")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM purchased_product").fetchone()[0], 1)
        self.assertEqual(
            self.conn.execute("SELECT SUM(sales) FROM purchased_product").fetchone()[0], 60.0)

    def test_separate_dates_are_separate_snapshots(self):
        db.store_purchased_product(self.conn, [row("A", "B", 60.0, 3)], "2026-08-02")
        db.store_purchased_product(self.conn, [row("A", "B", 70.0, 4)], "2026-08-03")
        self.assertEqual(db.latest_snapshot(self.conn, "purchased_product"), "2026-08-03")
        self.assertEqual(
            self.conn.execute("SELECT sales FROM purchased_product WHERE date='2026-08-03'"
                              ).fetchone()[0], 70.0)

    def test_missing_metrics_default_to_zero_not_null(self):
        sparse = {"campaignId": "c1", "adGroupId": "g1", "keywordId": "k1",
                  "advertisedAsin": "A", "purchasedAsin": "B"}
        db.store_purchased_product(self.conn, [sparse], "2026-08-03")
        got = self.conn.execute(
            "SELECT units_sold, sales, purchases FROM purchased_product").fetchone()
        self.assertEqual(got, (0, 0, 0))


class CrossPurchaseEndpoint(unittest.TestCase):
    """Exercises cmd_crosspurchase itself, so the JSON contract is covered."""

    def setUp(self):
        self.conn, self.path = temp_conn()
        db.store_purchased_product(self.conn, [
            row("A0001", "A0001", 80.0, 4),
            row("A0001", "B0002", 60.0, 3),
            row("A0001", "C0003", 25.0, 1),
            row("D0004", "D0004", 30.0, 2, campaignId="c2", adGroupId="g2"),
        ], "2026-08-03")
        self.conn.execute("INSERT OR REPLACE INTO ad_groups(ad_group_id,campaign_id,name)"
                          " VALUES('g1','c1','Funny Cat Tee')")
        self.conn.commit()
        self.conn.close()

    def tearDown(self):
        os.unlink(self.path)

    def _payload(self):
        import io
        import contextlib
        import appctl
        real = db.DB_PATH
        db.DB_PATH = self.path
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                appctl.cmd_crosspurchase(None)
        finally:
            db.DB_PATH = real
        return json.loads(buf.getvalue())["data"]

    def test_totals_split_own_from_other_asin_sales(self):
        data = self._payload()
        self.assertTrue(data["supported"])
        self.assertEqual(data["as_of"], "2026-08-03")
        self.assertEqual(data["totals"]["own_asin_sales"], 110.0)
        self.assertEqual(data["totals"]["other_asin_sales"], 85.0)
        self.assertAlmostEqual(data["totals"]["other_pct"], 85.0 / 195.0, places=4)

    def test_designs_rank_by_sales_they_sent_elsewhere(self):
        data = self._payload()
        top = data["designs"][0]
        self.assertEqual(top["advertised_asin"], "A0001")
        self.assertEqual(top["ad_group"], "Funny Cat Tee")
        self.assertEqual(top["other_sales"], 85.0)
        self.assertEqual(top["distinct_others"], 2)
        self.assertAlmostEqual(top["other_pct"], 85.0 / 165.0, places=4)

    def test_a_design_that_only_sells_itself_reports_zero_halo(self):
        data = self._payload()
        solo = next(d for d in data["designs"] if d["advertised_asin"] == "D0004")
        self.assertEqual(solo["other_sales"], 0.0)
        self.assertEqual(solo["other_pct"], 0.0)

    def test_pairs_exclude_self_purchases(self):
        data = self._payload()
        self.assertTrue(data["pairs"])
        for pair in data["pairs"]:
            self.assertNotEqual(pair["advertised_asin"], pair["purchased_asin"])
        self.assertEqual(data["pairs"][0]["purchased_asin"], "B0002")


    def test_value_comes_from_the_other_sku_columns(self):
        """Amazon reports a not-advertised purchase's value in *_other_sku only.

        The plain sales/purchases columns describe the ADVERTISED ASIN and are 0
        on these rows. Reading them made every real cross-sell look worthless —
        ES showed 4 pairs at 0.00 when it was really 32.21 EUR."""
        conn, path = temp_conn()
        db.store_sales_report_rows  # noqa: B018 - keep import surface obvious
        conn.execute(
            """INSERT INTO purchased_product
               (date,campaign_id,ad_group_id,keyword_id,keyword,keyword_type,
                match_type,advertised_asin,purchased_asin,units_sold,sales,purchases,
                units_sold_other_sku,sales_other_sku,purchases_other_sku)
               VALUES('2026-08-03','c1','g1','k1','loose','T','T','A0001','B0002',
                      0,0.0,0,1,16.52,1)""")
        conn.commit()
        conn.close()
        import io, contextlib, appctl
        real = db.DB_PATH
        db.DB_PATH = path
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                appctl.cmd_crosspurchase(None)
        finally:
            db.DB_PATH = real
            os.unlink(path)
        data = json.loads(buf.getvalue())["data"]
        self.assertEqual(data["totals"]["other_asin_sales"], 16.52,
                         "value must come from sales_other_sku, not sales")
        self.assertEqual(data["pairs"][0]["units"], 1)

    def test_empty_table_is_unsupported_not_zeroes(self):
        conn, path = temp_conn()
        conn.close()
        real = db.DB_PATH
        db.DB_PATH = path
        import io
        import contextlib
        import appctl
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                appctl.cmd_crosspurchase(None)
        finally:
            db.DB_PATH = real
            os.unlink(path)
        data = json.loads(buf.getvalue())["data"]
        self.assertFalse(data["supported"])
        self.assertNotIn("totals", data)


if __name__ == "__main__":
    unittest.main()
