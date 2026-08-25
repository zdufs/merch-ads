#!/usr/bin/env python3
"""
Organic-halo estimator — read-only. US-only (the Merch SALES_REPORT is US-only).

The Ads API reports ad-ATTRIBUTED sales only, so it is structurally blind to the
question every seller actually wants answered: **do my ads lift my organic sales?**
This tool answers it by windowing the dated Merch SALES_REPORT (total royalty, all
channels) to each design's own ad-serving period, and comparing the royalty-per-day
before and after ads started.

Scope: EVERY advertised design, whatever campaign type it sits in — lottery,
scavenger, standard, harvested. (It began scoped to a retired strategy whose
campaigns held exactly one ASIN. That shape does not generalise: a lottery campaign
holds up to 1,000 ASINs. So the unit here is the DESIGN, and its ad facts are summed
across every ad group that advertises it.)

Method (per ASIN):
  ad_start   = first day its ad groups recorded impressions (target_daily)
  ad_spend   = total cost across every ad group advertising it, in the window
  ad_clicks  = total clicks, same basis
  pre  window = report_start .. (ad_start - 1)   organic baseline, ad not serving
  post window = ad_start .. report_end           ad live
  base_rate   = pre  net royalty / pre  days     ($/day organic, no ad)
  post_rate   = post net royalty / post days     ($/day with ad)
  halo_est    = (post_rate - base_rate) * post_days   incremental royalty over baseline
  net_halo    = halo_est - ad_spend                   halo value net of ad cost
  royalty_less_spend = post net royalty - ad_spend

Ad facts come from `target_daily`, which holds TRUE per-day rows. The earlier
version read `campaign_perf`, whose rows are CUMULATIVE trailing-30 snapshots — it
took the newest row as "total spend", which is a trailing-30 figure, not a total.

CAVEATS (this is CORRELATIONAL, never causal):
  - Small samples per design; daily rates are noisy.
  - Seasonality confounds the pre/post split. A 4th-of-July design ramps in late
    June whether or not it is advertised. Flagged as peak-before-ad.
  - A design with impressions but no clicks got no ad TRAFFIC, so any "post" lift
    is organic by construction. Flagged as no-ad-traffic.
  - halo_est credits the ad with lift the ad may not have caused. Treat it as an
    UPPER BOUND, and read it against the never-served control designs.

Usage:  python3 halo.py                  # print + write outputs/halo.csv
        python3 halo.py --json           # machine-readable
        python3 halo.py --min-spend 5    # only designs that spent at least $5
        python3 halo.py --limit 100
Run under ADS_MARKET=US (default); other markets have no sales report to read.
"""

import argparse
import csv
import datetime
import io
import json
import os

import paths
import sys

import db
import markets
import traz

OUTDIR = os.path.join(paths.REPO_ROOT, "outputs")
OUT = os.path.join(OUTDIR, "halo.csv")
CACHE = os.path.join(OUTDIR, "halo_cache.json")

# Bump when the arithmetic changes, so an old cache cannot survive a code change.
CACHE_MODEL = "halo-2"
CACHE_KEEP = 4          # how many (min_spend) variants to keep on disk

DEFAULT_MIN_SPEND = 1.00   # below this the daily rates are pure noise
DEFAULT_LIMIT = 300


def _title_from_name(asin, name):
    """Pull the design title out of one ad-group name. Pure string work."""
    if not name or name == asin:
        return None
    tail = name[len(asin) + 1:] if name.startswith(asin + "_") else name
    title = (tail.rsplit("_", 1)[-1] if "_" in tail else tail).strip()
    return title or None


def titles_by_asin(conn):
    """{asin: title} for every advertised design, in ONE query.

    Ad groups are named ``<ASIN>_<product_type>_<Title>`` (e.g.
    ``B0EXAMPLE1_standard_tshirt_Some Design Title Here``). The same design can
    also sit in an ad group named just the bare ASIN, so pick the LONGEST name the
    ASIN has — that is the one carrying the title. Merch titles use spaces and
    never underscores, so the last underscore splits the type slug from the title.
    An ASIN with no descriptive ad group is absent from the map.

    This exists because `analyze()` used to call `design_title` once per design.
    At 55k designs that was 55k round trips and about four seconds of the seven
    the Organic Halo screen took to load. `_types_by_asin` already grouped its
    lookup once; this is the same fix for titles.
    """
    best = {}
    for asin, name in conn.execute(
            "SELECT p.asin, g.name FROM ad_group_product p "
            "JOIN ad_groups g ON g.ad_group_id = p.ad_group_id"):
        if not asin or not name:
            continue
        if len(name) > len(best.get(asin, "")):
            best[asin] = name
    out = {}
    for asin, name in best.items():
        title = _title_from_name(asin, name)
        if title:
            out[asin] = title
    return out


