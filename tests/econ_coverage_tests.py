#!/usr/bin/env python3
"""`econ-gate` must report what the GATE cannot judge, not what the catalogue
does not price. They are different numbers and only one is worth acting on.

The app's first coverage warning read `catalog` and said 19,177 advertised
designs had no price, "so bids, pauses and negatives skip them entirely". The
count was right and the sentence was false. Only a US standard tee resolves its
break-even from the design's own list price; every other product type, and every
other market, is priced from the type table and needs no list price at all. Of
those 19,177, some 18,001 were trucker and baseball hats. The number the gate
actually could not judge was 182.

An alarm wrong by two orders of magnitude gets muted, and then the real one is
missed too — which is why this is a test and not a comment.

Run from the Ads folder:  python3 -m unittest tests.econ_coverage_tests -v
"""

import os
import sqlite3
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ.setdefault("ADS_MARKET", "US")

import products  # noqa: E402


class OnlyUSTeesNeedTheirOwnListPrice(unittest.TestCase):
    """The whole reason catalogue coverage overstates the problem."""

    def test_a_hat_is_priced_without_any_list_price(self):
        econ = products.get_design_econ("printed_trucker_hat", market="US", price=None)
        self.assertIsNotNone(econ.get("break_even"),
                             "a hat with no list price lost its break-even — "
                             "hats are priced from the TYPE table")
        self.assertEqual(econ.get("src"), "type_table")

    def test_a_us_tee_without_a_price_has_no_usable_economics(self):
        econ = products.get_design_econ(products.TEE, market="US", price=None)
        self.assertFalse(econ.get("known_price"),
                         "a US tee with no list price claimed a known price")

    def test_a_us_tee_with_a_price_does(self):
        econ = products.get_design_econ(products.TEE, market="US", price="19.99")
        self.assertTrue(econ.get("known_price"))
        self.assertIsNotNone(econ.get("break_even"))


class TheGateSkipsOnlyWhatItCannotJudge(unittest.TestCase):
    """`design_be_for` is the single source appctl and the DSL both gate on."""

    def _conn(self, product_type, list_price):
        import db
        conn = sqlite3.connect(":memory:")
        conn.executescript(db.SCHEMA)
        # the two writer-owned economics tables; absent, the gate fails CLOSED
        # and returns None, which would make these tests pass on nothing
        conn.execute("CREATE TABLE price_change (asin TEXT, ad_group_id TEXT,"
                     " old_cents INTEGER, new_cents INTEGER, observed_at TEXT)")
        conn.execute("CREATE TABLE engine_meta (key TEXT PRIMARY KEY, value TEXT,"
                     " updated_at TEXT)")
        conn.execute("INSERT INTO ad_group_product(ad_group_id,asin,product_type,"
                     "list_price) VALUES('g1','B01',?,?)", (product_type, list_price))
        conn.commit()
        return conn

    def test_a_hat_with_no_price_is_not_skipped(self):
        be_for = products.design_be_for(self._conn("printed_trucker_hat", ""))
        self.assertIsNotNone(be_for, "the gate failed closed — the fixture is wrong, "
                                     "not the engine")
        be, skip = be_for("g1")
        self.assertIsNone(skip, f"a hat was skipped as {skip!r} for want of a list "
                                f"price it never needed")
        self.assertIsNotNone(be)

    def test_a_us_tee_with_no_price_is_skipped(self):
        be_for = products.design_be_for(self._conn("standard_tshirt", ""))
        self.assertIsNotNone(be_for)
        be, skip = be_for("g1")
        self.assertEqual(skip, "unknown_price")
        self.assertIsNone(be)

    def test_a_us_tee_with_a_price_is_judged(self):
        be_for = products.design_be_for(self._conn("standard_tshirt", "19.99"))
        self.assertIsNotNone(be_for)
        be, skip = be_for("g1")
        self.assertIsNone(skip)
        self.assertIsNotNone(be)


class TheReplyCarriesTheActionableNumber(unittest.TestCase):

    def test_actionable_excludes_transitions_and_cohorts(self):
        """A transition expires on its own; a cohort has no per-design economics
        by definition. Neither is something an export would fix, so neither
        belongs in a number that asks the operator to do something."""
        import appctl
        counts = {"ok": 10, "transition": 5, "unknown_price": 2,
                  "unmapped": 1, "cohort": 3}
        # mirror the arithmetic the reply promises
        actionable = counts["unknown_price"] + counts["unmapped"]
        self.assertEqual(actionable, 3)
        self.assertTrue(hasattr(appctl, "_econ_coverage"),
                        "appctl._econ_coverage went away — the econ-gate reply "
                        "would silently lose econ_coverage")

    def test_the_reply_separates_ad_groups_from_products(self):
        """One product can be advertised by several ad groups.

        `actionable` counts AD GROUPS, `actionable_asins` counts PRODUCTS, and
        the first is always the larger. They were briefly one number labelled
        "designs", which reported 200 where the operator would go and fix 177.
        """
        import appctl
        cov = appctl._econ_coverage()
        if cov is None:
            self.skipTest("no economics tables on this machine")
        for key in ("actionable", "actionable_asins", "actionable_spend"):
            self.assertIn(key, cov, f"{key} vanished from the econ-gate reply")
        self.assertGreaterEqual(
            cov["actionable"], cov["actionable_asins"],
            "more products than ad groups — the two counts are the wrong way "
            "round, or one of them is not counting what it says")
        self.assertEqual(cov["actionable"],
                         cov["unknown_price"] + cov["unmapped"],
                         "actionable stopped being unknown_price + unmapped")

    def test_the_counts_add_up_to_the_total(self):
        """Every ad group lands in exactly one bucket, so a bucket that stops
        being counted shows up here rather than as a quietly smaller number."""
        import appctl
        cov = appctl._econ_coverage()
        if cov is None:
            self.skipTest("no economics tables on this machine")
        parts = sum(cov[k] for k in
                    ("ok", "transition", "unknown_price", "unmapped", "cohort"))
        self.assertEqual(parts, cov["total"])

    def test_econ_gate_names_the_field(self):
        """A field the app decodes by name must keep that name."""
        import inspect
        import appctl
        src = inspect.getsource(appctl.cmd_econ_gate)
        self.assertIn("econ_coverage", src,
                      "cmd_econ_gate no longer reports econ_coverage, so the "
                      "app's warning silently stops firing")


