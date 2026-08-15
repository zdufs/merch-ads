#!/usr/bin/env python3
"""Rolling windows in the rules DSL.

Before per-target daily banking existed, `IN LAST N DAYS` was a parse error:
the only per-entity data was an overlapping trailing-30 snapshot. target_daily
and campaign_daily changed that for four entity kinds. The other two still have
no per-day source, and asking must fail at save time rather than as a nightly
"unsupported" weeks later.

Run from the Ads folder:  python3 -m unittest tests.rules_rolling_tests -v
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["ADS_MARKET"] = "US"

from rules.parser import parse, ParseError  # noqa: E402
from rules import runner  # noqa: E402

ROLLING = """FOR EACH target IN LAST 7 DAYS:
  IF clicks > 15 AND orders = 0:
    target.setBid(target.bid * 0.8)
"""


class Grammar(unittest.TestCase):

    def test_rolling_window_parses(self):
        prog = parse(ROLLING)
        fe = prog.rules[0]
        self.assertEqual(fe.window, "ROLLING")
        self.assertEqual(fe.window_days, 7)
        self.assertIsInstance(fe.window_days, int)

    def test_current_still_defaults(self):
        prog = parse("FOR EACH target:\n  IF clicks > 1:\n    target.pause()\n")
        self.assertEqual(prog.rules[0].window, "CURRENT")
        self.assertIsNone(prog.rules[0].window_days)

    def test_lifetime_is_untouched(self):
        prog = parse("FOR EACH target IN LIFETIME:\n  IF lifetime_sales > 5:\n    target.pause()\n")
        self.assertEqual(prog.rules[0].window, "LIFETIME")
        self.assertIsNone(prog.rules[0].window_days)

    def test_singular_day_parses(self):
        prog = parse("FOR EACH target IN LAST 1 DAY:\n  IF clicks > 1:\n    target.pause()\n")
        self.assertEqual(prog.rules[0].window_days, 1)

    def test_a_bare_number_is_still_an_error(self):
        with self.assertRaises(ParseError):
            parse("FOR EACH target IN 7:\n  IF clicks > 1:\n    target.pause()\n")


class Semantics(unittest.TestCase):

    def test_valid_rolling_rule_passes(self):
        self.assertTrue(runner.validate(ROLLING)["ok"])

    def test_window_beyond_retention_is_rejected_at_save_time(self):
        result = runner.validate(
            "FOR EACH target IN LAST 200 DAYS:\n  IF clicks > 1:\n    target.pause()\n")
        self.assertFalse(result["ok"])
        self.assertIn("92", result["errors"][0]["message"])

    def test_zero_days_is_rejected(self):
        result = runner.validate(
            "FOR EACH target IN LAST 0 DAYS:\n  IF clicks > 1:\n    target.pause()\n")
        self.assertFalse(result["ok"])

    def test_fractional_days_are_rejected(self):
        result = runner.validate(
            "FOR EACH target IN LAST 7.5 DAYS:\n  IF clicks > 1:\n    target.pause()\n")
        self.assertFalse(result["ok"])

    def test_searchterm_rolling_is_rejected_and_says_what_works(self):
        result = runner.validate(
            "FOR EACH searchTerm IN LAST 7 DAYS:\n  IF clicks > 1:\n    searchTerm.pause()\n")
        self.assertFalse(result["ok"])
        message = result["errors"][0]["message"]
        self.assertIn("searchterm", message.lower())
        self.assertIn("campaign", message.lower())

    def test_product_rolling_is_rejected(self):
        result = runner.validate(
            "FOR EACH product IN LAST 7 DAYS:\n  IF clicks > 1:\n    product.pause()\n")
        self.assertFalse(result["ok"])

    def test_searchterm_current_still_works(self):
        self.assertTrue(runner.validate(
            "FOR EACH searchTerm:\n  IF clicks > 1:\n    searchTerm.addNegative()\n")["ok"])

    def test_campaign_and_adgroup_rolling_are_allowed(self):
        for entity in ("campaign", "adGroup"):
            src = f"FOR EACH {entity} IN LAST 14 DAYS:\n  IF clicks > 1:\n    {entity}.pause()\n"
            self.assertTrue(runner.validate(src)["ok"], entity)


import datetime  # noqa: E402
import sqlite3  # noqa: E402

import db  # noqa: E402
from rules import entities  # noqa: E402

TODAY = datetime.date(2026, 8, 6)


def bank_day(conn, day, clicks=2, cost=1.0, target_id="t1", targeting="50s shirt"):
    """One target_daily row on one day. Columns are NAMED, so this cannot drift
    out of step with db.SCHEMA the way a positional INSERT did."""
    conn.execute(
        """INSERT OR REPLACE INTO target_daily
           (date, campaign_id, ad_group_id, targeting, match_type, target_id,
            impressions, clicks, cost, orders, sales, acos, pulled_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (day, "c1", "ag1", targeting, "EXACT", target_id,
         100, clicks, cost, 0, 0.0, None, "now"))