def design_title(conn, asin):
    """The design title for ONE ASIN. See `titles_by_asin` for the bulk path."""
    row = conn.execute(
        "SELECT g.name FROM ad_group_product p JOIN ad_groups g ON g.ad_group_id = p.ad_group_id "
        "WHERE p.asin = ? ORDER BY LENGTH(g.name) DESC LIMIT 1", (asin,)).fetchone()
    return _title_from_name(asin, row[0] if row else None)


def ad_facts(conn, start, end):
    """Per-ASIN ad facts over [start, end], summed across every ad group.

    Returns {asin: {"ad_start": date|None, "spend": float, "clicks": int,
                    "campaigns": set[str]}}.

    One row per design, not per campaign: a design advertised in a lottery
    campaign AND its typed scavenger cohort has one ad-serving history, and that
    is what the royalty timeline has to be compared against.
    """
    rows = conn.execute("""
        SELECT p.asin,
               MIN(CASE WHEN t.impressions > 0 THEN t.date END) AS first_served,
               SUM(t.cost)   AS spend,
               SUM(t.clicks) AS clicks
          FROM target_daily t
          JOIN ad_group_product p ON p.ad_group_id = t.ad_group_id
         WHERE t.date >= ? AND t.date <= ?
         GROUP BY p.asin""", (start.isoformat(), end.isoformat())).fetchall()

    out = {}
    for asin, first_served, spend, clicks in rows:
        if not asin:
            continue
        out[asin] = {
            "ad_start": datetime.date.fromisoformat(first_served) if first_served else None,
            "spend": float(spend or 0.0),
            "clicks": int(clicks or 0),
        }
    return out


def _types_by_asin(conn):
    """{asin: "lottery, scavenger"} — every campaign kind advertising each design.

    Classification goes through campaign_kinds. This function briefly carried its
    own copy of the ladder, which is exactly the drift campaign_kinds exists to
    prevent.
    """
    import campaign_kinds
    out = {}
    for asin, cname in conn.execute("""
            SELECT p.asin, c.name
              FROM ad_group_product p
              JOIN ad_groups g ON g.ad_group_id = p.ad_group_id
              JOIN campaigns c ON c.campaign_id = g.campaign_id"""):
        if not asin:
            continue
        out.setdefault(asin, set()).add(campaign_kinds.classify(cname))
    return {a: ", ".join(sorted(k)) for a, k in out.items()}


def _cache_key(conn, min_spend):
    """What the answer DEPENDS ON. Change any of it and the cache misses.

    Halo is recomputed from scratch on every screen open, but its inputs only
    move once a night: the nightly banks a new `target_daily` day, and the
    operator drops in a new SALES_REPORT now and then. So the honest cache is
    one keyed on those inputs rather than on a clock. Nothing here is a guess
    about how long an answer stays good.

    Returns None when the key cannot be built, and a None key means DO NOT
    CACHE — an unreadable input must never look like an unchanged one.
    """
    try:
        span = conn.execute(
            "SELECT MAX(date), COUNT(*) FROM target_daily").fetchone()
        agp = conn.execute("SELECT COUNT(*) FROM ad_group_product").fetchone()
        report = traz.sales_report_path()
        stat = os.stat(report) if report else None
        # EVERY sales report, not just the newest. `traz.load_sales_rows()`
        # reads the accumulated union of them, so importing an older report —
        # April, say — adds royalties to the answer while leaving the newest
        # file's name, size and mtime untouched. The key still matched, so
        # Organic Halo went on serving numbers computed before the import, and
        # a cache that is confidently yesterday reads healthy from every angle.
        reports = []
        for path in sorted(traz.sales_report_files()
                           if hasattr(traz, "sales_report_files")
                           else ([report] if report else [])):
            try:
                st = os.stat(path)
            except OSError:
                return None            # unreadable input: never cache
            reports.append(f"{os.path.basename(path)}|{st.st_size}|{int(st.st_mtime)}")
    except Exception:
        return None
    if not span or not span[0] or not report or not stat:
        return None
    return {
        "model": CACHE_MODEL,
        "market": markets.current(),
        "target_daily_last": span[0],
        "target_daily_rows": span[1],
        "ad_group_product_rows": agp[0],
        "report": os.path.basename(report),
        "report_size": stat.st_size,
        "report_mtime": int(stat.st_mtime),
        "reports": reports,
        "min_spend": min_spend,
    }


def _cache_read(key):
    """The cached full design list for `key`, or None."""
    if not key:
        return None
    try:
        with io.open(CACHE, encoding="utf-8") as fh:
            blob = json.load(fh)
    except Exception:
        return None
    for entry in blob.get("entries", []):
        if entry.get("key") == key:
            return entry.get("value")
    return None


