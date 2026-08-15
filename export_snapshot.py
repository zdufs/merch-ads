#!/usr/bin/env python3
"""Preserve per-ASIN economics from a catalogue export before it is deleted.

The Merch catalogue export is ~2GB and the engine keeps exactly one: both
`run_scheduled.sh` and `appctl adopt-export` delete every superseded copy. That
is reasonable housekeeping for a 2GB file, but it means a design's price and
royalty at a past date vanishes the moment a newer export lands.

Those numbers are what bids and kills are priced off. Losing them means a past
decision can never be explained — "why did we pause this in June?" has no
answer once June's prices are gone.

This streams the export (never loads it) and banks one row per advertised ASIN.
Scoped to ASINs the account actually advertises, ~197k rather than the full 2M,
because those are the only ones whose price drives a decision.

Usage:
  python3 export_snapshot.py <export_products_*.csv>
  python3 export_snapshot.py --auto     # newest export in the POD folder, skip if banked
  python3 export_snapshot.py            # show what is banked
"""

import csv
import glob
import os
import re
import sys

import db

KIND = "catalog_export"
DATE_RE = re.compile(r"export_products_(\d{4}-\d{2}-\d{2})")

# The export has ~90 columns; only these price a decision.
WANTED = ("asin", "marketplace", "productType", "brandName", "status",
          "listPrice", "royaltyLast30", "salesLast30", "salesTotal")


def export_date(path):
    """The ISO day stamped into the filename — the export's as-of date."""
    match = DATE_RE.search(os.path.basename(path))
    return match.group(1) if match else None


def advertised_asins():
    """Every ASIN any market advertises, from the per-market ad_group_product."""
    import sqlite3
    here = os.path.dirname(os.path.abspath(__file__))
    found = set()
    for path in glob.glob(os.path.join(here, "ads_data*.sqlite")):
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            found |= {r[0] for r in conn.execute(
                "SELECT DISTINCT asin FROM ad_group_product WHERE asin IS NOT NULL")}
            conn.close()
        except sqlite3.Error:
            continue
    return found


def _num(value, cast=float):
    try:
        return cast(float(str(value).strip() or 0))
    except (TypeError, ValueError):
        return cast(0)


def snapshot(path, conn=None, asins=None):
    """Stream one export and bank its advertised ASINs. Idempotent per export."""
    day = export_date(path)
    if not day:
        raise ValueError(f"no ISO date in filename: {os.path.basename(path)}")
    asins = advertised_asins() if asins is None else asins
    own = conn is None
    conn = conn or db.connect_shared()

    rows, scanned, matched = [], 0, 0
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in ("asin", "listPrice") if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"not a catalogue export — missing {', '.join(missing)}")
        for raw in reader:
            scanned += 1
            asin = (raw.get("asin") or "").strip()
            if not asin or asin not in asins:
                continue
            matched += 1
            rows.append((day, asin, (raw.get("marketplace") or "").strip(),
                         (raw.get("productType") or "").strip(),
                         (raw.get("brandName") or "").strip(),
                         (raw.get("status") or "").strip(),
                         (raw.get("listPrice") or "").strip(),
                         _num(raw.get("royaltyLast30")),
                         _num(raw.get("salesLast30"), int),
                         _num(raw.get("salesTotal"), int)))
            if len(rows) >= 50_000:            # bounded memory on a 2GB file
                db.store_asin_econ_snapshot(conn, rows)
                rows = []
    if rows:
        db.store_asin_econ_snapshot(conn, rows)

    total = conn.execute(
        "SELECT COUNT(*) FROM asin_econ_snapshot WHERE export_date=?", (day,)).fetchone()[0]
    meta = {"export_date": day, "filename": os.path.basename(path),
            "scanned": scanned, "matched": matched, "banked": total}
    db.log_import(conn, KIND, os.path.basename(path), period_start=day, period_end=day,
                  rows_in_file=scanned, rows_banked=total,
                  note=f"{matched} advertised ASINs of {scanned} catalogue rows")
    if own:
        conn.close()
    return meta


def coverage(conn=None):
    own = conn is None
    conn = conn or db.connect_shared(ro=True)
    try:
        return [{"export_date": r[0], "asins": r[1], "priced": r[2]}
                for r in conn.execute(
                    """SELECT export_date, COUNT(*), SUM(list_price <> '')
                       FROM asin_econ_snapshot GROUP BY export_date ORDER BY export_date""")]
    finally:
        if own:
            conn.close()


def auto():
    """Bank the newest catalogue export unless this exact file already was.

    The nightly calls this right before it deletes superseded exports, so every
    export is banked exactly once and re-runs on an unchanged file cost one
    imported_files lookup, not a 2GB stream."""
    pod = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    exports = sorted(glob.glob(os.path.join(pod, "export_products_*.csv")))
    if not exports:
        return {"skipped": "no catalogue export in the POD folder"}
    newest = exports[-1]
    conn = db.connect_shared()
    try:
        seen = conn.execute(
            "SELECT 1 FROM imported_files WHERE kind=? AND filename=?",
            (KIND, os.path.basename(newest))).fetchone()
        if seen:
            return {"skipped": f"already banked: {os.path.basename(newest)}"}
        return snapshot(newest, conn=conn)
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        for row in coverage():
            print(row)
    elif sys.argv[1] == "--auto":
        print(auto())
    else:
        print(snapshot(sys.argv[1]))
