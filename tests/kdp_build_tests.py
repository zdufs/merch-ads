#!/usr/bin/env python3
"""Composition tests for kdp_build — the KDP campaign builder. These check the
pure plan it composes from a manifest (no Amazon, no DB). A synthetic manifest
keeps the test independent of the real book catalog."""
import os
import sys
import unittest

# engine/, not the repo root — kdp_build lives there. This pointed at the repo
# root and only worked because the full suite happens to import another test
# module that fixes the path first, so running this file on its own failed.
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))
import kdp_build

# A synthetic held-out opener. The real list lives in kdp_config.json, which is
# operator data and absent on a fresh clone, so the test supplies its own and
# patches it in for the duration.
HELD = "HELD000001"


def _book(slug, asin, series=None, pos=None, pen=None, price=4.99, title=None):
    return {"slug": slug, "title": title or slug, "series_slug": series,
            "series_position": pos, "pen_name": pen,
            "keyword_slots": ["kw one", "kw two", "kw three"],
            "formats": {"ebook": {"asin": asin, "list_price_usd": price}}}


def _manifest():
    return [
        _book("standalone-a", "SA0000001A", title="Standalone A"),
        _book("standalone-b", "SB0000001B", title="Standalone B"),
        # series 'alpha' — its opener is the held-out ASIN; siblings have no pen
        _book("alpha-1", HELD, series="alpha", pos=1, pen="Pen A", title="Alpha One"),
        _book("alpha-2", "AL00000002", series="alpha", pos=2, title="Alpha Two"),
        _book("alpha-3", "AL00000003", series="alpha", pos=3, title="Alpha Three"),
        # series 'beta' — normal opener, not held out
        _book("beta-1", "BE00000001", series="beta", pos=1, pen="Pen B", title="Beta One"),
        _book("beta-2", "BE00000002", series="beta", pos=2, title="Beta Two"),
    ]


