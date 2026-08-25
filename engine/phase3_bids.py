#!/usr/bin/env python3
"""
PHASE 3 — BID MANAGEMENT (Model A, standard tees).
Adjusts the auto targeting-group bids toward each product's target ACOS:
  - ACOS over target        -> lower bid 15% (rein it in)
  - ACOS under target, sells -> raise bid 10% (capture more growth)
  - thin data / 0 sales      -> left untouched (negatives & pauses handle those)

Cadence: bids are reviewed ONCE A WEEK, on Monday (REVIEW_WEEKDAY) — increase, decrease,
or hold. The job still runs daily but holds bids on every other day, so each bid keeps its
value for a full week of data before the next decision (no daily +10% compounding).
Use --force to apply off-cycle (manual tuning).

SAFETY: preview by default. Writes only with --apply AND typed APPLY.
Every change logged (old->new bid) for review/rollback. Moves capped at ±15%/run
and clamped to [MIN_BID, MAX_BID] so nothing swings wildly.

Usage:
  python3 phase3_bids.py                    # preview all proposed bid changes
  python3 phase3_bids.py --apply            # apply all (asks for APPLY)
  python3 phase3_bids.py --downs-only --apply   # only the ACOS>30% bid-downs (savings)
  python3 phase3_bids.py --ups-only --apply     # only the bid-ups (growth, raises spend)
  python3 phase3_bids.py --min-clicks 20        # require 20+ clicks before bidding UP
  python3 phase3_bids.py --rollback-bids        # restore every bid to its last value
"""

import sqlite3
import sys
import db
import products
import killswitch
import scavenger
import datetime
import ads_client
from ads_client import AdsClient

DOWN = 0.85
UP = 1.10
DEFAULT_MIN_CLICKS = 20   # require 20+ clicks of proof before bidding a target UP
MIN_BID = 0.10
MAX_BID = 1.50
REVIEW_WEEKDAY = 0        # Monday (Python weekday(): Mon=0). Bids are reviewed ONCE a week,
                          # on this day — increase, decrease, or hold — never daily.


def arg_value(args, name, default):
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1]
    return default


