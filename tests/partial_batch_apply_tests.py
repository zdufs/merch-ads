#!/usr/bin/env python3
"""A partly-accepted batch is reported partly, not as a total failure.

`_http_ok` asks whether EVERY item in a batch went through. That is the right
question for a single-entity write, and almost every command is one. The
Approval Queue is not: it sends every approved negative in one call, and every
approved pause in another.

Amazon rejects individual items inside a 207 routinely — a duplicate negative
above all — and the old code turned one such rejection into:

  * `negatives_applied: 0`, so the app told the operator nothing happened while
    twenty-nine keywords were live on the account;
  * thirty `writes_log` rows marked `failed`, so the Audit Trail agreed with
    that and the operator had no way to find the real ones;
  * for pauses, `set_local_ad_group_state` skipped ENTIRELY, leaving the local
    mirror describing ad groups as ENABLED that Amazon had already paused.

That last one is the exact desync `_http_ok` was introduced to prevent, reached
from the opposite side.

These tests drive `cmd_negatives_apply` with a client that accepts some items
and refuses others, and read back what the operator would actually be told.

Run from the Ads folder:
    python3 -m unittest tests.partial_batch_apply_tests -v
"""

import json
import os
import sqlite3
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ["ADS_MARKET"] = "US"

import db        # noqa: E402
import appctl    # noqa: E402


def mk_conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(db.SCHEMA)
    return conn


class PartlyAcceptingClient:
    """Accepts every item except the ones named, the way Amazon's v3 does:
    HTTP 207 for the batch, the refusals listed item by item."""

    def __init__(self, reject_negatives=(), reject_pauses=()):
        self.reject_negatives = set(reject_negatives)
        self.reject_pauses = set(reject_pauses)

    def create_negative_keywords(self, items):
        created, failed = [], []
        for i, it in enumerate(items):
            if it["keywordText"] in self.reject_negatives:
                created.append(None)
                failed.append(it["keywordText"])
            else:
                created.append(f"neg{i}")
        return [{"http": 207, "failed_items": len(failed), "failed_ids": failed,
                 "created_ids": created}]

    def pause_ad_groups(self, ids):
        failed = [str(i) for i in ids if str(i) in self.reject_pauses]
        return [{"http": 207, "failed_items": len(failed), "failed_ids": failed}]


class Args:
    pass


def run_apply(conn, client, negatives, pauses, monkey):
    """Drive cmd_negatives_apply with stdin and a stubbed client/connection."""
    captured = {}
    monkey.setattr(appctl, "out", lambda d: captured.update(d))
    monkey.setattr(appctl, "_guard_kill", lambda: None)
    monkey.setattr(appctl, "_check_econ_gate", lambda *a, **k: None)
    monkey.setattr(db, "connect", lambda *a, **k: conn)
    import io
    monkey.setattr(appctl.sys, "stdin",
                   io.StringIO(json.dumps({"negatives": negatives,
                                           "pauses": pauses})))
    import ads_client
    monkey.setattr(ads_client, "AdsClient", lambda *a, **k: client)
    appctl.cmd_negatives_apply(Args())
    return captured


class _Monkey:
    """Tiny setattr/restore helper (unittest has no pytest fixtures here)."""

    def __init__(self):
        self._undo = []

    def setattr(self, obj, name, value):
        self._undo.append((obj, name, getattr(obj, name, None),
                           hasattr(obj, name)))
        setattr(obj, name, value)

    def restore(self):
        for obj, name, old, existed in reversed(self._undo):
            if existed:
                setattr(obj, name, old)
            else:
                delattr(obj, name)
        self._undo = []


class NegativesCountOnlyWhatAmazonTook(unittest.TestCase):

    def setUp(self):
        self.m = _Monkey()
        self.addCleanup(self.m.restore)
        self.conn = mk_conn()

    def _negs(self, terms):
        return [{"search_term": t, "campaign_id": "c1", "ad_group_id": "ag1"}
                for t in terms]

    def test_one_refused_keyword_leaves_the_others_applied(self):
        client = PartlyAcceptingClient(reject_negatives={"dupe"})
        r = run_apply(self.conn, client,
                      self._negs(["good one", "dupe", "another"]), [], self.m)
        self.assertEqual(r["negatives_applied"], 2,
                         "two keywords were created and must be counted")
        self.assertEqual(r["negatives_rejected"], 1)

    def test_the_audit_trail_marks_only_the_refused_row_failed(self):
        client = PartlyAcceptingClient(reject_negatives={"dupe"})
        run_apply(self.conn, client,
                  self._negs(["good one", "dupe", "another"]), [], self.m)
        rows = dict(self.conn.execute(
            "SELECT detail, result FROM writes_log WHERE action='add_negative'"))
        results = {d.split(" negid=")[0]: res for d, res in rows.items()}
        self.assertEqual(results["good one"], "submitted")
        self.assertEqual(results["another"], "submitted")
        self.assertEqual(results["dupe"], "failed")

    def test_an_applied_negative_keeps_its_id_so_undo_still_works(self):
        client = PartlyAcceptingClient(reject_negatives={"dupe"})
        run_apply(self.conn, client,
                  self._negs(["good one", "dupe"]), [], self.m)
        details = [d for (d,) in self.conn.execute(
            "SELECT detail FROM writes_log WHERE action='add_negative'")]
        kept = [d for d in details if d.startswith("good one")][0]
        self.assertIn("negid=", kept)
        self.assertTrue(appctl._row_undoable("add_negative", kept))

    def test_a_wholly_refused_batch_still_reports_zero(self):
        client = PartlyAcceptingClient(reject_negatives={"a", "b"})
        r = run_apply(self.conn, client, self._negs(["a", "b"]), [], self.m)
        self.assertEqual(r["negatives_applied"], 0)
        self.assertEqual(r["negatives_rejected"], 2)

    def test_a_clean_batch_counts_everything(self):
        client = PartlyAcceptingClient()
        r = run_apply(self.conn, client, self._negs(["a", "b", "c"]), [], self.m)
        self.assertEqual(r["negatives_applied"], 3)
        self.assertEqual(r["negatives_rejected"], 0)


