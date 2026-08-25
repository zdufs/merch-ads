#!/usr/bin/env python3
"""The appctl surface the Product Royalty tab talks to.

`royalties` reports every royalty the engine prices with and says where each
number came from. `royalty-set` / `royalty-clear` edit the US overrides. Other
markets derive their economics from the product export, so they come back
read-only and an edit is refused rather than silently doing nothing.

Run from the Ads folder:  python3 -m unittest tests.royalty_endpoint_tests -v
No Amazon API."""

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ["ADS_MARKET"] = "US"

import appctl            # noqa: E402
import products          # noqa: E402
import royalty_config    # noqa: E402

# The shipped numbers are the operator's own and change when they reprice, so
# read the value rather than restating it.
SHIPPED_MUG = products.PRODUCT_ECON["mug"][0]


class Args:
    def __init__(self, **kw):
        self.type = None
        self.price = None
        self.royalty = None
        self.note = None
        self.__dict__.update(kw)


def call(fn, args=None):
    """Run a command and return its reply, without letting it exit the process."""
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    appctl._RESPONDED = False
    try:
        with redirect_stdout(buf):
            fn(args or Args())
    except SystemExit:
        pass
    return json.loads(buf.getvalue().strip().splitlines()[-1])


class EndpointCase(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(self.path)
        self.real = royalty_config.CONFIG
        royalty_config.CONFIG = self.path
        royalty_config.invalidate()
        self.market = os.environ.get("ADS_MARKET")

    def tearDown(self):
        royalty_config.CONFIG = self.real
        royalty_config.invalidate()
        os.environ["ADS_MARKET"] = self.market or "US"
        if os.path.exists(self.path):
            os.unlink(self.path)


class ReadTests(EndpointCase):
    def test_every_built_in_row_is_reported(self):
        d = call(appctl.cmd_royalties)["data"]
        self.assertTrue(d["editable"])
        self.assertEqual(len(d["tee_prices"]), len(products.US_TEE_ROYALTY_CENTS))
        self.assertEqual(len(d["product_types"]), len(products.PRODUCT_ECON))

    def test_a_row_says_where_its_number_came_from(self):
        """The operator must be able to see which numbers they changed."""
        d = call(appctl.cmd_royalties)["data"]
        self.assertTrue(all(r["source"] == "built-in" for r in d["tee_prices"]))
        royalty_config.set_tee_price(2199, 700, note="rate change")
        rows = {r["price_cents"]: r for r in call(appctl.cmd_royalties)["data"]["tee_prices"]}
        self.assertEqual(rows[2199]["source"], "operator")
        self.assertEqual(rows[2199]["note"], "rate change")
        self.assertEqual(rows[2299]["source"], "built-in")

    def test_the_basis_sentence_counts_the_rows_it_describes(self):
        """The caption and the per-row badges must say the same thing.

        DE reads "worked out from your product export" over 13 rows badged
        built-in and one badged derived. The operator opening that screen to
        judge a royalty is told to go re-export a catalogue that cannot change
        13 of the 14 numbers (found 2026-08-24).
        """
        rows = [{"source": "built-in"}] * 13 + [{"source": "derived"}]
        basis = appctl._royalty_basis(rows)
        self.assertIn("the built-in table for 13", basis)
        self.assertIn("your product export for 1", basis)

    def test_one_source_needs_no_arithmetic(self):
        basis = appctl._royalty_basis([{"source": "built-in"}] * 4)
        self.assertEqual(basis, "the built-in table, with your edits on top")

    def test_the_live_reply_agrees_with_its_own_badges(self):
        d = call(appctl.cmd_royalties)["data"]
        sources = {r["source"] for r in d["tee_prices"] + d["product_types"]}
        self.assertEqual(sources, {"built-in"}, "fixture drifted")
        self.assertIn("built-in", d["basis"])
        self.assertNotIn("export", d["basis"])

    def test_break_even_is_reported_for_every_row(self):
        d = call(appctl.cmd_royalties)["data"]
        for r in d["tee_prices"] + d["product_types"]:
            self.assertTrue(0 < r["break_even"] < 1, r)

    def test_a_corrupt_override_is_surfaced_not_hidden(self):
        with open(self.path, "w") as f:
            json.dump({"product_types": {"mug": {"royalty": 99.0, "price": 18.99}}}, f)
        royalty_config.invalidate()
        self.assertTrue(call(appctl.cmd_royalties)["data"]["errors"])


class WriteTests(EndpointCase):
    def test_a_tee_price_round_trips(self):
        r = call(appctl.cmd_royalty_set, Args(price="25.99", royalty="10.07"))
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"]["royalty_cents"], 1007)
        rows = {x["price_cents"]: x for x in call(appctl.cmd_royalties)["data"]["tee_prices"]}
        self.assertEqual(rows[2599]["royalty"], 10.07)

    def test_a_product_type_round_trips(self):
        r = call(appctl.cmd_royalty_set, Args(type="mug", price="18.99", royalty="3.00"))
        self.assertTrue(r["ok"])
        rows = {x["product_type"]: x for x in call(appctl.cmd_royalties)["data"]["product_types"]}
        self.assertEqual(rows["mug"]["royalty"], 3.0)
        self.assertEqual(rows["mug"]["source"], "operator")

    def test_an_impossible_royalty_is_refused_and_nothing_is_written(self):
        r = call(appctl.cmd_royalty_set, Args(type="mug", price="18.99", royalty="25.00"))
        self.assertFalse(r["ok"])
        self.assertIn("cannot be at or above", r["error"])
        self.assertEqual(royalty_config.load()["product_types"], {})

    def test_missing_inputs_are_refused(self):
        self.assertFalse(call(appctl.cmd_royalty_set, Args(type="mug", price="18.99"))["ok"])
        self.assertFalse(call(appctl.cmd_royalty_set, Args(royalty="3.00"))["ok"])

    def test_clearing_restores_the_built_in_number(self):
        call(appctl.cmd_royalty_set, Args(type="mug", price="18.99", royalty="3.00"))
        self.assertTrue(call(appctl.cmd_royalty_clear, Args(type="mug"))["data"]["cleared"])
        rows = {x["product_type"]: x for x in call(appctl.cmd_royalties)["data"]["product_types"]}
        self.assertEqual(rows["mug"]["royalty"], SHIPPED_MUG)
        self.assertEqual(rows["mug"]["source"], "built-in")

    def test_clearing_something_that_was_never_set_is_not_an_error(self):
        self.assertFalse(call(appctl.cmd_royalty_clear, Args(type="mug"))["data"]["cleared"])


class OtherMarketsAreEditableTests(EndpointCase):
    """Amazon fixes a maximum price per product per market, so the figure the
    operator reads off the Merch dashboard is definitive. It must beat whatever
    median derive_econ.py worked out of the export."""

    def setUp(self):
        super().setUp()
        os.environ["ADS_MARKET"] = "DE"

    def test_a_saved_royalty_wins_over_the_derived_one(self):
        r = call(appctl.cmd_royalty_set,
                 Args(type="standard_tshirt", price="17.99", royalty="2.70"))
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["data"]["market"], "DE")
        rows = {x["product_type"]: x for x in call(appctl.cmd_royalties)["data"]["product_types"]}
        self.assertEqual(rows["standard_tshirt"]["price"], 17.99)
        self.assertEqual(rows["standard_tshirt"]["royalty"], 2.70)
        self.assertEqual(rows["standard_tshirt"]["source"], "operator")

    def test_break_even_follows_the_saved_numbers(self):
        call(appctl.cmd_royalty_set,
             Args(type="standard_tshirt", price="17.99", royalty="2.70"))
        rows = {x["product_type"]: x for x in call(appctl.cmd_royalties)["data"]["product_types"]}
        self.assertAlmostEqual(rows["standard_tshirt"]["break_even"], 2.70 / 17.99, places=4)

    def test_an_edit_here_does_not_touch_another_market(self):
        call(appctl.cmd_royalty_set,
             Args(type="standard_tshirt", price="17.99", royalty="2.70"))
        os.environ["ADS_MARKET"] = "FR"
        self.assertEqual(royalty_config.load("FR")["product_types"], {})

    def test_an_impossible_royalty_is_still_refused(self):
        r = call(appctl.cmd_royalty_set,
                 Args(type="standard_tshirt", price="17.99", royalty="20.00"))
        self.assertFalse(r["ok"])
        self.assertEqual(royalty_config.load("DE")["product_types"], {})

    def test_clearing_hands_the_row_back_to_the_export(self):
        """Cleared means the overlay stops answering for it, so products.get_econ
        falls through to whatever derive_econ banked. (The endpoint's own read
        cannot be asserted here: db.DB_PATH is fixed at import, so switching
        ADS_MARKET inside one process does not repoint the database. Every real
        invocation is a fresh process.)"""
        call(appctl.cmd_royalty_set,
             Args(type="standard_tshirt", price="17.99", royalty="2.70"))
        self.assertIn("standard_tshirt", royalty_config.load("DE")["product_types"])
        r = call(appctl.cmd_royalty_clear, Args(type="standard_tshirt"))
        self.assertTrue(r["data"]["cleared"])
        self.assertEqual(r["data"]["market"], "DE")
        self.assertEqual(royalty_config.load("DE")["product_types"], {})

    def test_there_is_no_tee_ladder_outside_the_us(self):
        d = call(appctl.cmd_royalties)["data"]
        self.assertTrue(d["editable"])
        self.assertEqual(d["tee_prices"], [],
                         "a tee earns one royalty here, not one per rung")

    def test_editing_by_price_is_refused_where_there_is_no_ladder(self):
        r = call(appctl.cmd_royalty_set, Args(price="17.99", royalty="2.70"))
        self.assertFalse(r["ok"])
        self.assertIn("product type", r["error"])


if __name__ == "__main__":
    unittest.main()
