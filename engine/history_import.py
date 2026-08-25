#!/usr/bin/env python3
"""Bank monthly account history exported from the Amazon Ads CONSOLE.

The Ads API stops at ~95 days ("startDate must be equal to or after report type
data retention start date"), and the v2 endpoint is retired. The console's
report builder reaches back years, so the only route to older history is an
operator-run export. Once banked here it is the only copy — the API can never
refill it.

TWO TRAPS, both learned the hard way from real exports:

1. The console's `Month` dimension is month-OF-YEAR, not year-month. A report
   spanning 2023-2024 returns twelve rows per currency with July 2023 and July
   2024 SUMMED into "month 7". There is no way to separate them afterwards, so
   this importer REFUSES a file whose year is ambiguous rather than banking a
   number that is quietly two years of spend. Add the `Year` dimension, or
   export one calendar year per file.

2. The `Country` dimension came back EMPTY on every row. The finest split
   available is Budget currency, and EUR covers DE, FR, ES and IT together.
   That is recorded as market "EU" rather than pretending it is one country.
"""

import csv
import os
import re

import db

KIND = "ads_history"

CURRENCY_MARKET = {"USD": "US", "GBP": "UK", "EUR": "EU"}

REQUIRED = ("Month", "Budget currency", "Total cost", "Sales")


class HistoryFormatError(ValueError):
    """Not a console history export, or its year cannot be established."""


def _num(value, cast=float):
    try:
        return cast(str(value).replace(",", "").strip() or 0)
    except (TypeError, ValueError):
        return cast(0)


def _year_from_name(path):
    """A single 4-digit year in the filename, e.g. History_backfill_2025.csv.

    A range like 2023-2024 deliberately does NOT match: two years in the name
    is exactly the ambiguous case this importer refuses."""
    years = re.findall(r"(?<!\d)(20\d{2})(?!\d)", os.path.basename(path))
    return int(years[0]) if len(set(years)) == 1 else None


def parse(path, year=None):
    """(rows, meta). Raises HistoryFormatError when the year is not knowable."""
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        reader = csv.DictReader(fh)
        fields = reader.fieldnames or []
        missing = [c for c in REQUIRED if c not in fields]
        if missing:
            raise HistoryFormatError(
                f"not an Ads console history export — missing: {', '.join(missing)}")
        raw = list(reader)

    if not raw:
        return [], {"filename": os.path.basename(path), "rows_in_file": 0,
                    "rows_banked": 0, "months": [], "currencies": [], "year": year}

    has_year_column = "Year" in fields
    if not has_year_column:
        year = year or _year_from_name(path)
        if not year:
            raise HistoryFormatError(
                "this export has no Year column and its filename names more than one "
                "year, so a month cannot be dated. Amazon's `Month` dimension is "
                "month-of-year: a 2023-2024 report SUMS July 2023 and July 2024 into "
                "one row. Re-export with the Year dimension added, or one calendar "
                "year per file.")

    agg = {}
    for r in raw:
        month = _num(r.get("Month"), int)
        if not 1 <= month <= 12:
            continue
        row_year = _num(r.get("Year"), int) if has_year_column else year
        if not row_year:
            continue
        cur = (r.get("Budget currency") or "").strip().upper()
        key = (f"{row_year:04d}-{month:02d}", cur)
        got = agg.setdefault(key, dict(impressions=0, clicks=0, spend=0.0,
                                       sales=0.0, purchases=0, units=0))
        got["impressions"] += _num(r.get("Impressions"), int)
        got["clicks"] += _num(r.get("Clicks"), int)
        got["spend"] += _num(r.get("Total cost"))
        got["sales"] += _num(r.get("Sales"))
        got["purchases"] += _num(r.get("Purchases"), int)
        got["units"] += _num(r.get("Units sold"), int)

    filename = os.path.basename(path)
    rows = [(month, cur, CURRENCY_MARKET.get(cur, cur), v["impressions"], v["clicks"],
             round(v["spend"], 2), round(v["sales"], 2), v["purchases"], v["units"],
             filename)
            for (month, cur), v in sorted(agg.items())]
    months = sorted({r[0] for r in rows})
    meta = {"filename": filename, "rows_in_file": len(raw), "rows_banked": len(rows),
            "months": months, "period_start": months[0] if months else None,
            "period_end": months[-1] if months else None,
            "currencies": sorted({r[1] for r in rows}),
            "year_source": "column" if has_year_column else "filename/argument"}
    return rows, meta


def bank(path, year=None, conn=None):
    rows, meta = parse(path, year=year)
    own = conn is None
    conn = conn or db.connect_shared()
    before = conn.execute("SELECT COUNT(*) FROM ads_history_monthly").fetchone()[0]
    if rows:
        db.store_history_monthly(conn, rows)
    after = conn.execute("SELECT COUNT(*) FROM ads_history_monthly").fetchone()[0]
    meta["new_rows"] = after - before
    meta["total_rows"] = after
    db.log_import(conn, KIND, meta["filename"],
                  period_start=meta.get("period_start"), period_end=meta.get("period_end"),
                  rows_in_file=meta["rows_in_file"], rows_banked=meta["rows_banked"],
                  note=f"{meta['new_rows']} new, currencies "
                       f"{','.join(meta['currencies'])}, year from {meta.get('year_source')}")
    if own:
        conn.close()
    return meta


def coverage(conn=None):
    own = conn is None
    conn = conn or db.connect_shared(ro=True)
    try:
        months = [r[0] for r in conn.execute(
            "SELECT DISTINCT month FROM ads_history_monthly ORDER BY month")]
        if not months:
            return {"months": 0, "first_month": None, "last_month": None, "by_market": []}
        # Each bucket carries its OWN range, because the account-wide one says
        # nothing about it. The three currency series are different lengths —
        # US 60 months, UK 44, EU 41 — so a screen that read the top-level
        # `months` and called it "continuous" claimed 60 continuous months for
        # a market whose table was empty (found on DE, 2026-08-24).
        by_market = [{"market": r[0], "currency": r[1], "months": r[2],
                      "spend": round(r[3] or 0, 2), "sales": round(r[4] or 0, 2),
                      "purchases": r[5] or 0, "first_month": r[6], "last_month": r[7]}
                     for r in conn.execute(
                         """SELECT market, currency, COUNT(*), SUM(spend), SUM(sales),
                                   SUM(purchases), MIN(month), MAX(month)
                            FROM ads_history_monthly GROUP BY market, currency
                            ORDER BY market""")]
        return {"months": len(months), "first_month": months[0], "last_month": months[-1],
                "by_market": by_market}
    finally:
        if own:
            conn.close()


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print(coverage())
    elif sys.argv[1] in ("-h", "--help"):
        print("usage: python3 history_import.py [<console-history.csv> [year]]\n"
              "  no args   show what monthly history is already banked\n"
              "  <csv>     bank a monthly history export from the Ads console")
    elif not os.path.exists(sys.argv[1]):
        sys.exit(f"no such file: {sys.argv[1]}")
    else:
        try:
            y = int(sys.argv[2]) if len(sys.argv) > 2 else None
        except ValueError:
            sys.exit(f"year must be a number, not {sys.argv[2]!r}")
        print(bank(sys.argv[1], year=y))
