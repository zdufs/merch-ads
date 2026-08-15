#!/usr/bin/env python3
"""
PHASE 4b — harvest converting ASIN search terms into manual product-targeting
campaigns ("Harvested [Type] - ASIN"). For winners where a shopper arrived via
a product page (the 'search term' is an ASIN). Creates an ASIN_SAME_AS target on
the design that converted, and negates that ASIN in the source auto campaign.

SAFETY: preview by default; writes only with --apply + typed APPLY. --limit N to test.

Usage:
  python3 phase4b_harvest_asins.py
  python3 phase4b_harvest_asins.py --limit 2 --apply
  python3 phase4b_harvest_asins.py --apply
"""

import datetime
import sys
import db
import killswitch
from ads_client import AdsClient, success_ids
from phase4_harvest_create import LABELS, CAMPAIGN_BUDGET, SP_CAMP

def camp_name(pt): return f"Harvested {LABELS.get(pt, (pt or 'misc').replace('_',' ').title())} - ASIN"


def build_plan(conn, limit, terms=None):
    """terms: optional set of search_term values (per-winner approval scoping)."""
    cur = conn.cursor()
    agp = {r[0]: (r[1], r[2]) for r in cur.execute(
        "SELECT ad_group_id, asin, product_type FROM ad_group_product")}
    cands = cur.execute(
        """SELECT search_term, source_ad_group_id, product_type, cpc, source_campaign_id
           FROM harvest_log WHERE promoted=0 AND kind='asin_target'""").fetchall()
    if terms is not None:
        cands = [c for c in cands if c[0] in terms]
    plan, skipped = {}, 0
    for tgt_asin, src_ag, pt0, cpc, src_cid in cands:
        design_asin, pt_map = agp.get(str(src_ag), (None, None))
        if not design_asin:
            skipped += 1
            continue
        pt = pt0 or pt_map or "unknown"
        bid = max(0.10, round((cpc or 0.20) * 1.15, 2))
        plan.setdefault(pt, {}).setdefault(
            design_asin, {"src_ag": str(src_ag), "src_cid": src_cid, "targets": []})
        plan[pt][design_asin]["targets"].append((tgt_asin, bid))
    adgroups = [(pt, asin, d) for pt, asins in plan.items() for asin, d in asins.items()]
    if limit:
        adgroups = adgroups[:limit]
    return adgroups, skipped


def preview(adgroups):
    bypt = {}
    for pt, asin, d in adgroups:
        bypt.setdefault(pt, [0, 0]); bypt[pt][0] += 1; bypt[pt][1] += len(d["targets"])
    print("PREVIEW — nothing created.\n")
    print(f"  Campaigns: {len(bypt)} (one per product type)")
    for pt, (ags, t) in bypt.items():
        print(f"    {camp_name(pt):36}  {ags} ad groups, {t} ASIN targets")
    print(f"  Budget per campaign: ${CAMPAIGN_BUDGET}/day")


def confirm():
    return input("\nType APPLY to create these ASIN campaigns (anything else cancels): ").strip() == "APPLY"


def existing_campaigns(client):
    camps = client.list_all("/sp/campaigns/list", SP_CAMP, "campaigns")
    return {c.get("name"): str(c.get("campaignId")) for c in camps}