class PausesMirrorOnlyWhatAmazonTook(unittest.TestCase):

    def setUp(self):
        self.m = _Monkey()
        self.addCleanup(self.m.restore)
        self.conn = mk_conn()
        for agid in ("ag1", "ag2", "ag3"):
            self.conn.execute(
                "INSERT INTO ad_groups (ad_group_id, campaign_id, name, state) "
                "VALUES (?,?,?,?)", (agid, "c1", agid, "ENABLED"))
        self.conn.commit()

    def _states(self):
        return dict(self.conn.execute("SELECT ad_group_id, state FROM ad_groups"))

    def test_the_mirror_follows_amazon_per_ad_group(self):
        """The whole point. Mirroring all three would claim ag2 is paused when
        Amazon refused it; mirroring none would leave ag1 and ag3 reading
        ENABLED while Amazon has them paused."""
        client = PartlyAcceptingClient(reject_pauses={"ag2"})
        r = run_apply(self.conn, client, [], ["ag1", "ag2", "ag3"], self.m)
        self.assertEqual(r["pauses_applied"], 2)
        self.assertEqual(r["pauses_rejected"], 1)
        self.assertEqual(self._states(),
                         {"ag1": "PAUSED", "ag2": "ENABLED", "ag3": "PAUSED"})

    def test_the_audit_trail_marks_only_the_refused_ad_group_failed(self):
        client = PartlyAcceptingClient(reject_pauses={"ag2"})
        run_apply(self.conn, client, [], ["ag1", "ag2", "ag3"], self.m)
        rows = dict(self.conn.execute(
            "SELECT entity_id, result FROM writes_log WHERE action='pause_ad_group'"))
        self.assertEqual(rows["ag1"], "submitted")
        self.assertEqual(rows["ag3"], "submitted")
        self.assertEqual(rows["ag2"], "failed")

    def test_a_wholly_refused_pause_batch_mirrors_nothing(self):
        client = PartlyAcceptingClient(reject_pauses={"ag1", "ag2", "ag3"})
        r = run_apply(self.conn, client, [], ["ag1", "ag2", "ag3"], self.m)
        self.assertEqual(r["pauses_applied"], 0)
        self.assertEqual(set(self._states().values()), {"ENABLED"})


class ResetBidsReportsWhatMovedNotWhatWasPlanned(unittest.TestCase):
    """`total_reduction` describes the PLAN and always did.

    On a partial rejection the app printed it as the headline and then printed
    "Amazon refused 1 of 3" underneath, so the receipt claimed a saving that
    never happened and contradicted its own next sentence. `applied_reduction`
    is the same arithmetic over the targets Amazon actually took.

    Found by review, 2026-08-23.
    """

    class _Client:
        def __init__(self, reject=()):
            self.reject = {str(r) for r in reject}

        def update_target_bids(self, items):
            failed = [str(i["targetId"]) for i in items
                      if str(i["targetId"]) in self.reject]
            return [{"http": 207, "failed_items": len(failed),
                     "failed_ids": failed}]

    PLAN = [{"targetId": "t1", "current": 1.00, "new": 0.90},
            {"targetId": "t2", "current": 2.00, "new": 1.50},
            {"targetId": "t3", "current": 3.00, "new": 2.75}]

    def _run(self, reject):
        import reset_inflated_bids as rib
        import ads_client
        conn = mk_conn()
        captured = {}
        m = _Monkey()
        m.setattr(appctl, "out", lambda d: captured.update(d))
        m.setattr(appctl, "db", appctl.db)
        m.setattr(appctl.db, "connect", lambda *a, **k: conn)
        m.setattr(appctl, "_check_econ_gate", lambda *a, **k: None)
        m.setattr(appctl, "_guard_kill", lambda *a, **k: None)
        m.setattr(rib, "build", lambda c: [dict(p) for p in self.PLAN])
        m.setattr(ads_client, "AdsClient", lambda *a, **k: self._Client(reject))
        args = Args()
        args.apply = True
        try:
            appctl.cmd_resetbids(args)
        finally:
            m.restore()
            conn.close()
        return captured

    def test_a_clean_run_reports_the_whole_plan(self):
        got = self._run(reject=())
        self.assertEqual(got["applied_count"], 3)
        self.assertEqual(got["rejected_count"], 0)
        self.assertAlmostEqual(got["total_reduction"], 0.85, places=2)
        self.assertAlmostEqual(got["applied_reduction"], 0.85, places=2)

    def test_a_refused_target_is_not_counted_as_a_saving(self):
        """t2 is the biggest cut. Refusing it must take 0.50 off the saving,
        not leave the headline claiming the full 0.85."""
        got = self._run(reject=("t2",))
        self.assertEqual(got["applied_count"], 2)
        self.assertEqual(got["rejected_count"], 1)
        self.assertAlmostEqual(got["total_reduction"], 0.85, places=2)
        self.assertAlmostEqual(got["applied_reduction"], 0.35, places=2,
                               msg="the refused target's cut must not be "
                                   "counted as money saved")

    def test_a_wholly_refused_run_saves_nothing(self):
        got = self._run(reject=("t1", "t2", "t3"))
        self.assertEqual(got["applied_count"], 0)
        self.assertEqual(got["applied_reduction"], 0.0)
        self.assertAlmostEqual(got["total_reduction"], 0.85, places=2,
                               msg="the PLAN figure is unchanged and still "
                                   "reported — it is simply not the headline")


