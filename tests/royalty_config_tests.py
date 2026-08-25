#!/usr/bin/env python3
"""Operator-editable royalties.

US economics used to live only in products.py, so changing a royalty meant
editing Python. This is the overlay that lets the app do it: a gitignored
royalty_overrides.json merged on top of the built-in tables.

The built-in tables stay the floor and keep their self-assert. An override is
validated on the way IN, so the app cannot write a number that would mis-price
the account. A file that is corrupt anyway drops the bad rows and CLOSES the
econ gate rather than quietly pricing off the defaults — the same fail-closed
rule the rest of the economics follow.

Run from the Ads folder:  python3 -m unittest tests.royalty_config_tests -v
No Amazon API, no production config — a temp overrides file per test."""

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ["ADS_MARKET"] = "US"

import products         # noqa: E402
import royalty_config   # noqa: E402

# The shipped numbers are the operator's own now and change when they reprice.
# These tests are about the OVERLAY, so they read the shipped value rather than
# restating it — restating it is how they rotted the first time.
SHIPPED_MUG = products.PRODUCT_ECON["mug"][0]
SHIPPED_2199 = products.US_TEE_ROYALTY_CENTS[2199]
# a label the catalogue really carries and the engine really cannot price
UNPRICED_LABEL = "Sport Backpack"


