#!/usr/bin/env python3
"""
Export ASINs of ALL currently-paused ad groups that are STANDARD T-SHIRTS,
excluding the "Retro Name Vault" brand. A worklist for manual price changes in Merch.
Read-only — changes nothing.

How it knows product type / brand / current price: joins the paused ASINs (from
the Ads API) against your Merch product export CSV in the POD folder
(columns: asin, productType, brandName, listPrice, productTitle).

Output: paused_asins.csv (asin, brand, title, current_price, ad_group_id, campaign, new_price[blank])

Run:  python3 export_paused_asins.py
"""

import csv
import glob
import os

import paths
import sqlite3
import db
from ads_client import AdsClient

csv.field_size_limit(10**9)

HERE = paths.REPO_ROOT
POD = os.path.dirname(HERE)
OUTDIR = os.path.join(HERE, "outputs"); os.makedirs(OUTDIR, exist_ok=True)
OUT = os.path.join(OUTDIR, "paused_asins.csv")
SP_AG = "application/vnd.spAdGroup.v3+json"

TARGET_TYPE = "standard_tshirt"
EXCLUDE_BRAND = "retro name vault"

# locate the product export CSV
matches = sorted(glob.glob(os.path.join(POD, "export_products_*.csv")))
if not matches:
    raise SystemExit(f"No export_products_*.csv found in {POD}")
PRODUCT_CSV = matches[-1]
print(f"Product export: {os.path.basename(PRODUCT_CSV)}")

conn = sqlite3.connect(db.DB_PATH)
cur = conn.cursor()
end = cur.execute("SELECT MAX(date) FROM campaign_perf").fetchone()[0]
client = AdsClient()

# 1) every PAUSED ad group account-wide
print("Fetching all PAUSED ad groups…")
paused = client.list_all("/sp/adGroups/list", SP_AG, "adGroups",
                         extra_body={"stateFilter": {"include": ["PAUSED"]}})
paused_ids = {str(a.get("adGroupId")) for a in paused}
print(f"  paused ad groups: {len(paused_ids):,}")
if not paused_ids:
    raise SystemExit("No paused ad groups found.")

# 2) ASIN per paused ad group (from product ads)
print("Fetching ASINs for paused ad groups (may take a few minutes)…")
ads = client.list_product_ads(paused_ids)
asin_meta = {}   # asin -> (ad_group_id, campaignId)
for a in ads:
    agid = str(a.get("adGroupId"))
    if agid in paused_ids and a.get("asin"):
        asin_meta[a["asin"]] = (agid, a.get("campaignId"))
paused_asins = set(asin_meta)
print(f"  distinct paused ASINs: {len(paused_asins):,}")

# 3) stream the product CSV; keep standard_tshirt, drop Retro Name Vault
camp_name = dict(cur.execute("SELECT campaign_id,name FROM campaigns").fetchall())
rows, excluded_brand, wrong_type, unmatched = [], 0, 0, set(paused_asins)
with open(PRODUCT_CSV, newline="", encoding="utf-8", errors="replace") as fh:
    r = csv.DictReader(fh)
    for prod in r:
        asin = prod.get("asin")
        if asin not in paused_asins:
            continue
        unmatched.discard(asin)
        if (prod.get("productType") or "") != TARGET_TYPE:
            wrong_type += 1
            continue
        if EXCLUDE_BRAND in (prod.get("brandName") or "").lower():
            excluded_brand += 1
            continue
        agid, cid = asin_meta[asin]
        rows.append({
            "asin": asin,
            "brand": prod.get("brandName", ""),
            "title": (prod.get("productTitle") or "")[:60],
            "current_price": prod.get("listPrice", ""),
            "ad_group_id": agid,
            "campaign": camp_name.get(cid, ""),
            "new_price": "",
        })

rows.sort(key=lambda x: x["brand"])
with open(OUT, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["asin", "brand", "title", "current_price",
                                      "ad_group_id", "campaign", "new_price"])
    w.writeheader()
    w.writerows(rows)

print(f"\nPaused ASINs that are standard_tshirt : {len(rows):,}")
print(f"  excluded — Retro Name Vault brand   : {excluded_brand:,}")
print(f"  skipped — other product types       : {wrong_type:,}")
print(f"  not found in product export         : {len(unmatched):,}")
print(f"Exported -> {OUT}")
conn.close()
