#!/usr/bin/env python3
"""
Write outputs/demand_feed.json — the contract MerchPirate's "Demand Feed" intake
reads. Two streams:
  - keyword_seeds : proven-converting search terms (≥2 ad orders), IP-filtered,
                    for NEW designs aimed at real demand.
  - proven_sellers: top ASINs by recent royalty (from the Merch export), for
                    MerchPirate's prompt-recovery / variation path.
Read-only. Regenerated each scheduled cycle.

Run:  python3 demand_feed.py
"""

import csv
import glob
import json
import os

import paths
import re
import db
import markets
import products

csv.field_size_limit(10**9)
HERE = paths.REPO_ROOT
# The catalogue folder is paths.POD_ROOT, never dirname(REPO_ROOT).
# The two agree only while MERCHADS_POD_DIR is unset. Set it, and half the
# engine reads one catalogue while products.export_signature() — which does use
# POD_ROOT — banks the signature of another, so the economics gate certifies a
# catalogue that was never mapped.
POD = paths.POD_ROOT
OUTDIR = os.path.join(HERE, "outputs"); os.makedirs(OUTDIR, exist_ok=True)
_MKT = markets.current()
EXPORT_MKT = markets.cfg()["export_mkt"]
OUT = os.path.join(OUTDIR, "demand_feed.json" if markets.is_default()
                   else f"demand_feed_{_MKT}.json")

MIN_ORDERS = 2
ASIN_RE = re.compile(r"^b0[0-9a-z]{8}$", re.I)
# Known IP / brand / person terms to exclude. EDIT as new ones appear.
# NOTE: this is best-effort; MerchPirate must keep a human IP review gate before upload.
IP_BLOCK = ["mumford and sons", "post malone", "dandy's world", "dandys world",
            "alo ", "pipkin", "davila"]

def ip_blocked(t):
    tl = (t or "").lower()
    return any(b in tl for b in IP_BLOCK)

conn = db.connect()
cur = conn.cursor()
end = cur.execute("SELECT MAX(date) FROM campaign_perf").fetchone()[0]
# search_term_perf is pulled less often than campaign_perf, so its latest snapshot
# date usually lags `end`. Anchor the seed query to the term table's OWN newest
# date (each snapshot is the full trailing-window cumulative), else keyword_seeds
# silently collapses to 0 on any day search terms weren't pulled.
seed_end = cur.execute("SELECT MAX(date) FROM search_term_perf").fetchone()[0]
agp = {r[0]: r[1] for r in cur.execute("SELECT ad_group_id,product_type FROM ad_group_product")}
cname = {r[0]: r[1] for r in cur.execute("SELECT campaign_id,name FROM campaigns")}

# --- keyword seeds (converting terms) ---
seen, seeds = set(), []
for st, cid, agid, clk, orders, cost, sales, acos in cur.execute(
    """SELECT search_term,campaign_id,ad_group_id,clicks,orders,cost,sales,acos
       FROM search_term_perf WHERE date=? AND orders>=? AND search_term IS NOT NULL
       ORDER BY orders DESC, sales DESC""", (seed_end, MIN_ORDERS)):
    if ASIN_RE.match(st.strip()) or ip_blocked(st) or st in seen:
        continue
    seen.add(st)
    seeds.append({
        "term": st, "niche": cname.get(cid, ""), "product_type": agp.get(str(agid), "standard_tshirt"),
        "orders": orders, "sales": round(sales or 0, 2),
        "acos": round(acos or 0, 4), "cvr": round((orders / clk) if clk else 0, 3),
    })

# --- proven sellers (top by royalty from the product catalog) ---
# The MerchFlow export carried royaltyLast30. Snap for MOD does not, so the
# ranking falls back to ALL-TIME royalty and says which one it used, rather
# than reading a lifetime figure as if it were a month.
proven = []
import export_reader


def _int(value):
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _money(value):
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


if export_reader.catalog_files(POD):
    rows = []
    for p in export_reader.catalog_rows(POD, marketplace=EXPORT_MKT):
        if p.get("status") != "published" or ip_blocked(p.get("productTitle", "")):
            continue
        try:
            roy = float(p.get("royaltyLast30") or 0)
        except (TypeError, ValueError):
            roy = 0.0
        if roy <= 0:
            try:
                lifetime_roy = float(p.get("royaltyTotal") or 0)
            except (TypeError, ValueError):
                lifetime_roy = 0.0
            if lifetime_roy > 0:
                rows.append((lifetime_roy, "lifetime", p))
            continue
        rows.append((roy, "last30", p))
    rows.sort(key=lambda x: -x[0])
    for roy, row_basis, p in rows[:60]:
        proven.append({
            "asin": p.get("asin", ""), "title": (p.get("productTitle") or "")[:80],
            "product_type": p.get("productType", ""), "brand": p.get("brandName", ""),
            "royalty_basis": row_basis,
            "royalty": round(roy, 2),
            "royalty_last30": round(roy, 2) if row_basis == "last30" else 0,
            "royalty_total": _money(p.get("royaltyTotal")),
            "sales_last30": _int(p.get("salesLast30")),
            "sales_total": _int(p.get("salesTotal")),
            "action": "variation",
        })

feed = {
    "schema": "merchads.demand_feed/v1",
    # `end` is campaign_perf's newest date. keyword_seeds are read from
    # search_term_perf at `seed_end`, which is a DIFFERENT report job and lags
    # whenever that job has failed. Stamping the feed with `end` dated the seeds
    # to a day they did not come from, and the seeds are the whole point of the
    # file. Say both, and let "generated" mean the seeds it carries.
    "generated": seed_end or end,
    "seeds_as_of": seed_end,
    "sellers_as_of": end,
    "market": _MKT,
    "source": "merch-ads",
    "notes": "keyword_seeds = proven-converting customer searches (make NEW designs). "
             "proven_sellers = top earners (make VARIATIONS). IP best-effort filtered; "
             "human IP/trademark review still required before upload.",
    "keyword_seeds": seeds,
    "proven_sellers": proven,
}
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(feed, f, indent=2)
print(f"Demand feed written: {OUT}")
print(f"  keyword_seeds: {len(seeds)}  | proven_sellers: {len(proven)}")
conn.close()
