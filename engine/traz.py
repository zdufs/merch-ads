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
# The catalogue folder is paths.POD_ROOT, never dirname(REPO_ROOT).
# The two agree only while MERCHADS_POD_DIR is unset. Set it, and half the
# engine reads one catalogue while products.export_signature() — which does use
# POD_ROOT — banks the signature of another, so the economics gate certifies a
# catalogue that was never mapped.
POD = paths.POD_ROOT
# NOT created here. Importing this module is what `halo` and every other
# reader do, and an import must not write into the operator's data folder
# — nor fail. A folder holding a FILE called `outputs` raised
# FileExistsError out of the import, and appctl answered `halo` with
# `[Errno 17] File exists` plus an absolute path. The one place that
# writes here creates it.
OUTDIR = os.path.join(HERE, "outputs")
OUT = os.path.join(OUTDIR, "traz_report.csv")


def load_asin_royalty():
    """asin -> royaltyLast30, from the merged product catalog.

    Snap for MOD exports no trailing-30 column, so on a Snap-only catalog this
    returns zeros for `royaltyLast30`. Callers wanting a real 30-day figure
    should use royalty_per_unit() or load_asin_royalty_windowed(), which read
    the dated SALES_REPORT.

    The field name is written out here rather than taken as an argument, and
    that is deliberate. A catalogue row is served either from the CSV files or
    from `catalog_cache`, and the cache banks only the twenty fields in
    `catalog_cache.FIELDS`. A field outside that set therefore reads back as
    None when the cache is warm and as a real value when it is cold — a wrong
    answer that depends on nothing the caller can see.

    `tests/catalog_cache_tests.py` exists to stop exactly that, by reading the
    field names out of the callers' syntax trees. It can only read a name it can
    SEE. This function used to take `field=` and reach the row through the
    variable, so the lint skipped the read in silence: planting a literal
    `p.get("bsr")` here fails the lint, while changing this default to
    `royaltyLast12Months` passed every test. Nothing called it with anything but
    the default, so nothing was ever wrong — the guard simply had a hole where
    it was documented to be closed. A literal closes it.
    """
    import export_reader
    out = {}
    for p in export_reader.catalog_rows(POD):
        a = p.get("asin")
        if not a:
            continue
        try:
            out[a] = float(p.get("royaltyLast30") or 0)
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


def sales_report_files():
    """Every dated SALES_REPORT in the POD folder.

    `load_sales_rows()` reads the UNION of these, so anything caching an answer
    derived from that union has to key on all of them. Keying on the newest file
    alone meant importing an older report changed the answer and not the key.
    """
    return sorted(glob.glob(os.path.join(POD, SALES_REPORT_GLOB)))


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


def royalty_per_unit(days=30, mkt=".com", rows=None, today=None):
    """asin -> royalty per unit sold over the last `days`, from the dated
    SALES_REPORT. Only ASINs with at least one unit in the window appear.

    This replaces the catalogue export's royaltyLast30/salesLast30 pair, which
    Snap for MOD does not carry. The report is per-day and covers every sales
    channel, so its rate is the truer one — the export fields were a single
    scalar Amazon refreshed on its own schedule."""
    import datetime as _dt
    start = (today or _dt.date.today()) - _dt.timedelta(days=int(days))
    units, royalty = {}, {}
    for r in (rows if rows is not None else load_sales_rows()):
        if mkt and r["mkt"] != mkt:
            continue
        if r["date"] < start:
            continue
        units[r["asin"]] = units.get(r["asin"], 0) + (r["purchased"] or 0)
        royalty[r["asin"]] = royalty.get(r["asin"], 0.0) + (r["royalty"] or 0.0)
    return {a: round(royalty.get(a, 0.0) / u, 4) for a, u in units.items() if u > 0}


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
    # The 30-day royalty comes from the dated SALES_REPORT, not the catalogue.
    # `load_asin_royalty` reads `royaltyLast30`, a MerchFlow column Snap for MOD
    # does not export — its own docstring says so and names this exact
    # alternative. On a Snap-sourced row it returns 0.0, so every such design
    # showed a royalty of zero and a TRAZ of minus its whole ad spend: a design
    # earning 80 with 30 of spend read as -30 instead of +50. The export figure
    # stays as the fallback for a design the report has never seen, and
    # `royalty_basis` says which one each row used.
    import datetime as _dt
    window_start = _dt.date.today() - _dt.timedelta(days=30)
    try:
        reported = load_asin_royalty_windowed(start=window_start)
    except Exception:
        reported = {}
    roy = load_asin_royalty()
    stats = asin_ad_stats(conn, end)
    rows = []
    for asin, (spend, clicks, orders) in stats.items():
        if spend <= 0:
            continue
        if asin in reported:
            total_roy, basis = reported[asin], "sales_report_30d"
        else:
            total_roy, basis = roy.get(asin, 0.0), "export_last30"
        traz = round(total_roy - spend, 2)
        epc = round(traz / clicks, 2) if clicks else 0
        rows.append(dict(asin=asin, royalty30=round(total_roy, 2), ad_spend=round(spend, 2),
                         traz=traz, clicks=clicks, orders=orders, epc=epc,
                         royalty_basis=basis,
                         cvr=round(orders / clicks * 100, 1) if clicks else 0))
    rows.sort(key=lambda r: r["traz"], reverse=True)
    return end, rows


if __name__ == "__main__":
    conn = db.connect()
    end, rows = compute(conn)
    os.makedirs(OUTDIR, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
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
