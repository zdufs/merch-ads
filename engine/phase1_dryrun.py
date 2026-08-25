#!/usr/bin/env python3
"""
PHASE 1 — DRY RUN. Read-only. Applies the locked bidding rules to the Phase 0
data and writes a proposal report. MAKES NO CHANGES to Amazon. Writes:
  - dryrun_report.md   (human review)
  - proposed_negatives.csv (the negative-keyword candidates)

PER-PRODUCT-TYPE rules (needs map_products.py to have run):
  - Negative a search term: 0 sales AND spend >= that product's neg threshold (royalty*0.5)
  - Pause an ad group: 0 sales AND spend >= that product's pause threshold (tee $5, else royalty*0.5)
  - Campaign over its target ACOS -> bid down; under it (converting) -> room to scale
    (standard tee target = 30% Model A; everything else = its break-even, Model B)

Run:  python3 phase1_dryrun.py
"""

import csv
import os

import paths
import sqlite3
from collections import Counter, defaultdict
import db
import products

FLOOR = 1.00   # fetch at/above lowest plausible threshold, filter per type in Python

HERE = paths.REPO_ROOT
OUTDIR = os.path.join(HERE, "outputs"); os.makedirs(OUTDIR, exist_ok=True)
REPORT = os.path.join(OUTDIR, "dryrun_report.md")
NEG_CSV = os.path.join(OUTDIR, "proposed_negatives.csv")

conn = db.connect()   # ensures all tables exist (incl. ad_group_product)
cur = conn.cursor()
# One date per perf table. Each is filled by its own Amazon report job, so a
# single shared END silently matched zero rows in the tables whose job had
# failed — the dry-run then reported "0 negatives, 0 pauses" instead of "no data".
END = db.latest_snapshot(conn, "campaign_perf")          # campaign rows only
ST_END = db.latest_snapshot(conn, "search_term_perf")    # negative candidates
TGT_END = db.latest_snapshot(conn, "targeting_perf")     # ad-group pause candidates
for _label, _date in (("campaign_perf", END), ("search_term_perf", ST_END),
                      ("targeting_perf", TGT_END)):
    _age = db.snapshot_age_days(_date)
    if _date is None or (_age is not None and _age > db.SNAPSHOT_STALE_AFTER_DAYS):
        print(f"  ! {_label} snapshot is {_date or 'missing'}"
              + (f" ({_age}d old)" if _age is not None else "")
              + " — the sections it feeds are stale")

pmap = db.get_product_map(conn)                    # ad_group_id -> product_type
lmap = db.get_lifetime_map(conn)                   # ad_group_id -> lifetime units (guardrail)
cv = defaultdict(Counter)                          # campaign -> dominant product type
for cid, pt in cur.execute(
    """SELECT a.campaign_id, p.product_type FROM ad_groups a
       JOIN ad_group_product p ON a.ad_group_id=p.ad_group_id"""):
    cv[cid][pt] += 1
camp_type = {cid: c.most_common(1)[0][0] for cid, c in cv.items() if c}

def econ_for_ag(agid): return products.get_econ(pmap.get(str(agid)))
def target_for_camp(cid): return products.get_econ(camp_type.get(cid))["target_acos"]

# 1) NEGATIVE candidates — per-type threshold
raw_negs = cur.execute(
    """SELECT s.search_term, s.campaign_id, c.name, s.ad_group_id, s.cost, s.clicks
       FROM search_term_perf s LEFT JOIN campaigns c ON c.campaign_id=s.campaign_id
       WHERE s.date=? AND s.orders=0 AND s.cost>=? ORDER BY s.cost DESC""",
    (ST_END, FLOOR)).fetchall()
negs = [r for r in raw_negs if r[4] >= econ_for_ag(r[3])["neg_threshold"]]

# cross-campaign repeat offenders (account-level negatives)
repeats = cur.execute(
    """SELECT search_term, COUNT(DISTINCT campaign_id) nc, SUM(cost) tot
       FROM search_term_perf WHERE date=? AND orders=0
       GROUP BY search_term HAVING nc>=2 AND tot>=? ORDER BY tot DESC LIMIT 25""",
    (ST_END, FLOOR)).fetchall()

# 2) AD-GROUP pause candidates — per-type threshold
raw_p = cur.execute(
    """SELECT t.ad_group_id, t.campaign_id, c.name, SUM(t.cost) spend, SUM(t.orders) orders
       FROM targeting_perf t LEFT JOIN campaigns c ON c.campaign_id=t.campaign_id
       WHERE t.date=? GROUP BY t.ad_group_id HAVING orders=0 AND spend>=? ORDER BY spend DESC""",
    (TGT_END, FLOOR)).fetchall()
pauses = [r for r in raw_p
          if r[3] >= products.pause_threshold(pmap.get(str(r[0])), lmap.get(str(r[0]), 0))]
# proven winners shielded this run (would pause at base threshold, protected by lifetime guardrail)
protected = sum(1 for r in raw_p
                if products.is_proven(lmap.get(str(r[0]), 0))
                and r[3] >= econ_for_ag(r[0])["pause_threshold"]
                and r[3] < products.pause_threshold(pmap.get(str(r[0])), lmap.get(str(r[0]), 0)))

