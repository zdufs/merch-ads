#!/usr/bin/env python3
"""
TAMAS optimizer — runs ONLY on TAMAS campaigns (name-prefixed). Separate from the
standard ACOS rules. Judges on TRAZ (royalty incl. organic − ad spend):
  - 0 sales AND spend >= TEST_MULT x unit royalty  -> pause the ad group
  - TRAZ < 0                                        -> lower the keyword bid (-15%)
  - TRAZ > 0 AND CVR >= CVR_TARGET (enough clicks)  -> raise the keyword bid (+5%)
  - else hold
Snapshot-guarded (one move per fresh pull). Gated: preview / --apply / --auto / KILL.

Usage:
  python3 tamas_optimize.py            # preview
  python3 tamas_optimize.py --apply    # apply (typed APPLY)
  python3 tamas_optimize.py --apply --auto   # scheduled (no prompt)
"""

import sys
import db
import killswitch
import products
import tamas
import traz
from ads_client import AdsClient


def build(conn, client):
    cur = conn.cursor()
    end = cur.execute("SELECT MAX(date) FROM campaign_perf").fetchone()[0]

    # ENABLED only. Matching on the name alone swept in PAUSED and ARCHIVED
    # campaigns, so a retired TAMAS account still looked live: the early return
    # below never fired and every nightly run made a pointless Amazon
    # /sp/keywords/list call for campaigns that can no longer serve. Worse, a bid
    # computed here would have been submitted against an archived campaign.
    # Same shape as scavenger_optimize, which gates its retire pass on ENABLED.
    tamas_camps = {r[0]: r[1] for r in cur.execute(
                       "SELECT campaign_id,name FROM campaigns WHERE state='ENABLED'")
                   if tamas.is_tamas(r[1])}
    if not tamas_camps:
        return end, [], []
    # perf per TAMAS campaign (latest snapshot)
    perf = {r[0]: dict(clicks=r[1] or 0, orders=r[2] or 0, cost=r[3] or 0)
            for r in cur.execute("SELECT campaign_id,clicks,orders,cost FROM campaign_perf WHERE date=?", (end,))
            if r[0] in tamas_camps}
    # TAMAS ad group name == ASIN
    ag_asin = {r[0]: r[1] for r in cur.execute("SELECT campaign_id,name FROM ad_groups")
               if r[0] in tamas_camps}
    ptype = db.get_product_map(conn)            # by ad group; fallback below
    roy = traz.load_asin_royalty()
    # current keyword bids
    kws = client.list_keywords(list(tamas_camps))
    already = {r[0] for r in cur.execute(
        "SELECT entity_id FROM writes_log WHERE action='tamas_bid' AND detail LIKE ?", (f"snap={end}%",)).fetchall()}

    bid_changes, pauses = [], []
    for k in kws:
        cid = str(k.get("campaignId")); kid = str(k.get("keywordId"))
        if kid in already:
            continue
        p = perf.get(cid)
        if not p:
            continue
        asin = ag_asin.get(cid, "")
        pt = ptype.get(str(k.get("adGroupId"))) or "standard_tshirt"
        # TAMAS tests at low prices ($13.99) OUTSIDE the tee price domain, so its
        # tee stop-loss royalty is PINNED to the minimum supported tee royalty as
        # explicit cohort policy (src=tamas_cohort_policy, PLAN.md v6) — never
        # price-derived. 10x cap: $48.90 -> $52.80. TRAZ bid logic untouched.
        unit_roy = (products.US_TEE_ROYALTY_CENTS[1999] / 100.0
                    if pt == products.TEE else products.get_econ(pt)["royalty"])
        royalty = roy.get(asin, 0.0)
        cost, clicks, orders = p["cost"], p["clicks"], p["orders"]
        trazv = royalty - cost
        cvr = (orders / clicks) if clicks else 0
        cur_bid = k.get("bid")
        if not cur_bid:
            continue

        # pause test-fails
        if orders == 0 and cost >= tamas.TEST_MULT * unit_roy:
            pauses.append((str(k.get("adGroupId")), cid, round(cost, 2)))
            continue
        new_bid, reason = None, None
        if trazv < 0 and clicks >= tamas.MIN_CLICKS:
            new_bid = round(cur_bid * tamas.SCALE_DOWN, 2); reason = f"TRAZ ${trazv:.0f} <0"
        elif trazv > 0 and cvr >= tamas.CVR_TARGET and clicks >= tamas.MIN_CLICKS:
            new_bid = round(cur_bid * tamas.SCALE_UP, 2); reason = f"TRAZ ${trazv:.0f}+, CVR {cvr*100:.0f}%"
        if new_bid is None:
            continue
        new_bid = max(tamas.MIN_BID, min(tamas.MAX_BID, new_bid))
        if abs(new_bid - cur_bid) < 0.01:
            continue
        bid_changes.append(dict(keywordId=kid, name=tamas_camps[cid], old=round(cur_bid, 2),
                                new=new_bid, reason=reason))
    return end, bid_changes, pauses


def confirm():
    return input("\nType APPLY to update TAMAS campaigns (anything else cancels): ").strip() == "APPLY"


def main():
    import markets
    if not markets.is_default():
        print(f"TAMAS is US-only — skipping for market {markets.current()}."); return
    args = sys.argv[1:]
    conn = db.connect()
    client = AdsClient()
    end, bids, pauses = build(conn, client)
    print(f"TAMAS optimize — snapshot {end}")
    print(f"  bid changes: {len(bids)}  |  ad-group pauses (10x test-fail): {len(pauses)}")
    for b in bids[:8]:
        arrow = "↑" if b["new"] > b["old"] else "↓"
        print(f"    {arrow} {b['name'][:40]}  ${b['old']:.2f}->${b['new']:.2f}  ({b['reason']})")
    if not bids and not pauses:
        print("  nothing to do."); return
    if "--apply" not in args:
        print("\nPREVIEW ONLY. Re-run with --apply."); return
    killswitch.check()
    if "--auto" not in args and not confirm():
        print("Cancelled."); return

    if bids:
        client.update_keyword_bids([{"keywordId": b["keywordId"], "bid": b["new"]} for b in bids])
        for b in bids:
            db.log_write(conn, "tamas_bid", "keyword", b["keywordId"],
                         f"snap={end} {b['old']}->{b['new']} ({b['reason']})", str(b["old"]), "submitted")
        print(f"  updated {len(bids)} TAMAS bids.")
    if pauses:
        pids = [a for a, _, _ in pauses]
        client.pause_ad_groups(pids)
        for a, cid, spend in pauses:
            db.log_write(conn, "tamas_pause", "adGroup", a, f"snap={end} 10x test-fail ${spend}", "ENABLED", "submitted")
        db.set_local_ad_group_state(conn, pids, "PAUSED")   # keep local mirror in sync
        print(f"  paused {len(pauses)} TAMAS ad groups (test-fail).")
    print("Done.")


if __name__ == "__main__":
    main()
