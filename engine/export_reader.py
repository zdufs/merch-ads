#!/usr/bin/env python3
"""One reader for every product-grid export the new-design intake understands.

Two browser extensions can export the Merch product grid:

  * MerchFlow / MerchPirate — `export_products_*.csv`, camelCase columns.
    This is also the ECONOMICS source: map_products.py, export_snapshot.py and
    halo.py read `listPrice` and the royalty columns out of it. Nothing here
    changes that.
  * Snap for MOD — `snap-grid-export-*.csv`, Title Case columns. This is what
    the operator exports today for new designs (Products tab, select the new
    products, the three-dots menu, Export selected data, Export full data, CSV).

Every caller keeps reading MerchFlow-shaped rows. Snap rows are translated into
that shape here, so the intake preview, the lottery builder and the scavenger
builder all learned the second format in one place.

Only the fields those three callers use are translated: marketplace, status,
asin, productTitle, productType, adAsins, salesTotal, createdDate, listPrice,
designId and brandName.

An unrecognised Snap product type comes back with an EMPTY productType, and the
raw label is kept in `productTypeLabel` so the caller can show the operator what
it skipped. The label is never guessed into a type. A near-miss on, say,
"Performance T-Shirt" would file the design as a standard tee, and standard tees
are the lottery money path.
"""

import csv
import datetime
import glob
import os
import re
import sys

csv.field_size_limit(10**9)

MERCHFLOW = "merchflow"
SNAP = "snap"

# Filename patterns, and the date each one stamps into its name.
# Snap: snap-grid-export-2026-08-15_23-53-46.csv
# MerchFlow: export_products_2026-08-04T16_30_41.366Z.csv
CATALOG_PATTERNS = (
    ("snap-grid-export-*.csv", re.compile(r"snap-grid-export-(\d{4}-\d{2}-\d{2})")),
    ("export_products_*.csv", re.compile(r"export_products_(\d{4}-\d{2}-\d{2})")),
)

# Snap prints the marketplace the way a seller says it. The rest of the engine
# compares against the MerchFlow codes (markets.cfg()["export_mkt"]).
SNAP_MARKETPLACE = {"US": "us", "UK": "gb", "GB": "gb", "DE": "de",
                    "FR": "fr", "ES": "es", "IT": "it", "JP": "jp"}

# Snap timestamps look like "Aug 12, 2026, 1:11 PM".
_SNAP_DATE_FORMATS = ("%b %d, %Y, %I:%M %p", "%b %d, %Y", "%B %d, %Y, %I:%M %p",
                      "%B %d, %Y", "%d %b %Y, %I:%M %p", "%d %b %Y", "%Y-%m-%d")


class UnknownExportFormat(ValueError):
    """The CSV header matches neither supported export."""


def _header(path):
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        return next(csv.reader(fh), [])


def detect(path):
    """MERCHFLOW, SNAP, or raise UnknownExportFormat. Decided from the header,
    not the filename, so a renamed file still parses.

    "Product Type" alone is NOT enough to call a file a Snap export: the dated
    Merch SALES_REPORT carries a "Product Type" and an "ASIN" column too, and
    claiming that file would send the operator's royalty history into the
    campaign builder. A Snap column no other file has must be present."""
    cols = {(c or "").strip().lower().lstrip("﻿") for c in _header(path)}
    if "producttype" in cols and "marketplace" in cols:
        return MERCHFLOW
    if "product type" in cols and cols & {"marketplace", "ad-safe asin", "design id"}:
        return SNAP
    raise UnknownExportFormat(
        "not a Merch product export — expected the MerchFlow columns "
        "(productType, marketplace) or the Snap for MOD columns "
        "(Product Type plus Marketplace / Ad-safe ASIN / Design ID)")


def is_export(path):
    """True when this file is a product-grid export we can read."""
    try:
        detect(path)
        return True
    except (UnknownExportFormat, OSError, StopIteration):
        return False


