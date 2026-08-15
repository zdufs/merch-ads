#!/usr/bin/env python3
"""
TAMAS launcher — create focused manual TAMAS campaigns from tamas_launches.csv.
Each row = one broad keyword + one ASIN -> a manual campaign with FIXED bids,
a low starting bid, and a low daily budget (per the TAMAS method).

You curate the picks (broad design + broad high-volume keyword + low price set in
Merch); this builds the campaign exactly to spec. Idempotent (skips already-launched).

tamas_launches.csv columns:
    keyword,asin,match,bid,budget
    dog,B0XXXXXXXX,phrase,0.25,5
    nurse,B0YYYYYYYY,broad,,
(blank match/bid/budget use TAMAS defaults: phrase / $0.25 / $5)

SAFETY: preview by default; writes only with --apply + typed APPLY; honors KILL.

Usage:
  python3 tamas_launch.py
  python3 tamas_launch.py --apply
  python3 tamas_launch.py --apply --yes   # skip the typed-APPLY prompt (non-interactive)
"""

import csv
import datetime
import os
import sys
import db
import killswitch
import tamas
from ads_client import AdsClient, success_ids

HERE = os.path.dirname(os.path.abspath(__file__))
LAUNCHES = os.path.join(HERE, "tamas_launches.csv")
SP_CAMP = "application/vnd.spCampaign.v3+json"
MATCHES = {"broad": "BROAD", "phrase": "PHRASE", "exact": "EXACT"}


def read_launches():
    if not os.path.exists(LAUNCHES):
        raise SystemExit(f"No {LAUNCHES} — create it with columns: keyword,asin,match,bid,budget")
    out = []
    with open(LAUNCHES, newline="") as f:
        for r in csv.DictReader(f):
            kw = (r.get("keyword") or "").strip()
            asin = (r.get("asin") or "").strip().upper()
            if not kw or not asin:
                continue
            match = MATCHES.get((r.get("match") or tamas.DEFAULT_MATCH).strip().lower(), "PHRASE")
            try: bid = float(r.get("bid") or tamas.DEFAULT_BID)
            except ValueError: bid = tamas.DEFAULT_BID
            try: budget = float(r.get("budget") or tamas.DEFAULT_BUDGET)
            except ValueError: budget = tamas.DEFAULT_BUDGET
            out.append(dict(keyword=kw, asin=asin, match=match, bid=bid, budget=budget))
    return out


def confirm():
    return input("\nType APPLY to launch these TAMAS campaigns (anything else cancels): ").strip() == "APPLY"


def main():
    import markets
    if not markets.is_default():
        print(f"TAMAS is US-only — skipping for market {markets.current()}."); return
    args = sys.argv[1:]
    rows = read_launches()
    if not rows:
        print("No launches in tamas_launches.csv."); return
    conn = db.connect()
    client = AdsClient()

    existing = {c.get("name") for c in client.list_all("/sp/campaigns/list", SP_CAMP, "campaigns")}
    todo = [r for r in rows if tamas.camp_name(r["keyword"], r["asin"]) not in existing]
    skipped = len(rows) - len(todo)

    print("PREVIEW — TAMAS launches (fixed bids):")
    for r in todo:
        print(f"    {tamas.camp_name(r['keyword'], r['asin'])}  | {r['match']} '{r['keyword']}'  bid ${r['bid']} budget ${r['budget']}/day")
    print(f"  to create: {len(todo)} | already exist (skipped): {skipped}")
    if not todo:
        print("Nothing new to launch."); return
    if "--apply" not in args:
        print("\nPREVIEW ONLY. Re-run with --apply."); return
    killswitch.check()
    # --yes / --auto skip the typed-APPLY prompt (non-interactive runs, e.g. under `!`)
    if "--yes" not in args and "--auto" not in args and not confirm():
        print("Cancelled."); return

    today = datetime.date.today().isoformat()
    # 1) campaigns (manual, fixed bids)
    camp_items = [{"name": tamas.camp_name(r["keyword"], r["asin"]), "budget": r["budget"],
                   "startDate": today, "targetingType": "MANUAL",
                   "strategy": tamas.BIDDING_STRATEGY} for r in todo]
    st, js = client.create_campaigns(camp_items)
    cids = success_ids(js, "campaigns", "campaignId")
    print(f"  campaigns created: {len(cids)}/{len(camp_items)} (HTTP {st})")
    if len(cids) != len(camp_items): print(f"  ⚠️ {str(js)[:400]}")

    # 2) ad groups (one ASIN) for created campaigns
    made = [(i, r) for i, r in enumerate(todo) if i in cids]
    ag_items = [{"name": r["asin"], "campaignId": cids[i], "defaultBid": r["bid"]} for i, r in made]
    st, js = client.create_ad_groups(ag_items)
    ag_ids = success_ids(js, "adGroups", "adGroupId")
    print(f"  ad groups created: {len(ag_ids)}/{len(ag_items)} (HTTP {st})")

    pa, kw = [], []
    for j, (i, r) in enumerate(made):
        if j not in ag_ids: continue
        agid = ag_ids[j]
        pa.append({"campaignId": cids[i], "adGroupId": agid, "asin": r["asin"]})
        kw.append({"campaignId": cids[i], "adGroupId": agid, "keywordText": r["keyword"],
                   "matchType": r["match"], "bid": r["bid"]})
    if pa:
        st, js = client.create_product_ads(pa)
        print(f"  product ads created: {len(success_ids(js,'productAds','adId'))}/{len(pa)} (HTTP {st})")
    if kw:
        st, js = client.create_keywords(kw)
        print(f"  keywords created: {len(success_ids(js,'keywords','keywordId'))}/{len(kw)} (HTTP {st})")
    for it in kw:
        db.log_write(conn, "tamas_launch", "campaign", it["campaignId"], f"{it['matchType']} '{it['keywordText']}'", "", "submitted")
    print(f"\nDone. Launched {len(kw)} TAMAS campaigns. Watch TRAZ — tamas_optimize.py scales them.")


if __name__ == "__main__":
    main()
