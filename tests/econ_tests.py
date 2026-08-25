#!/usr/bin/env python3
"""Unit tests for price-aware US tee economics (PLAN.md v6).
Run from the Ads folder:  python3 -m unittest tests.econ_tests -v
No Amazon API, no production DB — temp SQLite fixtures only."""

import datetime
import os
import sys

# tests/ on the path so this works whether unittest imports us as `tests.x`
# (package) or `x` (what `unittest discover` does).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _no_operator_data  # noqa: F401,E402  (isolates the operator overlay)
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ["ADS_MARKET"] = "US"

import db                                     # noqa: E402
import products                               # noqa: E402
from phase2_apply import _design_target       # noqa: E402


def temp_conn():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    real = db.DB_PATH
    db.DB_PATH = path
    conn = db.connect()                        # migrates: creates econ tables
    db.DB_PATH = real
    return conn, path


class RoyaltyTable(unittest.TestCase):
    def test_confirmed_pairs_exact(self):
        for cents, roy in ((2199, 688), (2299, 767), (2399, 847), (2499, 927)):
            e = products.tee_econ_for_cents(cents)
            self.assertEqual(e["royalty_cents"], roy)
            self.assertFalse(e["extrapolated"])

    def test_a_price_between_rungs_is_still_unsupported(self):
        """No INTERPOLATION: a royalty is never invented between two rungs.

        Widened 2026-08-22 from "no price but a rung" to "no price FAR from a
        rung". Every rung is a .99 price a dollar apart, and a price a few cents
        off is a formatting artifact rather than a pricing decision — across the
        whole US account, 21,267 advertised tees sat exactly on a rung and ONE
        was off, by a single cent. Refusing that one exempted a live, selling
        design from every bid, pause and negative rule, which is worse than
        reading the royalty off the rung one cent away.

        A price genuinely BETWEEN rungs still resolves to nothing and is still
        skipped, which is what this test is for.
        """
        self.assertIsNone(products.tee_econ_for_cents(2349))   # $23.49, 50c off
        self.assertIsNone(products.tee_econ_for_cents(1338))   # below the ladder
        self.assertIsNone(products.tee_econ_for_cents(2049))   # $20.49, 50c off

    def test_a_price_a_few_cents_off_is_priced_as_the_nearest_rung(self):
        for cents, rung in ((2000, 1999), (1998, 1999), (1995, 1999), (2100, 2099)):
            with self.subTest(cents=cents):
                e = products.tee_econ_for_cents(cents)
                self.assertIsNotNone(e, f"${cents/100:.2f} lost its economics")
                self.assertEqual(e["priced_as_cents"], rung)
                self.assertTrue(e["price_snapped"])
                # the REAL price is never overwritten
                self.assertEqual(e["price_cents"], cents)
                self.assertEqual(e["true_break_even"],
                                 round(e["royalty_cents"] / cents, 4))

    def test_snapping_never_reaches_a_second_rung(self):
        """Rungs are 100 cents apart and the tolerance is 5, so the rung a price
        snaps to can never be ambiguous."""
        self.assertLess(products.US_TEE_PRICE_SNAP_CENTS * 2, 100)

    def test_a_rounding_artifact_is_not_a_rank_push(self):
        """$19.95 is a $19.99 tee, not a deliberate price cut below the growth
        floor. Marking it growth-priced would give it a different action royalty
        for the sake of four cents."""
        self.assertFalse(products.tee_econ_for_cents(1995)["growth_priced"])
        self.assertTrue(products.tee_econ_for_cents(1499)["growth_priced"])

    def test_extrapolated_flagged(self):
        # 1999 and 2099 were extrapolated until 2026-08-21, when the operator
        # confirmed every rung off the dashboard. Nothing is a guess now, so the
        # flag's job is to stay FALSE — a new unconfirmed rung would set it again.
        self.assertFalse(products.tee_econ_for_cents(1999)["extrapolated"])
        self.assertFalse(products.tee_econ_for_cents(2099)["extrapolated"])

    def test_break_even_and_growth_cap(self):
        e = products.tee_econ_for_cents(2399)
        self.assertAlmostEqual(e["break_even"], 0.3531, places=4)
        self.assertEqual(e["target_acos"], 0.30)               # growth ceiling binds
        e = products.tee_econ_for_cents(1999)
        self.assertAlmostEqual(e["break_even"], 0.2641, places=4)
        self.assertEqual(e["target_acos"], 0.2641)             # BE < ceiling

    def test_price_parsing(self):
        self.assertEqual(products.parse_price_cents("23.99"), 2399)
        self.assertEqual(products.parse_price_cents("19.95"), 1995)
        self.assertEqual(products.parse_price_cents("20"), 2000)
        self.assertEqual(products.parse_price_cents(" 21.99 "), 2199)
        self.assertIsNone(products.parse_price_cents(""))
        self.assertIsNone(products.parse_price_cents(None))
        self.assertIsNone(products.parse_price_cents("n/a"))
        self.assertIsNone(products.parse_price_cents("-5"))

    def test_floor_row_replaces_stale(self):
        e = products.get_econ("standard_tshirt", "US")
        self.assertEqual(e["royalty"], 5.28)                    # not 4.89
        self.assertEqual(e["break_even"], 0.264)                # not 0.245
        self.assertEqual(e["pause_threshold"], 5.00)            # flat tee pause kept

    def test_lottery_bid_ref_pinned(self):
        import lottery
        self.assertEqual(lottery.LOTTERY_BID_REF_US, 4.89)      # byte-identical EU bids
        self.assertEqual(lottery.clause_bids(),                 # US untouched
                         {"close-match": 0.21, "loose-match": 0.18, "substitutes": 0.15})

    def test_stoploss_pin(self):
        # cohort policy: 10x the minimum supported tee royalty = $52.80
        self.assertAlmostEqual(products.US_TEE_ROYALTY_CENTS[1999] / 100.0 * 10, 52.80)


