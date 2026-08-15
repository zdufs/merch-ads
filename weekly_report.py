#!/usr/bin/env python3
"""
Weekly performance summary from accumulated snapshots. Read-only.
Each pull stores a 30-day-trailing snapshot dated by its window end, so over
time campaign_perf holds a trend. This shows total spend/sales/ACOS/CVR per
snapshot, plus the biggest movers. Writes weekly_summary.md.

Run:  python3 weekly_report.py
"""

import os
import sqlite3
import db

OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs"); os.makedirs(OUTDIR, exist_ok=True)
OUT = os.path.join(OUTDIR, "weekly_summary.md")
conn = sqlite3.connect(db.DB_PATH)
cur = conn.cursor()

# snapshot-level totals (each date = a 30-day trailing window)
snaps = cur.execute(
    """SELECT date, ROUND(SUM(cost),2), ROUND(SUM(sales),2),
              SUM(clicks), SUM(orders)
       FROM campaign_perf GROUP BY date ORDER BY date""").fetchall()

def money(x): return f"${x:,.2f}"
L = ["# Ads Weekly Summary\n", "> Read-only. Each row is a 30-day trailing snapshot from a scheduled pull.\n"]
L.append("## Account trend")
L.append("| Snapshot | Spend | Sales | ACOS | Orders | CVR |")
L.append("|---|---|---|---|---|---|")
for d, cost, sales, clicks, orders in snaps:
    acos = f"{cost/sales*100:.1f}%" if sales else "—"
    cvr = f"{orders/clicks*100:.1f}%" if clicks else "—"
    L.append(f"| {d} | {money(cost or 0)} | {money(sales or 0)} | {acos} | {orders or 0} | {cvr} |")

# movers: latest vs previous snapshot
if len(snaps) >= 2:
    latest, prev = snaps[-1][0], snaps[-2][0]
    cur_map = {r[0]: (r[1], r[2]) for r in cur.execute(
        "SELECT campaign_name,cost,sales FROM campaign_perf WHERE date=?", (latest,))}
    prev_map = {r[0]: (r[1], r[2]) for r in cur.execute(
        "SELECT campaign_name,cost,sales FROM campaign_perf WHERE date=?", (prev,))}
    movers = []
    for name, (c, s) in cur_map.items():
        ps = (prev_map.get(name) or (0, 0))[1] or 0
        movers.append((round((s or 0) - ps, 2), name, ps, s or 0))
    movers.sort(reverse=True)
    L.append(f"\n## Biggest sales movers ({prev} → {latest})")
    L.append("| Δ Sales | Campaign | Was | Now |")
    L.append("|---|---|---|---|")
    for delta, name, was, now in movers[:8]:
        L.append(f"| {money(delta)} | {name} | {money(was)} | {money(now)} |")
    for delta, name, was, now in movers[-4:]:
        if delta < 0:
            L.append(f"| {money(delta)} | {name} | {money(was)} | {money(now)} |")
else:
    L.append("\n_(Only one snapshot so far — the trend builds as the scheduled job runs.)_")

with open(OUT, "w") as f:
    f.write("\n".join(L) + "\n")
print(f"Weekly summary written: {OUT}  ({len(snaps)} snapshots)")
conn.close()
