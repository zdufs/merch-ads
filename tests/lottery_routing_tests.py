#!/usr/bin/env python3
"""The operator's lottery routing rule, pinned.

Dictated by the operator on 2026-07-03 and committed to the engine and the
handoff: standard tees FILL an existing lottery campaign to 1000 ASINs before
any new campaign is created, and they fill in numeric order.

Found by mutation on 2026-08-24: `US_MAX_ADGROUPS` could be changed from 1000
to 100 and nothing in the suite failed. The number is a business decision, not
a tuning parameter — at 100 the account grows ten times as many campaigns, each
one a thing to budget, name and watch. Nothing about the code says 1000 is
load-bearing, so this file says it.

`fill_plan` is a pure function, so this needs no database and no Amazon.

The class at the bottom is a different subject in the same module: `add_asins`
threw away every create response except its `success` entries, so an ad group
or a product ad Amazon refused left a count and no reason. That is the same
blind spot `scavenger_build.chunked_create` had, and it hid the same thing.

Run from the Ads folder:  python3 -m unittest tests.lottery_routing_tests -v
"""

import io
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ.setdefault("ADS_MARKET", "US")

import lottery          # noqa: E402
import lottery_build    # noqa: E402
import markets          # noqa: E402


def inv(**counts):
    """{campaign number: {"count": n}} the way lotto_inventory returns it."""
    return {int(k[1:]): {"count": v} for k, v in counts.items()}


class TheCapIsTheOperatorsNumber(unittest.TestCase):

    def test_a_us_lottery_campaign_holds_a_thousand(self):
        self.assertEqual(lottery_build.US_MAX_ADGROUPS, 1000)

    def test_the_eu_shard_size_is_unchanged_at_five_hundred(self):
        self.assertEqual(lottery.MAX_ADGROUPS, 500)

    def test_the_cap_follows_the_market(self):
        real = markets.is_default
        try:
            markets.is_default = lambda: True
            self.assertEqual(lottery_build.per_campaign_cap(), 1000)
            markets.is_default = lambda: False
            self.assertEqual(lottery_build.per_campaign_cap(), 500)
        finally:
            markets.is_default = real


class ExistingCampaignsFillBeforeNewOnesAreCreated(unittest.TestCase):

    def test_an_existing_campaign_is_filled_to_the_cap_first(self):
        plan = lottery_build.fill_plan(inv(n3=990), [f"A{i}" for i in range(20)],
                                       cap=1000)
        self.assertEqual(plan[0][0], 3)
        self.assertTrue(plan[0][1], "campaign 3 already exists")
        self.assertEqual(len(plan[0][2]), 10, "only 10 spaces were free")
        self.assertEqual(plan[1][0], 4)
        self.assertFalse(plan[1][1], "the overflow starts a new campaign")
        self.assertEqual(len(plan[1][2]), 10)

    def test_campaigns_fill_in_numeric_order_not_dictionary_order(self):
        """`lotto_inventory` keys are numbers, and 10 sorts before 9 as text."""
        plan = lottery_build.fill_plan(inv(n9=995, n10=0),
                                       [f"A{i}" for i in range(10)], cap=1000)
        self.assertEqual([p[0] for p in plan], [9, 10])
        self.assertEqual(len(plan[0][2]), 5)

    def test_a_full_campaign_is_skipped_rather_than_overfilled(self):
        plan = lottery_build.fill_plan(inv(n1=1000), ["A1"], cap=1000)
        self.assertEqual(plan, [(2, False, ["A1"])])

    def test_new_campaigns_never_exceed_the_cap(self):
        plan = lottery_build.fill_plan({}, [f"A{i}" for i in range(2500)],
                                       cap=1000)
        self.assertEqual([len(p[2]) for p in plan], [1000, 1000, 500])
        self.assertEqual([p[0] for p in plan], [1, 2, 3])
        self.assertTrue(all(not p[1] for p in plan))

    def test_nothing_is_dropped(self):
        """Every ASIN handed in must appear exactly once in the plan."""
        asins = [f"A{i}" for i in range(1234)]
        plan = lottery_build.fill_plan(inv(n1=400, n2=1000), asins, cap=1000)
        placed = [a for _n, _e, take in plan for a in take]
        self.assertEqual(placed, asins)