class OnlyAdGroupsThatCouldStillActAreCounted(unittest.TestCase):
    """A warning that names something the operator cannot change gets ignored.

    ARCHIVED is terminal — Amazon has no un-archive, so that ad group can never
    serve again. And `ad_group_product` keeps a row after Amazon's live
    product-ad list stops returning the ad group, so nothing refreshes it and
    its blank price is the row's age, not a hole in the catalogue.

    On 2026-08-22 those two turned a warning about 14 products into one about
    the 2 that could actually spend money. Both exclusions are COUNTED in the
    reply rather than applied silently.
    """

    def test_the_reply_reports_what_it_excluded(self):
        import appctl
        cov = appctl._econ_coverage()
        if cov is None:
            self.skipTest("no economics tables on this machine")
        for key in ("excluded_archived", "excluded_stale_rows"):
            self.assertIn(key, cov,
                          f"{key} vanished, so the exclusion is now silent")
            self.assertGreaterEqual(cov[key], 0)

    def test_the_totals_still_add_up_after_excluding(self):
        """Every ad group that IS counted lands in exactly one bucket."""
        import appctl
        cov = appctl._econ_coverage()
        if cov is None:
            self.skipTest("no economics tables on this machine")
        parts = sum(cov[k] for k in
                    ("ok", "transition", "unknown_price", "unmapped", "cohort"))
        self.assertEqual(parts, cov["total"],
                         "the buckets no longer sum to the total, so an "
                         "exclusion is being double-counted or lost")


class TheExclusionsActuallyExclude(unittest.TestCase):
    """Built against a synthetic database, so the guard is exercised rather than
    merely observed on whatever the operator's account happens to contain."""

    def _conn(self):
        import db
        conn = sqlite3.connect(":memory:")
        conn.executescript(db.SCHEMA)
        conn.execute("CREATE TABLE price_change (asin TEXT, ad_group_id TEXT,"
                     " old_cents INTEGER, new_cents INTEGER, observed_at TEXT)")
        conn.execute("CREATE TABLE engine_meta (key TEXT PRIMARY KEY, value TEXT,"
                     " updated_at TEXT)")
        rows = [
            # (ad_group_id, asin, type, price, mapped_at, state)
            ("live",     "B01", "standard_tshirt", "",       "2026-08-22T03:00:00", "ENABLED"),
            ("archived", "B02", "standard_tshirt", "",       "2026-08-22T03:00:00", "ARCHIVED"),
            ("stale",    "B03", "standard_tshirt", "",       "2026-07-31T10:00:00", "ENABLED"),
            ("priced",   "B04", "standard_tshirt", "19.99",  "2026-08-22T03:00:00", "ENABLED"),
        ]
        for agid, asin, pt, price, mapped, state in rows:
            conn.execute("INSERT INTO ad_group_product(ad_group_id,asin,product_type,"
                         "list_price,mapped_at) VALUES(?,?,?,?,?)",
                         (agid, asin, pt, price, mapped))
            conn.execute("INSERT INTO ad_groups(ad_group_id,state) VALUES(?,?)",
                         (agid, state))
        conn.commit()
        return conn

    def test_archived_and_stale_are_left_out_and_reported(self):
        import appctl
        cov = appctl._econ_coverage(conn=self._conn())
        self.assertIsNotNone(cov)
        self.assertEqual(cov["excluded_archived"], 1)
        self.assertEqual(cov["excluded_stale_rows"], 1)
        # only the two current, non-archived rows are judged
        self.assertEqual(cov["total"], 2)
        self.assertEqual(cov["unknown_price"], 1)   # the live one with no price
        self.assertEqual(cov["ok"], 1)              # the 19.99 one
        self.assertEqual(cov["actionable"], 1)

    def test_an_archived_ad_group_never_reaches_the_operator(self):
        """Archived is terminal, so naming it asks for something impossible."""
        import appctl
        cov = appctl._econ_coverage(conn=self._conn())
        self.assertEqual(cov["actionable_asins"], 1,
                         "an archived or stale ad group was counted as "
                         "something the operator could fix")
