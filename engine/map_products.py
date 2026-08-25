#!/usr/bin/env python3
"""
Map each active ad group -> its product type (so the rule engine can apply
per-type economics). Joins ad-group ASINs (Ads API) to the Merch export CSV.
Read-only on Amazon; writes the mapping into ads_data.sqlite.

Run after phase0_pull.py (and re-run when your catalog/export changes):
  python3 map_products.py
"""

import csv
import datetime
import os

import paths
import sqlite3
import sys

import db
import markets
from ads_client import AdsClient
import products
from products import get_econ

csv.field_size_limit(10**9)
HERE = paths.REPO_ROOT
# The catalogue folder is paths.POD_ROOT, never dirname(REPO_ROOT).
# The two agree only while MERCHADS_POD_DIR is unset. Set it, and half the
# engine reads one catalogue while products.export_signature() — which does use
# POD_ROOT — banks the signature of another, so the economics gate certifies a
# catalogue that was never mapped.
POD = paths.POD_ROOT
SP_AD = "application/vnd.spProductAd.v3+json"

import export_reader

CATALOG = export_reader.catalog_files(POD)
if not CATALOG:
    raise SystemExit(f"No product export in {POD} "
                     f"(snap-grid-export-*.csv or export_products_*.csv)")
PRODUCT_CSV = CATALOG[0]                       # newest, for the freshness stamp

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

print(f"Looking up product types in the catalog ({len(CATALOG)} file(s))…")
EXPORT_MKT = markets.cfg()["export_mkt"]
meta = {}
royalty30 = {}      # asin -> period royalty-per-unit (for cmd_profit's cache)
price_age = {}      # asin -> the export date the price came from
not_live = {}       # asin -> export status, for ADVERTISED designs not published
# The catalog can be several files. catalog_rows serves the newest row for each
# listing, so a chunk exported today overrides the same ASIN in an older file.
_skipped_chunks = []
for prod in export_reader.catalog_rows(POD, marketplace=EXPORT_MKT,
                                       skipped=_skipped_chunks):
    a = (prod.get("asin") or "").upper()
    if a not in asins:
        continue
    # Only a listing that can still be BOUGHT is authoritative (PLAN.md §3,
    # widened 2026-08-22). `published` is not the whole set: a timed-out or
    # locked listing stays up and keeps selling, and refusing to price it
    # exempts it from every economics rule instead of protecting it. See
    # products.PURCHASABLE_STATUSES for the evidence.
    if prod.get("status") not in products.PURCHASABLE_STATUSES:
        # A MerchFlow "all products" export includes REMOVED listings, so a
        # design being here at all does not mean it is for sale. Record the
        # status rather than dropping it silently: a removed design has no live
        # price, cannot get one from any future export, and must not be counted
        # among the designs a fresh export would fix. Reported through
        # catalog_coverage -> econ-gate, where the app decides what to say.
        not_live[a] = prod.get("status") or "unknown"
        continue
    try:
        lifetime = int(float(prod.get("salesTotal") or 0))
    except (TypeError, ValueError):
        lifetime = 0
    cents = products.parse_price_cents(prod.get("listPrice"))
    price_s = f"{cents/100:.2f}" if cents else ""
    meta[a] = (prod.get("productType", ""), prod.get("brandName", ""), price_s, lifetime)
    price_age[a] = prod.get("_as_of") or ""
    # Snap for MOD has no trailing-30 columns, so these are empty on a Snap row
    # and the rate is filled from the sales report below instead.
    try:
        s30 = float(prod.get("salesLast30") or 0)
        r30 = float(prod.get("royaltyLast30") or 0)
        if s30 > 0:
            royalty30[a] = round(r30 / s30, 4)
    except (TypeError, ValueError):
        pass

# Fallback royalty-per-unit, from the dated Merch SALES_REPORT. The export used
# to carry royaltyLast30/salesLast30 and Snap for MOD does not, so without this
# the profit screen would drop every design to its modeled royalty.
if len(royalty30) < len(meta):
    try:
        import traz
        rate = traz.royalty_per_unit(30, mkt=".com" if markets.is_default() else None)
        filled = 0
        for a, r in rate.items():
            if a in meta and a not in royalty30:
                royalty30[a] = r
                filled += 1
        if filled:
            print(f"  royalty-per-unit from the sales report: {filled:,} designs")
    except Exception as exc:                      # never let this break the map
        print(f"  sales-report royalty fallback skipped: {exc}")

# One row per ad group: the design's product data, or a NULL sentinel for a
# multi-ASIN cohort so no per-design economics are ever claimed for it.
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
with open(os.path.join(HERE, "outputs", f"period_royalty{mkt_sfx}.json"), "w", encoding="utf-8") as fh:
    json.dump({"export_signature": products.export_signature(),
               "royalty_per_unit": royalty30}, fh)

# How old are the prices this map is built on? A chunked catalog has no single
# as-of date, so a design priced from a file exported months ago sits next to
# one priced today, and nothing on screen would say so. econ-gate reads this.
stale_cut = (datetime.date.today()
             - datetime.timedelta(days=products.MAX_EXPORT_AGE_DAYS)).isoformat()
dated = [d for d in price_age.values() if d]
stale_prices = sum(1 for d in dated if d < stale_cut)
with open(os.path.join(HERE, "outputs", f"catalog_coverage{mkt_sfx}.json"), "w", encoding="utf-8") as fh:
    json.dump({"market": markets.current(),
               "files": [os.path.basename(p) for p in CATALOG],
               "newest": export_reader.file_date(PRODUCT_CSV),
               "designs_mapped": len(meta),
               "designs_wanted": len(asins),
               "priced_from_dated_file": len(dated),
               "prices_older_than_gate": stale_prices,
               "oldest_price_date": min(dated) if dated else None,
               # Advertised designs the catalogue says are NOT for sale. A
               # MerchFlow "all products" export carries removed listings, so
               # this is how the engine tells "no price yet" apart from "no
               # price ever again".
               "not_live": not_live}, fh)
if stale_prices:
    print(f"  prices older than {products.MAX_EXPORT_AGE_DAYS}d: {stale_prices:,} designs"
          f" (export a fresh catalog chunk to refresh them)")

# freshness stamps — the econ_gate reads these (bootstrap: adoption stamp is
# seeded here too, covering exports that predate deployment and never passed
# _adopt_export)
# A mapping built from a catalogue we only PARTLY read is not a successful
# mapping. An unreadable chunk is skipped with a notice on stderr and the run
# carried on, then stamped map_success_at and a signature covering every file on
# disk — including the one that was skipped. So the economics gate matched, the
# gate passed, and the listings in that chunk had silently lost their prices.
# Set the STALE marker instead: that is the existing channel for "the last
# adoption did not complete", and it closes the gate until someone looks.
if _skipped_chunks:
    names = ", ".join(c["file"] for c in _skipped_chunks)
    print(f"\n!! NOT stamping a successful mapping: {len(_skipped_chunks)} catalogue "
          f"chunk(s) could not be read ({names}). Their listings are missing from "
          f"this mapping, so the economics gate stays CLOSED until the file is "
          f"fixed or removed and this is re-run.", file=sys.stderr)
    db.meta_set(conn, "econ_stale", db._now())
    conn.commit()
    conn.close()
    sys.exit(1)

db.meta_set(conn, "map_success_at", db._now())
db.meta_set(conn, "export_signature", products.export_signature())
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
