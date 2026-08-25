#!/usr/bin/env python3
"""What the app declares about an "act everywhere" instance, the engine must send.

`EverywhereInstance` in `Models.swift` declares `campaign_id`, `target_id`,
`asin` and `state`. `_everywhere_slim` sent none of them. The app used the
absence of `target_id` to decide WHY an instance was skipped, so every skip was
reported as "the app cannot address this" and the genuine no-ops were counted as
zero — on every preview, in every market, since the field was added.

The Swift test written to cover it hand-wrote `target_id` into its fixture, so
it passed against a JSON shape production has never produced. That is the shape
of a test that cannot fail: the author asserts what they believe the engine
sends, and nothing ever compares the belief with the engine.

So this reads BOTH SIDES. The Swift struct is the declaration, the engine
function is the source, and a field on one side with no counterpart on the other
fails here.

The second half asserts the engine now SAYS why, rather than leaving the app to
work it out — an ASIN pause acts on ad groups, which carry no target id at all,
so the old inference could never have been right for it.

Found by review, 2026-08-23.

Run from the Ads folder:
    python3 -m unittest tests.everywhere_contract_tests -v
"""

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ.setdefault("ADS_MARKET", "US")

import appctl  # noqa: E402

MODELS = os.path.join(HERE, "MerchAds", "Models", "Models.swift")


