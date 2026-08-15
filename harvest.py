#!/usr/bin/env python3
"""
Harvest logger — finds converting search terms in your auto campaigns and logs
them as candidates to promote into manual exact-match campaigns. Read-only on
Amazon; accumulates winners in harvest_log so the list grows each cycle.

Harvest rule: search term with >= MIN_ORDERS orders AND ACOS <= that product's
target (i.e. proven + profitable). ASIN search terms are tagged separately
(they promote as product targets, not keywords).

Run after phase0_pull.py + map_products.py:
  python3 harvest.py
"""

import csv
import os
import re
import db
import markets
import products

MIN_ORDERS = 2
ASIN_RE = re.compile(r"^b0[0-9a-z]{8}$", re.I)
OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs"); os.makedirs(OUTDIR, exist_ok=True)
OUT = os.path.join(OUTDIR, "harvest_candidates.csv")

conn = db.connect()
cur = conn.cursor()
# Winners are read from search_term_perf, so anchor to ITS newest snapshot.
# campaign_perf is filled by a different report job and its date matches
# nothing here once the search-term job has been failing.
end = db.latest_snapshot(conn, "search_term_perf")
pm = db.get_product_map(conn)
dmap = db.get_design_map(conn)


def _promotion_target(agid):
    """Per-design target ACOS a converting search term must beat to be promoted.

    KDP books: the book's OWN break-even (via kdp_econ). Without this branch a
    book falls through to products.get_econ, whose PRODUCT_ECON tables key off
    tee product types and hand any book the 18% tee default — far too strict for
    a book that breaks even near 70%, so almost nothing would ever promote.
    Econ not loaded yet ⇒ target 0 (fail closed, promotes nothing) until the
    operator enters the royalty; harvest only runs well after launch anyway.

    Merch: per-design target where the tee price is known, else the cohort floor.
    """
    d = dmap.get(str(agid))
    if markets.is_kdp():
        asin = (d or {}).get("asin")
        if asin:
            import kdp_econ
            e = kdp_econ.book_econ(asin)
            if e:
                return e["break_even"]
        return 0.0
    if d and d.get("asin"):
        return products.get_design_econ(d.get("product_type") or pm.get(str(agid)),
                                        price=d.get("list_price"))["target_acos"]
    return products.get_econ(pm.get(str(agid)))["target_acos"]


rows = cur.execute(
    """SELECT search_term, campaign_id, ad_group_id, clicks, orders, cost, sales, acos
       FROM search_term_perf WHERE date=? AND orders>=? AND acos IS NOT NULL""",
    (end, MIN_ORDERS)).fetchall()

cand = []
for st, cid, agid, clk, orders, cost, sales, acos in rows:
    if not st:
        continue
    tgt = _promotion_target(agid)
    if acos <= tgt:
        kind = "asin_target" if ASIN_RE.match(st.strip()) else "keyword"
        cpc = round(cost / clk, 2) if clk else 0
        cand.append(dict(term=st, agid=str(agid), cid=cid, kind=kind,
                         pt=pm.get(str(agid), ""), clicks=clk, orders=orders,
                         sales=round(sales, 2), acos=round(acos, 4), cpc=cpc))

db.upsert_harvest(conn, cand)

# ---- CSV: de-duplicate by term (sum orders/sales, keep best/lowest ACOS context) ----
agg = {}
for r in cand:
    a = agg.get(r["term"])
    if not a:
        agg[r["term"]] = dict(r, n_ctx=1)
    else:
        a["orders"] += r["orders"]; a["sales"] += r["sales"]; a["n_ctx"] += 1
        if r["acos"] < a["acos"]:
            a["acos"], a["cpc"], a["pt"], a["cid"], a["agid"] = r["acos"], r["cpc"], r["pt"], r["cid"], r["agid"]

ranked = sorted(agg.values(), key=lambda x: -x["sales"])
# only candidates not yet promoted
promoted = {r[0] for r in cur.execute("SELECT DISTINCT search_term FROM harvest_log WHERE promoted=1")}
ranked = [r for r in ranked if r["term"] not in promoted]

with open(OUT, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["search_term", "kind", "product_type", "orders", "sales",
                "best_acos", "suggested_exact_bid", "source_campaign_id"])
    for r in ranked:
        # suggested starting exact bid: proven CPC + ~15% headroom (it converts)
        bid = round(max(0.10, (r["cpc"] or 0.20) * 1.15), 2)
        w.writerow([r["term"], r["kind"], r["pt"], r["orders"], f"{r['sales']:.2f}",
                    f"{r['acos']*100:.0f}%", f"{bid:.2f}", r["cid"]])

kw = sum(1 for r in ranked if r["kind"] == "keyword")
asn = sum(1 for r in ranked if r["kind"] == "asin_target")
print(f"Harvest run (read-only). Snapshot {end}.")
print(f"  candidates this run: {len(cand)}  | distinct terms not yet promoted: {len(ranked)}")
print(f"  keywords: {kw}  | asin-targets: {asn}")
print(f"  total sales represented: ${sum(r['sales'] for r in ranked):,.2f}")
print(f"  -> {OUT}")
print("  (harvest_log keeps the full running history across cycles.)")
conn.close()
