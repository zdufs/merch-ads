#!/usr/bin/env python3
"""
Audit: how many ad groups (= ASINs) are spending with ZERO sales, bucketed by
spend. Answers "is 43 really all of it, or is the $5 threshold hiding a tail?"
Read-only.

Run:  python3 paused_audit.py
"""

import sqlite3
import db

conn = sqlite3.connect(db.DB_PATH)
cur = conn.cursor()
# Read targeting_perf's OWN newest snapshot — campaign_perf's date belongs to a
# different report job and matches nothing here once the two drift apart.
end = db.latest_snapshot(conn, "targeting_perf")

# ad-group level totals (one ASIN per ad group)
rows = cur.execute(
    """SELECT ad_group_id, SUM(cost) c, SUM(orders) o
       FROM targeting_perf WHERE date=? GROUP BY ad_group_id""", (end,)).fetchall()

total = len(rows)
with_sales = sum(1 for r in rows if (r[2] or 0) > 0)
zero = [r for r in rows if (r[2] or 0) == 0 and (r[1] or 0) > 0]
zero_spend = sum(r[1] for r in zero)

print(f"Snapshot {end}")
print(f"Ad groups that spent anything : {total:,}")
print(f"  of those, with >=1 sale     : {with_sales:,}")
print(f"  with 0 sales (any spend)    : {len(zero):,}   total ${zero_spend:,.2f}")

print("\nZero-sale ad groups by spend bucket:")
buckets = [(5.0, 9e9), (2.0, 5.0), (1.0, 2.0), (0.5, 1.0), (0.0, 0.5)]
for lo, hi in buckets:
    sel = [r for r in zero if lo <= (r[1] or 0) < hi]
    s = sum(r[1] for r in sel)
    label = f"${lo:.2f}+" if hi > 1e8 else f"${lo:.2f}–${hi:.2f}"
    print(f"  {label:14} : {len(sel):5} ad groups   ${s:8.2f}")

print(f"\nWe paused the ≥$5 bucket (43). If the $2–5 / $1–2 buckets hold a lot of")
print(f"money, lowering the pause threshold would catch them. Numbers above decide it.")
conn.close()
