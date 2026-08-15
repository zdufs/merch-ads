#!/usr/bin/env python3
"""
TRAZ + EPC — royalty-aware profitability per design. Read-only.
  TRAZ = total royalty (ad + ORGANIC, from the Merch export) − ad spend (ads data)
  EPC  = TRAZ ÷ ad clicks
Per ASIN, over the last-30-day window. This is the metric a royalty-first strategy judges
on instead of ACOS, because it captures the organic halo our ACOS view misses.

Writes outputs/traz_report.csv and prints the headline. Also exposes
load_asin_royalty() for callers wanting the plain per-ASIN map.

Run:  python3 traz.py
"""

import csv
import datetime
import glob
import os

import paths
import re
import sqlite3
import db

csv.field_size_limit(10**9)
HERE = paths.REPO_ROOT
POD = os.path.dirname(HERE)
OUTDIR = os.path.join(HERE, "outputs"); os.makedirs(OUTDIR, exist_ok=True)
OUT = os.path.join(OUTDIR, "traz_report.csv")


def load_asin_royalty(field="royaltyLast30"):
    """asin -> royalty over the window (organic + ad), from the latest Merch export."""
    csvs = sorted(glob.glob(os.path.join(POD, "export_products_*.csv")))
    if not csvs:
        return {}
    out = {}
    with open(csvs[-1], newline="", encoding="utf-8", errors="replace") as fh:
        for p in csv.DictReader(fh):
            a = p.get("asin")
            if not a:
                continue
            try:
                out[a] = float(p.get(field) or 0)
            except (TypeError, ValueError):
                out[a] = 0.0
    return out


# --- dated Merch SALES_REPORT loader (richer than the static royaltyLast30 field) ---
# The export's royaltyLast30 is a single per-ASIN scalar; the SALES_REPORT-<range>.csv
# is per-order with a date + market, so total royalty can be summed over any window and
# split by market. Used by the halo estimator; other callers still use the
# export field by default (load_asin_royalty), so this is purely additive.

def _sr_date(s):
    """'4/20/26' -> date(2026, 4, 20). Two-digit years are 2000-based."""
    mo, d, y = s.strip().split("/")
    y = int(y)
    return datetime.date(2000 + y if y < 100 else y, int(mo), int(d))


SALES_REPORT_GLOB = "SALES_REPORT-*.csv"
_SR_NAME = re.compile(r"SALES_REPORT-(\d{1,2}_\d{1,2}_\d{2,4})-(\d{1,2}_\d{1,2}_\d{2,4})\.csv$",
                      re.IGNORECASE)


def sales_report_range(path):
    """(start, end) read out of a SALES_REPORT filename, or None if it has none.

    Amazon names these SALES_REPORT-<M_D_YY>-<M_D_YY>.csv, e.g.
    SALES_REPORT-4_15_26-7_13_26.csv."""
    match = _SR_NAME.search(os.path.basename(path))
    if not match:
        return None
    try:
        return tuple(_sr_date(part.replace("_", "/")) for part in match.groups())
    except (ValueError, TypeError):
        return None


def sales_report_path():
    """The newest dated Merch SALES_REPORT in the POD folder.

    The dates in the filename are M_D_YY with no zero padding, so sorting the
    names as text puts '1_5_27' before '4_15_26' and would silently keep an
    older report on hand. Rank on the parsed end date instead. A file whose
    name carries no range falls back to its modification time, and always
    loses to one that does."""
    best = None
    for path in glob.glob(os.path.join(POD, SALES_REPORT_GLOB)):
        span = sales_report_range(path)
        if span:
            key = (1, span[1], span[0])          # end date first, then start
        else:
            mtime = datetime.date.fromtimestamp(os.path.getmtime(path))
            key = (0, mtime, datetime.date.min)
        if best is None or key > best[0]:
            best = (key, path)
    return best[1] if best else None