class TransitionRules(unittest.TestCase):
    def test_max_across_legs_both_directions(self):
        be, unk = products.transition_break_even(2399, [(1999, 2399, "t")])
        self.assertFalse(unk)
        self.assertAlmostEqual(be, 0.3531, places=4)            # increase: new wins
        be, unk = products.transition_break_even(1999, [(2499, 1999, "t")])
        self.assertAlmostEqual(be, 0.3709, places=4)            # decrease: OLD wins

    def test_multi_reprice_aggregates_all_legs(self):
        legs = [(2499, 1999, "t1"), (1999, 2199, "t2")]         # 24.99->19.99->21.99
        be, unk = products.transition_break_even(2199, legs)
        self.assertFalse(unk)
        self.assertAlmostEqual(be, 0.3709, places=4)            # 37.1% regime still counted

    def test_unsupported_leg_means_unknown(self):
        _, unk = products.transition_break_even(2399, [(1338, 2399, "t")])
        self.assertTrue(unk)
        _, unk = products.transition_break_even(None, [(1999, 2199, "t")])
        self.assertTrue(unk)

    def test_design_target_states(self):
        trans = {"A1": [(1999, 2399, "t")],                      # normal transition
                 "A2": [(None, 2399, "t")],                      # seeded: unknown
                 "A3": [(1338, 2399, "t")]}                      # unsupported leg
        row = lambda asin, price: {"asin": asin, "product_type": "standard_tshirt",
                                   "list_price": price}
        tgt, unk, sfx = _design_target(row("A0", "23.99"), {})   # plain known price
        self.assertEqual((round(tgt, 2), unk), (0.30, False))
        self.assertIn("econ_v1=", sfx)
        tgt, unk, _ = _design_target(row("A1", "23.99"), trans)  # max-legs target
        self.assertEqual((tgt, unk), (0.30, False))              # min(0.30, 0.3531)
        _, unk, _ = _design_target(row("A2", "23.99"), trans)
        self.assertTrue(unk)                                     # NULL-old = unknown
        _, unk, _ = _design_target(row("A3", "23.99"), trans)
        self.assertTrue(unk)
        _, unk, _ = _design_target(row("A4", "13.38"), {})       # unsupported price
        self.assertTrue(unk)
        tgt, unk, _ = _design_target(row(None, None), {})        # cohort -> floor econ
        self.assertEqual((tgt, unk), (0.264, False))
        tgt, unk, _ = _design_target({"asin": "X", "product_type": "mug",
                                      "list_price": None}, {})   # non-tee: per-type
        self.assertEqual((tgt, unk), (0.162, False))   # mug: 2.75 / 16.99