def apply(client, conn, adgroups):
    today = datetime.date.today().isoformat()
    pts = list({pt for pt, _, _ in adgroups})
    have = existing_campaigns(client)
    to_make = [pt for pt in pts if camp_name(pt) not in have]
    camp_id = {pt: have[camp_name(pt)] for pt in pts if camp_name(pt) in have}
    if to_make:
        items = [{"name": camp_name(pt), "budget": CAMPAIGN_BUDGET, "startDate": today} for pt in to_make]
        st, js = client.create_campaigns(items)
        ids = success_ids(js, "campaigns", "campaignId")
        for i, pt in enumerate(to_make):
            if i in ids: camp_id[pt] = ids[i]
        print(f"  campaigns created: {len(ids)}/{len(to_make)} (HTTP {st})")
        if len(ids) != len(to_make): print(f"  ⚠️ {str(js)[:400]}")

    usable = [(pt, asin, d) for pt, asin, d in adgroups if pt in camp_id]
    if not usable:
        print("  No campaigns available — aborting."); return

    # reuse existing ad group for an ASIN if already present (no duplicates on re-run)
    cids = list({camp_id[pt] for pt, _, _ in usable})
    existing_ag = {}
    try:
        for a in client.list_all("/sp/adGroups/list", "application/vnd.spAdGroup.v3+json", "adGroups",
                                 extra_body={"campaignIdFilter": {"include": [str(c) for c in cids]}}):
            existing_ag[(str(a.get("campaignId")), a.get("name"))] = str(a.get("adGroupId"))
    except Exception as e:
        print(f"  (couldn't list existing ad groups: {e})")

    resolved, to_create = {}, []
    for pt, asin, d in usable:
        ex = existing_ag.get((str(camp_id[pt]), f"{asin}-asin"))
        if ex:
            resolved[(pt, asin)] = ex
        else:
            to_create.append((pt, asin, d))

    if to_create:
        ag_items = [{"name": f"{asin}-asin", "campaignId": camp_id[pt],
                     "defaultBid": max(b for _, b in d["targets"])} for pt, asin, d in to_create]
        st, js = client.create_ad_groups(ag_items)
        ag_ids = success_ids(js, "adGroups", "adGroupId")
        print(f"  ad groups: {len(ag_ids)} created, {len(resolved)} reused (HTTP {st})")
        pa = []
        for i, (pt, asin, d) in enumerate(to_create):
            if i in ag_ids:
                resolved[(pt, asin)] = ag_ids[i]
                pa.append({"campaignId": camp_id[pt], "adGroupId": ag_ids[i], "asin": asin})
        if pa:
            st, js = client.create_product_ads(pa)
            print(f"  product ads created: {len(success_ids(js,'productAds','adId'))}/{len(pa)} (HTTP {st})")
    else:
        print(f"  ad groups: 0 created, {len(resolved)} reused")

    tgts, negs, promoted = [], [], []
    for pt, asin, d in usable:
        agid = resolved.get((pt, asin))
        if not agid:
            continue
        cid = camp_id[pt]
        for tgt_asin, bid in d["targets"]:
            tgts.append({"campaignId": cid, "adGroupId": agid, "asin": tgt_asin, "bid": bid})
            negs.append({"campaignId": d["src_cid"], "adGroupId": d["src_ag"], "asin": tgt_asin})
            promoted.append((tgt_asin, d["src_ag"]))

    if tgts:
        st, js = client.create_product_targets(tgts)
        print(f"  ASIN targets created: {len(success_ids(js,'targetingClauses','targetId'))}/{len(tgts)} (HTTP {st})")
    if negs:
        client.create_negative_product_targets(negs)
        print(f"  source ASIN negations submitted: {len(negs)}")

    conn.executemany("UPDATE harvest_log SET promoted=1 WHERE search_term=? AND source_ad_group_id=?", promoted)
    conn.commit()
    for t, src in promoted:
        db.log_write(conn, "harvest_promote_asin", "asin_target", t, f"ASIN target created; negated in {src}", "", "submitted")
    print(f"\nDone. {len(promoted)} ASIN targets promoted + negated.")


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
            print(f"** --terms-file: scoped to {len(terms)} approved ASIN targets **")
        except (IndexError, OSError) as e:
            raise SystemExit(f"--terms-file: {e}")
    conn = db.connect(); client = AdsClient()
    adgroups, skipped = build_plan(conn, limit, terms)
    if not adgroups:
        print("No un-promoted ASIN-target candidates."); return
    preview(adgroups)
    if skipped: print(f"  (skipped {skipped} with no ASIN on source ad group)")
    if "--apply" not in args:
        print("\nPREVIEW ONLY. Re-run with --apply (add --limit N to test)."); return
    killswitch.check()
    if "--auto" not in args and not confirm():
        print("Cancelled."); return
    apply(client, conn, adgroups)


if __name__ == "__main__":
    main()
