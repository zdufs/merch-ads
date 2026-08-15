#!/usr/bin/env python3
"""Owned cross-sell — the guard that spares a bleeding design when its ads sell
MY other designs.

The point of these tests is the two decisions that make the guard correct:
  1. Only cross-purchases of ASINs in MY catalogue count. A third of the
     cross-purchased ASINs in real data are not mine, and those must never
     protect a design.
  2. The value is ROYALTY per unit (from the trusted per-design economics),
     not the retail sale — I earn a royalty, not the whole price.

Run from the Ads folder:  python3 -m unittest tests.cross_sell_tests -v
"""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ["ADS_MARKET"] = "US"

import db          # noqa: E402
import products    # noqa: E402
import cross_sell  # noqa: E402


def temp_conn():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    real = db.DB_PATH
    db.DB_PATH = path
    conn = db.connect()
    db.DB_PATH = real
    return conn, path


def econ_row(asin, product_type, price, mkt="us", export="2026-08-04"):
    # (export_date, asin, marketplace, product_type, brand, status,
    #  list_price, royalty_last30, sales_last30, sales_total)
    return (export, asin, mkt, product_type, "brand", "live", price, 0.0, 0, 0)


def purchase(adv, pur, units, sales=0.0):
    """One spPurchasedProduct row. Cross rows put the units in the *OtherSku
    columns, exactly like Amazon's report."""
    same = adv == pur
    return dict(campaignId="c1", adGroupId="g1", keywordId="k1", keyword="tee",
                keywordType="KEYWORD", matchType="BROAD",
                advertisedAsin=adv, purchasedAsin=pur,
                unitsSoldClicks30d=units if same else 0,
                sales30d=sales if same else 0.0, purchases30d=units if same else 0,
                unitsSoldOtherSku30d=0 if same else units,
                salesOtherSku30d=0.0 if same else sales,
                purchasesOtherSku30d=0 if same else units)


class OwnedCrossSellRoyalty(unittest.TestCase):

    def setUp(self):
        self.conn, self.path = temp_conn()
        # Stub the economics so the test pins cross_sell's own logic, not the
        # tee-price table (which has its own tests). product_type "royN" -> $N
        # royalty per unit; anything else -> no royalty.
        self._real_econ = products.get_design_econ
        products.get_design_econ = self._stub_econ

    def tearDown(self):
        products.get_design_econ = self._real_econ
        self.conn.close()
        os.unlink(self.path)

    @staticmethod
    def _stub_econ(product_type, market=None, price=None):
        if product_type and product_type.startswith("roy"):
            return {"royalty": float(product_type[3:])}
        return {"royalty": None}

    def test_only_my_asins_count(self):
        # B1 is mine (in econ snapshot), B2 is not. g1's ad sold both.
        db.store_asin_econ_snapshot(self.conn, [econ_row("B1", "roy4", "23.99")])
        db.store_purchased_product(
            self.conn,
            [purchase("A", "B1", 2, 40.0), purchase("A", "B2", 5, 90.0)],
            "2026-08-12")
        out = cross_sell.owned_cross_sell_royalty(self.conn, "US", econ_conn=self.conn)
        self.assertIn("g1", out)
        self.assertEqual(out["g1"]["royalty"], 8.0)       # 2 units * $4, B2 ignored
        self.assertEqual(out["g1"]["owned_units"], 2)
        self.assertEqual([o["asin"] for o in out["g1"]["others"]], ["B1"])

    def test_sums_multiple_owned_designs(self):
        db.store_asin_econ_snapshot(self.conn, [
            econ_row("B1", "roy4", "23.99"), econ_row("B3", "roy5", "21.99")])
        db.store_purchased_product(
            self.conn,
            [purchase("A", "B1", 2, 40.0), purchase("A", "B3", 3, 60.0)],
            "2026-08-12")
        out = cross_sell.owned_cross_sell_royalty(self.conn, "US", econ_conn=self.conn)
        self.assertEqual(out["g1"]["royalty"], 23.0)      # 2*4 + 3*5
        self.assertEqual(out["g1"]["owned_units"], 5)

    def test_same_asin_is_not_cross_sell(self):
        # A shopper buying the advertised design is own-sku, never cross-sell.
        db.store_asin_econ_snapshot(self.conn, [econ_row("A", "roy4", "23.99")])
        db.store_purchased_product(
            self.conn, [purchase("A", "A", 3, 60.0)], "2026-08-12")
        out = cross_sell.owned_cross_sell_royalty(self.conn, "US", econ_conn=self.conn)
        self.assertEqual(out, {})

    def test_no_royalty_means_no_protection(self):
        # Mine, but the economics resolve to no royalty (unknown price etc.).
        db.store_asin_econ_snapshot(self.conn, [econ_row("B1", "unknown", "0.00")])
        db.store_purchased_product(
            self.conn, [purchase("A", "B1", 2, 40.0)], "2026-08-12")
        out = cross_sell.owned_cross_sell_royalty(self.conn, "US", econ_conn=self.conn)
        self.assertEqual(out, {})

    def test_fail_open_without_purchased_snapshot(self):
        db.store_asin_econ_snapshot(self.conn, [econ_row("B1", "roy4", "23.99")])
        self.assertEqual(cross_sell.owned_cross_sell_royalty(self.conn, "US", econ_conn=self.conn), {})

    def test_fail_open_without_econ_snapshot(self):
        db.store_purchased_product(
            self.conn, [purchase("A", "B1", 2, 40.0)], "2026-08-12")
        self.assertEqual(cross_sell.owned_cross_sell_royalty(self.conn, "US", econ_conn=self.conn), {})

    def test_econ_from_a_separate_db_like_eu(self):
        # EU mirrors the real layout: purchased_product in the market DB, but the
        # account-wide econ snapshot in a DIFFERENT (default) DB. The DE rows use
        # marketplace 'de'.
        econ_conn, econ_path = temp_conn()
        try:
            db.store_asin_econ_snapshot(
                econ_conn, [econ_row("B1", "roy6", "18.45", mkt="de")])
            db.store_purchased_product(
                self.conn, [purchase("A", "B1", 2, 36.0)], "2026-08-12")
            out = cross_sell.owned_cross_sell_royalty(
                self.conn, "DE", econ_conn=econ_conn)
            self.assertEqual(out["g1"]["royalty"], 12.0)      # 2 units * $6
            self.assertEqual(out["g1"]["owned_units"], 2)
        finally:
            econ_conn.close()
            os.unlink(econ_path)


class SparesPause(unittest.TestCase):

    def test_threshold_is_royalty_ge_spend(self):
        m = {"g1": {"royalty": 10.0, "owned_units": 3, "others": []}}
        self.assertTrue(cross_sell.spares_pause(m, "g1", 10.0))   # exactly covers
        self.assertTrue(cross_sell.spares_pause(m, "g1", 9.99))   # covers with room
        self.assertFalse(cross_sell.spares_pause(m, "g1", 10.01)) # short
        self.assertTrue(cross_sell.spares_pause(m, "g1", 0.0))    # any spend<=roy spares

    def test_unknown_or_empty_spares_nothing(self):
        self.assertFalse(cross_sell.spares_pause({}, "g1", 0.0))
        self.assertFalse(cross_sell.spares_pause({"g2": {"royalty": 99}}, "g1", 1.0))

    def test_accepts_int_and_str_ids(self):
        m = {"123": {"royalty": 5.0, "owned_units": 1, "others": []}}
        self.assertTrue(cross_sell.spares_pause(m, 123, 4.0))
        self.assertTrue(cross_sell.spares_pause(m, "123", 4.0))


if __name__ == "__main__":
    unittest.main()
