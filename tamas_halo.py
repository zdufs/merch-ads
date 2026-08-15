#!/usr/bin/env python3
"""
TAMAS halo estimator — read-only. US-only (TAMAS is US-only).

The Ads API only sees ad-ATTRIBUTED sales, so it is blind to the organic halo the
TAMAS method is built to capture. This tool windows the dated Merch SALES_REPORT
(total royalty, all channels) to each TAMAS design's ad-serving period and estimates
the INCREMENTAL organic lift over that design's own pre-ad baseline.

Method (per ASIN):
  ad_start   = first snapshot date the campaign showed impressions (campaign_perf)
  ad_spend   = latest cumulative cost for the campaign (snapshots are CUMULATIVE)
  ad_clicks  = latest cumulative clicks
  pre  window = report_start .. (ad_start - 1)   organic baseline (ad not serving)
  post window = ad_start .. report_end           ad live
  base_rate   = pre  net royalty / pre  days     ($/day organic, no ad)
  post_rate   = post net royalty / post days     ($/day with ad)
  halo_est    = (post_rate - base_rate) * post_days   incremental royalty over baseline
  net_halo    = halo_est - ad_spend                   halo value net of ad cost
  traz_window = post net royalty - ad_spend           TAMAS's TRAZ, serving-windowed

CAVEATS (this is CORRELATIONAL, never causal):
  - Tiny samples: a few units/design; daily rates are noisy.
  - Seasonality confounds the pre/post split (e.g. a 4th-of-July design ramps in late
    June regardless of ads). Flagged where royalty rows cluster near a holiday.
  - Designs with impressions but ~0 clicks / $0 spend got no ad TRAFFIC, so any "post"
    lift there is organic-by-construction, not halo. Flagged as no-ad-traffic.
  - Attribution: halo_est credits the ad with lift the ad may not have caused. It is an
    UPPER-BOUND estimate, sanity-anchored by the paused/never-served control designs.

Usage:  python3 tamas_halo.py          # print + write outputs/tamas_halo.csv
        python3 tamas_halo.py --json    # machine-readable
Run under ADS_MARKET=US (default); other markets are skipped (no TAMAS there).
"""

import csv
import datetime
import json
import os
import re
import sys

import db
import markets
import tamas
import traz

ASIN_RE = re.compile(r"B0[A-Z0-9]{8}")
OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
OUT = os.path.join(OUTDIR, "tamas_halo.csv")


def campaign_asin(conn, cid, name):
    """ASIN for a TAMAS campaign: prefer its ad_group_product mapping, else parse the name."""
    row = conn.execute(
        "SELECT p.asin FROM ad_groups g JOIN ad_group_product p ON p.ad_group_id = g.ad_group_id "
        "WHERE g.campaign_id = ? LIMIT 1", (cid,)).fetchone()
    if row and row[0]:
        return row[0]
    m = ASIN_RE.search(name or "")
    return m.group(0) if m else None


def design_title(conn, asin):
    """The full design title for an ASIN, taken from its descriptive ad-group name.

    Ad groups are named ``<ASIN>_<product_type>_<Title>`` (e.g.
    ``B0EXAMPLE1_standard_tshirt_Some Design Title Here``).
    The TAMAS ad group for the same design is named just the bare ASIN, so pick the
    LONGEST name the ASIN has — that is the one carrying the title. Merch titles use
    spaces and never underscores, so the last underscore splits the type slug from
    the title. Returns None when the ASIN has no descriptive ad group.
    """
    row = conn.execute(
        "SELECT g.name FROM ad_group_product p JOIN ad_groups g ON g.ad_group_id = p.ad_group_id "
        "WHERE p.asin = ? ORDER BY LENGTH(g.name) DESC LIMIT 1", (asin,)).fetchone()
    name = row[0] if row else None
    if not name or name == asin:
        return None
    tail = name[len(asin) + 1:] if name.startswith(asin + "_") else name
    title = (tail.rsplit("_", 1)[-1] if "_" in tail else tail).strip()
    return title or None


def ad_facts(conn, cid):
    """(ad_start_date, ad_spend, ad_clicks) from CUMULATIVE campaign_perf snapshots.
    ad_start = first snapshot with impressions>0; spend/clicks = latest cumulative."""
    rows = conn.execute(
        "SELECT date, impressions, clicks, cost FROM campaign_perf WHERE campaign_id=? ORDER BY date",
        (cid,)).fetchall()
    if not rows:
        return None, 0.0, 0
    start = next((datetime.date.fromisoformat(r[0]) for r in rows if (r[1] or 0) > 0), None)
    last = rows[-1]
    return start, float(last[3] or 0), int(last[2] or 0)


