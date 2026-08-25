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
from phase4_harvest_create import (LABELS, CAMPAIGN_BUDGET, SP_CAMP,
                                   report, summary)

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
        print("  No campaigns available — aborting.")
        return summary("asins", aborted="no campaigns available")

    # reuse existing ad group for an ASIN if already present (no duplicates on re-run)
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
        # An ad group is NOT a destination until its product ad lands. This was
        # set the moment the ad group was created, before the product ad was
        # even requested, so a failed product ad gave us a destination that
        # advertises nothing — and the code below then created the target there
        # and NEGATED the earning source. Same fault as the keyword half.
        pa, pa_owner = [], []
        for i, (pt, asin, d) in enumerate(to_create):
            if i in ag_ids:
                pa.append({"campaignId": camp_id[pt], "adGroupId": ag_ids[i], "asin": asin})
                pa_owner.append((pt, asin, ag_ids[i]))
        if pa:
            st, js = client.create_product_ads(pa)
            pa_ok = success_ids(js, "productAds", "adId")
            for j, (pt, asin, agid) in enumerate(pa_owner):
                if j in pa_ok:
                    resolved[(pt, asin)] = agid
            print(f"  product ads created: {len(pa_ok)}/{len(pa)} (HTTP {st})")
            if len(pa_ok) < len(pa_owner):
                print(f"  {len(pa_owner) - len(pa_ok)} new ad group(s) got NO product"
                      f" ad — they cannot serve, so their source ASINs are left"
                      f" ALONE and stay eligible.")
    else:
        print(f"  ad groups: 0 created, {len(resolved)} reused")

    tgts, negs, promoted = [], [], []
    # Create FIRST, then negate only what was created. Both writes used to go out
    # regardless of each other, so a rejected target plus an accepted negative
    # stopped an ASIN that was earning — and marked it promoted, so it never
    # came back. Same fault as the keyword half.
    plan = []
    for pt, asin, d in usable:
        agid = resolved.get((pt, asin))
        if not agid:
            continue
        cid = camp_id[pt]
        for tgt_asin, bid in d["targets"]:
            tgts.append({"campaignId": cid, "adGroupId": agid, "asin": tgt_asin, "bid": bid})
            plan.append(({"campaignId": d["src_cid"], "adGroupId": d["src_ag"],
                          "asin": tgt_asin}, (tgt_asin, d["src_ag"])))

    created = {}
    if tgts:
        st, js = client.create_product_targets(tgts)
        created = success_ids(js, "targetingClauses", "targetId")
        print(f"  ASIN targets created: {len(created)}/{len(tgts)} (HTTP {st})")
        if len(created) < len(tgts):
            print(f"  {len(tgts) - len(created)} replacement target(s) were NOT"
                  f" created — their source ASINs are left ALONE and stay eligible.")
    negs = [plan[i][0] for i in sorted(created)]
    promoted = [plan[i][1] for i in sorted(created)]
    # The negative's response was discarded and every ASIN was then logged
    # "negated in <src>" as submitted whether or not it landed. A failed
    # negative leaves the ASIN serving in BOTH places, competing with its own
    # replacement and paying twice, with nothing saying so.
    #
    # THREE states, not two. `create_negative_product_targets` returns
    # (status, json) and this endpoint's success block is not one the engine
    # parses anywhere else, so a 2xx whose body we cannot read must NOT be
    # called a failure: an alarm that fires on every promotion gets muted, and
    # then the real one is missed too. Unreadable is recorded as unconfirmed.
    negated, unconfirmed = set(), False
    if negs:
        st, js = client.create_negative_product_targets(negs)
        block = (js or {}).get("negativeTargetingClauses") or {}
        ok_idx = {it["index"] for it in (block.get("success") or []) if "index" in it}
        err_idx = {it["index"] for it in (block.get("error") or []) if "index" in it}
        if st not in (200, 207):
            print(f"  source ASIN negations: HTTP {st} — none landed")
        elif ok_idx or err_idx:
            negated = ok_idx
            print(f"  source ASIN negations: {len(negated)}/{len(negs)} landed")
            if len(negated) < len(negs):
                print(f"  {len(negs) - len(negated)} source negative(s) were NOT"
                      f" created — those ASINs still serve in their source ad"
                      f" group and now compete with their own replacement.")
        else:
            unconfirmed = True
            print(f"  source ASIN negations: HTTP {st}, but the response could"
                  f" not be read — {len(negs)} recorded as UNCONFIRMED.")

    conn.executemany("UPDATE harvest_log SET promoted=1 WHERE search_term=? AND source_ad_group_id=?", promoted)
    conn.commit()
    for i, (t, src) in enumerate(promoted):
        if unconfirmed:
            db.log_write(conn, "harvest_promote_asin", "asin_target", t,
                         f"ASIN target created; source negative in {src} submitted "
                         f"but NOT confirmed — check it before trusting it",
                         "", "submitted")
        elif i in negated:
            db.log_write(conn, "harvest_promote_asin", "asin_target", t,
                         f"ASIN target created; negated in {src}", "", "submitted")
        else:
            db.log_write(conn, "harvest_promote_asin", "asin_target", t,
                         f"ASIN target created, but the source negative was NOT "
                         f"created — the ASIN still serves in {src} and is now "
                         f"competing with its own replacement. Add it by hand.",
                         "", "failed")
    print(f"\nDone. {len(promoted)} ASIN targets promoted + negated.")
    # An UNCONFIRMED negative is deliberately not a refusal. The response body
    # for this endpoint is one the engine parses nowhere else, so a 2xx we
    # cannot read must not raise an alarm that would fire on every promotion.
    # It is counted and named instead, and the exit code stays clean.
    return summary("asins",
                   requested=len(tgts), created=len(created),
                   negatives_requested=len(negs),
                   negatives_landed=0 if unconfirmed else len(negated),
                   negatives_refused=0 if unconfirmed else len(negs) - len(negated),
                   negatives_unconfirmed=len(negs) if unconfirmed else 0,
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
            print(f"** --terms-file: scoped to {len(terms)} approved ASIN targets **")
        except (IndexError, OSError) as e:
            raise SystemExit(f"--terms-file: {e}")
    conn = db.connect(); client = AdsClient()
    adgroups, skipped = build_plan(conn, limit, terms)
    if not adgroups:
        print("No un-promoted ASIN-target candidates.")
        return report(summary("asins"))
    preview(adgroups)
    if skipped: print(f"  (skipped {skipped} with no ASIN on source ad group)")
    if "--apply" not in args:
        print("\nPREVIEW ONLY. Re-run with --apply (add --limit N to test)."); return 0
    killswitch.check()
    if "--auto" not in args and not confirm():
        print("Cancelled."); return 0
    return report(apply(client, conn, adgroups))


if __name__ == "__main__":
    sys.exit(main())