class ApprovedEvidenceMustStillBeCurrent(unittest.TestCase):
    """The approval queue refuses a plan approved against an older snapshot.

    Found by mutation on 2026-08-24. Flipping the comparison in
    `cmd_negatives_apply` from `!=` to `==` — so it refuses the CURRENT
    snapshot and applies every stale one — broke nothing in the whole suite.

    The guard had four tests already and all four read the SOURCE: they parse
    the function's AST, or grep `inspect.getsource` for a phrase. Those are
    worth keeping, and one of them checks something behaviour cannot reach
    (that `phase2_apply.candidates` is NOT called, which is an absence). But
    not one of them CALLS the function, so the comparison itself was pinned
    only by the fact that the words were still on the page. Both directions
    are asserted below, because a guard that refuses everything passes a
    one-sided test just as happily as one that refuses nothing.
    """

    def setUp(self):
        self.conn = mk_conn()
        # The date the operator's plan was built against, and the newer one
        # that arrived overnight.
        self.conn.execute(
            "INSERT INTO search_term_perf (date, campaign_id, ad_group_id, "
            "search_term, targeting, impressions, clicks, cost, sales, orders) "
            "VALUES ('2026-08-22','c1','a1','shirt','shirt',10,2,1.0,0.0,0)")
        self.conn.commit()

    def _apply(self, as_of):
        """Returns (reply, refusal). `appctl.err` exits the process, so a
        refusal arrives as SystemExit and must be caught, not stubbed away —
        stubbing it to merely record would let execution run on into the write
        and test a path that cannot happen."""
        monkey = _Monkey()
        captured, refusal = {}, []

        def fake_err(msg):
            refusal.append(str(msg))
            raise SystemExit(1)

        try:
            monkey.setattr(appctl, "out", lambda d: captured.update(d))
            monkey.setattr(appctl, "err", fake_err)
            monkey.setattr(appctl, "_guard_kill", lambda: None)
            monkey.setattr(appctl, "_check_econ_gate", lambda *a, **k: None)
            monkey.setattr(db, "connect", lambda *a, **k: self.conn)
            import io
            monkey.setattr(appctl.sys, "stdin", io.StringIO(json.dumps(
                {"as_of": as_of,
                 "negatives": [{"search_term": "shirt",
                                "campaign_id": "c1", "ad_group_id": "a1"}],
                 "pauses": []})))
            import ads_client
            monkey.setattr(ads_client, "AdsClient",
                           lambda *a, **k: PartlyAcceptingClient())
            try:
                appctl.cmd_negatives_apply(Args())
            except SystemExit:
                pass
        finally:
            monkey.restore()
        return captured, (refusal[0] if refusal else None)

    def test_a_plan_approved_against_older_evidence_is_refused(self):
        reply, refusal = self._apply("2026-08-18")
        self.assertIsNotNone(refusal,
                             "evidence moved after approval — this must refuse")
        self.assertIn("2026-08-18", refusal,
                      "the refusal must name the date that was approved")
        self.assertEqual(reply, {},
                         "a refusal must never also emit a success envelope")

    def test_a_plan_approved_against_the_current_snapshot_applies(self):
        """The other direction. Without this, a guard that refuses EVERY plan
        passes the test above and takes the Approval Queue off the air."""
        reply, refusal = self._apply("2026-08-22")
        self.assertIsNone(refusal,
                          "current evidence must not be refused as stale")

    def test_an_app_that_sends_no_as_of_is_not_refused(self):
        reply, refusal = self._apply(None)
        self.assertIsNone(refusal)


if __name__ == "__main__":
    unittest.main()