class DbLayer(unittest.TestCase):
    def test_migrate_meta_and_price_changes(self):
        conn, path = temp_conn()
        try:
            self.assertTrue(db.econ_tables_present(conn))
            db.meta_set(conn, "k", "v1")
            db.meta_set(conn, "k", "v2")
            self.assertEqual(db.meta_get(conn, "k"), "v2")
            self.assertIsNone(db.meta_get(conn, "missing"))
            db.log_price_changes(conn, [("A", "1", 1999, 2399), ("B", "2", None, 2399)])
            active = db.active_price_changes(conn)
            self.assertEqual(active["A"], [(1999, 2399, active["A"][0][2])])
            self.assertEqual(active["B"][0][0], None)            # seeded row survives
            # expire: backdate a row past the window
            old = (datetime.datetime.now()
                   - datetime.timedelta(days=db.TRANSITION_DAYS + 1)).isoformat(timespec="seconds")
            conn.execute("UPDATE price_change SET observed_at=? WHERE asin='A'", (old,))
            conn.commit()
            self.assertNotIn("A", db.active_price_changes(conn))
        finally:
            conn.close(); os.unlink(path)

    def test_detail_prefix_and_suffix(self):
        sfx = db.econ_suffix(price_cents=2399, break_even=0.3531, target=0.30,
                             src="us_tee_table", model="2026-07-12")
        detail = "wobbling teapot (22 clicks, 0 sales)" + sfx
        self.assertEqual(db.detail_prefix(detail), "wobbling teapot (22 clicks, 0 sales)")
        self.assertEqual(db.detail_prefix("plain old row"), "plain old row")
        self.assertIsNone(db.detail_prefix(None))
        self.assertIn('"price":2399', sfx)

    def test_bid_detail_parses_with_suffix(self):
        import re
        RX_BID = re.compile(r"([0-9]*\.?[0-9]+)\s*->\s*([0-9]*\.?[0-9]+)")
        raw = "snap=2026-07-11 0.21->0.23 (standard_tshirt: ACOS 8% < 30%, scaling)" \
              + db.econ_suffix(price_cents=2399, target=0.30)
        d = db.detail_prefix(raw)
        m = RX_BID.search(d)
        self.assertEqual((m.group(1), m.group(2)), ("0.21", "0.23"))
        self.assertNotIn("econ_v1", d)

    def test_absent_tables_fail_closed(self):
        # a DB created WITHOUT migration (simulates old file on an ro connection)
        import sqlite3
        fd, path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE ad_group_product (ad_group_id TEXT PRIMARY KEY,"
                     " asin TEXT, product_type TEXT, brand TEXT, list_price TEXT,"
                     " lifetime_sales INTEGER, mapped_at TEXT)")
        try:
            self.assertFalse(db.econ_tables_present(conn))
            import appctl
            self.assertIsNone(appctl._design_be_for(conn))       # unavailable, not "no history"
        finally:
            conn.close(); os.unlink(path)


class KillEconResolver(unittest.TestCase):
    def _fixture(self):
        conn, path = temp_conn()
        rows = [("1", "TEE23", "standard_tshirt", "b", "23.99", 5),
                ("2", "TEE21", "standard_tshirt", "b", "21.99", 5),
                ("3", None, "standard_tshirt", "", None, 0),      # multi-ASIN cohort
                ("4", "TEEUNK", "standard_tshirt", "b", "13.38", 0),
                ("5", "HAT1", "printed_trucker_hat", "b", "14.99", 0),
                ("6", "TRANS", "standard_tshirt", "b", "23.99", 0),
                ("7", "SEED", "standard_tshirt", "b", "23.99", 0)]
        db.upsert_ad_group_products(conn, rows)
        db.log_price_changes(conn, [("TRANS", "6", 1999, 2399), ("SEED", "7", None, 2399)])
        return conn, path

    def test_per_design_and_skips(self):
        import appctl
        conn, path = self._fixture()
        try:
            be_for = appctl._design_be_for(conn)
            self.assertAlmostEqual(be_for("1")[0], 0.3531, places=4)   # $23.99 cohort
            self.assertAlmostEqual(be_for("2")[0], 0.3129, places=4)   # $21.99 cohort
            self.assertEqual(be_for("3"), (None, "cohort"))
            self.assertEqual(be_for("4"), (None, "unknown_price"))
            self.assertAlmostEqual(be_for("5")[0], 0.151, places=3)    # hat keeps hat econ
            self.assertEqual(be_for("6"), (None, "transition"))
            self.assertEqual(be_for("7"), (None, "transition"))        # seeded unknown
            self.assertEqual(be_for("99"), (None, "unmapped"))
        finally:
            conn.close(); os.unlink(path)


