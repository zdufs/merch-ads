#!/usr/bin/env python3
"""
Map each active ad group -> its product type (so the rule engine can apply
per-type economics). Joins ad-group ASINs (Ads API) to the Merch export CSV.
Read-only on Amazon; writes the mapping into ads_data.sqlite.

Run after phase0_pull.py (and re-run when your catalog/export changes):
  python3 map_products.py
"""

import csv
import glob
import os

import paths
import sqlite3
import db
import markets
from ads_client import AdsClient
import products
from products import get_econ

csv.field_size_limit(10**9)
HERE = paths.REPO_ROOT
POD = os.path.dirname(HERE)
SP_AD = "application/vnd.spProductAd.v3+json"

matches = sorted(glob.glob(os.path.join(POD, "export_products_*.csv")))
if not matches:
    raise SystemExit(f"No export_products_*.csv in {POD}")
PRODUCT_CSV = matches[-1]

conn = db.connect()   # ensures all tables exist (incl. ad_group_product)
cur = conn.cursor()
# Each perf table has its OWN newest snapshot — the report jobs behind them fail
# independently. Sharing one date across tables silently matches zero rows.
camp_end = db.latest_snapshot(conn, "campaign_perf")
tgt_end = db.latest_snapshot(conn, "targeting_perf")
client = AdsClient()

# campaigns the rule engine touches (had spend / data this snapshot)
camp_ids = sorted({r[0] for r in cur.execute(
    "SELECT DISTINCT campaign_id FROM targeting_perf WHERE date=?", (tgt_end,)).fetchall()}
    | {r[0] for r in cur.execute(
    "SELECT DISTINCT campaign_id FROM campaign_perf WHERE date=? AND cost>0", (camp_end,)).fetchall()})
print(f"Active campaigns to map: {len(camp_ids)}")

print("Fetching product ads (ASIN per ad group)…")
ads = client.list_all("/sp/productAds/list", SP_AD, "productAds",
                      extra_body={"campaignIdFilter": {"include": [str(c) for c in camp_ids]}})
# Cardinality matters (PLAN.md §2): scavenger ad groups hold MANY ASINs — those
# are cohorts, not designs, and get a NULL sentinel so no per-design economics
# ever apply. Archived product ads don't count (an enabled+archived pair is
# still a single-ASIN design).
from collections import defaultdict
ag_asins = defaultdict(set)
for a in ads:
    if a.get("asin") and (a.get("state") or "").upper() != "ARCHIVED":
        ag_asins[str(a.get("adGroupId"))].add(a["asin"])
ag_asin = {agid: next(iter(s)) for agid, s in ag_asins.items() if len(s) == 1}
multi = {agid for agid, s in ag_asins.items() if len(s) > 1}
asins = set(ag_asin.values())
print(f"  single-ASIN ad groups: {len(ag_asin):,}  | multi-ASIN cohorts: {len(multi):,}"
      f"  | distinct ASINs: {len(asins):,}")

print("Looking up product types in Merch export…")
EXPORT_MKT = markets.cfg()["export_mkt"]
meta = {}
royalty30 = {}      # asin -> period royalty-per-unit (for cmd_profit's cache)
dups = 0
with open(PRODUCT_CSV, newline="", encoding="utf-8", errors="replace") as fh:
    for prod in csv.DictReader(fh):
        a = prod.get("asin")
        if a not in asins:
            continue
        # only this market's PUBLISHED listing is authoritative (PLAN.md §3)
        if prod.get("marketplace") != EXPORT_MKT or prod.get("status") != "published":
            continue
        if a in meta:
            dups += 1
            continue                                  # deterministic: first published wins
        try:
            lifetime = int(float(prod.get("salesTotal") or 0))
        except (TypeError, ValueError):
            lifetime = 0
        cents = products.parse_price_cents(prod.get("listPrice"))
        price_s = f"{cents/100:.2f}" if cents else ""
        meta[a] = (prod.get("productType", ""), prod.get("brandName", ""), price_s, lifetime)
        try:
            s30 = float(prod.get("salesLast30") or 0)
            r30 = float(prod.get("royaltyLast30") or 0)
            if s30 > 0:
                royalty30[a] = round(r30 / s30, 4)
        except (TypeError, ValueError):
            pass
if dups:
    print(f"  duplicate export rows skipped: {dups:,}")

