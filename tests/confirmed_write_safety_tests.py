#!/usr/bin/env python3
"""Regression tests for the confirmed 2026-08-24 write-safety defects.

All clients and databases are local fakes. No test can call Amazon or write an
operator database.
"""

import contextlib
import io
import os
import sqlite3
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ["ADS_MARKET"] = "US"

import ads_client  # noqa: E402
import appctl  # noqa: E402
import db  # noqa: E402
import harvest_prune  # noqa: E402
import phase2_apply  # noqa: E402
import preempt_negatives  # noqa: E402
import reset_inflated_bids  # noqa: E402
import scavenger_optimize  # noqa: E402
from rules import executor  # noqa: E402


def memory_db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(db.SCHEMA)
    return conn


def batch(ids, failed=(), outcomes=None, created_ids=None):
    failed = {str(i) for i in failed}
    return {
        "http": 207,
        "count": len(ids),
        "ids": [str(i) for i in ids],
        "failed_items": len(failed),
        "failed_ids": [str(i) for i in ids if str(i) in failed],
        "outcomes": outcomes,
        "created_ids": created_ids or [None] * len(ids),
    }


class Response:
    def __init__(self, status, body):
        self.status_code = status
        self.body = body
        self.text = ""

    def json(self):
        return self.body


class FanoutVolume(unittest.TestCase):
    def _change(self):
        return {"action": "pauseEverywhere", "entity_kind": "accumulated_asin",
                "entity_id": "B000000001", "args": []}

    def test_every_fanout_write_counts_against_the_cap(self):
        plan = {"ops": [{"skip": i >= 800} for i in range(805)]}
        with mock.patch.object(appctl, "_everywhere_plan", return_value=plan):
            self.assertEqual(executor._write_volume(None, [self._change()]), 800)

    def test_a_raising_resolver_refuses_before_any_write(self):
        class Client:
            calls = []

        with mock.patch.object(appctl, "_everywhere_plan",
                               side_effect=RuntimeError("resolver broke")):
            got = executor.execute(None, [self._change()], market="US",
                                   client=Client(), cap=500)
        self.assertFalse(got["applied"])
        self.assertEqual(got["blocked"], "change_volume_unresolved")
        self.assertEqual(Client.calls, [])


class CeilingReadFailure(unittest.TestCase):
    def test_an_unreadable_ceiling_blocks_the_request(self):
        client = ads_client.AdsClient.__new__(ads_client.AdsClient)
        client._ceilings = {}
        client.last_clamps = []
        sent = []
        client._send_retry = lambda *a, **k: sent.append((a, k))
        with mock.patch.object(db, "connect", side_effect=sqlite3.OperationalError("locked")):
            with self.assertRaises(ads_client.CeilingReadError):
                client.update_target_bids([{"targetId": "t1", "bid": 9.99}])
        self.assertEqual(sent, [])


class Unreadable207(unittest.TestCase):
    UNKNOWN = [{"http": 207, "failed_items": None, "failed_ids": [], "ids": ["x"]}]

    def test_appctl_http_check_refuses_it(self):
        self.assertFalse(appctl._http_ok(self.UNKNOWN))

    def test_appctl_outcome_is_unconfirmed(self):
        self.assertEqual(appctl._applied_outcome(self.UNKNOWN, ["x"]), ([], False))

    def test_rules_executor_refuses_it(self):
        self.assertFalse(executor._ok(self.UNKNOWN))

    def test_unreadable_plain_200_keeps_existing_success_behavior(self):
        plain = [{"http": 200, "failed_items": None, "failed_ids": [], "ids": ["x"]}]
        self.assertTrue(appctl._http_ok(plain))
        self.assertEqual(appctl._applied_outcome(plain, ["x"]), (["x"], True))