class OverlayCase(unittest.TestCase):
    """Each test gets its own empty overrides file."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(self.path)                 # start with NO file
        self.real = royalty_config.CONFIG
        royalty_config.CONFIG = self.path
        royalty_config.invalidate()

    def tearDown(self):
        royalty_config.CONFIG = self.real
        royalty_config.invalidate()
        if os.path.exists(self.path):
            os.unlink(self.path)

    def write_raw(self, obj):
        with open(self.path, "w") as f:
            json.dump(obj, f)
        royalty_config.invalidate()


class NoFileTests(OverlayCase):
    def test_the_builtin_tables_are_used_when_no_override_exists(self):
        self.assertEqual(products.tee_royalty_table()[2199], SHIPPED_2199)
        self.assertEqual(products.product_econ_table()["mug"][0], SHIPPED_MUG)

    def test_no_file_is_not_an_error(self):
        self.assertEqual(royalty_config.errors(), [])


class TeePriceOverrideTests(OverlayCase):
    def test_an_override_replaces_a_builtin_royalty(self):
        royalty_config.set_tee_price(2199, 700, note="rate change")
        self.assertEqual(products.tee_royalty_table()[2199], 700)

    def test_the_override_reaches_design_economics(self):
        royalty_config.set_tee_price(2199, 700)
        e = products.get_design_econ("standard_tshirt", price="21.99")
        self.assertAlmostEqual(e["royalty"], 7.00)
        self.assertAlmostEqual(e["break_even"], 700 / 2199, places=4)

    def test_a_new_price_point_can_be_added(self):
        """A price Amazon did not have before must become supported, not unknown."""
        self.assertIsNone(products.tee_royalty_table().get(2599))
        royalty_config.set_tee_price(2599, 1007)
        self.assertEqual(products.tee_royalty_table()[2599], 1007)
        e = products.get_design_econ("standard_tshirt", price="25.99")
        self.assertAlmostEqual(e["royalty"], 10.07)

    def test_clearing_restores_the_builtin_value(self):
        royalty_config.set_tee_price(2199, 700)
        royalty_config.clear_tee_price(2199)
        self.assertEqual(products.tee_royalty_table()[2199], SHIPPED_2199)

    def test_an_override_is_never_flagged_extrapolated(self):
        """An operator-confirmed number is not a guess. Every shipped rung is
        confirmed since 2026-08-21, so this now guards the rule rather than a
        particular rung: whatever the ladder says, an override is not a guess."""
        royalty_config.set_tee_price(1999, 530)
        self.assertFalse(products.get_design_econ(
            "standard_tshirt", price="19.99")["extrapolated"])
        self.assertNotIn(1999, products.tee_extrapolated())


class ProductTypeOverrideTests(OverlayCase):
    def test_royalty_and_price_set_the_break_even(self):
        royalty_config.set_product_type("mug", royalty=3.00, price=18.99)
        e = products.get_econ("mug")
        self.assertAlmostEqual(e["royalty"], 3.00)
        self.assertAlmostEqual(e["break_even"], 3.00 / 18.99, places=4)

    def test_a_new_product_type_becomes_known(self):
        self.assertFalse(products.get_econ("beach_towel")["known"])
        royalty_config.set_product_type("beach_towel", royalty=6.00, price=29.99)
        e = products.get_econ("beach_towel")
        self.assertTrue(e["known"])
        self.assertAlmostEqual(e["royalty"], 6.00)

    def test_thresholds_follow_the_new_royalty(self):
        royalty_config.set_product_type("mug", royalty=3.00, price=18.99)
        self.assertAlmostEqual(products.get_econ("mug")["neg_threshold"], 1.50)

    def test_clearing_restores_the_builtin_value(self):
        royalty_config.set_product_type("mug", royalty=3.00, price=18.99)
        royalty_config.clear_product_type("mug")
        self.assertAlmostEqual(products.get_econ("mug")["royalty"], SHIPPED_MUG)

    def test_the_flat_tee_pause_survives_an_override(self):
        """standard_tshirt keeps its $5 flat ad-group pause — that is policy,
        not arithmetic, and an edited royalty must not quietly change it."""
        royalty_config.set_product_type("standard_tshirt", royalty=6.00, price=22.99)
        self.assertAlmostEqual(products.get_econ("standard_tshirt")["pause_threshold"], 5.00)


class RejectBadInputTests(OverlayCase):
    def bad(self, fn, *a, **kw):
        with self.assertRaises(ValueError):
            fn(*a, **kw)
        self.assertEqual(royalty_config.load()["product_types"], {})
        self.assertEqual(royalty_config.load()["tee_prices"], {})

    def test_a_zero_royalty_is_refused(self):
        self.bad(royalty_config.set_product_type, "mug", royalty=0, price=18.99)

    def test_a_negative_royalty_is_refused(self):
        self.bad(royalty_config.set_product_type, "mug", royalty=-1, price=18.99)

    def test_a_royalty_above_the_price_is_refused(self):
        """You cannot earn more than the item sold for."""
        self.bad(royalty_config.set_product_type, "mug", royalty=25.0, price=18.99)

    def test_a_zero_price_is_refused(self):
        self.bad(royalty_config.set_product_type, "mug", royalty=3.0, price=0)

    def test_a_non_numeric_value_is_refused(self):
        self.bad(royalty_config.set_product_type, "mug", royalty="lots", price=18.99)

    def test_an_empty_product_type_is_refused(self):
        self.bad(royalty_config.set_product_type, "  ", royalty=3.0, price=18.99)

    def test_a_tee_royalty_above_its_price_is_refused(self):
        self.bad(royalty_config.set_tee_price, 2199, 2500)

    def test_an_absurd_price_is_refused(self):
        self.bad(royalty_config.set_product_type, "mug", royalty=3.0, price=100000.0)


class CorruptFileTests(OverlayCase):
    def test_a_bad_row_is_dropped_and_reported(self):
        self.write_raw({"product_types": {"mug": {"royalty": 99.0, "price": 18.99}}})
        self.assertAlmostEqual(products.get_econ("mug")["royalty"], SHIPPED_MUG)
        self.assertTrue(any("mug" in e for e in royalty_config.errors()))

    def test_unreadable_json_is_reported_not_raised(self):
        with open(self.path, "w") as f:
            f.write("{not json")
        royalty_config.invalidate()
        self.assertEqual(royalty_config.load()["product_types"], {})
        self.assertTrue(royalty_config.errors())

    def test_a_good_row_survives_a_bad_neighbour(self):
        self.write_raw({"product_types": {
            "mug": {"royalty": 99.0, "price": 18.99},
            "tote_bag": {"royalty": 6.0, "price": 24.99}}})
        self.assertAlmostEqual(products.get_econ("tote_bag")["royalty"], 6.00)
        self.assertAlmostEqual(products.get_econ("mug")["royalty"], SHIPPED_MUG)

    def test_the_econ_gate_closes_on_a_bad_override(self):
        """Fail closed. Mis-priced economics must not drive a live write."""
        self.write_raw({"product_types": {"mug": {"royalty": 99.0, "price": 18.99}}})
        gate = products.econ_gate()
        self.assertFalse(gate["ok"])
        self.assertTrue(any("royalt" in r.lower() for r in gate["reasons"]))


class FreshnessTests(OverlayCase):
    """The app keeps a long-running `serve` worker per market for fast reads. If
    the overlay cached forever, an edit saved by a one-shot call would not show
    up until the worker was restarted — the operator would type a royalty, see
    the old number, and type it again."""

    def test_a_change_made_by_another_process_is_picked_up(self):
        royalty_config.set_product_type("mug", royalty=3.00, price=18.99)
        self.assertAlmostEqual(products.get_econ("mug")["royalty"], 3.00)
        # a DIFFERENT process writes the file; this one never calls invalidate()
        raw = json.load(open(self.path))
        raw["product_types"]["mug"]["royalty"] = 4.00
        with open(self.path, "w") as f:
            json.dump(raw, f)
        os.utime(self.path, (0, 0))          # force a distinct mtime
        self.assertAlmostEqual(products.get_econ("mug")["royalty"], 4.00,
                               msg="the overlay cached past an external write")

    def test_deleting_the_file_restores_the_builtins(self):
        royalty_config.set_product_type("mug", royalty=3.00, price=18.99)
        os.unlink(self.path)
        self.assertAlmostEqual(products.get_econ("mug")["royalty"], SHIPPED_MUG)


class OperatorTypeResolutionTests(OverlayCase):
    """A type the operator has priced must stop being "unknown".

    Snap for MOD exports a dashboard LABEL ("Sport Backpack"). If that label
    has no entry in products.EXPORT_TYPE_LABELS the engine keeps it verbatim, so
    pricing it under that exact spelling is how the operator makes it real.
    """

    def test_an_unpriced_label_is_still_unknown(self):
        self.assertIsNone(products.type_from_export_label(UNPRICED_LABEL))

    def test_pricing_a_label_makes_it_resolve(self):
        royalty_config.set_product_type(UNPRICED_LABEL, royalty=4.53, price=24.99)
        self.assertEqual(products.type_from_export_label(UNPRICED_LABEL),
                         UNPRICED_LABEL)
        self.assertAlmostEqual(products.get_econ(UNPRICED_LABEL)["royalty"], 4.53)

    def test_a_near_miss_is_still_never_guessed_into_the_lottery_type(self):
        """standard_tshirt is the lottery money path. Adding types must not make
        the matcher looser about it."""
        royalty_config.set_product_type(UNPRICED_LABEL, royalty=4.53, price=24.99)
        for label in ("Ringer T-Shirt", "Sport Jersey Shirt"):
            self.assertNotEqual(
                products.type_from_export_label(label), "standard_tshirt", label)


class PerMarketTests(OverlayCase):
    """Amazon fixes a maximum price per product per market, so each market keeps
    its own royalty and one market's edit must never leak into another."""

    def test_a_market_override_beats_the_derived_number(self):
        royalty_config.set_product_type("standard_tshirt", royalty=2.70, price=17.99,
                                        market="DE")
        e = products.get_econ("standard_tshirt", market="DE")
        self.assertAlmostEqual(e["royalty"], 2.70)
        self.assertAlmostEqual(e["break_even"], 2.70 / 17.99, places=4)
        self.assertTrue(e["known"])

    def test_markets_do_not_leak_into_each_other(self):
        royalty_config.set_product_type("standard_tshirt", royalty=2.70, price=17.99,
                                        market="DE")
        self.assertEqual(royalty_config.load("FR")["product_types"], {})
        self.assertEqual(royalty_config.load("US")["product_types"], {})

    def test_the_us_ladder_is_not_visible_to_another_market(self):
        royalty_config.set_tee_price(2199, 700)
        self.assertEqual(royalty_config.load("DE")["tee_prices"], {})

    def test_clearing_is_market_scoped(self):
        for m in ("DE", "FR"):
            royalty_config.set_product_type("standard_tshirt", royalty=3.00, price=19.49,
                                            market=m)
        royalty_config.clear_product_type("standard_tshirt", market="DE")
        self.assertEqual(royalty_config.load("DE")["product_types"], {})
        self.assertIn("standard_tshirt", royalty_config.load("FR")["product_types"])

    def test_a_bad_row_in_one_market_closes_only_that_gate(self):
        self.write_raw({"markets": {"DE": {"product_types": {
            "standard_tshirt": {"royalty": 99.0, "price": 17.99}}}}})
        self.assertTrue(royalty_config.errors("DE"))
        self.assertEqual(royalty_config.errors("FR"), [])
        self.assertFalse(products.econ_gate(market="DE")["ok"])
        self.assertTrue(products.econ_gate(market="FR")["ok"])


class BuiltinGuardTests(OverlayCase):
    def test_the_builtin_table_still_self_asserts(self):
        """The override layer must not weaken the guard on the shipped table."""
        with open(os.path.join(HERE, "engine", "products.py")) as f:
            src = f.read()
        self.assertIn("US tee royalty table corrupted", src)

    def test_an_override_cannot_delete_a_builtin_price(self):
        royalty_config.set_tee_price(2199, 700)
        for cents in (1499, 2299, 2399, 2499):
            self.assertIn(cents, products.tee_royalty_table())


if __name__ == "__main__":
    unittest.main()