def _snake(name):
    """camelCase -> snake_case, the app's `.convertFromSnakeCase` in reverse."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def swift_fields(struct):
    """The stored properties of one Swift struct, as engine key names.

    Stored only: a `var x: T { ... }` is computed and has no wire key.
    """
    with open(MODELS, encoding="utf-8") as fh:
        text = fh.read()
    start = text.index(f"struct {struct}:")
    depth, i, body = 0, text.index("{", start), None
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                body = text[i + 1:j]
                break
    assert body is not None, f"could not read the body of {struct}"
    out = set()
    for m in re.finditer(r"^\s*(?:let|var)\s+(\w+)\s*:\s*[^\n=]+?(=|\{|$)",
                         body, re.M):
        if m.group(2) == "{":
            continue                      # computed property, never decoded
        out.add(_snake(m.group(1)))
    return out


class TheInstanceContractHoldsBothWays(unittest.TestCase):

    def test_the_reader_finds_the_struct(self):
        """A lint that reads an empty struct passes forever."""
        self.assertGreater(len(swift_fields("EverywhereInstance")), 5,
                           "found almost no fields — this has stopped matching "
                           "how Models.swift is written")

    def test_the_engine_sends_every_field_the_app_declares(self):
        sent = set(appctl._everywhere_slim({}))
        sent.add("skip")                  # added beside _everywhere_slim by the caller
        declared = swift_fields("EverywhereInstance")
        missing = sorted(declared - sent)
        self.assertEqual(
            missing, [],
            "the app declares these and the engine never sends them, so they "
            "decode to nil forever: " + ", ".join(missing))

    def test_the_engine_sends_nothing_the_app_ignores(self):
        """The other direction. A field added to the engine and nowhere else is
        the truth-field failure this project keeps finding: the reply looks
        careful and no screen is any wiser."""
        sent = set(appctl._everywhere_slim({}))
        declared = swift_fields("EverywhereInstance") | {"skip"}
        unread = sorted(sent - declared)
        self.assertEqual(
            unread, [],
            "the engine sends these and no Swift property names them: "
            + ", ".join(unread))


class TheEngineSaysWhyItSkipped(unittest.TestCase):

    def test_a_row_that_would_be_written_has_no_reason(self):
        self.assertIsNone(appctl._skip_reason("ENABLED"))
        self.assertIsNone(appctl._skip_reason("ENABLED", "t1", needs_target=True))

    def test_a_paused_row_is_a_genuine_no_op(self):
        self.assertEqual(appctl._skip_reason("PAUSED"), "already_paused")
        self.assertEqual(appctl._skip_reason("ARCHIVED"), "already_paused")

    def test_no_clause_to_write_to_is_not_a_no_op(self):
        """The distinction the whole field exists for. Nothing about this row is
        already where the operator wants it; the app simply cannot address it,
        and calling that 'already paused' is how part of a selection goes
        quietly missing under a reassuring word."""
        self.assertEqual(
            appctl._skip_reason("ENABLED", None, needs_target=True),
            "unaddressable")

    def test_an_ad_group_action_never_reports_unaddressable(self):
        """An ASIN pause acts on AD GROUPS, which have no target id by design.
        Under the old rule — infer from `target_id is None` — every already
        paused ad group in the account was reported as unwritable."""
        self.assertEqual(appctl._skip_reason("PAUSED", None), "already_paused")

    def test_an_unmirrored_state_is_neither(self):
        """A row whose state was never mirrored is not a no-op, it is a row we
        cannot judge. Saying 'already paused' about it would be a guess."""
        self.assertEqual(appctl._skip_reason(None), "state_unknown")
        self.assertEqual(appctl._skip_reason(None, "t1", needs_target=True),
                         "state_unknown")

    def test_unaddressable_outranks_an_unknown_state(self):
        self.assertEqual(
            appctl._skip_reason(None, None, needs_target=True), "unaddressable")


class AClauseWeCannotSeeIsNotTheAdGroup(unittest.TestCase):
    """`_skip_reason` was tested directly, so nothing exercised how the PLAN
    works out the state it passes in — and the plan was substituting the ad
    group's state for a clause missing from the `targets` mirror.

    An enabled ad group therefore reported the clause as ENABLED. The plan said
    "no reason to skip", the pause went out as a no-op, and `writes_log`
    recorded a previous state of ENABLED — so Undo would ENABLE a clause the
    operator had paused before any of this ran.

    Found by the second review pass, 2026-08-23.
    """

    def test_the_clause_own_state_wins(self):
        tgt = {"t1": ("PAUSED", 0.30)}
        ag = {"ag1": "ENABLED"}
        self.assertEqual(
            appctl._target_state(tgt, ag, {"target_id": "t1", "ad_group_id": "ag1"}),
            "PAUSED")

    def test_an_unseen_clause_in_an_enabled_group_is_unknown(self):
        """The bug. The ad group says ENABLED and the clause says nothing."""
        self.assertIsNone(
            appctl._target_state({}, {"ag1": "ENABLED"},
                                 {"target_id": "t1", "ad_group_id": "ag1"}))

    def test_a_paused_group_is_conclusive(self):
        """An ad group that cannot serve settles it whatever the clause says,
        so this stays a genuine no-op rather than becoming unknown."""
        for state in ("PAUSED", "ARCHIVED"):
            with self.subTest(state=state):
                self.assertEqual(
                    appctl._target_state({}, {"ag1": state},
                                         {"target_id": "t1", "ad_group_id": "ag1"}),
                    state)

    def test_that_unknown_state_becomes_a_skip_with_its_own_reason(self):
        st = appctl._target_state({}, {"ag1": "ENABLED"},
                                  {"target_id": "t1", "ad_group_id": "ag1"})
        self.assertEqual(appctl._skip_reason(st, "t1", needs_target=True),
                         "state_unknown")


class NoReadEndpointReportsAMeasuredZeroItDidNotMeasure(unittest.TestCase):
    """The ratio test checked the shared helper, so a hardcoded rate sitting in
    one endpoint went on answering 0.0 for a row with nothing behind it."""

    def test_an_unresolved_watchlist_row_has_no_conversion_rate(self):
        row = appctl._watchlist_metric_row(None, None, "legacy", {}, {})
        self.assertFalse(row["resolved"])
        self.assertIsNone(row["cvr"], "nothing was measured, so there is no rate")
        self.assertIsNone(row["acos"])


if __name__ == "__main__":
    unittest.main()