class HarvestPrunePartial207(unittest.TestCase):
    def test_rejected_target_is_failed_counted_and_proposed_next_time(self):
        conn = memory_db()
        try:
            conn.execute("INSERT INTO campaigns(campaign_id,name,state) VALUES(?,?,?)",
                         ("c1", "Harvested Tees - Exact", "ENABLED"))
            for tid, targeting in (("t1", "accepted"), ("t2", "rejected")):
                conn.execute(
                    "INSERT INTO targeting_perf(date,campaign_id,ad_group_id,targeting,"
                    "match_type,target_id,clicks,cost,orders,sales) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    ("2026-08-24", "c1", "g1", targeting, "EXACT", tid, 20, 5.0, 0, 0))
            conn.commit()

            rows = [
                {"entity_id": "t1", "label": "accepted", "reason": "20 clicks, 0 sales"},
                {"entity_id": "t2", "label": "rejected", "reason": "20 clicks, 0 sales"},
            ]

            def api(ids, state):
                self.assertEqual(state, "PAUSED")
                return [batch(ids, failed={"t2"})]

            with contextlib.redirect_stderr(io.StringIO()):
                count = harvest_prune._pause_batch(
                    None, conn, rows, api, "pause_keyword", "keyword")
            self.assertEqual(count, 1)
            logged = dict(conn.execute(
                "SELECT entity_id,result FROM writes_log ORDER BY rowid"))
            self.assertEqual(logged, {"t1": "submitted", "t2": "failed"})

            gate = {"ok": True, "date": "2026-08-24", "reason": ""}
            with mock.patch.object(db, "snapshot_gate", return_value=gate), \
                    mock.patch.object(harvest_prune.products, "econ_gate",
                                      return_value={"ok": True, "reasons": []}), \
                    mock.patch.object(db, "get_product_map", return_value={"g1": "standard_tshirt"}), \
                    mock.patch.object(db, "get_design_map", return_value={}), \
                    mock.patch.object(db, "active_price_changes", return_value={}):
                _end, next_plan = harvest_prune.build_plan(conn)
            self.assertEqual([p["entity_id"] for p in next_plan], ["t2"])
        finally:
            conn.close()


class HarvestPruneRefusalIsReported(unittest.TestCase):
    """A refused batch and an empty plan both pause nothing. The reply has to
    tell them apart, or the app prints "Paused 0 keywords." in the success
    colour over 40 keywords Amazon rejected."""

    ROWS = [{"entity_id": "t1", "label": "one", "reason": "20 clicks, 0 sales"},
            {"entity_id": "t2", "label": "two", "reason": "20 clicks, 0 sales"}]

    def _run(self, api):
        conn = memory_db()
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                return harvest_prune.pause_outcome(
                    None, conn, list(self.ROWS), api, "pause_keyword", "keyword")
        finally:
            conn.close()

    def test_every_item_rejected_reads_as_failed_not_as_nothing_to_do(self):
        got = self._run(lambda ids, state: [batch(ids, failed=set(ids))])
        self.assertEqual(got, {"requested": 2, "paused": 0, "failed": 2,
                               "unconfirmed": 0})

    def test_transport_failure_is_unconfirmed_never_refused(self):
        got = self._run(lambda ids, state: [{"http": 500, "count": len(ids),
                                             "ids": ids}])
        self.assertEqual(got, {"requested": 2, "paused": 0, "failed": 0,
                               "unconfirmed": 2})

    def test_partial_batch_counts_both_sides(self):
        got = self._run(lambda ids, state: [batch(ids, failed={"t2"})])
        self.assertEqual(got, {"requested": 2, "paused": 1, "failed": 1,
                               "unconfirmed": 0})

    def test_the_reply_the_app_reads_names_the_refusal(self):
        conn = memory_db()
        plan = [dict(r, kind="keyword") for r in self.ROWS]

        class Client:
            def set_keywords_state(self, ids, state):
                return [batch(ids, failed=set(ids))]

            def set_targets_state(self, ids, state):
                return []

        got = {}
        try:
            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.object(appctl, "_guard_kill", return_value=None))
                stack.enter_context(mock.patch.object(appctl, "_check_econ_gate", return_value=None))
                stack.enter_context(mock.patch.object(db, "connect", return_value=conn))
                stack.enter_context(mock.patch.object(harvest_prune, "build_plan",
                                                      return_value=("2026-08-24", plan)))
                stack.enter_context(mock.patch.object(ads_client, "AdsClient",
                                                      return_value=Client()))
                stack.enter_context(mock.patch.object(appctl, "out",
                                                     side_effect=lambda d: got.update(d)))
                stack.enter_context(mock.patch.object(sys, "stdin", io.StringIO("")))
                stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
                appctl.cmd_harvest_prune_apply(type("Args", (), {})())
        finally:
            conn.close()
        self.assertEqual(got["requested"], 2)
        self.assertEqual(got["paused"], 0)
        self.assertEqual(got["failed"], 2)

    def test_empty_plan_asked_for_nothing(self):
        conn = memory_db()
        try:
            got = harvest_prune.pause_outcome(
                None, conn, [], None, "pause_keyword", "keyword")
        finally:
            conn.close()
        self.assertEqual(got["requested"], 0)


