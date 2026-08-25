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

import re
import sys
from collections import defaultdict

import ads_client
import campaign_kinds
import db
import killswitch
import preempt
import scavenger
from ads_client import AdsClient

SP_CAMP = "application/vnd.spCampaign.v3+json"


# A lottery campaign whose name is the prefix plus a NUMBER is the tee lottery:
# 'LOTTO - 3' in the EU markets, 'Lotto 3' in US. A lottery campaign named after
# some other product — 'Lotto Sweatshirts', 'Lotto Zip Hoodies' — is not, and its
# type is not resolvable from the name.
_TEE_LOTTERY = re.compile(r"^(?:LOTTO - |Lotto )\d+$")


def campaign_type(name):
    """Uniform product type for a campaign we manage, else None.

    None means SKIP. That is the safe answer here: these negatives say a product
    can never fulfil a search, so guessing the product wrong blocks traffic the
    campaign exists to win.

    This used to ask `lottery.is_lottery`, which only knows the EU 'LOTTO - '
    prefix. The eleven ENABLED US lottery campaigns are named 'Lotto 1'..'Lotto 8'
    and so on, so every one of them was skipped and the job still reported its
    remaining campaigns as shielded. The canonical classifier in campaign_kinds
    already knew both spellings; this module simply never used it.

    Widening the name test alone would have been worse than the bug. Three of
    those US campaigns sell hoodies and sweatshirts, and the tee list negates
    'hoodie' and 'sweatshirt' — so a lottery campaign only takes the tee list
    when its name says tees, which for this series means a bare number.
    """
    n = name or ""
    if campaign_kinds.classify(n) == "lottery":
        return "standard_tshirt" if _TEE_LOTTERY.match(n) else None
    if scavenger.is_scavenger(n):
        return scavenger.econ_type_for_campaign(n)
    return None


def apply(client, conn, items):
    """Write and log each campaign and term pair by request index."""
    res = client.create_campaign_negative_keywords(items)
    outcomes = ads_client.item_outcomes(res)
    accepted = 0
    for index, item in enumerate(items):
        status = outcomes[index] if index < len(outcomes) else "uncertain"
        landed = status in ("accepted", "duplicate")
        db.log_write(conn, "preempt_format_neg", "campaign", item["campaignId"],
                     db.detail_prefix(item["keywordText"]),
                     "", "submitted" if landed else "failed")
        accepted += 1 if landed else 0
    return accepted


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

    ok = apply(client, conn, to_add)
    print(f"  added {ok}/{len(to_add)} campaign-level format negatives"
          + (f" — {len(to_add) - ok} were NOT confirmed by Amazon" if ok < len(to_add) else "")
          + ".")


if __name__ == "__main__":
    main()
