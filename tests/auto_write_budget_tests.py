#!/usr/bin/env python3
"""An unattended run stops instead of writing until it runs out of rows.

`db.AUTO_CHANGE_CAP_DEFAULT` guards the rules DSL, and that guard is the better
one: it counts the whole plan first and refuses all-or-nothing. But it guards
the DSL alone, and eight other scripts run `--apply --auto` every night —
phase2, phase3, preempt_negatives, seasonal_pause, harvest_prune, the two
harvest builders and the two campaign builders. None of them counted anything.
A threshold one character too loose in any of them writes until it runs out of
rows, with nobody watching.

This layer cannot refuse up front, because the total is not known here. It is a
STOP, not a plan check: past the cap the run raises and exits non-zero, which
the nightly's step tracker reports. Stopping at 500 beats continuing to 50,000.

It is hooked into `_send_retry`, the single funnel every write passes through,
rather than into the eighteen write methods — a guard that relies on a list of
call sites is a guard that a nineteenth method silently escapes.

It applies to `--auto` only. An operator-run bulk repair is deliberate and
supervised: rebid_clauses legitimately wrote 9,648 clauses in one go.

Found by review, 2026-08-23.

Run from the Ads folder:
    python3 -m unittest tests.auto_write_budget_tests -v
"""

import os
import sys
import types
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ.setdefault("ADS_MARKET", "US")

import ads_client as ac  # noqa: E402


class _Client:
    """The budget half of AdsClient, without touching .env or the network."""

    def __init__(self, auto, cap, build_cap=10**9, builder=False):
        self._auto, self._written, self._write_cap = auto, 0, cap
        self._built, self._build_cap = 0, build_cap
        self._is_builder = builder
        self.market = "US"
        self.cap_stopped = None

    _budget = ac.AdsClient._budget
    _load_caps = ac.AdsClient._load_caps
    _surface = ac.AdsClient._surface
    declare_campaign_builder = ac.AdsClient.declare_campaign_builder


class OnlyMutatingRequestsCount(unittest.TestCase):

    def test_a_batch_counts_its_entities(self):
        self.assertEqual(ac._entity_count("POST", "/sp/campaigns",
                                          {"campaigns": [1, 2, 3]}), 3)
        self.assertEqual(ac._entity_count("PUT", "/sp/targets",
                                          {"targetingClauses": [1] * 100}), 100)

    def test_a_read_counts_nothing(self):
        self.assertEqual(ac._entity_count("GET", "/sp/campaigns", {}), 0)

    def test_a_list_endpoint_that_posts_a_filter_counts_nothing(self):
        """The v3 list calls are POSTs with a filter body. Counting those would
        make an ordinary read exhaust the budget before any write happened."""
        self.assertEqual(
            ac._entity_count("POST", "/sp/campaigns/list",
                             {"campaignIdFilter": {"include": [1, 2, 3]}}), 0)

    def test_asking_for_a_report_is_not_an_account_change(self):
        self.assertEqual(ac._entity_count("POST", "/reporting/reports",
                                          {"columns": [1] * 40}), 0)

    def test_a_write_with_no_list_still_counts_as_one(self):
        self.assertEqual(ac._entity_count("POST", "/sp/something", {"a": 1}), 1)

    def test_an_id_filter_counts_the_ids_inside_it(self):
        """v3 DELETES take an id FILTER rather than entity objects, so a batch
        of a hundred archives counted as ONE — a guard a hundred times weaker
        than it reads, on the action Amazon cannot undo."""
        self.assertEqual(
            ac._entity_count("POST", "/sp/campaigns/delete",
                             {"campaignIdFilter": {"include": [str(i) for i in range(100)]}}),
            100)
        self.assertEqual(
            ac._entity_count("POST", "/sp/negativeKeywords/delete",
                             {"negativeKeywordIdFilter": {"include": ["a", "b", "c"]}}),
            3)

    def test_a_top_level_list_still_wins_when_it_is_longer(self):
        self.assertEqual(
            ac._entity_count("POST", "/sp/keywords",
                             {"keywords": [1] * 40, "meta": {"tags": [1, 2]}}), 40)