class Phase2PerItemOutcome(unittest.TestCase):
    def _seed(self, conn):
        conn.execute("INSERT INTO campaigns(campaign_id,name,state) VALUES(?,?,?)",
                     ("c1", "Standard", "ENABLED"))
        for term in ("accepted", "rejected"):
            conn.execute(
                "INSERT INTO search_term_perf(date,campaign_id,ad_group_id,search_term,"
                "clicks,cost,orders) VALUES(?,?,?,?,?,?,?)",
                ("2026-08-24", "c1", "g1", term, 12, 4.0, 0))
        conn.commit()

    def _next_terms(self, conn):
        gates = {
            "search_term_perf": {"ok": True, "date": "2026-08-24", "reason": ""},
            "targeting_perf": {"ok": False, "date": None, "reason": "empty"},
        }
        with mock.patch.object(db, "snapshot_gate", side_effect=lambda c, t: gates[t]), \
                mock.patch.object(phase2_apply.products, "econ_gate",
                                  return_value={"ok": True, "reasons": []}), \
                mock.patch.object(db, "get_design_map", return_value={}), \
                mock.patch.object(db, "active_price_changes", return_value={}), \
                mock.patch.object(db, "get_product_map", return_value={}), \
                mock.patch.object(db, "get_lifetime_map", return_value={}), \
                mock.patch.object(phase2_apply.cross_sell, "owned_cross_sell_royalty",
                                  return_value={}), \
                contextlib.redirect_stderr(io.StringIO()):
            _end, negs, _pauses = phase2_apply.candidates(conn)
        return [n[0] for n in negs]

    def test_rejection_is_failed_and_is_proposed_again(self):
        conn = memory_db()
        self._seed(conn)

        class Client:
            def create_negative_keywords(self, items):
                body = {"negativeKeywords": {
                    "success": [{"index": 0, "negativeKeywordId": "neg1"}],
                    "error": [{"index": 1, "errors": [
                        {"errorType": "INVALID_ARGUMENT"}]}]}}
                result = ads_client._batch_result(
                    Response(207, body), ["accepted", "rejected"])
                result["created_ids"] = ["neg1", None]
                return [result]

        try:
            with contextlib.redirect_stdout(io.StringIO()):
                phase2_apply.apply_negatives(
                    Client(), conn,
                    [("accepted", "c1", "g1", 4.0, "waste"),
                     ("rejected", "c1", "g1", 4.0, "waste")])
            logged = dict(conn.execute(
                "SELECT detail,result FROM writes_log ORDER BY rowid"))
            self.assertEqual(logged["rejected"], "failed")
            self.assertIn("rejected", self._next_terms(conn))
        finally:
            conn.close()

    def test_only_recognised_duplicate_is_deduped(self):
        conn = memory_db()
        self._seed(conn)

        class Client:
            def create_negative_keywords(self, items):
                body = {"negativeKeywords": {
                    "success": [{"index": 0, "negativeKeywordId": "neg1"}],
                    "error": [{"index": 1, "errors": [
                        {"errorType": "duplicateValueError"}]}]}}
                result = ads_client._batch_result(
                    Response(207, body), ["accepted", "rejected"])
                result["created_ids"] = ["neg1", None]
                return [result]

        try:
            with contextlib.redirect_stdout(io.StringIO()):
                phase2_apply.apply_negatives(
                    Client(), conn,
                    [("accepted", "c1", "g1", 4.0, "waste"),
                     ("rejected", "c1", "g1", 4.0, "waste")])
            logged = dict(conn.execute(
                "SELECT detail,result FROM writes_log ORDER BY rowid"))
            self.assertEqual(logged["rejected"], "duplicate")
            self.assertNotIn("rejected", self._next_terms(conn))
        finally:
            conn.close()


