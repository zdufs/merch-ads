# harvest_promote_group.py
"""Promote a cohort search-term winner to a chosen FAMILY of designs.

Unlike phase4 (one ad group per single design), a cohort winner targets a set of
related designs the operator picked. This builds one ad group per product type
under the reused "Harvested <type> - Exact" campaign, puts the chosen designs in
it as product ads, adds the phrase as an EXACT keyword, negates the phrase in the
source cohort, and marks the winner promoted. The plan builder is pure and tested;
apply runs through ads_client and is operator-run.
"""

import datetime

import db
import phase4_harvest_create as p4


def build_group_plan(conn, term, source_ad_group_id, source_campaign_id, asins):
    cur = conn.cursor()
    row = cur.execute("SELECT cpc FROM harvest_log WHERE search_term=? AND source_ad_group_id=?",
                      (term, source_ad_group_id)).fetchone()
    cpc = (row[0] if row and row[0] else 0.20)
    bid = max(0.10, round(cpc * 1.15, 2))
    ptmap = {r[0]: r[1] for r in cur.execute(
        "SELECT asin, product_type FROM ad_group_product WHERE asin IS NOT NULL")}
    by_type, skipped = {}, []
    for a in asins:
        pt = ptmap.get(a)
        if not pt:
            skipped.append(a); continue
        by_type.setdefault(pt, []).append(a)
    groups = [{"product_type": pt, "campaign_name": p4.camp_name(pt), "asins": aa}
              for pt, aa in by_type.items()]
    return {"term": term, "source_ad_group_id": source_ad_group_id,
            "source_campaign_id": source_campaign_id, "bid": bid,
            "groups": groups, "skipped_asins": skipped}


def apply_group(client, conn, plan):
    today = datetime.date.today().isoformat()
    term, bid = plan["term"], plan["bid"]
    # 1) campaigns (reuse by name, else create) — one per product type in the plan
    have = p4.existing_campaigns(client)
    names = {g["campaign_name"] for g in plan["groups"]}
    to_make = [n for n in names if n not in have]
    camp_id = {n: have[n] for n in names if n in have}
    campaigns_created = 0
    if to_make:
        st, js = client.create_campaigns(
            [{"name": n, "budget": p4.CAMPAIGN_BUDGET, "startDate": today} for n in to_make])
        ids = p4.success_ids(js, "campaigns", "campaignId")
        for i, n in enumerate(to_make):
            if i in ids:
                camp_id[n] = ids[i]; campaigns_created += 1

    # 2) ad groups — REUSE an existing ad group whose name matches (a retried
    # promote of the same term shouldn't create a duplicate ad group + a
    # duplicate EXACT keyword); mirrors phase4_harvest_create.py:apply().
    usable = [g for g in plan["groups"] if g["campaign_name"] in camp_id]
    cids = list({camp_id[g["campaign_name"]] for g in usable})
    existing_ag = {}
    if cids:
        try:
            for a in client.list_all(
                    "/sp/adGroups/list", "application/vnd.spAdGroup.v3+json", "adGroups",
                    extra_body={"campaignIdFilter": {"include": [str(c) for c in cids]}}):
                existing_ag[(str(a.get("campaignId")), a.get("name"))] = str(a.get("adGroupId"))
        except Exception:
            pass

    # 3) product ads = chosen designs, keyword = the phrase, per group's ad group
    ad_groups_created = keywords_created = product_ads_created = 0
    groups_with_keyword = 0
    for g in usable:
        cid = camp_id[g["campaign_name"]]
        ag_name = f"{term[:70]} [{g['product_type']}]"
        agid = existing_ag.get((str(cid), ag_name))
        if not agid:
            st, js = client.create_ad_groups([{"name": ag_name, "campaignId": cid, "defaultBid": bid}])
            ag_ids = p4.success_ids(js, "adGroups", "adGroupId")
            agid = ag_ids.get(0)
            if not agid:
                continue
            ad_groups_created += 1
        st, js = client.create_product_ads([{"campaignId": cid, "adGroupId": agid, "asin": a}
                                            for a in g["asins"]])
        product_ads_created += len(p4.success_ids(js, "productAds", "adId"))
        st, js = client.create_keywords([{"campaignId": cid, "adGroupId": agid,
                                          "keywordText": term, "bid": bid, "matchType": "EXACT"}])
        new_ids = p4.success_ids(js, "keywords", "keywordId")
        keywords_created += len(new_ids)
        keyword_present = bool(new_ids)
        if not keyword_present:
            # Amazon rejected the create with zero new ids. That's ambiguous: it
            # could be a real failure, or it could be a RETRY of a prior run that
            # created this exact keyword and then failed before negating/
            # promoting — Amazon rejects the duplicate create outright, which
            # used to strand the winner forever. Check whether it's already
            # live in this ad group before writing it off as a dead end.
            try:
                for kw in client.list_keywords([cid]):
                    if (str(kw.get("adGroupId")) == str(agid)
                            and (kw.get("keywordText") or "").lower() == term.lower()):
                        keyword_present = True
                        break
            except Exception:
                pass
        if keyword_present:
            groups_with_keyword += 1

    # 4) negate the phrase in the source cohort ad group + mark promoted + log —
    # ONLY when the keyword is actually live in at least one group (freshly
    # created OR already present from a prior attempt). Unconditionally
    # negating/promoting would strip the phrase out of a working source ad
    # group with nothing built to replace it, and promoted=1 would hide the
    # failure from ever re-queuing.
    negations = 0
    promoted = False
    if groups_with_keyword > 0:
        neg_results = client.create_negative_keywords(
            [{"campaignId": plan["source_campaign_id"],
             "adGroupId": plan["source_ad_group_id"], "keywordText": term}])
        negations = sum(1 for r in neg_results for nid in (r.get("created_ids") or [])
                        if nid is not None)
        conn.execute("UPDATE harvest_log SET promoted=1 WHERE search_term=? AND source_ad_group_id=?",
                     (term, plan["source_ad_group_id"]))
        conn.commit()
        db.log_write(conn, "harvest_promote", "keyword", term,
                     f"family exact-match live in {groups_with_keyword} group(s) "
                     f"({keywords_created} newly created); negated in {plan['source_ad_group_id']}",
                     "", "submitted")
        promoted = True
    return {"campaigns_created": campaigns_created, "ad_groups_created": ad_groups_created,
            "product_ads_created": product_ads_created, "keywords_created": keywords_created,
            "groups_with_keyword": groups_with_keyword,
            "negations": negations, "promoted": promoted}


def promote_group(term, source_ad_group_id, source_campaign_id, asins, apply=False):
    # Dry run does no writes — open read-only so it never contends with the
    # nightly writer for a lock; apply needs a real read-write connection.
    conn = db.connect(ro=True) if not apply else db.connect()
    plan = build_group_plan(conn, term, source_ad_group_id, source_campaign_id, asins)
    if not apply:
        return {"plan": plan, "applied": False}
    from ads_client import AdsClient
    result = apply_group(AdsClient(), conn, plan)
    return {"plan": plan, "applied": True, "result": result}
