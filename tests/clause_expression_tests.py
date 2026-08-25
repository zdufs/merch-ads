#!/usr/bin/env python3
"""The auto-targeting expression map must mean what Amazon means by it.

Amazon's own report labels the two query clauses like this — proved by joining
the `targets` mirror (which stores the enum the engine wrote) to
`targeting_perf` (which stores Amazon's own label) on target_id, US, 2026-08-20:

    Amazon says close-match = QUERY_HIGH_REL_MATCHES   (35,516 clauses)
    Amazon says loose-match = QUERY_BROAD_REL_MATCHES  ( 7,069 clauses)

The map in lottery.py had those two swapped, so every lottery campaign launched
with the HIGH starting bid on loose match (the wide, low-intent clause) and the
LOW one on close match. Europe shows the bill: DE loose match spent $97.48
against close match's $7.14.

Run from the Ads folder:  python3 -m unittest tests.clause_expression_tests -v
No Amazon API, no production DB.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ.setdefault("ADS_MARKET", "US")

import lottery                        # noqa: E402


# What Amazon means. Do not edit this without a report row that says otherwise.
AMAZON = {
    "close-match": "QUERY_HIGH_REL_MATCHES",
    "loose-match": "QUERY_BROAD_REL_MATCHES",
    "substitutes": "ASIN_SUBSTITUTE_RELATED",
    "complements": "ASIN_ACCESSORY_RELATED",
}


class ExpressionMeaningTests(unittest.TestCase):
    def test_the_builder_map_matches_amazon(self):
        self.assertEqual(
            lottery.EXPRESSION_TYPE, AMAZON,
            "lottery.EXPRESSION_TYPE must use Amazon's meaning of close/loose match")

    def test_close_match_is_high_relevance(self):
        """The named trap. HIGH relevance is the CLOSE one."""
        self.assertEqual(lottery.EXPRESSION_TYPE["close-match"], "QUERY_HIGH_REL_MATCHES")
        self.assertEqual(lottery.EXPRESSION_TYPE["loose-match"], "QUERY_BROAD_REL_MATCHES")

    def test_no_second_copy_of_the_map_exists(self):
        """inspect_lotto_bids kept its own copy of the map and it drifted with
        the original. One source of truth or the screens disagree."""
        import pathlib
        src = pathlib.Path(HERE, "engine", "inspect_lotto_bids.py").read_text()
        self.assertNotIn(
            '"QUERY_BROAD_REL_MATCHES":', src,
            "inspect_lotto_bids must derive its names from lottery.EXPRESSION_TYPE")

    def test_close_match_is_bid_above_loose_match(self):
        """The whole point of the fix: the tighter clause pays more."""
        bids = lottery.US_CLAUSE_BIDS
        self.assertGreater(bids["close-match"], bids["loose-match"])
        self.assertGreater(bids["loose-match"], bids["substitutes"])


class RemovedProductTests(unittest.TestCase):
    """Oversized T-Shirt and Samsung Galaxy Case were removed 2026-08-21 — the
    operator does not sell them. A type the engine cannot price must not sit in
    the tables inviting a bid, and its export label must resolve to nothing so a
    listing is SKIPPED rather than guessed into a cohort."""

    REMOVED = ("oversized_tshirt", "phone_case_samsung_galaxy", "quarter_zip")

    def test_neither_type_is_priced_any_more(self):
        import products
        for t in self.REMOVED:
            self.assertNotIn(t, products.PRODUCT_ECON, t)

    def test_no_export_label_resolves_to_them(self):
        import products
        for label in ("Oversized T-Shirt", "oversized t shirt", "Oversized Tshirt",
                      "Samsung Galaxy Case", "Galaxy Case", "samsung galaxy case",
                      "Quarter Zip", "quarter zip sweatshirt"):
            self.assertNotIn(products.type_from_export_label(label), self.REMOVED, label)

    def test_no_engine_map_still_names_them(self):
        """A stale entry in a format-group or campaign-label map would build a
        cohort for a product that has no economics."""
        import pathlib
        for name in ("preempt.py", "phase4_harvest_create.py", "products.py"):
            src = pathlib.Path(HERE, "engine", name).read_text()
            for t in self.REMOVED:
                self.assertNotIn(f'"{t}"', src, f"{name} still names {t}")


class OperatorTypeSpellingTests(unittest.TestCase):
    """An operator names a type the way their dashboard does — "Performance
    quarter-zip", "Baseball Jersey". The matcher lowercased the export label but
    NOT the type it compared against, so every capital letter fell outside
    [a-z0-9] and only the one exact spelling matched. A royalty they had entered
    then silently did nothing.
    """

    def setUp(self):
        import royalty_config
        import tempfile
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(self.path)
        self.real = royalty_config.CONFIG
        royalty_config.CONFIG = self.path
        royalty_config.invalidate()
        royalty_config.set_product_type("Performance quarter-zip",
                                        royalty=5.51, price=24.99, market="US")

    def tearDown(self):
        import royalty_config
        royalty_config.CONFIG = self.real
        royalty_config.invalidate()
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_any_reasonable_spelling_finds_the_operator_type(self):
        import products
        for label in ("Performance quarter-zip", "Performance Quarter-Zip",
                      "performance quarter zip", "PERFORMANCE QUARTER ZIP",
                      "Performance  Quarter  Zip"):
            self.assertEqual(products.type_from_export_label(label),
                             "Performance quarter-zip", label)

    def test_a_different_product_still_does_not_match(self):
        import products
        for label in ("Quarter Zip", "Performance Hoodie Zip", "Zip Hoodie"):
            self.assertNotEqual(products.type_from_export_label(label),
                                "Performance quarter-zip", label)


if __name__ == "__main__":
    unittest.main()
