#!/usr/bin/env python3
"""
SCAVENGER optimizer — runs ONLY on scavenger campaigns (name-prefixed). Separate
from the standard ACOS rules. Two simple jobs:

  1. PRUNE wasteful keywords — a broad keyword with 0 orders, enough clicks, and
     spend over the standard-tee stop-loss (royalty * 0.5) gets PAUSED. This is the
     video's "turn off keywords wasting clicks and not getting sales," automated.
  2. FLAG chronic-dead campaigns — if a whole scavenger campaign has burned real
     spend with ~no orders, flag it for retirement ("don't marry your campaigns").
     Reported only; never auto-deleted.

Snapshot-guarded (one prune per fresh pull). Gated: preview / --apply / --auto / KILL.
Winners the scavenger finds are promoted to focused manual campaigns by the normal
harvest step — this optimizer only trims the fat.

Usage:
  python3 scavenger_optimize.py            # preview
  python3 scavenger_optimize.py --apply    # apply (typed APPLY)
  python3 scavenger_optimize.py --apply --auto   # scheduled (no prompt)
"""

import sys
import ads_client
import db
import killswitch
import products
import scavenger
from ads_client import AdsClient


def build(conn):
    cur = conn.cursor()
    # Pruning reads targeting_perf; retiring reads campaign_perf. Two report jobs,
    # two dates — sharing one matched zero rows for the other and quietly pruned
    # nothing. Each half also fails closed on its own stale evidence.
    # BOTH halves gate. The comment above promised that and only the targeting
    # half delivered it: the campaign half took a bare MAX(date), so a campaign
    # report that had been failing for ten days still RETIRED campaigns — and it
    # retires by pausing them, unattended, every night.
    cp_gate = db.snapshot_gate(conn, "campaign_perf")
    camp_end = cp_gate["date"]
    tg_gate = db.snapshot_gate(conn, "targeting_perf")
    snaps = {"targeting": tg_gate["date"], "campaign": camp_end}
    scav = {r[0]: r[1] for r in cur.execute("SELECT campaign_id,name FROM campaigns")
            if scavenger.is_scavenger(r[1])}
    if not scav:
        return snaps, [], []

    # per-campaign stop-loss: each series prunes on its own product-type economics
    stop_loss = {cid: products.get_econ(scavenger.econ_type_for_campaign(name))["neg_threshold"]
                 for cid, name in scav.items()}
    already = {r[0] for r in cur.execute(
        "SELECT entity_id FROM writes_log WHERE action='scav_prune' AND detail LIKE ?",
        (f"snap={tg_gate['date']}%",)).fetchall()}

    # 1) wasteful keywords to pause
    prune = []
    if not tg_gate["ok"]:
        print(f"TARGETING DATA STALE — no scavenger keyword prunes: {tg_gate['reason']}")
    for r in (cur.execute(
        """SELECT target_id,campaign_id,clicks,cost,orders FROM targeting_perf
           WHERE date=? AND target_id IS NOT NULL""", (tg_gate["date"],))
              if tg_gate["ok"] else []):
        tid, cid, clicks, cost, orders = str(r[0]), r[1], r[2] or 0, r[3] or 0, r[4] or 0
        if cid not in scav or tid in already:
            continue
        if orders == 0 and clicks >= scavenger.MIN_CLICKS_PRUNE and cost >= stop_loss[cid]:
            prune.append((tid, cid, round(cost, 2), clicks))

    # 2) chronic-dead campaigns to AUTO-RETIRE (pause). Skip ones already paused or
    # already retired on this snapshot. "Don't marry a dying campaign" — automated.
    states = dict(cur.execute("SELECT campaign_id,state FROM campaigns").fetchall())
    retired = {r[0] for r in cur.execute(
        "SELECT entity_id FROM writes_log WHERE action='scav_retire' AND detail LIKE ?",
        (f"snap={camp_end}%",)).fetchall()}
    chronic = []
    if not cp_gate["ok"]:
        print("CAMPAIGN DATA STALE — no scavenger retirements this run: "
              + cp_gate["reason"], file=sys.stderr)
        return snaps, prune, chronic
    for r in cur.execute(
        "SELECT campaign_id,cost,orders,sales FROM campaign_perf WHERE date=?", (camp_end,)):
        cid, cost, orders, sales = r[0], r[1] or 0, r[2] or 0, r[3] or 0
        if (cid not in scav or states.get(cid) != "ENABLED" or cid in retired
                or cost < scavenger.CHRONIC_SPEND):
            continue
        if orders <= scavenger.CHRONIC_MAX_ORDERS:
            chronic.append((cid, scav[cid], round(cost, 2), orders, f"{orders} orders"))
            continue
        # converting but bleeding: ACOS above the product target x discovery buffer
        target = products.get_econ(scavenger.econ_type_for_campaign(scav[cid]))["target_acos"]
        ceiling = target * scavenger.CHRONIC_ACOS_MULT
        acos = cost / sales if sales else None
        if acos is not None and acos > ceiling:
            chronic.append((cid, scav[cid], round(cost, 2), orders,
                            f"ACOS {acos*100:.0f}% > {ceiling*100:.0f}%"))
    return snaps, prune, chronic