class Gate(unittest.TestCase):
    def test_gate_scoped_to_us_only(self):
        g = products.econ_gate("DE")
        self.assertTrue(g["ok"])                                   # non-US untouched

    def test_gate_fails_closed_without_stamps(self):
        conn, path = temp_conn()                                   # tables, no stamps
        try:
            g = products.econ_gate("US", conn=conn)
            self.assertFalse(g["ok"])
            self.assertTrue(any("mapping" in r for r in g["reasons"]))
        finally:
            conn.close(); os.unlink(path)

    def test_gate_passes_with_fresh_stamps(self):
        conn, path = temp_conn()
        real_newest, real_fresh = products._newest_export, products._export_fresh
        try:
            db.meta_set(conn, "map_success_at", "2026-07-12T13:00:00")
            db.meta_set(conn, "export_adopted_at", "2026-07-12T12:00:00")
            products._newest_export = lambda: None                 # sig check skipped
            products._export_fresh = lambda p: (True, None)
            g = products.econ_gate("US", conn=conn)
            self.assertTrue(g["ok"], g["reasons"])
            db.meta_set(conn, "econ_stale", "1")                   # STALE closes it
            self.assertFalse(products.econ_gate("US", conn=conn)["ok"])
        finally:
            products._newest_export, products._export_fresh = real_newest, real_fresh
            conn.close(); os.unlink(path)

    def test_export_freshness_needs_name_and_mtime(self):
        with tempfile.TemporaryDirectory() as td:
            stale_name = os.path.join(td, "export_products_2026-01-01T00_00_00.csv")
            open(stale_name, "w").write("x")                       # fresh mtime, old name
            ok, why = products._export_fresh(stale_name)
            self.assertFalse(ok)
            self.assertIn("older", why)
            today = datetime.date.today().isoformat()
            fresh_name = os.path.join(td, f"export_products_{today}T00_00_00.csv")
            open(fresh_name, "w").write("x")
            self.assertTrue(products._export_fresh(fresh_name)[0])


class RankPushPricingTests(unittest.TestCase):
    """A tee priced below $19.99 is a deliberate rank push. It must be ACTED on
    with the $19.99 economics, while what it actually earns stays true —
    operator decision 2026-08-16."""

    def test_the_1499_royalty_is_the_confirmed_one(self):
        self.assertEqual(products.US_TEE_ROYALTY_CENTS[1499], 128)

    def test_a_1499_tee_earns_its_real_royalty(self):
        e = products.tee_econ_for_cents(1499)
        self.assertEqual(e["royalty"], 1.28)
        self.assertAlmostEqual(e["true_break_even"], 0.0854, places=4)

    def test_a_1499_tee_is_acted_on_at_the_1999_break_even(self):
        e = products.tee_econ_for_cents(1499)
        self.assertEqual(e["break_even"], products.tee_econ_for_cents(1999)["break_even"])
        self.assertAlmostEqual(e["break_even"], 0.2641, places=4)
        self.assertTrue(e["growth_priced"])

    def test_prices_at_or_above_the_floor_are_untouched(self):
        for cents in (1999, 2199, 2299, 2399, 2499):
            e = products.tee_econ_for_cents(cents)
            self.assertEqual(e["break_even"], e["true_break_even"], cents)
            self.assertFalse(e["growth_priced"], cents)

    def test_the_negative_threshold_follows_the_action_royalty(self):
        """Half of a $1.28 royalty would negate a keyword after 64 cents and
        strangle the design the price cut was meant to feed."""
        e = products.get_design_econ(products.TEE, market="US", price="14.99")
        self.assertEqual(e["neg_threshold"], 2.64)

    def test_a_1499_design_is_no_longer_price_unknown(self):
        """Before the royalty was confirmed these designs failed closed, so no
        rule could act on them at all."""
        e = products.get_design_econ(products.TEE, market="US", price="14.99")
        self.assertTrue(e["known_price"])
        self.assertEqual(e["src"], "us_tee_table")


if __name__ == "__main__":
    unittest.main(verbosity=2)
