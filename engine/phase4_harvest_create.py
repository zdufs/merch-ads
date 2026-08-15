#!/usr/bin/env python3
"""
PHASE 4 — create manual EXACT-match campaigns from harvested winners.
One campaign per product type ("Harvested Tees - Exact", etc.). Inside, one ad
group per ASIN (the design that converted), its product ad, and the converting
search terms as EXACT keywords. Then negate each term in its SOURCE auto campaign
so the two don't bid against each other.

SAFETY: preview by default. Writes only with --apply AND typed APPLY.
Test small first with --limit N (creates only N ad groups). Everything logged.

Usage:
  python3 phase4_harvest_create.py                # preview the plan
  python3 phase4_harvest_create.py --limit 3 --apply   # create 3 ad groups (test)
  python3 phase4_harvest_create.py --apply        # create all
"""

import datetime
import sys
import db
import killswitch
from ads_client import AdsClient, success_ids

CAMPAIGN_BUDGET = 25.00   # daily $ per harvested campaign
SP_CAMP = "application/vnd.spCampaign.v3+json"

LABELS = {
    "standard_tshirt": "Tees", "premium_tshirt": "Premium Tees",
    "oversized_tshirt": "Oversized Tees", "performance_tshirt": "Performance Tees",
    "vneck": "V-Necks", "tank_top": "Tanks", "long_sleeve": "Long Sleeves",
    "standard_sweatshirt": "Sweatshirts", "standard_pullover_hoodie": "Pullover Hoodies",
    "zip_hoodie": "Zip Hoodies", "quarter_zip": "Quarter Zips", "polo": "Polos",
    "printed_baseball_hat": "Baseball Hats", "printed_trucker_hat": "Trucker Hats",
    "sport_sun_visor": "Sun Visors", "crop_top": "Crop Tops",
    "tote_bag": "Tote Bags", "throw_pillow": "Throw Pillows",
}
def camp_name(pt): return f"Harvested {LABELS.get(pt, (pt or 'misc').replace('_',' ').title())} - Exact"


def build_plan(conn, limit, terms=None):
    """terms: optional set of search_term values (the app's per-winner approval) —
    when given, only those candidates are promoted."""
    cur = conn.cursor()
    agp = {r[0]: (r[1], r[2]) for r in cur.execute(
        "SELECT ad_group_id, asin, product_type FROM ad_group_product")}
    cands = cur.execute(
        """SELECT search_term, source_ad_group_id, product_type, cpc, source_campaign_id
           FROM harvest_log WHERE promoted=0 AND kind='keyword'""").fetchall()
    if terms is not None:
        cands = [c for c in cands if c[0] in terms]
    # plan[pt][asin] = {src_ag, src_cid, keywords:[(term,bid)]}
    plan = {}
    skipped_no_asin = 0
    for term, src_ag, pt0, cpc, src_cid in cands:
        asin, pt_map = agp.get(str(src_ag), (None, None))
        if not asin:
            skipped_no_asin += 1
            continue
        pt = pt0 or pt_map or "unknown"
        bid = max(0.10, round((cpc or 0.20) * 1.15, 2))
        plan.setdefault(pt, {}).setdefault(asin, {"src_ag": str(src_ag), "src_cid": src_cid, "kw": []})
        plan[pt][asin]["kw"].append((term, bid))
    # flatten ad groups for limit
    adgroups = [(pt, asin, d) for pt, asins in plan.items() for asin, d in asins.items()]
    if limit:
        adgroups = adgroups[:limit]
    return adgroups, skipped_no_asin


def preview(adgroups):
    bypt = {}
    kw = 0
    for pt, asin, d in adgroups:
        bypt.setdefault(pt, [0, 0])
        bypt[pt][0] += 1
        bypt[pt][1] += len(d["kw"])
        kw += len(d["kw"])
    print("PREVIEW — nothing created.\n")
    print(f"  Campaigns: {len(bypt)} (one per product type)")
    for pt, (ags, k) in bypt.items():
        print(f"    {camp_name(pt):34}  {ags} ad groups, {k} exact keywords")
    print(f"  Total: {len(adgroups)} ad groups, {kw} keywords, {kw} source-negations")
    print(f"  Budget per campaign: ${CAMPAIGN_BUDGET}/day")


def confirm():
    return input("\nType APPLY to create these campaigns (anything else cancels): ").strip() == "APPLY"


def existing_campaigns(client):
    camps = client.list_all("/sp/campaigns/list", SP_CAMP, "campaigns")
    return {c.get("name"): str(c.get("campaignId")) for c in camps}