def confirm():
    return input("\nType APPLY to prune keywords + retire dead scavenger campaigns (anything else cancels): ").strip() == "APPLY"


def apply(client, conn, snaps, prune, chronic):
    """Apply state writes and mirror only ids Amazon confirmed."""
    keyword_count = campaign_count = 0
    if prune:
        ids = [tid for tid, _, _, _ in prune]
        res = client.set_keywords_state(ids, "PAUSED")
        accepted = ads_client.certain_ids(res, ids)
        for tid, cid, cost, clicks in prune:
            db.log_write(conn, "scav_prune", "keyword", tid,
                         f"snap={snaps['targeting']} 0 orders/{clicks} clicks/${cost}",
                         "ENABLED", "submitted" if str(tid) in accepted else "failed")
        db.set_local_target_state(conn, sorted(accepted), "PAUSED")
        keyword_count = len(accepted)
        print(f"  paused {keyword_count}/{len(prune)} wasteful scavenger keywords.")
    if chronic:
        ids = [row[0] for row in chronic]
        res = client.set_campaigns_state(ids, "PAUSED")
        accepted = ads_client.certain_ids(res, ids)
        for cid, name, cost, orders, reason in chronic:
            db.log_write(conn, "scav_retire", "campaign", cid,
                         f"snap={snaps['campaign']} ${cost} spent / {reason}",
                         "ENABLED", "submitted" if str(cid) in accepted else "failed")
        db.set_local_campaign_state(conn, sorted(accepted), "PAUSED")
        campaign_count = len(accepted)
        print(f"  retired {campaign_count}/{len(chronic)} chronic-dead scavenger campaigns.")
    return {"keywords": keyword_count, "campaigns": campaign_count}


def main():
    args = sys.argv[1:]
    conn = db.connect()
    snaps, prune, chronic = build(conn)
    print(f"SCAVENGER optimize — targeting {snaps['targeting']} · campaigns {snaps['campaign']}")
    print(f"  wasteful keywords to pause: {len(prune)}")
    for tid, cid, cost, clicks in prune[:8]:
        print(f"    pause kw {tid}  ${cost:.2f} spent, {clicks} clicks, 0 orders")
    if chronic:
        print(f"  chronic-dead campaigns to retire (auto-pause): {len(chronic)}")
        for cid, nm, cost, orders, reason in chronic:
            print(f"     {nm}: ${cost:.2f} spent, {reason} -> PAUSE")
    if not prune and not chronic:
        print("  nothing to do."); return
    if "--apply" not in args:
        print("\nPREVIEW ONLY. Re-run with --apply."); return
    killswitch.check()
    if "--auto" not in args and not confirm():
        print("Cancelled."); return

    client = AdsClient()
    apply(client, conn, snaps, prune, chronic)
    print("Done.")


if __name__ == "__main__":
    main()
