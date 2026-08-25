#!/usr/bin/env python3
"""UNKNOWN must not satisfy a condition — including a negated one.

Making the ratios answer NONE for a zero denominator closed a hole on the `<`
side and opened one on the other, because the equality and membership operators
were not three-valued:

    _eq(None, 0) is False, so `not _eq(None, 0)` is TRUE

which means

    FOR EACH adGroup:
      IF adGroup.cvr != 0:
        adGroup.pause()

matched every ad group nobody had ever clicked. `NOT IN` and `NOT CONTAINS` had
the same shape, and `NOT (...)` wrapped around ANY fail-closed False inverted it
into a match one level up. The relational operators were always right; it was
their negations that were not.

So comparisons now answer three ways — True, False, UNKNOWN — UNKNOWN
propagates through AND / OR / NOT the way SQL does, and `eval_condition`
collapses it to "does not match". That is the same fail-closed rule the engine
already applies to economics, snapshot dates, rolling windows and batch writes.

An author who writes the NONE literal is asking a real question and keeps a real
answer: `IF target.bid != NONE` still means "is a bid set".

None of the eight shipped rules uses `!=`, `<>`, `NOT IN`, `NOT CONTAINS`, `NOT`
or `NONE` — they use `IN` twice and relational operators everywhere else — and
all 56 rule previews across all seven markets returned identical counts before
and after. So this was latent, not live. It would not have stayed latent: the
whole point of the DSL is that the operator writes new rules.

Found by review, 2026-08-23.

Run from the Ads folder:
    python3 -m unittest tests.rules_unknown_logic_tests -v
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ.setdefault("ADS_MARKET", "US")

from rules import evaluator as ev      # noqa: E402
from rules import parser               # noqa: E402

U = None                                # UNKNOWN, spelled out for the tables


def cond(text, **scope):
    """Evaluate a condition the way a rule does: the real parser, the real AST.
    Hand-built calls to `_compare` cannot reach the NONE-literal branch or the
    Logic/Not nodes, and that is where half of this bug lived."""
    return ev.eval_condition(parser.parse_expr(text), dict(scope))


def raw(text, **scope):
    """The three-valued value BEFORE it is collapsed to a decision."""
    return ev.eval_expr(parser.parse_expr(text), dict(scope))


class UnknownNeverMatches(unittest.TestCase):
    """The property, stated once per operator family."""

    OPERATORS = ["x = 0", "x != 0", "x <> 0", "x < 1", "x <= 1", "x > 1",
                 "x >= 1", "x IN [0, 1]", "x NOT IN [0, 1]"]

    def test_no_operator_matches_an_unknown_value(self):
        for expr in self.OPERATORS:
            with self.subTest(expr=expr):
                self.assertFalse(
                    cond(expr, x=U),
                    f"`{expr}` matched a row whose value nobody measured. "
                    "Every write in this engine is gated on knowing.")

    def test_the_negations_are_the_ones_that_used_to_match(self):
        """Named separately because these are the regressions: each of these
        was True before the fix, and each sits in front of `pause()` in the
        most natural rule an author would write."""
        for expr in ("x != 0", "x <> 0", "x NOT IN [0, 1]"):
            with self.subTest(expr=expr):
                self.assertIsNone(raw(expr, x=U), "must be UNKNOWN, not False")
                self.assertFalse(cond(expr, x=U))

    def test_a_string_negation_does_not_match_a_missing_string(self):
        self.assertFalse(cond('x NOT CONTAINS "test"', x=U))
        self.assertFalse(cond('x CONTAINS "test"', x=U))


class AnAuthoredNoneTestStillAnswers(unittest.TestCase):
    """`NONE` is a literal in this language, so asking about it is a real
    question. Fail-closed must not eat the one idiom for "is this set"."""

    def test_a_value_is_not_none(self):
        self.assertTrue(cond("x != NONE", x=0.35))
        self.assertFalse(cond("x = NONE", x=0.35))

    def test_a_missing_value_is_none(self):
        self.assertTrue(cond("x = NONE", x=U))
        self.assertFalse(cond("x != NONE", x=U))

    def test_a_measured_zero_is_not_none(self):
        """The distinction the whole change turns on: 0.0 is an answer."""
        self.assertTrue(cond("x != NONE", x=0.0))
        self.assertTrue(cond("x = 0", x=0.0))
        self.assertFalse(cond("x != 0", x=0.0))


class OrdinaryComparisonsAreUntouched(unittest.TestCase):
    """The fix must cost nothing when the data is present, or it is not a fix,
    it is a behaviour change."""

    CASES = [("x = 5", 5, True), ("x = 5", 4, False),
             ("x != 5", 4, True), ("x != 5", 5, False),
             ("x < 5", 4, True), ("x < 5", 5, False),
             ("x <= 5", 5, True), ("x > 5", 6, True),
             ("x >= 5", 5, True), ("x >= 5", 4, False),
             ("x IN [1, 5]", 5, True), ("x IN [1, 5]", 2, False),
             ("x NOT IN [1, 5]", 2, True), ("x NOT IN [1, 5]", 5, False)]

    def test_every_case(self):
        for expr, value, want in self.CASES:
            with self.subTest(expr=expr, value=value):
                self.assertEqual(cond(expr, x=value), want)

    def test_strings_still_compare_case_insensitively(self):
        self.assertTrue(cond('x = "ENABLED"', x="enabled"))
        self.assertTrue(cond('x CONTAINS "abl"', x="ENABLED"))
        self.assertFalse(cond('x NOT CONTAINS "abl"', x="ENABLED"))


class ThreeValuedAndOrNot(unittest.TestCase):
    """SQL's tables. The interesting rows are the ones with UNKNOWN in them:
    FALSE AND UNKNOWN is FALSE (the other side already decided it), but
    TRUE AND UNKNOWN is UNKNOWN, and collapsing that to False would let a NOT
    one level up turn it into a match."""

    AND = [(True, True, True), (True, False, False), (False, True, False),
           (False, False, False), (True, U, U), (U, True, U),
           (False, U, False), (U, False, False), (U, U, U)]

    OR = [(True, True, True), (True, False, True), (False, True, True),
          (False, False, False), (True, U, True), (U, True, True),
          (False, U, U), (U, False, U), (U, U, U)]

    def test_and(self):
        for left, right, want in self.AND:
            with self.subTest(left=left, right=right):
                # `y != 0` with y unknown is the UNKNOWN operand.
                lhs = "y != 0" if left is U else ("1 = 1" if left else "1 = 0")
                rhs = "z != 0" if right is U else ("1 = 1" if right else "1 = 0")
                self.assertEqual(raw(f"{lhs} AND {rhs}", y=U, z=U), want)

    def test_or(self):
        for left, right, want in self.OR:
            with self.subTest(left=left, right=right):
                lhs = "y != 0" if left is U else ("1 = 1" if left else "1 = 0")
                rhs = "z != 0" if right is U else ("1 = 1" if right else "1 = 0")
                self.assertEqual(raw(f"{lhs} OR {rhs}", y=U, z=U), want)

    def test_not_of_unknown_stays_unknown(self):
        """The one that makes the rest necessary. `NOT (unknown)` must not be
        a match — otherwise every fail-closed False in this engine becomes a
        write as soon as somebody writes NOT in front of it."""
        self.assertIsNone(raw("NOT (y < 1)", y=U))
        self.assertFalse(cond("NOT (y < 1)", y=U))

    def test_not_of_a_known_value_still_inverts(self):
        self.assertTrue(cond("NOT (y < 1)", y=5))
        self.assertFalse(cond("NOT (y < 1)", y=0))


class AClicksFloorStillProtectsAsDocumented(unittest.TestCase):
    """The shape the DSL guide tells authors to write. It has to keep working,
    and it has to keep being unnecessary."""

    def test_a_floor_and_a_ratio_together(self):
        self.assertFalse(cond("clicks >= 15 AND cvr < 0.08", clicks=0, cvr=U))
        self.assertTrue(cond("clicks >= 15 AND cvr < 0.08", clicks=40, cvr=0.0))
        self.assertFalse(cond("clicks >= 15 AND cvr < 0.08", clicks=40, cvr=0.2))
        # The DECISION above was the same before this change, because the floor
        # short-circuits — so it documents the shape and guards nothing. What is
        # new is that the ratio half is UNKNOWN rather than false, which is what
        # stops a NOT one level up turning it into a match. Assert that.
        self.assertIsNone(raw("cvr < 0.08", cvr=U))
        self.assertIsNone(raw("clicks >= 15 AND cvr < 0.08", clicks=40, cvr=U))
        self.assertFalse(raw("clicks >= 15 AND cvr < 0.08", clicks=0, cvr=U),
                         "a floor that already failed decides it: FALSE, not "
                         "UNKNOWN — otherwise NOT(...) would match")

    def test_the_ratio_alone_is_now_safe_too(self):
        """Before the ratios answered NONE, `cvr < 8%` matched 37,330 US ad
        groups nobody had clicked. It now matches only measured ones.

        The `assertFalse` here passed before the change too — relational
        comparisons already refused a None operand. The guard is the raw value
        and the negation, which did not."""
        self.assertFalse(cond("cvr < 0.08", cvr=U))
        self.assertTrue(cond("cvr < 0.08", cvr=0.0))
        self.assertIsNone(raw("cvr < 0.08", cvr=U))
        self.assertFalse(cond("NOT (cvr < 0.08)", cvr=U),
                         "the shape that used to invert a fail-closed False "
                         "into a live match")


class UnknownCannotEscapeThroughAFunctionOrAString(unittest.TestCase):
    """The holes the FIRST version of this fix left open.

    Making comparisons three-valued was not enough, because a missing value
    could still be turned into a real one on the way to the comparison:

        x + ""                 -> the four characters "None" (an f-string)
        LOWER(x) / CONCAT(x,"") -> the same, via str()
        IF(x != 0, FALSE, TRUE) -> the else-branch, so TRUE

    Each made `IF ...: target.pause()` propose a live write on a row nobody had
    measured, and `IF(...)` did it with an empty trace behind it because the
    trace builder does not descend into a function call. Found by the second
    review pass, 2026-08-23.
    """

    def test_string_concatenation_does_not_invent_the_word_none(self):
        self.assertIsNone(raw('x + ""', x=U))
        self.assertFalse(cond('x + "" = "None"', x=U))

    def test_string_functions_are_strict(self):
        for expr in ('LOWER(x) = "none"', 'UPPER(x) = "NONE"',
                     'CONCAT(x, "") = "None"', 'REPLACE(x, "a", "b") = "None"'):
            with self.subTest(expr=expr):
                self.assertFalse(cond(expr, x=U))

    def test_IF_picks_neither_branch_on_an_unknown_condition(self):
        self.assertIsNone(raw("IF(x != 0, FALSE, TRUE)", x=U))
        self.assertFalse(cond("IF(x != 0, FALSE, TRUE)", x=U),
                         "this is the one that reached pause() in a valid rule")

    def test_IF_still_works_when_the_condition_is_known(self):
        self.assertTrue(cond("IF(x > 1, TRUE, FALSE)", x=5))
        self.assertFalse(cond("IF(x > 1, TRUE, FALSE)", x=0))

    def test_numeric_functions_stay_strict(self):
        self.assertIsNone(raw("MAX(x, 0.10)", x=U))
        self.assertIsNone(raw("ROUND(x, 2)", x=U))

    def test_a_relational_test_against_an_authored_none_is_not_a_null_test(self):
        """`bid < NONE` is nonsense, and answering it False let `NOT (...)`
        invert that into a match on a target whose bid was perfectly well
        known."""
        self.assertIsNone(raw("bid < NONE", bid=0.40))
        self.assertFalse(cond("NOT (bid < NONE)", bid=0.40))

    def test_none_survives_a_let_binding(self):
        """`LET missing = NONE` used to lose the marker, so a rule that matched
        before the fix silently stopped matching and preview gave no reason."""
        import rules.evaluator as _ev
        self.assertTrue(cond("bid = missing", bid=None, missing=_ev.NULL))
        self.assertFalse(cond("bid = missing", bid=0.40, missing=_ev.NULL))

    def test_the_marker_never_reaches_a_caller(self):
        """Action arguments and the debug trace are ordinary values; the marker
        is internal. A marker reaching the executor would not be caught by its
        `is None` skip, and would not survive json.dumps for the trace."""
        import json
        import rules.evaluator as _ev
        self.assertIsNone(_ev.plain(_ev.NULL))
        self.assertEqual(_ev.plain([_ev.NULL, 1]), [None, 1])
        json.dumps({"actual": _ev.plain(_ev.NULL)})


if __name__ == "__main__":
    unittest.main()