class PreemptIdentity(unittest.TestCase):
    def test_same_text_in_two_campaigns_is_counted_per_pair(self):
        conn = memory_db()
        items = [
            {"campaignId": "c1", "keywordText": "hoodie", "matchType": "NEGATIVE_PHRASE"},
            {"campaignId": "c2", "keywordText": "hoodie", "matchType": "NEGATIVE_PHRASE"},
        ]

        class Client:
            def create_campaign_negative_keywords(self, sent):
                self.sent = sent
                body = {"campaignNegativeKeywords": {
                    "success": [{"index": 0}],
                    "error": [{"index": 1, "errors": [
                        {"errorType": "INVALID_ARGUMENT"}]}]}}
                return [ads_client._batch_result(
                    Response(207, body), ["hoodie", "hoodie"])]

        try:
            client = Client()
            with contextlib.redirect_stdout(io.StringIO()):
                count = preempt_negatives.apply(client, conn, items)
            self.assertEqual(count, 1)
            logged = dict(conn.execute(
                "SELECT entity_id,result FROM writes_log ORDER BY rowid"))
            self.assertEqual(logged, {"c1": "submitted", "c2": "failed"})
        finally:
            conn.close()


class ConfirmedStateAndBidMirrors(unittest.TestCase):
    def test_scavenger_logs_and_mirrors_only_confirmed_ids(self):
        conn = memory_db()
        for tid in ("t1", "t2"):
            conn.execute("INSERT INTO targets(target_id,state) VALUES(?,?)", (tid, "ENABLED"))
        for cid in ("c1", "c2"):
            conn.execute("INSERT INTO campaigns(campaign_id,state) VALUES(?,?)", (cid, "ENABLED"))
        conn.commit()

        class Client:
            def set_keywords_state(self, ids, state):
                return [batch(ids, failed={"t2"})]

            def set_campaigns_state(self, ids, state):
                return [batch(ids, failed={"c2"})]

        prune = [("t1", "c1", 4.0, 20), ("t2", "c1", 5.0, 21)]
        chronic = [("c1", "one", 20.0, 0, "dead"),
                   ("c2", "two", 30.0, 0, "dead")]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                got = scavenger_optimize.apply(
                    Client(), conn, {"targeting": "d1", "campaign": "d2"},
                    prune, chronic)
            self.assertEqual(got, {"keywords": 1, "campaigns": 1})
            self.assertEqual(dict(conn.execute("SELECT target_id,state FROM targets")),
                             {"t1": "PAUSED", "t2": "ENABLED"})
            self.assertEqual(dict(conn.execute("SELECT campaign_id,state FROM campaigns")),
                             {"c1": "PAUSED", "c2": "ENABLED"})
            self.assertEqual(dict(conn.execute(
                "SELECT entity_id,result FROM writes_log WHERE action='scav_prune'")),
                {"t1": "submitted", "t2": "failed"})
            self.assertEqual(dict(conn.execute(
                "SELECT entity_id,result FROM writes_log WHERE action='scav_retire'")),
                {"c1": "submitted", "c2": "failed"})
        finally:
            conn.close()

    def test_reset_uses_confirmed_clamped_values(self):
        conn = memory_db()
        for tid, bid in (("t1", 2.0), ("t2", 3.0)):
            conn.execute("INSERT INTO targets(target_id,bid) VALUES(?,?)", (tid, bid))
        conn.commit()
        plan = [
            {"targetId": "t1", "current": 2.0, "new": 1.8, "original": 2.0},
            {"targetId": "t2", "current": 3.0, "new": 2.7, "original": 3.0},
        ]

        class Client:
            last_clamps = [{"id": "t1", "requested": 1.8, "cap": 0.5},
                           {"id": "t2", "requested": 2.7, "cap": 0.5}]

            def update_target_bids(self, items):
                return [batch(["t1", "t2"], failed={"t2"})]

        try:
            res, accepted, written = reset_inflated_bids.apply(Client(), conn, plan)
            self.assertEqual(accepted, {"t1"})
            self.assertEqual(written, {"t1": 0.5, "t2": 0.5})
            self.assertEqual(dict(conn.execute("SELECT target_id,bid FROM targets")),
                             {"t1": 0.5, "t2": 3.0})
            logs = dict(conn.execute(
                "SELECT entity_id,detail FROM writes_log ORDER BY rowid"))
            self.assertIn("2.0->0.5", logs["t1"])
            results = dict(conn.execute(
                "SELECT entity_id,result FROM writes_log ORDER BY rowid"))
            self.assertEqual(results, {"t1": "submitted", "t2": "failed"})
            self.assertEqual(res[0]["http"], 207)
        finally:
            conn.close()


