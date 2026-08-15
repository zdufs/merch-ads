#!/usr/bin/env python3
"""
TAMAS candidate finder — read-only. US-only (TAMAS is US-only).

TAMAS pays off by AMPLIFYING designs that already have organic pull (the halo the
ad-attributed view can't see). So the best candidates are PROVEN ORGANIC SELLERS that
are NOT yet on TAMAS and get little/no current ad support — a low-fixed-bid broad
keyword campaign has the most room to compound the flywheel there.

Source of truth: the dated Merch SALES_REPORT (total royalty, all channels), cross-
referenced with the ads DB for current ad spend so we can tell organic from ad-driven.

Eligibility (defaults; tune via flags):
  - product type = Standard t-shirt (the method tests at low tee prices)   [--all to include others]
  - net units >= 8, sold on >= 6 distinct days, spanning >= 21 days, net royalty > 0
    (consistent multi-week organic demand, not a one-off spike)
  - NOT already a TAMAS design
Tiers:
  prime    = current ad spend < --max-prime-spend  (under-advertised → most upside)
  flagship = already advertised but strong organic  (amplify / matching-cohort plays)
Both sorted by organic $/day (royalty / span).

Flags surfaced per design:
  sensitive  = weed/cannabis or suicide/mental-health themes — Amazon restricts keyword
               ads on these; the broad keyword may be suppressed/disapproved. Start elsewhere.
Note: keyword + price choice stay MANUAL per the method. Proven sellers keep their proven
price — do NOT drop them to the $13.99 test price (that rule is for unproven designs).

Usage:  python3 tamas_candidates.py [--all] [--min-units N] [--min-sale-days N]
                                     [--min-span N] [--max-prime-spend X] [--json]
Run under ADS_MARKET=US (default); other markets are skipped (no TAMAS there).
"""

import argparse
import csv
import json
import os
import re
import sys

import db
import markets
import tamas
import traz

ASIN_RE = re.compile(r"B0[A-Z0-9]{8}")
OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
OUT = os.path.join(OUTDIR, "tamas_candidates.csv")

SENSITIVE = re.compile(
    r"\b(weed|stoner|stoned|joint|bong|cannabis|marijuana|420|kush|"
    r"suicide|988|mental\s*health|self[\s-]*harm)\b", re.I)


def tamas_asins(conn):
    """ASINs already running under a TAMAS campaign (exclude from candidates)."""
    asins = set()
    for cid, name in conn.execute("SELECT campaign_id, name FROM campaigns"):
        if not (tamas.is_tamas(name) or "tamas" in (name or "").lower()):
            continue
        row = conn.execute(
            "SELECT p.asin FROM ad_groups g JOIN ad_group_product p ON p.ad_group_id = g.ad_group_id "
            "WHERE g.campaign_id = ? LIMIT 1", (str(cid),)).fetchone()
        a = row[0] if row and row[0] else (ASIN_RE.search(name or "") or [None])
        if isinstance(a, str):
            asins.add(a)
        else:
            m = ASIN_RE.search(name or "")
            if m:
                asins.add(m.group(0))
    return asins


def ad_stats(conn):
    """asin -> (ad_spend, ad_orders) over the latest CUMULATIVE targeting_perf snapshot."""
    end = conn.execute("SELECT MAX(date) FROM targeting_perf").fetchone()[0]
    agp = {r[0]: r[1] for r in conn.execute("SELECT ad_group_id, asin FROM ad_group_product")}
    spend, orders = {}, {}
    for ag, cost, o in conn.execute(
            "SELECT ad_group_id, SUM(cost), SUM(orders) FROM targeting_perf WHERE date=? GROUP BY ad_group_id",
            (end,)):
        a = agp.get(str(ag))
        if not a:
            continue
        spend[a] = spend.get(a, 0.0) + (cost or 0)
        orders[a] = orders.get(a, 0) + (o or 0)
    return spend, orders, set(agp.values())


