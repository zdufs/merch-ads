#!/usr/bin/env python3
"""
Post a rich digest embed to Discord (reads DISCORD_WEBHOOK_URL from .env).
KPIs + what the scheduled run auto-applied + top sales movers + dashboard pointer.

Run:  python3 notify_discord.py          (posts)
      python3 notify_discord.py --dry     (prints payload, posts nothing)
"""

import json
import os
import sys
import sqlite3
import urllib.request
import db
import markets

HERE = os.path.dirname(os.path.abspath(__file__))
MKT = markets.current()
SYM = {"USD": "$", "GBP": "£", "EUR": "€"}.get(markets.cfg()["currency"], "$")

def load_env(path=os.path.join(HERE, ".env")):
    env = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env
conn = db.connect()   # ensures schema (incl. daily_totals) exists
cur = conn.cursor()

snaps = [r[0] for r in cur.execute("SELECT DISTINCT date FROM campaign_perf ORDER BY date")]
latest = snaps[-1] if snaps else None
prev = snaps[-2] if len(snaps) >= 2 else None

def totals(d):
    r = cur.execute("SELECT SUM(cost),SUM(sales),SUM(clicks),SUM(orders) FROM campaign_perf WHERE date=?", (d,)).fetchone()
    return (r[0] or 0, r[1] or 0, r[2] or 0, r[3] or 0)

cost, sales, clicks, orders = totals(latest) if latest else (0, 0, 0, 0)
acos = cost / sales * 100 if sales else 0
cvr = orders / clicks * 100 if clicks else 0

# --- daily + month-to-date ad spend/ACOS (from period_totals) ---
def _acos(c, s):
    return f"{c / s * 100:.1f}%" if s else "—"

daily = db.get_period_total(conn, "daily")     # (window, cost, sales, orders) or None
mtd = db.get_period_total(conn, "mtd")
if daily:
    daily_spend_txt = f"{SYM}{(daily[1] or 0):,.2f}"
    daily_acos_txt = _acos(daily[1] or 0, daily[2] or 0)
else:
    daily_spend_txt = daily_acos_txt = "n/a"
if mtd:                                          # true month-to-date
    month_spend_txt = f"{SYM}{(mtd[1] or 0):,.0f}"
    month_acos_txt = _acos(mtd[1] or 0, mtd[2] or 0)
else:                                            # fallback: 30-day rolling snapshot
    month_spend_txt = f"{SYM}{cost:,.0f}"
    month_acos_txt = f"{acos:.1f}%"

# what the latest run auto-applied (writes_log, most recent applied date)
applied = {}
last_day = cur.execute("SELECT substr(MAX(applied_at),1,10) FROM writes_log").fetchone()[0]
if last_day:
    for a, n in cur.execute(
        "SELECT action,COUNT(*) FROM writes_log WHERE substr(applied_at,1,10)=? GROUP BY action", (last_day,)):
        applied[a] = n
neg = applied.get("add_negative", 0)
pause = applied.get("pause_ad_group", 0)
bids = applied.get("bid_change", 0)
harv = applied.get("harvest_promote", 0) + applied.get("harvest_promote_asin", 0)

# --- separate entity: scavenger (name-prefixed bucket) ---
def bucket(prefix):
    r = cur.execute(
        """SELECT COUNT(*),SUM(cost),SUM(sales),SUM(orders) FROM campaign_perf
           WHERE date=? AND campaign_name LIKE ?""", (latest, prefix + "%")).fetchone()
    n, c, s, o = (r[0] or 0), (r[1] or 0), (r[2] or 0), (r[3] or 0)
    return n, c, s, o, (c / s * 100 if s else 0)

sn, sc, ss, so, sacos = bucket("SCAVENGER - ")
scav_acts = (f"{applied.get('scav_add_kw',0)} refreshed · {applied.get('scav_prune',0)} kw pruned"
             f" · {applied.get('scav_retire',0)} retired")

# top movers vs previous snapshot
movers = []
if prev:
    p = {r[0]: (r[1] or 0) for r in cur.execute("SELECT campaign_name,sales FROM campaign_perf WHERE date=?", (prev,))}
    for name, s in cur.execute("SELECT campaign_name,sales FROM campaign_perf WHERE date=?", (latest,)):
        movers.append((round((s or 0) - p.get(name, 0), 2), name))
    movers.sort(reverse=True)
top = movers[:3]
mv_txt = "\n".join(f"• {n[:34]}  {'+' if d>=0 else ''}${d:,.0f}" for d, n in top) or "—"

color = 0x51CF66 if acos and acos < 25 else 0xFF922B
fields = [
    {"name": "💸 Daily spend", "value": daily_spend_txt, "inline": True},
    {"name": "📊 Daily ACOS", "value": daily_acos_txt, "inline": True},
    {"name": "​", "value": "​", "inline": True},
    {"name": "🗓️ Month spend", "value": month_spend_txt, "inline": True},
    {"name": "📈 Month ACOS", "value": month_acos_txt, "inline": True},
    {"name": "​", "value": "​", "inline": True},
    {"name": "🤖 Auto-applied this run",
     "value": f"{neg} negatives · {pause} pauses · {bids} bids · {harv} harvested", "inline": False},
]
fields.append({"name": "🧹 Scavenger",
               "value": f"{sn} camps · {SYM}{sc:,.0f} · {so} orders · ACOS {sacos:.0f}%\n{scav_acts}", "inline": True})
fields.append({"name": "📈 Top movers (vs prior snapshot)", "value": mv_txt, "inline": False})
embed = {
    "title": f"🦜 Merch Ads [{MKT}] — {latest}",
    "color": color,
    "fields": fields,
    "footer": {"text": f"CVR {cvr:.1f}% · market {MKT}"},
}
payload = {"embeds": [embed]}
conn.close()

if "--dry" in sys.argv:
    print(json.dumps(payload, indent=2)); sys.exit(0)

url = load_env().get("DISCORD_WEBHOOK_URL", "").strip()
if not url:
    print("No DISCORD_WEBHOOK_URL in .env — skipping."); sys.exit(0)
req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                            headers={"Content-Type": "application/json",
                                     "User-Agent": "Merch-Ads/1.0"})
try:
    urllib.request.urlopen(req, timeout=20)
    print("posted to Discord ✅")
except Exception as e:
    print(f"Discord post failed: {e}")