def rolling_conn(days=None):
    """A DB built from the REAL db.SCHEMA, with seven complete days in
    target_daily plus the structure rows the loaders join against.

    Built from db.SCHEMA rather than hand-written CREATE TABLEs on purpose: a
    hand-written target_daily drifted out of column order once already, and a
    fixture that lies about the shape of a table teaches the next reader the
    wrong shape.
    """
    conn = sqlite3.connect(":memory:")
    conn.executescript(db.SCHEMA)
    conn.execute("INSERT INTO ad_groups (ad_group_id, campaign_id, name, state, "
                 "default_bid) VALUES ('ag1','c1','Tee AG','ENABLED',0.75)")
    conn.execute("INSERT INTO ad_group_product (ad_group_id, asin, product_type) "
                 "VALUES ('ag1','B0TEST','tee')")
    conn.execute("INSERT INTO targets (target_id, campaign_id, ad_group_id, "
                 "match_type, state, bid) VALUES ('t1','c1','ag1','EXACT','ENABLED',0.90)")
    conn.execute("INSERT INTO campaigns (campaign_id, name, state, daily_budget, "
                 "bidding_strategy) VALUES ('c1','Lotto 1','ENABLED',10.0,'auto')")
    # Seven days, two clicks and one dollar each.
    if days is None:
        days = [(datetime.date(2026, 8, 4) - datetime.timedelta(days=n)).isoformat()
                for n in range(7)]
    for day in days:
        bank_day(conn, day)
    return conn


