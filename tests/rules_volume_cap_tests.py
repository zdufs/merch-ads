#!/usr/bin/env python3
"""Nothing limited how MANY changes one automatic run could apply.

Every other guard in this engine is about VALUE or SAFETY: the KILL file, the
economics gate, the snapshot freshness gate, the cross-rule conflict guard, the
per-market bid ceiling, the no-op check. Not one of them counts.

So a rule whose condition was one character too loose — `>= 1` where `>= 15` was
meant — would match tens of thousands of targets, and every gate above would
wave it through. The data is fresh, the economics are available, no two rules
disagree, and a pause is not a bid so no ceiling touches it. Six rules run on
AUTO nightly across seven markets with nobody looking.

The old ceiling was `cap=50000`, and at the cap the executor applied the first N
and set `truncated: True`. That is the worst of both: half an account paused, no
refusal, and a flag nobody reads. This replaces it with a refusal.

Where the number comes from
---------------------------
Measured, not guessed. Across every market's `writes_log`, counting only the
actions a rule can emit, the busiest day on record is US 2026-06-29 at 255 —
and that includes the hardcoded phases, not just the DSL. Every EU market peaks
at 26. A normal night across the whole account is 4 to 49 writes.

`db.AUTO_CHANGE_CAP_DEFAULT` is 500: about twice the busiest day ever seen, and
about a hundred times a normal night. Large enough that a real night never meets
it, small enough that a runaway stops.

Refusing is the point
---------------------
A run that trips the cap applies NOTHING. A partial apply would leave the
account in a state no rule described and no operator chose. And the case where
the cap fires legitimately — a new rule enabled for the first time, matching 800
real kill candidates — is exactly the case a human should see before it runs.

Run from the Ads folder:  python3 -m unittest tests.rules_volume_cap_tests -v
"""

import ast
import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ["ADS_MARKET"] = "US"

import db                                                    # noqa: E402
from rules import executor                                   # noqa: E402
from tests.rules_executor_tests import (temp_conn, FakeClient,  # noqa: E402
                                        _change, seed_fresh_snapshots)


def _many(n):
    return [_change("pause", eid=f"t{i}",
                    ref={"campaign_id": "c1", "ad_group_id": "g1",
                         "target_id": f"t{i}", "asin": None})
            for i in range(n)]


