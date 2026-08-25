#!/usr/bin/env python3
"""A preview must refuse everything the save will refuse.

`rules-validate` and `rules-save` run the parser AND `_semantic_errors` —
unknown fields, unknown or not-yet-executable action verbs, rolling windows on
entities with no per-day history, and windows reaching past Amazon's ~92-day
reporting retention.

`rules-preview` ran only the parser. So five classes of rule that can never be
saved still previewed, and two of them previewed as a confident number:

    target.explode()            -> "72 changes"
    target.createKeyword("x")   -> "72 changes"

Neither verb can execute. The reply was a count of writes that will never
happen, against a rule the Save button was about to reject.

The subtler one is worse, because it does not look like an error at all:

    target.clickz >= 12         -> "matched 0"

"matched 0" is exactly what a correct rule matching nothing looks like — the
same screen an operator reads as "no rows meet my condition" when the truth is
that the field is misspelt. The DSL's own design note says unknown fields are
rejected at validation "not discovered as a nightly 'unsupported' later". A
preview that skips that check moves the discovery to the Save button instead.

Found by the 2026-08-23 DSL audit, by running the same rule through both
commands and diffing the answers.

Run from the Ads folder:
    python3 -m unittest tests.rules_preview_validate_tests -v
"""

import os
import sqlite3
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ["ADS_MARKET"] = "US"

import db                       # noqa: E402
from rules import runner as rr     # noqa: E402
from rules import entities as ent  # noqa: E402


