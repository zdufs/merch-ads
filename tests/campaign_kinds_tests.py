#!/usr/bin/env python3
"""Campaign-kind classification: one classifier, no second copy.

Run from the Ads folder:  python3 -m unittest tests.campaign_kinds_tests -v

There used to be two implementations — one in appctl, one added to the halo
estimator. Two copies of "what kind of campaign is this?" drift, and then a
campaign is bid on by one set of rules and reported under another.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(HERE, "engine")
sys.path.insert(0, ENGINE)
os.environ["ADS_MARKET"] = "US"

import campaign_kinds  # noqa: E402


class Classify(unittest.TestCase):
    def test_lottery_us_and_eu_naming(self):
        for name in ("Lotto 3", "Lottery 12", "LOTTO - Tees 4", "lotto 9"):
            self.assertEqual(campaign_kinds.classify(name), "lottery", name)

    def test_harvested(self):
        self.assertEqual(campaign_kinds.classify("Harvested Exact - tees"), "harvested")

    def test_standard_is_the_fallback(self):
        for name in ("My hand-made campaign", "", None, "Lottery-adjacent thing"):
            self.assertEqual(campaign_kinds.classify(name), "standard", repr(name))

    def test_lottery_needs_the_whole_first_word(self):
        """'Lotteryish' is not a lottery campaign — the test is the first WORD,
        so a design name that merely starts with those letters is not swept in."""
        self.assertEqual(campaign_kinds.classify("Lotteryish tees"), "standard")

    def test_a_retired_strategys_campaigns_classify_as_standard(self):
        """Campaigns from a retired strategy are no longer special-cased by name.
        They are skipped on STATE instead, which is the real reason not to touch
        them: they are archived, and Amazon rejects writes to archived entities."""
        self.assertEqual(campaign_kinds.classify("RETIRED - dog - B0EXAMPLE1"), "standard")


class NoSecondCopy(unittest.TestCase):
    def test_nothing_reimplements_the_classifier(self):
        """appctl and halo must call campaign_kinds, not carry their own ladder."""
        import re
        for mod in ("appctl.py", "halo.py"):
            src = open(os.path.join(ENGINE, mod)).read()
            self.assertNotRegex(
                src, r'\breturn\s+"lottery"',
                f"{mod} looks like it reimplements classify(); call campaign_kinds instead")


if __name__ == "__main__":
    unittest.main()
