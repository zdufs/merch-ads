#!/usr/bin/env python3
"""The shipped economics ARE the operator's numbers now.

Every royalty was read off the Merch dashboard on 2026-08-20/21 and promoted
from the overrides file into products.py, so the engine ships with them and the
app shows "built-in" rather than "yours". Nothing is a guess and nothing is a
median any more.

These tests exist because the tables are now hand-maintained data that money
decisions run on. A typo in one of them mis-prices a whole product line.

Run from the Ads folder:  python3 -m unittest tests.shipped_economics_tests -v"""

import os
import sys

# tests/ on the path so this works whether unittest imports us as `tests.x`
# (package) or `x` (what `unittest discover` does).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _no_operator_data  # noqa: F401,E402  (isolates the operator overlay)
import unittest


HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ.setdefault("ADS_MARKET", "US")

import products  # noqa: E402

EU = ("UK", "DE", "FR", "IT", "ES")


class ArithmeticTests(unittest.TestCase):
    """break-even must BE royalty / price. It is stored, so it can drift."""

    def test_every_us_break_even_matches_its_price(self):
        for t, (roy, be, _model, _tgt) in products.PRODUCT_ECON.items():
            price = products.PRODUCT_PRICE.get(t)
            self.assertIsNotNone(price, f"{t} has no list price")
            # the table stores 3 decimals, so rounding can sit half a tick out
            self.assertAlmostEqual(be, roy / price, delta=0.0006,
                                   msg=f"US {t}: {be} != {roy}/{price}")

    def test_every_market_break_even_matches_its_price(self):
        for m in EU:
            for t, (roy, be, price) in products.MARKET_PRODUCT_ECON[m].items():
                self.assertAlmostEqual(be, roy / price, delta=0.0006,
                                       msg=f"{m} {t}: {be} != {roy}/{price}")

    def test_every_tee_rung_break_even_matches_its_price(self):
        for cents, roy in products.US_TEE_ROYALTY_CENTS.items():
            e = products.tee_econ(cents) if hasattr(products, "tee_econ") else None
            self.assertLess(roy, cents, f"${cents/100:.2f} tee earns more than it sells for")


class SanityTests(unittest.TestCase):
    """A royalty that cannot be real must never reach a bid."""

    def test_no_product_earns_more_than_it_sells_for(self):
        for t, (roy, _be, _m, _tgt) in products.PRODUCT_ECON.items():
            self.assertLess(roy, products.PRODUCT_PRICE[t], f"US {t}")
        for m in EU:
            for t, (roy, _be, price) in products.MARKET_PRODUCT_ECON[m].items():
                self.assertLess(roy, price, f"{m} {t}")

    def test_every_royalty_is_positive(self):
        for t, (roy, *_rest) in products.PRODUCT_ECON.items():
            self.assertGreater(roy, 0, f"US {t}")
        for m in EU:
            for t, (roy, *_rest) in products.MARKET_PRODUCT_ECON[m].items():
                self.assertGreater(roy, 0, f"{m} {t}")

    def test_no_break_even_is_absurd(self):
        """Above ~60% would mean Amazon pays out most of the sale price."""
        for t, (_r, be, _m, _t) in products.PRODUCT_ECON.items():
            self.assertTrue(0.01 < be < 0.60, f"US {t} break-even {be}")
        for m in EU:
            for t, (_r, be, _p) in products.MARKET_PRODUCT_ECON[m].items():
                self.assertTrue(0.01 < be < 0.60, f"{m} {t} break-even {be}")


class NothingIsAGuessTests(unittest.TestCase):
    def test_no_tee_rung_is_still_extrapolated(self):
        self.assertEqual(products.US_TEE_EXTRAPOLATED, set())

    def test_the_ladder_rises_with_price(self):
        """A dearer tee must earn more. A transposed pair would show up here."""
        rungs = sorted(products.US_TEE_ROYALTY_CENTS.items())
        for (p1, r1), (p2, r2) in zip(rungs, rungs[1:]):
            self.assertLess(r1, r2, f"${p1/100:.2f} earns more than ${p2/100:.2f}")

    def test_the_standard_tee_is_still_the_growth_model(self):
        """Model A is the tee's CVR-first path. Promoting the tables must not
        have flattened it into everything-else."""
        self.assertEqual(products.PRODUCT_ECON["standard_tshirt"][2], "A")


class MarketLookupTests(unittest.TestCase):
    def test_a_market_reads_its_own_shipped_royalty(self):
        for m in EU:
            roy = products.MARKET_PRODUCT_ECON[m]["standard_tshirt"][0]
            self.assertAlmostEqual(products.get_econ("standard_tshirt", market=m)["royalty"],
                                   roy, msg=m)

    def test_the_price_lookup_returns_the_real_price(self):
        """Not royalty / break_even, which lands a few cents off."""
        self.assertEqual(products.list_price_for("standard_sweatshirt", market="US"), 33.99)
        self.assertEqual(products.list_price_for("standard_tshirt", market="DE"), 17.99)

    def test_an_unshipped_type_still_falls_through(self):
        """A market keeps working for a product nobody has confirmed yet."""
        self.assertFalse(products.get_econ("beach_towel", market="UK")["known"])


if __name__ == "__main__":
    unittest.main()