class TheBudgetStopsAnAutomaticRunwayOnly(unittest.TestCase):

    def test_an_automatic_run_stops_past_the_cap(self):
        c = _Client(auto=True, cap=500)
        c._budget(400)
        with self.assertRaises(SystemExit) as cm:
            c._budget(200)
        self.assertIn("500-write limit", str(cm.exception))

    def test_the_refused_batch_is_not_counted_as_written(self):
        """`_budget` added first and compared second, so the stop announced a
        number that included the batch it had just refused to send — a claim
        about the live account, in the one message read when something is
        already wrong."""
        c = _Client(auto=True, cap=500)
        c._budget(400)
        with self.assertRaises(SystemExit) as cm:
            c._budget(200)
        self.assertEqual(400, c._written)
        self.assertIn("has written 400 entities", str(cm.exception))
        self.assertNotIn("has written 600", str(cm.exception))

    def test_it_stops_on_the_cumulative_total_not_one_batch(self):
        """The runaway shape is many ordinary-looking batches, not one huge one.
        A per-batch check would never fire: Amazon takes 100 at a time."""
        c = _Client(auto=True, cap=250)
        for _ in range(2):
            c._budget(100)
        with self.assertRaises(SystemExit):
            for _ in range(2):
                c._budget(100)

    def test_exactly_the_cap_is_allowed(self):
        c = _Client(auto=True, cap=500)
        c._budget(500)          # must not raise
        self.assertEqual(c._written, 500)

    def test_an_operator_run_is_never_capped(self):
        """rebid_clauses wrote 9,648 clauses in one supervised run. A cap that
        breaks the repair tool is a cap somebody turns off."""
        c = _Client(auto=False, cap=500)
        for _ in range(100):
            c._budget(100)
        self.assertEqual(c._written, 0)

    def test_a_cap_of_zero_is_off(self):
        """`appctl change-cap --set 0` means off, and it must mean off here too,
        or the two guards disagree about the same number."""
        c = _Client(auto=True, cap=0)
        c._budget(100000)
        self.assertEqual(c._written, 100000)


