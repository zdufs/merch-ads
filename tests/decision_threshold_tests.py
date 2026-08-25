#!/usr/bin/env python3
"""The numbers that decide what gets paused, negated and bid.

Found by mutation on 2026-08-24: every one of the constants below could be
changed — some by a factor of ten, some to a value that inverts what the rule
means — and NOTHING in the suite failed. These run on AUTO, nightly, across
seven live markets, with nobody watching.

They are not tuning parameters. Each is a decision about how much evidence is
enough before real money moves, so each is pinned here with the reason it holds
that value. A deliberate change fails this file and is re-argued; an accidental
one stops being silent.

`phase3_bids.UP` was the gap in the first version: the file pinned DOWN,
MIN_BID and MAX_BID and left out the one factor that RAISES a bid, which is the
only one here that can spend more money on its own. The two KDP constants were
the opposite mistake — pinned by value under a class that said "ceilings" while
neither clamps anything. Both are addressed below.

WHAT ALREADY HAS A BACKSTOP, AND WHAT DOES NOT — the reason this file leads
with the bid factors rather than the ceilings:

  * `MAX_BID` is the mildest of the nine. `ads_client._apply_ceiling` clamps
    every write to the market ceiling, so raising MAX_BID cannot actually
    overbid a capped market.
  * The click thresholds have a partial backstop. Lowering them makes a rule
    match far more rows, and `db.AUTO_CHANGE_CAP_DEFAULT` refuses a run over
    500 changes rather than truncating it.
  * `DOWN` and `MIN_BID` have NEITHER. The ceiling clamps upward only
    (`if b > cap`), the volume cap counts changes and not their size, and a
    collapse writes exactly as many rows as a nudge. A bid-down factor of 0.15
    cuts 85% off every bid it touches and passes every guard in the engine.

Run from the Ads folder:  python3 -m unittest tests.decision_threshold_tests -v
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ.setdefault("ADS_MARKET", "US")

import appctl          # noqa: E402
import harvest_prune   # noqa: E402
import kdp_build       # noqa: E402
import phase2_apply    # noqa: E402
import phase3_bids     # noqa: E402
import products        # noqa: E402


class BidMovesAreNudgesNotCollapses(unittest.TestCase):
    """The two with no runtime backstop at all."""

    def test_a_bid_down_takes_fifteen_percent_off_not_eighty_five(self):
        self.assertAlmostEqual(phase3_bids.DOWN, 0.85, places=4)

    def test_the_down_factor_is_a_trim(self):
        """Stated as a range as well as a value: any factor this far from 1.0
        is a collapse, whatever number replaces it."""
        self.assertLess(phase3_bids.DOWN, 1.0, "a bid-DOWN must reduce the bid")
        self.assertGreaterEqual(
            phase3_bids.DOWN, 0.5,
            "a single automatic step must never halve a bid — phase 3 runs "
            "nightly, so repeated steps compound on their own")

    def test_a_bid_up_adds_ten_percent_not_a_multiple(self):
        """The one factor in this file that was pinned by nothing at all.

        `UP` raises a converting target's bid every review, so it is the only
        constant here that can spend MORE money on its own. The bid ceiling is
        a partial backstop and only a partial one: it clamps at the market cap,
        which US sets at $0.50 and the five EU markets had unset for months.
        """
        self.assertAlmostEqual(phase3_bids.UP, 1.10, places=4)

    def test_the_up_factor_is_a_nudge_in_the_right_direction(self):
        self.assertGreater(phase3_bids.UP, 1.0, "a bid-UP must raise the bid")
        self.assertLessEqual(
            phase3_bids.UP, 1.5,
            "a single automatic step must never add half a bid again — phase 3 "
            "reviews weekly and the steps compound")

    def test_the_two_factors_are_the_same_size_of_step(self):
        """Up 10% and down 15% are both trims. A pair that is wildly uneven
        walks every bid one way over a few weeks with nothing to say so."""
        self.assertLess(abs((phase3_bids.UP - 1.0) - (1.0 - phase3_bids.DOWN)),
                        0.25)

    def test_the_bid_floor_can_still_win_an_auction(self):
        self.assertAlmostEqual(phase3_bids.MIN_BID, 0.10, places=4)
        self.assertGreaterEqual(
            phase3_bids.MIN_BID, 0.05,
            "below this a bid stops serving, which reads as a design that "
            "stopped selling rather than one that stopped bidding")

    def test_the_bid_band_is_the_right_way_round(self):
        self.assertLess(phase3_bids.MIN_BID, phase3_bids.MAX_BID)
        self.assertAlmostEqual(phase3_bids.MAX_BID, 1.50, places=4)


class EnoughEvidenceBeforeMoneyMoves(unittest.TestCase):

    def test_a_search_term_is_negated_only_after_ten_clicks(self):
        """A negative is PERMANENT. Ten clicks with no order is the bar."""
        self.assertEqual(phase2_apply.MIN_CLICKS_NEG, 10)

    def test_a_design_is_paused_only_after_twenty_clicks(self):
        self.assertEqual(phase2_apply.MIN_CLICKS_PAUSE, 20)

    def test_pausing_needs_more_evidence_than_negating(self):
        """Pausing stops a whole design; negating removes one term from one ad
        group. The order of these two is the policy, not the values."""
        self.assertGreater(phase2_apply.MIN_CLICKS_PAUSE,
                           phase2_apply.MIN_CLICKS_NEG)

    def test_phase_three_moves_a_bid_only_after_twenty_clicks(self):
        self.assertEqual(phase3_bids.DEFAULT_MIN_CLICKS, 20)

    def test_a_harvested_keyword_is_pruned_only_after_fifteen_clicks(self):
        self.assertEqual(harvest_prune.MIN_CLICKS, 15)


class TheConversionFloorIsAFloor(unittest.TestCase):
    """8% — a design converting below it is not earning its clicks. Doubling
    the digit to 0.80 would put almost the whole account below the floor."""

    def test_phase_two_floor(self):
        self.assertAlmostEqual(phase2_apply.CVR_FLOOR, 0.08, places=4)

    def test_harvest_prune_floor(self):
        self.assertAlmostEqual(harvest_prune.CVR_FLOOR, 0.08, places=4)

    def test_the_two_floors_agree(self):
        """They mean the same thing and drifting apart would be silent."""
        self.assertAlmostEqual(phase2_apply.CVR_FLOOR,
                               harvest_prune.CVR_FLOOR, places=6)

    def test_every_floor_is_a_rate_not_a_percentage(self):
        """0.08 is 8%. An 8 typed here instead would put every design above the
        floor and switch the rule off in silence."""
        for label, value in (("phase2", phase2_apply.CVR_FLOOR),
                             ("harvest_prune", harvest_prune.CVR_FLOOR)):
            with self.subTest(module=label):
                self.assertLess(value, 1.0, f"{label}: CVR is a fraction")
                self.assertGreater(value, 0.0)


class TheKillListFloorIsTheSameEightPercent(unittest.TestCase):

    def test_the_kill_list_floor(self):
        self.assertAlmostEqual(appctl.FLOOR_CVR, 0.08, places=4)

    def test_it_agrees_with_the_rules_that_act_on_it(self):
        """The screen names designs the automatic rules then pause. Two
        different floors would name a different set from the one that moves."""
        self.assertAlmostEqual(appctl.FLOOR_CVR, phase2_apply.CVR_FLOOR,
                               places=6)


class BookAdsAreLaunchedInsideTheirOwnNumbers(unittest.TestCase):
    """KDP is a separate advertiser, so these are not the tee numbers.

    The class used to be called "…HaveTheirOwnCeilings" and pinned the two
    values on their own. Neither clamps anything: `BID_CEILING` is marked
    "(informational)" where it is defined, and both it and `MONTHLY_CAP` are
    read only into the settings block the plan REPORTS. A test that pins a
    number nothing enforces reads exactly like one guarding a real limit.

    So the ceiling is asked of the PLAN instead — every bid a build proposes
    has to be inside it — and the cap is checked where it does its only job,
    which is being stated in the summary the operator reads. The real clamp on
    a live write is `ads_client._apply_ceiling` against the market ceiling in
    `engine_meta`, which is a different mechanism entirely.
    """

    BOOKS = [{"slug": "a-book", "title": "A Book", "series_slug": "ser",
              "series_position": 1, "keyword_slots": ["one two", "three"],
              "formats": {"ebook": {"asin": "B00TEST001"}}}]

    def _plan(self):
        return kdp_build.build_plan(self.BOOKS, start_date="2026-08-24")

    def test_the_book_bid_ceiling(self):
        self.assertAlmostEqual(kdp_build.BID_CEILING, 0.65, places=4)

    def test_no_bid_in_a_built_plan_is_over_the_ceiling(self):
        """The assertion that makes the constant mean something."""
        plan = self._plan()
        bids = []

        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key in ("bid", "defaultBid") and isinstance(value, (int, float)):
                        bids.append((key, value))
                    else:
                        walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(plan["campaigns"])
        self.assertTrue(bids, "the plan carried no bids — this measured nothing")
        for key, value in bids:
            self.assertLessEqual(value, kdp_build.BID_CEILING,
                                 f"{key} {value} is over the book bid ceiling")

    def test_the_book_monthly_spend_cap(self):
        self.assertAlmostEqual(kdp_build.MONTHLY_CAP, 500.0, places=2)

    def test_the_plan_states_the_cap_it_cannot_enforce(self):
        """Nothing stops the spend, so saying so is the whole mechanism. A cap
        that quietly left the reply would be a cap nobody was told about."""
        plan = self._plan()
        self.assertAlmostEqual(kdp_build.MONTHLY_CAP,
                               plan["summary"]["monthly_cap"], places=2)
        self.assertAlmostEqual(kdp_build.BID_CEILING,
                               plan["settings"]["bid_ceiling"], places=4)

    def test_a_book_start_bid_is_under_its_ceiling(self):
        self.assertLess(kdp_build.START_BID, kdp_build.BID_CEILING)


class TheRankPushFloorIsTheTeeListPrice(unittest.TestCase):
    """A US tee priced BELOW this is deliberately discounted to buy velocity,
    and is acted on with the $19.99 economics so the rules do not pause the
    campaigns the price cut was meant to feed. Moving the floor silently
    removes that protection from every tee between the two values."""

    def test_the_growth_floor_is_nineteen_ninety_nine(self):
        self.assertEqual(products.US_TEE_GROWTH_FLOOR_CENTS, 1999)

    def test_the_floor_is_a_real_rung_of_the_tee_ladder(self):
        self.assertIn(products.US_TEE_GROWTH_FLOOR_CENTS,
                      products.US_TEE_ROYALTY_CENTS,
                      "a floor that is not a priced rung protects nothing")


if __name__ == "__main__":
    unittest.main()
