#!/usr/bin/env python3
"""The approved phase-2 plan is re-checked against the tables it came FROM.

`negatives-apply` takes ids the operator approved on a screen that can sit open
across a nightly pull, so it refuses a plan whose evidence has moved. The first
version of that guard compared the plan's `as_of` against `search_term_perf`
alone.

`as_of` is the OLDER of two dates (phase2_apply.candidates), because negatives
are read from `search_term_perf` and pauses from `targeting_perf` — two tables
filled by two independent Amazon report jobs. They drift: the US database holds
9 dates present in search_term_perf and absent from targeting_perf, and 3 the
other way. So the one-table comparison failed in both directions at once.

  * targeting behind → `as_of` is the targeting date, never equals the
    search-term date, and EVERY apply is refused. Re-previewing reproduces the
    same mismatch, so the approval queue cannot be applied at all.
  * search terms behind → `as_of` matches by coincidence and the PAUSE half,
    resolved against a targeting table that may have moved, is never checked.

Today all seven markets happen to agree, which is why this was quiet.

The decision is unit-tested rather than driven through `negatives-apply`,
because that command needs a live Amazon client and an approved plan. Proved by
mutation: putting the old one-table comparison back into `_evidence_checks`
fails `test_a_moved_targeting_snapshot_refuses_the_pauses` and
`test_a_plan_is_not_refused_because_the_OTHER_table_moved`.

Run from the Ads folder:  python3 -m unittest tests.negatives_evidence_gate_tests -v
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))

import appctl  # noqa: E402

OLD = "2026-08-20"
NEW = "2026-08-23"


def judge(plan, has_negatives=True, has_pauses=True,
          current_st=NEW, current_tg=NEW):
    current_as_of = min([d for d in (current_st, current_tg) if d], default=None)
    checks = appctl._evidence_checks(plan, has_negatives, has_pauses,
                                     current_st, current_tg, current_as_of)
    return appctl._stale_evidence(checks)


def plan_from_preview(as_of_st, as_of_tg):
    """What `negatives-preview` publishes, and the app sends back."""
    return {"as_of": min(as_of_st, as_of_tg),
            "as_of_search_terms": as_of_st,
            "as_of_targeting": as_of_tg}


class EachHalfIsCheckedAgainstItsOwnTable(unittest.TestCase):

    def test_a_fresh_plan_is_applied(self):
        self.assertIsNone(judge(plan_from_preview(NEW, NEW)))

    def test_a_moved_search_term_snapshot_refuses_the_negatives(self):
        msg = judge(plan_from_preview(OLD, NEW), current_st=NEW, current_tg=NEW)
        self.assertIsNotNone(msg)
        self.assertIn("the negatives", msg)
        self.assertIn("search_term_perf", msg)

    def test_a_moved_targeting_snapshot_refuses_the_pauses(self):
        """The half the one-table comparison could never see."""
        msg = judge(plan_from_preview(NEW, OLD), current_st=NEW, current_tg=NEW)
        self.assertIsNotNone(msg)
        self.assertIn("the pauses", msg)
        self.assertIn("targeting_perf", msg)

    def test_a_plan_is_not_refused_because_the_OTHER_table_moved(self):
        """The deadlock. Targeting is behind, so the preview's `as_of` is the
        targeting date — which will never equal the search-term date, however
        many times the operator re-previews. Both halves are current here, so
        nothing may be refused."""
        self.assertIsNone(judge(plan_from_preview(NEW, OLD),
                                current_st=NEW, current_tg=OLD))

    def test_only_the_half_that_is_being_applied_is_checked(self):
        """Approving negatives alone must not be refused because the targeting
        table moved — no pause is being written from it."""
        self.assertIsNone(judge(plan_from_preview(NEW, OLD),
                                has_pauses=False, current_st=NEW, current_tg=NEW))
        self.assertIsNone(judge(plan_from_preview(OLD, NEW),
                                has_negatives=False, current_st=NEW, current_tg=NEW))


class OlderClientsAndMissingDates(unittest.TestCase):

    def test_a_plan_with_only_as_of_is_compared_the_way_it_was_built(self):
        """An app older than the per-table fields sends one date, and the
        preview built it as the OLDER of the two. Comparing it against the
        older of the two CURRENT dates is the same arithmetic."""
        self.assertIsNone(judge({"as_of": OLD}, current_st=NEW, current_tg=OLD))
        msg = judge({"as_of": OLD}, current_st=NEW, current_tg=NEW)
        self.assertIsNotNone(msg)
        self.assertIn("this plan", msg)

    def test_a_plan_with_no_dates_at_all_proceeds(self):
        """Refusing every older client would be worse than the drift."""
        self.assertIsNone(judge({}))

    def test_a_table_with_no_snapshot_cannot_refuse(self):
        """A check needs both sides. None on either is "cannot judge", not
        "moved" — otherwise a market mid-backfill could never apply."""
        self.assertIsNone(judge(plan_from_preview(NEW, NEW),
                                current_st=None, current_tg=None))


class ThePreviewPublishesBothDates(unittest.TestCase):
    """The apply can only check what the preview sends."""

    def test_cmd_negatives_preview_reports_each_table(self):
        import ast
        with open(os.path.join(HERE, "engine", "appctl.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "cmd_negatives_preview")
        keys = {k.value for n in ast.walk(fn) if isinstance(n, ast.Dict)
                for k in n.keys if isinstance(k, ast.Constant)}
        for key in ("as_of", "as_of_search_terms", "as_of_targeting"):
            self.assertIn(key, keys,
                          f"negatives-preview stopped publishing {key}, so the "
                          f"apply-time check for that half cannot run")


if __name__ == "__main__":
    unittest.main()