class BuildingACampaignIsNotTheSameAsChangingOne(unittest.TestCase):
    """The 500 was measured over CHANGES, and got applied to both surfaces.

    On 2026-08-24, the first night the cap reached ads_client, US
    scavenger_build stopped at 475 of about 700 product ads and the nightly
    recorded `scavenger_build (exit 1)`. Nothing was wrong with the build. The
    cap was measured over the actions a threshold can emit — the busiest day in
    any market's writes_log is 518 — while the two campaign builders create
    1,500 to 3,900 entities on an ordinary night and 27,319 on the busiest day
    ever recorded. One number cannot be right for both.
    """

    def _builder(self):
        c = _Client(auto=True, cap=500)
        c.declare_campaign_builder()
        return c

    def test_populating_a_campaign_is_the_build_surface(self):
        c = self._builder()
        for path in ("/sp/adGroups", "/sp/productAds", "/sp/keywords"):
            self.assertEqual("build", c._surface("POST", path), path)

    def test_a_builders_clause_writes_are_part_of_the_build(self):
        """Amazon auto-generates four clauses under every new AUTO ad group, and
        lottery_build then writes three bids and pauses complements. That is
        FOUR PUT /sp/targets per ad group it just created, so 125 new ASINs is
        500 writes and a routine build stops with its ad groups already on the
        account and their clauses half-configured."""
        self.assertEqual("build", self._builder()._surface("PUT", "/sp/targets"))

    def test_phase3_and_the_dsl_still_get_the_strict_cap_on_targets(self):
        """The same endpoint, from a process that never declared itself."""
        self.assertEqual("change",
                         _Client(auto=True, cap=500)._surface("PUT", "/sp/targets"))

    def test_the_same_endpoints_stay_capped_for_everyone_else(self):
        """`phase4_harvest_create` and `phase4b_harvest_asins` create ad groups,
        product ads and keywords too, and they are threshold-driven — they
        promote what the harvest judged a winner. The endpoint alone cannot
        tell the two cases apart, so the process has to declare itself."""
        c = _Client(auto=True, cap=500)          # never declared
        for path in ("/sp/adGroups", "/sp/productAds", "/sp/keywords"):
            self.assertEqual("change", c._surface("POST", path), path)

    def test_creating_a_campaign_stays_on_the_change_cap(self):
        """A campaign is the unit of daily spend — each one is $5/day — and no
        legitimate night creates more than about fifty."""
        self.assertEqual("change", self._builder()._surface("POST", "/sp/campaigns"))

    def test_changing_an_existing_entity_is_the_change_surface(self):
        c = self._builder()
        for path in ("/sp/keywords", "/sp/adGroups", "/sp/campaigns"):
            self.assertEqual("change", c._surface("PUT", path), path)
        self.assertEqual("change", c._surface("POST", "/sp/negativeKeywords"))
        self.assertEqual("change", c._surface("POST", "/sp/campaigns/delete"))

    def test_a_product_target_is_never_a_build(self):
        """POST /sp/targets is used by phase4b_harvest_asins alone."""
        self.assertEqual("change", self._builder()._surface("POST", "/sp/targets"))

    def test_an_unknown_endpoint_gets_the_stricter_cap(self):
        """The list fails safe: forgetting one stops a run loudly rather than
        letting it run away."""
        self.assertEqual("change", self._builder()._surface("POST", "/sp/somethingNew"))

    def test_only_the_two_campaign_builders_declare_themselves(self):
        """Read the engine rather than trust this docstring. A third script
        appearing here is a decision, not an accident."""
        import glob
        import os
        declaring = []
        for path in glob.glob(os.path.join(HERE, "engine", "*.py")):
            with open(path, encoding="utf-8") as fh:
                if "declare_campaign_builder()" in fh.read():
                    declaring.append(os.path.basename(path))
        self.assertEqual(["lottery_build.py", "scavenger_build.py"], sorted(declaring))

    def test_a_nightly_build_is_not_stopped_by_the_change_cap(self):
        """The exact shape of the 2026-08-24 failure: 475 product ads sent in
        batches of 100, against a change cap of 500."""
        c = _Client(auto=True, cap=500, build_cap=50_000)
        for _ in range(38):                     # 3,800 product ads, a busy night
            c._budget(100, "build")
        self.assertEqual(3800, c._built)
        self.assertEqual(0, c._written, "a build spent the change budget")
        self.assertIsNone(c.cap_stopped)

    def test_the_build_cap_still_stops_a_runaway(self):
        c = _Client(auto=True, cap=500, build_cap=1000)
        c._budget(900, "build")
        with self.assertRaises(SystemExit) as cm:
            c._budget(200, "build")
        self.assertEqual(900, c._built)
        self.assertIn("1000-write limit", str(cm.exception))
        self.assertIn("--set-build", str(cm.exception))
        self.assertEqual("build", c.cap_stopped["surface"])

    def test_the_two_budgets_do_not_share_a_counter(self):
        """A night that builds thousands must still stop at 500 bid changes."""
        c = _Client(auto=True, cap=500, build_cap=50_000)
        c._budget(5000, "build")
        c._budget(400, "change")
        with self.assertRaises(SystemExit):
            c._budget(200, "change")
        self.assertEqual(400, c._written)
        self.assertEqual(5000, c._built)

    def test_the_default_surface_is_the_stricter_one(self):
        c = _Client(auto=True, cap=500, build_cap=50_000)
        c._budget(400)
        self.assertEqual(400, c._written)
        self.assertEqual(0, c._built)

    def test_a_build_cap_of_zero_is_off(self):
        c = _Client(auto=True, cap=500, build_cap=0)
        c._budget(10**6, "build")
        self.assertEqual(10**6, c._built)

    def test_the_shipped_build_cap_clears_the_builders_own_ceiling(self):
        """50,000 is chosen from scavenger_build's structural cap, so a build
        those caps allow can never trip it: 6 series x 6 campaigns x
        (1000 ASINs + 200 keywords)."""
        import db
        import scavenger
        ceiling = 6 * scavenger.MAX_CAMPAIGNS * (scavenger.MAX_ASINS
                                                 + scavenger.MAX_KEYWORDS)
        self.assertGreater(db.AUTO_BUILD_CAP_DEFAULT, ceiling)
        self.assertGreater(db.AUTO_BUILD_CAP_DEFAULT, db.AUTO_CHANGE_CAP_DEFAULT)