def rows(path):
    """Yield MerchFlow-shaped row dicts, whichever export this file is."""
    kind = detect(path)
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        for row in csv.DictReader(fh):
            yield row if kind == MERCHFLOW else _snap_row(row)


# --- the catalog: one logical product grid built from several files -----------
# Snap for MOD exports at most 100k rows per file, and the account has ~1.3M
# live listings, so the catalog arrives in CHUNKS. Every export in the POD
# folder is part of it. The newest file wins for any listing that appears twice,
# which is what makes an incremental refresh work: export the part that changed,
# drop it in, and those rows take over.


def file_date(path):
    """The ISO day stamped into an export's filename, or None."""
    name = os.path.basename(path)
    for _pattern, date_re in CATALOG_PATTERNS:
        m = date_re.search(name)
        if m:
            return m.group(1)
    return None


# The TIME inside an export's filename, not just its day. Both exporters stamp
# one: Snap writes `snap-grid-export-2026-08-20_12-00-21.csv` and MerchFlow
# writes `export_products_2026-08-22T12_33_25.034Z.csv`.
_FILE_TIME_RES = (
    re.compile(r"snap-grid-export-\d{4}-\d{2}-\d{2}_(\d{2})-(\d{2})-(\d{2})"),
    re.compile(r"export_products_\d{4}-\d{2}-\d{2}T(\d{2})_(\d{2})_(\d{2})"),
)


def file_stamp(path):
    """('YYYY-MM-DD', 'HH:MM:SS') for an export, either part '' when unknown.

    catalog_files sorted on the DAY and broke ties on the basename, so two
    chunks exported the same day were ordered alphabetically — and
    'snap-grid-export…' always sorts above 'export_products…'. A Snap file
    written at 09:00 therefore beat a MerchFlow file written at 18:00 the same
    day, and since the newest file wins per listing, that silently reinstated
    the older row's price, status and sales. The time is right there in both
    filenames; nothing was reading it.
    """
    name = os.path.basename(path)
    day = file_date(path) or ""
    for rx in _FILE_TIME_RES:
        m = rx.search(name)
        if m:
            return day, ":".join(m.groups())
    return day, ""


def pod_root():
    import paths
    return paths.POD_ROOT


def catalog_files(folder=None):
    """Every product-grid export in the folder, NEWEST FIRST.

    Sorted by the DATE and then the TIME in the filename, so two chunks exported
    the same day are ordered by when they were actually exported and not by
    which exporter's name happens to sort higher. The basename is the last
    tiebreak, so the order stays stable. A file with no date sorts oldest — it
    can still supply coverage, but it never overrides a dated file."""
    folder = folder or pod_root()
    found = []
    for pattern, _date_re in CATALOG_PATTERNS:
        for path in glob.glob(os.path.join(folder, pattern)):
            day, tod = file_stamp(path)
            found.append((day, tod, os.path.basename(path), path))
    found.sort(reverse=True)
    return [path for _day, _tod, _name, path in found]


def newest_catalog_file(folder=None):
    """The freshest export in the folder, or None. This is the file the
    economics freshness gate reads its date from."""
    files = catalog_files(folder)
    return files[0] if files else None


def catalog_signature(folder=None):
    """A signature over the WHOLE catalog, so adding one chunk changes it.
    Used to tell whether the banked mapping still matches what is on disk."""
    parts = []
    for path in sorted(catalog_files(folder)):
        try:
            parts.append(f"{os.path.basename(path)}|{int(os.path.getmtime(path))}")
        except OSError:
            continue
    return ";".join(parts) or None


