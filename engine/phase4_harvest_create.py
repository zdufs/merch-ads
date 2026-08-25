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
import json
import sys
import db
import killswitch
from ads_client import AdsClient, success_ids

CAMPAIGN_BUDGET = 25.00   # daily $ per harvested campaign
SP_CAMP = "application/vnd.spCampaign.v3+json"

LABELS = {
    "standard_tshirt": "Tees", "premium_tshirt": "Premium Tees",
    "performance_tshirt": "Performance Tees",
    "vneck": "V-Necks", "tank_top": "Tanks", "long_sleeve": "Long Sleeves",
    "standard_sweatshirt": "Sweatshirts", "standard_pullover_hoodie": "Pullover Hoodies",
    "zip_hoodie": "Zip Hoodies", "polo": "Polos",
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


# ---- what the run actually did -----------------------------------------------
# Amazon refuses individual writes inside a batch and still answers 200 for the
# batch, so an exit code alone cannot say a promotion went through. This script
# already printed what it could not do; it then exited 0, and `appctl promote`
# kept only that code. A promotion in which every source negative was refused
# reached the app as a green "keywords exit 0".
#
# The last line of stdout is now the counts, and `appctl promote` reads it back.
# phase4b prints the same shape, so one reader serves both.
RESULT_PREFIX = "PROMOTE_RESULT "


def summary(phase, **kw):
    """The counts both phases report, with every field always present."""
    base = {"phase": phase, "requested": 0, "created": 0,
            "negatives_requested": 0, "negatives_landed": 0,
            "negatives_refused": 0, "negatives_unconfirmed": 0,
            "promoted": 0, "aborted": None}
    base.update(kw)
    return base


def report(got):
    """Print the machine-readable line and answer with this run's exit code.

    A refused SOURCE NEGATIVE is the failure that costs money: the replacement
    keyword is live, and the term still serves in the ad group it was meant to
    leave, so the two now bid against each other and the design pays twice. An
    abort means Amazon refused the campaigns and nothing was promoted at all.
    Both exit non-zero, so the nightly counts them as a failed step instead of
    printing a warning into a log nobody reads.
    """
    print(RESULT_PREFIX + json.dumps(got))
    return 1 if (got.get("negatives_refused") or got.get("aborted")) else 0


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
        print("  No campaigns available — aborting before ad groups.")
        return summary("keywords", aborted="no campaigns available")

    # 2) ad groups — REUSE an existing ad group for an ASIN if one's already there
    # (a design harvested before for another term shouldn't create a duplicate ad group)
    cids = list({camp_id[pt] for pt, _, _ in usable})
    existing_ag = {}
    try:
        for a in client.list_all("/sp/adGroups/list", "application/vnd.spAdGroup.v3+json", "adGroups",
                                 extra_body={"campaignIdFilter": {"include": [str(c) for c in cids]}}):
            existing_ag[(str(a.get("campaignId")), a.get("name"))] = str(a.get("adGroupId"))
    except Exception as e:
        # An EMPTY inventory means "no ad group exists for this ASIN yet", and a
        # failed listing produced exactly that. The builder then put every
        # candidate into to_create and submitted duplicate ad groups, product
        # ads and targeting for ASINs that were already there. "We could not
        # look" is not "there is nothing", and only one of them is safe to act
        # on. Stop: the next run rebuilds the same plan from the same data.
        print(f"  ABORTING: could not list existing ad groups ({e}). Creating "
              f"without that list would submit duplicates for every ASIN "
              f"already in these campaigns. Nothing was written; re-run when "
              f"Amazon answers.", file=sys.stderr)
        return

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
        # An ad group is NOT a destination until its product ad lands.
        #
        # `resolved` used to be set the moment the ad group was created, before
        # the product ad was even requested. An ad group with no product ad
        # advertises nothing, so a failed product ad gave us a destination that
        # can never serve — and the code below then created the keyword there
        # and NEGATED the earning source. The term stops serving in the source
        # and cannot serve in the replacement: a term that was making money,
        # dead, marked promoted, logged as a clean promotion.
        pa_items, pa_owner = [], []
        for i, (pt, asin, d) in enumerate(to_create):
            if i in ag_ids:
                pa_items.append({"campaignId": camp_id[pt], "adGroupId": ag_ids[i], "asin": asin})
                pa_owner.append((pt, asin, ag_ids[i]))
        if pa_items:
            st, js = client.create_product_ads(pa_items)
            pa_ok = success_ids(js, "productAds", "adId")
            for j, (pt, asin, agid) in enumerate(pa_owner):
                if j in pa_ok:
                    resolved[(pt, asin)] = agid
            print(f"  product ads created: {len(pa_ok)}/{len(pa_items)} (HTTP {st})")
            if len(pa_ok) < len(pa_owner):
                print(f"  {len(pa_owner) - len(pa_ok)} new ad group(s) got NO product"
                      f" ad — they cannot serve, so their source terms are left"
                      f" ALONE and stay eligible.")
    else:
        print(f"  ad groups: 0 created, {len(resolved)} reused")

    # 3) keywords, THEN negate the source — and only for the terms whose
    # replacement Amazon actually created.
    #
    # These two writes used to go out unconditionally, side by side. A promotion
    # is "start this term over here, stop it over there", so if the create is
    # rejected and the negative is accepted, a term that was EARNING stops
    # serving anywhere. It was then marked promoted=1, so it never came back for
    # another attempt, and writes_log recorded a clean promotion. The count
    # printed was the count planned.
    kw_items, plan = [], []
    for pt, asin, d in usable:
        agid = resolved.get((pt, asin))
        if not agid:
            continue
        for term, bid in d["kw"]:
            kw_items.append({"campaignId": camp_id[pt], "adGroupId": agid,
                             "keywordText": term, "bid": bid})
            plan.append({"campaignId": d["src_cid"], "adGroupId": d["src_ag"],
                         "keywordText": term, "_src": d["src_ag"], "_term": term})

    created = {}
    if kw_items:
        st, js = client.create_keywords(kw_items)
        created = success_ids(js, "keywords", "keywordId")
        print(f"  exact keywords created: {len(created)}/{len(kw_items)} (HTTP {st})")
        if len(created) < len(kw_items):
            print(f"  {len(kw_items) - len(created)} replacement keyword(s) were NOT"
                  f" created — their source terms are left ALONE and stay eligible.")

    neg_items = [{k: v for k, v in plan[i].items() if not k.startswith("_")}
                 for i in sorted(created)]
    promoted = [(plan[i]["_term"], plan[i]["_src"]) for i in sorted(created)]
    # The negative's response was DISCARDED, and every term was then logged
    # "negated in <src>" as submitted whether or not it landed. A failed
    # negative leaves the term serving in BOTH places — the new keyword and the
    # source it was meant to replace — competing with itself and paying twice,
    # with nothing anywhere saying so. `harvest_promote_group` was fixed for
    # this on 2026-08-23; this is its twin, and it was not.
    negated = set()
    if neg_items:
        neg_results = client.create_negative_keywords(neg_items)
        # created_ids is one entry per input item, in order, None where that
        # item errored — so flattening the batches keeps the alignment.
        landed = [nid for r in neg_results for nid in (r.get("created_ids") or [])]
        negated = {i for i, nid in enumerate(landed) if nid is not None}
        print(f"  source negations: {len(negated)}/{len(neg_items)} landed")
        if len(negated) < len(neg_items):
            print(f"  {len(neg_items) - len(negated)} source negative(s) were NOT"
                  f" created — those terms still serve in their source ad group"
                  f" and are now competing with their own replacement.")

    # promoted=1 stays even when the negative failed: the replacement keyword IS
    # live, so re-queuing the term would create it a second time. The same
    # deliberate choice harvest_promote_group makes. What has to be true is the
    # audit row.
    conn.executemany("UPDATE harvest_log SET promoted=1 WHERE search_term=? AND source_ad_group_id=?", promoted)
    conn.commit()
    for i, (term, src) in enumerate(promoted):
        if i in negated:
            db.log_write(conn, "harvest_promote", "keyword", term,
                         f"exact-match created; negated in {src}", "", "submitted")
        else:
            db.log_write(conn, "harvest_promote", "keyword", term,
                         f"exact-match created, but the source negative was NOT "
                         f"created — the term still serves in {src} and is now "
                         f"competing with its own replacement. Add the negative "
                         f"by hand.", "", "failed")
    print(f"\nDone. {len(promoted)} terms promoted + negated. Logged in writes_log.")
    return summary("keywords",
                   requested=len(kw_items), created=len(created),
                   negatives_requested=len(neg_items),
                   negatives_landed=len(negated),
                   negatives_refused=len(neg_items) - len(negated),
                   promoted=len(promoted))


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
        print("No un-promoted keyword candidates to harvest.")
        return report(summary("keywords"))
    preview(adgroups)
    if skipped:
        print(f"  (skipped {skipped} candidates with no ASIN on their source ad group)")
    if "--apply" not in args:
        print("\nPREVIEW ONLY. Re-run with --apply (add --limit N to test small first).")
        return 0
    killswitch.check()
    if "--auto" not in args and not confirm():
        print("Cancelled — nothing created."); return 0
    return report(apply(client, conn, adgroups))


if __name__ == "__main__":
    sys.exit(main())