class OneRefusedFlagRefusesTheWholeCommand(unittest.TestCase):
    """`change-cap` used to write the change cap and THEN validate the build one.

    `change-cap --set 0 --set-build junk` printed an error and exited 1, and the
    change guard was off from then on. A command that reads as failed must not
    have changed anything.
    """

    def setUp(self):
        import tempfile
        import db
        import appctl
        self.db, self.appctl = db, appctl
        fd, path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        real, db.DB_PATH = db.DB_PATH, path
        self.conn = db.connect()
        db.DB_PATH = real
        self.addCleanup(os.unlink, path)
        self.addCleanup(self.conn.close)
        import io
        self._set(appctl, "out", lambda d: None)
        self._set(appctl, "_RESP_STREAM", io.StringIO())   # keep err() quiet
        self._set(appctl, "_guard_kill", lambda: None)
        self._set(db, "connect", lambda *a, **k: self.conn)

    def _set(self, obj, name, value):
        old = getattr(obj, name)
        setattr(obj, name, value)
        self.addCleanup(setattr, obj, name, old)

    def _args(self, **kw):
        return types.SimpleNamespace(
            set=kw.get("set"), clear=kw.get("clear", False),
            set_build=kw.get("set_build"), clear_build=kw.get("clear_build", False))

    def test_a_bad_build_cap_leaves_the_change_cap_alone(self):
        with self.assertRaises(SystemExit):
            self.appctl.cmd_change_cap(self._args(set="0", set_build="junk"))
        self.assertEqual(self.db.AUTO_CHANGE_CAP_DEFAULT,
                         self.db.get_auto_change_cap(self.conn))

    def test_a_bad_change_cap_leaves_the_build_cap_alone(self):
        with self.assertRaises(SystemExit):
            self.appctl.cmd_change_cap(self._args(set="-1", set_build="0"))
        self.assertEqual(self.db.AUTO_BUILD_CAP_DEFAULT,
                         self.db.get_auto_build_cap(self.conn))

    def test_two_good_numbers_are_both_written(self):
        self.appctl.cmd_change_cap(self._args(set="120", set_build="7000"))
        self.assertEqual(120, self.db.get_auto_change_cap(self.conn))
        self.assertEqual(7000, self.db.get_auto_build_cap(self.conn))


class TheHookIsWhereItCannotBeMissed(unittest.TestCase):

    def test_every_write_goes_through_the_funnel_that_counts(self):
        """If a write method stops using _send_retry, it escapes the budget
        silently. This reads the source rather than trusting the docstring."""
        import inspect
        src = inspect.getsource(ac.AdsClient)
        self.assertIn("self._budget(_entity_count(", src)
        self.assertIn("self._surface(method, path)", src,
                      "the surface is not resolved at the funnel, so every write "
                      "falls back to one budget again")
        writers = [n for n, f in vars(ac.AdsClient).items()
                   if callable(f) and n.startswith(("create_", "update_", "set_",
                                                    "pause_", "archive_"))
                   and n != "create_report"]
        self.assertGreater(len(writers), 12, "the write-method scan found almost "
                                             "nothing — it has stopped matching")
        for name in writers:
            body = inspect.getsource(getattr(ac.AdsClient, name))
            self.assertIn("_send_retry", body,
                          f"{name} does not go through _send_retry, so it is not "
                          f"counted by the automatic write budget")


if __name__ == "__main__":
    unittest.main()