def catalog_rows(folder=None, marketplace=None, files=None, skipped=None):
    """Yield the merged catalog: every export in the folder, newest first, with
    each (marketplace, asin) emitted ONCE from the newest file that carries it.

    Each row gains `_source` (the filename it came from) and `_as_of` (that
    file's date), because a chunked catalog has no single as-of date and a
    caller banking a price should be able to say how old that price is.

    `marketplace` filters to one export market code ("us", "gb", …) while
    reading. Pass it when you only need one market: it keeps the de-duplication
    set to that market's listings instead of the whole 1.3M-row account.

    The merge is banked by `catalog_cache`, and served from there when the table
    still matches the files on disk. That is a PURE OPTIMISATION — a cache that
    is stale, missing or corrupt returns None and this falls through to the
    CSVs, so it can only ever change how long a read takes. An explicit `files`
    list always reads those files: it is a scoped request, and answering it from
    a cache of the whole catalogue would hand back listings the caller excluded.

    `skipped` is an optional list the reader appends every unreadable chunk to.
    A chunk that cannot be parsed is dropped with a notice on stderr, and a
    caller that BANKS what it read — map_products — must not then record a
    successful mapping over a catalogue it only partly saw. Nobody could tell:
    the signature covers the file names on disk, including the one that was
    skipped, so the economics gate matched and passed.
    """
    if files is None:
        import catalog_cache
        cached = catalog_cache.read(folder, marketplace)
        if cached is not None:
            yield from cached
            return
    yield from _catalog_rows_csv(folder, marketplace, files, skipped)


def _catalog_rows_csv(folder=None, marketplace=None, files=None, skipped=None):
    """The merge, read straight from the CSV files. Always correct, never fast."""
    seen = set()
    for path in (files if files is not None else catalog_files(folder)):
        as_of = file_date(path)
        source = os.path.basename(path)
        # `rows()` is a generator, so nothing in it runs until it is iterated —
        # the format check and the open() both happen on the first `next()`.
        # Guarding only the call therefore guarded nothing, and an unreadable or
        # unrecognised export took down the whole catalogue read instead of
        # being skipped. Skipping is right, but it must not be SILENT: a chunk
        # that quietly drops out is a chunk whose listings vanish from every
        # economics rule, which is the 2026-08-22 Drinkware failure again.
        try:
            for row in rows(path):
                mkt = row.get("marketplace") or ""
                if marketplace and mkt != marketplace:
                    continue
                asin = (row.get("asin") or "").upper()
                if not asin:
                    continue
                key = f"{mkt}|{asin}"
                if key in seen:
                    continue
                seen.add(key)
                row["_source"] = source
                row["_as_of"] = as_of
                yield row
        except (UnknownExportFormat, OSError) as exc:
            print(f"  !! catalogue chunk skipped: {source} ({exc}) — its "
                  f"listings are missing from this read", file=sys.stderr)
            if skipped is not None:
                skipped.append({"file": source, "reason": str(exc)})
            continue


def _clean(value):
    # Snap writes a narrow no-break space before AM/PM; strptime chokes on it.
    return (value or "").replace(" ", " ").replace(" ", " ").strip()


def snap_date(value):
    """Snap's "Aug 12, 2026, 1:11 PM" -> "2026-08-12". Empty when unreadable —
    the intake's recency window then leaves the design out, which is the safe
    direction. A parsed ISO date is what every caller compares against."""
    raw = _clean(value)
    if not raw:
        return ""
    for fmt in _SNAP_DATE_FORMATS:
        try:
            return datetime.datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    m = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    return m.group(1) if m else ""


def snap_price(value):
    """"$19.99" / "€12,99" -> "19.99". Empty when there is no number."""
    raw = _clean(value)
    digits = re.sub(r"[^0-9.,]", "", raw)
    if not digits:
        return ""
    if "," in digits and "." not in digits:
        digits = digits.replace(",", ".")
    else:
        digits = digits.replace(",", "")
    return digits


def snap_number(value):
    """Sales counts arrive as "0" or "1,234"."""
    digits = re.sub(r"[^0-9-]", "", _clean(value))
    return digits or "0"


