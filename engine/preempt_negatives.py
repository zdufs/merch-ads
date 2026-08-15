#!/usr/bin/env python3
"""
Apply preemptive wrong-FORMAT negatives (campaign level) to the lottery + scavenger
campaigns of the active market. These are can't-fulfill terms (a tee can never serve
a "hoodie"/"mug"/"sticker" search), so they go on immediately — no spend threshold —
and only to campaigns whose product type is known and uniform (lottery = tees,
scavenger = its cohort type). Idempotent: skips terms already present.

SAFETY: preview by default; writes only with --apply + typed APPLY (or --auto); KILL aware.

Usage:
  ADS_MARKET=DE python3 preempt_negatives.py            # preview
  ADS_MARKET=DE python3 preempt_negatives.py --apply
"""

import sys
from collections import defaultdict

import db
import killswitch
import lottery
import preempt
import scavenger
from ads_client import AdsClient

SP_CAMP = "application/vnd.spCampaign.v3+json"


def campaign_type(name):
    """Uniform product type for a campaign we manage, else None."""
    if lottery.is_lottery(name):
        return "standard_tshirt"
    if scavenger.is_scavenger(name):
        return scavenger.econ_type_for_campaign(name)
    return None


def main():
    args = sys.argv[1:]
    conn = db.connect()
    client = AdsClient()

    targets = []   # (campaignId, name, negs)
    for c in client.list_all("/sp/campaigns/list", SP_CAMP, "campaigns"):
        t = campaign_type(c.get("name"))
        if not t:
            continue
        negs = preempt.negatives_for(t)
        if negs:
            targets.append((c["campaignId"], c.get("name"), negs))
    if not targets:
        print(f"[{client.market}] no lottery/scavenger campaigns to shield."); return

    cids = [cid for cid, _, _ in targets]
    existing = defaultdict(set)
    for cn in client.list_campaign_negative_keywords(cids):
        existing[str(cn.get("campaignId"))].add((cn.get("keywordText") or "").lower())

    to_add = []
    for cid, name, negs in targets:
        have = existing.get(str(cid), set())
        for kw in negs:
            if kw.lower() not in have:
                to_add.append({"campaignId": cid, "keywordText": kw, "matchType": "NEGATIVE_PHRASE"})

    print(f"[{client.market}] campaigns shielded: {len(targets)} | format negatives to add: {len(to_add)}")
    if not to_add:
        print("  already up to date."); return
    if "--apply" not in args:
        print("  sample:", ", ".join(sorted({i['keywordText'] for i in to_add})[:12]))
        print("\nPREVIEW ONLY. Re-run with --apply."); return
    killswitch.check()
    if "--auto" not in args and input("\nType APPLY to add these negatives: ").strip() != "APPLY":
        print("Cancelled."); return

    res = client.create_campaign_negative_keywords(to_add)
    ok = sum(b["count"] for b in res if b["http"] in (200, 207))
    db.log_write(conn, "preempt_format_neg", "market", client.market,
                 f"{len(to_add)} campaign negatives across {len(targets)} campaigns", "", "submitted")
    print(f"  added ~{ok} campaign-level format negatives.")


if __name__ == "__main__":
    main()