def apply(client, conn, adgroups):
    today = datetime.date.today().isoformat()
    pts = list({pt for pt, _, _ in adgroups})

    # 1) campaigns (reuse by name, else create)
    have = existing_campaigns(client)
    to_make = [pt for pt in pts if camp_name(pt) not in have]
    camp_id = {pt: have[camp_name(pt)] for pt in pts if camp_name(pt) in have}
    if to_make:
        items = [{"name": camp_name(pt), "budget": CAMPAIGN_BUDGET, "startDate": today} for pt in to_make]
        st, js = client.create_campaigns(items)
        ids = success_ids(js, "campaigns", "campaignId")
        for i, pt in enumerate(to_make):
            if i in ids:
                camp_id[pt] = ids[i]
        print(f"  campaigns created: {len(ids)}/{len(to_make)} (HTTP {st})")
        if len(ids) != len(to_make):
            print(f"  ⚠️ campaign create response: {str(js)[:400]}")

    usable = [(pt, asin, d) for pt, asin, d in adgroups if pt in camp_id]
    if not usable:
        print("  No campaigns available — aborting before ad groups."); return

    # 2) ad groups — REUSE an existing ad group for an ASIN if one's already there
    # (a design harvested before for another term shouldn't create a duplicate ad group)
    cids = list({camp_id[pt] for pt, _, _ in usable})
    existing_ag = {}
    try:
        for a in client.list_all("/sp/adGroups/list", "application/vnd.spAdGroup.v3+json", "adGroups",
                                 extra_body={"campaignIdFilter": {"include": [str(c) for c in cids]}}):
            existing_ag[(str(a.get("campaignId")), a.get("name"))] = str(a.get("adGroupId"))
    except Exception as e:
        print(f"  (couldn't list existing ad groups: {e})")

    resolved = {}                 # (pt,asin) -> adGroupId
    to_create = []                # (pt,asin,d) needing a new ad group
    for pt, asin, d in usable:
        ex = existing_ag.get((str(camp_id[pt]), asin))
        if ex:
            resolved[(pt, asin)] = ex
        else:
            to_create.append((pt, asin, d))

    if to_create:
        ag_items = [{"name": asin, "campaignId": camp_id[pt],
                     "defaultBid": max(b for _, b in d["kw"])} for pt, asin, d in to_create]
        st, js = client.create_ad_groups(ag_items)
        ag_ids = success_ids(js, "adGroups", "adGroupId")
        print(f"  ad groups: {len(ag_ids)} created, {len(resolved)} reused (HTTP {st})")
        pa_items = []
        for i, (pt, asin, d) in enumerate(to_create):
            if i in ag_ids:
                resolved[(pt, asin)] = ag_ids[i]
                pa_items.append({"campaignId": camp_id[pt], "adGroupId": ag_ids[i], "asin": asin})
        if pa_items:
            st, js = client.create_product_ads(pa_items)
            print(f"  product ads created: {len(success_ids(js,'productAds','adId'))}/{len(pa_items)} (HTTP {st})")
    else:
        print(f"  ad groups: 0 created, {len(resolved)} reused")

    # 3) keywords + negate source, for every resolved ad group (existing or new)
    kw_items, neg_items, promoted = [], [], []
    for pt, asin, d in usable:
        agid = resolved.get((pt, asin))
        if not agid:
            continue
        for term, bid in d["kw"]:
            kw_items.append({"campaignId": camp_id[pt], "adGroupId": agid, "keywordText": term, "bid": bid})
            neg_items.append({"campaignId": d["src_cid"], "adGroupId": d["src_ag"], "keywordText": term})
            promoted.append((term, d["src_ag"]))

    if kw_items:
        st, js = client.create_keywords(kw_items)
        print(f"  exact keywords created: {len(success_ids(js,'keywords','keywordId'))}/{len(kw_items)} (HTTP {st})")
    if neg_items:
        client.create_negative_keywords(neg_items)
        print(f"  source negations submitted: {len(neg_items)}")

    # mark promoted + log
    conn.executemany("UPDATE harvest_log SET promoted=1 WHERE search_term=? AND source_ad_group_id=?", promoted)
    conn.commit()
    for term, src in promoted:
        db.log_write(conn, "harvest_promote", "keyword", term, f"exact-match created; negated in {src}", "", "submitted")
    print(f"\nDone. {len(promoted)} terms promoted + negated. Logged in writes_log.")


def main():
    args = sys.argv[1:]
    limit = None
    if "--limit" in args:
        try: limit = int(args[args.index("--limit") + 1])
        except Exception: pass
    terms = None
    if "--terms-file" in args:
        try:
            with open(args[args.index("--terms-file") + 1], encoding="utf-8") as fh:
                terms = {line.strip() for line in fh if line.strip()}
            print(f"** --terms-file: scoped to {len(terms)} approved terms **")
        except (IndexError, OSError) as e:
            raise SystemExit(f"--terms-file: {e}")
    conn = db.connect()
    client = AdsClient()
    adgroups, skipped = build_plan(conn, limit, terms)
    if not adgroups:
        print("No un-promoted keyword candidates to harvest."); return
    preview(adgroups)
    if skipped:
        print(f"  (skipped {skipped} candidates with no ASIN on their source ad group)")
    if "--apply" not in args:
        print("\nPREVIEW ONLY. Re-run with --apply (add --limit N to test small first).")
        return
    killswitch.check()
    if "--auto" not in args and not confirm():
        print("Cancelled — nothing created."); return
    apply(client, conn, adgroups)


if __name__ == "__main__":
    main()
