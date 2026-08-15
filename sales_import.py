#!/usr/bin/env python3
"""Bank the Merch sales report into SQLite instead of reading one file at a time.

The dated `SALES_REPORT-*.csv` is the ONLY source of organic royalty — the Ads
API reports ad-attributed sales and nothing else. Each download covers one
window, and the engine used to read whichever file was newest. That silently
hid every earlier period: two reports on disk covering 15 Apr–13 Jul and
13 Jul–4 Aug meant 5,217 rows became invisible the moment the 1,328-row file
arrived.

Amazon emits one line per colour/size variant, so several lines share an
(mkt, date, asin, product_type). They are SUMMED here, which makes re-importing
the same report a no-op rather than a doubling.

Reports do not always abut — the pair above leaves 10–12 July uncovered. Banking
per day makes that gap visible (`coverage`) instead of pretending the newest
file is the whole story.
"""

import csv
import datetime
import os

import db

KIND = "sales_report"

# Amazon's header, kept explicit so a format change fails loudly rather than
# silently banking zeros.
REQUIRED = ("Mkt", "Date", "ASIN", "Product Type", "Purchased", "Royalties")


class SalesReportFormatError(ValueError):
    """The CSV is not a Merch sales report (or Amazon changed the columns)."""


def _date(value):
    """'7/13/26' -> date(2026, 7, 13). Amazon writes M/D/YY with no padding."""
    month, day, year = (int(part) for part in value.strip().split("/"))
    return datetime.date(2000 + year if year < 100 else year, month, day)


def _num(value, cast=float):
    try:
        return cast(str(value).strip() or 0)
    except (TypeError, ValueError):
        return cast(0)


def parse(path):
    """(rows, meta) — rows summed per (mkt, date, asin, product_type).

    Raises SalesReportFormatError when the header is not a sales report, so an
    operator who drops the catalogue export here is told, not silently ignored.
    """
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
        if missing:
            raise SalesReportFormatError(
                f"not a Merch sales report — missing column(s): {', '.join(missing)}")
        agg, seen, skipped = {}, 0, 0
        for raw in reader:
            seen += 1
            try:
                day = _date(raw["Date"])
                asin = (raw["ASIN"] or "").strip()
                if not asin:
                    raise ValueError("no ASIN")
            except (KeyError, ValueError, AttributeError):
                skipped += 1
                continue
            key = ((raw["Mkt"] or "").strip(), day.isoformat(), asin,
                   (raw.get("Product Type") or "").strip())
            row = agg.get(key)
            if row is None:
                agg[key] = row = {
                    "title": (raw.get("Title") or "").strip(),
                    "currency": (raw.get("Currency") or "").strip(),
                    "purchased": 0, "cancelled": 0, "returned": 0,
                    "revenue": 0.0, "royalty": 0.0}
            row["purchased"] += _num(raw.get("Purchased"), int)
            row["cancelled"] += _num(raw.get("Cancelled"), int)
            row["returned"] += _num(raw.get("Returned"), int)
            row["revenue"] += _num(raw.get("Revenue"))
            row["royalty"] += _num(raw.get("Royalties"))

    rows = [(mkt, day, asin, v["title"], ptype, v["purchased"], v["cancelled"],
             v["returned"], round(v["revenue"], 2), round(v["royalty"], 2),
             v["currency"])
            for (mkt, day, asin, ptype), v in sorted(agg.items())]
    days = sorted({r[1] for r in rows})
    meta = {"filename": os.path.basename(path), "rows_in_file": seen,
            "rows_banked": len(rows), "skipped": skipped,
            "period_start": days[0] if days else None,
            "period_end": days[-1] if days else None,
            "markets": sorted({r[0] for r in rows}),
            "asins": len({r[2] for r in rows})}
    return rows, meta


def bank(path, conn=None):
    """Parse and accumulate one report. Returns the parse meta plus totals.

    Writes to the ACCOUNT-WIDE store (db.connect_shared) because one file covers
    every marketplace."""
    rows, meta = parse(path)
    own = conn is None
    conn = conn or db.connect_shared()
    before = conn.execute("SELECT COUNT(*) FROM sales_report_rows").fetchone()[0]
    if rows:
        db.store_sales_report_rows(conn, rows)
    after = conn.execute("SELECT COUNT(*) FROM sales_report_rows").fetchone()[0]
    meta["new_rows"] = after - before
    meta["total_rows"] = after
    db.log_import(conn, KIND, meta["filename"],
                  period_start=meta["period_start"], period_end=meta["period_end"],
                  rows_in_file=meta["rows_in_file"], rows_banked=meta["rows_banked"],
                  note=f"{meta['new_rows']} new, {meta['asins']} ASINs, "
                       f"markets {','.join(meta['markets'])}")
    if own:
        conn.close()
    return meta


def coverage(conn=None):
    """What organic history is banked, and where the holes are.

    Gaps matter: royalty summed over a window that is only half covered reads as
    a slump rather than missing data."""
    own = conn is None
    conn = conn or db.connect_shared(ro=True)
    try:
        days = [r[0] for r in conn.execute(
            "SELECT DISTINCT date FROM sales_report_rows ORDER BY date")]
        if not days:
            return {"days": 0, "first_day": None, "last_day": None,
                    "gaps": [], "rows": 0, "asins": 0}
        rows, asins = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT asin) FROM sales_report_rows").fetchone()
        first = datetime.date.fromisoformat(days[0])
        last = datetime.date.fromisoformat(days[-1])
        have = set(days)
        gaps, run = [], None
        step = first
        while step <= last:
            iso = step.isoformat()
            if iso not in have:
                run = run or iso
            elif run:
                gaps.append({"start": run, "end": (step - datetime.timedelta(days=1)).isoformat()})
                run = None
            step += datetime.timedelta(days=1)
        if run:
            gaps.append({"start": run, "end": last.isoformat()})
        return {"days": len(days), "first_day": days[0], "last_day": days[-1],
                "gaps": gaps, "rows": rows, "asins": asins}
    finally:
        if own:
            conn.close()


def banked_rows(conn=None):
    """Every banked row in the shape traz.load_sales_rows returns."""
    own = conn is None
    conn = conn or db.connect_shared(ro=True)
    try:
        return [dict(mkt=r[0], date=datetime.date.fromisoformat(r[1]), asin=r[2],
                     title=r[3] or "", ptype=r[4] or "", purchased=r[5] or 0,
                     returned=r[7] or 0, royalty=r[9] or 0.0, revenue=r[8] or 0.0)
                for r in conn.execute(
                    """SELECT mkt,date,asin,title,product_type,purchased,cancelled,
                              returned,revenue,royalty FROM sales_report_rows""")]
    except Exception:
        return []
    finally:
        if own:
            conn.close()


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print(coverage())
    else:
        print(bank(sys.argv[1]))
