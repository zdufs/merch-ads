#!/usr/bin/env python3
"""Rules DSL executor — Layer 2 (actions + safety). Uses a FAKE ads client
(no live Amazon), mirroring tests/maxbid_tests."""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["ADS_MARKET"] = "US"

import db          # noqa: E402
from rules import executor  # noqa: E402


def temp_conn():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    real = db.DB_PATH
    db.DB_PATH = path
    conn = db.connect()
    db.DB_PATH = real
    return conn, path


class FakeClient:
    """Records calls; returns ok HTTP. last_clamps mimics the real clamp."""
    def __init__(self):
        self.calls = []
        self.last_clamps = []

    def set_targets_state(self, ids, state):
        self.calls.append(("set_targets_state", list(ids), state))
        return [{"http": 200, "count": len(ids)}]

    def set_ad_groups_state(self, ids, state):
        self.calls.append(("set_ad_groups_state", list(ids), state))
        return [{"http": 200, "count": len(ids)}]

    def set_campaigns_state(self, ids, state):
        self.calls.append(("set_campaigns_state", list(ids), state))
        return [{"http": 200, "count": len(ids)}]

    def update_target_bids(self, items):
        self.last_clamps = []
        self.calls.append(("update_target_bids", list(items)))
        return [{"http": 200, "count": len(items)}]

    def update_campaign_budgets(self, items):
        self.calls.append(("update_campaign_budgets", list(items)))
        return [{"http": 200, "count": len(items)}]

    def create_negative_keywords(self, items):
        self.calls.append(("create_negative_keywords", list(items)))
        return [{"http": 200, "count": len(items)}]


def _change(action, args=None, ref=None, label="x", kind="target", eid="t1"):
    return {"entity_kind": kind, "entity_id": eid, "label": label, "action": action,
            "args": args or [], "note": "why", "trace": [],
            "ref": ref or {"campaign_id": "c1", "ad_group_id": "g1", "target_id": eid, "asin": None}}


def seed_fresh_snapshots(conn):
    """A same-day snapshot row per perf table so the executor's freshness gate
    sees live evidence (the gate fails closed on empty/stale tables)."""
    import datetime
    today = datetime.date.today().isoformat()
    conn.execute("""INSERT INTO targeting_perf(date,campaign_id,ad_group_id,targeting,target_id,cost,sales)
                    VALUES (?,?,?,?,?,?,?)""", (today, "c1", "g1", "auto", "t1", 1.0, 5.0))
    conn.execute("""INSERT INTO search_term_perf(date,campaign_id,ad_group_id,search_term,cost,sales)
                    VALUES (?,?,?,?,?,?)""", (today, "c1", "g1", "tee", 1.0, 5.0))
    conn.execute("""INSERT INTO campaign_perf(date,campaign_id,cost,sales)
                    VALUES (?,?,?,?)""", (today, "c1", 1.0, 5.0))
    conn.commit()


class FailingClient(FakeClient):
    """Every write comes back as an Amazon-side rejection."""
    def set_targets_state(self, ids, state):
        self.calls.append(("set_targets_state", list(ids), state))
        return [{"http": 422, "count": len(ids)}]

    def set_ad_groups_state(self, ids, state):
        self.calls.append(("set_ad_groups_state", list(ids), state))
        return [{"http": 422, "count": len(ids)}]

    def update_target_bids(self, items):
        self.last_clamps = []
        self.calls.append(("update_target_bids", list(items)))
        return [{"http": 500, "count": len(items)}]


