#!/usr/bin/env python3
"""
Derive per-product-type economics for a NON-US market from the Merch export and
store them in market_econ (read by products.get_econ for that market).

For each product type in the market: royalty-per-unit = median(royaltyTotal/salesTotal)
across listings with sales; price = median(listPrice). break_even = royalty / price.
Everything in local currency, so the stop-loss / negative thresholds are correct.

US is skipped — it uses the authoritative hardcoded table in products.py.

Run (per market):  ADS_MARKET=UK python3 derive_econ.py
"""

import csv
import glob
import os

import paths
import statistics
from collections import defaultdict

import db
import markets

csv.field_size_limit(10**9)
POD = paths.POD_ROOT


def newest_export():
    matches = sorted(glob.glob(os.path.join(POD, "export_products_*.csv")))
    if not matches:
        raise SystemExit(f"No export_products_*.csv in {POD}")
    return matches[-1]


def main():
    market = markets.current()
    if markets.is_default():
        print("US uses the hardcoded economics in products.py — nothing to derive."); return
    xm = markets.cfg(market)["export_mkt"]
    cur = markets.cfg(market)["currency"]
    path = newest_export()
    print(f"Deriving {market} economics ({cur}) from {os.path.basename(path)} (market='{xm}')")

    roy = defaultdict(list)
    price = defaultdict(list)
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        for p in csv.DictReader(fh):
            if (p.get("marketplace") != xm) or (p.get("status") != "published"):
                continue
            t = p.get("productType") or ""
            if not t:
                continue
            try:
                st = int(float(p.get("salesTotal") or 0))
                rt = float(p.get("royaltyTotal") or 0)
            except (TypeError, ValueError):
                st, rt = 0, 0.0
            if st > 0 and rt > 0:
                roy[t].append(rt / st)
            try:
                pr = float(p.get("listPrice") or 0)
            except (TypeError, ValueError):
                pr = 0.0
            if pr > 0:
                price[t].append(pr)

    tee_price = markets.TEE_PRICE.get(market)   # capped real selling price for tees
    rows = []
    for t in roy:
        r = round(statistics.median(roy[t]), 2)
        pr = round(statistics.median(price[t]), 2) if price.get(t) else 0.0
        # standard tees use the capped selling price (override the export median)
        if t == "standard_tshirt" and tee_price:
            pr = tee_price
        be = round(r / pr, 3) if pr else 0.18      # fallback break-even if no price
        rows.append((t, r, pr, be, len(roy[t])))
    rows.sort(key=lambda x: -x[4])

    conn = db.connect()
    db.store_market_econ(conn, market, rows)
    print(f"  stored {len(rows)} product types for {market}:")
    for t, r, pr, be, n in rows[:12]:
        print(f"    {t:28} royalty {cur} {r:6.2f}  price {pr:6.2f}  break-even {be*100:4.1f}%  (n={n})")


if __name__ == "__main__":
    main()