class TheCapIsStoredPerMarket(unittest.TestCase):

    def test_an_unset_cap_is_the_shipped_default(self):
        conn, path = temp_conn()
        try:
            self.assertEqual(db.get_auto_change_cap(conn),
                             db.AUTO_CHANGE_CAP_DEFAULT)
        finally:
            conn.close(); os.unlink(path)

    def test_the_operator_can_raise_or_lower_it(self):
        conn, path = temp_conn()
        try:
            db.set_auto_change_cap(conn, 25)
            self.assertEqual(db.get_auto_change_cap(conn), 25)
            db.set_auto_change_cap(conn, None)
            self.assertEqual(db.get_auto_change_cap(conn),
                             db.AUTO_CHANGE_CAP_DEFAULT)
        finally:
            conn.close(); os.unlink(path)

    def test_zero_means_no_cap_and_is_a_deliberate_choice(self):
        """Same shape as the bid ceiling, where null means NO ceiling.

        The operator is allowed to turn a guard off. What they are not allowed
        to do is turn it off by accident, so 0 has to be typed.
        """
        conn, path = temp_conn()
        try:
            db.set_auto_change_cap(conn, 0)
            self.assertEqual(db.get_auto_change_cap(conn), 0)
        finally:
            conn.close(); os.unlink(path)

    def test_a_corrupt_value_falls_back_to_the_default_rather_than_off(self):
        """A guard that fails OPEN is not a guard."""
        conn, path = temp_conn()
        try:
            db.meta_set(conn, "auto_change_cap", "not a number")
            self.assertEqual(db.get_auto_change_cap(conn),
                             db.AUTO_CHANGE_CAP_DEFAULT)
        finally:
            conn.close(); os.unlink(path)

    def test_a_fraction_is_corrupt_and_never_rounds_down_into_off(self):
        """`int(float(v))` turned "0.5" and "1e-20" into 0, which means NO CAP.

        The one way this guard could fail open: a corrupt setting that reads as
        the operator deliberately switching it off. Only a typed 0 may do that.
        """
        for bad in ("0.5", "-0.5", "1e-20", "1e-324", "nan", "inf", "499.9"):
            conn, path = temp_conn()
            try:
                db.meta_set(conn, "auto_change_cap", bad)
                self.assertEqual(db.get_auto_change_cap(conn),
                                 db.AUTO_CHANGE_CAP_DEFAULT, bad)
                db.meta_set(conn, "auto_build_cap", bad)
                self.assertEqual(db.get_auto_build_cap(conn),
                                 db.AUTO_BUILD_CAP_DEFAULT, bad)
            finally:
                conn.close(); os.unlink(path)

    def test_both_caps_move_in_one_transaction_or_neither(self):
        """`change-cap` can move both numbers in one command. Two independent
        commits mean a failure on the second leaves the first standing under a
        command that reported an error."""
        conn, path = temp_conn()
        try:
            db.set_auto_caps(conn, change=11, build=22)
            self.assertEqual(11, db.get_auto_change_cap(conn))
            self.assertEqual(22, db.get_auto_build_cap(conn))
            db.set_auto_caps(conn, build=33)          # change left alone
            self.assertEqual(11, db.get_auto_change_cap(conn))
            self.assertEqual(33, db.get_auto_build_cap(conn))
            db.set_auto_caps(conn, change=None)       # cleared, build untouched
            self.assertEqual(db.AUTO_CHANGE_CAP_DEFAULT, db.get_auto_change_cap(conn))
            self.assertEqual(33, db.get_auto_build_cap(conn))
        finally:
            conn.close(); os.unlink(path)

    def test_a_failure_on_the_second_cap_commits_neither(self):
        """The reason the two writes share a transaction. Read from a SECOND
        connection, because the writing one can see its own open transaction."""
        import sqlite3 as _sq
        conn, path = temp_conn()
        try:
            db.set_auto_caps(conn, change=11, build=22)
            real = db._set_cap

            def explode(c, key, value, commit=True):
                if key == db._AUTO_BUILD_CAP_KEY:
                    raise _sq.OperationalError("disk I/O error")
                return real(c, key, value, commit=commit)

            db._set_cap = explode
            try:
                with self.assertRaises(_sq.OperationalError):
                    db.set_auto_caps(conn, change=999, build=888)
            finally:
                db._set_cap = real
            other = _sq.connect(path)
            try:
                self.assertEqual(11, db.get_auto_change_cap(other),
                                 "the first cap was committed under a failed command")
                self.assertEqual(22, db.get_auto_build_cap(other))
            finally:
                other.close()
        finally:
            conn.close(); os.unlink(path)

    def test_the_build_cap_reads_the_same_way(self):
        conn, path = temp_conn()
        try:
            self.assertEqual(db.get_auto_build_cap(conn), db.AUTO_BUILD_CAP_DEFAULT)
            db.set_auto_build_cap(conn, 1234)
            self.assertEqual(db.get_auto_build_cap(conn), 1234)
            self.assertEqual(db.get_auto_change_cap(conn),
                             db.AUTO_CHANGE_CAP_DEFAULT,
                             "setting one cap moved the other")
            db.set_auto_build_cap(conn, None)
            self.assertEqual(db.get_auto_build_cap(conn), db.AUTO_BUILD_CAP_DEFAULT)
        finally:
            conn.close(); os.unlink(path)

    def test_a_database_with_no_engine_meta_still_gets_the_shipped_cap(self):
        """Not being able to READ the setting is not permission to run uncapped.

        Several test fixtures build a bare connection with only the tables they
        need, and a database older than engine_meta would look the same. The
        first draft of this returned 0 there, which is "no cap" — the guard
        would have switched itself off on exactly the connections nobody had
        looked at closely.
        """
        import sqlite3 as _sq
        conn = _sq.connect(":memory:")
        try:
            self.assertEqual(db.get_auto_change_cap(conn),
                             db.AUTO_CHANGE_CAP_DEFAULT)
        finally:
            conn.close()