def load_sales_rows(path=None):
    """Organic sales rows — the BANKED union of every imported report.

    Reading one file meant the newest download hid every earlier period: with
    15 Apr–13 Jul and 13 Jul–4 Aug both on disk, importing the second dropped
    the engine from 5,217 rows to 1,328 and nothing said so. Rows are now
    accumulated per day (sales_import), so halo and TRAZ both
    see the whole history through this one function.

    An explicit `path` still reads that file directly — callers that want to
    inspect a specific report, rather than the history, keep working. Falls back
    to the newest file when nothing is banked yet, so a fresh install behaves as
    before.
    """
    if path is None:
        try:
            import sales_import
            banked = sales_import.banked_rows()
            if banked:
                return banked
        except Exception:
            pass                       # never let the bank break the file path
    path = path or sales_report_path()
    if not path:
        return []
    rows = []
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        for r in csv.DictReader(fh):
            try:
                rows.append(dict(
                    mkt=r["Mkt"].strip(), date=_sr_date(r["Date"]), asin=r["ASIN"].strip(),
                    title=(r.get("Title") or "").strip(),
                    ptype=(r.get("Product Type") or "").strip(),
                    purchased=int(r["Purchased"] or 0), returned=int(r["Returned"] or 0),
                    royalty=float(r["Royalties"] or 0), revenue=float(r["Revenue"] or 0)))
            except (KeyError, ValueError):
                continue
    return rows


def load_asin_royalty_windowed(start=None, end=None, mkt=".com", rows=None):
    """asin -> NET royalty summed from the dated SALES_REPORT within [start, end].
    Returns/refunds appear as negative royalty rows, so the sum is already net.
    start/end are datetime.date or None (unbounded). mkt=None sums all markets."""
    out = {}
    for r in (rows if rows is not None else load_sales_rows()):
        if mkt and r["mkt"] != mkt:
            continue
        if start and r["date"] < start:
            continue
        if end and r["date"] > end:
            continue
        out[r["asin"]] = out.get(r["asin"], 0.0) + r["royalty"]
    return out


def asin_ad_stats(conn, end):
    """asin -> (ad_spend, clicks, orders) over the snapshot, from ads data."""
    agp = {r[0]: r[1] for r in conn.execute("SELECT ad_group_id, asin FROM ad_group_product")}
    stats = {}
    for ag, cost, clicks, orders in conn.execute(
        "SELECT ad_group_id, SUM(cost), SUM(clicks), SUM(orders) FROM targeting_perf WHERE date=? GROUP BY ad_group_id",
        (end,)):
        asin = agp.get(str(ag))
        if not asin:
            continue
        s = stats.setdefault(asin, [0.0, 0, 0])
        s[0] += cost or 0; s[1] += clicks or 0; s[2] += orders or 0
    return stats


def compute(conn):
    # asin_ad_stats reads targeting_perf, so it needs targeting_perf's own newest
    # snapshot — campaign_perf's date is filled by a different report job and
    # matches zero targeting rows whenever that job has been failing.
    end = db.latest_snapshot(conn, "targeting_perf")
    roy = load_asin_royalty()
    stats = asin_ad_stats(conn, end)
    rows = []
    for asin, (spend, clicks, orders) in stats.items():
        if spend <= 0:
            continue
        total_roy = roy.get(asin, 0.0)
        traz = round(total_roy - spend, 2)
        epc = round(traz / clicks, 2) if clicks else 0
        rows.append(dict(asin=asin, royalty30=round(total_roy, 2), ad_spend=round(spend, 2),
                         traz=traz, clicks=clicks, orders=orders, epc=epc,
                         cvr=round(orders / clicks * 100, 1) if clicks else 0))
    rows.sort(key=lambda r: r["traz"], reverse=True)
    return end, rows


if __name__ == "__main__":
    conn = db.connect()
    end, rows = compute(conn)
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["asin", "royalty30", "ad_spend", "traz", "epc", "cvr", "clicks", "orders"])
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in w.fieldnames})
    advertised = len(rows)
    pos = [r for r in rows if r["traz"] > 0]
    print(f"TRAZ report ({end}) — advertised ASINs with spend: {advertised}")
    print(f"  TRAZ-positive (ads profitable incl. organic): {len(pos)}")
    print(f"  total TRAZ: ${sum(r['traz'] for r in rows):,.2f}  | total ad spend: ${sum(r['ad_spend'] for r in rows):,.2f}")
    print("\n  Top TRAZ ASINs (royalty − spend):")
    for r in rows[:8]:
        print(f"    {r['asin']}  TRAZ ${r['traz']:7.2f}  EPC ${r['epc']:.2f}  CVR {r['cvr']:.0f}%  (roy ${r['royalty30']}, spend ${r['ad_spend']})")
    print("\n  Worst TRAZ (bleeding even after organic):")
    for r in rows[-5:]:
        print(f"    {r['asin']}  TRAZ ${r['traz']:7.2f}  spend ${r['ad_spend']}  roy ${r['royalty30']}")
    print(f"\n  -> {OUT}")
    conn.close()
