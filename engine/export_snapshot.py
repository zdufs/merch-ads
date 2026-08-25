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
  python3 export_snapshot.py <snap-grid-export-*.csv | export_products_*.csv>
  python3 export_snapshot.py --auto     # newest export in the POD folder, skip if banked
  python3 export_snapshot.py            # show what is banked
"""

import glob
import os

import paths
import sys

import db

KIND = "catalog_export"

# The export has ~90 columns; only these price a decision.
WANTED = ("asin", "marketplace", "productType", "brandName", "status",
          "listPrice", "royaltyLast30", "salesLast30", "salesTotal")


def export_date(path):
    """The ISO day stamped into the filename — the export's as-of date.
    Handles both product-grid exports (Snap for MOD and MerchFlow)."""
    import export_reader
    return export_reader.file_date(path)


def advertised_asins():
    """Every ASIN any market advertises, from the per-market ad_group_product."""
    import sqlite3
    here = paths.REPO_ROOT
    found = set()
    for path in glob.glob(os.path.join(here, "ads_data*.sqlite")):
        try:
            conn = db.open_readonly(path)
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

    import export_reader
    rows, scanned, matched = [], 0, 0
    # export_reader raises on a file that is not a product grid, which is the
    # same guard the old fieldname check gave.
    for raw in export_reader.rows(path):
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
                     # Snap for MOD has no trailing-30 columns; those bank as 0.
                     _num(raw.get("royaltyLast30")),
                     _num(raw.get("salesLast30"), int),
                     _num(raw.get("salesTotal"), int)))
        if len(rows) >= 50_000:            # bounded memory on a multi-GB file
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
    """Bank every product export that has not been banked yet.

    The catalog arrives in CHUNKS now (Snap for MOD exports at most 100k rows a
    time), so banking only the newest file would leave most of a refresh
    unbanked. Each file is banked once — a re-run on unchanged files costs one
    imported_files lookup each, not a re-stream."""
    import export_reader
    exports = export_reader.catalog_files(paths.POD_ROOT)
    if not exports:
        return {"skipped": "no product export in the POD folder"}
    conn = db.connect_shared()
    banked, skipped = [], []
    try:
        for path in reversed(exports):             # oldest first: newest wins
            name = os.path.basename(path)
            seen = conn.execute(
                "SELECT 1 FROM imported_files WHERE kind=? AND filename=?",
                (KIND, name)).fetchone()
            if seen:
                skipped.append(name)
                continue
            banked.append(snapshot(path, conn=conn))
        if not banked:
            return {"skipped": f"already banked: {len(skipped)} file(s)"}
        return {"banked": banked, "already_banked": skipped}
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        for row in coverage():
            print(row)
    elif sys.argv[1] in ("-h", "--help"):
        print("usage: python3 export_snapshot.py [--auto | <export.csv>]\n"
              "  no args   show which exports are already banked\n"
              "  --auto    bank every export not yet banked\n"
              "  <csv>     bank one export by path")
    elif sys.argv[1] == "--auto":
        print(auto())
    elif not os.path.exists(sys.argv[1]):
        sys.exit(f"no such file: {sys.argv[1]}")
    else:
        print(snapshot(sys.argv[1]))