class ARunOverTheCapAppliesNothing(unittest.TestCase):

    def test_a_run_past_the_cap_is_refused_whole(self):
        conn, path = temp_conn()
        try:
            seed_fresh_snapshots(conn)
            db.set_auto_change_cap(conn, 3)
            fc = FakeClient()
            res = executor.execute(conn, _many(5), market="US", client=fc)
            self.assertFalse(res["applied"])
            self.assertEqual(res["blocked"], "change_volume")
            self.assertEqual(res["count"], 5)
            self.assertEqual(res["cap"], 3)
            self.assertEqual(fc.calls, [],
                             "the executor called Amazon despite refusing the run")
        finally:
            conn.close(); os.unlink(path)

    def test_the_refusal_says_what_to_do_about_it(self):
        conn, path = temp_conn()
        try:
            seed_fresh_snapshots(conn)
            db.set_auto_change_cap(conn, 2)
            res = executor.execute(conn, _many(9), market="US",
                                   client=FakeClient())
            msg = res.get("message") or ""
            self.assertIn("9", msg)
            self.assertIn("2", msg)
            self.assertTrue(len(msg) > 40,
                            "a refusal with no reason trains the reader to "
                            "raise the cap without thinking")
        finally:
            conn.close(); os.unlink(path)

    def test_a_run_at_the_cap_still_runs(self):
        """The cap is a ceiling, not a fence one short of it."""
        conn, path = temp_conn()
        try:
            seed_fresh_snapshots(conn)
            db.set_auto_change_cap(conn, 5)
            fc = FakeClient()
            res = executor.execute(conn, _many(5), market="US", client=fc)
            self.assertNotEqual(res.get("blocked"), "change_volume")
            self.assertTrue(fc.calls, "nothing was applied at exactly the cap")
        finally:
            conn.close(); os.unlink(path)

    def test_a_normal_night_is_nowhere_near_it(self):
        """49 writes is the busiest ordinary night measured across all markets."""
        conn, path = temp_conn()
        try:
            seed_fresh_snapshots(conn)
            fc = FakeClient()
            res = executor.execute(conn, _many(49), market="US", client=fc)
            self.assertNotEqual(res.get("blocked"), "change_volume")
        finally:
            conn.close(); os.unlink(path)

    def test_the_busiest_day_on_record_would_still_pass(self):
        """US 2026-06-29 applied 255 rule-eligible writes. That must not trip."""
        self.assertGreater(db.AUTO_CHANGE_CAP_DEFAULT, 255)

    def test_a_cap_of_zero_lets_a_huge_run_through(self):
        conn, path = temp_conn()
        try:
            seed_fresh_snapshots(conn)
            db.set_auto_change_cap(conn, 0)
            fc = FakeClient()
            res = executor.execute(conn, _many(600), market="US", client=fc)
            self.assertNotEqual(res.get("blocked"), "change_volume")
        finally:
            conn.close(); os.unlink(path)

    def test_an_explicit_cap_argument_still_wins(self):
        """`rules-approve` passes cap=0: the operator picked those ids by hand."""
        conn, path = temp_conn()
        try:
            seed_fresh_snapshots(conn)
            db.set_auto_change_cap(conn, 2)
            fc = FakeClient()
            res = executor.execute(conn, _many(9), market="US", client=fc, cap=0)
            self.assertNotEqual(res.get("blocked"), "change_volume")
        finally:
            conn.close(); os.unlink(path)


class NothingIsSilentlyTruncatedAnyMore(unittest.TestCase):
    """The behaviour this replaces, kept as a test so it cannot come back.

    `execute` used to apply `changes[:cap]` and set `truncated: True`. Half an
    account acted on, no refusal, and a flag that reached no screen.
    """

    def test_a_run_over_the_cap_never_half_applies(self):
        conn, path = temp_conn()
        try:
            seed_fresh_snapshots(conn)
            db.set_auto_change_cap(conn, 3)
            fc = FakeClient()
            res = executor.execute(conn, _many(10), market="US", client=fc)
            self.assertEqual(res.get("count"), 10,
                             "count must report what was PROPOSED, not what "
                             "a truncation would have kept")
            self.assertEqual(res.get("results"), [])
            self.assertEqual(fc.calls, [])
        finally:
            conn.close(); os.unlink(path)