rows = []
for agid, asin in ag_asin.items():
    pt, brand, price, lifetime = meta.get(asin, ("", "", "", 0))
    rows.append([agid, asin, pt, brand, price, lifetime])
for agid in multi:                                    # NULL sentinel: cohort, not a design
    rows.append([agid, None, "", "", None, 0])

# Fallback: ad groups whose ASIN wasn't in the export get typed from the
# campaign name (keyword -> type, else the campaign's dominant mapped type).
from collections import Counter, defaultdict
camp_name = dict(cur.execute("SELECT campaign_id, name FROM campaigns").fetchall())
ag_camp = dict(cur.execute("SELECT ad_group_id, campaign_id FROM ad_groups").fetchall())
dom = defaultdict(Counter)
for r in rows:
    if r[2]:
        dom[ag_camp.get(r[0])][r[2]] += 1
camp_dom = {cid: c.most_common(1)[0][0] for cid, c in dom.items()}
filled = 0
for r in rows:
    if not r[2]:
        t = (products.infer_type_from_campaign(camp_name.get(ag_camp.get(r[0]), ""))
             or camp_dom.get(ag_camp.get(r[0])))
        if t:
            r[2] = t; filled += 1
print(f"  campaign-name fallback filled: {filled:,} ad groups")

# --- price-change tracking + freshness stamps (PLAN.md §4/§8) ------------------
# Diff stored vs export price BEFORE upserting; log every change. On the very
# first post-deploy map, seed transition-unknown (old_cents=NULL) for every
# pre-existing single-ASIN tee without a detected change — pre-deploy history
# can't prove a price "held", so those designs get 30 days of leniency.
existing = {str(r[0]): (r[1], r[2], r[3]) for r in conn.execute(
    "SELECT ad_group_id, asin, list_price, product_type FROM ad_group_product")}
deployed = db.meta_get(conn, "econ_deployed_at")
changes, seeds = [], []
for agid, asin in ag_asin.items():
    pt, _brand, price_s, _life = meta.get(asin, ("", "", "", 0))
    new_c = products.parse_price_cents(price_s)
    old_row = existing.get(agid)
    if old_row and old_row[0] == asin:
        old_c = products.parse_price_cents(old_row[1])
        if new_c != old_c:
            changes.append((asin, agid, old_c, new_c))
        elif not deployed and pt == products.TEE:
            seeds.append((asin, agid, None, new_c))   # NULL-old = transition-unknown
    elif not deployed and pt == products.TEE:
        seeds.append((asin, agid, None, new_c))
if changes:
    db.log_price_changes(conn, changes)
    print(f"  price changes logged: {len(changes):,}")
if seeds:
    db.log_price_changes(conn, seeds)
    print(f"  deployment seed (transition-unknown 30d): {len(seeds):,} tees")

db.upsert_ad_group_products(conn, [tuple(r) for r in rows])

# period royalty-per-unit cache for cmd_profit (keyed by export signature)
import json
os.makedirs(os.path.join(HERE, "outputs"), exist_ok=True)
mkt_sfx = "" if markets.is_default() else f"_{markets.current()}"
with open(os.path.join(HERE, "outputs", f"period_royalty{mkt_sfx}.json"), "w") as fh:
    json.dump({"export_signature": products.export_signature(PRODUCT_CSV),
               "royalty_per_unit": royalty30}, fh)

# freshness stamps — the econ_gate reads these (bootstrap: adoption stamp is
# seeded here too, covering exports that predate deployment and never passed
# _adopt_export)
db.meta_set(conn, "map_success_at", db._now())
db.meta_set(conn, "export_signature", products.export_signature(PRODUCT_CSV))
if not db.meta_get(conn, "export_adopted_at"):
    db.meta_set(conn, "export_adopted_at", db._now())
if not deployed:
    db.meta_set(conn, "econ_deployed_at", db._now())
conn.execute("DELETE FROM engine_meta WHERE key='econ_stale'")
conn.commit()

# summary by type
from collections import Counter
c = Counter(r[2] or "UNMAPPED" for r in rows)
print(f"\nMapped {len(rows):,} ad groups. By product type:")
for t, n in c.most_common():
    e = get_econ(t if t != "UNMAPPED" else None)
    flag = "" if e["known"] else "  <- using DEFAULT economics"
    print(f"  {t:32} {n:6}   target {e['target_acos']*100:.1f}% model {e['model']}{flag}")
conn.close()
print("\nDone. phase1/2/3 will now use per-type rules.")
