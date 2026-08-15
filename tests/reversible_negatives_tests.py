#!/usr/bin/env python3
"""Reversible negatives (the MerchDash "one revert from gone" parity).

A negative used to be permanent: the executor logged the text but not the
created id, so there was nothing to delete. Now create_negative_keywords returns
the created ids, the executor logs `negid=<id>`, and Undo deletes it. A negative
created before this still logs no id and is honestly reported not-undoable.

Run from the Ads folder:  python3 -m unittest tests.reversible_negatives_tests -v
"""

import os
import sqlite3
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ["ADS_MARKET"] = "US"

import db  # noqa: E402
import ads_client  # noqa: E402
import appctl  # noqa: E402
from rules import executor  # noqa: E402


def mk_conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(db.SCHEMA)
    return conn


class FakeClient:
    def __init__(self, created_ids=("neg777",)):
        self.calls = []
        self._created = list(created_ids)

    def create_negative_keywords(self, items):
        self.calls.append(("create", items))
        return [{"http": 200, "failed_items": 0, "failed_ids": [],
                 "created_ids": self._created}]

    def delete_negative_keywords(self, ids):
        self.calls.append(("delete", list(ids)))
        return [{"http": 200, "failed_items": 0, "failed_ids": []}]


class CreatedIds(unittest.TestCase):
    def test_parses_success_array(self):
        body = {"negativeKeywords": {
            "success": [{"index": 0, "negativeKeywordId": "neg123"}],
            "error": []}}
        self.assertEqual(ads_client._created_ids(body, "negativeKeywordId"),
                         {0: "neg123"})

    def test_unknown_shape_is_empty(self):
        self.assertEqual(ads_client._created_ids({"weird": 1}, "negativeKeywordId"), {})
        self.assertEqual(ads_client._created_ids(None, "negativeKeywordId"), {})


class ExecutorLogsTheId(unittest.TestCase):
    def test_addnegative_logs_negid(self):
        conn, client = mk_conn(), FakeClient(["neg777"])
        ch = {"action": "addNegative", "entity_kind": "searchterm",
              "entity_id": "ag1", "args": ["bad term", "exact"],
              "ref": {"campaign_id": "c1", "ad_group_id": "ag1"}, "note": "waste"}
        res = executor._apply_one(conn, client, ch)
        self.assertTrue(res["ok"])
        detail = conn.execute("SELECT detail FROM writes_log WHERE action='add_negative' "
                              "ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        self.assertIn("negid=neg777", detail)


class DeleteRequestShape(unittest.TestCase):
    def test_delete_uses_id_filter(self):
        sent = {}

        class C(ads_client.AdsClient):
            def __init__(self):  # skip real auth
                pass

            def _send_retry(self, method, path, ct, payload):
                sent.update(method=method, path=path, payload=payload)

                class R:
                    status_code = 200
                    def json(self_inner):
                        return {"negativeKeywords": {"success": [], "error": []}}
                return R()

        C().delete_negative_keywords(["neg777"])
        self.assertEqual(sent["method"], "POST")
        self.assertEqual(sent["path"], "/sp/negativeKeywords/delete")
        self.assertEqual(sent["payload"],
                         {"negativeKeywordIdFilter": {"include": ["neg777"]}})


class Undoable(unittest.TestCase):
    def test_negative_with_id_is_undoable(self):
        self.assertTrue(appctl._row_undoable("add_negative", "bad term negid=neg1"))

    def test_negative_without_id_is_not(self):
        self.assertFalse(appctl._row_undoable("add_negative", "bad term"))

    def test_pause_is_always_undoable(self):
        self.assertTrue(appctl._row_undoable("pause_ad_group", ""))

    def test_unknown_action_is_not(self):
        self.assertFalse(appctl._row_undoable("create_keyword", "x"))


class Phase2PathsLogTheId(unittest.TestCase):
    """The two phase-2 apply paths used to discard created_ids, so an Approval
    Queue or nightly negative was permanent while the identical negative from
    right-click, negate-everywhere or a rule was one Undo from gone."""

    def test_apply_negatives_logs_negid(self):
        import phase2_apply
        conn, client = mk_conn(), FakeClient(["neg888"])
        negs = [("bad term", "c1", "ag1", 9.99, "10 clicks 0 sales")]
        phase2_apply.apply_negatives(client, conn, negs)
        detail = conn.execute("SELECT detail FROM writes_log WHERE action='add_negative' "
                              "ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        self.assertIn("negid=neg888", detail)
        self.assertTrue(appctl._row_undoable("add_negative", detail))

    def test_cmd_negatives_apply_logs_negid(self):
        import io
        import json as _json
        conn, client = mk_conn(), FakeClient(["neg999"])
        plan = {"negatives": [{"search_term": "bad term", "campaign_id": "c1",
                               "ad_group_id": "ag1"}], "pauses": []}
        orig = (appctl._guard_kill, appctl._check_econ_gate, db.connect,
                ads_client.AdsClient, sys.stdin, appctl.out)
        captured = {}
        try:
            appctl._guard_kill = lambda: None
            appctl._check_econ_gate = lambda *a, **k: None
            db.connect = lambda *a, **k: conn
            ads_client.AdsClient = lambda mkt: client
            sys.stdin = io.StringIO(_json.dumps(plan))
            appctl.out = lambda payload: captured.update(payload)
            appctl.cmd_negatives_apply(None)
        finally:
            (appctl._guard_kill, appctl._check_econ_gate, db.connect,
             ads_client.AdsClient, sys.stdin, appctl.out) = orig
        detail = conn.execute("SELECT detail FROM writes_log WHERE action='add_negative' "
                              "ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        self.assertIn("negid=neg999", detail)
        self.assertTrue(appctl._row_undoable("add_negative", detail))
        self.assertEqual(captured["negatives_applied"], 1)


if __name__ == "__main__":
    unittest.main()