def mk_conn(case):
    """One connection per test, closed on the way out — an unclosed one warns
    on every run and warnings that always fire get read as background noise."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(db.SCHEMA)
    case.addCleanup(conn.close)
    return conn


# Each is REFUSED by validate, and each previewed happily before this test.
REJECTED = {
    "an over-retention rolling window": '''FOR EACH target IN LAST 200 DAYS:
  IF target.clicks >= 1:
    target.pause()''',

    "a misspelt field": '''FOR EACH target:
  IF target.clickz >= 12:
    target.pause()''',

    "an action verb that does not exist": '''FOR EACH target:
  IF target.clicks >= 12:
    target.explode()''',

    "a verb that exists but cannot execute": '''FOR EACH target:
  IF target.clicks >= 12:
    target.createKeyword("x")''',

    "a rolling window on an entity with no per-day history":
    '''FOR EACH searchTerm IN LAST 7 DAYS:
  IF searchTerm.clicks >= 1:
    searchTerm.addNegative(searchTerm.search_term, "exact")''',
}

# Sound rules, which must keep previewing. Real shapes, taken from the live set.
ACCEPTED = {
    "a plain current-window rule": '''FOR EACH target:
  IF target.clicks >= 12 AND target.orders = 0 AND target.state = "ENABLED":
    target.pause()''',

    "a rolling window inside retention": '''FOR EACH target IN LAST 14 DAYS:
  IF econ_available AND target.clicks >= 15 AND target.profit < $0:
    target.pause()''',

    "a window at exactly the cap": '''FOR EACH target IN LAST 92 DAYS:
  IF target.clicks >= 1:
    target.pause()''',

    "economics in the condition and the argument": '''FOR EACH target:
  IF econ_available AND target.acos > break_even * 1.5 AND target.clicks >= 15:
    target.setBid(MAX($0.10, target.bid * 0.85))''',
}


class PreviewRefusesWhatSaveRefuses(unittest.TestCase):

    def test_validate_rejects_all_of_them(self):
        """The premise. If validate ever stops refusing one of these, the
        comparison below proves nothing."""
        for label, src in REJECTED.items():
            with self.subTest(case=label):
                self.assertFalse(rr.validate(src)["ok"],
                                 f"validate now ACCEPTS {label} — this test's "
                                 "premise has changed")

    def test_preview_rejects_all_of_them_too(self):
        conn = mk_conn(self)
        for label, src in REJECTED.items():
            with self.subTest(case=label):
                got = rr.preview(conn, src)
                self.assertFalse(
                    got["ok"],
                    f"preview ACCEPTED {label}. A preview is what the author "
                    "believes about a rule before saving it, so it has to "
                    "refuse what the save refuses.")
                self.assertTrue(got.get("errors"),
                                "a refusal must say why")
                self.assertEqual(got.get("matched"), 0)
                self.assertEqual(got.get("changes"), [])

    def test_a_refused_preview_proposes_nothing(self):
        """The two verb cases are the reason. Both previewed as a count of
        changes, for writes that can never happen."""
        conn = mk_conn(self)
        for label in ("an action verb that does not exist",
                      "a verb that exists but cannot execute"):
            with self.subTest(case=label):
                got = rr.preview(conn, REJECTED[label])
                self.assertEqual(len(got.get("changes") or []), 0,
                                 "a rule whose action cannot run must not "
                                 "report proposed changes")

    def test_sound_rules_still_preview(self):
        """The fix must not make preview stricter than save. Every live rule
        passed validation when it was saved, so every live rule must preview."""
        conn = mk_conn(self)
        for label, src in ACCEPTED.items():
            with self.subTest(case=label):
                self.assertTrue(rr.validate(src)["ok"],
                                f"{label} should validate")
                got = rr.preview(conn, src)
                self.assertTrue(got["ok"],
                                f"preview now refuses {label}: {got.get('errors')}")

    def test_the_two_commands_agree_on_every_case(self):
        """The property, stated once: for any rule, validate and preview reach
        the same verdict. This is what stops the two drifting apart again."""
        conn = mk_conn(self)
        for label, src in {**REJECTED, **ACCEPTED}.items():
            with self.subTest(case=label):
                self.assertEqual(
                    rr.validate(src)["ok"], rr.preview(conn, src)["ok"],
                    f"validate and preview disagree about {label}")


class ASyntaxErrorStillReportsItsPosition(unittest.TestCase):
    """The parse branch predates this and must keep its line/col, which is what
    the editor underlines."""

    def test_a_parse_error_keeps_line_and_col(self):
        got = rr.preview(mk_conn(self), "FOR EACH target\n  IF ???:\n")
        self.assertFalse(got["ok"])
        e = got["errors"][0]
        self.assertIn("line", e)
        self.assertIn("col", e)
        self.assertTrue(e.get("message"))


class AZeroDenominatorIsUnknown(unittest.TestCase):
    """Every ratio answers NONE when its denominator is zero, and NONE matches
    no numeric comparison — so a rule skips the row instead of acting on a
    number nobody measured.

    `cvr` and `ctr` used to answer 0.0. That is not "unknown", it is the worst
    possible score, on exactly the two metrics an author writes with `<`
    because low is bad. So

        IF adGroup.cvr < 8%

    was TRUE for every ad group that had never been clicked — 37,330 of them on
    the live US account — against a rule whose action is `pause()`. The engine
    fails closed on unknown data everywhere else: economics, snapshot dates,
    rolling windows, batch writes. This was the one place it failed open, and it
    failed toward switching ads off.

    Changing it moved nothing: all 53 rule previews across all seven markets
    returned identical counts before and after, because the only shipped rule
    that reads `cvr` demands `clicks >= 15` first. Measured, not assumed.
    """

    def test_every_ratio_answers_none_on_a_zero_denominator(self):
        self.assertIsNone(ent._acos(1.0, 0), "acos with no sales")
        self.assertIsNone(ent._cpc(1.0, 0), "cpc with no clicks")
        self.assertIsNone(ent._roas(1.0, 0), "roas with no spend")
        self.assertIsNone(ent._cvr(0, 0), "cvr with no clicks")
        self.assertIsNone(ent._ctr(0, 0), "ctr with no impressions")

    def test_a_measured_zero_is_still_zero(self):
        """The case that must NOT change. Forty clicks and no orders is a real
        0% conversion, and a rule looking for poor converters should find it."""
        self.assertEqual(ent._cvr(0, 40), 0.0)
        self.assertEqual(ent._ctr(0, 900), 0.0)
        self.assertEqual(ent._acos(5.0, 20.0), 0.25)

    def test_an_unknown_cvr_cannot_pass_a_less_than_test(self):
        """The consequence, stated as an assertion. `None < 0.08` is what the
        evaluator is handed, and it must not be true."""
        self.assertIsNone(ent._cvr(0, 0))
        measured = ent._cvr(0, 40)
        self.assertIsNotNone(measured)
        self.assertLess(measured, 0.08)

    def test_every_module_that_computes_a_ratio_agrees(self):
        """The half that was missed the first time.

        `rules/entities.py` was fixed and `appctl.py` was not, so the write side
        answered NONE for an unclicked row while every READ endpoint — campaigns,
        ad groups, targets, accumulated, watchlist, reports — went on answering
        0.0 for the same row. A design nobody had clicked sorted beside one
        clicked forty times without selling, and the CSV exported 0% for both.
        Two modules, one question, two answers. Found by review, 2026-08-23.
        """
        import importlib
        appctl = importlib.import_module("appctl")
        for name, args in (("_cvr", (0, 0)), ("_acos", (1.0, 0))):
            with self.subTest(fn=name):
                self.assertIsNone(getattr(appctl, name)(*args),
                                  f"appctl.{name} still answers a measured "
                                  "number where it has no data")
                self.assertIsNone(getattr(ent, name)(*args),
                                  f"rules.entities.{name} regressed")

    def test_a_measured_zero_survives_in_both(self):
        import importlib
        appctl = importlib.import_module("appctl")
        self.assertEqual(appctl._cvr(0, 40), 0.0)
        self.assertEqual(ent._cvr(0, 40), 0.0)

    def test_the_documentation_agrees(self):
        """The table in the DSL guide is where an author learns this, and it
        was the half that was wrong before — it already claimed NONE."""
        path = os.path.join(HERE, "docs", "rules-dsl.md")
        with open(path, encoding="utf-8") as fh:
            doc = fh.read()
        self.assertIn("A ratio with a zero denominator is `NONE`", doc)
        self.assertIn("A measured zero is different", doc)


class NoDataIsNotNothingQualifying(unittest.TestCase):
    """`matched: 0` had two meanings and said neither.

    A bare `MAX(date)` over an EMPTY snapshot table returns None, every
    `date = ?` query then matches zero rows, and the rule reports
    `evaluated: 0, matched: 0` with `ok: True`. That is indistinguishable from a
    correct rule that looked at the whole account and found nothing — and for a
    REVIEW rule it clears the pending queue as though the engine had looked.

    Refusing the preview would be the WRONG fix. `validate` and `preview` are
    required to reach the same verdict for any rule, and validate never touches
    data, so a data-driven refusal would break that on every sound rule. The run
    still succeeds; the reply says what it could not see.
    """

    SRC = '''FOR EACH target:
  IF target.clicks >= 12:
    target.pause()'''

    def test_an_empty_snapshot_table_is_named_in_the_reply(self):
        conn = mk_conn(self)
        got = rr.preview(conn, self.SRC)

        self.assertTrue(got["ok"], "a sound rule must still preview")
        self.assertEqual(0, got["matched"])
        self.assertTrue(got["no_evidence"],
                        "matched:0 over an empty table must say so")
        self.assertEqual("targeting_perf", got["no_evidence"][0]["table"])
        self.assertIn("not the same as nothing qualifying",
                      got["no_evidence"][0]["reason"])

    def test_a_table_with_a_snapshot_reports_no_gap(self):
        conn = mk_conn(self)
        conn.execute("INSERT INTO targeting_perf (date, target_id) VALUES (?,?)",
                     ("2026-08-22", "t1"))
        conn.commit()
        got = rr.preview(conn, self.SRC)
        self.assertTrue(got["ok"])
        self.assertEqual([], got["no_evidence"],
                         "there IS evidence here — nothing should be reported")

    def test_the_key_is_always_present_so_callers_need_not_guess(self):
        conn = mk_conn(self)
        self.assertIn("no_evidence", rr.preview(conn, self.SRC))

    def test_validate_and_preview_still_agree(self):
        """The invariant this fix had to preserve."""
        conn = mk_conn(self)
        self.assertEqual(rr.validate(self.SRC)["ok"], rr.preview(conn, self.SRC)["ok"])


if __name__ == "__main__":
    unittest.main()
