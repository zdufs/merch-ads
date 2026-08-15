#!/usr/bin/env python3
"""
LIVE status check for one or more ASINs (or campaigns) — queries the Amazon Ads API
in real time, so it NEVER relies on the possibly-stale local snapshot for state.

The local DB is used only to MAP an ASIN to the ad groups/campaigns it lives in
(structure, which rarely changes). The actual state (ENABLED / PAUSED / ARCHIVED) is
pulled fresh from the API at call time, and the local mirror is healed with what we see.

Recent performance (spend/sales/ACOS/CVR) is shown from the cached ~30-day snapshot for
context and clearly labelled as cached.

Usage:
  python3 status.py B0EXAMPLE1 [B0XXXX ...]      # US (default)
  ADS_MARKET=DE python3 status.py B0XXXX         # another market
"""

import os
import sys

import db
import markets
from ads_client import AdsClient


def _perf(conn, ad_group_id):
    """Cached ~30-day spend/sales/orders/clicks for an ad group (latest snapshot)."""
    end = conn.execute("SELECT MAX(date) FROM targeting_perf").fetchone()[0]
    r = conn.execute(
        """SELECT SUM(cost), SUM(sales), SUM(orders), SUM(clicks)
           FROM targeting_perf WHERE date=? AND ad_group_id=?""", (end, ad_group_id)).fetchone()
    return [x or 0 for x in (r or (0, 0, 0, 0))]


def lookup(conn, asins):
    """ASIN -> list of (ad_group_id, ad_group_name, campaign_id, campaign_name, type, lifetime)."""
    out = {}
    for a in asins:
        rows = conn.execute(
            """SELECT agp.ad_group_id, ag.name, c.campaign_id, c.name, agp.product_type, agp.lifetime_sales
               FROM ad_group_product agp
               JOIN ad_groups ag ON ag.ad_group_id=agp.ad_group_id
               JOIN campaigns  c ON c.campaign_id=ag.campaign_id
               WHERE agp.asin=?""", (a,)).fetchall()
        out[a] = rows
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args:
        print("Usage: python3 status.py ASIN [ASIN ...]"); return
    mkt = markets.current()
    conn = db.connect()
    client = AdsClient()

    mapping = lookup(conn, args)
    all_ag = sorted({r[0] for rows in mapping.values() for r in rows})
    all_cmp = sorted({r[2] for rows in mapping.values() for r in rows})
    if not all_ag:
        print(f"[{mkt}] None of those ASINs are mapped to ad groups locally "
              f"(not advertised, or not yet mapped). Nothing to check."); return

    # LIVE state from the API
    print(f"[{mkt}] Querying Amazon Ads API live for {len(all_ag)} ad group(s) / {len(all_cmp)} campaign(s)…")
    live_ag = {str(g["adGroupId"]): g for g in client.list_ad_groups_by_id(all_ag)}
    live_cmp = {str(c["campaignId"]): c for c in client.list_campaigns_by_id(all_cmp)}

    # heal the local mirror with what we just saw
    for st in ("ENABLED", "PAUSED", "ARCHIVED"):
        db.set_local_ad_group_state(conn, [g for g, v in live_ag.items() if v.get("state") == st], st)
        db.set_local_campaign_state(conn, [c for c, v in live_cmp.items() if v.get("state") == st], st)

    for asin, rows in mapping.items():
        print(f"\n================ {asin} ================")
        if not rows:
            print("  not advertised / not mapped locally."); continue
        print(f"  type={rows[0][4]}  lifetime_sales={rows[0][5]}")
        # sort: enabled first, then by spend
        enriched = []
        for agid, agname, cid, cname, ptype, life in rows:
            ag_state = (live_ag.get(str(agid)) or {}).get("state", "??(not returned)")
            cm_state = (live_cmp.get(str(cid)) or {}).get("state", "??")
            cost, sales, orders, clicks = _perf(conn, agid)
            acos = (cost / sales * 100) if sales else None
            cvr = (orders / clicks * 100) if clicks else 0
            enriched.append((ag_state, cost, agname, cname, cm_state, acos, cvr, sales, orders, clicks))
        enriched.sort(key=lambda e: (e[0] != "ENABLED", -e[1]))
        for ag_state, cost, agname, cname, cm_state, acos, cvr, sales, orders, clicks in enriched:
            acos_s = f"{acos:.0f}%" if acos is not None else "—"
            print(f"   • {cname[:34]:34} | campaign:{cm_state:8} ad group:{ag_state:8}")
            print(f"       cached ~30d: ${cost:7.2f} spend / ${sales:8.2f} sales / "
                  f"ACOS {acos_s} / CVR {cvr:.0f}% / {orders} ord")
    print("\n(State = LIVE from Amazon just now. Spend/sales = cached ~30-day snapshot.)")


if __name__ == "__main__":
    main()