class ApprovingByHandIsNotAnAutomaticRun(unittest.TestCase):
    """`rules-approve` must pass cap=0, and nothing else may.

    The cap exists because AUTO rules apply with nobody looking. Every id in an
    approve call was selected in the Approval Queue, so the human gate has
    already happened and refusing the batch would block a deliberate act.

    This reads the source rather than running the command, because approving
    needs a pending store, a live client and an approved plan. A source check
    still fails the day someone deletes the exemption or copies it somewhere it
    does not belong.
    """

    def _appctl_source(self):
        with open(os.path.join(HERE, "engine", "appctl.py"), encoding="utf-8") as f:
            return ast.parse(f.read())

    def _execute_calls(self, tree):
        """[(enclosing function, {kwarg: literal})] for every rex.execute call."""
        found = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for sub in ast.walk(node):
                if not isinstance(sub, ast.Call):
                    continue
                fn = sub.func
                if not (isinstance(fn, ast.Attribute) and fn.attr == "execute"):
                    continue
                if not (isinstance(fn.value, ast.Name) and fn.value.id in ("rex", "executor")):
                    continue
                kw = {k.arg: getattr(k.value, "value", "?") for k in sub.keywords}
                found.append((node.name, kw))
        return found

    def test_only_the_approve_path_is_exempt(self):
        calls = self._execute_calls(self._appctl_source())
        self.assertGreaterEqual(len(calls), 3,
                                "the parse found almost no execute() calls — "
                                "this check would pass forever")
        for func, kw in calls:
            if func == "cmd_rules_approve":
                self.assertEqual(kw.get("cap"), 0,
                                 "rules-approve must pass cap=0: the operator "
                                 "already picked these ids by hand")
            else:
                self.assertIsNone(
                    kw.get("cap"),
                    f"{func} passes an explicit cap, which skips the volume "
                    f"guard. Only rules-approve may do that.")


class ABlockedNightlyDoesNotClaimItApplied(unittest.TestCase):
    """A refusal has to reach the reply, and must not be read as a success.

    `rules-nightly` builds its own summary rather than spreading the executor's
    reply, and reading a BLOCKED result like a normal one lies twice. `count` on
    a refusal is what was PROPOSED, so it becomes `total_applied` — 700 pauses
    announced, none made. And `results` is empty, so `zip(kept, results)` runs
    zero times and the reason reaches no rule row.

    Found 2026-08-22 while asking what a capped night would actually look like:
    a large, confident success with no explanation anywhere in it.
    """

    def _run(self, res, n=700):
        import appctl
        kept = [{"rule": "Pause kill candidates"} for _ in range(n)]
        summary = [{"rule": "Pause kill candidates", "mode": "auto",
                    "matched": n, "applied": 0, "skipped_conflict": 0}]
        by_rule = {s["rule"]: s for s in summary}
        total, extra = appctl._nightly_apply_summary(res, kept, by_rule, summary)
        return total, extra, summary

    def test_a_refused_run_reports_zero_applied(self):
        total, extra, summary = self._run(
            {"applied": False, "blocked": "change_volume", "count": 700,
             "cap": 500, "results": [], "message": "over the limit"})
        self.assertEqual(total, 0, "the nightly reported changes it never made")
        self.assertEqual(summary[0]["applied"], 0)

    def test_the_reason_and_the_numbers_reach_the_reply(self):
        total, extra, summary = self._run(
            {"applied": False, "blocked": "change_volume", "count": 700,
             "cap": 500, "results": [], "message": "over the limit"})
        self.assertEqual(extra["blocked"], "change_volume")
        self.assertEqual(extra["blocked_cap"], 500)
        self.assertEqual(extra["blocked_proposed"], 700)
        self.assertIn("limit", extra["blocked_message"])
        self.assertEqual(summary[0]["blocked"], "change_volume",
                         "the rule's own row does not say it was blocked")

    def test_the_kill_freeze_is_reported_the_same_way(self):
        """KILL returns the same shape. It lost its reason here too."""
        total, extra, summary = self._run(
            {"applied": False, "blocked": "kill", "count": 0, "results": [],
             "message": "KILL freeze is active"})
        self.assertEqual(total, 0)
        self.assertEqual(extra["blocked"], "kill")

    def test_a_normal_run_is_unchanged(self):
        res = {"applied": True, "count": 2, "results": [
            {"status": "applied"}, {"status": "applied"},
            {"status": "skipped_noop"}]}
        total, extra, summary = self._run(res, n=3)
        self.assertEqual(total, 2)
        self.assertEqual(extra, {})
        self.assertEqual(summary[0]["applied"], 2)
        self.assertNotIn("blocked", summary[0])