# 3) CAMPAIGN bid actions — per-type target ACOS
camp_rows = cur.execute(
    """SELECT campaign_id, campaign_name, cost, sales, acos,
              (orders*1.0/NULLIF(clicks,0)) cvr FROM campaign_perf
       WHERE date=? AND cost>0""", (END,)).fetchall()
overspend, scaleup = [], []
for cid, name, cost, sales, acos, cvr in camp_rows:
    tgt = target_for_camp(cid)
    if acos is not None and acos > tgt:
        overspend.append((name, cost, sales, acos, cvr, tgt, camp_type.get(cid, "?")))
    elif acos is not None and 0 < acos < tgt and (cvr or 0) > 0:
        scaleup.append((name, cost, sales, acos, cvr, tgt, camp_type.get(cid, "?")))
overspend.sort(key=lambda r: -r[1])
scaleup.sort(key=lambda r: -(r[4] or 0))
scaleup = scaleup[:15]

neg_spend = sum(r[4] for r in negs)
pause_spend = sum(r[3] for r in pauses)

# ---- CSV of negatives ----
with open(NEG_CSV, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["search_term", "campaign_id", "campaign_name", "ad_group_id",
                "product_type", "spend", "clicks", "proposed_action"])
    for r in negs:
        w.writerow([r[0], r[1], r[2], r[3], pmap.get(str(r[3]), ""), f"{r[4]:.2f}", r[5], "negative_exact"])

# ---- markdown report ----
def money(x): return f"${x:,.2f}"

L = []
L.append(f"# Ads Dry-Run Proposal — {END}\n")
L.append(f"> Snapshots read: campaigns {END} · search terms {ST_END} · targeting {TGT_END}\n")
L.append("> Read-only. NOTHING changed on Amazon. Per-product-type rules applied.\n")
L.append("## Summary")
L.append(f"- Negative-keyword candidates: **{len(negs)}** terms, **{money(neg_spend)}/mo**")
L.append(f"- Ad groups to pause (0 sales, over per-type threshold): **{len(pauses)}**, **{money(pause_spend)}/mo**")
L.append(f"- Proven winners shielded from pause (lifetime guardrail): **{protected}**")
L.append(f"- Campaigns over their target ACOS (bid down): **{len(overspend)}**")
L.append(f"- Campaigns under target with headroom (scale up): **{len(scaleup)}**")
L.append(f"- Total immediate recoverable: **{money(neg_spend + pause_spend)}/mo**\n")

L.append("## 1. Add as negative exact (top 30)")
L.append("| Spend | Clicks | Search term | Campaign |")
L.append("|---|---|---|---|")
for r in negs[:30]:
    L.append(f"| {money(r[4])} | {r[5]} | {r[0]} | {r[2] or ''} |")
L.append(f"\n_Full list ({len(negs)}) in proposed_negatives.csv._\n")

L.append("## 2. Cross-campaign repeat offenders (account-level negatives)")
L.append("| Total spend | # campaigns | Search term |")
L.append("|---|---|---|")
for r in repeats:
    L.append(f"| {money(r[2])} | {r[1]} | {r[0]} |")

L.append("\n## 3. Ad groups to pause (0 sales, over per-type threshold)")
L.append("| Spend | Campaign | Product type | Ad group id |")
L.append("|---|---|---|---|")
for r in pauses[:30]:
    L.append(f"| {money(r[3])} | {r[2] or ''} | {pmap.get(str(r[0]),'?')} | {r[0]} |")

L.append("\n## 4. Campaigns over their target ACOS — bid down")
L.append("| Campaign | Type | Spend | Sales | ACOS | Target | CVR |")
L.append("|---|---|---|---|---|---|---|")
for name, cost, sales, acos, cvr, tgt, pt in overspend:
    cvrs = f"{cvr*100:.1f}%" if cvr is not None else "—"
    L.append(f"| {name} | {pt} | {money(cost)} | {money(sales)} | {acos*100:.0f}% | {tgt*100:.0f}% | {cvrs} |")

L.append("\n## 5. Headroom to scale (under target, ranked by CVR — bid up)")
L.append("| Campaign | Type | Spend | Sales | ACOS | Target | CVR |")
L.append("|---|---|---|---|---|---|---|")
for name, cost, sales, acos, cvr, tgt, pt in scaleup:
    cvrs = f"{cvr*100:.1f}%" if cvr is not None else "—"
    L.append(f"| {name} | {pt} | {money(cost)} | {money(sales)} | {acos*100:.0f}% | {tgt*100:.0f}% | {cvrs} |")

with open(REPORT, "w", encoding="utf-8") as f:
    f.write("\n".join(L) + "\n")

print("Dry-run complete (read-only, per-type).")
print(f"  negatives: {len(negs)} (${neg_spend:.2f})  |  pauses: {len(pauses)} (${pause_spend:.2f})  |  proven-protected: {protected}")
print(f"  over-target: {len(overspend)}  |  scale-up: {len(scaleup)}")
if not pmap:
    print("  ⚠️ no product map found — run map_products.py first for per-type rules.")
print(f"  report:  {REPORT}")
conn.close()