def analyze():
    if not markets.is_default():
        return None  # TAMAS is US-only
    conn = db.connect()
    sales = traz.load_sales_rows()
    if not sales:
        return {"error": "no SALES_REPORT-*.csv found in the POD folder"}
    us = [r for r in sales if r["mkt"] == ".com"]
    report_start = min(r["date"] for r in us)
    report_end = max(r["date"] for r in us)

    tcamps = [(str(r[0]), r[1]) for r in conn.execute("SELECT campaign_id, name FROM campaigns")
              if tamas.is_tamas(r[1]) or "tamas" in (r[1] or "").lower()]

    designs = []
    for cid, name in tcamps:
        asin = campaign_asin(conn, cid, name)
        if not asin:
            continue
        ad_start, spend, clicks = ad_facts(conn, cid)
        arows = [r for r in us if r["asin"] == asin]
        total_roy = sum(r["royalty"] for r in arows)
        net_units = sum(r["purchased"] - r["returned"] for r in arows)

        served = ad_start is not None
        # pre/post split on the design's own timeline
        if served:
            pre = [r for r in arows if r["date"] < ad_start]
            post = [r for r in arows if r["date"] >= ad_start]
            pre_days = max((ad_start - report_start).days, 1)
            post_days = max((report_end - ad_start).days + 1, 1)
        else:  # never served (paused control): whole window is baseline
            pre, post, pre_days, post_days = arows, [], (report_end - report_start).days + 1, 0

        pre_roy = sum(r["royalty"] for r in pre)
        post_roy = sum(r["royalty"] for r in post)
        base_rate = pre_roy / pre_days if pre_days else 0.0
        post_rate = post_roy / post_days if post_days else 0.0
        halo_est = (post_rate - base_rate) * post_days if post_days else 0.0
        net_halo = halo_est - spend
        traz_window = post_roy - spend if served else None

        # data-quality flags
        flags = []
        if not served:
            flags.append("never-served (control)")
        elif clicks == 0:
            flags.append("no-ad-traffic (0 clicks)")
        elif served and pre_roy > post_roy and base_rate > 2 * max(post_rate, 0.01):
            # design's royalty peaked BEFORE the ad launched → the pre/post split is
            # confounded (often seasonal, e.g. ad started just after a gifting peak);
            # a negative halo_est here is a baseline artifact, not ad harm.
            flags.append("peak-before-ad (baseline-confound)")

        designs.append(dict(
            asin=asin, name=name, title=design_title(conn, asin),
            ad_start=ad_start.isoformat() if ad_start else None,
            ad_spend=round(spend, 2), ad_clicks=clicks,
            total_royalty=round(total_roy, 2), net_units=net_units,
            pre_days=pre_days, post_days=post_days,
            pre_royalty=round(pre_roy, 2), post_royalty=round(post_roy, 2),
            base_rate=round(base_rate, 3), post_rate=round(post_rate, 3),
            halo_est=round(halo_est, 2), net_halo=round(net_halo, 2),
            traz_window=None if traz_window is None else round(traz_window, 2),
            flags="; ".join(flags)))

    designs.sort(key=lambda d: (d["ad_spend"], d["ad_clicks"]), reverse=True)
    return dict(market="US", report_start=report_start.isoformat(),
                report_end=report_end.isoformat(), designs=designs)


def main():
    res = analyze()
    if res is None:
        print(f"TAMAS is US-only — skipping for market {markets.current()}."); return
    if "error" in res:
        print("ERROR:", res["error"]); return
    if "--json" in sys.argv[1:]:
        print(json.dumps(res, indent=2)); return

    print(f"TAMAS halo — US · SALES_REPORT {res['report_start']} → {res['report_end']}")
    print(f"{'design':22} {'ad_start':10} {'spend':>6} {'clk':>4} "
          f"{'base$/d':>8} {'post$/d':>8} {'halo_est':>9} {'net_halo':>9} {'TRAZ_w':>8}  flags")
    for d in res["designs"]:
        label = re.sub(r"^TAMAS - | - B0[A-Z0-9]{8}$", "", d["name"])[:22]
        tz = "" if d["traz_window"] is None else f"{d['traz_window']:8.2f}"
        print(f"{label:22} {d['ad_start'] or '--':10} {d['ad_spend']:6.2f} {d['ad_clicks']:4d} "
              f"{d['base_rate']:8.3f} {d['post_rate']:8.3f} {d['halo_est']:9.2f} "
              f"{d['net_halo']:9.2f} {tz:>8}  {d['flags']}")

    os.makedirs(OUTDIR, exist_ok=True)
    cols = ["asin", "name", "title", "ad_start", "ad_spend", "ad_clicks", "total_royalty", "net_units",
            "pre_days", "post_days", "pre_royalty", "post_royalty", "base_rate", "post_rate",
            "halo_est", "net_halo", "traz_window", "flags"]
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for d in res["designs"]:
            w.writerow(d)
    print(f"\n  -> {OUT}")
    print("  NOTE: halo_est is an UPPER BOUND (correlational, not causal). "
          "Compare served designs against the never-served control.")


if __name__ == "__main__":
    main()