class ManualBudgetClamp(unittest.TestCase):
    class Client:
        def __init__(self, cap):
            self.cap = cap
            self.last_clamps = []

        def update_campaign_budgets(self, items):
            requested = float(items[0]["budget"])
            self.last_clamps = [{"id": str(items[0]["campaignId"]),
                                 "requested": requested, "cap": self.cap}]
            return [{"http": 200, "failed_items": None, "failed_ids": []}]

    def _patch_command(self, conn, client, captured):
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(appctl, "_guard_kill", return_value=None))
        stack.enter_context(mock.patch.object(db, "connect", return_value=conn))
        stack.enter_context(mock.patch.object(ads_client, "AdsClient", return_value=client))
        stack.enter_context(mock.patch.object(appctl, "out", side_effect=lambda d: captured.update(d)))
        return stack

    def test_setbudget_records_the_clamped_value_everywhere(self):
        conn = memory_db()
        conn.execute("INSERT INTO campaigns(campaign_id,daily_budget) VALUES(?,?)", ("c1", 20.0))
        conn.commit()
        args = type("Args", (), {"campaign": "c1", "budget": 100.0, "prev": None})()
        got = {}
        try:
            with self._patch_command(conn, self.Client(50.0), got):
                appctl.cmd_setbudget(args)
            with self.subTest("reply"):
                self.assertEqual(got["new_budget"], 50.0)
                self.assertTrue(got["adjusted"])
            with self.subTest("mirror"):
                self.assertEqual(conn.execute(
                    "SELECT daily_budget FROM campaigns WHERE campaign_id='c1'").fetchone()[0],
                    50.0)
            detail = conn.execute(
                "SELECT detail FROM writes_log WHERE action='budget_change'").fetchone()[0]
            with self.subTest("audit"):
                self.assertIn("20.0->50.0", detail)
                self.assertIn("[adjusted]", detail)
        finally:
            conn.close()

    def test_budget_undo_records_the_clamped_restore_value(self):
        conn = memory_db()
        conn.execute("INSERT INTO campaigns(campaign_id,daily_budget) VALUES(?,?)", ("c1", 50.0))
        db.log_write(conn, "budget_change", "campaign", "c1",
                     "snap=app 80.0->50.0 (manual)", "80.0", "submitted")
        rid = conn.execute("SELECT rowid FROM writes_log").fetchone()[0]
        got = {}
        try:
            with self._patch_command(conn, self.Client(50.0), got):
                appctl.cmd_undo(type("Args", (), {"row": rid})())
            with self.subTest("reply"):
                self.assertEqual(got["restored_bid"], 50.0)
                self.assertTrue(got["adjusted"])
            with self.subTest("mirror"):
                self.assertEqual(conn.execute(
                    "SELECT daily_budget FROM campaigns WHERE campaign_id='c1'").fetchone()[0],
                    50.0)
            detail = conn.execute(
                "SELECT detail FROM writes_log ORDER BY rowid DESC LIMIT 1").fetchone()[0]
            with self.subTest("audit"):
                self.assertIn("50.0->50.0", detail)
                self.assertIn("[adjusted]", detail)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