def build_proposals(conn, client, min_clicks):
    cur = conn.cursor()
    # campaign_perf picks the active campaigns; targeting_perf is the bid
    # EVIDENCE. Separate report jobs fill them, so they drift apart when one
    # fails — each query must use its own table's date. Sharing campaign_perf's
    # date matched zero targeting rows and printed "no bid changes meet the
    # thresholds" while nothing had actually been evaluated (US, Aug 2026).
    # campaign_perf only picks WHICH campaigns to look at, so a stale one does not
    # make a bid wrong — it makes the run QUIET about campaigns that started after
    # the stuck date, which is worse, because the run then reports success. Say so
    # rather than refusing: the bid evidence below has its own gate.
    cp_gate = db.snapshot_gate(conn, "campaign_perf")
    camp_end = cp_gate["date"]
    tg_gate = db.snapshot_gate(conn, "targeting_perf")
    snap = tg_gate["date"] or camp_end
    gate = products.econ_gate(conn=conn)
    if not gate["ok"]:
        print("ECON GATE CLOSED — no phase-3 bid proposals this run: "
              + "; ".join(gate["reasons"]))
        return snap, []
    if not tg_gate["ok"]:
        # Fail closed: bidding on stale evidence is worse than not bidding.
        print(f"TARGETING DATA STALE — no phase-3 bid proposals this run: {tg_gate['reason']}")
        return snap, []
    end = tg_gate["date"]
    # Scavenger runs on its own optimizer. Everything not ENABLED is skipped on
    # STATE: Amazon keeps reporting trailing-30 rows for paused and archived
    # campaigns, so they still surface here, and a bid written to one is rejected.
    # This used to name a retired strategy instead — its campaigns were archived,
    # so the name test was standing in for a state test.
    skip_ids = {r[0] for r in cur.execute("SELECT campaign_id,name,state FROM campaigns")
                if scavenger.is_scavenger(r[1]) or (r[2] or "") != "ENABLED"}
    active = [r[0] for r in cur.execute(
        "SELECT campaign_id FROM campaign_perf WHERE date=? AND cost>0", (camp_end,)).fetchall()
        if r[0] not in skip_ids]   # scavenger has its own optimizer; rest are not ENABLED
    if not cp_gate["ok"]:
        print(f"  WARNING: the campaign list is {cp_gate['reason']} — any campaign "
              f"that started after {camp_end} is NOT considered in this run.")
    if not active:
        return end, []

    print(f"Fetching current bids for {len(active)} active campaigns…")
    clauses = client.list_targets(active)
    defbid = dict(cur.execute("SELECT ad_group_id, default_bid FROM ad_groups").fetchall())
    pmap = db.get_product_map(conn)   # ad_group_id -> product_type (per-type rules)
    dmap = db.get_design_map(conn)    # per-design price economics (US tees)
    trans = db.active_price_changes(conn)
    from phase2_apply import _design_target

    perf = {}
    for r in cur.execute(
        """SELECT target_id,clicks,cost,orders,sales,acos FROM targeting_perf
           WHERE date=? AND target_id IS NOT NULL""", (end,)):
        perf[str(r[0])] = dict(clicks=r[1], cost=r[2], orders=r[3], sales=r[4], acos=r[5])

    # Guard: skip targets already adjusted for THIS data snapshot (no double-applying
    # on the same evidence — one bid move per fresh pull, then wait for new data).
    already = {row[0] for row in cur.execute(
        "SELECT entity_id FROM writes_log WHERE action='bid_change' AND detail LIKE ?",
        (f"snap={end}%",)).fetchall()}

    proposals = []
    for c in clauses:
        tid = str(c.get("targetId"))
        if tid in already:
            continue   # already moved on this data snapshot
        agid = str(c.get("adGroupId"))
        cur_bid = c.get("bid") or defbid.get(c.get("adGroupId"))
        if not cur_bid:
            continue
        p = perf.get(tid)
        if not p:
            continue
        d = dmap.get(agid)
        target, unknown, sfx = _design_target(d, trans)
        if unknown:
            continue   # transition-unknown / unsupported price: no bid moves at all
        in_transition = bool(d and d.get("asin") and trans.get(d["asin"]))
        ptype_label = (d or {}).get("product_type") or pmap.get(agid) or "unknown"
        acos = p["acos"]
        new_bid, reason = None, None
        # DOWN: above THIS design's target (cut fast — few clicks needed);
        # during a transition `target` is already the max across price legs
        if p["cost"] and acos is not None and acos > target and (p["clicks"] or 0) >= 5:
            new_bid = round(cur_bid * DOWN, 2)
            reason = f"{ptype_label}: ACOS {acos*100:.0f}% > {target*100:.0f}%"
        # UP: below THIS design's target, converting, enough proof — SUPPRESSED
        # during a price transition (its ACOS still reflects the old price)
        elif (not in_transition
              and acos is not None and 0 < acos < target and (p["orders"] or 0) >= 1
              and (p["clicks"] or 0) >= min_clicks):
            new_bid = round(cur_bid * UP, 2)
            reason = f"{ptype_label}: ACOS {acos*100:.0f}% < {target*100:.0f}%, scaling"
        if new_bid is None:
            continue
        new_bid = max(MIN_BID, min(MAX_BID, new_bid))
        if abs(new_bid - cur_bid) < 0.01:
            continue
        proposals.append(dict(targetId=tid, adGroupId=c.get("adGroupId"),
                              campaignId=c.get("campaignId"),
                              expr=c.get("expressionType") or c.get("targeting"),
                              old=round(cur_bid, 2), new=new_bid, reason=reason,
                              clicks=p["clicks"], acos=acos, econ_sfx=sfx))
    return end, proposals


def preview(proposals):
    ups = [p for p in proposals if p["new"] > p["old"]]
    downs = [p for p in proposals if p["new"] < p["old"]]
    print(f"\nPREVIEW — nothing changed.")
    print(f"  Bid changes proposed : {len(proposals)}  ({len(ups)} up, {len(downs)} down)")
    print("\n  Sample (old -> new, reason):")
    for p in (downs[:4] + ups[:4]):
        print(f"    {(p['expr'] or '')[:14]:14} ${p['old']:.2f} -> ${p['new']:.2f}  ({p['reason']}, {p['clicks']} clicks)")


def confirm():
    return input('\nType APPLY to write these bid changes (anything else cancels): ').strip() == "APPLY"