class Executor(unittest.TestCase):
    def test_pause_and_setbid_route_and_log(self):
        conn, path = temp_conn()
        try:
            seed_fresh_snapshots(conn)
            fc = FakeClient()
            changes = [_change("pause"), _change("setBid", args=[0.33], eid="t2",
                                                ref={"campaign_id": "c1", "ad_group_id": "g1",
                                                     "target_id": "t2", "asin": None})]
            res = executor.execute(conn, changes, market="US", client=fc)
            self.assertTrue(res["applied"])
            self.assertEqual(res["count"], 2)
            verbs = [c[0] for c in fc.calls]
            self.assertIn("set_targets_state", verbs)
            self.assertIn("update_target_bids", verbs)
            logged = conn.execute("SELECT action, entity_id FROM writes_log ORDER BY rowid").fetchall()
            self.assertEqual(len(logged), 2)
        finally:
            conn.close()
            os.unlink(path)

    def test_addnegative_routes_to_negative_keywords(self):
        conn, path = temp_conn()
        try:
            seed_fresh_snapshots(conn)
            fc = FakeClient()
            ch = _change("addNegative", args=["cheap tee"], kind="searchterm", eid="cheap tee")
            res = executor.execute(conn, [ch], market="US", client=fc)
            self.assertTrue(res["applied"])
            self.assertEqual(fc.calls[0][0], "create_negative_keywords")
            self.assertEqual(fc.calls[0][1][0]["keywordText"], "cheap tee")
        finally:
            conn.close()
            os.unlink(path)

    def test_kill_blocks(self):
        conn, path = temp_conn()
        try:
            import killswitch
            open(killswitch.KILL_FILE, "w").close()
            try:
                res = executor.execute(conn, [_change("pause")], market="US", client=FakeClient())
                self.assertFalse(res["applied"])
                self.assertEqual(res["blocked"], "kill")
            finally:
                os.unlink(killswitch.KILL_FILE)
        finally:
            conn.close()
            os.unlink(path)

    def test_rejected_write_is_failed_not_applied(self):
        """A 4xx/5xx from Amazon must surface as status=failed, stay out of the
        applied count, and land in writes_log as result=failed — never as a
        change the operator believes happened."""
        conn, path = temp_conn()
        try:
            seed_fresh_snapshots(conn)
            fc = FailingClient()
            res = executor.execute(conn, [_change("pause"),
                                          _change("setBid", args=[0.33], eid="t2")],
                                   market="US", client=fc)
            self.assertEqual(res["count"], 0)
            self.assertEqual([r["status"] for r in res["results"]],
                             ["failed", "failed"])
            logged = conn.execute(
                "SELECT result FROM writes_log ORDER BY rowid").fetchall()
            self.assertEqual([r[0] for r in logged], ["failed", "failed"])
        finally:
            conn.close()
            os.unlink(path)

    def test_applied_pause_mirrors_local_ad_group_state(self):
        conn, path = temp_conn()
        try:
            seed_fresh_snapshots(conn)
            conn.execute("INSERT INTO ad_groups(ad_group_id,campaign_id,name,state)"
                         " VALUES ('g1','c1','G1','ENABLED')")
            conn.commit()
            ch = _change("pause", kind="adgroup", eid="g1")
            res = executor.execute(conn, [ch], market="US", client=FakeClient())
            self.assertEqual(res["count"], 1)
            state = conn.execute(
                "SELECT state FROM ad_groups WHERE ad_group_id='g1'").fetchone()[0]
            self.assertEqual(state, "PAUSED")
        finally:
            conn.close()
            os.unlink(path)

    def test_stale_snapshot_blocks_change(self):
        """No fresh snapshot for the entity's source table → the DSL must fail
        closed exactly like phase2/phase3 (this is the standing snapshot rule —
        an auto rule must never pause/bid off week-old evidence)."""
        conn, path = temp_conn()
        try:
            # nothing seeded: targeting_perf is empty = gate closed
            fc = FakeClient()
            res = executor.execute(conn, [_change("pause")], market="US", client=fc)
            self.assertEqual(res["count"], 0)
            self.assertEqual(res["results"][0]["status"], "blocked_stale_data")
            self.assertEqual(fc.calls, [])          # nothing reached Amazon
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM writes_log").fetchone()[0], 0)
        finally:
            conn.close()
            os.unlink(path)

    def test_change_cap_truncates(self):
        conn, path = temp_conn()
        try:
            seed_fresh_snapshots(conn)
            fc = FakeClient()
            changes = [_change("pause", eid=f"t{i}",
                               ref={"campaign_id": "c1", "ad_group_id": "g1",
                                    "target_id": f"t{i}", "asin": None}) for i in range(5)]
            res = executor.execute(conn, changes, market="US", client=fc, cap=3)
            self.assertTrue(res["truncated"])
            self.assertEqual(res["count"], 3)
        finally:
            conn.close()
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