def _cache_write(key, value):
    """Store `value` under `key`, keeping the newest CACHE_KEEP variants.

    A failure here is never fatal: the cache is an optimisation, and a read-only
    or full disk must not break a read-only report.
    """
    if not key:
        return
    try:
        try:
            with io.open(CACHE, encoding="utf-8") as fh:
                blob = json.load(fh)
        except Exception:
            blob = {}
        entries = [e for e in blob.get("entries", []) if e.get("key") != key]
        entries.insert(0, {"key": key, "value": value})
        os.makedirs(OUTDIR, exist_ok=True)
        tmp = CACHE + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"entries": entries[:CACHE_KEEP]}, fh)
        os.replace(tmp, CACHE)
    except Exception:
        pass


def _shape(report_start, report_end, designs, limit, min_spend):
    """The reply, sliced to `limit`. Kept separate so a cached full list and a
    freshly computed one are shaped by the SAME code."""
    total = len(designs)
    truncated = bool(limit and total > limit)
    return dict(market="US",
                report_start=report_start,
                report_end=report_end,
                count=total,
                returned=min(total, limit) if limit else total,
                truncated=truncated,
                min_spend=min_spend,
                designs=designs[:limit] if limit else designs)


def analyze(min_spend=DEFAULT_MIN_SPEND, limit=DEFAULT_LIMIT, conn=None):
    """`conn` lets a caller analyze ITS database instead of opening the market's.
    Without it, a caller working on a temporary database (the rules DSL under
    test, for one) silently reported halo from the operator's real data."""
    if not markets.is_default():
        return None  # the Merch SALES_REPORT is US-only
    # A caller-supplied connection may be a temporary or test database. Its answer
    # must never be written to, or served from, the operator's cache.
    cacheable = conn is None
    # Read-only: this is a report. Opening read-write created the market
    # database on a folder that had never been pulled.
    conn = conn or db.connect(ro=True)

    # Cheap short-circuit BEFORE parsing the sales-report CSV: with no per-day ad
    # history there is nothing to window against, and the CSV parse is the
    # expensive part.
    span = conn.execute("SELECT MIN(date), MAX(date) FROM target_daily").fetchone()
    if not span or not span[0]:
        return {"error": "no per-day ad history (target_daily) banked yet; "
                         "run backfill_target_daily.py"}

    key = _cache_key(conn, min_spend) if cacheable else None
    hit = _cache_read(key)
    if hit:
        return _shape(hit["report_start"], hit["report_end"],
                      hit["designs"], limit, min_spend)

    sales = traz.load_sales_rows()
    if not sales:
        return {"error": "no SALES_REPORT-*.csv found in the POD folder"}
    us = [r for r in sales if r["mkt"] == ".com"]
    if not us:
        return {"error": "the SALES_REPORT has no US (.com) rows"}
    report_start = min(r["date"] for r in us)
    report_end = max(r["date"] for r in us)

    facts = ad_facts(conn, report_start, report_end)
    if not facts:
        return {"error": "no per-day ad history (target_daily) overlaps the sales report; "
                         "run backfill_target_daily.py or import a more recent report"}
    types = _types_by_asin(conn)
    titles = titles_by_asin(conn)

    # Royalty rows grouped once, rather than rescanned per design.
    by_asin = {}
    for r in us:
        by_asin.setdefault(r["asin"], []).append(r)

    # The first day of ad history we HAVE. `ad_start` is the first day a design
    # recorded impressions, and a design already advertising before this date
    # simply looks as if it started here — so the days before it get counted as
    # the organic, ad-free baseline when the ad was in fact running. Measured
    # 2026-08-24: the sales report starts 2026-04-15 and target_daily starts
    # 2026-05-06, so three weeks of ad-served sales sit in every such baseline.
    # The estimate is already documented as an upper bound with flags for its
    # confounds; this is one more confound, named.
    coverage_from = conn.execute("SELECT MIN(date) FROM target_daily").fetchone()[0]
    coverage_from = (datetime.date.fromisoformat(coverage_from)
                     if coverage_from else None)

    designs = []
    for asin, f in facts.items():
        spend, clicks, ad_start = f["spend"], f["clicks"], f["ad_start"]
        arows = by_asin.get(asin, [])
        if spend < min_spend and arows == []:
            continue
        total_roy = sum(r["royalty"] for r in arows)
        net_units = sum(r["purchased"] - r["returned"] for r in arows)

        served = ad_start is not None
        if served:
            pre = [r for r in arows if r["date"] < ad_start]
            post = [r for r in arows if r["date"] >= ad_start]
            pre_days = max((ad_start - report_start).days, 1)
            post_days = max((report_end - ad_start).days + 1, 1)
        else:  # never served in the window: the whole thing is baseline (a control)
            pre, post, pre_days, post_days = arows, [], (report_end - report_start).days + 1, 0

        pre_roy = sum(r["royalty"] for r in pre)
        post_roy = sum(r["royalty"] for r in post)
        base_rate = pre_roy / pre_days if pre_days else 0.0
        post_rate = post_roy / post_days if post_days else 0.0
        halo_est = (post_rate - base_rate) * post_days if post_days else 0.0
        net_halo = halo_est - spend
        royalty_less_spend = post_roy - spend if served else None

        flags = []
        if not served:
            flags.append("never-served (control)")
        elif clicks == 0:
            flags.append("no-ad-traffic (0 clicks)")
        elif pre_roy > post_roy and base_rate > 2 * max(post_rate, 0.01):
            # royalty peaked BEFORE the ad launched, so the pre/post split is
            # confounded (often seasonal). A negative halo_est here is a baseline
            # artifact, not evidence the ad did harm.
            flags.append("peak-before-ad (baseline-confound)")
        if (served and coverage_from and ad_start <= coverage_from
                and report_start < coverage_from):
            # We have no ad history before `coverage_from`, so we cannot say this
            # design was NOT advertising then. Its "baseline" may be ad-served.
            flags.append(f"ad-history-starts-{coverage_from.isoformat()} "
                         f"(baseline may include ad-served days)")

        title = titles.get(asin)
        designs.append(dict(
            asin=asin, name=title or asin, title=title,
            campaign_types=types.get(asin),
            ad_start=ad_start.isoformat() if ad_start else None,
            ad_spend=round(spend, 2), ad_clicks=clicks,
            total_royalty=round(total_roy, 2), net_units=net_units,
            pre_days=pre_days, post_days=post_days,
            pre_royalty=round(pre_roy, 2), post_royalty=round(post_roy, 2),
            base_rate=round(base_rate, 3), post_rate=round(post_rate, 3),
            halo_est=round(halo_est, 2), net_halo=round(net_halo, 2),
            traz_window=None if royalty_less_spend is None else round(royalty_less_spend, 2),
            flags="; ".join(flags)))

    designs = [d for d in designs if d["ad_spend"] >= min_spend or d["total_royalty"] > 0]
    designs.sort(key=lambda d: (d["ad_spend"], d["ad_clicks"]), reverse=True)

    _cache_write(key, {"report_start": report_start.isoformat(),
                       "report_end": report_end.isoformat(),
                       "designs": designs})

    return _shape(report_start.isoformat(), report_end.isoformat(),
                  designs, limit, min_spend)