class RollingLoad(unittest.TestCase):

    def test_target_metrics_are_the_window_sum(self):
        rows = entities.load(rolling_conn(), "target", window="ROLLING",
                             window_days=7, today=TODAY)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].field("clicks"), 14)
        self.assertEqual(rows[0].field("spend"), 7.0)

    def test_window_is_lagged_so_the_newest_days_are_excluded(self):
        """Only 7 days ending 2026-08-04 count. A row dated 2026-08-05 sits
        inside the lag and must not be summed."""
        conn = rolling_conn()
        bank_day(conn, "2026-08-05", clicks=99, cost=99.0)
        rows = entities.load(conn, "target", window="ROLLING",
                             window_days=7, today=TODAY)
        self.assertEqual(rows[0].field("clicks"), 14)

    def test_shorter_window_sums_fewer_days(self):
        rows = entities.load(rolling_conn(), "target", window="ROLLING",
                             window_days=3, today=TODAY)
        self.assertEqual(rows[0].field("clicks"), 6)

    def test_bid_and_state_still_come_from_the_mirror(self):
        """Only the metrics change in a rolling window. The bid is a setting,
        not a measurement, so it reads the same mirror as always."""
        rows = entities.load(rolling_conn(), "target", window="ROLLING",
                             window_days=7, today=TODAY)
        self.assertEqual(rows[0].field("bid"), 0.90)
        self.assertFalse(rows[0].field("bid_inherited"))

    def test_identity_fields_survive(self):
        rows = entities.load(rolling_conn(), "target", window="ROLLING",
                             window_days=7, today=TODAY)
        self.assertEqual(rows[0].field("asin"), "B0TEST")
        self.assertEqual(rows[0].field("product_type"), "tee")
        self.assertEqual(rows[0].field("match_type"), "EXACT")

    def test_ad_groups_sum_their_targets(self):
        rows = entities.load(rolling_conn(), "adgroup", window="ROLLING",
                             window_days=7, today=TODAY)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].field("clicks"), 14)
        self.assertEqual(rows[0].field("default_bid"), 0.75)

    def test_campaigns_read_campaign_daily_not_target_daily(self):
        conn = rolling_conn()
        for offset in range(7):
            day = (datetime.date(2026, 8, 4) - datetime.timedelta(days=offset)).isoformat()
            conn.execute(
                """INSERT INTO campaign_daily (date, campaign_id, campaign_name, cost,
                                               sales, orders, impressions, clicks,
                                               units, pulled_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (day, "c1", "Lotto 1", 5.0, 0.0, 0, 500, 10, 0, "now"))
        rows = entities.load(conn, "campaign", window="ROLLING",
                             window_days=7, today=TODAY)
        self.assertEqual(rows[0].field("clicks"), 70)
        self.assertEqual(rows[0].field("budget"), 10.0)

    def test_current_window_is_unchanged(self):
        """The default path must not have moved. targeting_perf is still the
        source when no rolling window is asked for."""
        conn = rolling_conn()
        conn.execute(
            """INSERT INTO targeting_perf (date, campaign_id, ad_group_id, targeting,
                                           match_type, target_id, impressions, clicks,
                                           cost, orders, sales, acos)
               VALUES ('2026-08-05','c1','ag1','50s shirt','EXACT','t1',
                       3000,55,30.0,1,19.99,1.5)""")
        rows = entities.load(conn, "target")
        self.assertEqual(rows[0].field("clicks"), 55)

    def test_target_id_picks_deterministically_when_it_changes_mid_window(self):
        """A keyword deleted and recreated mid-window keeps the same targeting
        text and match type, but Amazon gives it a new target_id. The rolling
        group key is (campaign, ad group, targeting, match type) — it does not
        include target_id — so both rows fold into one group. The picked id
        must be deterministic, not whatever SQLite's bare-column extension
        happens to return."""
        conn = rolling_conn()
        conn.execute("UPDATE target_daily SET target_id='t9' WHERE date=?",
                     ("2026-08-04",))
        rows = entities.load(conn, "target", window="ROLLING",
                             window_days=7, today=TODAY)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].field("target_id"), "t9")


from rules import executor  # noqa: E402


class ChangeCarriesWindow(unittest.TestCase):
    """`preview` resolves economics for every row. On a minimal test DB that
    degrades cleanly rather than raising: products.design_be_for catches the
    missing tables and returns None, and econ_fields._break_even reports
    'unmapped'. So these tests need no economics fixtures."""

    def test_preview_change_records_its_window(self):
        """The executor gates on the window the change was measured over, so
        the change has to carry it."""
        conn = rolling_conn()
        src = ("FOR EACH target IN LAST 7 DAYS:\n"
               "  IF clicks > 10:\n"
               "    target.setBid(0.50)\n")
        result = runner.preview(conn, src, today=TODAY)
        self.assertTrue(result["ok"], result.get("errors"))
        self.assertEqual(len(result["changes"]), 1)
        self.assertEqual(result["changes"][0]["window"], "ROLLING")
        self.assertEqual(result["changes"][0]["window_days"], 7)

    def test_current_change_records_current(self):
        conn = rolling_conn()
        conn.execute(
            """INSERT INTO targeting_perf (date, campaign_id, ad_group_id, targeting,
                                           match_type, target_id, impressions, clicks,
                                           cost, orders, sales, acos)
               VALUES ('2026-08-05','c1','ag1','50s shirt','EXACT','t1',
                       3000,55,30.0,1,19.99,1.5)""")
        result = runner.preview(
            conn, "FOR EACH target:\n  IF clicks > 10:\n    target.setBid(0.50)\n")
        self.assertEqual(result["changes"][0]["window"], "CURRENT")
        self.assertIsNone(result["changes"][0]["window_days"])


class RollingSourceTable(unittest.TestCase):

    def test_rolling_targets_gate_on_target_daily(self):
        self.assertEqual(executor._ROLLING_SOURCE["target"], "target_daily")
        self.assertEqual(executor._ROLLING_SOURCE["adgroup"], "target_daily")

    def test_rolling_campaigns_gate_on_campaign_daily(self):
        self.assertEqual(executor._ROLLING_SOURCE["campaign"], "campaign_daily")

    def test_current_changes_still_gate_on_the_snapshot_tables(self):
        self.assertEqual(executor._SOURCE_TABLE["target"], "targeting_perf")
        self.assertEqual(executor._SOURCE_TABLE["campaign"], "campaign_perf")

    def test_kinds_without_a_per_day_table_are_absent_not_defaulted(self):
        """Absent, so the lookup can return None and the executor can block.
        A default here would gate a search-term change on target_daily's
        completeness while its numbers came from search_term_perf."""
        self.assertNotIn("searchterm", executor._ROLLING_SOURCE)
        self.assertNotIn("product", executor._ROLLING_SOURCE)


class NoPerDaySource(unittest.TestCase):
    """Search terms and products have no per-day table. Three layers each
    half-covered this and the hole ran straight between them: the save-time
    check is skipped by preview, the loader quietly served CURRENT rows
    instead, and the executor then guessed target_daily as the table to gate
    on. Reachable by hand-editing a file in rule_defs/."""

    def test_load_raises_rather_than_downgrading_to_current(self):
        """Returning trailing-30 snapshot rows for a rule that asked for seven
        days is the dangerous answer: the operator reads per-day numbers and
        gets a month of overlapping ones."""
        conn = rolling_conn()
        for kind in ("searchterm", "product", "asin"):
            with self.assertRaises(entities.FieldError, msg=kind) as caught:
                entities.load(conn, kind, window="ROLLING", window_days=7,
                              today=TODAY)
            self.assertIn("per-day", str(caught.exception))

    def test_preview_reports_it_instead_of_previewing_snapshot_rows(self):
        conn = rolling_conn()
        result = runner.preview(
            conn, "FOR EACH searchTerm IN LAST 7 DAYS:\n"
                  "  IF clicks > 1:\n    searchTerm.addNegative()\n")
        self.assertFalse(result["ok"])
        self.assertIn("per-day", result["errors"][0]["message"])

    def test_current_windows_on_those_kinds_still_load(self):
        """Only the rolling path is refused. The snapshot path is what these
        kinds have always used and must not move."""
        conn = rolling_conn()
        conn.execute(
            """INSERT INTO search_term_perf (date, campaign_id, ad_group_id,
                                             search_term, targeting, match_type,
                                             impressions, clicks, cost, orders, sales)
               VALUES ('2026-08-05','c1','ag1','cheap tee','50s shirt','EXACT',
                       900,12,6.0,0,0.0)""")
        rows = entities.load(conn, "searchterm")
        self.assertEqual(rows[0].field("clicks"), 12)

    def test_executor_blocks_such_a_change_rather_than_guessing_a_table(self):
        """If one reaches the executor anyway, it must be blocked. Gating it
        on target_daily would judge search_term_perf's numbers by another
        table's state — the standing rule this engine has been burned by
        twice — and skip search_term_perf's own freshness check entirely."""
        conn = rolling_conn()
        client = ExplodingClient()
        change = {"entity_kind": "searchterm", "entity_id": "cheap tee",
                  "label": "cheap tee", "action": "addNegative",
                  "args": ["cheap tee"], "note": "hand-edited rule",
                  "window": "ROLLING", "window_days": 7,
                  "ref": {"campaign_id": "c1", "ad_group_id": "ag1"}}
        res = executor.execute(conn, [change], market="US", client=client)
        self.assertEqual(res["count"], 0)
        self.assertEqual(res["results"][0]["status"], "blocked_stale_data")
        self.assertIn("no per-day table", res["results"][0]["reason"])
        self.assertEqual(client.calls, [])


class NeverCalled(BaseException):
    """Deliberately NOT an Exception. The executor catches Exception around
    every apply and turns it into a result row, so a plain error would be
    swallowed and the test would pass while a live write was attempted."""


class ExplodingClient:
    """Any Amazon call at all is a test failure, loudly."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def boom(*a, **kw):
            self.calls.append(name)
            raise NeverCalled(f"the executor called Amazon: {name}")
        return boom


class RecordingClient:
    """Accepts every write and remembers it."""

    def __init__(self):
        self.calls = []
        self.last_clamps = []

    def update_target_bids(self, items):
        self.last_clamps = []
        self.calls.append(("update_target_bids", list(items)))
        return [{"http": 200, "count": len(items)}]

    def update_keyword_bids(self, items):
        # The fixture clause is a keyword (match EXACT), so the executor now
        # routes its bid to /sp/keywords; accept it the same way.
        self.last_clamps = []
        self.calls.append(("update_keyword_bids", list(items)))
        return [{"http": 200, "count": len(items)}]


BID_RULE = ("FOR EACH target IN LAST 7 DAYS:\n"
            "  IF clicks > 10:\n"
            "    target.setBid(0.50)\n")


def window_days_now(days=7):
    """The exact dates a 7-day rolling window covers as of the REAL today.
    These end-to-end tests leave runner.preview's date unpinned (its default)
    and executor.execute resolves the window itself, so the fixture is built
    against the real calendar to match what those paths see."""
    start, end = db.daily_window(days)
    start = datetime.date.fromisoformat(start)
    end = datetime.date.fromisoformat(end)
    return [(start + datetime.timedelta(days=n)).isoformat()
            for n in range((end - start).days + 1)]


class RollingGateEndToEnd(unittest.TestCase):
    """The whole loop, from rule text to the Amazon call that must not happen.

    The gate is unit-tested and the executor's table lookup is tested by dict
    membership, but both would still pass if the `if not snap["ok"]` branch in
    rules/executor.py were deleted. This is the assertion standing between an
    incomplete week of data and a real bid change.
    """

    def test_a_hole_in_the_window_stops_the_write_reaching_amazon(self):
        days = window_days_now()
        holed = list(days)
        holed.pop(3)                            # one day gone from the middle
        conn = rolling_conn(days=holed)

        result = runner.preview(conn, BID_RULE)
        self.assertTrue(result["ok"], result.get("errors"))
        self.assertEqual(len(result["changes"]), 1,
                         "the rule must still MATCH — six days summed look like "
                         "a week, which is exactly why the gate exists")

        client = ExplodingClient()
        res = executor.execute(conn, result["changes"], market="US", client=client)
        self.assertEqual(res["count"], 0)
        self.assertEqual(res["results"][0]["status"], "blocked_stale_data")
        self.assertIn("6 of 7 days", res["results"][0]["reason"])
        self.assertEqual(client.calls, [], "nothing may reach Amazon")
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM writes_log").fetchone()[0], 0)

    def test_a_complete_window_reaches_amazon(self):
        """The other half. A gate that blocked everything would also pass the
        test above."""
        conn = rolling_conn(days=window_days_now())

        result = runner.preview(conn, BID_RULE)
        self.assertTrue(result["ok"], result.get("errors"))
        self.assertEqual(len(result["changes"]), 1)

        client = RecordingClient()
        res = executor.execute(conn, result["changes"], market="US", client=client)
        self.assertEqual(res["count"], 1, res["results"])
        self.assertEqual(res["results"][0]["status"], "applied")
        # the fixture clause is a keyword (match EXACT), so the bid routes to
        # the keyword endpoint, not /sp/targets
        self.assertEqual(client.calls[0][0], "update_keyword_bids")
        self.assertEqual(client.calls[0][1][0]["bid"], 0.50)
        logged = conn.execute(
            "SELECT action, entity_id FROM writes_log").fetchall()
        self.assertEqual(logged, [("bid_change", "t1")])