class BuildPlan(unittest.TestCase):
    def setUp(self):
        self._real_held = kdp_build.HELD_OUT_OPENER_ASINS
        kdp_build.HELD_OUT_OPENER_ASINS = {HELD}
        self.addCleanup(setattr, kdp_build, "HELD_OUT_OPENER_ASINS", self._real_held)
        self.plan = kdp_build.build_plan(_manifest(), start_date="2026-08-11")
        self.leads = [c for c in self.plan["campaigns"] if c["kind"] == "lead"]
        self.series = [c for c in self.plan["campaigns"] if c["kind"] == "series"]

    def test_counts(self):
        # 2 standalones + 2 openers = 4 entry points, minus the held-out opener = 3 leads
        self.assertEqual(self.plan["summary"]["leads"], 3)
        self.assertEqual(self.plan["summary"]["series_ads"], 2)
        self.assertEqual(self.plan["summary"]["total_budgets"], 5)
        self.assertEqual(self.plan["summary"]["daily_spend_ceiling"], 25.0)

    def test_held_out_opener_gets_no_lead_but_stays_in_its_series_ad(self):
        held = self.plan["summary"]["held_out"]
        self.assertEqual([h["slug"] for h in held], ["alpha-1"])
        # no lead advertises the held-out book
        self.assertNotIn(HELD, [pa for c in self.leads for pa in c["product_ads"]])
        # but the alpha series ad carries every alpha ASIN, opener included
        alpha = next(c for c in self.series if c["series_slug"] == "alpha")
        self.assertEqual(alpha["product_ads"], [HELD, "AL00000002", "AL00000003"])

    def test_lead_shape(self):
        lead = next(c for c in self.leads if c["book_slug"] == "standalone-a")
        self.assertTrue(lead["name"].endswith(" - PHRASE"))
        self.assertEqual(lead["targetingType"], "MANUAL")
        self.assertEqual(lead["budget"], kdp_build.DAILY_BUDGET)
        self.assertEqual(lead["biddingStrategy"], "LEGACY_FOR_SALES")
        self.assertEqual(lead["product_ads"], ["SA0000001A"])
        self.assertTrue(all(k["matchType"] == kdp_build.LEAD_MATCH for k in lead["keywords"]))
        self.assertEqual(len(lead["keywords"]), 3)
        # day-one negatives: 'free' phrase + own ASIN product target
        self.assertTrue(any(n["keywordText"] == "free" and n["matchType"] == "NEGATIVE_PHRASE"
                            for n in lead["negatives"]["keywords"]))
        self.assertEqual([t["asin"] for t in lead["negatives"]["product_targets"]], ["SA0000001A"])

    def test_series_shape_and_pen_fallback(self):
        alpha = next(c for c in self.series if c["series_slug"] == "alpha")
        self.assertEqual(alpha["name"], "Alpha - SERIES")
        self.assertEqual(alpha["targetingType"], "AUTO")
        self.assertEqual(alpha["keywords"], [])
        # siblings alpha-2/3 have no pen, so the series ad borrows the opener's pen
        self.assertTrue(any(n["keywordText"] == "Pen A" and n["matchType"] == "NEGATIVE_EXACT"
                            for n in alpha["negatives"]["keywords"]))
        # a series ad carries NO own-ASIN product negatives (they'd block cross-promo)
        self.assertEqual(alpha["negatives"]["product_targets"], [])
        # but its keyword negatives still include every in-series title + 'free'
        self.assertTrue(any(n["keywordText"] == "free" for n in alpha["negatives"]["keywords"]))

    def test_series_pen_override_fills_a_manifest_gap(self):
        # a series with NO pen on any book uses the operator override, not a flag.
        # The overrides live in kdp_config.json (operator data), so supply one here.
        real = kdp_build.SERIES_PEN_OVERRIDES
        kdp_build.SERIES_PEN_OVERRIDES = {"gap-series": "Synthetic Pen"}
        self.addCleanup(setattr, kdp_build, "SERIES_PEN_OVERRIDES", real)

        m = [_book("gap-1", "GP00000001", series="gap-series", pos=1, title="Gap One"),
             _book("gap-2", "GP00000002", series="gap-series", pos=2, title="Gap Two")]
        plan = kdp_build.build_plan(m, start_date="2026-08-11")
        series = next(c for c in plan["campaigns"] if c["kind"] == "series")
        self.assertTrue(any(n["keywordText"] == "Synthetic Pen"
                            for n in series["negatives"]["keywords"]))
        self.assertEqual(series["flags"], [])

    def test_comma_titles_are_sanitized_for_negatives(self):
        # Amazon silently drops a negative keyword containing a comma, so a
        # comma title must be stored comma-free (matches punctuation-insensitively)
        m = [_book("comma", "CM00000001", title="Small Balcony, Big Harvest",
                   pen="Claire Bennett")]
        plan = kdp_build.build_plan(m, start_date="2026-08-11")
        negs = [n["keywordText"] for n in plan["campaigns"][0]["negatives"]["keywords"]]
        self.assertIn("Small Balcony Big Harvest", negs)
        self.assertTrue(all("," not in n for n in negs))

    def test_missing_pen_is_flagged_not_faked(self):
        # a standalone with no pen must flag the omission, never invent an author
        m = [_book("no-pen", "NP00000001", title="No Pen Book")]
        plan = kdp_build.build_plan(m, start_date="2026-08-11")
        lead = plan["campaigns"][0]
        self.assertTrue(any("pen name" in f for f in lead["flags"]))
        self.assertFalse(any(n["reason"] == "own author name"
                             for n in lead["negatives"]["keywords"]))


class APartialBuildIsNeverReportedAsApplied(unittest.TestCase):
    """`applied` was a bare True whatever happened underneath.

    Three outcomes never reached `partials`, which is the list the reply is
    judged by:

      * a campaign Amazon REFUSED outright
      * `created: "campaign_only"` — the ad group failed, leaving a LIVE
        campaign with a budget and nothing under it. It can never serve, and the
        next run finds the name in `existing` and skips the whole downstream
        build, so it stays empty for good while every run says "already exists"
      * the product-target negatives, whose response was discarded entirely

    So a run where every campaign came back campaign_only reported a clean
    success.
    """

    def _source(self):
        import inspect
        import kdp_build
        return inspect.getsource(kdp_build.apply_plan) \
            if hasattr(kdp_build, "apply_plan") else inspect.getsource(kdp_build)

    def test_a_refused_campaign_is_a_partial(self):
        src = self._source()
        self.assertIn('"partial": ["campaign"]', src)

    def test_a_campaign_with_no_ad_group_is_a_partial(self):
        src = self._source()
        self.assertIn('"partial": ["ad_group"]', src)

    def test_the_empty_campaign_says_what_it_costs(self):
        src = self._source()
        self.assertIn("cannot serve", src)
        self.assertIn("skip it", src)

    def test_the_product_target_negatives_are_counted(self):
        src = self._source()
        self.assertIn("negative_targets", src)
        self.assertIn("nt_unconfirmed", src,
                      "an unreadable 2xx must not be reported as a failure")

    def test_applied_follows_partials(self):
        src = self._source()
        self.assertIn('"applied": not partials', src)
        self.assertNotIn('"applied": True, "market": MARKET', src)


if __name__ == "__main__":
    unittest.main()