def main():
    ap = argparse.ArgumentParser(description="Estimate the organic halo of advertising, per design.")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--min-spend", type=float, default=DEFAULT_MIN_SPEND)
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="0 = every design")
    args = ap.parse_args()

    res = analyze(min_spend=args.min_spend, limit=args.limit or 0)
    if res is None:
        print(f"The Merch sales report is US-only — skipping for market {markets.current()}."); return
    if "error" in res:
        print("ERROR:", res["error"]); return
    if args.json:
        print(json.dumps(res, indent=2)); return

    print(f"Organic halo — US · SALES_REPORT {res['report_start']} → {res['report_end']}")
    print(f"{res['returned']} of {res['count']} designs (min spend ${res['min_spend']:.2f})\n")
    print(f"{'design':30} {'types':20} {'ad_start':10} {'spend':>7} {'clk':>5} "
          f"{'base$/d':>8} {'post$/d':>8} {'halo_est':>9} {'net_halo':>9}  flags")
    for d in res["designs"]:
        label = (d["title"] or d["asin"])[:30]
        print(f"{label:30} {(d['campaign_types'] or '')[:20]:20} {d['ad_start'] or '--':10} "
              f"{d['ad_spend']:7.2f} {d['ad_clicks']:5d} "
              f"{d['base_rate']:8.3f} {d['post_rate']:8.3f} {d['halo_est']:9.2f} "
              f"{d['net_halo']:9.2f}  {d['flags']}")

    os.makedirs(OUTDIR, exist_ok=True)
    cols = ["asin", "name", "title", "campaign_types", "ad_start", "ad_spend", "ad_clicks",
            "total_royalty", "net_units", "pre_days", "post_days", "pre_royalty",
            "post_royalty", "base_rate", "post_rate", "halo_est", "net_halo",
            "traz_window", "flags"]
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for d in res["designs"]:
            w.writerow(d)
    print(f"\n  -> {OUT}")
    print("  NOTE: halo_est is an UPPER BOUND (correlational, not causal). "
          "Read it against the never-served control designs.")


if __name__ == "__main__":
    main()