def analyze(min_units=8, min_sale_days=6, min_span=21, max_prime_spend=8.0, tees_only=True):
    if not markets.is_default():
        return None  # TAMAS is US-only
    conn = db.connect(ro=True)
    sales = [r for r in traz.load_sales_rows() if r["mkt"] == ".com"]
    if not sales:
        return {"error": "no SALES_REPORT-*.csv found in the POD folder"}
    report_start = min(r["date"] for r in sales).isoformat()
    report_end = max(r["date"] for r in sales).isoformat()

    agg = {}
    for r in sales:
        g = agg.setdefault(r["asin"], dict(u=0, ret=0, roy=0.0, days=set(),
                                           first=r["date"], last=r["date"], title="", pt=""))
        g["u"] += r["purchased"]; g["ret"] += r["returned"]; g["roy"] += r["royalty"]
        if r["purchased"] > 0:
            g["days"].add(r["date"])
        g["first"] = min(g["first"], r["date"]); g["last"] = max(g["last"], r["date"])
        g["title"] = r.get("title") or g["title"]; g["pt"] = r["ptype"] or g["pt"]

    exclude = tamas_asins(conn)
    spend, orders, in_ads = ad_stats(conn)

    prime, flagship = [], []
    for a, g in agg.items():
        if a in exclude:
            continue
        if tees_only and g["pt"] != "Standard t-shirt":
            continue
        net = g["u"] - g["ret"]
        span = (g["last"] - g["first"]).days + 1
        sdays = len(g["days"])
        if net < min_units or sdays < min_sale_days or span < min_span or g["roy"] <= 0:
            continue
        sp = round(spend.get(a, 0.0), 2)
        flags = []
        if SENSITIVE.search(g["title"] or ""):
            flags.append("sensitive")
        rec = dict(asin=a, title=(g["title"] or "")[:60], product_type=g["pt"],
                   net_units=net, royalty=round(g["roy"], 2), sale_days=sdays, span_days=span,
                   organic_per_day=round(g["roy"] / span, 2),
                   ad_spend=sp, ad_orders=orders.get(a, 0), in_ads=a in in_ads,
                   flags="; ".join(flags))
        (prime if sp < max_prime_spend else flagship).append(rec)

    prime.sort(key=lambda x: x["organic_per_day"], reverse=True)
    flagship.sort(key=lambda x: x["organic_per_day"], reverse=True)
    return dict(market="US", report_start=report_start, report_end=report_end,
                thresholds=dict(min_units=min_units, min_sale_days=min_sale_days,
                                min_span=min_span, max_prime_spend=max_prime_spend,
                                tees_only=tees_only),
                prime_count=len(prime), flagship_count=len(flagship),
                prime=prime, flagship=flagship)


def _cli_args():
    p = argparse.ArgumentParser()
    p.add_argument("--all", action="store_true", help="include non-tee product types")
    p.add_argument("--min-units", type=int, default=8)
    p.add_argument("--min-sale-days", type=int, default=6)
    p.add_argument("--min-span", type=int, default=21)
    p.add_argument("--max-prime-spend", type=float, default=8.0)
    p.add_argument("--json", action="store_true")
    return p.parse_args()


def main():
    a = _cli_args()
    res = analyze(a.min_units, a.min_sale_days, a.min_span, a.max_prime_spend, not a.all)
    if res is None:
        print(f"TAMAS is US-only — skipping for market {markets.current()}."); return
    if "error" in res:
        print("ERROR:", res["error"]); return
    if a.json:
        print(json.dumps(res, indent=2)); return

    print(f"TAMAS candidates — US · SALES_REPORT {res['report_start']} → {res['report_end']}")
    print(f"  filter: {'tee-only' if res['thresholds']['tees_only'] else 'all types'}, "
          f"net>={a.min_units}u, >={a.min_sale_days} sale-days, >={a.min_span}d span")

    def show(title, rows, n):
        print(f"\n{title}  ({len(rows)})")
        print(f"  {'ASIN':11} {'net':>3} {'roy$':>7} {'$/day':>5} {'sdays':>5} {'adspd':>6} {'flags':10} design")
        for x in rows[:n]:
            print(f"  {x['asin']:11} {x['net_units']:3d} {x['royalty']:7.0f} "
                  f"{x['organic_per_day']:5.2f} {x['sale_days']:5d} {x['ad_spend']:6.2f} "
                  f"{x['flags'][:10]:10} {x['title'][:44]}")

    show("PRIME — proven organic, under-advertised (most upside)", res["prime"], 20)
    show("FLAGSHIP — strong organic, already advertised (amplify)", res["flagship"], 8)

    os.makedirs(OUTDIR, exist_ok=True)
    cols = ["asin", "title", "product_type", "net_units", "royalty", "sale_days", "span_days",
            "organic_per_day", "ad_spend", "ad_orders", "in_ads", "flags"]
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["tier"] + cols); w.writeheader()
        for tier, rows in (("prime", res["prime"]), ("flagship", res["flagship"])):
            for x in rows:
                w.writerow(dict(tier=tier, **{k: x[k] for k in cols}))
    print(f"\n  -> {OUT}")
    print("  NOTE: keep proven sellers at their proven price (do NOT drop to $13.99); "
          "'sensitive' = weed/suicide themes Amazon may restrict for keyword ads.")


if __name__ == "__main__":
    main()