class ARefusedLotteryBatchSaysWhy(unittest.TestCase):
    """`add_asins` read `success` and dropped the rest of the response.

    `js` and `js2` held the HTTP status and the error block and neither was
    ever read. So a refused ad group and a refused product ad both reached the
    log as a bare count — "ad groups added: 0/100 | product ads: 0" — with no
    way to tell an ineligible ASIN from a quota, a bad bid or an outage.
    """

    def setUp(self):
        self._sleep = lottery_build.time.sleep
        lottery_build.time.sleep = lambda *a, **k: None
        self.addCleanup(setattr, lottery_build.time, "sleep", self._sleep)
        self._patch(lottery_build.db, "log_write", lambda *a, **k: None)
        self._patch(lottery_build, "set_clause_bids", lambda *a, **k: None)

    def _patch(self, obj, name, value):
        old = getattr(obj, name)
        setattr(obj, name, value)
        self.addCleanup(setattr, obj, name, old)

    def run_add(self, ag_response, pa_response):
        class _Client:
            def create_ad_groups(self, items):
                return ag_response

            def create_product_ads(self, items):
                return pa_response

        pairs = [("B0LOT00001", "one"), ("B0LOT00002", "two")]
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            placed = lottery_build.add_asins(_Client(), None, "c1", "LOTTO 9", pairs)
        return placed, out.getvalue(), err.getvalue()

    def test_a_refused_product_ad_names_the_asin_and_the_reason(self):
        ag_ok = (207, {"adGroups": {"success": [{"index": 0, "adGroupId": "ag1"},
                                                {"index": 1, "adGroupId": "ag2"}],
                                    "error": []}})
        pa_bad = (207, {"productAds": {
            "success": [{"index": 0, "adId": "ad1"}],
            "error": [{"index": 1, "cause": {"trigger": "B0LOT00002"},
                       "errors": [{"errorType": "AD_INELIGIBLE",
                                   "errorValue": {"otherError": {
                                       "reason": "ASIN is not eligible"}}}]}]}})
        placed, out, err = self.run_add(ag_ok, pa_bad)
        self.assertEqual(placed, 1)
        self.assertIn("1 of 2 REFUSED", err)
        self.assertIn("B0LOT00002", err)
        self.assertIn("AD_INELIGIBLE", err)
        self.assertIn("LOTTO 9", err)
        self.assertNotIn("AD_INELIGIBLE", out, "diagnostics belong on stderr")

    def test_a_refused_ad_group_is_reported_too(self):
        ag_bad = (207, {"adGroups": {
            "success": [{"index": 0, "adGroupId": "ag1"}],
            "error": [{"index": 1, "cause": {"trigger": "B0LOT00002"},
                       "errors": [{"errorType": "DUPLICATE_VALUE",
                                   "errorValue": {"otherError": {
                                       "reason": "an ad group with that name exists"}}}]}]}})
        pa_ok = (207, {"productAds": {"success": [{"index": 0, "adId": "ad1"}],
                                      "error": []}})
        placed, out, err = self.run_add(ag_bad, pa_ok)
        self.assertEqual(placed, 1)
        self.assertIn("adGroups: 1 of 2 REFUSED", err)
        self.assertIn("DUPLICATE_VALUE", err)

    def test_a_batch_amazon_took_whole_stays_quiet(self):
        ag_ok = (207, {"adGroups": {"success": [{"index": 0, "adGroupId": "ag1"},
                                                {"index": 1, "adGroupId": "ag2"}],
                                    "error": []}})
        pa_ok = (207, {"productAds": {"success": [{"index": 0, "adId": "ad1"},
                                                  {"index": 1, "adId": "ad2"}],
                                      "error": []}})
        placed, out, err = self.run_add(ag_ok, pa_ok)
        self.assertEqual(placed, 2)
        self.assertEqual(err.strip(), "")


if __name__ == "__main__":
    unittest.main()
