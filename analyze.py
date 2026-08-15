#!/usr/bin/env python3
"""
READ-ONLY analysis of ads_data.sqlite (Phase 0 data). Makes no API calls,
changes nothing. Shows what the rules WOULD flag — a dry-run preview.

Run:  python3 analyze.py
"""

import sqlite3
import db

conn = sqlite3.connect(db.DB_PATH)
cur = conn.cursor()
# One date per table — they are filled by separate report jobs and drift.
END = db.latest_snapshot(conn, "campaign_perf")
ST_END = db.latest_snapshot(conn, "search_term_perf")
print(f"Analysis — campaigns {END} · search terms {ST_END}\n")

# --- campaigns above the 30% ACOS ceiling ---
print("Campaigns over 30% ACOS (bid-down / investigate), by spend:")
rows = cur.execute(
    """SELECT campaign_name,cost,sales,acos FROM campaign_perf
       WHERE date=? AND acos IS NOT NULL AND acos>0.30 AND cost>0
       ORDER BY cost DESC LIMIT 15""", (END,)).fetchall()
if not rows:
    print("  (none)")
for r in rows:
    print(f"    {(r[0] or '')[:40]:40}  spend ${r[1]:7.2f}  sales ${r[2]:7.2f}  ACOS {r[3]*100:3.0f}%")

# --- zero-sale search terms bucketed by spend ---
print("\nZero-sale search terms by spend (true negative candidates rise with threshold):")
for thr in (0.50, 1.00, 1.40, 2.00, 4.00):
    row = cur.execute(
        "SELECT COUNT(*),SUM(cost) FROM search_term_perf WHERE date=? AND orders=0 AND cost>=?",
        (ST_END, thr)).fetchone()
    print(f"    ≥ ${thr:>4.2f} spent, 0 sales : {row[0]:5} terms   ${row[1] or 0:8.2f} recoverable")

# --- biggest individual money-wasters ---
print("\nTop 20 single search terms wasting money (0 sales):")
rows = cur.execute(
    """SELECT s.search_term, c.name, s.cost, s.clicks
       FROM search_term_perf s LEFT JOIN campaigns c ON c.campaign_id=s.campaign_id
       WHERE s.date=? AND s.orders=0 AND s.cost>0
       ORDER BY s.cost DESC LIMIT 20""", (ST_END,)).fetchall()
for r in rows:
    print(f"    ${r[2]:6.2f}  {r[3]:3} clicks  '{(r[0] or '')[:34]:34}'  [{(r[1] or '')[:20]}]")

# --- overall recoverable estimate at a representative $2 stop-loss ---
row = cur.execute(
    "SELECT COUNT(*),SUM(cost) FROM search_term_perf WHERE date=? AND orders=0 AND cost>=2.0",
    (ST_END,)).fetchone()
print(f"\nAt a ~$2 stop-loss: {row[0]} terms to negate, ${row[1] or 0:,.2f}/mo recoverable "
      f"(vs the full wasted-spend headline — most of that is sub-$1 noise).")
conn.close()
