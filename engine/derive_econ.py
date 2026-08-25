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
    import export_reader
    path = export_reader.newest_catalog_file(POD)
    if not path:
        raise SystemExit(f"No product export in {POD}")
    return path


def collect(xm, folder=None):
    """(royalty-per-unit samples, price samples) per product type for ONE market,
    read from the merged catalog.

    This used to open the newest file with a raw csv.DictReader and MerchFlow
    column names. A Snap for MOD chunk has none of those names, so the moment a
    Snap file became the newest export this returned NOTHING and every EU market
    silently fell back to DEFAULT_ECON."""
    import export_reader
    roy = defaultdict(list)
    price = defaultdict(list)
    for p in export_reader.catalog_rows(folder or POD, marketplace=xm):
        if p.get("status") != "published":
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
    return roy, price


def main():
    market = markets.current()
    if markets.is_default():
        print("US uses the hardcoded economics in products.py — nothing to derive."); return
    xm = markets.cfg(market)["export_mkt"]
    cur = markets.cfg(market)["currency"]
    import export_reader
    files = export_reader.catalog_files(POD)
    if not files:
        raise SystemExit(f"No product export in {POD}")
    print(f"Deriving {market} economics ({cur}) from {len(files)} catalog file(s) "
          f"(market='{xm}')")
    roy, price = collect(xm)

    tee_price = markets.TEE_PRICE.get(market)   # capped real selling price for tees
    rows, unpriced = [], []
    for t in roy:
        r = round(statistics.median(roy[t]), 2)
        pr = round(statistics.median(price[t]), 2) if price.get(t) else 0.0
        # standard tees use the capped selling price (override the export median)
        if t == "standard_tshirt" and tee_price:
            pr = tee_price
        if not pr:
            # A break-even is royalty divided by price. With no price there is
            # no break-even, and 0.18 is the US TEE number — it belongs to a
            # different product in a different market and to this row not at
            # all. Storing it made an unpriceable type look priced: get_econ
            # read it back with known=True, so bids and pauses ran against an
            # invented threshold. Skip the row instead. products.get_econ then
            # reports the type as unknown and design_be_for fails closed, which
            # is what "we could not price this" is supposed to look like.
            unpriced.append((t, r, len(roy[t])))
            continue
        be = round(r / pr, 3)
        rows.append((t, r, pr, be, len(roy[t])))
    rows.sort(key=lambda x: -x[4])
    if unpriced:
        print(f"  {len(unpriced)} product type(s) had royalties but NO usable list "
              f"price, so they were NOT stored and stay unpriced:")
        for t, r, n in sorted(unpriced, key=lambda x: -x[2]):
            print(f"    {t}: median royalty {r} over {n} listing(s), no price")

    conn = db.connect()
    db.store_market_econ(conn, market, rows)
    print(f"  stored {len(rows)} product types for {market}:")
    for t, r, pr, be, n in rows[:12]:
        print(f"    {t:28} royalty {cur} {r:6.2f}  price {pr:6.2f}  break-even {be*100:4.1f}%  (n={n})")


if __name__ == "__main__":
    main()
