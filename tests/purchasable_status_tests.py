#!/usr/bin/env python3
"""Pricing a design you ALREADY advertise, and choosing a NEW one, ask different
questions about the same `status` column. The bars are deliberately different.

A MerchFlow "all products" export carries every listing the account has ever
had. `map_products` required `status == "published"`, so a listing in any other
state got no list price, and no list price means no break-even, and no
break-even means every economics rule skips it. Not paused, not flagged, not
counted — exempt.

That was wrong for listings that are still for sale. The operator confirmed it
on 2026-08-22 and the export's own 30-day sales agree:

    timed_out    569 listings,  20 units
    locked       114 listings, 348 units   <- sells harder per listing than published
    propagated   147 listings,   1 unit
    published    477k listings, 758 units
    deleted_*    317k listings,   6 units  <- the attribution tail
    publishing   801 listings,    0 units  <- not live yet
    review        14 listings,    0 units

So `map_products` prices anything PURCHASABLE. The builders that pick NEW
designs to advertise still require `published`, because starting a campaign on a
locked or timed-out design is a different decision from continuing to manage one.

Run from the Ads folder:  python3 -m unittest tests.purchasable_status_tests -v
"""

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ.setdefault("ADS_MARKET", "US")

import products  # noqa: E402

# Chooses which designs to START advertising — must stay strict.
PICKS_NEW_DESIGNS = ["lottery_build.py", "scavenger_build.py", "appctl.py"]


class WhatCountsAsPurchasable(unittest.TestCase):

    def test_a_listing_that_still_sells_is_priced(self):
        for status in ("published", "timed_out", "locked", "propagated"):
            with self.subTest(status=status):
                self.assertIn(status, products.PURCHASABLE_STATUSES)

    def test_a_deleted_listing_is_not(self):
        for status in ("deleted_content_creator", "deleted_inactive_no_sales",
                       "deleted_content_policy_violation", "amazon_rejected"):
            with self.subTest(status=status):
                self.assertNotIn(status, products.PURCHASABLE_STATUSES)

    def test_a_listing_that_is_not_live_yet_is_not(self):
        """`publishing` and `review` have prices but have never sold a unit —
        they are pre-launch, not for sale."""
        for status in ("publishing", "review"):
            with self.subTest(status=status):
                self.assertNotIn(status, products.PURCHASABLE_STATUSES)


class TheTwoBarsStayDifferent(unittest.TestCase):

    def test_map_products_prices_anything_purchasable(self):
        src = open(os.path.join(HERE, "engine", "map_products.py"),
                   encoding="utf-8").read()
        self.assertIn("PURCHASABLE_STATUSES", src,
                      "map_products went back to a bare published check, so a "
                      "selling design is exempt from economics again")

    def test_choosing_new_designs_still_requires_published(self):
        """The asymmetry is the point, so it is pinned rather than commented.

        Widening these would start NEW campaigns on locked and timed-out
        designs, which is not what the pricing change was about.
        """
        for name in PICKS_NEW_DESIGNS:
            with self.subTest(module=name):
                src = open(os.path.join(HERE, "engine", name),
                           encoding="utf-8").read()
                self.assertTrue(
                    re.search(r'status["\']\)\s*!=\s*["\']published["\']', src)
                    or 'status") != "published"' in src,
                    f"{name} no longer requires `published` when picking "
                    f"designs to advertise")


class TheEvidenceIsNotLost(unittest.TestCase):
    """The reasoning lives beside the constant, because the next person to read
    `{'published', 'timed_out', 'locked', 'propagated'}` will reasonably think
    the last three are a mistake."""

    def test_the_constant_explains_itself(self):
        src = open(os.path.join(HERE, "engine", "products.py"), encoding="utf-8").read()
        i = src.index("PURCHASABLE_STATUSES")
        preamble = src[max(0, i - 2000):i]
        for token in ("timed_out", "locked", "units"):
            self.assertIn(token, preamble,
                          "the evidence for widening the status filter is no "
                          "longer written next to it")