# Snap's Status column in the vocabulary the rest of the engine uses. The
# mapping is explicit rather than a blanket underscore-swap so a Snap status
# nobody has mapped stays visibly unmapped instead of inventing a name that
# happens to collide with a real one.
SNAP_STATUS = {
    "live": "published",
    "timed out": "timed_out",
    "under review": "review",
    "removed": "removed",
    "locked": "locked",
    "processing": "processing",
    "rejected": "rejected",
}


def _snap_status(raw):
    v = (raw or "").strip().lower()
    return SNAP_STATUS.get(v, v)


def _snap_row(p):
    import products

    def g(key):
        return _clean(p.get(key))

    label = g("Product Type")
    market = g("Marketplace").upper()
    return {
        "marketplace": SNAP_MARKETPLACE.get(market, market.lower()),
        # Snap says "Live" where MerchFlow says "published". It also exports
        # Removed, Locked, Timed Out, Processing, Rejected and Under Review
        # listings, and none of those may pass as published.
        #
        # The rest of the engine speaks MerchFlow's vocabulary, which is
        # snake_case: products.PURCHASABLE_STATUSES holds 'timed_out', and Snap
        # writes 'Timed Out'. Lowercasing alone left a SPACE, so those listings
        # matched nothing and were treated as not for sale. A listing that is not
        # purchasable gets no list price, and no list price means no break-even,
        # which EXEMPTS the design from every economics rule — not paused, not
        # flagged, just quietly beyond judgement. The newest Snap export carries
        # 306 of them. It is masked today only because a newer MerchFlow export
        # still wins the merge for those ASINs.
        "status": _snap_status(g("Status")),
        "asin": g("ASIN").upper(),
        "productTitle": g("Product Title"),
        "productType": products.type_from_export_label(label) or "",
        "productTypeLabel": label,
        "adAsins": g("Ad-safe ASIN").upper(),
        # Snap's Sales and Royalties are ALL-TIME totals. Verified 2026-08-15
        # against the 2026-08-04 MerchFlow export: 99.6% of 82,963 shared
        # listings matched salesTotal and royaltyTotal exactly, and the rest had
        # sold again in the eleven days between the two files.
        "salesTotal": snap_number(g("Sales")),
        "royaltyTotal": snap_price(g("Royalties")) or "0",
        "returnedTotal": snap_number(g("Returns")),
        "firstSaleDate": snap_date(g("First Sold")),
        "lastSaleDate": snap_date(g("Last Sold")),
        # Snap's grid has no trailing-30 columns, so these stay EMPTY rather
        # than borrowing the all-time figure. A caller that needs a 30-day
        # royalty rate must fall back (see map_products) instead of reading a
        # lifetime number as if it were a month.
        "salesLast30": "",
        "royaltyLast30": "",
        "createdDate": snap_date(g("Created Date")),
        "listPrice": snap_price(g("Price")),
        "designId": g("Design ID"),
        "brandName": g("Brand"),
    }


if __name__ == "__main__":
    import sys
    import collections

    # A foreseeable state is REPORTED, never leaked as a traceback. Reading
    # argv[1] blind meant no arguments raised IndexError and `--help` raised
    # FileNotFoundError — a stack trace is the worst possible answer to
    # somebody asking how a tool is used.
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        sys.exit("usage: python3 export_reader.py <product-export.csv>\n"
                 "Prints the detected format and a row/type/marketplace count.")
    path = sys.argv[1]
    if not os.path.exists(path):
        sys.exit(f"no such file: {path}")
    print(f"format: {detect(path)}")
    types = collections.Counter()
    markets_seen = collections.Counter()
    n = 0
    for r in rows(path):
        n += 1
        types[r.get("productType") or f"?{r.get('productTypeLabel')}"] += 1
        markets_seen[r.get("marketplace")] += 1
    print(f"rows: {n:,}")
    print("markets:", dict(markets_seen))
    for t, c in types.most_common():
        print(f"  {t:34} {c:,}")
