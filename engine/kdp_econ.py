#!/usr/bin/env python3
"""KDP (Kindle Direct Publishing) book economics — the KDP analogue of the Merch
tee royalty tables. Computes per-book royalty and break-even ACOS so profit /
break-even / the DSL economics fields work for advertised books.

Royalty (Amazon's published formula, verified 2026 — confirm exact values in your
KDP dashboard, sources in docs):
  paperback/hardcover:  royalty = rate * list_price - printing_cost
    rate (US):          50% if list_price < $9.99, else 60%   (since 2025-06)
    printing (US b&w paperback): $2.30 flat for <=110 pages,
                                 else $1.00 + $0.012 * pages   (110-828 pages)
  ebook (Kindle):       royalty = rate * list_price - delivery
    rate:               70% if $2.99 <= list_price <= $9.99, else 35%
    delivery (70% tier): ~$0.06 * file_size_mb   (0 on the 35% tier)
break-even ACOS = royalty / list_price  (same meaning as the tee break-even).

Per-book INPUTS are NOT in the Ads API — they come from your catalog. Provide
them in kdp_books.json (via `appctl kdp-book`): each ASIN carries either the
inputs (format/list_price/page_count/ink) to COMPUTE, or a direct `royalty` read
straight off your KDP dashboard (most accurate). A book with no data resolves to
None — economics FAIL CLOSED (never guessed), exactly like the Merch econ gate."""

import json
import os

import paths

HERE = paths.REPO_ROOT
CONFIG = os.path.join(HERE, "kdp_books.json")

# US black-ink paperback printing constants (verifiable in the KDP dashboard).
_US_BW_FIXED = 1.00
_US_BW_PER_PAGE = 0.012
_US_BW_FLAT_UNDER = 2.30
_US_BW_FLAT_MAX_PAGES = 110
_EBOOK_DELIVERY_PER_MB = 0.06


def load_books():
    try:
        with open(CONFIG) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def save_book(asin, data):
    books = load_books()
    books[asin.upper()] = {k: v for k, v in data.items() if v is not None}
    with open(CONFIG, "w") as f:
        json.dump(books, f, indent=2, sort_keys=True)
        f.write("\n")
    return books[asin.upper()]


def clear_book(asin):
    books = load_books()
    books.pop(asin.upper(), None)
    with open(CONFIG, "w") as f:
        json.dump(books, f, indent=2, sort_keys=True)
        f.write("\n")


def paperback_royalty_rate(list_price):
    """US print royalty tier (since 2025-06): 50% under $9.99, 60% at/above."""
    return 0.50 if list_price < 9.99 else 0.60


def printing_cost(book):
    """US black-ink paperback printing cost, or None when the inputs aren't a
    supported case (color, hardcover, non-US, missing pages) — fail closed."""
    fmt = (book.get("format") or "paperback").lower()
    ink = (book.get("ink") or "bw").lower()
    market = (book.get("marketplace") or "US").upper()
    pages = book.get("page_count")
    if fmt not in ("paperback",) or ink not in ("bw", "black", "black_white") or market != "US":
        return None
    if not pages:
        return None
    if pages <= _US_BW_FLAT_MAX_PAGES:
        return _US_BW_FLAT_UNDER
    return round(_US_BW_FIXED + _US_BW_PER_PAGE * pages, 2)


def ebook_royalty(list_price, file_size_mb=1.0):
    if 2.99 <= list_price <= 9.99:
        return round(0.70 * list_price - _EBOOK_DELIVERY_PER_MB * (file_size_mb or 0), 2)
    return round(0.35 * list_price, 2)


def book_econ(asin):
    """{royalty, break_even, list_price, known} for a book, or None when its data
    is absent/unsupported (fail closed — economics-driven writes then skip it)."""
    b = load_books().get((asin or "").upper())
    if not b:
        return None
    lp = b.get("list_price")
    if lp is None:
        return None
    fmt = (b.get("format") or "paperback").lower()

    if b.get("royalty") is not None:            # direct KDP-dashboard value (most accurate)
        roy = round(float(b["royalty"]), 2)
    elif fmt == "ebook":
        roy = ebook_royalty(lp, b.get("file_size_mb", 1.0))
    else:
        pc = printing_cost(b)
        if pc is None:
            return None                          # unsupported print case — no guessing
        roy = round(paperback_royalty_rate(lp) * lp - pc, 2)

    be = round(roy / lp, 4) if lp else None
    return {"royalty": roy, "break_even": be, "list_price": lp, "known": True}