def apply(client, conn, proposals, end):
    items = [{"targetId": p["targetId"], "bid": p["new"]} for p in proposals]
    print(f"Updating {len(items)} bids…")
    res = client.update_target_bids(items)
    # 207 batches can reject individual items — log those as failed, and
    # write accepted bids through to the targets mirror so DSL previews and
    # the app's Bid column stay honest until the next pull.
    # Only what Amazon PROVED it accepted. An empty rejected set is produced by
    # a clean 207, by a 500, and by a 207 whose body we could not read — the
    # last two were being mirrored as applied bids.
    accepted = ads_client.certain_ids(res, [p["targetId"] for p in proposals])
    caps = {c["id"]: c["cap"] for c in (getattr(client, "last_clamps", None) or [])}
    for p in proposals:
        tid = str(p["targetId"])
        written = caps.get(tid, p["new"])
        db.log_write(conn, "bid_change", "target", p["targetId"],
                     f"snap={end} {p['old']}->{written} ({p['reason']})"
                     + (" [adjusted]" if tid in caps else "")
                     + (p.get("econ_sfx") or ""), str(p["old"]),
                     "submitted" if tid in accepted else "failed")
    db.set_local_target_bids(
        conn, [(p["targetId"], caps.get(str(p["targetId"]), p["new"]))
               for p in proposals if str(p["targetId"]) in accepted])
    ok = sum(1 for b in res if b["http"] in (200, 207))
    # Every proposal Amazon did not PROVE it accepted — see the note in
    # phase2_apply.apply_pauses: a failure list can be empty and still be wrong.
    rejected = [p["targetId"] for p in proposals
                if str(p["targetId"]) not in accepted]
    print(f"  done ({ok}/{len(res)} batches accepted"
          + (f", {len(rejected)} item(s) REJECTED" if rejected else "")
          + "). See writes_log.")


def rollback_bids(client, conn):
    """Restore each target to the prev bid from its most recent bid_change log row."""
    rows = conn.execute(
        """SELECT w.entity_id, w.prev_state FROM writes_log w
           JOIN (SELECT entity_id, MAX(rowid) mr FROM writes_log
                 WHERE action='bid_change' GROUP BY entity_id) m
           ON w.rowid=m.mr""").fetchall()
    items = []
    for tid, prev in rows:
        try:
            items.append({"targetId": tid, "bid": float(prev)})
        except (TypeError, ValueError):
            continue
    if not items:
        print("No bid changes in the log to roll back.")
        return
    print(f"Restoring {len(items)} bids to their previous values…")
    res = client.update_target_bids(items)
    for it in items:
        db.log_write(conn, "bid_rollback", "target", it["targetId"],
                     f"restored to {it['bid']}", "", "submitted")
    ok = sum(1 for b in res if b["http"] in (200, 207))
    print(f"  done ({ok}/{len(res)} batches accepted).")


def main():
    args = sys.argv[1:]
    conn = db.connect()
    client = AdsClient()

    if "--rollback-bids" in args:
        if confirm():
            killswitch.check()
            rollback_bids(client, conn)
        else:
            print("Cancelled.")
        return

    min_clicks = int(arg_value(args, "--min-clicks", DEFAULT_MIN_CLICKS))
    end, proposals = build_proposals(conn, client, min_clicks)

    if "--downs-only" in args:
        proposals = [p for p in proposals if p["new"] < p["old"]]
    elif "--ups-only" in args:
        proposals = [p for p in proposals if p["new"] > p["old"]]

    print(f"Snapshot {end} | profile {client.profile_id} | min-clicks(up)={min_clicks}")
    if not proposals:
        print("No bid changes meet the thresholds right now.")
        return
    preview(proposals)

    if "--apply" not in args:
        print("\nPREVIEW ONLY. Re-run with --apply to execute.")
        return
    # Weekly review gate: only move bids on REVIEW_WEEKDAY (Monday). The job runs daily,
    # but holds bids on every other day so each one keeps its bid for a full week before
    # the next increase / decrease / hold. '--force' overrides (manual off-cycle tuning).
    if datetime.date.today().weekday() != REVIEW_WEEKDAY and "--force" not in args:
        print(f"\nNot the weekly review day (Monday) — holding bids. Use --force to override.")
        return
    killswitch.check()
    if "--auto" not in args and not confirm():
        print("Cancelled — nothing written.")
        return
    apply(client, conn, proposals, end)


if __name__ == "__main__":
    main()
