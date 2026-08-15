#!/usr/bin/env python3
"""
ONE-OFF: undo the runaway daily +10% bid inflation.

phase3 used to nudge a profitable auto-target's bid +10% EVERY day, compounding it far
above where it started. This resets every target that was net-increased back to its
ORIGINAL starting bid x 0.90 (a clean, slightly-below-original base). From the next
Monday review, phase3 takes over again — now weekly (increase / decrease / hold).

"Original" = the bid right before phase3's first move on that target (the first
bid_change row's prev value in writes_log). "Current" = the latest logged bid.
Only targets whose current bid is still above their original are reset.

Per market (reads ADS_MARKET; default US). Preview by default; --apply writes to Amazon.
  python3 reset_inflated_bids.py                       # preview US
  ADS_MARKET=UK python3 reset_inflated_bids.py --apply --auto
  for M in US UK DE FR ES IT; do ADS_MARKET=$M python3 reset_inflated_bids.py --apply --auto; done
"""

import re
import sys

import db
import killswitch
import markets
from ads_client import AdsClient

RX = re.compile(r"([0-9]*\.?[0-9]+)\s*->\s*([0-9]*\.?[0-9]+)")
MIN_BID = 0.10
FACTOR = 0.90        # reset to original minus 10%


def build(conn):
    rows = conn.execute(
        "SELECT entity_id, prev_state, detail, rowid FROM writes_log "
        "WHERE action='bid_change' ORDER BY entity_id, rowid").fetchall()
    hist = {}
    for eid, prev, detail, rid in rows:
        hist.setdefault(eid, []).append((prev, detail))

    plan = []
    for eid, h in hist.items():
        # original = old bid of the FIRST recorded move
        try:
            original = float(h[0][0])
        except (TypeError, ValueError):
            m = RX.search(h[0][1] or "")
            original = float(m.group(1)) if m else None
        if original is None:
            continue
        # current = new bid of the LAST recorded move
        m = RX.search(h[-1][1] or "")
        current = float(m.group(2)) if m else None
        if current is None:
            continue
        if current > original + 0.001:                 # net-inflated by the daily ups
            new = max(MIN_BID, round(original * FACTOR, 2))
            if abs(new - current) >= 0.01:
                plan.append(dict(targetId=eid, original=original, current=current, new=new))
    return plan


def main():
    args = set(sys.argv[1:])
    mkt = markets.current()
    conn = db.connect()
    plan = build(conn)
    cut = sum(p["current"] for p in plan) - sum(p["new"] for p in plan)
    print(f"[{mkt}] inflated targets to reset: {len(plan)}  (total bid reduction ${cut:.2f})")
    for p in sorted(plan, key=lambda p: -p["current"])[:8]:
        print(f"   {p['targetId']}: orig ${p['original']:.2f}, now ${p['current']:.2f} -> ${p['new']:.2f}")
    if not plan:
        print("  nothing to reset in this market."); return

    if "--apply" not in args:
        print("\nPREVIEW ONLY. Re-run with --apply to write to Amazon."); return
    killswitch.check()
    if "--auto" not in args and input("\nType APPLY to reset these bids: ").strip() != "APPLY":
        print("Cancelled — nothing written."); return

    client = AdsClient(mkt)
    res = client.update_target_bids([{"targetId": p["targetId"], "bid": p["new"]} for p in plan])
    for p in plan:
        db.log_write(conn, "bid_change", "target", p["targetId"],
                     f"snap=reset {p['current']}->{p['new']} (reset to original-10%)",
                     str(p["current"]), "submitted")
    ok = sum(1 for b in res if b["http"] in (200, 207))
    print(f"  reset {len(plan)} bids ({ok}/{len(res)} batches accepted). See writes_log.")


if __name__ == "__main__":
    main()
