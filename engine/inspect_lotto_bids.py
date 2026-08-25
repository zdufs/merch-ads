#!/usr/bin/env python3
"""
Read the auto-targeting expression bids from your US Lotto campaigns so we can
replicate them. Prints, per expression type (close-match / loose-match /
substitutes / complements), how many clauses and the bid distribution.
Read-only.

Run:  python3 inspect_lotto_bids.py
"""

from collections import defaultdict
from statistics import median
import lottery
from ads_client import AdsClient

SP_CAMP = "application/vnd.spCampaign.v3+json"
TYPE_NAME = lottery.EXPRESSION_NAME   # one source of truth (see lottery.py)

client = AdsClient()  # US
camps = [c for c in client.list_all("/sp/campaigns/list", SP_CAMP, "campaigns")
         if "lotto" in (c.get("name") or "").lower()]
cids = [c["campaignId"] for c in camps]
print(f"Lotto campaigns: {len(cids)} — fetching targeting clauses (may take a minute)…")

clauses = client.list_targets(cids)
bids = defaultdict(list)
states = defaultdict(lambda: defaultdict(int))
for cl in clauses:
    expr = cl.get("expression") or []
    etype = expr[0].get("type") if expr and isinstance(expr[0], dict) else cl.get("expressionType")
    name = TYPE_NAME.get(etype, etype)
    b = cl.get("bid")
    if b is not None:
        bids[name].append(b)
    states[name][cl.get("state", "?")] += 1

print(f"\ntotal clauses: {len(clauses)}")
print(f"{'expression':14}{'clauses':>9}{'min':>8}{'median':>9}{'max':>8}   states")
for name in ["close-match", "loose-match", "substitutes", "complements"]:
    v = bids.get(name, [])
    if not v and name not in states:
        continue
    st = ", ".join(f"{k}:{n}" for k, n in states.get(name, {}).items())
    if v:
        print(f"{name:14}{len(v):>9}{min(v):>8.2f}{median(v):>9.2f}{max(v):>8.2f}   {st}")
    else:
        print(f"{name:14}{'0':>9}{'—':>8}{'—':>9}{'—':>8}   {st}")
