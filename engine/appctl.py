#!/usr/bin/env python3
"""
appctl — the single JSON API the SwiftUI Mac app calls. One dispatcher, structured JSON
out, reusing the existing engine. Per-market endpoints follow the usual convention: the
caller sets ADS_MARKET (default US); cross-market endpoints open each market DB themselves.

Every command prints ONE json object: {"ok": true, "data": ...} or {"ok": false, "error": ...}.

READ (fast, DB-only — safe to call anytime):
  markets                         list markets + availability
  metrics                         KPI cards (trailing-30 headline, daily, mtd) + trend + movers
  campaigns [--type T] [--state S] campaign list w/ perf, classified by strategy
  adgroups --campaign ID          ad groups in a campaign w/ perf + cached state
  targets --adgroup ID            targets in an ad group w/ perf + last bid change
  searchterms --adgroup ID        search terms hitting an ad group (spend-sorted)
  asin ASIN                       every ad group an ASIN runs in (+ cached perf)
  bidhistory --target ID          bid-change timeline for one target
  history --campaign|--adgroup|--target ID   dated perf series from banked snapshots
  negatives --adgroup ID          negative keywords already applied (from writes_log)
  daily [--days N]                true per-day account totals (daily_totals)
  killlist                        designs with CVR < floor AND ACOS over break-even
  health                          per-market last pull / last write / freshness (all markets)

LIVE / ACTIONS (delegate to the engine; need Amazon API — run on the Mac):
  status ASIN [ASIN ...]          LIVE state from Amazon (wraps status.py)
  run [--phase NAME]              trigger the scheduled job (or one phase) for this market
  kill [--on|--off]               freeze/unfreeze ALL writes (the KILL file); no flag = report
  pause/enable --adgroup ID       set ad-group state (logs + mirrors locally)
  pause-campaign/enable-campaign --campaign ID
  setbid --target ID --bid X [--prev OLD]   manual bid edit on one target
  setbudget --campaign ID --budget X [--prev OLD]   manual daily-budget edit (undoable)
  resetbids [--apply]             reset net-inflated bids to original-10% (preview default)
  negatives-preview               phase2 plan (proposed negatives + pauses) as JSON
  negatives-apply                 apply APPROVED subset (plan JSON on stdin)
  everywhere-preview              resolve an accumulated 'act everywhere' selection
                                  (stdin {kind,action,keys}) to its instances (read-only)
  everywhere-apply                apply it: pause ad groups/targets or negate, across all
  audit [--limit N]               recent writes_log rows + undoable flag
  undo --row ID                   undo one logged write (states, bids, and negatives)

Examples:
  ADS_MARKET=US python3 appctl.py metrics
  ADS_MARKET=DE python3 appctl.py campaigns --type lottery --state ENABLED
"""

import argparse
import datetime
import glob
import io
import json
import os

# paths resolves the data folder and STOPS when it was named and is not there
# (engine/paths.py). That happens at import, before there is a dispatcher to
# wrap it, and the app's whole contract is one JSON object on stdout — so the
import re
import sqlite3
import subprocess
import sys


def _import_failed(reason):
    """Emit the envelope for a startup that never reached the dispatcher."""
    print(json.dumps({"ok": False, "error": str(reason)}))
    raise SystemExit(1)


# Importing the engine can legitimately stop the process before there is a
# dispatcher to wrap the reason, and the app's whole contract is one JSON object
# on stdout. Two ways it happens today, both with a message worth reading:
#
#   paths.py       MERCHADS_DATA_DIR names a folder that is not there
#   markets.py     ADS_MARKET is not a market — raised from db.py's module body,
#                  which resolves the database path at import
#
# Without this the operator got a bare "appctl exited with code 1" and the
# sentence explaining it went to a stderr tail. Keep every engine import inside
# the block: whichever runs first is the one that raises.
try:
    import paths

    HERE = paths.REPO_ROOT
    import db
    import markets
    import products
    import campaign_kinds
    import scavenger
    import cross_sell
    import harvest_suggest
    import harvest_promote_group
except SystemExit as _startup_error:
    _import_failed(_startup_error.code if isinstance(_startup_error.code, str) else _startup_error)

FLOOR_CVR = 0.08          # csmetro's "kill under 8% CVR"
RX_BID = re.compile(r"([0-9]*\.?[0-9]+)\s*->\s*([0-9]*\.?[0-9]+)")


# ---- helpers ---------------------------------------------------------------
_RESPONDED = False   # serve mode: whether the current request already printed
# serve mode pins the app's real pipe here so the single envelope always reaches
# it, even while a handler's stray stdout is being redirected into a sink. None
# in one-shot mode, where envelopes go to sys.stdout as before.
_RESP_STREAM = None



def _engine_script(name):
    """Absolute path to an engine script.

    They live in engine/, NOT the repo root. Every subprocess launch here used
    to build repo_root/<name>, which cannot be opened, so `adopt-export` marked
    US economics STALE on every single run, `demandfeed --refresh` quietly
    served a stale file, and `status` / `run --phase` / `backfill-daily` /
    `promote` failed outright. Same class as the `_newest_export` bug the
    products.py comment describes: an aliased path that survived the move into
    engine/ without being updated. Guarded by tests/engine_script_path_tests.py.
    """
    return os.path.join(paths.ENGINE_DIR, name)


def out(data):
    global _RESPONDED
    _RESPONDED = True
    print(json.dumps({"ok": True, "data": data}, default=str),
          file=_RESP_STREAM or sys.stdout)


def err(msg):
    global _RESPONDED
    _RESPONDED = True
    print(json.dumps({"ok": False, "error": str(msg)}),
          file=_RESP_STREAM or sys.stdout)
    sys.exit(1)


def _market_db_path(mkt):
    return os.path.join(HERE, "ads_data.sqlite" if mkt == "US" else f"ads_data_{mkt}.sqlite")


def _iso_date_arg(value, flag):
    """A YYYY-MM-DD date, or a refusal that names the flag.

    An unparseable date used to go straight into the SQL and match nothing, so
    `report --start not-a-date` answered `ok:true` with an all-zero period —
    visually identical to a genuinely quiet fortnight, with `day_count: 0` the
    only tell and no error field anywhere. `--days` and `--limit` already refuse
    a bad value; dates were the one input that did not."""
    if value in (None, ""):
        return None
    try:
        return datetime.date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError):
        err(f"{flag} needs a date as YYYY-MM-DD (got {value!r})")


def _iso_timestamp_arg(value, flag):
    """An ISO timestamp, returned unchanged, or a refusal that names the flag.

    Unchanged on purpose: `writes_log.applied_at` is compared as TEXT, so a
    bare date has to keep meaning the start of that day."""
    if value in (None, ""):
        return None
    try:
        datetime.datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        err(f"{flag} needs an ISO timestamp like 2026-08-24T10:00:00 "
            f"(got {value!r})")
    return str(value)


def _child_failure_reason(text):
    """One readable line from a subprocess that died, fit for the envelope.

    A byte slice of the tail is worse than useless. `[-300:]` cuts the
    "Traceback (most recent call last):" header off and keeps the BODY — source
    lines, caret markers, absolute paths — so the reply carried a traceback that
    the project's own literal `assertNotIn("Traceback")` check could not see.

    The last line of a traceback is the exception, and that is the one line
    worth repeating. When it carries an errno or a path, only the exception
    class comes through: an absolute path the reader never asked about is not an
    explanation, and errnos in the envelope are what the 2026-08-21 audit took
    out everywhere else. The whole thing goes to stderr regardless."""
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    if not lines:
        return ""
    last = lines[-1]
    if "[Errno" in last or 'File "' in last:
        return last.split(":", 1)[0].strip()
    return last[:200]


def _env_if_configured():
    """The credentials, or an empty dict when `.env` is not there yet.

    `ads_client.load_env` raises a string SystemExit for a missing file, and
    that is right for a command which needs Amazon — the dispatcher turns it
    into a one-sentence envelope. But SystemExit is a BaseException, so the
    `except Exception` that `health` and `alerts` wrap it in never caught it,
    and those two refused to answer at all on a fresh data folder. They are the
    first screens an operator opens on a standalone install, and both already
    carry a `configured` field that says exactly this."""
    try:
        from ads_client import load_env
        return load_env()
    except (SystemExit, Exception):
        return {}


def classify(name):
    """The engine's view of a campaign's strategy. Delegates to campaign_kinds so
    there is exactly one implementation — this used to be a second copy."""
    return campaign_kinds.classify(name)


def _acos(cost, sales):
    return round(cost / sales, 4) if sales else None


def _cvr(orders, clicks):
    """Conversion rate, or None when nothing was ever clicked.

    It returned 0.0, which is not "unknown" — it is the WORST possible score,
    on the one metric a reader scans for the worst rows. A keyword nobody has
    clicked sorted beside a keyword clicked forty times without selling, and
    the CSV export said 0% for both. `_acos` directly above has always answered
    None for no sales; this was the sibling that did not, and the rules engine
    had the identical hole on the write side (fixed the same day). Every `cvr`
    field in the app is optional so this draws as "—". Found by review,
    2026-08-23."""
    return round(orders / clicks, 4) if clicks else None


def _cond(name, actual, threshold, passed):
    """One debug-trace row: what a single condition evaluated to. Additive on
    preview endpoints — actual/threshold are raw (fractions for acos/cvr, dollars
    for bids/spend); the app formats them. None actual renders as '—'."""
    return {"condition": name, "actual": actual, "threshold": threshold,
            "pass": bool(passed)}


def _reset_trace(item):
    """resetbids preview item + the decisive condition (10%-below-original reset
    only fires when the current bid drifted above the original)."""
    return {**item, "trace": [
        _cond("current > original", item["current"], item["original"],
              item["current"] > item["original"])]}


def _neg_trace(m):
    """Debug trace for a phase2 negative-keyword candidate, from its raw metrics
    (attached by phase2_apply.candidates). None when metrics are absent."""
    if not m:
        return None
    if m.get("rule") == "waste":
        return [_cond("clicks >= min", m["clicks"], m["min_clicks"], m["clicks"] >= m["min_clicks"]),
                _cond("orders == 0", m["orders"], 0, m["orders"] == 0)]
    if m.get("rule") == "acos":
        return [_cond("acos > ceiling", m["acos"], m["ceiling"], m["acos"] > m["ceiling"])]
    return None


def _pause_trace(m):
    """Debug trace for a phase2 ad-group pause candidate, from its raw metrics."""
    if not m:
        return None
    if m.get("rule") == "no_sales":
        return [_cond("orders == 0", 0, 0, True)]
    if m.get("rule") == "acos_cvr":
        return [_cond("acos > target", m["acos"], m["target"], m["acos"] > m["target"]),
                _cond("cvr < floor", m["cvr"], m["cvr_floor"], m["cvr"] < m["cvr_floor"])]
    return None


def _latest_two_dates(cur):
    ds = [r[0] for r in cur.execute("SELECT DISTINCT date FROM campaign_perf ORDER BY date DESC LIMIT 2")]
    return (ds[0] if ds else None), (ds[1] if len(ds) > 1 else None)


# Each perf table is pulled on its own cadence and carries its own latest
# snapshot date — targeting_perf/search_term_perf routinely lag campaign_perf
# by a day or two. Filtering one table by another's MAX(date) silently returns
# ZERO rows (the ad-groups/targets/search-terms zeroing bug). Always resolve a
# table's own latest date. `table` is an internal constant, never user input.
def _latest_date(cur, table):
    r = cur.execute(f"SELECT MAX(date) FROM {table}").fetchone()  # noqa: S608 (constant table)
    return r[0] if r else None


def _check_econ_gate():
    """Refuse economics-driven writes when the US freshness gate is closed
    (PLAN.md §8). Operator-explicit single actions (pause/enable/setbid on a
    named entity) do NOT come through here — those are human decisions."""
    g = products.econ_gate()
    if not g["ok"]:
        err("economics gate closed: " + "; ".join(g["reasons"]))


def _design_be_for(conn):
    """Per-design break-even resolver for the kill list / kill alerts. Thin alias
    for the single source products.design_be_for (shared with the rules DSL) so
    the gating can never drift. See that function for the contract."""
    return products.design_be_for(conn)


def _econ_coverage(not_live=None, conn=None):
    """How many advertised ad groups the economics gate can actually judge.

    `catalog_status` counts designs the price map COVERS, and that number reads
    far worse than the truth. Only US standard tees resolve their break-even
    from the design's own list price; every other product type, and every other
    market, is priced from the type table and needs no per-design price at all
    (see products.design_be_for). On 2026-08-22 the catalogue was missing a list
    price for 19,185 advertised designs and 18,001 of those were hats, which are
    priced per TYPE and were never affected — the number the operator would act
    on was 182.

    So this asks the REAL gate, one ad group at a time, and reports its own skip
    reasons. `transition` is a deliberate 30-day leniency after a price change,
    not a gap. ~0.5s over 85k ad groups; None when the economics tables are not
    present yet, so a caller can say "unknown" rather than "fine".
    """
    import collections
    try:
        # `conn` is injectable for the same reason halo.analyze takes one: the
        # exclusions below are a money-path guard, and a guard that can only be
        # exercised against the operator's live database is not really tested.
        conn = conn if conn is not None else db.connect(ro=True)
        be_for = products.design_be_for(conn)
        if be_for is None:
            return None
        counts = collections.Counter()
        stuck_asins = set()
        stuck_groups = []
        # Only ad groups that could still ACT are counted.
        #
        # ARCHIVED is terminal — Amazon has no un-archive, so an archived ad
        # group can never serve or spend again, and reporting it as "no usable
        # economics" asks the operator to fix something that cannot happen.
        #
        # A row the latest map did not touch is a leftover: `ad_group_product`
        # keeps its row, but Amazon's live product-ad list no longer returns
        # that ad group, so nothing refreshes it and its blank price is an
        # artifact of the row's age rather than a gap in the catalogue. On
        # 2026-08-22 the two together turned a warning about 14 products into
        # one about the 2 that could actually spend money.
        #
        # Both exclusions are COUNTED and reported, never silent.
        state = {str(k): (v or "").upper() for k, v in
                 conn.execute("SELECT ad_group_id, state FROM ad_groups")}
        newest_map = conn.execute(
            "SELECT MAX(mapped_at) FROM ad_group_product").fetchone()[0] or ""
        newest_day = newest_map[:10]
        excluded = collections.Counter()
        removed_enabled = set()
        for agid, asin, mapped_at in conn.execute(
                "SELECT ad_group_id, asin, mapped_at FROM ad_group_product"):
            if state.get(str(agid)) == "ARCHIVED":
                excluded["archived"] += 1
                continue
            if newest_day and (mapped_at or "")[:10] < newest_day:
                excluded["stale_row"] += 1
                continue
            skip = be_for(str(agid))[1]
            counts[skip or "ok"] += 1
            if skip in ("unknown_price", "unmapped"):
                stuck_groups.append(str(agid))
                if asin:
                    stuck_asins.add(asin)
                    # A removed listing whose ad is ALREADY paused needs
                    # nothing. Telling the operator to go and pause it is the
                    # same failure as telling them to re-export for a design
                    # that is gone: an instruction that is already satisfied
                    # reads exactly like one that is not.
                    if asin in (not_live or {}) and state.get(str(agid)) == "ENABLED":
                        removed_enabled.add(asin)
        # A MerchFlow "all products" export carries REMOVED listings, so a
        # design with no price may be one no export can ever price again. Those
        # must not be counted among the ones a fresh export would fix, and the
        # app must not tell the operator to go and re-export for them.
        not_live = not_live or {}
        removed = {a for a in stuck_asins if a in not_live}
        spend = 0.0
        if stuck_groups:
            latest = db.latest_snapshot(conn, "targeting_perf")
            if latest:
                marks = ",".join("?" * len(stuck_groups))
                spend = conn.execute(
                    f"SELECT COALESCE(SUM(cost),0) FROM targeting_perf"
                    f" WHERE date=? AND ad_group_id IN ({marks})",
                    [latest] + stuck_groups).fetchone()[0] or 0.0
    except sqlite3.Error:
        return None
    return {"total": sum(counts.values()),
            "ok": counts.get("ok", 0),
            "transition": counts.get("transition", 0),
            "unknown_price": counts.get("unknown_price", 0),
            "unmapped": counts.get("unmapped", 0),
            "cohort": counts.get("cohort", 0),
            # AD GROUPS, not products — one product can be advertised by
            # several. On 2026-08-22 that was 200 ad groups over 177 ASINs, and
            # calling the first number "designs" overstated it by 23.
            "actionable": counts.get("unknown_price", 0) + counts.get("unmapped", 0),
            "actionable_asins": len(stuck_asins),
            # Split by whether the listing still exists. `actionable_live` is
            # the only number a fresh catalogue export can move.
            "actionable_removed": len(removed),
            # Of the removed ones, those whose ad is still ENABLED — the only
            # subset there is anything to do about.
            "actionable_removed_enabled": len(removed_enabled),
            "actionable_live": len(stuck_asins) - len(removed),
            "removed_statuses": dict(collections.Counter(
                not_live[a] for a in removed)),
            # Ad groups deliberately left out of every count above, because
            # neither can serve: archived is terminal, and a stale row is one
            # Amazon's live product-ad list no longer returns.
            "excluded_archived": excluded.get("archived", 0),
            "excluded_stale_rows": excluded.get("stale_row", 0),
            # What it COSTS to leave them unjudged. A count alone reads as
            # bookkeeping; this is the number that says whether to care. These
            # ad groups are not paused or flagged by anything — no rule can
            # decide, so no rule acts.
            "actionable_spend": round(spend, 2)}


def cmd_econ_gate(args):
    """Freshness gate status for the current market (run_scheduled + app)."""
    g = products.econ_gate()
    g["market"] = markets.current()
    g["currency"] = markets.cfg().get("currency")
    g["model_version"] = products.US_TEE_ROYALTY_V
    # The per-ASIN status map is bulk (hundreds of entries) and only the counts
    # belong in the reply, so it is taken out of `catalog` and consumed here.
    not_live = (g.get("catalog") or {}).pop("not_live", None)
    g["econ_coverage"] = _econ_coverage(not_live)
    out(g)


# ---- READ endpoints --------------------------------------------------------
def cmd_markets(args):
    avail = set(markets.available(db.load_env() if hasattr(db, "load_env") else {}))
    rows = []
    for code, cfg in markets.MARKETS.items():
        path = _market_db_path(code)
        rows.append({
            "code": code,
            "currency": cfg.get("currency"),
            "region": "EU" if "eu" in cfg.get("endpoint", "") else "NA",
            "is_default": code == "US",
            "kind": cfg.get("kind", "merch"),
            "label": cfg.get("label", code),
            "has_data": db.has_data(path),
        })
    out({"markets": rows, "current": markets.current()})


def cmd_metrics(args):
    mkt = markets.current()
    cur = db.connect(ro=True).cursor()
    latest, prev = _latest_two_dates(cur)
    if not latest:
        return out({"market": mkt, "empty": True})

    # trailing-30 headline (campaign_perf latest snapshot = rolling ~30d, stable/trustworthy)
    r = cur.execute("""SELECT SUM(cost),SUM(sales),SUM(orders),SUM(clicks)
                       FROM campaign_perf WHERE date=?""", (latest,)).fetchone()
    t_cost, t_sales, t_ord, t_clk = [x or 0 for x in r]
    trailing = {"spend": round(t_cost, 2), "sales": round(t_sales, 2), "orders": t_ord,
                "clicks": t_clk, "acos": _acos(t_cost, t_sales), "cvr": _cvr(t_ord, t_clk),
                "as_of": latest}

    # daily + mtd (true single-day / month-to-date, from period_totals; daily is lagged)
    def period(p):
        row = db.get_period_total(cur.connection, p) if hasattr(db, "get_period_total") else None
        if not row:
            return None
        window, cost, sales, orders = row[0], row[1], row[2], row[3]
        return {"window": window, "spend": round(cost, 2), "sales": round(sales, 2),
                "orders": orders, "acos": _acos(cost, sales)}
    daily = period("daily")
    if daily:
        daily["settling"] = True     # freshest day under-attributed — flag it
    mtd = period("mtd")

    # trend (rolling-trailing series) — last 30 pull dates
    trend = [{"date": d, "spend": round(c or 0, 2), "sales": round(s or 0, 2),
              "acos": _acos(c or 0, s or 0)}
             for d, c, s in cur.execute(
                 """SELECT date,SUM(cost),SUM(sales) FROM campaign_perf
                    GROUP BY date ORDER BY date DESC LIMIT 30""")][::-1]

    # movers vs previous snapshot (by sales delta)
    movers = []
    if prev:
        pv = {r[0]: (r[1] or 0) for r in cur.execute(
            "SELECT campaign_name,sales FROM campaign_perf WHERE date=?", (prev,))}
        for name, s in cur.execute("SELECT campaign_name,sales FROM campaign_perf WHERE date=?", (latest,)):
            d = (s or 0) - pv.get(name, 0)
            if abs(d) >= 1:
                movers.append({"campaign": name, "delta": round(d, 2)})
        movers.sort(key=lambda m: abs(m["delta"]), reverse=True)
        movers = movers[:8]

    # calendar month + YTD from banked daily history (for the menu bar / headline)
    months, ytd, coverage = _monthly_rows(cur)
    month = months[-1] if months else None

    out({"market": mkt, "currency": markets.cfg(mkt).get("currency"),
         "trailing30": trailing, "daily": daily, "mtd": mtd, "trend": trend,
         "movers": movers, "month": month, "ytd": ytd, "coverage": coverage})


def _monthly_rows(cur, market=None):
    """Calendar-month aggregates + YTD from daily_totals (true per-day history).
    Returns (months, ytd, coverage) — all None-safe when nothing is banked.

    The month rows are daily-only on purpose: a calendar month either has
    banked days or it doesn't. Only YTD reaches further back, and it does so
    through _ytd_totals so it cannot drift from the dashboard's figure."""
    rows = cur.execute(
        """SELECT substr(date,1,7) ym, SUM(cost), SUM(sales), SUM(orders), COUNT(*)
           FROM daily_totals GROUP BY ym ORDER BY ym""").fetchall()
    if not rows:
        return [], None, None
    months = [{"month": ym, "spend": round(c or 0, 2), "sales": round(s or 0, 2),
               "orders": o or 0, "acos": _acos(c or 0, s or 0), "days_banked": n}
              for ym, c, s, o, n in rows]
    first, last = cur.execute("SELECT MIN(date), MAX(date) FROM daily_totals").fetchone()
    ytd = _ytd_totals(market or markets.current(), cur)
    coverage = {"first_day": first, "last_day": last}
    return months, ytd, coverage


def cmd_monthly(args):
    """Per-calendar-month + year-to-date totals from banked daily history.
    Coverage starts at the first banked day (the API can't reach further back
    than ~95 days, so run backfill-daily once to fill what's reachable)."""
    mkt = markets.current()
    cur = db.connect(ro=True).cursor()
    months, ytd, coverage = _monthly_rows(cur)
    out({"market": mkt, "currency": markets.cfg(mkt).get("currency"),
         "months": months, "ytd": ytd, "coverage": coverage,
         "note": "sales use the 30-day attribution window — the newest ~2 weeks "
                 "keep growing; re-run backfill weekly to true them up"})


def cmd_backfill_daily(args):
    """Wrap backfill_daily.py (one per-day report, banks ~92 days locally)."""
    p = subprocess.run([sys.executable, _engine_script("backfill_daily.py"),
                        "--days", str(args.days)],
                       cwd=HERE, env=dict(os.environ), capture_output=True, text=True,
                       timeout=3000)
    out({"market": markets.current(), "code": p.returncode,
         "text": (p.stdout or "")[-1200:], "stderr": (p.stderr or "")[-400:]})


def cmd_campaigns(args):
    mkt = markets.current()
    cur = db.connect(ro=True).cursor()
    latest, _ = _latest_two_dates(cur)
    perf = {r[0]: r for r in cur.execute(
        """SELECT campaign_id,SUM(cost),SUM(sales),SUM(orders),SUM(clicks),SUM(impressions)
           FROM campaign_perf WHERE date=? GROUP BY campaign_id""", (latest,))}
    rows = []
    for cid, name, state, ttype, budget, strat in cur.execute(
        "SELECT campaign_id,name,state,targeting_type,daily_budget,bidding_strategy FROM campaigns"):
        kind = classify(name)
        if args.type and kind != args.type:
            continue
        if args.state and (state or "") != args.state:
            continue
        p = perf.get(cid)
        cost, sales, orders, clicks, imps = (
            (p[1] or 0, p[2] or 0, p[3] or 0, p[4] or 0, p[5] or 0) if p else (0, 0, 0, 0, 0))
        rows.append({"campaign_id": cid, "name": name, "type": kind, "state": state,
                     "targeting": ttype, "budget": budget, "bidding": strat,
                     "spend": round(cost, 2), "sales": round(sales, 2), "orders": orders,
                     "clicks": clicks, "impressions": imps,
                     "acos": _acos(cost, sales), "cvr": _cvr(orders, clicks)})
    rows.sort(key=lambda r: r["spend"], reverse=True)
    out({"market": mkt, "count": len(rows), "campaigns": rows})


def cmd_adgroups(args):
    mkt = markets.current()
    cur = db.connect(ro=True).cursor()
    latest = _latest_date(cur, "targeting_perf")
    perf = {str(r[0]): r for r in cur.execute(
        """SELECT ad_group_id,SUM(cost),SUM(sales),SUM(orders),SUM(clicks),SUM(impressions)
           FROM targeting_perf WHERE date=? GROUP BY ad_group_id""", (latest,))}
    prod = {r[0]: (r[1], r[2], r[3]) for r in cur.execute(
        "SELECT ad_group_id,asin,product_type,lifetime_sales FROM ad_group_product")}
    rows = []
    for agid, name, state, bid in cur.execute(
        "SELECT ad_group_id,name,state,default_bid FROM ad_groups WHERE campaign_id=?", (args.campaign,)):
        p = perf.get(str(agid))
        cost, sales, orders, clicks, imps = (
            (p[1] or 0, p[2] or 0, p[3] or 0, p[4] or 0, p[5] or 0) if p else (0, 0, 0, 0, 0))
        asin, ptype, life = prod.get(agid, (None, None, None))
        rows.append({"ad_group_id": agid, "name": name, "state": state, "default_bid": bid,
                     "asin": asin, "type": ptype, "lifetime_sales": life,
                     "spend": round(cost, 2), "sales": round(sales, 2), "orders": orders,
                     "clicks": clicks, "impressions": imps,
                     "acos": _acos(cost, sales), "cvr": _cvr(orders, clicks)})
    rows.sort(key=lambda r: r["spend"], reverse=True)
    out({"market": mkt, "campaign_id": args.campaign, "ad_groups": rows})


def cmd_asin(args):
    mkt = markets.current()
    asin = args.asins[0].upper()
    cur = db.connect(ro=True).cursor()
    latest = _latest_date(cur, "targeting_perf")
    rows = cur.execute(
        """SELECT agp.ad_group_id, ag.name, ag.state, ag.default_bid, c.campaign_id, c.name,
                  agp.product_type, agp.lifetime_sales
           FROM ad_group_product agp
           JOIN ad_groups ag ON ag.ad_group_id=agp.ad_group_id
           JOIN campaigns  c ON c.campaign_id=ag.campaign_id
           WHERE agp.asin=?""", (asin,)).fetchall()
    groups = []
    for agid, agname, agstate, bid, cid, cname, ptype, life in rows:
        p = cur.execute("""SELECT SUM(cost),SUM(sales),SUM(orders),SUM(clicks)
                           FROM targeting_perf WHERE date=? AND ad_group_id=?""", (latest, agid)).fetchone()
        cost, sales, orders, clicks = [x or 0 for x in p]
        groups.append({"ad_group_id": agid, "ad_group": agname, "state_cached": agstate,
                       "bid": bid, "campaign_id": cid, "campaign": cname, "type": classify(cname),
                       "spend": round(cost, 2), "sales": round(sales, 2), "orders": orders,
                       "clicks": clicks, "acos": _acos(cost, sales), "cvr": _cvr(orders, clicks)})
    out({"market": mkt, "asin": asin,
         "product_type": rows[0][6] if rows else None,
         "lifetime_sales": rows[0][7] if rows else None,
         "note": "state_cached is from the last pull — use `status` for live state",
         "ad_groups": groups})


def cmd_targets(args):
    """Targets inside one ad group (latest snapshot) + last bid change per target."""
    mkt = markets.current()
    cur = db.connect(ro=True).cursor()
    latest = _latest_date(cur, "targeting_perf")
    raw = cur.execute(
        """SELECT target_id, targeting, match_type,
                  SUM(impressions), SUM(clicks), SUM(cost), SUM(orders), SUM(sales)
           FROM targeting_perf WHERE date=? AND ad_group_id=?
           GROUP BY target_id, targeting, match_type""", (latest, args.adgroup)).fetchall()
    rows = []
    for tid, targeting, match, imps, clicks, cost, orders, sales in raw:
        imps, clicks, cost, orders, sales = [x or 0 for x in (imps, clicks, cost, orders, sales)]
        last_bid, bid_changes = None, 0
        if tid:
            for (detail,) in cur.execute(
                """SELECT detail FROM writes_log WHERE action='bid_change' AND entity_id=?
                   ORDER BY applied_at DESC""", (tid,)):
                bid_changes += 1
                if last_bid is None:
                    m = RX_BID.search(detail or "")
                    if m:
                        last_bid = float(m.group(2))
        rows.append({"target_id": tid, "targeting": targeting, "match_type": match,
                     "impressions": imps, "clicks": clicks, "spend": round(cost, 2),
                     "sales": round(sales, 2), "orders": orders,
                     "acos": _acos(cost, sales), "cvr": _cvr(orders, clicks),
                     "last_bid": last_bid, "bid_changes": bid_changes})
    rows.sort(key=lambda r: r["spend"], reverse=True)
    live = False
    if getattr(args, "live", False):
        # merge Amazon's ACTUAL current bid + state per target (one API call)
        from ads_client import AdsClient
        cid = cur.execute("SELECT campaign_id FROM ad_groups WHERE ad_group_id=?",
                          (args.adgroup,)).fetchone()
        if cid and cid[0]:
            clauses = {str(c.get("targetId")): c
                       for c in AdsClient(mkt).list_targets([cid[0]])
                       if str(c.get("adGroupId")) == str(args.adgroup)}
            for r in rows:
                clause = clauses.get(str(r["target_id"]))
                if clause:
                    r["live_bid"] = clause.get("bid")
                    r["live_state"] = clause.get("state")
            live = True
    out({"market": mkt, "ad_group_id": args.adgroup, "as_of": latest,
         "live": live, "targets": rows})


def _alltargets_rows(cur, limit=2000):
    """The Targets-tab rows: latest snapshot, spend-sorted, capped at limit+1
    so the caller can detect truncation. Each row carries campaign/ad-group/
    ASIN context and — via the pull's `targets` mirror — the entity's own bid
    (fallback: the ad-group default, flagged bid_inherited)."""
    latest = _latest_date(cur, "targeting_perf")
    camp_names = {str(r[0]): r[1] for r in cur.execute("SELECT campaign_id, name FROM campaigns")}
    ag_names = {str(r[0]): r[1] for r in cur.execute("SELECT ad_group_id, name FROM ad_groups")}
    ag_asin = {str(r[0]): r[1] for r in cur.execute("SELECT ad_group_id, asin FROM ad_group_product")}
    ag_default = {str(r[0]): r[1] for r in cur.execute("SELECT ad_group_id, default_bid FROM ad_groups")}
    try:
        mirror = {str(r[0]): r[1] for r in cur.execute("SELECT target_id, bid FROM targets")}
    except sqlite3.OperationalError:
        mirror = {}                     # ro reader on a pre-mirror DB
    raw = cur.execute(
        """SELECT target_id, targeting, match_type, campaign_id, ad_group_id,
                  SUM(impressions), SUM(clicks), SUM(cost), SUM(orders), SUM(sales)
           FROM targeting_perf WHERE date=?
           GROUP BY target_id, targeting, match_type, campaign_id, ad_group_id
           ORDER BY SUM(cost) DESC
           LIMIT ?""", (latest, limit + 1)).fetchall()
    rows = []
    for tid, targeting, match, cid, agid, imps, clicks, cost, orders, sales in raw:
        imps, clicks, cost, orders, sales = [x or 0 for x in (imps, clicks, cost, orders, sales)]
        own_bid = mirror.get(str(tid))
        rows.append({"target_id": tid, "targeting": targeting, "match_type": match,
                     "campaign_id": str(cid) if cid is not None else None,
                     "campaign": camp_names.get(str(cid), str(cid) if cid is not None else None),
                     "ad_group_id": str(agid) if agid is not None else None,
                     "ad_group": ag_names.get(str(agid)),
                     "asin": ag_asin.get(str(agid)),
                     "bid": own_bid if own_bid is not None else ag_default.get(str(agid)),
                     "bid_inherited": own_bid is None,
                     "impressions": imps, "clicks": clicks, "spend": round(cost, 2),
                     "sales": round(sales, 2), "orders": orders,
                     "acos": _acos(cost, sales), "cvr": _cvr(orders, clicks)})
    return rows


def _alltargets_total(cur, latest):
    """How many targets the snapshot holds in all — the same grouping the rows
    above use, counted rather than fetched. Measured at 0.08s over the live US
    snapshot (50,822 targets), so the honest total costs nothing."""
    return cur.execute(
        """SELECT COUNT(*) FROM (
               SELECT 1 FROM targeting_perf WHERE date=?
               GROUP BY target_id, targeting, match_type, campaign_id, ad_group_id)""",
        (latest,)).fetchone()[0]


def cmd_alltargets(args):
    """Account-wide flat list of EVERY target (latest snapshot) across all campaigns —
    MerchDash's Targets tab. Spend-sorted, capped. Each row carries its campaign +
    ad-group + ASIN context so the app can show and deep-link them.

    `count` is the TRUE total and `returned` is what this reply carries — the
    same split the accumulated-* commands were fixed to make. `count` used to be
    computed AFTER the slice, so it was always min(total, limit): the Targets tab
    said "top 2000 by spend" and nothing in the reply could say how many targets
    sat beyond the cap.
    """
    mkt = markets.current()
    cur = db.connect(ro=True).cursor()
    limit = int(getattr(args, "limit", 2000) or 2000)
    latest = _latest_date(cur, "targeting_perf")
    rows = _alltargets_rows(cur, limit=limit)
    truncated = len(rows) > limit
    rows = rows[:limit]
    out({"market": mkt, "as_of": latest,
         "count": _alltargets_total(cur, latest),
         "returned": len(rows), "truncated": truncated, "targets": rows})


def cmd_searchterms(args):
    """Search terms that hit one ad group (latest snapshot), spend-sorted."""
    mkt = markets.current()
    cur = db.connect(ro=True).cursor()
    latest = _latest_date(cur, "search_term_perf")
    rows = []
    for term, targeting, match, imps, clicks, cost, orders, sales in cur.execute(
        """SELECT search_term, targeting, match_type,
                  SUM(impressions), SUM(clicks), SUM(cost), SUM(orders), SUM(sales)
           FROM search_term_perf WHERE date=? AND ad_group_id=?
           GROUP BY search_term, targeting, match_type""", (latest, args.adgroup)):
        imps, clicks, cost, orders, sales = [x or 0 for x in (imps, clicks, cost, orders, sales)]
        rows.append({"search_term": term, "targeting": targeting, "match_type": match,
                     "impressions": imps, "clicks": clicks, "spend": round(cost, 2),
                     "sales": round(sales, 2), "orders": orders,
                     "acos": _acos(cost, sales), "cvr": _cvr(orders, clicks)})
    rows.sort(key=lambda r: r["spend"], reverse=True)
    rows = rows[:int(args.limit)]
    out({"market": mkt, "ad_group_id": args.adgroup, "as_of": latest, "search_terms": rows})


def cmd_bidhistory(args):
    cur = db.connect(ro=True).cursor()
    hist = []
    for ts, detail, prev in cur.execute(
        """SELECT applied_at,detail,prev_state FROM writes_log
           WHERE action='bid_change' AND entity_id=? ORDER BY applied_at""", (args.target,)):
        detail = db.detail_prefix(detail)
        m = RX_BID.search(detail or "")
        old = float(m.group(1)) if m else (float(prev) if prev else None)
        new = float(m.group(2)) if m else None
        reason = re.sub(r"^snap=\S+\s*", "", detail or "")
        reason = re.sub(r"^[0-9.]+->[0-9.]+\s*", "", reason).strip("() ")
        hist.append({"at": ts, "old": old, "new": new, "reason": reason})
    out({"target_id": args.target, "changes": hist})


def _history_basis(conn, entity_id, column="target_id", table="target_daily"):
    """'daily' when the per-day table carries at least TWO days for this
    entity, else 'trailing30_snapshot'. The two series look identical on a
    chart and mean very different things, so the app has to be told which it
    is drawing.

    Two days, not one, because both chart call sites need more than one point
    to draw a line. A target with a single banked day used to win this contest
    and then render as "not enough history", losing the snapshot series it
    would otherwise have shown — on the live US DB that silently blanked 18,062
    of 67,763 charts. A one-point daily series is unchartable either way, so
    falling back can only add information.
    """
    try:
        days = conn.execute(
            f"""SELECT COUNT(*) FROM (SELECT DISTINCT date FROM {table}
                                       WHERE {column}=? LIMIT 2)""",
            (str(entity_id),)).fetchone()[0]
    except sqlite3.OperationalError:
        return "trailing30_snapshot"        # table absent on an old DB
    return "daily" if days >= 2 else "trailing30_snapshot"


def cmd_history(args):
    """Dated performance series for ONE campaign / ad group / target.

    Prefers TRUE per-day rows (target_daily for targets and ad groups,
    campaign_daily for campaigns) whenever they give at least two points.
    Otherwise falls back to the banked nightly snapshots, where each point is
    that day's trailing-30 aggregate — drift over time rather than per-day
    numbers. `basis` says which, because the two look the same on a chart.
    """
    mkt = markets.current()
    conn = db.connect(ro=True)
    cur = conn.cursor()
    if args.campaign:
        entity, eid = "campaign", str(args.campaign)
        basis = _history_basis(cur, eid, column="campaign_id", table="campaign_daily")
        sql = ("""SELECT date, SUM(impressions), SUM(clicks), SUM(cost),
                         SUM(orders), SUM(sales)
                    FROM campaign_daily WHERE campaign_id=? GROUP BY date ORDER BY date"""
               if basis == "daily" else
               """SELECT date, SUM(impressions), SUM(clicks), SUM(cost),
                         SUM(orders), SUM(sales)
                    FROM campaign_perf WHERE campaign_id=? GROUP BY date ORDER BY date""")
    elif args.adgroup:
        entity, eid = "ad_group", str(args.adgroup)
        basis = _history_basis(cur, eid, column="ad_group_id")
        sql = ("""SELECT date, SUM(impressions), SUM(clicks), SUM(cost),
                         SUM(orders), SUM(sales)
                    FROM target_daily WHERE ad_group_id=? GROUP BY date ORDER BY date"""
               if basis == "daily" else
               """SELECT date, SUM(impressions), SUM(clicks), SUM(cost),
                         SUM(orders), SUM(sales)
                    FROM targeting_perf WHERE ad_group_id=? GROUP BY date ORDER BY date""")
    elif args.target:
        entity, eid = "target", str(args.target)
        basis = _history_basis(cur, eid)
        sql = ("""SELECT date, SUM(impressions), SUM(clicks), SUM(cost),
                         SUM(orders), SUM(sales)
                    FROM target_daily WHERE target_id=? GROUP BY date ORDER BY date"""
               if basis == "daily" else
               """SELECT date, SUM(impressions), SUM(clicks), SUM(cost),
                         SUM(orders), SUM(sales)
                    FROM targeting_perf WHERE target_id=? GROUP BY date ORDER BY date""")
    else:
        err("pass one of --campaign / --adgroup / --target")
    points = []
    for d, imps, clicks, cost, orders, sales in cur.execute(sql, (eid,)):
        imps, clicks, cost, orders, sales = [x or 0 for x in (imps, clicks, cost, orders, sales)]
        points.append({"date": d, "impressions": imps, "clicks": clicks,
                       "spend": round(cost, 2), "sales": round(sales, 2), "orders": orders,
                       "acos": _acos(cost, sales), "cvr": _cvr(orders, clicks)})
    note = ("true per-day totals" if basis == "daily" else
            "points are trailing-30 snapshots per pull date, not per-day totals")
    # basis says WHICH series this is; these say how much of it there is.
    # A thinly-banked target (say two days of target_daily) still returns
    # basis: "daily" honestly, but a chart drawing just those two points
    # needs to know its own span rather than imply months of history.
    dates = [p["date"] for p in points]
    out({"market": mkt, "entity": entity, "id": eid, "basis": basis,
         "note": note, "days_banked": len(dates),
         "first": dates[0] if dates else None,
         "last": dates[-1] if dates else None,
         "points": points})


def cmd_negatives(args):
    """Negative-exact keywords already applied to one ad group — from writes_log,
    i.e. the engine's/app's own writes (negatives created by hand in the Amazon
    console before this tool existed aren't known locally)."""
    mkt = markets.current()
    cur = db.connect(ro=True).cursor()
    seen = {}
    for at, term, res in cur.execute(
        """SELECT applied_at, detail, result FROM writes_log
           WHERE action='add_negative' AND entity_id=? ORDER BY applied_at""",
        (str(args.adgroup),)):
        term = db.detail_prefix(term)
        if term:
            seen[term.strip().lower()] = {"term": term, "at": at, "result": res}
    negs = sorted(seen.values(), key=lambda r: r["at"], reverse=True)
    out({"market": mkt, "ad_group_id": str(args.adgroup), "count": len(negs),
         "negatives": negs})


def cmd_daily(args):
    """True per-day account totals from daily_totals (banked by daily_metrics /
    backfill_daily) — the honest day-by-day view the monthly rollup is built from."""
    mkt = markets.current()
    conn = db.connect(ro=True)
    try:
        rows = conn.execute(
            "SELECT date, cost, sales, orders FROM daily_totals ORDER BY date DESC LIMIT ?",
            (int(args.days),)).fetchall()
    except sqlite3.OperationalError:
        rows = []   # market DB predates daily banking
    days = [{"date": d, "spend": round(c or 0, 2), "sales": round(s or 0, 2),
             "orders": o or 0, "acos": _acos(c or 0, s or 0)}
            for d, c, s, o in reversed(rows)]
    out({"market": mkt, "currency": markets.cfg(mkt).get("currency"), "days": days})


def cmd_killlist(args):
    mkt = markets.current()
    conn = db.connect(ro=True)
    cur = conn.cursor()
    latest = _latest_date(cur, "targeting_perf")
    if not latest:
        # No snapshot means nothing was EVALUATED, and that is a different
        # sentence from "no design is below the CVR floor". The two replies used
        # to differ only in the `skipped` counters, which are non-zero today by
        # luck — a US day with nothing in transition and no cohort groups would
        # have made them byte-identical. Every sibling read (`stale`,
        # `negatives-preview`, `harvest-prune`) already says `as_of: null` here.
        out({"market": mkt, "cvr_floor": FLOOR_CVR, "as_of": None,
             "evaluated": 0, "count": 0, "designs": [],
             "skipped": {"transition": 0, "unknown_price": 0, "cohort": 0,
                         "cross_sell": 0},
             "spared": [],
             "note": "no targeting snapshot banked yet — nothing was evaluated"})
        return
    prod = {r[0]: (r[1], r[2]) for r in cur.execute(
        "SELECT ad_group_id,asin,product_type FROM ad_group_product")}
    states = dict(cur.execute("SELECT ad_group_id,state FROM ad_groups").fetchall())
    be_for = _design_be_for(conn)
    if be_for is None:
        out({"market": mkt, "cvr_floor": FLOOR_CVR, "as_of": latest,
             "evaluated": 0, "count": 0, "designs": [],
             "econ": "unavailable — run a pull/map once to migrate the economics tables"})
        return
    # A bleeding design whose ad drives enough owned cross-sell royalty to cover
    # its own spend is SPARED, not killed — pausing it would kill the catalogue
    # sales its ad creates. Same threshold and helper as phase 2's auto-pause.
    cross_map = cross_sell.owned_cross_sell_royalty(conn, mkt)
    rows = []
    spared = []
    evaluated = 0
    skipped = {"transition": 0, "unknown_price": 0, "cohort": 0, "cross_sell": 0}
    for agid, cost, sales, orders, clicks in cur.execute(
        """SELECT ad_group_id,SUM(cost),SUM(sales),SUM(orders),SUM(clicks)
           FROM targeting_perf WHERE date=? GROUP BY ad_group_id HAVING SUM(clicks)>=15""", (latest,)):
        evaluated += 1
        asin, ptype = prod.get(agid, (None, None))
        cost, sales, orders, clicks = cost or 0, sales or 0, orders or 0, clicks or 0
        cvr = _cvr(orders, clicks)
        acos = _acos(cost, sales)
        be, skip = be_for(agid)
        if skip in skipped:
            skipped[skip] += 1
            continue                      # no per-design profitability claim
        over = (acos is not None and be is not None and acos > be)
        if cvr is not None and cvr < FLOOR_CVR and over:
            if cross_sell.spares_pause(cross_map, agid, cost):
                skipped["cross_sell"] += 1
                xs = cross_map[str(agid)]
                spared.append({"asin": asin, "ad_group_id": agid, "type": ptype,
                               "state": states.get(agid), "spend": round(cost, 2),
                               "cross_sell_royalty": xs["royalty"],
                               "owned_units": xs["owned_units"],
                               "others": xs["others"][:10]})
                continue
            rows.append({"asin": asin, "ad_group_id": agid, "type": ptype,
                         "state": states.get(agid), "clicks": clicks, "orders": orders,
                         "cvr": cvr, "spend": round(cost, 2), "sales": round(sales, 2),
                         "acos": acos, "break_even": be,
                         "trace": [
                             _cond("cvr < floor", cvr, FLOOR_CVR, cvr < FLOOR_CVR),
                             _cond("acos > break_even", acos, be,
                                   acos is not None and be is not None and acos > be),
                         ]})
    rows.sort(key=lambda r: r["spend"], reverse=True)
    spared.sort(key=lambda r: r["cross_sell_royalty"], reverse=True)
    out({"market": mkt, "cvr_floor": FLOOR_CVR, "as_of": latest,
         "evaluated": evaluated, "count": len(rows), "designs": rows,
         "skipped": skipped, "spared": spared})


def _bidreport_data(cur, days=7):
    """The what-moved report. Enrichment looks up ONLY the changed target ids
    — first in the `targets` mirror (knows every target), falling back to the
    latest targeting_perf snapshot. It used to SELECT DISTINCT over the FULL
    targeting_perf history (~1.7M rows in the US DB): 4 seconds of scan to
    label a few dozen changes."""
    import datetime
    since = (datetime.datetime.now() - datetime.timedelta(days=int(days))).isoformat(timespec="seconds")
    rows = cur.execute(
        """SELECT applied_at, entity_id, detail, prev_state FROM writes_log
           WHERE action='bid_change' AND applied_at>=? ORDER BY applied_at DESC""",
        (since,)).fetchall()
    ids = sorted({str(r[1]) for r in rows})

    def lookup(sql_tpl):
        found = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            ph = ",".join("?" * len(chunk))
            found.update({str(r[0]): (str(r[1]) if r[1] is not None else None, r[2])
                          for r in cur.execute(sql_tpl.format(ph=ph), chunk)})
        return found

    try:
        tmap = lookup("SELECT target_id, ad_group_id, text FROM targets"
                      " WHERE target_id IN ({ph})")
    except sqlite3.OperationalError:
        tmap = {}                        # ro reader on a pre-mirror DB
    missing = [i for i in ids if i not in tmap]
    if missing:
        latest = _latest_date(cur, "targeting_perf")
        if latest:
            ids = missing
            tmap.update(lookup(
                f"SELECT target_id, ad_group_id, targeting FROM targeting_perf"
                f" WHERE date='{latest}' AND target_id IN ({{ph}})"))

    agids = sorted({v[0] for v in tmap.values() if v[0]})
    prod = {}
    for i in range(0, len(agids), 500):
        chunk = agids[i:i + 500]
        ph = ",".join("?" * len(chunk))
        prod.update(dict(cur.execute(
            f"SELECT ad_group_id, asin FROM ad_group_product"
            f" WHERE ad_group_id IN ({ph})", chunk)))

    changes, ups, downs, delta_sum = [], 0, 0, 0.0
    for at, eid, detail, prev in rows:
        detail = db.detail_prefix(detail)
        m = RX_BID.search(detail or "")
        old = float(m.group(1)) if m else (float(prev) if prev not in (None, "", "?") else None)
        new = float(m.group(2)) if m else None
        delta = round(new - old, 2) if (old is not None and new is not None) else None
        if delta is not None:
            delta_sum += delta
            if delta > 0:
                ups += 1
            elif delta < 0:
                downs += 1
        agid, targeting = tmap.get(str(eid), (None, None))
        reason = re.sub(r"^snap=\S+\s*", "", detail or "")
        reason = re.sub(r"^[0-9.]+->[0-9.]+\s*", "", reason).strip("() ")
        changes.append({"at": at, "target_id": str(eid), "old": old, "new": new,
                        "delta": delta, "reason": reason, "ad_group_id": agid,
                        "targeting": targeting, "asin": prod.get(agid)})
    # There was a `"held": 0` here, hardcoded and never set by anything. It read
    # as "changes the engine held back", which is a real thing the conflict
    # guard and the volume cap both do — and this function cannot report it,
    # because `writes_log` records what was WRITTEN and a held change never
    # reaches it. A field that is structurally always zero is worse than a
    # missing one: it answers a question it cannot see.
    return {"days": int(days), "ups": ups, "downs": downs,
            "net_delta": round(delta_sum, 2), "count": len(changes),
            "changes": changes}


def cmd_bidreport(args):
    """Bid changes in the last N days (the weekly what-moved report): every
    writes_log bid_change with old/new/delta + reason, enriched with the
    target's ad group / ASIN where the local mirror knows it."""
    cur = db.connect(ro=True).cursor()
    out({"market": markets.current(), **_bidreport_data(cur, days=int(args.days))})


def _harvest_winners(conn):
    """Pure winner-row assembly for `harvest`, shared with tests. Each row gets
    `needs_design`: True when the winner is not promoted AND its source ad
    group is a cohort (asin IS NULL in ad_group_product) — the app routes
    those into the "Needs a design" section instead of auto-promote."""
    cur = conn.cursor()
    cohort = {str(r[0]) for r in cur.execute(
        "SELECT ad_group_id FROM ad_group_product WHERE asin IS NULL")}
    rows = []
    for term, agid, kind, ptype, cid, clicks, orders, sales, acos, cpc, first, last, promoted in cur.execute(
        """SELECT search_term, source_ad_group_id, kind, product_type, source_campaign_id,
                  clicks, orders, sales, acos, cpc, first_seen, last_seen, promoted
           FROM harvest_log ORDER BY promoted, sales DESC"""):
        w = {"search_term": term, "source_ad_group_id": agid, "kind": kind,
             "type": ptype, "source_campaign_id": cid,
             "clicks": clicks or 0, "orders": orders or 0,
             "sales": round(sales or 0, 2), "acos": acos, "cpc": cpc,
             "first_seen": first, "last_seen": last, "promoted": bool(promoted)}
        w["needs_design"] = (not w["promoted"]) and (str(agid) in cohort)
        rows.append(w)
    return rows


def cmd_harvest(args):
    """Winning search terms the harvester collected — promoted ones and the queue
    still waiting. Promotion itself = phase4/phase4b (see `run --phase`)."""
    mkt = markets.current()
    conn = db.connect(ro=True)
    rows = _harvest_winners(conn)
    pending = sum(1 for r in rows if not r["promoted"])
    out({"market": mkt, "count": len(rows), "pending": pending, "winners": rows})


def cmd_harvest_suggest(args):
    """Ranked whole-catalogue design suggestions for a cohort winner (read-only)."""
    conn = db.connect(ro=True)
    rows = harvest_suggest.suggest(conn, args.term, limit=args.limit)
    out({"term": args.term, "count": len(rows), "suggestions": rows})


STALE_MIN_IMPRESSIONS = 1000   # visible enough that "nobody clicks" means something


def cmd_stale(args):
    """Stale designs: ENABLED, zero lifetime sales, plenty of impressions in the
    trailing-30 snapshot but (almost) no clicks — Amazon shows them, shoppers skip
    them. Candidates to pause or redesign. (Zero-impression long-tail is NOT
    listed: that's normal for lottery discovery, not staleness.)"""
    mkt = markets.current()
    cur = db.connect(ro=True).cursor()
    latest = _latest_date(cur, "targeting_perf")
    perf = {r[0]: (r[1] or 0, r[2] or 0, r[3] or 0, r[4] or 0, r[5] or 0)
            for r in cur.execute(
        """SELECT ad_group_id, SUM(impressions), SUM(clicks), SUM(cost), SUM(orders), SUM(sales)
           FROM targeting_perf WHERE date=? GROUP BY ad_group_id""", (latest,))}
    prod = {r[0]: (r[1], r[2], r[3] or 0) for r in cur.execute(
        "SELECT ad_group_id, asin, product_type, lifetime_sales FROM ad_group_product")}
    rows = []
    for agid, name, state in cur.execute("SELECT ad_group_id, name, state FROM ad_groups"):
        if state != "ENABLED":
            continue
        asin, ptype, life = prod.get(agid, (None, None, 0))
        if life:
            continue                      # proven design, not stale
        imps, clicks, cost, orders, sales = perf.get(agid, (0, 0, 0, 0, 0))
        if orders > 0 or clicks > 2 or imps < STALE_MIN_IMPRESSIONS:
            continue                      # traction, or not visible enough to judge
        rows.append({"ad_group_id": agid, "name": name, "asin": asin, "type": ptype,
                     "impressions": imps, "clicks": clicks, "spend": round(cost, 2)})
    rows.sort(key=lambda r: r["impressions"], reverse=True)
    out({"market": mkt, "as_of": latest, "min_impressions": STALE_MIN_IMPRESSIONS,
         "count": len(rows), "designs": rows[:500]})


def cmd_halo(args):
    """Organic-halo estimate for EVERY advertised design (US-only). Windows the dated
    Merch SALES_REPORT to each design's own ad-serving period and estimates the
    INCREMENTAL organic lift over its own pre-ad baseline — the lift the ad-attributed
    view cannot see, because the Ads API reports ad-attributed sales only.

    Ad facts are summed per DESIGN across every ad group advertising it, from the true
    per-day `target_daily` rows, so it spans lottery, scavenger, standard and harvested
    campaigns alike. halo_est is an UPPER BOUND (correlational, not causal); see halo.py.
    Non-US markets return supported:false — the Merch sales report is US-only."""
    import halo
    res = halo.analyze(min_spend=float(args.min_spend), limit=int(args.limit))
    if res is None:
        out({"market": markets.current(), "supported": False,
             "reason": "the Merch sales report is US-only", "designs": []})
        return
    if "error" in res:
        # Every one of these is a STATE — nothing banked, no sales report, no
        # overlap — not a fault. Every sibling read (killlist, stale, harvest,
        # negatives-preview) answers a folder with no data with an empty shape,
        # and the screen already knows how to draw `supported: false` with a
        # reason, because that is how a non-US market is answered.
        out({"market": markets.current(), "supported": False,
             "reason": res["error"], "count": 0, "returned": 0,
             "designs": []})
        return
    res["supported"] = True
    res["note"] = ("halo_est is an upper-bound estimate (correlational, not causal); "
                   "compare served designs against the never-served control")
    out(res)


def _sales_report_status():
    """What the engine currently has for organic royalty, or None if nothing.

    The dated Merch SALES_REPORT is the only source that sees organic sales —
    the Ads API reports ad-attributed sales only. The organic halo
    and TRAZ all read it."""
    import datetime
    import traz
    path = traz.sales_report_path()
    if not path:
        return None
    rows = traz.load_sales_rows(path)
    us = [r for r in rows if r["mkt"] == ".com"]
    span = traz.sales_report_range(path)
    start = min((r["date"] for r in rows), default=None)
    end = max((r["date"] for r in rows), default=None)
    # The filename's range is what ranks the files; the rows are what is
    # actually covered. Report both so a mislabelled file is visible.
    age = (datetime.date.today() - end).days if end else None
    return {
        "path": path,
        "filename": os.path.basename(path),
        "folder": os.path.dirname(path),
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "named_start": span[0].isoformat() if span else None,
        "named_end": span[1].isoformat() if span else None,
        "rows": len(rows),
        "us_rows": len(us),
        "asins": len({r["asin"] for r in rows}),
        "age_days": age,
        # Amazon posts royalties with a lag, so a couple of weeks is normal; a
        # month means the halo and candidate figures are quietly out of date.
        "stale": age is not None and age > 30,
    }


def _sales_report_name(start, end):
    """Amazon's own naming: SALES_REPORT-<M_D_YY>-<M_D_YY>.csv."""
    def part(d):
        return f"{d.month}_{d.day}_{d.year % 100:02d}"
    return f"SALES_REPORT-{part(start)}-{part(end)}.csv"


def cmd_sales_history(args):
    """What ORGANIC history is banked, and every file that built it.

    The per-file view (`sales-report`) answers "what did I last import?". This
    answers "what does the engine actually know?" — which is the question that
    matters, because halo and TRAZ read the accumulated union,
    not the newest file. `gaps` is the honest part: royalty summed across a
    half-covered window reads as a slump rather than as missing data."""
    import sales_import
    conn = db.connect_shared(ro=True)
    try:
        cov = sales_import.coverage(conn)
        log = [{"kind": r[0], "filename": r[1], "imported_at": r[2],
                "period_start": r[3], "period_end": r[4],
                "rows_in_file": r[5], "rows_banked": r[6], "note": r[7]}
               for r in db.imported_file_log(conn, kind=sales_import.KIND)]
    except sqlite3.OperationalError:
        return out({"banked": False,
                    "note": "nothing banked yet — import a Merch sales report"})
    finally:
        conn.close()
    return out({"banked": cov["days"] > 0, "coverage": cov, "imports": log,
                "note": "account-wide: one report covers every marketplace, so "
                        "rows are banked once with their `mkt` and read by every "
                        "market. Re-importing a report is idempotent."})


def cmd_sales_report(args):
    """The Merch sales report that gives the engine ORGANIC royalty (the Ads API
    only reports ad-attributed sales). No --import: report what is on hand.
    With --import PATH: validate the file, name it after the period it actually
    covers, and copy it into the POD folder where the engine reads it."""
    import shutil
    import traz
    if getattr(args, "import_path", None):
        src = os.path.abspath(os.path.expanduser(args.import_path))
        if not os.path.exists(src):
            err(f"no such file: {src}")
        rows = traz.load_sales_rows(src)
        if not rows:
            err("that CSV has no Merch sales rows — a sales report needs the "
                "Mkt, Date, ASIN, Purchased, Returned, Royalties and Revenue columns")
        start = min(r["date"] for r in rows)
        end = max(r["date"] for r in rows)
        # BANK the rows before touching the folder. Keeping the file was never
        # the point — the engine used to read whichever report was newest, so
        # importing a fresh one hid every earlier period. Accumulated per day,
        # each import ADDS history instead of replacing it.
        banked = None
        try:
            import sales_import
            banked = sales_import.bank(src)
        except Exception as e:                     # never fail the import on this
            banked = {"error": f"{type(e).__name__}: {e}"}
        # Named from the rows, not from whatever the file was called, so the
        # newest-report ranking always has a range to read.
        dest = os.path.join(os.path.dirname(HERE), _sales_report_name(start, end))
        copied = os.path.abspath(src) != os.path.abspath(dest)
        if copied:
            shutil.copy2(src, dest)
        status = _sales_report_status()
        # Importing an older report is allowed but changes nothing: the engine
        # always reads the newest one. Say so rather than reporting success and
        # leaving the caller to assume the figures just moved.
        active = status and os.path.abspath(status["path"]) == os.path.abspath(dest)
        out({"imported": True, "copied": copied, "source": src,
             "file": {"filename": os.path.basename(dest),
                      "start": start.isoformat(), "end": end.isoformat(),
                      "rows": len(rows)},
             "is_newest": bool(active),
             "banked": banked,
             "report": status})
        return
    status = _sales_report_status()
    if not status:
        out({"imported": False, "report": None,
             "folder": os.path.dirname(HERE),
             "note": "no SALES_REPORT-*.csv in the POD folder — organic royalty "
                     "is unavailable, so the organic-halo estimate cannot run"})
        return
    out({"imported": False, "report": status})


def cmd_demandfeed(args):
    """The MerchPirate demand feed for this market (keyword seeds to design for +
    proven sellers to make variations of). Reads the nightly JSON; --refresh
    regenerates it first (read-only, quick)."""
    mkt = markets.current()
    path = os.path.join(HERE, "outputs",
                        "demand_feed.json" if mkt == "US" else f"demand_feed_{mkt}.json")
    if args.refresh or not os.path.exists(path):
        p = subprocess.run([sys.executable, _engine_script("demand_feed.py")],
                           cwd=HERE, env=dict(os.environ), capture_output=True, text=True,
                           timeout=600)
        # A failed refresh is a failed refresh. This only reported one when
        # there was NO file to fall back on, so a crash while regenerating quietly
        # served yesterday's feed under a successful envelope — and the operator
        # had just pressed Refresh, which is the moment they are least likely to
        # doubt what they are looking at.
        if p.returncode != 0:
            detail = (p.stderr or p.stdout or "").strip()
            if detail:
                print(detail, file=sys.stderr)   # diagnostics, never the envelope
            reason = _child_failure_reason(detail)
            err("demand_feed.py could not rebuild the feed"
                + (f": {reason}" if reason else "")
                + " — the full output is on stderr.")
    with open(path, encoding="utf-8") as fh:
        out(json.load(fh))


def _accum_latest(cur):
    """Latest targeting_perf snapshot date (cumulative trailing-30 per the repo
    convention: read this one date, SUM across entities, never across dates)."""
    row = cur.execute("SELECT MAX(date) FROM targeting_perf").fetchone()
    return row[0] if row else None


def _accum_page(rows, limit):
    """Apply the row cap HONESTLY. `count` is the true total, `returned` is what
    the caller actually got, and `truncated` says whether anything was cut.
    Before this, `count` reported the full total while `rows` was silently
    sliced — the screen's header claimed 31,814 ASINs above a 500-row table,
    and 680 ASINs that had genuinely spent money were missing with no hint.
    limit=0 (or None) means no cap."""
    total = len(rows)
    page = rows if not limit else rows[:limit]
    return {"count": total, "returned": len(page),
            "truncated": len(page) < total, "rows": page}


def _accumulated_asins(conn, limit=500, expand=None):
    """Each advertised ASIN summed across every campaign it runs in, at the latest
    snapshot. NULL-asin (multi-ASIN cohort) rows are excluded from ASIN rows."""
    cur = conn.cursor()
    latest = _accum_latest(cur)
    if expand:
        rows = []
        for cid, cname, cstate, agid, agname, imps, clk, cost, orders, sales in cur.execute(
            """SELECT t.campaign_id, c.name, c.state, t.ad_group_id, g.name,
                      SUM(t.impressions), SUM(t.clicks), SUM(t.cost), SUM(t.orders), SUM(t.sales)
                 FROM targeting_perf t
                 JOIN ad_group_product p ON p.ad_group_id = t.ad_group_id
                 LEFT JOIN campaigns c ON c.campaign_id = t.campaign_id
                 LEFT JOIN ad_groups g ON g.ad_group_id = t.ad_group_id
                WHERE t.date=? AND p.asin=?
                GROUP BY t.campaign_id, t.ad_group_id
                ORDER BY SUM(t.cost) DESC""", (latest, expand)):
            imps, clk, cost, orders, sales = imps or 0, clk or 0, cost or 0, orders or 0, sales or 0
            rows.append({"campaign_id": str(cid), "campaign": cname, "state": cstate,
                         "ad_group_id": str(agid), "ad_group": agname, "impressions": imps, "clicks": clk,
                         "spend": round(cost, 2), "orders": orders, "sales": round(sales, 2),
                         "acos": _acos(cost, sales), "cvr": _cvr(orders, clk)})
        return {"market": markets.current(), "asin": expand, "as_of": latest, "breakdown": rows}
    rows = []
    for asin, ptype, ncamp, nag, imps, clk, cost, orders, sales in cur.execute(
        """SELECT p.asin, MIN(p.product_type),
                  COUNT(DISTINCT t.campaign_id), COUNT(DISTINCT t.ad_group_id),
                  SUM(t.impressions), SUM(t.clicks), SUM(t.cost), SUM(t.orders), SUM(t.sales)
             FROM targeting_perf t
             JOIN ad_group_product p ON p.ad_group_id = t.ad_group_id
            WHERE t.date=? AND p.asin IS NOT NULL
            GROUP BY p.asin""", (latest,)):
        imps, clk, cost, orders, sales = imps or 0, clk or 0, cost or 0, orders or 0, sales or 0
        rows.append({"asin": asin, "product_type": ptype, "campaigns": ncamp, "ad_groups": nag,
                     "impressions": imps, "clicks": clk, "spend": round(cost, 2),
                     "orders": orders, "sales": round(sales, 2),
                     "acos": _acos(cost, sales), "cvr": _cvr(orders, clk)})
    rows.sort(key=lambda r: r["spend"], reverse=True)
    return {"market": markets.current(), "as_of": latest, **_accum_page(rows, limit)}


# A "keyword" is text the operator chose to bid on. The auto-targeting groups
# (close-match / loose-match / substitutes / complements, stored as match_type
# TARGETING_EXPRESSION_PREDEFINED) and product/ASIN targeting (TARGETING_EXPRESSION,
# e.g. asin="B0...") are NOT keywords — they are Amazon's generic auto targets and
# product targeting. The Accumulated Keywords rollup lists only real keyword text.
KEYWORD_MATCH_TYPES = ("BROAD", "EXACT", "PHRASE")


def _accumulated_keywords(conn, limit=500, expand=None):
    """Each keyword (text you bid on) summed by (targeting, match_type) across every
    campaign/ad group, at the latest snapshot. Auto-targeting groups and product/ASIN
    targeting are excluded — see KEYWORD_MATCH_TYPES."""
    cur = conn.cursor()
    latest = _accum_latest(cur)
    if expand:
        rows = []
        for cid, cname, cstate, agid, agname, mt, imps, clk, cost, orders, sales in cur.execute(
            """SELECT t.campaign_id, c.name, c.state, t.ad_group_id, g.name, t.match_type,
                      SUM(t.impressions), SUM(t.clicks), SUM(t.cost), SUM(t.orders), SUM(t.sales)
                 FROM targeting_perf t
                 LEFT JOIN campaigns c ON c.campaign_id = t.campaign_id
                 LEFT JOIN ad_groups g ON g.ad_group_id = t.ad_group_id
                WHERE t.date=? AND t.targeting=?
                GROUP BY t.campaign_id, t.ad_group_id, t.match_type
                ORDER BY SUM(t.cost) DESC""", (latest, expand)):
            imps, clk, cost, orders, sales = imps or 0, clk or 0, cost or 0, orders or 0, sales or 0
            rows.append({"campaign_id": str(cid), "campaign": cname, "state": cstate,
                         "ad_group_id": str(agid), "ad_group": agname, "match_type": mt,
                         "impressions": imps, "clicks": clk,
                         "spend": round(cost, 2), "orders": orders, "sales": round(sales, 2),
                         "acos": _acos(cost, sales), "cvr": _cvr(orders, clk)})
        return {"market": markets.current(), "targeting": expand, "as_of": latest, "breakdown": rows}
    rows = []
    kw_ph = ",".join("?" * len(KEYWORD_MATCH_TYPES))
    for targeting, mt, ncamp, nag, imps, clk, cost, orders, sales in cur.execute(
        f"""SELECT t.targeting, t.match_type,
                  COUNT(DISTINCT t.campaign_id), COUNT(DISTINCT t.ad_group_id),
                  SUM(t.impressions), SUM(t.clicks), SUM(t.cost), SUM(t.orders), SUM(t.sales)
             FROM targeting_perf t
            WHERE t.date=? AND t.targeting IS NOT NULL
              AND t.match_type IN ({kw_ph})
            GROUP BY t.targeting, t.match_type""", (latest, *KEYWORD_MATCH_TYPES)):
        imps, clk, cost, orders, sales = imps or 0, clk or 0, cost or 0, orders or 0, sales or 0
        rows.append({"targeting": targeting, "match_type": mt, "campaigns": ncamp, "ad_groups": nag,
                     "impressions": imps, "clicks": clk, "spend": round(cost, 2),
                     "orders": orders, "sales": round(sales, 2),
                     "acos": _acos(cost, sales), "cvr": _cvr(orders, clk)})
    rows.sort(key=lambda r: r["spend"], reverse=True)
    return {"market": markets.current(), "as_of": latest, **_accum_page(rows, limit)}


def _accum_write_csv(name, rows):
    import csv as csvmod
    mkt = markets.current()
    sfx = "" if mkt == markets.DEFAULT else f"_{mkt}"
    outdir = os.path.join(HERE, "outputs")
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"{name}{sfx}.csv")
    if rows:
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csvmod.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    return path


def _watchlist_metric_row(cur, latest, kind, pin, names):
    """Resolve one pinned entity's metrics at the latest snapshot. Returns a row
    dict with resolved=False (zeroed) when the entity has no data."""
    zero = {"impressions": 0, "clicks": 0, "spend": 0.0, "orders": 0, "sales": 0.0}
    if kind == "campaign":
        cid = str(pin.get("campaign_id"))
        # campaign_perf keeps its own (usually newer) snapshot date — `latest`
        # here is targeting_perf's, so resolve campaign_perf's separately.
        c_latest = _latest_date(cur, "campaign_perf")
        row = cur.execute(
            """SELECT SUM(impressions),SUM(clicks),SUM(cost),SUM(orders),SUM(sales)
                 FROM campaign_perf WHERE date=? AND campaign_id=?""", (c_latest, cid)).fetchone()
        rid, label = cid, names["campaigns"].get(cid, cid)
    elif kind == "adgroup":
        agid = str(pin.get("ad_group_id"))
        row = cur.execute(
            """SELECT SUM(impressions),SUM(clicks),SUM(cost),SUM(orders),SUM(sales)
                 FROM targeting_perf WHERE date=? AND ad_group_id=?""", (latest, agid)).fetchone()
        rid, label = agid, names["ad_groups"].get(agid, agid)
    elif kind == "target":
        tid = str(pin.get("target_id"))
        row = cur.execute(
            """SELECT SUM(impressions),SUM(clicks),SUM(cost),SUM(orders),SUM(sales)
                 FROM targeting_perf WHERE date=? AND target_id=?""", (latest, tid)).fetchone()
        rid = tid
        label = cur.execute("SELECT targeting FROM targeting_perf WHERE target_id=? LIMIT 1",
                            (tid,)).fetchone()
        label = label[0] if label else tid
    elif kind == "asin":
        asin = str(pin.get("asin"))
        row = cur.execute(
            """SELECT SUM(t.impressions),SUM(t.clicks),SUM(t.cost),SUM(t.orders),SUM(t.sales)
                 FROM targeting_perf t JOIN ad_group_product p ON p.ad_group_id = t.ad_group_id
                WHERE t.date=? AND p.asin=?""", (latest, asin)).fetchone()
        rid, label = asin, asin
    else:
        return {"kind": kind, "id": None, "label": "(unknown kind)", "resolved": False, **zero,
                "acos": None, "cvr": None}   # nothing was measured, so no rate
    imps, clk, cost, orders, sales = (row or (None,) * 5)
    resolved = row is not None and (clk or cost or orders or sales or imps)
    imps, clk, cost, orders, sales = imps or 0, clk or 0, cost or 0, orders or 0, sales or 0
    return {"kind": kind, "id": rid, "label": label, "resolved": bool(resolved),
            "impressions": imps, "clicks": clk, "spend": round(cost, 2),
            "orders": orders, "sales": round(sales, 2),
            "acos": _acos(cost, sales), "cvr": _cvr(orders, clk)}


def _watchlist_rows(conn, pins):
    """Resolve a list of pins into current-window metric rows + an aggregate
    summary. Unresolvable pins (deleted entities) are reported, never crash."""
    cur = conn.cursor()
    latest = _accum_latest(cur)
    names = {
        "campaigns": dict(cur.execute("SELECT campaign_id,name FROM campaigns")),
        "ad_groups": dict(cur.execute("SELECT ad_group_id,name FROM ad_groups")),
    }
    rows = [_watchlist_metric_row(cur, latest, (p.get("kind") or "").lower(), p, names)
            for p in pins]
    agg = {"impressions": 0, "clicks": 0, "spend": 0.0, "orders": 0, "sales": 0.0}
    for r in rows:
        for k in agg:
            agg[k] += r[k]
    agg["spend"] = round(agg["spend"], 2)
    agg["sales"] = round(agg["sales"], 2)
    agg["acos"] = _acos(agg["spend"], agg["sales"])
    agg["cvr"] = _cvr(agg["orders"], agg["clicks"])
    return {"market": markets.current(), "as_of": latest, "rows": rows, "summary": agg}


def _rule_src(args):
    """Rule text from --rule <name> (the store) or, failing that, stdin."""
    name = getattr(args, "rule", None)
    if name:
        import rules.store as rs
        r = rs.get_rule(name)
        if not r:
            err(f"no rule named {name!r}")
        return r["text"]
    return sys.stdin.read()


def cmd_rules_validate(args):
    """Lex+parse a rule (stdin or --rule); report syntax errors. No DB, no writes."""
    import rules.runner as rr
    out(rr.validate(_rule_src(args)))


def cmd_rules_preview(args):
    """Evaluate a rule (stdin or --rule) against this market read-only — proposed
    changes + per-condition debug traces. No writes (Layer 1)."""
    import rules.runner as rr
    conn = db.connect(ro=True)
    out(rr.preview(conn, _rule_src(args)))


def cmd_rules_list(args):
    # Scope to the selected profile's family — KDP books and Merch tees keep
    # separate rule sets, so picking KDP US never shows Merch rules.
    import rules.store as rs
    out({"rules": rs.list_rules(kind=markets.kind())})


def cmd_rules_get(args):
    import rules.store as rs
    r = rs.get_rule(args.rule)
    if not r:
        err(f"no rule named {args.rule!r}")
    out(r)


def cmd_rules_save(args):
    """Save/replace a rule. stdin JSON {name, text, enabled?, mode?, season?}.
    Validated before saving (a rule that won't parse is rejected)."""
    import rules.store as rs
    import rules.runner as rr
    payload = json.loads(sys.stdin.read())
    v = rr.validate(payload.get("text", ""))
    if not v["ok"]:
        out({"ok": False, "saved": False, "errors": v["errors"]})
        return
    try:
        r = rs.save_rule(payload["name"], payload["text"],
                         enabled=payload.get("enabled", False),
                         mode=payload.get("mode", "review"),
                         season=payload.get("season"),
                         kind=markets.kind())
    except (ValueError, KeyError) as e:
        err(str(e))
    out(r)


def cmd_rules_delete(args):
    """Delete a rule AND its pending proposals.

    The proposals used to survive the rule, so the Approval queue could offer a
    write from a rule that no longer existed and no refresh would clear it."""
    import rules.store as rs
    import rules.pending as rp
    rs.delete_rule(args.rule)
    rp.remove_rule(markets.current(), args.rule)
    out({"deleted": args.rule})


def cmd_rules_nightly(args):
    """Nightly hook (run_scheduled loops markets). ENABLED + in-season rules:
    AUTO-mode → apply now (KILL + econ-gate + cap); REVIEW-mode → queue their
    proposed changes to the pending store for the app's Approval queue (no write).
    """
    _guard_kill()
    import rules.store as rs
    import rules.runner as rr
    import rules.executor as rex
    import rules.pending as rp
    import rules.conflicts as rc
    conn = db.connect()
    mkt = markets.current()
    summary = []
    queued = 0
    collected = []
    # AUTO rules are collected FIRST and executed once, together. Executing each
    # rule as it was previewed meant two rules that both moved one target's bid
    # both wrote, and whichever ran last silently won. Collecting first lets the
    # conflict guard see across rules; rule order decides who wins, which is
    # stable, so the outcome does not depend on timing.
    auto = []
    for meta in rs.enabled_rules(kind=markets.kind(mkt)):
        if not rs.in_season(meta.get("season")):
            continue
        r = rs.get_rule(meta["name"])
        prev = rr.preview(conn, r["text"])
        if not prev["ok"]:
            summary.append({"rule": meta["name"], "ok": False, "errors": prev["errors"]})
            continue
        if meta["mode"] == "auto":
            for ch in prev["changes"]:
                ch["rule"] = meta["name"]
            auto.extend(prev["changes"])
            summary.append({"rule": meta["name"], "mode": "auto",
                            "matched": prev["matched"], "applied": 0, "skipped_conflict": 0})
        else:
            rp.set_rule(mkt, meta["name"], prev["changes"])
            collected.append(meta["name"])
            queued += len(prev["changes"])
            summary.append({"rule": meta["name"], "mode": "review",
                            "matched": prev["matched"], "queued": len(prev["changes"])})
    pruned = rp.keep_only(mkt, collected)

    total = 0
    kept, skipped = rc.resolve(auto, conn)
    by_rule = {s["rule"]: s for s in summary if s.get("mode") == "auto"}
    for ch in skipped:
        row = by_rule.get(ch.get("rule"))
        if row:
            row["skipped_conflict"] += 1
    refusal = {}
    if kept:
        res = rex.execute(conn, kept, market=mkt)
        total, refusal = _nightly_apply_summary(res, kept, by_rule, summary)
    out({"market": mkt, "rules": summary, "total_applied": total,
         "total_queued": queued, "pruned": pruned,
         "conflicts_skipped": len(skipped),
         "conflicts": [rc.describe(ch) for ch in skipped], **refusal})


def _nightly_apply_summary(res, kept, by_rule, summary):
    """(total_applied, extra fields) for one rules-nightly execute() result.

    A BLOCKED reply needs its own branch, and reading it like a normal one lies
    twice. `count` on a refusal is what was PROPOSED, so reporting it as
    total_applied claims writes that never happened — 700 pauses announced, none
    made. And `results` is empty, so `zip(kept, results)` runs zero times and the
    reason reaches no rule row at all. The nightly would have printed a large
    success with no explanation anywhere in it.

    A refusal is the loudest thing this reply can carry, so it goes to the TOP
    level as well as onto every auto rule's row.
    """
    if res.get("blocked"):
        for row in summary:
            if row.get("mode") == "auto":
                row["blocked"] = res.get("blocked")
                row["applied"] = 0
        return 0, {"blocked": res.get("blocked"),
                   "blocked_cap": res.get("cap"),
                   "blocked_proposed": res.get("count"),
                   "blocked_message": res.get("message")}
    total = res.get("count", 0)
    # Only "applied" was counted, and every other outcome vanished. A rule whose
    # forty changes were ALL refused for stale evidence, or all rejected by
    # Amazon, reported matched:40 applied:0 with no reason anywhere in the reply
    # — and the nightly's own status file then recorded the step as a success.
    # Count what did NOT happen, and say why, per rule and at the top.
    # A no-op is a healthy outcome, not a failure: the entity is already where
    # the rule wants it. Counting it here would put a number in front of the
    # operator on a night when nothing was wrong, and an alarm that fires on a
    # normal night is one that gets ignored.
    BENIGN = {"applied", "skipped_noop"}
    outcomes = {}
    for ch, r in zip(kept, res.get("results", [])):
        row = by_rule.get(ch.get("rule"))
        st = r.get("status") or "unknown"
        if st not in BENIGN:
            outcomes[st] = outcomes.get(st, 0) + 1
        if not row:
            continue
        if st == "applied":
            row["applied"] += 1
        elif st not in BENIGN:
            row.setdefault("not_applied", {})
            row["not_applied"][st] = row["not_applied"].get(st, 0) + 1
    return total, ({"not_applied": outcomes,
                    "not_applied_total": sum(outcomes.values())} if outcomes else {})


def cmd_rules_collect(args):
    """Re-evaluate every ENABLED + in-season REVIEW-mode rule and refresh the
    pending Approval queue. Read-only re: Amazon (only writes the pending store) —
    the app calls this to populate the queue on demand without applying autos."""
    import rules.store as rs
    import rules.runner as rr
    import rules.pending as rp
    conn = db.connect(ro=True)
    mkt = markets.current()
    queued = 0
    collected = []
    for meta in rs.enabled_rules(kind=markets.kind(mkt)):
        if meta["mode"] != "review" or not rs.in_season(meta.get("season")):
            continue
        r = rs.get_rule(meta["name"])
        prev = rr.preview(conn, r["text"])
        if prev["ok"]:
            rp.set_rule(mkt, meta["name"], prev["changes"])
            collected.append(meta["name"])
            queued += len(prev["changes"])
    # Drop what belongs to rules that are no longer collected — disabled,
    # switched to auto, out of season, deleted. set_rule only replaces its own
    # rule's rows, so those proposals used to sit in the queue for good.
    stale = rp.keep_only(mkt, collected)
    out({"market": mkt, "queued": queued, "pruned": stale, **rp.load(mkt)})


def cmd_rules_pending(args):
    """The Approval queue's rows, with cross-rule conflicts marked.

    Review mode keeps every proposal — the operator is the one deciding, and
    hiding one of the two options would be worse than showing both. Each
    contested row carries `conflict` naming the other rules that want the same
    entity and which one would win if applied together."""
    import rules.pending as rp
    import rules.conflicts as rc
    mkt = markets.current()
    data = rp.load(mkt)
    changes, contested = rc.annotate(data.get("changes", []), db.connect(ro=True))
    out({"market": mkt, **data, "changes": changes, "conflicts": contested})


def cmd_rules_approve(args):
    """Apply an APPROVED subset of the pending rule changes. stdin {"ids":[...]}.
    Executes via the rules executor (max-bid clamp / KILL / econ-gate / cap) and
    removes applied ids from the pending store."""
    _guard_kill()
    import rules.executor as rex
    import rules.pending as rp
    import rules.conflicts as rc
    mkt = markets.current()
    ids = json.loads(sys.stdin.read() or "{}").get("ids", [])
    changes, stale = rp.select(mkt, ids)
    if not changes:
        out({"market": mkt, "applied": 0, "results": [],
             "stale_skipped": len(stale),
             "stale": [{"id": c.get("id"), "rule": c.get("rule"),
                        "label": c.get("label"), "action": c.get("action"),
                        "args_text": c.get("args_text"),
                        "reason": c.get("stale_reason")} for c in stale],
             "note": ("every approved change came from a rule that has since "
                      "changed — re-collect and approve again"
                      if stale else "no matching pending changes")})
        return
    # Approving both sides of a conflict used to send both writes, and the last
    # one silently won. Apply the winner and hold the loser back — it stays in
    # the queue, so approving it on its own afterwards still works.
    changes, skipped = rc.resolve(changes, db.connect(ro=True))
    # A proposal whose rule was edited, disabled or deleted after it was queued
    # is not something any rule currently says. It used to be applied anyway:
    # queue setBid(0.50), edit the rule to 0.20, approve — and 0.50 went out.
    _stale_report = [{"id": c.get("id"), "rule": c.get("rule"),
                      "label": c.get("label"), "action": c.get("action"),
                      "args_text": c.get("args_text"),
                      "reason": c.get("stale_reason")} for c in stale]
    if not changes:
        out({"market": mkt, "applied": 0, "results": [], "conflicts_skipped": len(skipped),
             "conflicts": [rc.describe(c) for c in skipped],
             "note": "every approved change was outranked by another rule"})
        return
    conn = db.connect()
    # cap=0: no VOLUME cap here. That guard exists because AUTO rules apply with
    # nobody looking; every id in this call was selected in the Approval Queue,
    # so the human gate has already happened and refusing the batch would only
    # block a deliberate act.
    res = rex.execute(conn, changes, market=mkt, cap=0)
    applied_ids = [c["id"] for c, r in zip(changes, res.get("results", []))
                   if r.get("status") == "applied"]
    rp.remove(mkt, applied_ids)
    out({"market": mkt, **res, "conflicts_skipped": len(skipped),
         "conflicts": [rc.describe(c) for c in skipped]})


def cmd_rules_discard(args):
    """Drop pending rule changes. stdin {"ids":[...]} or {"all":true}."""
    import rules.pending as rp
    mkt = markets.current()
    body = json.loads(sys.stdin.read() or "{}")
    if body.get("all"):
        rp.clear(mkt)
    else:
        rp.remove(mkt, body.get("ids", []))
    out({"market": mkt, **rp.load(mkt)})


def cmd_rules_run(args):
    """Run a rule (stdin). Default = preview (read-only proposed changes + traces).
    --apply executes the changes through ads_client (max-bid clamp applies),
    KILL + econ-gate + change-cap enforced, each logged to writes_log."""
    import rules.runner as rr
    import rules.executor as rex
    src = _rule_src(args)
    if not args.apply:
        conn = db.connect(ro=True)
        out(rr.preview(conn, src))
        return
    _guard_kill()
    conn = db.connect()
    prev = rr.preview(conn, src)
    if not prev["ok"]:
        out(prev)
        return
    res = rex.execute(conn, prev["changes"], market=markets.current())
    out({"market": markets.current(), "evaluated": prev["evaluated"],
         "matched": prev["matched"], **res})


def cmd_synccal(args):
    """Per-day calendar heat-grid data (MerchDash-style): for each stored day,
    whether it was synced, how many values automation adjusted, ad spend, and
    orders. Drives the Dashboard's 4-mode contribution grid."""
    conn = db.connect(ro=True)
    # impressions/clicks/units are newer columns — read-only here, so tolerate DBs
    # that predate them (they fill in once daily_metrics/backfill next writes).
    have = {r[1] for r in conn.execute("PRAGMA table_info(daily_totals)")}
    extra = all(c in have for c in ("impressions", "clicks", "units"))
    query = ("SELECT date, cost, sales, orders, impressions, clicks, units FROM daily_totals"
             if extra else "SELECT date, cost, sales, orders, NULL, NULL, NULL FROM daily_totals")
    days = {}
    for date, cost, sales, orders, impr, clk, units in conn.execute(query):
        days[date] = {"stored": True, "spend": round(cost or 0, 2),
                      "sales": round(sales or 0, 2), "orders": orders or 0,
                      "impressions": impr, "clicks": clk, "units": units, "adjusted": 0}
    for d, n in conn.execute(
            "SELECT substr(applied_at,1,10) d, COUNT(*) FROM writes_log GROUP BY d"):
        if d:
            days.setdefault(d, {"stored": False, "spend": 0.0, "sales": 0.0, "orders": 0,
                                "impressions": None, "clicks": None, "units": None, "adjusted": 0})
            days[d]["adjusted"] = n
    rows = [{"date": d, **v} for d, v in sorted(days.items())]
    tot = {"days": sum(1 for r in rows if r["stored"]),
           "adjusted": sum(r["adjusted"] for r in rows),
           "spend": round(sum(r["spend"] for r in rows), 2),
           "orders": sum(r["orders"] for r in rows)}
    out({"market": markets.current(), "days": rows, "count": len(rows), "totals": tot})


def cmd_report(args):
    """Account rollup for ANY date range (MerchDash's Reports) from the true per-day
    daily_totals bank — totals + derived ratios + the per-day series for a chart.
    Fast/read-only; range is bounded by what's banked (see `available`)."""
    conn = db.connect(ro=True)
    cur = conn.cursor()
    have = {r[1] for r in cur.execute("PRAGMA table_info(daily_totals)")}
    extra = all(c in have for c in ("impressions", "clicks", "units"))
    cols = "date,cost,sales,orders" + (",impressions,clicks,units" if extra else ",NULL,NULL,NULL")
    start = _iso_date_arg(getattr(args, "start", None), "--start")
    end = _iso_date_arg(getattr(args, "end", None), "--end")
    where, params = "", []
    if start:
        where += " AND date>=?"; params.append(start)
    if end:
        where += " AND date<=?"; params.append(end)
    days = []
    t_spend = t_sales = 0.0
    t_orders = t_impr = t_clk = t_units = 0
    for date, cost, sales, orders, impr, clk, units in cur.execute(
            f"SELECT {cols} FROM daily_totals WHERE 1=1{where} ORDER BY date", params):
        days.append({"date": date, "stored": True, "adjusted": 0,
                     "spend": round(cost or 0, 2), "sales": round(sales or 0, 2),
                     "orders": orders or 0, "impressions": impr, "clicks": clk, "units": units})
        t_spend += cost or 0; t_sales += sales or 0; t_orders += orders or 0
        t_impr += impr or 0; t_clk += clk or 0; t_units += units or 0
    totals = {
        "spend": round(t_spend, 2), "sales": round(t_sales, 2), "orders": t_orders,
        "impressions": t_impr, "clicks": t_clk, "units": t_units,
        "acos": _acos(t_spend, t_sales),
        "roas": round(t_sales / t_spend, 3) if t_spend else None,
        "ctr": round(t_clk / t_impr, 4) if t_impr else None,
        "cpc": round(t_spend / t_clk, 2) if t_clk else None,
        "cvr": round(t_orders / t_clk, 4) if t_clk else None,
        "cpo": round(t_spend / t_orders, 2) if t_orders else None,
    }
    bounds = cur.execute("SELECT MIN(date),MAX(date) FROM daily_totals").fetchone()
    out({"market": markets.current(), "start": getattr(args, "start", None),
         "end": getattr(args, "end", None),
         "available": {"min": bounds[0], "max": bounds[1]},
         "day_count": len(days), "totals": totals, "days": days})


def cmd_campaigndaily(args):
    """Per-day metric series for the Targets chart, summed over selected campaigns
    (--campaigns id,id,...) or ALL campaigns when none given. Same day shape as
    synccal so the app reuses the metric chart. Built from the campaign_daily bank."""
    conn = db.connect(ro=True)
    cur = conn.cursor()
    have = {r[1] for r in cur.execute("PRAGMA table_info(campaign_daily)")}
    if "date" not in have:
        out({"market": markets.current(), "campaign_ids": [], "days": [], "count": 0,
             "note": "no per-campaign daily data banked yet — run backfill-daily"})
        return
    ids = [c.strip() for c in (getattr(args, "campaigns", None) or "").split(",") if c.strip()]
    where, params = "", []
    if ids:
        where = " WHERE campaign_id IN (%s)" % ",".join("?" * len(ids))
        params = ids
    rows = cur.execute(
        f"""SELECT date, SUM(cost), SUM(sales), SUM(orders),
                   SUM(impressions), SUM(clicks), SUM(units)
            FROM campaign_daily{where} GROUP BY date ORDER BY date""", params).fetchall()
    days = [{"date": d, "stored": True, "adjusted": 0,
             "spend": round(cost or 0, 2), "sales": round(sales or 0, 2),
             "orders": orders or 0, "impressions": impr, "clicks": clk, "units": units}
            for d, cost, sales, orders, impr, clk, units in rows]
    out({"market": markets.current(), "campaign_ids": ids, "days": days, "count": len(days)})


def cmd_watchlist(args):
    """Resolve pinned entities into aggregated rows. Reads pins JSON from stdin:
       {"pins":[{kind, campaign_id?, ad_group_id?, target_id?, asin?}...]}"""
    conn = db.connect(ro=True)
    raw = sys.stdin.read().strip()
    try:
        pins = (json.loads(raw).get("pins", []) if raw else [])
    except ValueError as e:
        # Name the source, the way everywhere-preview does. A bare
        # "Expecting value: line 1 column 9" says nothing about which input
        # this reply is complaining about.
        err(f"could not parse pins from stdin: {e}")
        return
    out(_watchlist_rows(conn, pins))


def cmd_accumulated_asins(args):
    conn = db.connect(ro=True)
    data = _accumulated_asins(conn, limit=int(args.limit), expand=args.expand)
    if getattr(args, "csv", False) and not args.expand:
        _accum_write_csv("accumulated_asins", data["rows"])
    out(data)


def cmd_accumulated_keywords(args):
    conn = db.connect(ro=True)
    data = _accumulated_keywords(conn, limit=int(args.limit), expand=args.expand)
    if getattr(args, "csv", False) and not args.expand:
        _accum_write_csv("accumulated_keywords", data["rows"])
    out(data)


def _mtd_profit(conn, cur, type_rows, total_roy, total_orders):
    """Current-month profit — MODELED, and labelled as such everywhere it surfaces.

    Spend and orders are EXACT: they come from `period_totals` (Amazon's own
    month-to-date report), falling back to summing `daily_totals`.

    Royalty is NOT exact and cannot be. Royalty is per DESIGN, and no per-design
    daily table exists — `campaign_daily` is the finest per-day grain there is.
    So the royalty RATE is modeled: each product type's effective royalty per
    order comes from the trailing-30 per-design calculation above (which does use
    real per-design price economics), and `campaign_daily` supplies the mix of
    which types this month's orders came from.

    campaign_daily lags daily_totals by about a day, so it is used ONLY to weight
    that mix — never to set the window. That keeps the card's period identical to
    the exact spend/orders figures beside it.

    Mirrors the trailing-30 contract: multi-ASIN cohort campaigns are held out of
    profit and reported as uncovered spend, never presented as margin.
    """
    month = (_latest_date(cur, "daily_totals") or "")[:7]
    if not month:
        return None

    # ---- exact MTD spend + orders -------------------------------------------
    window = spend = orders = None
    row = cur.execute(
        "SELECT window, cost, orders FROM period_totals WHERE period='mtd'").fetchone()
    if row and row[0] and row[0].startswith(f"{month}-01"):
        window, spend, orders = row[0], row[1] or 0, row[2] or 0
    else:                                   # no fresh MTD report — sum the days we have
        agg = cur.execute(
            "SELECT MIN(date), MAX(date), SUM(cost), SUM(orders) FROM daily_totals"
            " WHERE date LIKE ?", (f"{month}-%",)).fetchone()
        if not agg or not agg[0]:
            return None
        window, spend, orders = f"{agg[0]}→{agg[1]}", agg[2] or 0, agg[3] or 0

    # ---- per-type effective royalty per order (from the trailing-30 designs) --
    eff = {t["type"]: (t["royalty_est"] / t["orders"])
           for t in type_rows if t.get("orders")}
    blended = (total_roy / total_orders) if total_orders else 0.0
    if not blended and not eff:
        return None

    # ---- this month's type mix, and the cohort share, from campaign_daily -----
    camp_type, camp_covered = {}, {}
    for cid, ptype, asin in cur.execute(
        """SELECT a.campaign_id, p.product_type, p.asin FROM ad_groups a
           JOIN ad_group_product p ON a.ad_group_id = p.ad_group_id"""):
        if asin:                                  # single-ASIN = attributable
            camp_covered[cid] = True
            camp_type.setdefault(cid, {})
            camp_type[cid][ptype] = camp_type[cid].get(ptype, 0) + 1
        else:
            camp_covered.setdefault(cid, False)   # cohort group, no per-design royalty

    mix, cohort_orders, cohort_spend, seen_orders, seen_spend = {}, 0, 0.0, 0, 0.0
    for cid, cost, ords in cur.execute(
        """SELECT campaign_id, SUM(cost), SUM(orders) FROM campaign_daily
           WHERE date LIKE ? GROUP BY campaign_id""", (f"{month}-%",)):
        cost, ords = cost or 0, ords or 0
        seen_orders += ords
        seen_spend += cost
        if not camp_covered.get(cid):
            cohort_orders += ords
            cohort_spend += cost
            continue
        types_here = camp_type.get(cid) or {}
        dominant = max(types_here, key=types_here.get) if types_here else None
        mix[dominant] = mix.get(dominant, 0) + ords

    # Scale the exact MTD totals by the covered share campaign_daily observed.
    cov_orders_share = ((seen_orders - cohort_orders) / seen_orders) if seen_orders else 1.0
    cov_spend_share = ((seen_spend - cohort_spend) / seen_spend) if seen_spend else 1.0
    cov_orders = orders * cov_orders_share
    cov_spend = round(spend * cov_spend_share, 2)

    mix_total = sum(mix.values())
    if mix_total:
        royalty = sum((n / mix_total) * cov_orders * eff.get(t, blended)
                      for t, n in mix.items())
        basis = "per-campaign product-type mix"
    else:                                    # no per-campaign days banked yet this month
        royalty = cov_orders * blended
        basis = "blended trailing-30 royalty per order"

    royalty = round(royalty, 2)
    return {"month": month, "window": window, "modeled": True,
            "spend": cov_spend, "orders": round(cov_orders, 1),
            "royalty_est": royalty, "profit": round(royalty - cov_spend, 2),
            "uncovered_spend": round(spend - cov_spend, 2),
            "royalty_per_order": round(royalty / cov_orders, 2) if cov_orders else None,
            "basis": basis,
            "note": "Current month. Spend and orders are exact (Amazon MTD report). "
                    "Royalty is MODELED — there is no per-design daily data, so each "
                    f"type's trailing-30 royalty per order is applied via the {basis}. "
                    "Cohort campaign spend is held out, as in the trailing-30 figure."}


def cmd_crosspurchase(args):
    """MEASURED cross-purchase — which design's ads sold a DIFFERENT design.

    From the spPurchasedProduct report: a shopper clicked the ad for
    `advertised_asin` and bought `purchased_asin`. Where those differ, the sale
    is halo the campaign/targeting reports credit nowhere, so a design can look
    like it loses money while quietly selling the rest of the catalogue.

    This is Amazon's own attribution, unlike `halo`, which infers
    lift correlationally from the Merch sales report. Both are useful: this one
    is measured but ad-click-only; that one catches organic lift too.
    """
    mkt = markets.current()
    conn = db.connect(ro=True)
    cur = conn.cursor()
    try:
        latest = db.latest_snapshot(conn, "purchased_product")
    except sqlite3.OperationalError:
        return out({"market": mkt, "supported": False,
                    "note": "purchased_product not banked yet — run a pull after "
                            "the report is wired in (phase0_pull 'purchased')"})
    if not latest:
        return out({"market": mkt, "supported": False, "as_of": None,
                    "note": "no purchased-product snapshot yet — the nightly pull "
                            "banks one once the report lands"})

    names = {r[0]: r[1] for r in cur.execute("SELECT ad_group_id, name FROM ad_groups")}
    own_sales = other_sales = 0.0
    by_design, pairs = {}, {}
    for (agid, adv, pur, units, sales, purchases,
         o_units, o_sales, o_purchases) in cur.execute(
        """SELECT ad_group_id, advertised_asin, purchased_asin,
                  SUM(units_sold), SUM(sales), SUM(purchases),
                  SUM(units_sold_other_sku), SUM(sales_other_sku),
                  SUM(purchases_other_sku)
           FROM purchased_product WHERE date=?
           GROUP BY ad_group_id, advertised_asin, purchased_asin""", (latest,)):
        same = (adv == pur)
        # Amazon puts the value of a not-advertised purchase in the *_other_sku
        # columns; the plain sales/purchases columns describe the ADVERTISED
        # ASIN and are 0 on these rows. Reading the wrong pair reports every
        # cross-sell as worth nothing.
        if same:
            sales, units, purchases = sales or 0, units or 0, purchases or 0
        else:
            sales = o_sales or sales or 0
            units = o_units or units or 0
            purchases = o_purchases or purchases or 0
        if same:
            own_sales += sales
        else:
            other_sales += sales
        d = by_design.setdefault(adv, {"advertised_asin": adv, "ad_group_id": agid,
                                       "ad_group": names.get(agid), "own_sales": 0.0,
                                       "other_sales": 0.0, "other_units": 0,
                                       "distinct_others": 0})
        if same:
            d["own_sales"] = round(d["own_sales"] + sales, 2)
        else:
            d["other_sales"] = round(d["other_sales"] + sales, 2)
            d["other_units"] += units
            d["distinct_others"] += 1
            key = (adv, pur)
            p = pairs.setdefault(key, {"advertised_asin": adv, "purchased_asin": pur,
                                       "ad_group": names.get(agid),
                                       "sales": 0.0, "units": 0, "purchases": 0})
            p["sales"] = round(p["sales"] + sales, 2)
            p["units"] += units
            p["purchases"] += purchases

    designs = sorted(by_design.values(), key=lambda d: -d["other_sales"])
    for d in designs:
        total = d["own_sales"] + d["other_sales"]
        d["other_pct"] = round(d["other_sales"] / total, 4) if total else None
    top_pairs = sorted(pairs.values(), key=lambda p: -p["sales"])[:250]
    total_sales = own_sales + other_sales
    out({"market": mkt, "supported": True, "as_of": latest,
         "totals": {"ad_sales": round(total_sales, 2),
                    "own_asin_sales": round(own_sales, 2),
                    "other_asin_sales": round(other_sales, 2),
                    "other_pct": round(other_sales / total_sales, 4) if total_sales else None},
         "designs": designs[:250], "pairs": top_pairs,
         "note": "Amazon-attributed: a click on the advertised ASIN's ad followed by "
                 "a purchase of another ASIN. The Sponsored Products purchased-product "
                 "report contains ONLY not-advertised purchases, so own_asin_sales is "
                 "normally 0 and the whole figure is halo. Value comes from Amazon's "
                 "*_other_sku columns. Ad-click-driven only — organic halo is not "
                 "included (see `halo`, an upper-bound estimate, not a measurement)."})


RETENTION_NOTE = ("Amazon's reporting retention starts ~95 days back and rolls forward, "
                  "so days older than the first banked one can never be recovered.")


# Console-imported history is per CURRENCY, not per market. USD is US and GBP is
# UK, but EUR covers DE+FR+ES+IT together and cannot be attributed to one of
# them — so EU markets get no supplement rather than a made-up share.
HISTORY_CURRENCY = {"US": "USD", "UK": "GBP"}


def _imported_supplement(market, start, end, daily_first):
    """Months of console-imported history that sit BEFORE the daily data begins.

    Both sources cover April 2026 onwards, and they agree to the cent, so taking
    imported months only up to the month before `daily_first` extends the window
    without ever double-counting."""
    currency = HISTORY_CURRENCY.get(market)
    if not currency or not daily_first:
        return None
    cutoff = daily_first[:7]                      # first month daily history owns
    # The import is ACCOUNT-WIDE (one console export covers every marketplace),
    # so it lives in the shared store, not this market's DB.
    try:
        shared = db.connect_shared(ro=True)
    except sqlite3.OperationalError:
        return None
    try:
        row = shared.execute(
            """SELECT SUM(spend), SUM(sales), SUM(purchases), COUNT(*),
                      MIN(month), MAX(month)
               FROM ads_history_monthly
               WHERE currency=? AND month < ? AND month >= ? AND month <= ?""",
            (currency, cutoff, start[:7], end[:7])).fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        shared.close()
    if not row or not row[3]:
        return None
    return {"spend": round(row[0] or 0, 2), "sales": round(row[1] or 0, 2),
            "orders": row[2] or 0, "months": row[3],
            "first_month": row[4], "last_month": row[5]}


def _ytd_totals(market, cur):
    """Year to date, computed ONE way for every endpoint that reports it.

    There used to be three copies of this. `periods` supplemented the banked
    daily history with the months imported from the Ads console; `_monthly_rows`
    and `cmd_overview` each ran their own daily-only query. On 2026-08-06 the
    US dashboard and the All Markets screen reported year-to-date totals 2.3x
    apart for the same year, because daily history only reached back to April
    and the other two silently dropped January through March.

    The supplement is not optional detail. Amazon's reporting retention starts
    ~95 days back, so months before the first banked day exist nowhere but the
    console import. Leaving them out does not make the year conservative, it
    makes it wrong.

    Returns None when nothing is banked. `supplemented` says whether imported
    months reached this market: only US and UK can be supplemented, because one
    console export covers every marketplace and came back with no country, so
    DE/FR/ES/IT share a single merged EUR series that cannot be split."""
    first, last = cur.execute(
        "SELECT MIN(date), MAX(date) FROM daily_totals").fetchone()
    if not last:
        return None
    year = last[:4]
    start = f"{year}-01-01"
    cost, sales, orders = [x or 0 for x in cur.execute(
        "SELECT SUM(cost), SUM(sales), SUM(orders) FROM daily_totals WHERE date>=?",
        (start,)).fetchone()]
    # Stops at the month before the daily data begins, so the overlap (both
    # sources cover April onwards and agree to the cent) is never counted twice.
    extra = _imported_supplement(market, start, last, first)
    if extra:
        cost += extra["spend"]
        sales += extra["sales"]
        orders += extra["orders"]
    covers_from = extra["first_month"] if extra else first[:7]
    return {"year": year, "spend": round(cost, 2), "sales": round(sales, 2),
            "orders": orders, "acos": _acos(cost, sales),
            "supplemented": bool(extra),
            "first_month": covers_from,
            # A "year" that starts in June is not a short year, it is a partial
            # one, and saying so is the difference between a small market and a
            # badly reported one.
            "partial": covers_from > f"{year}-01",
            "basis": ("banked daily history plus imported console months"
                      if extra else "banked daily history only")}


def _window_totals(cur, start, end):
    """Exact spend/sales/orders for [start, end] from banked daily history."""
    cost, sales, orders, days, first, last = cur.execute(
        """SELECT SUM(cost), SUM(sales), SUM(orders), COUNT(*), MIN(date), MAX(date)
           FROM daily_totals WHERE date>=? AND date<=?""", (start, end)).fetchone()
    if not days:
        return None
    return {"spend": round(cost or 0, 2), "sales": round(sales or 0, 2),
            "orders": orders or 0, "acos": _acos(cost or 0, sales or 0),
            "days_banked": days, "first_day": first, "last_day": last}


def _modeled_period_profit(cur, start, end, spend, orders, eff, blended):
    """Modeled profit for any window — the same method as the month card.

    Spend and orders are exact. Royalty is NOT: it is per design, and no
    per-design daily table exists. Each product type's trailing-30 royalty per
    order is applied, weighted by the campaign-type mix `campaign_daily` saw in
    this window. Windows older than campaign_daily's reach fall back to the
    blended rate and say so.

    Accuracy degrades going back — today's royalty rates are applied to older
    orders, and US tee prices changed in July 2026. Never present as exact.
    """
    if not blended and not eff:
        return None
    camp_type, camp_covered = {}, {}
    for cid, ptype, asin in cur.execute(
        """SELECT a.campaign_id, p.product_type, p.asin FROM ad_groups a
           JOIN ad_group_product p ON a.ad_group_id = p.ad_group_id"""):
        if asin:
            camp_covered[cid] = True
            camp_type.setdefault(cid, {})
            camp_type[cid][ptype] = camp_type[cid].get(ptype, 0) + 1
        else:
            camp_covered.setdefault(cid, False)

    mix, cohort_orders, cohort_spend, seen_orders, seen_spend = {}, 0, 0.0, 0, 0.0
    for cid, cost, ords in cur.execute(
        """SELECT campaign_id, SUM(cost), SUM(orders) FROM campaign_daily
           WHERE date>=? AND date<=? GROUP BY campaign_id""", (start, end)):
        cost, ords = cost or 0, ords or 0
        seen_orders += ords
        seen_spend += cost
        if not camp_covered.get(cid):
            cohort_orders += ords
            cohort_spend += cost
            continue
        types_here = camp_type.get(cid) or {}
        dominant = max(types_here, key=types_here.get) if types_here else None
        mix[dominant] = mix.get(dominant, 0) + ords

    cov_orders = orders * (((seen_orders - cohort_orders) / seen_orders) if seen_orders else 1.0)
    cov_spend = round(spend * (((seen_spend - cohort_spend) / seen_spend) if seen_spend else 1.0), 2)
    mix_total = sum(mix.values())
    if mix_total:
        royalty = sum((n / mix_total) * cov_orders * eff.get(t, blended) for t, n in mix.items())
        basis = "per-campaign product-type mix"
    else:
        royalty = cov_orders * blended
        basis = "blended trailing-30 royalty per order"
    royalty = round(royalty, 2)
    return {"royalty_est": royalty, "profit": round(royalty - cov_spend, 2),
            "covered_spend": cov_spend, "uncovered_spend": round(spend - cov_spend, 2),
            "royalty_per_order": round(royalty / cov_orders, 2) if cov_orders else None,
            "basis": basis, "modeled": True}


def cmd_periods(args):
    """The dashboard's period stack: current month, previous month, year to date,
    previous year, all time.

    Spend / sales / orders / ACOS are EXACT for every period, all read from the
    same banked daily history so the rows are directly comparable. Profit is
    modeled per period (see _modeled_period_profit).

    A period the data cannot cover is returned with available:false and the
    reason, never as zeroes — Amazon's retention window means the earliest
    months simply do not exist and never will."""
    mkt = markets.current()
    cur = db.connect(ro=True).cursor()
    first, last = cur.execute("SELECT MIN(date), MAX(date) FROM daily_totals").fetchone()
    if not last:
        return out({"market": mkt, "empty": True, "periods": [],
                    "note": "no daily history banked yet — run backfill-daily"})

    core = _profit_core(mkt, cur)
    eff, blended = {}, 0.0
    if core:
        eff = {t["type"]: (t["royalty_est"] / t["orders"])
               for t in core["type_rows"] if t.get("orders")}
        blended = (core["total_roy"] / core["total_orders"]) if core["total_orders"] else 0.0

    year = int(last[:4])
    month_start = f"{last[:7]}-01"
    prev_month_end = (datetime.date.fromisoformat(month_start) - datetime.timedelta(days=1))
    prev_month_start = prev_month_end.replace(day=1)

    # (key, label, daily start, end, requested start, supplement floor).
    # "All time" must reach back past the daily data, so its supplement floor is
    # open-ended rather than the first banked day.
    specs = [
        ("current_month", "Current month", month_start, last, month_start, month_start),
        ("previous_month", "Previous month", prev_month_start.isoformat(),
         prev_month_end.isoformat(), prev_month_start.isoformat(),
         prev_month_start.isoformat()),
        ("ytd", "Year to date", f"{year}-01-01", last, f"{year}-01-01", f"{year}-01-01"),
        ("previous_year", "Previous year", f"{year - 1}-01-01", f"{year - 1}-12-31",
         f"{year - 1}-01-01", f"{year - 1}-01-01"),
        ("all_time", "All time", first, last, first, "1970-01-01"),
    ]

    rows = []
    for key, label, start, end, wanted_start, supp_start in specs:
        totals = _window_totals(cur, start, end)
        extra = _imported_supplement(mkt, supp_start, end, first)
        if not totals and not extra:
            rows.append({"key": key, "label": label, "available": False,
                         "requested_window": f"{start}→{end}",
                         "reason": (f"no banked days in {start[:4]} and no imported "
                                    f"months either — daily history starts {first}. "
                                    + RETENTION_NOTE
                                    + (" EUR history covers DE+FR+ES+IT together and "
                                       "cannot be split per market."
                                       if mkt not in HISTORY_CURRENCY else ""))})
            continue
        if not totals:
            # Wholly before the daily data — the imported months ARE the window.
            rows.append({"key": key, "label": label, "available": True,
                         "window": f"{extra['first_month']}→{extra['last_month']}",
                         "requested_window": f"{wanted_start}→{end}",
                         "partial": False, "months_imported": extra["months"],
                         "source": "imported monthly (Ads console export)",
                         "spend": extra["spend"], "sales": extra["sales"],
                         "orders": extra["orders"],
                         "acos": _acos(extra["spend"], extra["sales"]),
                         "profit": None,
                         "profit_note": "no profit estimate: royalty is modeled from "
                                        "today's per-design economics, which cannot be "
                                        "applied to months this old (US tee prices moved "
                                        "$23.99 to $19.99 in a single week)."})
            continue
        # The window we actually covered can start later than the one asked for.
        # `partial` meant only "the history starts later than the window asked
        # for". A window whose START is covered but which is MISSING DAYS in the
        # middle came out marked exact: 1 to 22 August with the 10th absent
        # dropped that day's spend and sales from the total and said nothing.
        # Count what the calendar wanted against what is banked.
        try:
            _span = ((datetime.date.fromisoformat(totals["last_day"])
                      - datetime.date.fromisoformat(totals["first_day"])).days + 1)
        except (TypeError, ValueError):
            _span = totals["days_banked"]
        gaps = max(_span - totals["days_banked"], 0)
        entry_gaps = gaps
        partial = totals["first_day"] > wanted_start or gaps > 0
        entry = {"key": key, "label": label, "available": True,
                 "window": f"{totals['first_day']}→{totals['last_day']}",
                 "requested_window": f"{wanted_start}→{end}",
                 "partial": partial, "days_banked": totals["days_banked"],
                 "days_missing": entry_gaps,
                 "spend": totals["spend"], "sales": totals["sales"],
                 "orders": totals["orders"], "acos": totals["acos"]}
        if entry_gaps:
            entry["partial_reason"] = (
                f"{entry_gaps} day(s) inside {totals['first_day']}→{totals['last_day']} "
                f"have no banked history, so this total is missing them")
        elif partial:
            entry["partial_reason"] = (f"history starts {first}, so "
                                       f"{wanted_start}–{totals['first_day']} is missing. "
                                       + RETENTION_NOTE)
        modeled = _modeled_period_profit(cur, totals["first_day"], totals["last_day"],
                                         totals["spend"], totals["orders"], eff, blended)
        if modeled:
            entry.update(modeled)
        if extra:
            # Extend backwards with imported months. Profit stays on the daily
            # portion only — modelling months this old off today's royalty would
            # be a guess dressed as a figure.
            entry["spend"] = round(entry["spend"] + extra["spend"], 2)
            entry["sales"] = round(entry["sales"] + extra["sales"], 2)
            entry["orders"] = entry["orders"] + extra["orders"]
            entry["acos"] = _acos(entry["spend"], entry["sales"])
            entry["window"] = f"{extra['first_month']}→{totals['last_day']}"
            entry["months_imported"] = extra["months"]
            entry["source"] = "banked daily + imported monthly"
            entry["partial"] = False
            entry.pop("partial_reason", None)
            entry["profit_note"] = ("profit covers the daily-banked portion only; "
                                    "imported months have no per-design economics")
        rows.append(entry)

    return out({"market": mkt, "currency": markets.cfg(mkt).get("currency"),
                "coverage": {"first_day": first, "last_day": last},
                "periods": rows, "retention_note": RETENTION_NOTE,
                "note": "spend/sales/orders/ACOS are exact and all come from the same "
                        "banked daily history; profit is MODELED per period because "
                        "royalty is per design and no per-design daily data exists"})


def _profit_core(mkt, cur):
    """The trailing-30 per-design royalty computation.

    Shared by `profit` (which reports it directly) and `periods` (which only
    needs the per-type royalty RATES it produces). Returns None when no
    targeting snapshot is banked. Kept as one function so the two commands can
    never drift into disagreeing about the same designs."""
    latest = _latest_date(cur, "targeting_perf")
    if not latest:
        return None
    prod = {r[0]: (r[1], r[2], r[3]) for r in cur.execute(
        "SELECT ad_group_id, asin, product_type, list_price FROM ad_group_product")}

    # RETROSPECTIVE royalty (PLAN.md §5): per-ASIN period royalty-per-unit
    # (royaltyLast30/salesLast30, written by map_products keyed by export
    # signature) — period-correct across price changes. Fallback = current
    # modeled royalty, disclosed via modeled_royalty_n. Never drives writes.
    period_roy = {}
    try:
        sfx = "" if mkt == markets.DEFAULT else f"_{mkt}"
        with open(os.path.join(HERE, "outputs", f"period_royalty{sfx}.json"), encoding="utf-8") as fh:
            cache = json.load(fh)
        newest = products._newest_export()
        if newest and cache.get("export_signature") == products.export_signature(newest):
            period_roy = cache.get("royalty_per_unit") or {}
    except (OSError, ValueError):
        pass
    modeled_n = 0
    econ_cache = {}

    def royalty(ptype, asin, price_s):
        nonlocal modeled_n
        if asin and asin in period_roy:
            return period_roy[asin], "period"
        if markets.is_kdp():
            import kdp_econ
            e = kdp_econ.book_econ(asin) if asin else None
            if e:
                modeled_n += 1
                return e["royalty"], "kdp"
            return 0, "kdp-unknown"           # fail closed: no book data → 0 royalty
        modeled_n += 1
        if mkt == markets.DEFAULT and ptype == products.TEE:
            return products.get_design_econ(ptype, price=price_s).get("royalty") or 0, "modeled"
        if ptype not in econ_cache:
            econ_cache[ptype] = products.get_econ(ptype).get("royalty") or 0
        return econ_cache[ptype], "modeled"

    designs, types = [], {}
    cohort = {"spend": 0.0, "orders": 0, "sales": 0.0, "groups": 0}
    for agid, cost, sales, orders, clicks in cur.execute(
        """SELECT ad_group_id, SUM(cost), SUM(sales), SUM(orders), SUM(clicks)
           FROM targeting_perf WHERE date=? GROUP BY ad_group_id
           HAVING SUM(cost)>0 OR SUM(orders)>0""", (latest,)):
        asin, ptype, price_s = prod.get(agid, (None, None, None))
        cost, sales, orders, clicks = cost or 0, sales or 0, orders or 0, clicks or 0
        if prod.get(agid) and asin is None:
            # multi-ASIN cohort group (scavenger): no per-design royalty is
            # honest — report it as unattributed coverage, never as profit
            cohort["spend"] = round(cohort["spend"] + cost, 2)
            cohort["orders"] += orders
            cohort["sales"] = round(cohort["sales"] + sales, 2)
            cohort["groups"] += 1
            continue
        roy, _src = royalty(ptype, asin, price_s)
        royalty_est = round(orders * roy, 2)
        profit = round(royalty_est - cost, 2)
        designs.append({"ad_group_id": agid, "asin": asin, "type": ptype,
                        "orders": orders, "clicks": clicks, "spend": round(cost, 2),
                        "sales": round(sales, 2), "royalty_per_unit": roy,
                        "royalty_est": royalty_est, "profit": profit,
                        "royalty_roi": round(royalty_est / cost, 2) if cost else None})
        t = types.setdefault(ptype or "unknown", {"type": ptype or "unknown", "designs": 0,
                                                  "orders": 0, "spend": 0.0, "sales": 0.0,
                                                  "royalty_est": 0.0, "profit": 0.0,
                                                  "profitable": 0})
        t["designs"] += 1
        t["orders"] += orders
        t["spend"] = round(t["spend"] + cost, 2)
        t["sales"] = round(t["sales"] + sales, 2)
        t["royalty_est"] = round(t["royalty_est"] + royalty_est, 2)
        t["profit"] = round(t["profit"] + profit, 2)
        if profit > 0:
            t["profitable"] += 1
    designs.sort(key=lambda d: d["profit"])
    type_rows = sorted(types.values(), key=lambda t: t["profit"])
    total_profit = round(sum(t["profit"] for t in type_rows), 2)
    total_spend = round(sum(t["spend"] for t in type_rows), 2)
    total_roy = round(sum(t["royalty_est"] for t in type_rows), 2)
    total_orders = sum(t["orders"] for t in type_rows)
    return {"latest": latest, "designs": designs, "type_rows": type_rows,
            "cohort": cohort, "total_profit": total_profit, "total_spend": total_spend,
            "total_roy": total_roy, "total_orders": total_orders, "modeled_n": modeled_n}


def cmd_profit(args):
    """Royalty-aware TRUE margin per design and per product type (trailing-30).
    profit = units x royalty-per-unit - ad spend. ACOS says efficiency; this says
    dollars. `royalty_roi` = royalty earned per ad dollar (>1 = ads pay for themselves).
    Also returns a MODELED `mtd` block for the dashboard's current-month cards."""
    mkt = markets.current()
    conn = db.connect(ro=True)
    cur = conn.cursor()
    core = _profit_core(mkt, cur)
    if core is None:
        return out({"market": mkt, "empty": True})
    latest, designs, type_rows = core["latest"], core["designs"], core["type_rows"]
    cohort, modeled_n = core["cohort"], core["modeled_n"]
    total_profit, total_spend = core["total_profit"], core["total_spend"]
    total_roy, total_orders = core["total_roy"], core["total_orders"]
    # worst 250 + best 250 keeps the payload sane on huge markets
    listed = designs[:250] + designs[-250:] if len(designs) > 500 else designs
    all_spend = round(total_spend + cohort["spend"], 2)
    coverage = round(total_spend / all_spend, 4) if all_spend else 1.0
    out({"market": mkt, "as_of": latest, "total_spend": total_spend,
         "total_royalty_est": total_roy, "total_profit": total_profit,
         "mtd": _mtd_profit(conn, cur, type_rows, total_roy, total_orders),
         "design_count": len(designs), "types": type_rows, "designs": listed,
         "unattributed_cohort_spend": cohort["spend"],
         "unattributed_cohort_orders": cohort["orders"],
         "unattributed_cohort_sales": cohort["sales"],
         "unattributed_cohort_groups": cohort["groups"],
         "coverage_pct": coverage, "modeled_royalty_n": modeled_n,
         "note": "trailing-30 window; royalty_est = per-ASIN period royalty where "
                 "the export has recent sales, else current modeled royalty "
                 f"({modeled_n} modeled). COVERED profit only — multi-ASIN cohort "
                 "spend is reported separately, not presented as profit. Organic "
                 "halo not counted."})




def _spike_driver(cur):
    """Best-effort attribution of a market-wide daily spend spike to ONE campaign.
    `campaign_perf.cost` is a trailing-30 snapshot, not per-day spend, so no exact
    per-campaign daily figure exists; the campaign whose trailing-30 cost grew the
    most between the two latest snapshots is the best available proxy for "what's
    spending more lately". Falls back to the biggest trailing-30 spender when there
    is no growth signal, then to nothing. Only campaigns that still exist in the
    `campaigns` table are eligible, so the app's deep-link resolves. Returns
    (campaign_id, campaign_name) or (None, None)."""
    dates = [r[0] for r in cur.execute(
        "SELECT DISTINCT date FROM campaign_perf ORDER BY date DESC LIMIT 2").fetchall()]
    if not dates:
        return None, None
    cur_date = dates[0]
    prev_date = dates[1] if len(dates) > 1 else None
    rows = cur.execute(
        """SELECT p.campaign_id, COALESCE(c.name, p.campaign_name), p.cost
           FROM campaign_perf p JOIN campaigns c ON c.campaign_id = p.campaign_id
           WHERE p.date = ?""", (cur_date,)).fetchall()
    if not rows:
        return None, None
    prev = {}
    if prev_date:
        prev = {r[0]: (r[1] or 0) for r in cur.execute(
            "SELECT campaign_id, cost FROM campaign_perf WHERE date=?", (prev_date,))}
    best = None  # (delta, id, name)
    for cid, name, cost in rows:
        delta = (cost or 0) - prev.get(cid, 0)
        if best is None or delta > best[0]:
            best = (delta, cid, name)
    if best is None or best[0] <= 0:   # no recent-growth signal → biggest spender
        cid, name, _ = max(rows, key=lambda r: r[2] or 0)
        return str(cid), name
    return str(best[1]), best[2]


PERF_TABLES = ("campaign_perf", "targeting_perf", "search_term_perf")

# data_stale fires at 4+ days — exactly when db.snapshot_gate freezes writes,
# ONE threshold everywhere (alert, health badge, write gate). Not earlier: EU
# markets sit at a structural 2-day Amazon lag, so before the 10:00 pull their
# data is legitimately 3 days behind — a 3-day alarm fired every morning for
# all six markets once before (see IssueDerivation.live in the app).


def _staleness_alerts(conn, mkt, today=None):
    """data_stale alerts: a perf table whose report job stopped landing.

    Each table is filled by its own report job and they fail independently.
    The key carries the stuck date: one alert per incident, a new incident
    alerts again. Tables with no data at all stay silent (never-pulled markets
    like an unconfigured KDP profile are not an incident)."""
    alerts = []
    for table in PERF_TABLES:
        try:
            gate = db.snapshot_gate(conn, table, today=today)
        except sqlite3.OperationalError:
            continue                      # table missing in an old DB
        if gate["date"] and not gate["ok"]:
            alerts.append({"kind": "data_stale",
                           "key": f"stale:{mkt}:{table}:{gate['date']}",
                           "market": mkt,
                           "message": f"[{mkt}] {table} newest data is {gate['date']} "
                                      f"({gate['age_days']}d old) — its report job "
                                      f"is failing and economics-driven writes "
                                      f"are frozen"})
    return alerts


def _table_freshness(conn, today=None):
    """(tables, stale_tables, latest_data) for one market DB.

    latest_data is the WORST of the three perf tables — they are filled by
    independent report jobs, and campaign_perf alone stayed green through both
    freezes this engine has had while targeting/search_term drifted behind."""
    tables, stale_tables = {}, []
    for t in PERF_TABLES:
        try:
            gate = db.snapshot_gate(conn, t, today=today)
        except sqlite3.OperationalError:
            continue                      # table missing in an old DB
        tables[t] = gate["date"]
        if gate["date"] and not gate["ok"]:
            stale_tables.append(t)
    dated = [d for d in tables.values() if d]
    return tables, stale_tables, (min(dated) if dated else None)


def _portfolio_cap_alerts(conn, mkt):
    """R8 spend guard: warn as month-to-date pooled ad spend nears the market's
    monthly portfolio cap. Fires once per month per level — nearing (>=80%) then
    over (>=100%) — and the app dedups by key. Portfolio-wide, so it carries no
    campaign_id and the app lands on the Dashboard. No-op for a market with no cap
    set (only KDP carries one today). Daily budgets stop per-campaign throttling;
    this watches the pool, which nothing else does."""
    cap = db.get_portfolio_cap(conn)
    if not cap or cap <= 0:
        return []
    month = datetime.date.today().strftime("%Y-%m")
    row = conn.execute("SELECT SUM(cost) FROM daily_totals WHERE date>=?",
                       (month + "-01",)).fetchone()
    mtd = row[0] if row and row[0] is not None else 0.0
    pct = mtd / cap
    level = "over" if mtd >= cap else ("nearing" if pct >= 0.80 else None)
    if level is None:
        return []
    msg = (f"[{mkt}] month-to-date ad spend ${mtd:.0f} is {pct*100:.0f}% of the "
           f"${cap:.0f}/mo portfolio cap"
           + (" — OVER the cap" if level == "over" else " — nearing the cap"))
    return [{"kind": "portfolio_cap", "key": f"portfolio_cap:{mkt}:{month}:{level}",
             "market": mkt, "message": msg}]


def _seasonal_tags_alerts(conn, mkt):
    """The seasonal tag map went empty after designs had been tagged.

    seasonal.json is one global file, so the file-level half of this check would
    otherwise fire six identical rows — it is reported from the default market
    only. The stranded-ad-group half is genuinely per market: those are ad groups
    THIS market seasonal-paused and can no longer release, and every market has
    its own set.

    This exists because the map was lost on 2026-08-15 and the scheduler ran as a
    silent no-op until an audit six days later. seasonal_pause is allowed to do
    nothing; it is not allowed to do nothing quietly."""
    try:
        import seasonal_pause
        lost = seasonal_pause.tags_lost(conn)
    except Exception as e:
        # A detector that watches for a SILENT failure must not fail silently
        # itself. This used to `return []`, which is byte-identical to "the tag
        # map is fine" — so a renamed column or any bug inside tags_lost would
        # switch this off for good and the alerts feed would simply stay clean.
        #
        # That is the same fault `stream_check_failed` was written for, and it
        # matters more here: the seasonal map really was lost, on 2026-08-15,
        # and the scheduler ran as a silent no-op for six days. This alert is
        # the thing standing between that and happening again.
        #
        # The key carries the exception TYPE, so a persistent fault alerts once
        # rather than on every poll.
        return [{"kind": "guard_check_failed",
                 "key": f"guard_check_failed:seasonal:{mkt}:{type(e).__name__}",
                 "market": mkt,
                 "message": (f"[{mkt}] The seasonal tag-loss check could not "
                             f"run: {type(e).__name__}: {e}. Until this is "
                             f"fixed nothing is watching for the tag map going "
                             f"empty, which is what happened on 2026-08-15 and "
                             f"ran unnoticed for six days.")}]
    if not lost:
        return []
    if not lost["stranded"] and not markets.is_default(mkt):
        return []
    return [{"kind": "seasonal_tags_lost", "key": f"seasonal_tags:{mkt}",
             "market": mkt, "message": f"[{mkt}] {lost['reason']}"}]


def _rules_lost_alerts(mkt):
    """Every authored rule stopped loading.

    rule_defs/ is one global directory, not per market, so this is reported from
    the DEFAULT market only — one alert, not seven copies of the same sentence.

    The store returns an empty index when the directory is missing, which reads
    exactly like a fresh install: the nightly evaluates nothing, reports success,
    and writes nothing. That is how the seasonal tag map stayed dead for six
    days, and this one would take every market's automation with it."""
    if not markets.is_default(mkt):
        return []
    try:
        from rules import store as rules_store
        lost = rules_store.rules_lost()
    except Exception as e:
        # Same reasoning as the seasonal check above: returning an empty list
        # here is indistinguishable from "every rule is loading fine", so a bug
        # in rules_lost() would take the detector off duty without a word.
        # An empty rule set already reads exactly like a fresh install — the
        # nightly evaluates nothing and reports success — so this alert is the
        # only thing that can tell the two apart, across every market at once.
        return [{"kind": "guard_check_failed",
                 "key": f"guard_check_failed:rules:{mkt}:{type(e).__name__}",
                 "market": mkt,
                 "message": (f"[{mkt}] The rules-loss check could not run: "
                             f"{type(e).__name__}: {e}. Until this is fixed "
                             f"nothing is watching for every authored rule "
                             f"silently failing to load, which would stop all "
                             f"automation in every market and still report a "
                             f"successful nightly.")}]
    if not lost:
        return []
    return [{"kind": "rules_lost", "key": "rules_lost", "market": mkt,
             "message": f"[{mkt}] {lost['reason']}"}]


def _aws_plan_expiry_alerts(mkt, today=None):
    """The AWS account holding the Stream queues has an expiry date.

    One account serves every realm, so this is reported from the DEFAULT market
    only — one sentence, not seven copies of it.

    This is the rare guard for something that has not gone wrong yet, and it
    earns its place on HOW it would fail rather than on how likely it is. If the
    account lapses the queues go, Stream stops arriving, and Amazon keeps
    reporting the subscription ACTIVE. Every screen still works. The day just
    gets quieter, which looks exactly like a slow sales week.

    Silent until the window opens, so it is not furniture for the next
    five months.
    """
    if not markets.is_default(mkt):
        return []
    import stream_config
    when = getattr(stream_config, "AWS_PLAN_EXPIRY", None)
    if not when:
        return []                         # deliberately switched off
    try:
        expiry = datetime.date.fromisoformat(when)
    except (TypeError, ValueError) as e:
        # A detector that watches for a SILENT failure must not fail silently
        # itself — the same reasoning already written out at
        # `stream_check_failed`. This one guards the AWS account holding the
        # Stream queues, whose expiry ends ad data while Amazon still reports
        # the subscription ACTIVE. Returning [] for a mistyped date is
        # indistinguishable from "nothing to warn about", which is the one
        # answer this alert must never give by accident.
        return [{"kind": "guard_check_failed", "market": mkt,
                 "key": f"guard:aws_plan_expiry:{mkt}:{type(e).__name__}",
                 "message": ("the AWS plan-expiry check could not read "
                             f"AWS_PLAN_EXPIRY ({when!r}): {e}. It is not "
                             "watching. Set it to a YYYY-MM-DD date, or to "
                             "None to switch it off on purpose.")}]
    now = datetime.date.fromisoformat(today) if today else datetime.date.today()
    left = (expiry - now).days
    if left > getattr(stream_config, "AWS_PLAN_WARN_DAYS", 60):
        return []
    if left >= 0:
        body = (f"The AWS account holding the Marketing Stream queues is on the "
                f"free plan and closes on {when} — {left} days away.")
    else:
        body = (f"The AWS account holding the Marketing Stream queues was due to "
                f"close on {when}, {abs(left)} days ago.")
    return [{"kind": "aws_plan_expiry",
             # Keyed on the date, so it is one alert rather than a daily one.
             "key": f"aws_plan_expiry:{when}",
             "market": mkt,
             "message": (f"[{mkt}] {body} If it lapses the queues go and Stream "
                         f"stops arriving, while Amazon still reports the "
                         f"subscription ACTIVE — the day simply reads quieter, "
                         f"which looks like a slow sales week. Upgrade the "
                         f"account, then update stream_config.AWS_PLAN_EXPIRY.")}]


def _stream_corrupt_alerts(mkt):
    """The Stream database failing its own integrity check.

    Reported from the DEFAULT market only: one `stream_data.sqlite` serves every
    realm, so seven markets would raise seven alerts about one file — which is
    exactly what the 2026-08-22 corruption did through `stream_check_failed`.

    That alert already fires when the undercount check RAISES, and it did its
    job. But it says the check could not run, which is a symptom; this names the
    fault and the fix. `quick_check` catches it in about a millisecond — measured
    on that exact file, where the full `integrity_check` took nine.
    """
    if not markets.is_default():
        return []
    try:
        import stream_store
        h = stream_store.health(_env_if_configured())
    except Exception as exc:                      # never let a check break alerts
        return [{"kind": "stream_check_failed",
                 "key": f"stream_health_failed:{type(exc).__name__}",
                 "market": mkt,
                 "message": (f"The Stream database health check could not run: "
                             f"{type(exc).__name__}: {exc}")}]
    if not h.get("corrupt"):
        return []
    detail = h.get("corrupt_detail") or "no detail reported"
    return [{"kind": "stream_db_corrupt",
             "key": "stream_db_corrupt",
             "market": mkt,
             "message": (f"The Marketing Stream database is corrupt "
                         f"({detail}). Hours already banked may be unreadable, "
                         f"and Stream never resends. Recover it before the next "
                         f"drain writes over more of it.")}]


def _stream_undercount_alerts(mkt):
    """Stream delivered a whole day and it did not match the report.

    This is the one Stream failure that hides. Everything else announces
    itself: an empty queue, a stale drain, a missing hour, a market that
    resolves to nothing. A pipeline that is simply DROPPING part of what Amazon
    sends stays internally consistent all the way to the screen — the totals
    add up, the placements add up, the hours add up, and the number is quietly
    low. The operator caught the first instance of it by eye.

    So the comparison runs by itself rather than waiting for someone to
    remember the command. It only speaks about a day Stream saw whole and the
    report has banked; every other case is a refusal with a reason, not an
    alarm, because a day that is expected to read low proves nothing.
    """
    try:
        import stream_verify
        got = stream_verify.verify(market=mkt)
    except Exception as e:
        # The check that watches for a SILENT failure must not fail silently
        # itself. This used to `return []`, so a schema change, a renamed
        # column or any bug inside stream_verify would switch the one
        # drop-detector off for good and nothing anywhere would say so — the
        # alerts feed would simply stay clean, which is what it looks like when
        # everything is fine.
        #
        # A market with no Stream data does NOT come through here: verify()
        # returns comparable:false with a reason for that, checked against all
        # of UK, DE and USKDP. So an exception is a real fault, not absence.
        #
        # The key carries the exception TYPE, so a persistent fault alerts once
        # instead of on every poll.
        return [{"kind": "stream_check_failed",
                 "key": f"stream_check_failed:{mkt}:{type(e).__name__}",
                 "market": mkt,
                 "message": (f"[{mkt}] The Stream undercount check could not "
                             f"run: {type(e).__name__}: {e}. Until this is "
                             f"fixed nothing is watching for Stream quietly "
                             f"dropping data — every other Stream check proves "
                             f"only that what ARRIVED was read faithfully.")}]
    if not got.get("comparable") or not got.get("verdict"):
        return []
    if "MISMATCH" not in got["verdict"]:
        return []
    day = got.get("day")
    return [{"kind": "stream_undercount",
             # The day is in the key, so a bad day alerts once rather than
             # every hour until it scrolls out of the window.
             "key": f"stream_undercount:{mkt}:{day}",
             "market": mkt,
             "message": f"[{mkt}] Marketing Stream is undercounting {day}. "
                        + got["verdict"]}]


def cmd_alerts(args):
    """Conditions worth a notification. The app dedups by `key` so each alert
    fires once. Kinds: spend_spike (latest banked day vs 7-day average),
    budget_max (campaign's average daily spend ~ its budget), kill_candidate,
    data_stale (a perf table's report job stopped landing), portfolio_cap
    (month-to-date pooled spend nears the market's monthly cap),
    seasonal_tags_lost (the seasonal tag map went empty after designs had been
    tagged, so the scheduler is a silent no-op), rules_lost (rule_defs stopped
    loading, so the nightly evaluates no rules at all), stream_undercount
    (Marketing Stream delivered a whole day and it did not match the report —
    the one Stream failure that stays internally consistent all the way to the
    screen)."""
    mkt = markets.current()
    conn = db.connect(ro=True)
    cur = conn.cursor()
    alerts = []

    alerts.extend(_staleness_alerts(conn, mkt))
    alerts.extend(_portfolio_cap_alerts(conn, mkt))
    alerts.extend(_seasonal_tags_alerts(conn, mkt))
    alerts.extend(_rules_lost_alerts(mkt))
    alerts.extend(_stream_corrupt_alerts(mkt))
    alerts.extend(_stream_undercount_alerts(mkt))
    alerts.extend(_aws_plan_expiry_alerts(mkt))

    # spend spike: latest banked day vs the 7 before it
    days = cur.execute(
        "SELECT date, cost FROM daily_totals ORDER BY date DESC LIMIT 8").fetchall()
    if len(days) >= 4:
        latest_d, latest_c = days[0]
        base = [c or 0 for _, c in days[1:]]
        avg = sum(base) / len(base)
        if avg > 1 and (latest_c or 0) > 1.5 * avg:
            drv_id, drv_name = _spike_driver(cur)
            msg = (f"[{mkt}] spend {latest_c:.2f} on {latest_d} is "
                   f"{latest_c / avg:.1f}x the 7-day average ({avg:.2f})")
            if drv_name:
                msg += f" — likely driver: {drv_name}"
            alert = {"kind": "spend_spike", "key": f"spike:{mkt}:{latest_d}",
                     "market": mkt, "message": msg}
            if drv_id:
                alert["campaign_id"] = drv_id
            alerts.append(alert)

    # budget maxing: trailing-30 average daily spend within 90% of the daily budget
    latest, _ = _latest_two_dates(cur)
    if latest:
        for cid, name, budget, cost in cur.execute(
            """SELECT c.campaign_id, c.name, c.daily_budget, p.cost
               FROM campaigns c JOIN campaign_perf p
                 ON p.campaign_id=c.campaign_id AND p.date=?
               WHERE c.state='ENABLED' AND c.daily_budget>0""", (latest,)):
            daily = (cost or 0) / 30.0
            if daily >= 0.9 * budget:
                # key has NO date: alert once per campaign, not daily
                alerts.append({"kind": "budget_max", "key": f"budget:{mkt}:{cid}",
                               "market": mkt, "campaign_id": str(cid),
                               "message": f"[{mkt}] '{name}' averages {daily:.2f}/day "
                                          f"~ its {budget:.2f} budget — likely capped"})

    # kill candidates (same rule as the kill list; one alert per design+snapshot)
    prod = {r[0]: (r[1], r[2]) for r in cur.execute(
        "SELECT ad_group_id, asin, product_type FROM ad_group_product")}
    states = dict(cur.execute("SELECT ad_group_id, state FROM ad_groups").fetchall())
    ag_camp = dict(cur.execute("SELECT ad_group_id, campaign_id FROM ad_groups").fetchall())
    be_for = _design_be_for(conn)
    t_latest = _latest_date(cur, "targeting_perf")   # its own snapshot, not campaign_perf's
    if t_latest and be_for is not None:
        for agid, cost, sales, orders, clicks in cur.execute(
            """SELECT ad_group_id, SUM(cost), SUM(sales), SUM(orders), SUM(clicks)
               FROM targeting_perf WHERE date=? GROUP BY ad_group_id
               HAVING SUM(clicks)>=15""", (t_latest,)):
            if states.get(agid) != "ENABLED":
                continue
            asin, ptype = prod.get(agid, (None, None))
            cost, sales, orders, clicks = cost or 0, sales or 0, orders or 0, clicks or 0
            cvr = _cvr(orders, clicks)
            acos = _acos(cost, sales)
            be, skip = be_for(agid)
            if skip:
                continue                  # transition/unknown/cohort: no kill claim
            if (cvr is not None and cvr < FLOOR_CVR
                    and acos is not None and be is not None and acos > be):
                # key has NO date: one alert per design until it's dealt with
                cid_k = ag_camp.get(agid)
                alerts.append({"kind": "kill_candidate", "key": f"kill:{mkt}:{agid}",
                               "market": mkt, "ad_group_id": str(agid),
                               "campaign_id": str(cid_k) if cid_k else None,
                               "asin": asin,
                               "message": f"[{mkt}] {asin or agid} CVR {cvr*100:.0f}% < 8% "
                                          f"and ACOS {acos*100:.0f}% over break-even — kill candidate"})
    out({"market": mkt, "count": len(alerts), "alerts": alerts})


def _target_daily_coverage(conn):
    """{days, first, last} for target_daily's banked date range, or None
    when the table is empty or absent — an older DB, or a market whose
    seed hasn't started yet. A rolling-window rule refuses to write when
    its window has holes, so the operator needs somewhere to see why."""
    try:
        cov = conn.execute("SELECT COUNT(DISTINCT date), MIN(date), MAX(date) "
                           "FROM target_daily").fetchone()
    except sqlite3.OperationalError:
        return None
    return {"days": cov[0], "first": cov[1], "last": cov[2]} if cov and cov[0] else None


def _campaign_counts(conn):
    """(total, enabled) campaigns in the mirror.

    Two numbers because the app shows both: appctl's count next to the one it
    reads straight from SQLite, as proof the direct path works. The direct read
    counts ENABLED only — the mirror also holds PAUSED and ARCHIVED rows, and
    counting all three overstated the footer (US: 373 rows, 57 serving). So the
    reply has to carry the enabled count too, or the app compares 57 against
    373 and flags a mismatch on every healthy market."""
    total = conn.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0]
    enabled = conn.execute(
        "SELECT COUNT(*) FROM campaigns WHERE state = 'ENABLED'").fetchone()[0]
    return total, enabled


def _daily_totals_coverage(conn, today=None):
    """How much true per-day ACCOUNT history this market has banked.

    daily_totals is filled by daily_metrics.py, a different nightly step from
    the perf pull, so it goes stale on its own. It is what the dashboard's
    day grid, trend, month and year-to-date rows read. When the nightly ran
    US-only for five nights the EU grids simply greyed out, and no screen said
    a step had stopped running."""
    gate = db.daily_bank_gate(conn, "daily_totals", today=today)
    try:
        cov = conn.execute("SELECT COUNT(DISTINCT date), MIN(date), MAX(date) "
                           "FROM daily_totals").fetchone()
    except sqlite3.OperationalError:
        return None
    if not cov or not cov[0]:
        return None
    return {"days": cov[0], "first": cov[1], "last": cov[2],
            "behind_days": gate["age_days"], "stale": not gate["ok"],
            "reason": gate["reason"]}


def _bid_ceilings(conn):
    """This market's write ceilings, one entry per surface, null when unset.

    The ceiling is the third safety rail after the kill switch and approval
    mode, and the only one stored per market. Until this landed the only way to
    read one was Settings, which shows whichever market the profile picker is
    on — so seeing a GAP meant loading seven screens, and nobody did. US was
    capped and the five EU markets were not, for months, while the same six auto
    bid-writing rules ran nightly in all six.

    An unset surface is reported as null and never dropped: a missing key decodes
    to the same nil as an absent field, so the app could not tell "no ceiling"
    from "this engine is too old to say". Never raises — health opens every
    market DB it can find, including ones predating engine_meta, and one old file
    must not take the whole screen down."""
    out_ = {}
    for surface in ("target", "keyword", "budget"):
        try:
            out_[surface] = db.get_bid_ceiling(conn, surface)
        except Exception:
            out_[surface] = None
    return out_


def cmd_health(args):
    env = _env_if_configured()
    # No `.env` means no market is configured, and the screen says so per row.
    # The old fallback called every market configured when the credentials file
    # was missing, which is the one case the column exists to report.
    avail = set(markets.available(env))
    rows = []
    for code in markets.MARKETS:
        path = _market_db_path(code)
        entry = {"market": code, "configured": code in avail, "has_data": db.has_data(path)}
        if os.path.exists(path):
            # The OPEN is inside the try too. It was outside, so one unreadable
            # market file — a corrupt database, a directory wearing the name —
            # raised out of the whole command and System Health showed "file is
            # not a database" for all seven markets instead of naming the one
            # that is broken. Same rule the bid-ceiling reader already follows:
            # one old file must not take the screen down.
            c = None
            try:
                c = db.open_readonly(path)
                tables, stale_tables, latest = _table_freshness(c)
                entry["latest_data"] = latest
                entry["tables"] = tables
                entry["stale_tables"] = stale_tables
                entry["last_pull"] = c.execute("SELECT MAX(pulled_at) FROM pull_log").fetchone()[0]
                entry["last_write"] = c.execute("SELECT MAX(applied_at) FROM writes_log").fetchone()[0]
                entry["campaigns"], entry["campaigns_enabled"] = _campaign_counts(c)
                # Rolling-window rules refuse to write when their window has
                # holes, so the operator needs somewhere to see the coverage.
                entry["target_daily"] = _target_daily_coverage(c)
                # daily_totals has its OWN nightly step (daily_metrics.py), so
                # it goes stale independently of the perf pull. Surfaced here
                # because the app must be able to say a step stopped running.
                entry["daily_totals"] = _daily_totals_coverage(c)
                # The per-market write ceilings. Here so a market with NO cap is
                # visible next to the six that have one, instead of hiding behind
                # the profile picker in Settings.
                entry["bid_ceiling"] = _bid_ceilings(c)
                note = c.execute(
                    """SELECT pulled_at, kind, note FROM pull_log
                       WHERE note IS NOT NULL AND note<>'' ORDER BY pulled_at DESC LIMIT 1""").fetchone()
                if note:
                    entry["last_note"] = {"at": note[0], "kind": note[1], "note": note[2]}
                # Only count GENUINELY stalled reports, not normal in-flight ones.
                # The engine polls Amazon for MAX_WAIT (25 min) then intentionally
                # defers slow reports (searchterm/campaigns routinely exceed it) to
                # the next daily run, which resumes the same report_id. So a report
                # requested this morning and still downloaded=0 is normal, not an
                # error. It's only stalled once it has survived a full daily cycle
                # (~26h — one 10:00 run + margin) still undownloaded. requested_at is
                # local-naive ISO (db._now), so compare against a local cutoff.
                _stall_cutoff = (datetime.datetime.now()
                                 - datetime.timedelta(hours=26)).isoformat()
                # Dead jobs (FAILED/CANCELLED, or EXPIRED by the pull's 48h
                # sweep) are history, not a stall — without this they counted
                # as "pending" forever.
                entry["reports_pending"] = c.execute(
                    """SELECT COUNT(*) FROM report_jobs
                       WHERE downloaded=0 AND requested_at < ?
                         AND status NOT IN ('FAILED','CANCELLED','EXPIRED')""",
                    (_stall_cutoff,)).fetchone()[0]
            except Exception as e:
                entry["error"] = str(e)
            finally:
                if c is not None:
                    c.close()
        rows.append(entry)
    # last nightly run, as written by run_scheduled.sh's step tracker — the
    # app's System Health shows failed steps here since Discord digests are off
    last_run = _last_run_status()
    # Marketing Stream is global, not per-market: one queue serves a whole realm.
    # Read from local state only, so System Health stays offline and fast.
    stream = None
    try:
        import stream_store
        stream = stream_store.health(env)
    except Exception as e:
        stream = {"configured": False, "error": str(e)}

    out({"kill_active": os.path.exists(os.path.join(HERE, "KILL")),
         "approval_required": os.path.exists(os.path.join(HERE, "REQUIRE_APPROVAL")),
         "last_run": last_run,
         "stream": stream,
         "markets": rows})


# ---- LIVE / ACTION endpoints (delegate to engine; need API — run on Mac) ----
def _kill_file():
    return os.path.join(HERE, "KILL")


def _guard_kill():
    """JSON-friendly kill check (killswitch.check() prints text + exits)."""
    if os.path.exists(_kill_file()):
        err("KILL switch is ON — all writes are frozen. Turn it off first (kill --off).")


def _api_errors(batches):
    """Amazon's own words for why a write was rejected, or None if it wasn't.

    `applied: false, http: [400]` and nothing else is not a diagnosis — it
    cannot distinguish a bad id from a malformed payload from an operation the
    endpoint does not support. The client already parses the response body;
    this stops it being thrown away."""
    msgs = []
    for b in batches or []:
        body = b.get("body")
        if not isinstance(body, dict):
            continue
        for key in ("message", "detail", "errorMessage"):
            if isinstance(body.get(key), str):
                msgs.append(body[key])
        for section in body.values():
            if not isinstance(section, dict):
                continue
            for entry in section.get("error") or []:
                for e in (entry or {}).get("errors") or []:
                    value = e.get("errorValue")
                    text = (value or {}).get("message") if isinstance(value, dict) else None
                    msgs.append(text or e.get("errorType") or "unspecified error")
    seen, unique = set(), []
    for m in msgs:
        if m not in seen:
            seen.add(m)
            unique.append(m)
    return "; ".join(unique) if unique else None


def _http_ok(batches):
    """Batch success the honest way. Amazon answers 207 even when every item
    in the batch errored, so require zero item-level failures too (the client
    parses the v3 success/error arrays into failed_items).

    This answers ONE question: did EVERY item go through. That is the right
    question for a single-entity write, which is what almost every caller does.
    A caller that sends MANY items must ask `_applied_subset` instead — see the
    warning there."""
    import ads_client
    return ads_client.items_ok(batches)


def _applied_subset(batches, requested):
    """Which of `requested` Amazon actually accepted, item by item.

    `_http_ok` is all-or-nothing, and for a multi-item write that is a lie in
    the dangerous direction. Amazon rejects individual items inside a 207 all
    the time — a duplicate negative, a keyword it will not take — so one bad
    item out of thirty turned the whole call into "nothing was applied", while
    twenty-nine were live on the account. The operator was told 0, every row in
    the Audit Trail said `failed`, and the local mirror was left describing ad
    groups that Amazon had already paused. That desync is the exact fault
    `_http_ok` was added to prevent, arrived at from the other side.

    The client already knows the answer per item: `failed_ids` maps the v3
    error entries back onto the ids that were sent. So the rule is:

      * any batch that did not return 2xx  -> nothing is counted at all.
        Those ids are absent from `failed_ids` (there was no body to parse),
        so trusting the id list alone would count a transport failure as a
        clean run. Refusing the whole call is the safe reading, and it is
        also what the code did before.
      * any batch that reported MORE failures than it could name -> also
        nothing. `failed_items` counts the v3 error entries; `failed_ids`
        carries only the ones whose `index` mapped back onto an id we sent.
        An error entry with no usable index leaves us knowing that something
        in the batch was refused and not knowing what, so subtracting only
        the named ids counts a REPORTED FAILURE AS ACCEPTED — the local
        mirror then says PAUSED for an ad group Amazon left ENABLED, which
        keeps spending while the screen says it stopped. `items_ok` in
        ads_client already reads `failed_items`; this did not, and that
        disagreement was the bug (found by review, 2026-08-23).
      * every batch returned 2xx and named every failure -> applied =
        requested minus the ids Amazon named as rejected.

    Refusing the whole call, rather than the one batch, is deliberate: it is
    the rule already stated above for a transport failure, and the caller
    logs `failed`, which reads as "we cannot say this went through". The
    opposite error is not self-correcting.

    Returns the accepted ids as strings, in the order they were requested.
    """
    return _applied_outcome(batches, requested)[0]


def _applied_outcome(batches, requested):
    """(accepted ids, confirmed).

    `confirmed` is False when Amazon's answer cannot be mapped onto the items we
    sent — a transport failure, or an error entry with no usable index. The
    accepted list is empty in that case, and the two are NOT the same thing:
    an empty list with confirmed=True means Amazon refused everything, and with
    confirmed=False it means nobody knows. Callers used to subtract the empty
    list from what they asked for and tell the operator "Amazon refused all 40",
    which is a claim about Amazon that nothing supports (found by review,
    2026-08-23)."""
    import ads_client
    if not batches or any(b.get("http") not in (200, 207) for b in batches):
        return [], False
    if "uncertain" in ads_client.item_outcomes(batches, requested):
        return [], False
    accepted = ads_client.certain_ids(batches, requested)
    return [str(r) for r in requested if str(r) in accepted], True


def cmd_kill(args):
    """The engine's emergency brake: creates/removes the KILL freeze file."""
    if args.on and args.off:
        err("pass --on or --off, not both")
    if args.on:
        open(_kill_file(), "w", encoding="utf-8").close()
    elif args.off:
        if os.path.exists(_kill_file()):
            os.remove(_kill_file())
    out({"kill_active": os.path.exists(_kill_file())})


def cmd_approval_mode(args):
    """The approval gate: when REQUIRE_APPROVAL exists, the nightly job SKIPS
    phase2's auto-apply (negatives + pauses) — it only collects. You then apply
    from the app's Approval Queue. Softer than KILL: bids, harvest, builders
    still run automatically."""
    path = os.path.join(HERE, "REQUIRE_APPROVAL")
    if args.on and args.off:
        err("pass --on or --off, not both")
    if args.on:
        open(path, "w", encoding="utf-8").close()
    elif args.off:
        if os.path.exists(path):
            os.remove(path)
    out({"approval_required": os.path.exists(path)})


def cmd_overview(args):
    """All-markets rollup: each market's trailing-30 headline (no FX conversion —
    money stays in its own currency; the app groups subtotals per currency).
    Opens every market DB directly — call without ADS_MARKET.

    `--kind merch|kdp` scopes the rollup to one advertiser family. KDP is a
    separate Amazon Ads profile, so its market (USKDP) must never share a table
    with the Merch markets. The app passes the selected profile's kind, so
    picking KDP US shows only KDP markets and vice versa. No --kind = every
    market (ad-hoc CLI use)."""
    import sqlite3 as s3
    rows = []
    for code, cfg in markets.MARKETS.items():
        if args.kind and cfg.get("kind", "merch") != args.kind:
            continue
        path = _market_db_path(code)
        if not os.path.exists(path):
            continue
        c = db.open_readonly(path)
        try:
            latest = c.execute("SELECT MAX(date) FROM campaign_perf").fetchone()[0]
            if not latest:
                continue
            cost, sales, orders, clicks = [x or 0 for x in c.execute(
                """SELECT SUM(cost), SUM(sales), SUM(orders), SUM(clicks)
                   FROM campaign_perf WHERE date=?""", (latest,)).fetchone()]
            daily = c.execute(
                "SELECT cost, sales FROM period_totals WHERE period='daily'").fetchone()
            # Same helper the dashboard's period stack uses, so All Markets and
            # the Year-to-date row can never report different years again.
            ytd = _ytd_totals(code, c)
            rows.append({"market": code, "currency": cfg.get("currency"),
                         "as_of": latest, "spend": round(cost, 2), "sales": round(sales, 2),
                         "orders": orders, "clicks": clicks,
                         "acos": _acos(cost, sales), "cvr": _cvr(orders, clicks),
                         "daily_spend": round(daily[0] or 0, 2) if daily else None,
                         "daily_sales": round(daily[1] or 0, 2) if daily else None,
                         "ytd_spend": ytd["spend"] if ytd else None,
                         "ytd_sales": ytd["sales"] if ytd else None,
                         "ytd_supplemented": ytd["supplemented"] if ytd else None,
                         # What the year-to-date figure does NOT cover.
                         # `_ytd_totals` has computed both of these all along and
                         # this reply dropped them, so All Markets printed UK,
                         # DE, FR, ES and IT — all of which only began
                         # advertising 2026-06-24 — under a plain "YTD" heading.
                         # Three months of spend read as a full year.
                         "ytd_partial": ytd["partial"] if ytd else None,
                         "ytd_first_month": ytd["first_month"] if ytd else None,
                         "ytd_basis": ytd["basis"] if ytd else None})
        finally:
            c.close()
    out({"markets": rows})


def cmd_digest(args):
    """writes_log activity since a timestamp (the app's post-run digest):
    counts per action for the CURRENT market."""
    mkt = markets.current()
    since = _iso_timestamp_arg(args.since, "--since")
    cur = db.connect(ro=True).cursor()
    # Only writes that LANDED. This is the post-run digest the app shows, and it
    # counted rejected attempts as actions — so a run where Amazon refused every
    # bid change still reported them as work done. Failures are reported
    # separately rather than dropped, because a run that failed silently reads
    # exactly like a quiet one.
    counts = {action: n for action, n in cur.execute(
        """SELECT action, COUNT(*) FROM writes_log WHERE applied_at>=?
             AND NOT """ + db.FAILED_RESULT_SQL + """
           GROUP BY action""", (since,))}
    failed = {action: n for action, n in cur.execute(
        """SELECT action, COUNT(*) FROM writes_log WHERE applied_at>=?
             AND """ + db.FAILED_RESULT_SQL + """
           GROUP BY action""", (since,))}
    latest = cur.execute("SELECT MAX(applied_at) FROM writes_log").fetchone()[0]
    out({"market": mkt, "since": since, "latest_write": latest,
         "actions": counts, "failed": failed,
         "failed_total": sum(failed.values())})


def cmd_harvest_prune(args):
    """Preview the wasteful Harvested keywords + ASIN targets the engine would pause
    (per-target) — the app's prune review data."""
    import harvest_prune
    mkt = markets.current()
    # Read-only: a preview must not create or migrate the market database.
    conn = db.connect(ro=True)
    end, plan = harvest_prune.build_plan(conn)
    out({"market": mkt, "as_of": end, "count": len(plan),
         "wasted": round(sum(p["cost"] for p in plan), 2),
         "keywords": [{"keyword_id": p["entity_id"], "kind": p["kind"], "keyword": p["label"],
                       "asin": p["asin"], "type": p["type"], "clicks": p["clicks"],
                       "orders": p["orders"], "spend": p["cost"], "sales": p["sales"],
                       "acos": p["acos"], "cvr": p["cvr"], "break_even": p["break_even"],
                       "reason": p["reason"]} for p in plan]})


def cmd_harvest_prune_apply(args):
    """Pause an APPROVED subset of harvested targets (stdin: {"keyword_ids": [...]} —
    keyword AND ASIN-target ids; empty stdin = all in the current plan). Splits by
    kind and pauses keywords vs product targets via the right API."""
    _guard_kill()
    _check_econ_gate()
    import harvest_prune
    from ads_client import AdsClient
    mkt = markets.current()
    conn = db.connect()
    _, plan = harvest_prune.build_plan(conn)
    approved = None
    if not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
        if raw:
            try:
                approved = {str(k) for k in json.loads(raw).get("keyword_ids", [])}
            except Exception as e:
                err(f"could not parse approved ids from stdin: {e}")
    if approved is not None:
        plan = [p for p in plan if p["entity_id"] in approved]
    if not plan:
        out({"market": mkt, "requested": 0, "paused": 0, "failed": 0,
             "unconfirmed": 0, "note": "nothing to pause"})
        return
    client = AdsClient(mkt)
    # Report what Amazon CONFIRMED, not just how many rows we sent. A batch it
    # refused outright comes back paused=0, which is the number a run with
    # nothing to do also returns — the app printed that in the success colour
    # (found by review, 2026-08-24).
    totals = {"requested": 0, "paused": 0, "failed": 0, "unconfirmed": 0}
    for kind, api, action in (("keyword", client.set_keywords_state, "pause_keyword"),
                              ("target", client.set_targets_state, "pause_target")):
        got = harvest_prune.pause_outcome(
            client, conn, [p for p in plan if p["kind"] == kind], api, action, kind)
        for key in totals:
            totals[key] += got[key]
    out({"market": mkt, **totals})


def cmd_negate(args):
    """Add ONE negative-exact keyword to an ad group — the browser's
    'negate this search term' action. Reversible: the created id is logged so
    Undo can delete it. The app still confirms this one before applying."""
    _guard_kill()
    from ads_client import AdsClient
    mkt = markets.current()
    conn = db.connect()
    client = AdsClient(mkt)
    res = client.create_negative_keywords([{
        "campaignId": args.campaign, "adGroupId": args.adgroup, "keywordText": args.term}])
    ok = _http_ok(res)
    negid = (res[0].get("created_ids") or [None])[0] if res else None
    detail = args.term + (f" negid={negid}" if negid else "")
    db.log_write(conn, "add_negative", "searchTerm", str(args.adgroup),
                 detail, "none", "submitted" if ok else "failed")
    out({"market": mkt, "term": args.term, "ad_group_id": str(args.adgroup),
         "applied": ok, "undoable": bool(negid), "http": [b.get("http") for b in res]})


def cmd_harvest_promote_group(args):
    """Promote a cohort winner to a chosen family of designs. Dry run by default;
    --apply writes to the live account (KILL-gated, operator-run)."""
    try:
        body = json.loads(sys.stdin.read() or "{}")
    except Exception as e:
        err(f"could not parse promote request: {e}")
    term = body.get("term"); src_ag = body.get("source_ad_group_id")
    src_cid = body.get("source_campaign_id"); asins = body.get("asins") or []
    if not (term and src_ag and src_cid):
        err("term, source_ad_group_id, source_campaign_id required")
    if args.apply:
        _guard_kill()   # prints the JSON error and exits if KILL is on (same as cmd_negate)
        _check_econ_gate()
    res = harvest_promote_group.promote_group(term, src_ag, src_cid, asins, apply=args.apply)
    out(res)


def cmd_livestate(args):
    """Structured LIVE state for one ASIN: every ad group it runs in with the
    real ENABLED/PAUSED from Amazon right now (and the local mirror healed) —
    the machine-readable sibling of `status`."""
    from ads_client import AdsClient
    mkt = markets.current()
    conn = db.connect()
    asin = args.asins[0].upper()
    rows = conn.execute(
        """SELECT agp.ad_group_id, ag.name, c.campaign_id, c.name
           FROM ad_group_product agp
           JOIN ad_groups ag ON ag.ad_group_id=agp.ad_group_id
           JOIN campaigns  c ON c.campaign_id=ag.campaign_id
           WHERE agp.asin=?""", (asin,)).fetchall()
    if not rows:
        out({"market": mkt, "asin": asin, "groups": []})
        return
    client = AdsClient(mkt)
    ag_ids = sorted({str(r[0]) for r in rows})
    cmp_ids = sorted({str(r[2]) for r in rows})
    live_ag = {str(g.get("adGroupId")): g for g in client.list_ad_groups_by_id(ag_ids)}
    live_cmp = {str(c.get("campaignId")): c for c in client.list_campaigns_by_id(cmp_ids)}
    for state in ("ENABLED", "PAUSED", "ARCHIVED"):
        db.set_local_ad_group_state(conn, [g for g, v in live_ag.items() if v.get("state") == state], state)
        db.set_local_campaign_state(conn, [c for c, v in live_cmp.items() if v.get("state") == state], state)
    groups = [{"ad_group_id": str(agid), "ad_group": agname,
               "campaign_id": str(cid), "campaign": cname,
               "ad_group_live": (live_ag.get(str(agid)) or {}).get("state"),
               "campaign_live": (live_cmp.get(str(cid)) or {}).get("state"),
               "bid_live": (live_ag.get(str(agid)) or {}).get("defaultBid")}
              for agid, agname, cid, cname in rows]
    out({"market": mkt, "asin": asin, "groups": groups})


def _promote_summary(text):
    """The counts a phase4 script printed on its last line.

    Amazon refuses individual writes inside a batch and still answers 200 for
    the batch, so an exit code cannot say a promotion went through. This reply
    used to carry the code and nothing else, and a run where every source
    negative was refused reached the app as a green "keywords exit 0" — with
    each of those terms still serving in the ad group it was meant to leave,
    competing with the replacement that had just gone live.

    A phase that printed no line is reported as UNVERIFIED, never as clean.
    """
    from phase4_harvest_create import RESULT_PREFIX

    line = None
    for ln in (text or "").splitlines():
        if ln.startswith(RESULT_PREFIX):
            line = ln[len(RESULT_PREFIX):]
    if line is None:
        return {"reported": False,
                "note": "this phase printed no result line — what it wrote could "
                        "not be read back, so treat the run as unverified"}
    try:
        got = json.loads(line)
    except ValueError:
        return {"reported": False,
                "note": "this phase's result line could not be read"}
    got["reported"] = True
    return got


def cmd_promote(args):
    """Promote APPROVED harvest winners (stdin: {"terms": [...]} — search_term
    values from `harvest`; empty stdin = all pending). Runs phase4 (keywords)
    and phase4b (ASIN winners) scoped via --terms-file.

    Each phase reports what LANDED, not just its exit code: how many
    replacements were created, and how many source negatives Amazon refused.
    A refused source negative is the expensive half — the new keyword serves and
    the old one never stopped."""
    _guard_kill()
    _check_econ_gate()
    conn = db.connect()
    terms = None
    if not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
        if raw:
            try:
                terms = [t for t in json.loads(raw).get("terms", [])]
            except Exception as e:
                err(f"could not parse approved terms from stdin: {e}")
    kinds = {k for (k,) in conn.execute(
        "SELECT DISTINCT kind FROM harvest_log WHERE promoted=0")}
    os.makedirs(os.path.join(HERE, "outputs"), exist_ok=True)
    import datetime
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    scope_path = None
    if terms is not None:
        scope_path = os.path.join(HERE, "outputs", f"promote_scope_{stamp}.txt")
        with open(scope_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(terms))

    def run_phase(script):
        cmd = [sys.executable, _engine_script(script), "--apply", "--auto"]
        if scope_path:
            cmd += ["--terms-file", scope_path]
        p = subprocess.run(cmd, cwd=HERE, env=dict(os.environ),
                           capture_output=True, text=True, timeout=1800)
        res = {"code": p.returncode, "text": (p.stdout or "")[-1500:]}
        res.update(_promote_summary(p.stdout))
        return res

    result = {"market": markets.current(), "scoped": len(terms) if terms is not None else None}
    if "keyword" in kinds or not kinds:
        result["keywords"] = run_phase("phase4_harvest_create.py")
    if "asin_target" in kinds:
        result["asins"] = run_phase("phase4b_harvest_asins.py")
    out(result)


def _set_adgroup_state(agid, state, action):
    from ads_client import AdsClient
    mkt = markets.current()
    conn = db.connect()
    prev = conn.execute("SELECT state FROM ad_groups WHERE ad_group_id=?", (agid,)).fetchone()
    prev = prev[0] if prev else None
    client = AdsClient(mkt)
    res = client.set_ad_groups_state([agid], state)
    ok = _http_ok(res)
    db.log_write(conn, action, "adGroup", str(agid), f"app: {prev}->{state}",
                 prev or "?", "submitted" if ok else f"http={res[0].get('http') if res else '?'}")
    if ok:
        db.set_local_ad_group_state(conn, [agid], state)
    out({"market": mkt, "ad_group_id": str(agid), "prev_state": prev,
         "new_state": state if ok else prev, "applied": ok,
         "http": [b.get("http") for b in res]})


def cmd_pause(args):
    _guard_kill()
    _set_adgroup_state(args.adgroup, "PAUSED", "pause_ad_group")


def cmd_enable(args):
    _guard_kill()
    _set_adgroup_state(args.adgroup, "ENABLED", "enable_ad_group")


def _set_campaign_state(cid, state, action):
    from ads_client import AdsClient
    mkt = markets.current()
    conn = db.connect()
    prev = conn.execute("SELECT state FROM campaigns WHERE campaign_id=?", (cid,)).fetchone()
    prev = prev[0] if prev else None
    client = AdsClient(mkt)
    res = client.set_campaigns_state([cid], state)
    ok = _http_ok(res)
    db.log_write(conn, action, "campaign", str(cid), f"app: {prev}->{state}",
                 prev or "?", "submitted" if ok else f"http={res[0].get('http') if res else '?'}")
    if ok:
        db.set_local_campaign_state(conn, [cid], state)
    out({"market": mkt, "campaign_id": str(cid), "prev_state": prev,
         "new_state": state if ok else prev, "applied": ok,
         "http": [b.get("http") for b in res],
         "error": None if ok else _api_errors(res)})


def cmd_pause_campaign(args):
    _guard_kill()
    _set_campaign_state(args.campaign, "PAUSED", "pause_campaign")


def cmd_enable_campaign(args):
    _guard_kill()
    _set_campaign_state(args.campaign, "ENABLED", "enable_campaign")


def cmd_archive_campaign(args):
    """Archive a campaign. PERMANENT — Amazon has no un-archive.

    The campaign leaves the console for good and can never be re-enabled, so
    this refuses without an explicit --confirm rather than trusting that a
    campaign id on the command line was the intended one. `archive_campaign`
    is deliberately absent from UNDOABLE: the write is logged like any other,
    but the Audit Trail must never offer an Undo that Amazon cannot honour.
    Pausing is the reversible alternative."""
    _guard_kill()
    if not getattr(args, "confirm", False):
        err("archiving is permanent — Amazon cannot un-archive a campaign, and "
            "it can never be re-enabled. Pass --confirm to proceed, or use "
            "pause-campaign if you may want it back.")
    from ads_client import AdsClient
    cid = args.campaign
    mkt = markets.current()
    conn = db.connect()
    prev = conn.execute("SELECT state FROM campaigns WHERE campaign_id=?", (cid,)).fetchone()
    prev = prev[0] if prev else None
    # Archiving goes to /sp/campaigns/delete, NOT the state setter — Amazon's
    # state enum is [ENABLED, PROPOSED, PAUSED] and rejects ARCHIVED with a 400.
    res = AdsClient(mkt).archive_campaigns([cid])
    ok = _http_ok(res)
    db.log_write(conn, "archive_campaign", "campaign", str(cid),
                 f"app: {prev}->ARCHIVED", prev or "?",
                 "submitted" if ok else f"http={res[0].get('http') if res else '?'}")
    if ok:
        db.set_local_campaign_state(conn, [cid], "ARCHIVED")
    out({"market": mkt, "campaign_id": str(cid), "prev_state": prev,
         "new_state": "ARCHIVED" if ok else prev, "applied": ok,
         "http": [b.get("http") for b in res],
         "error": None if ok else _api_errors(res)})


def _set_target_state(tid, state, action):
    # targeting_perf holds no per-target state, so prev is unknown ("?"). Mirrors
    # the uniform targetId handling cmd_setbid already relies on (update_target_bids).
    from ads_client import AdsClient
    mkt = markets.current()
    conn = db.connect()
    client = AdsClient(mkt)
    res = client.set_targets_state([tid], state)
    ok = _http_ok(res)
    db.log_write(conn, action, "target", str(tid), f"app: ?->{state}",
                 "?", "submitted" if ok else f"http={res[0].get('http') if res else '?'}")
    out({"market": mkt, "target_id": str(tid), "prev_state": None,
         "new_state": state if ok else None, "applied": ok,
         "http": [b.get("http") for b in res]})


def cmd_pause_target(args):
    _guard_kill()
    _set_target_state(args.target, "PAUSED", "pause_target")


def cmd_enable_target(args):
    _guard_kill()
    _set_target_state(args.target, "ENABLED", "enable_target")


def cmd_setbid(args):
    """Manual bid edit on one target. --prev is the app's last-known bid (for the log)."""
    _guard_kill()
    from ads_client import AdsClient
    mkt = markets.current()
    conn = db.connect()
    bid = round(float(args.bid), 2)
    if bid < 0.02:
        err(f"bid {bid} below Amazon minimum")
    client = AdsClient(mkt)
    res = client.update_target_bids([{"targetId": args.target, "bid": bid}])
    ok = _http_ok(res)
    prev = args.prev if args.prev is not None else "?"
    clamp = client.last_clamps[0] if client.last_clamps else None
    written = clamp["cap"] if clamp else bid
    reason = "manual" + (" [adjusted]" if clamp else "")
    detail = f"snap=app {prev}->{written} ({reason})"
    if clamp:
        detail += f' cap_v1={{"req":{clamp["requested"]},"cap":{clamp["cap"]}}}'
    db.log_write(conn, "bid_change", "target", str(args.target),
                 detail, str(prev), "submitted" if ok else "failed")
    if ok:
        db.set_local_target_bids(conn, [(args.target, written)])
    out({"market": mkt, "target_id": str(args.target), "prev_bid": args.prev,
         "new_bid": written if ok else None, "adjusted": clamp is not None,
         "applied": ok, "http": [b.get("http") for b in res]})


def cmd_setbudget(args):
    """Manual DAILY-budget edit on one campaign — the missing remedy for the
    budget_max alert. --prev is the app's last-known budget (for the log/undo);
    without it the local campaigns table provides the previous value."""
    _guard_kill()
    from ads_client import AdsClient
    mkt = markets.current()
    conn = db.connect()
    budget = round(float(args.budget), 2)
    if budget < 1.0:
        err(f"budget {budget} below Amazon's 1.00 minimum")
    row = conn.execute("SELECT daily_budget FROM campaigns WHERE campaign_id=?",
                       (args.campaign,)).fetchone()
    prev = args.prev if args.prev is not None else (row[0] if row else None)
    client = AdsClient(mkt)
    res = client.update_campaign_budgets([{"campaignId": args.campaign, "budget": budget}])
    ok = _http_ok(res)
    clamp = client.last_clamps[0] if client.last_clamps else None
    written = clamp["cap"] if clamp else budget
    reason = "manual" + (" [adjusted]" if clamp else "")
    detail = f"snap=app {prev}->{written} ({reason})"
    if clamp:
        detail += f' cap_v1={{"req":{clamp["requested"]},"cap":{clamp["cap"]}}}'
    db.log_write(conn, "budget_change", "campaign", str(args.campaign),
                 detail,
                 str(prev) if prev is not None else "?",
                 "submitted" if ok else "failed")
    if ok:
        db.set_local_campaign_budget(conn, [(args.campaign, written)])
    out({"market": mkt, "campaign_id": str(args.campaign),
         "prev_budget": float(prev) if prev not in (None, "?", "") else None,
         "new_budget": written if ok else None, "adjusted": clamp is not None,
         "applied": ok,
         "http": [b.get("http") for b in res]})


def cmd_resetbids(args):
    """Wrap reset_inflated_bids: preview returns the plan; --apply writes it."""
    import reset_inflated_bids as rib
    from ads_client import AdsClient
    mkt = markets.current()
    conn = db.connect()
    plan = rib.build(conn)
    reduction = round(sum(p["current"] for p in plan) - sum(p["new"] for p in plan), 2)
    if not args.apply:
        out({"market": mkt, "count": len(plan), "total_reduction": reduction,
             "preview": True, "items": [_reset_trace(p) for p in plan]})
        return
    _check_econ_gate()
    _guard_kill()
    if not plan:
        out({"market": mkt, "count": 0, "total_reduction": 0.0, "preview": False, "applied": True})
        return
    client = AdsClient(mkt)
    res, applied_ids, written = rib.apply(client, conn, plan)
    # Judged per target: a plan is many bids in one call, and one target Amazon
    # refuses must not make the other few hundred read as failures in the Audit
    # Trail. `applied` stays the strict all-went-through answer the app already
    # decodes; `applied_count` is what actually moved.
    _applied_list, confirmed = _applied_outcome(res, [p["targetId"] for p in plan])
    ok = _http_ok(res)
    # `total_reduction` describes the PLAN and always has. On a partial
    # rejection the app printed it beside "Amazon refused 1 of 3", so the
    # headline claimed a saving that did not happen. `applied_reduction` is the
    # same arithmetic over the targets that actually moved, and it is what the
    # receipt shows (found by review, 2026-08-23).
    applied_reduction = round(
        sum(p["current"] - written[str(p["targetId"])] for p in plan
            if str(p["targetId"]) in applied_ids), 2)
    out({"market": mkt, "count": len(plan), "total_reduction": reduction,
         "preview": False, "applied": ok, "applied_count": len(applied_ids),
         "applied_reduction": applied_reduction,
         "rejected_count": (len(plan) - len(applied_ids)) if confirmed else None,
         "outcome_confirmed": confirmed,
         "http": [b.get("http") for b in res]})


def _enrich_pause_rows(conn, pauses):
    names = dict(conn.execute("SELECT ad_group_id,name FROM ad_groups"))
    prod = {r[0]: r[1] for r in conn.execute("SELECT ad_group_id,asin FROM ad_group_product")}
    rows = []
    for p in pauses:                       # (agid, cid, spend, reason[, sfx[, metrics]])
        agid, cid, spend, reason = p[0], p[1], p[2], p[3]
        metrics = p[5] if len(p) > 5 else None
        rows.append({"ad_group_id": str(agid), "campaign_id": str(cid), "spend": spend,
                     "reason": reason, "name": names.get(agid), "asin": prod.get(agid),
                     "trace": _pause_trace(metrics)})
    return rows


def cmd_negatives_preview(args):
    """What the automation WANTS to do (phase2 rules) — the approval-queue data."""
    import phase2_apply
    mkt = markets.current()
    # Read-only: a preview must not create or migrate the market database.
    conn = db.connect(ro=True)
    end, negs, pauses = phase2_apply.candidates(conn)
    neg_rows = []
    for n in negs:                         # (st, cid, agid, cost, reason[, sfx[, metrics]])
        st, cid, agid, cost, reason = n[0], n[1], n[2], n[3], n[4]
        metrics = n[6] if len(n) > 6 else None
        neg_rows.append({"search_term": st, "campaign_id": str(cid), "ad_group_id": str(agid),
                         "spend": round(cost, 2), "reason": reason, "trace": _neg_trace(metrics)})
    # The two halves of this plan are read from two DIFFERENT tables, filled by
    # two independent Amazon report jobs, so they drift apart. `as_of` is the
    # honest headline — the plan is no fresher than the oldest evidence behind
    # it — and each half also reports its OWN date, because that is what the
    # apply has to check against. Comparing one table's date to the other's is
    # the standing "never date one perf table from another" rule.
    out({"market": mkt, "as_of": end,
         "as_of_search_terms": db.latest_snapshot(conn, "search_term_perf"),
         "as_of_targeting": db.latest_snapshot(conn, "targeting_perf"),
         "negatives": neg_rows,
         "pauses": _enrich_pause_rows(conn, pauses)})


def _evidence_checks(plan, has_negatives, has_pauses,
                     current_st, current_tg, current_as_of):
    """What this approved plan has to be re-checked against, half by half.

    Negatives are resolved from `search_term_perf` and pauses from
    `targeting_perf`. Two independent Amazon report jobs fill those tables, so
    they drift apart — the US database holds 12 days where one had a snapshot
    and the other did not. The standing rule is to resolve a date from the table
    you are about to read, and this is the apply-time half of it.

    The first version of this guard compared the plan's `as_of` — which the
    preview builds as the OLDER of the two dates — against `search_term_perf`
    alone. That failed in both directions of the drift at once. With targeting
    behind, `as_of` was the targeting date, it never matched, and EVERY apply
    was refused; re-previewing reproduced the same mismatch, so the queue could
    not be applied at all. With search terms behind, `as_of` matched by
    coincidence and the PAUSE half — resolved against a targeting table that
    may have moved — was never checked.

    A plan carrying only `as_of` comes from an app older than the per-table
    fields. It is compared the way the preview built it, against the older of
    the two current dates, rather than being refused outright.

    Returns a list of (what, approved, current, table) tuples.
    """
    checks = []
    if has_negatives and plan.get("as_of_search_terms"):
        checks.append(("the negatives", plan["as_of_search_terms"],
                       current_st, "search_term_perf"))
    if has_pauses and plan.get("as_of_targeting"):
        checks.append(("the pauses", plan["as_of_targeting"],
                       current_tg, "targeting_perf"))
    if not checks and plan.get("as_of"):
        checks.append(("this plan", plan["as_of"], current_as_of,
                       "the perf tables"))
    return checks


def _stale_evidence(checks):
    """The refusal sentence for the first half whose evidence moved, or None.

    A date missing on either side means the check cannot be made — an older app,
    or a table with no snapshot — and that proceeds as before rather than
    refusing every such client."""
    for what, approved, current, table in checks:
        if approved and current and str(approved) != str(current):
            return (f"{what} in this plan were approved against the {approved} "
                    f"{table} snapshot and the newest is now {current}. Nothing "
                    f"was applied — the evidence moved, so re-run the preview "
                    f"and approve from that.")
    return None


def cmd_negatives_apply(args):
    """Apply an APPROVED subset of the phase2 plan. Reads the plan JSON from stdin:
       {"negatives":[{"search_term","campaign_id","ad_group_id"}...],
        "pauses":["ad_group_id"...]}"""
    _guard_kill()
    _check_econ_gate()
    from ads_client import AdsClient
    mkt = markets.current()
    conn = db.connect()
    try:
        plan = json.load(sys.stdin)
    except Exception as e:
        err(f"could not parse approved plan from stdin: {e}")
    negs = plan.get("negatives") or []
    pause_ids = [str(p) for p in (plan.get("pauses") or [])]

    # The approval queue is a screen an operator can leave open. These ids were
    # resolved against ONE snapshot, and every other apply path in this engine
    # re-resolves against fresh state before writing — `everywhere-apply` and
    # `harvest-prune-apply` both do. This one sent the captured ids straight to
    # Amazon, so a nightly pull landing in between meant negating a term or
    # pausing an ad group that had since earned its keep.
    #
    # Rebuilding the plan here is the wrong shape: it would silently apply a
    # DIFFERENT set from the one the operator read and approved. So the evidence
    # date is compared instead, and a moved snapshot refuses and asks for a
    # fresh preview.
    #
    # Each half is checked against its OWN table — see _evidence_checks.
    current_st = db.latest_snapshot(conn, "search_term_perf")
    current_tg = db.latest_snapshot(conn, "targeting_perf")
    current_as_of = min([d for d in (current_st, current_tg) if d], default=None)
    checks = _evidence_checks(plan, bool(negs), bool(pause_ids),
                              current_st, current_tg, current_as_of)
    stale = _stale_evidence(checks)
    if stale:
        err(stale)
    result = {"market": mkt, "negatives_applied": 0, "pauses_applied": 0,
              "as_of": current_as_of,
              "as_of_search_terms": current_st,
              "as_of_targeting": current_tg,
              "as_of_checked": bool(checks) and all(a and c for _, a, c, _ in checks)}
    client = AdsClient(mkt)
    if negs:
        items = [{"campaignId": n["campaign_id"], "adGroupId": n["ad_group_id"],
                  "keywordText": n["search_term"]} for n in negs]
        res = client.create_negative_keywords(items)
        # Amazon rejects individual keywords inside a 207 routinely (duplicates
        # above all), so this is judged PER KEYWORD. A created id is the proof
        # that one went through — it is also what makes it reversible, because
        # without `negid=` in the detail the Audit Trail cannot offer an Undo.
        created = [cid for b in res for cid in (b.get("created_ids") or [])]
        transport_ok = bool(res) and all(b.get("http") in (200, 207) for b in res)
        applied_n = 0
        for i, n in enumerate(negs):
            negid = created[i] if i < len(created) else None
            went_through = transport_ok and negid is not None
            detail = n["search_term"] + (f" negid={negid}" if negid else "")
            db.log_write(conn, "add_negative", "searchTerm", n["ad_group_id"],
                         detail, "none", "submitted" if went_through else "failed")
            applied_n += 1 if went_through else 0
        result["negatives_applied"] = applied_n
        result["negatives_rejected"] = len(negs) - applied_n
        result["negatives_http"] = [b.get("http") for b in res]
    if pause_ids:
        res = client.pause_ad_groups(pause_ids)
        # Same per-item rule. Mirroring the WHOLE list on a partial success
        # would tell the local database that ad groups Amazon refused are
        # paused; mirroring NONE of it leaves ad groups Amazon did pause
        # reading ENABLED. Only the accepted ids are written either way.
        applied_ids, pauses_confirmed = _applied_outcome(res, pause_ids)
        applied_set = set(applied_ids)
        for pid in pause_ids:
            db.log_write(conn, "pause_ad_group", "adGroup", pid, "approved in app",
                         "ENABLED", "submitted" if str(pid) in applied_set else "failed")
        if applied_ids:
            db.set_local_ad_group_state(conn, applied_ids, "PAUSED")
        result["pauses_applied"] = len(applied_ids)
        result["pauses_rejected"] = ((len(pause_ids) - len(applied_ids))
                                     if pauses_confirmed else None)
        result["pauses_confirmed"] = pauses_confirmed
        result["pauses_http"] = [b.get("http") for b in res]
    out(result)


# ---- accumulated "act everywhere" ------------------------------------------
# Act on a cross-campaign rollup in one shot: pause an ASIN's ad groups
# everywhere, pause a keyword's target clauses everywhere, or negate a keyword in
# every ad group it ran in. The read half (accumulated-asins/keywords) already
# resolves the instances; this is the write half. Operator decisions (2026-08-09):
# pause (reversible), never archive; negatives default to exact.
def _everywhere_adgroups(cur, latest, asin):
    """Every ad group advertising `asin`, spend-sorted — an ASIN pauses these."""
    rows = []
    for cid, cname, agid, agname, spend in cur.execute(
        """SELECT t.campaign_id, c.name, t.ad_group_id, g.name, SUM(t.cost)
             FROM targeting_perf t
             JOIN ad_group_product p ON p.ad_group_id = t.ad_group_id
             LEFT JOIN campaigns c ON c.campaign_id = t.campaign_id
             LEFT JOIN ad_groups g ON g.ad_group_id = t.ad_group_id
            WHERE t.date=? AND p.asin=?
            GROUP BY t.campaign_id, t.ad_group_id
            ORDER BY SUM(t.cost) DESC""", (latest, asin)):
        rows.append({"campaign_id": str(cid), "campaign": cname,
                     "ad_group_id": str(agid), "ad_group": agname,
                     "spend": round(spend or 0, 2)})
    return rows


def _everywhere_targets(cur, latest, targeting):
    """Every target clause carrying `targeting`, one per (campaign, ad group,
    match type), with its own id — a keyword pauses these / negates their ad groups."""
    rows = []
    for cid, cname, agid, agname, tid, mt, spend in cur.execute(
        """SELECT t.campaign_id, c.name, t.ad_group_id, g.name, t.target_id,
                  t.match_type, SUM(t.cost)
             FROM targeting_perf t
             LEFT JOIN campaigns c ON c.campaign_id = t.campaign_id
             LEFT JOIN ad_groups g ON g.ad_group_id = t.ad_group_id
            WHERE t.date=? AND t.targeting=?
            GROUP BY t.campaign_id, t.ad_group_id, t.target_id, t.match_type
            ORDER BY SUM(t.cost) DESC""", (latest, targeting)):
        rows.append({"campaign_id": str(cid), "campaign": cname,
                     "ad_group_id": str(agid), "ad_group": agname,
                     "target_id": str(tid) if tid is not None else None,
                     "match_type": mt, "spend": round(spend or 0, 2)})
    return rows


def _target_state(tgt, ag_state, row):
    """One targeting clause's own state, or None when we genuinely cannot say.

    It used to fall back to the AD GROUP's state, so a clause missing from the
    `targets` mirror inside an enabled ad group reported ENABLED. That is a
    claim, not a reading: the clause itself may already be paused. The plan then
    said skip_reason=None, the write went out as a no-op, and the writes_log
    recorded a previous state of ENABLED — so Undo would ENABLE a clause the
    operator had paused before any of this.

    The ad group is still consulted, but only where it is CONCLUSIVE: an ad
    group that is not enabled cannot serve, whatever the clause says, so
    "already paused" is safe there. Measured across all seven markets on
    2026-08-23: zero clauses in `targeting_perf` are missing from the mirror, so
    this costs nothing today and closes the hole for the day it does not.
    Found by review, 2026-08-23."""
    own = (tgt.get(row["target_id"]) or (None,))[0]
    if own:
        return own
    parent = ag_state.get(row["ad_group_id"])
    if parent and parent != "ENABLED":
        return parent          # cannot serve regardless of the clause
    return None                # enabled ad group, unknown clause -> unknown


def _skip_reason(state, target_id=None, needs_target=False):
    """Why this instance would NOT be written, or None if it would be.

    The app used to work this out for itself, from whether a `target_id` came
    back — and `_everywhere_slim` never sent one, so every skip read as "the app
    cannot address this" and the genuine no-ops were counted as zero, on every
    preview. The engine has known the answer all along; it simply was not saying
    it. Found by review, 2026-08-23.

    `state_unknown` is deliberately not folded into `already_paused`: a row whose
    state we never mirrored is not a no-op, it is a row we cannot judge, and
    telling the operator "already paused" about it would be a guess.
    """
    if needs_target and target_id is None:
        return "unaddressable"        # no clause id to write to; NOT a no-op
    if state is None:
        return "state_unknown"
    if state != "ENABLED":
        return "already_paused"
    return None


def _everywhere_plan(conn, kind, action, keys, match="exact"):
    """Resolve an accumulated selection to concrete per-instance operations,
    tagging no-ops (already paused, or a keyword with no target id). Read-only."""
    cur = conn.cursor()
    latest = _accum_latest(cur)
    match = str(match or "exact").lower()
    if action == "negate" and match not in ("exact", "phrase"):
        raise ValueError(f"negate match must be exact or phrase, not {match!r}")
    ag_state = {str(k): v for k, v in conn.execute("SELECT ad_group_id, state FROM ad_groups")}
    try:
        tgt = {str(r[0]): (r[1], r[2]) for r in
               conn.execute("SELECT target_id, state, bid FROM targets")}
    except Exception:
        tgt = {}
    ops = []
    if kind == "asin" and action == "pause":
        for asin in keys:
            for r in _everywhere_adgroups(cur, latest, asin):
                st = ag_state.get(r["ad_group_id"])
                why = _skip_reason(st)
                ops.append({**r, "key": asin, "op": "pause_ad_group",
                            "state": st, "skip_reason": why, "skip": why is not None})
    elif kind == "keyword" and action == "pause":
        for kw in keys:
            for r in _everywhere_targets(cur, latest, kw):
                st = _target_state(tgt, ag_state, r)
                why = _skip_reason(st, r["target_id"], needs_target=True)
                ops.append({**r, "key": kw, "op": "pause_target",
                            "state": st, "skip_reason": why, "skip": why is not None})
    elif kind == "keyword" and action == "setbid":
        for kw in keys:
            for r in _everywhere_targets(cur, latest, kw):
                cur_bid = (tgt.get(r["target_id"]) or (None, None))[1]
                st = _target_state(tgt, ag_state, r)
                why = _skip_reason(st, r["target_id"], needs_target=True)
                ops.append({**r, "key": kw, "op": "set_bid", "state": st,
                            "current_bid": cur_bid,
                            "skip_reason": why, "skip": why is not None})
    elif kind == "keyword" and action == "negate":
        seen = set()
        for kw in keys:
            for r in _everywhere_targets(cur, latest, kw):
                dedup = (kw, r["ad_group_id"])
                if dedup in seen:                 # negation is per ad group, not per clause
                    continue
                seen.add(dedup)
                ops.append({"key": kw, "search_term": kw, "op": "add_negative",
                            "campaign_id": r["campaign_id"], "campaign": r["campaign"],
                            "ad_group_id": r["ad_group_id"], "ad_group": r["ad_group"],
                            "spend": r["spend"], "match": match,
                            "skip_reason": None, "skip": False})
    else:
        raise ValueError(f"unsupported everywhere action {kind!r}/{action!r} — "
                         f"asin+pause, keyword+pause, keyword+setbid, or keyword+negate")
    return {"kind": kind, "action": action, "match": match, "as_of": latest, "ops": ops}


def _everywhere_slim(o):
    """The instance fields the app draws. `campaign_id`, `target_id`, `asin`,
    `state` and `skip_reason` were declared on the Swift side and stripped here,
    so the confirm sheet explained every skip with the wrong sentence."""
    return {"key": o.get("key"), "campaign": o.get("campaign"),
            "campaign_id": o.get("campaign_id"),
            "ad_group": o.get("ad_group"), "ad_group_id": o.get("ad_group_id"),
            "target_id": o.get("target_id"), "asin": o.get("asin"),
            "state": o.get("state"), "skip_reason": o.get("skip_reason"),
            "op": o.get("op"), "spend": o.get("spend")}


def _everywhere_applicable(plan):
    """The resolved operations that would perform a write."""
    return sum(1 for op in plan["ops"] if not op.get("skip"))


def _read_everywhere_req():
    try:
        req = json.load(sys.stdin)
    except Exception as e:
        err(f"could not parse selection from stdin: {e}")
    if not (req.get("keys") or []):
        err("selection is empty — pass keys:[...] (ASINs or keyword texts)")
    return req


def _everywhere_apply_ops(conn, client, ops, setbid_value=None):
    """Apply resolved everywhere ops through ads_client, logging each write so it
    is individually undoable (pauses carry the real prev_state; negatives log
    negid; bids log old->new). Skips no-ops. Shared by cmd_everywhere_apply and
    the rules executor's fan-out. Returns (applied, skipped, failed, results)."""
    applied = skipped = failed = 0
    results = []
    for o in ops:
        if o.get("skip"):
            skipped += 1
            results.append({**_everywhere_slim(o), "status": "skipped_noop"})
            continue
        op = o["op"]
        if op == "pause_ad_group":
            res = client.pause_ad_groups([o["ad_group_id"]])
            ok = _http_ok(res)
            db.log_write(conn, "pause_ad_group", "adGroup", o["ad_group_id"],
                         f"pause everywhere: {o['key']}", o.get("state") or "ENABLED",
                         "submitted" if ok else "failed")
            if ok:
                db.set_local_ad_group_state(conn, [o["ad_group_id"]], "PAUSED")
        elif op == "pause_target":
            res = client.set_targets_state([o["target_id"]], "PAUSED")
            ok = _http_ok(res)
            db.log_write(conn, "pause_target", "target", o["target_id"],
                         f"pause everywhere: {o['key']}", o.get("state") or "ENABLED",
                         "submitted" if ok else "failed")
        elif op == "set_bid":
            cur_bid = o.get("current_bid")
            try:
                cur_bid = round(float(cur_bid), 2) if cur_bid is not None else None
            except (TypeError, ValueError):
                cur_bid = None
            if cur_bid is not None and cur_bid == setbid_value:   # no-op: same bid
                skipped += 1
                results.append({**_everywhere_slim(o), "status": "skipped_noop"})
                continue
            res = client.update_target_bids([{"targetId": o["target_id"], "bid": setbid_value}])
            ok = _http_ok(res)
            clamp = client.last_clamps[0] if getattr(client, "last_clamps", None) else None
            written = clamp["cap"] if clamp else setbid_value
            old_txt = f"{cur_bid}" if cur_bid is not None else "?"
            db.log_write(conn, "bid_change", "target", o["target_id"],
                         f"snap=everywhere {old_txt}->{written} (setbid everywhere: {o['key']})",
                         None, "submitted" if ok else "failed")
            if ok:
                db.set_local_target_bids(conn, [(o["target_id"], written)])
        else:  # add_negative (exact or phrase)
            res = client.create_negative_keywords([{"campaignId": o["campaign_id"],
                                                    "adGroupId": o["ad_group_id"],
                                                    "keywordText": o["search_term"],
                                                    "matchType": "NEGATIVE_" + o.get("match", "exact").upper()}])
            ok = _http_ok(res)
            negid = (res[0].get("created_ids") or [None])[0] if res else None
            kind_txt = o.get("match", "exact")
            detail = f"{o['search_term']} (negate everywhere, {kind_txt})" + (f" negid={negid}" if negid else "")
            db.log_write(conn, "add_negative", "searchTerm", o["ad_group_id"], detail,
                         "none", "submitted" if ok else "failed")
        if ok:
            applied += 1
            results.append({**_everywhere_slim(o), "status": "applied"})
        else:
            failed += 1
            results.append({**_everywhere_slim(o), "status": "failed",
                            "http": [b.get("http") for b in (res or [])]})
    return applied, skipped, failed, results


def cmd_everywhere_preview(args):
    """Read-only: resolve an 'act everywhere' selection to its instances.
    stdin {"kind":"asin"|"keyword","action":"pause"|"negate","keys":[...]}."""
    conn = db.connect(ro=True)
    req = _read_everywhere_req()
    try:
        plan = _everywhere_plan(conn, req.get("kind"), req.get("action"),
                                req["keys"], req.get("match", "exact"))
    except ValueError as e:
        err(str(e))
    ops = plan["ops"]
    apply_n = _everywhere_applicable(plan)
    out({"market": markets.current(), "kind": plan["kind"], "action": plan["action"],
         "as_of": plan["as_of"], "count": len(ops), "applicable": apply_n,
         "skipped_noop": len(ops) - apply_n,
         "campaigns": len({o["campaign_id"] for o in ops}),
         "instances": [_everywhere_slim({**o}) | {"skip": o.get("skip", False)} for o in ops]})


def cmd_everywhere_apply(args):
    """Apply an 'act everywhere' selection across every instance — pause ad groups
    / target clauses, set a bid, or add exact/phrase negatives. stdin is the SAME
    shape as everywhere-preview (+ "bid" for setbid); the plan is RE-RESOLVED here
    against fresh state so the app never sends ids that could be stale. Live,
    KILL-gated; every write is logged and individually undoable, bids are clamped
    to the ceiling, and already-paused / same-bid instances are skipped."""
    _guard_kill()
    from ads_client import AdsClient
    mkt = markets.current()
    conn = db.connect()
    req = _read_everywhere_req()
    try:
        plan = _everywhere_plan(conn, req.get("kind"), req.get("action"),
                                req["keys"], req.get("match", "exact"))
    except ValueError as e:
        err(str(e))
    setbid_value = None
    if plan["action"] == "setbid":
        try:
            setbid_value = round(float(req.get("bid")), 2)
        except (TypeError, ValueError):
            err('setbid everywhere needs a numeric bid, e.g. {"bid": 0.45}')
        if setbid_value <= 0:
            err("bid must be greater than 0")
    client = AdsClient(mkt)
    applied, skipped, failed, results = _everywhere_apply_ops(
        conn, client, plan["ops"], setbid_value)
    out({"market": mkt, "kind": plan["kind"], "action": plan["action"],
         "applied": applied, "skipped_noop": skipped, "failed": failed,
         "count": len(plan["ops"]), "results": results})


# actions the audit trail knows how to undo, and how
UNDOABLE = {"pause_ad_group": "enable", "enable_ad_group": "pause",
            "pause_campaign": "enable", "enable_campaign": "pause",
            "pause_keyword": "enable_keyword", "pause_target": "enable_target",
            "bid_change": "restore_bid", "budget_change": "restore_budget",
            "add_negative": "remove_negative",
            "rollback_enable": "pause"}


def _row_undoable(action, detail):
    """Whether one writes_log row can actually be undone. Everything in UNDOABLE
    can, EXCEPT a negative created before reversible negatives existed: without a
    logged `negid=` there is nothing to delete, so the row is honestly not
    undoable rather than offering an Undo that would fail."""
    if action not in UNDOABLE:
        return False
    if action == "add_negative":
        return "negid=" in (detail or "")
    return True


def cmd_audit(args):
    """Recent writes_log rows (newest first) + whether each can be undone.
    Ad-group and campaign entity ids are resolved to names (ASIN fallback) so
    the trail is readable without cross-referencing."""
    mkt = markets.current()
    conn = db.connect(ro=True)
    rows = []
    before = getattr(args, "before", None)
    where = "WHERE rowid < ?" if before else ""
    params = ([int(before)] if before else []) + [int(args.limit)]
    for rid, at, action, etype, eid, detail, prev, res in conn.execute(
        f"""SELECT rowid, applied_at, action, entity_type, entity_id, detail, prev_state, result
           FROM writes_log {where} ORDER BY rowid DESC LIMIT ?""", params):
        rows.append({"row_id": rid, "at": at, "action": action, "entity_type": etype,
                     "entity_id": str(eid), "detail": detail, "prev_state": prev,
                     "result": res, "undoable": _row_undoable(action, detail)})

    # resolve names for just this page's entities (targeted IN lookups, chunked)
    def _chunks(ids, n=500):
        ids = list(ids)
        for i in range(0, len(ids), n):
            yield ids[i:i + n]
    names = {}
    ag_ids = {r["entity_id"] for r in rows if r["entity_type"] in ("adGroup", "searchTerm")}
    for chunk in _chunks(ag_ids):
        q = ",".join("?" * len(chunk))
        for agid, name in conn.execute(
                f"SELECT ad_group_id, name FROM ad_groups WHERE ad_group_id IN ({q})", chunk):
            names[str(agid)] = name
        for agid, asin in conn.execute(
                f"SELECT ad_group_id, asin FROM ad_group_product WHERE ad_group_id IN ({q})", chunk):
            names.setdefault(str(agid), asin)
    c_ids = {r["entity_id"] for r in rows if r["entity_type"] == "campaign"}
    for chunk in _chunks(c_ids):
        q = ",".join("?" * len(chunk))
        for cid, name in conn.execute(
                f"SELECT campaign_id, name FROM campaigns WHERE campaign_id IN ({q})", chunk):
            names[str(cid)] = name
    for r in rows:
        r["entity_name"] = names.get(r["entity_id"])
    # a full page usually means more history exists below it — the app shows
    # "Load older" and pages with --before <oldest row_id>
    out({"market": mkt, "count": len(rows), "writes": rows,
         "totals": _audit_totals(conn),
         "has_more": len(rows) == int(args.limit)})


# A write that changed nothing. The engine stopped logging these, but older
# rows are still in the table and they are not writes.
_NO_OP_DETAIL = "(detail IS NULL OR (detail NOT LIKE '0 ASINs%' AND detail NOT LIKE '0 designs%'))"


def _audit_totals(conn):
    """How many writes there REALLY are, counted in SQL over the whole log.

    The app derived these from the page it had loaded, so every card was
    capped by the fetch limit: on 2026-08-24 US read "500 writes this week"
    against a true 10,635, with 9,663 of them written on one day. A runaway
    rule is exactly what this screen exists to catch, and a 21x understatement
    reads as a quiet week.
    """
    today, week = conn.execute(
        f"""SELECT
              SUM(CASE WHEN substr(applied_at,1,10) = date('now','localtime')
                       THEN 1 ELSE 0 END),
              SUM(CASE WHEN substr(applied_at,1,10) >= date('now','localtime','-6 day')
                       THEN 1 ELSE 0 END)
            FROM writes_log WHERE {_NO_OP_DETAIL}""").fetchone()
    no_ops = conn.execute(
        f"""SELECT COUNT(*) FROM writes_log
            WHERE substr(applied_at,1,10) = date('now','localtime')
              AND NOT {_NO_OP_DETAIL}""").fetchone()[0]
    # Undoable is decided per row by _row_undoable, so ask IT rather than
    # rewriting the rule in SQL where the two could drift apart. Only the
    # candidate actions are read, which is a few thousand rows.
    marks = ",".join("?" * len(UNDOABLE))
    undoable = sum(1 for action, detail in conn.execute(
        f"SELECT action, detail FROM writes_log WHERE action IN ({marks})",
        sorted(UNDOABLE)) if _row_undoable(action, detail))
    return {"today": today or 0, "week": week or 0, "no_ops_today": no_ops or 0,
            "undoable": undoable, "window_days": 7}


def cmd_undo(args):
    """Undo ONE logged write: re-enable a paused ad group / re-pause an enabled one /
    restore the previous bid / delete a negative keyword by its logged id."""
    _guard_kill()
    from ads_client import AdsClient
    mkt = markets.current()
    conn = db.connect()
    row = conn.execute(
        """SELECT rowid, action, entity_type, entity_id, detail, prev_state
           FROM writes_log WHERE rowid=?""", (int(args.row),)).fetchone()
    if not row:
        err(f"no writes_log row {args.row}")
    rid, action, etype, eid, detail, prev = row
    kind = UNDOABLE.get(action)
    if not kind:
        err(f"action '{action}' can't be undone (keyword/campaign creates are permanent)")
    client = AdsClient(mkt)
    if kind in ("enable", "pause"):
        state = "ENABLED" if kind == "enable" else "PAUSED"
        if etype == "campaign":
            res = client.set_campaigns_state([eid], state)
            ok = _http_ok(res)
            if ok:
                db.set_local_campaign_state(conn, [eid], state)
        else:
            res = client.set_ad_groups_state([eid], state)
            ok = _http_ok(res)
            if ok:
                db.set_local_ad_group_state(conn, [eid], state)
        db.log_write(conn, f"undo_{action}", etype, str(eid), f"undo of row {rid}",
                     "PAUSED" if state == "ENABLED" else "ENABLED",
                     "submitted" if ok else "failed")
        out({"market": mkt, "undid_row": rid, "entity_id": str(eid),
             "new_state": state if ok else None, "applied": ok})
    elif kind in ("enable_keyword", "enable_target"):   # undo a harvested pause
        is_kw = kind == "enable_keyword"
        res = (client.set_keywords_state if is_kw else client.set_targets_state)([eid], "ENABLED")
        ok = _http_ok(res)
        db.log_write(conn, "undo_pause_keyword" if is_kw else "undo_pause_target",
                     "keyword" if is_kw else "target", str(eid),
                     f"undo of row {rid}", "PAUSED", "submitted" if ok else "failed")
        out({"market": mkt, "undid_row": rid, "entity_id": str(eid),
             "new_state": "ENABLED" if ok else None, "applied": ok})
    elif kind == "restore_budget":
        m = RX_BID.search(detail or "")
        old = float(m.group(1)) if m else (float(prev) if prev not in (None, "?", "") else None)
        if old is None:
            err(f"row {rid} has no parseable previous budget to restore")
        res = client.update_campaign_budgets([{"campaignId": eid, "budget": old}])
        ok = _http_ok(res)
        cur = float(m.group(2)) if m else None
        clamp = client.last_clamps[0] if client.last_clamps else None
        written = clamp["cap"] if clamp else old
        reason = f"undo of row {rid}" + (" [adjusted]" if clamp else "")
        detail = f"snap=undo {cur}->{written} ({reason})"
        if clamp:
            detail += f' cap_v1={{"req":{clamp["requested"]},"cap":{clamp["cap"]}}}'
        db.log_write(conn, "budget_change", "campaign", str(eid),
                     detail, str(cur),
                     "submitted" if ok else "failed")
        if ok:
            db.set_local_campaign_budget(conn, [(eid, written)])
        out({"market": mkt, "undid_row": rid, "entity_id": str(eid),
             "restored_bid": written if ok else None, "adjusted": clamp is not None,
             "applied": ok})
    elif kind == "remove_negative":
        m = re.search(r"negid=(\S+)", detail or "")
        if not m:
            err(f"row {rid} has no negative-keyword id to remove "
                f"(it was created before negatives became reversible)")
        negid = m.group(1)
        res = client.delete_negative_keywords([negid])
        ok = _http_ok(res)
        db.log_write(conn, "undo_add_negative", etype, str(eid),
                     f"removed negid={negid} (undo of row {rid})", "none",
                     "submitted" if ok else "failed")
        out({"market": mkt, "undid_row": rid, "entity_id": str(eid),
             "removed_negative": negid if ok else None, "applied": ok})
    else:  # restore_bid
        m = RX_BID.search(detail or "")
        old = float(m.group(1)) if m else (float(prev) if prev not in (None, "?", "") else None)
        if old is None:
            err(f"row {rid} has no parseable previous bid to restore")
        res = client.update_target_bids([{"targetId": eid, "bid": old}])
        ok = _http_ok(res)
        cur = float(m.group(2)) if m else None
        # The BID twin of the budget clamp above. Undo restores a bid that was
        # under the ceiling when it was first written, and the ceiling may have
        # been lowered since — so AdsClient can clamp this write too. Recording
        # the REQUESTED bid would make the mirror, the audit row and the reply
        # all name a bid that is not on the account.
        clamp = client.last_clamps[0] if client.last_clamps else None
        written = clamp["cap"] if clamp else old
        db.log_write(conn, "bid_change", "target", str(eid),
                     f"snap=undo {cur}->{written} (undo of row {rid}"
                     + (" [adjusted]" if clamp else "") + ")", str(cur),
                     "submitted" if ok else "failed")
        if ok:
            db.set_local_target_bids(conn, [(eid, written)])
        out({"market": mkt, "undid_row": rid, "entity_id": str(eid),
             "restored_bid": written if ok else None,
             "adjusted": clamp is not None, "applied": ok})


# ---- new-design intake (CSV -> campaigns) -----------------------------------
def _intake_scan(csv_path, days):
    """Cached front-end for _intake_scan_raw: the export is a multi-GB catalog and
    scanning it takes ~15s, so the 90-day scan is cached per (path, mtime, size,
    market) in outputs/. Day-window changes then filter in memory instantly.
    Windows over 90 days bypass the cache."""
    days = int(days)
    if days > 90:
        return _intake_scan_raw(csv_path, days)
    import datetime
    sig = [os.path.abspath(csv_path), os.path.getmtime(csv_path), os.path.getsize(csv_path)]
    cache_path = os.path.join(HERE, "outputs", f"intake_cache_{markets.current()}.json")
    cached = None
    try:
        with open(cache_path, encoding="utf-8") as fh:
            candidate = json.load(fh)
        if candidate.get("sig") == sig:
            cached = candidate
    except Exception:
        pass
    if cached is None:
        designs, skipped = _intake_scan_raw(csv_path, 90)
        cached = {"sig": sig, "designs": designs, "skipped": skipped}
        os.makedirs(os.path.join(HERE, "outputs"), exist_ok=True)
        try:
            with open(cache_path, "w", encoding="utf-8") as fh:
                json.dump(cached, fh)
        except Exception:
            pass
    cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    designs = [d for d in cached["designs"] if (d.get("created") or "") >= cutoff]
    # skipped-type counts stay at the 90-day window — informational only
    return designs, cached["skipped"]


def _intake_scan_raw(csv_path, days):
    """Parse a product-grid export for the CURRENT market and route each live
    design UPLOADED IN THE LAST `days` DAYS per the plan: tees -> Lottery +
    Scavenger Tees; other cohort types -> their Scavenger series (ad-safe ASIN
    for hardgoods). Reads either supported export (Snap for MOD or MerchFlow)
    through export_reader. The recency window is what turns a full-catalog
    export into "the designs I just uploaded" (dedup-vs-DB alone would match the
    whole back catalog); a hand-picked Snap export is already scoped, so the
    window only trims it further. Returns (designs, skipped_types)."""
    import datetime
    import export_reader
    xm = markets.cfg()["export_mkt"]
    cutoff = (datetime.date.today() - datetime.timedelta(days=int(days))).isoformat()
    type_series = {t: c["series"] for c in scavenger.COHORTS for t in c["types"]}
    designs, skipped, seen = [], {}, set()
    for p in export_reader.rows(csv_path):
        if (p.get("marketplace") != xm or p.get("status") != "published"
                or not p.get("asin")):
            continue
        if (p.get("createdDate") or "") < cutoff:
            continue
        asin = p["asin"].strip().upper()
        if asin in seen:
            continue
        seen.add(asin)
        ptype = (p.get("productType") or "").strip()
        series = type_series.get(ptype)
        if not series:
            # Report what the operator saw in Merch. A Snap row carries the
            # dashboard label, and an unmapped label has no engine type at all,
            # so the label is the only name that can be shown for it.
            skipped_key = (p.get("productTypeLabel") or "").strip() or ptype
            skipped[skipped_key] = skipped.get(skipped_key, 0) + 1
            continue
        ad_field = p.get("adAsins") or ""
        ad_asins = [a.strip().upper() for a in ad_field.split(",") if a.strip()] or [asin]
        try:
            total = int(float(p.get("salesTotal") or 0))
        except (TypeError, ValueError):
            total = 0
        designs.append({
            "asin": asin, "ad_asins": ad_asins, "type": ptype,
            "series": ("Tees" if series == scavenger.TEES_SERIES
                       and not markets.is_default() else series),
            "title": (p.get("productTitle") or "")[:90], "lifetime_sales": total,
            "created": p.get("createdDate") or "",
            "lottery": ptype == "standard_tshirt",   # US tees fill the numbered Lotto campaigns
        })
    return designs, skipped


def _advertised_asins(conn):
    """ASINs the local DB already knows are advertised: mapped products + lottery
    ad-group names (which ARE the ASIN). Scavenger membership isn't tracked locally —
    the builders skip existing product ads at apply time anyway (idempotent)."""
    ads = {r[0] for r in conn.execute(
        "SELECT DISTINCT asin FROM ad_group_product WHERE asin IS NOT NULL")}
    ads |= {r[0].upper() for r in conn.execute("SELECT name FROM ad_groups")
            if r[0] and re.fullmatch(r"B0[A-Z0-9]{8}", r[0].strip().upper())}
    return ads


def _adopt_export(csv_path):
    """Catalog housekeeping after an intake: the export moves into the POD
    folder, where seven nightly scripts read the catalog from, and the US
    product map is rebuilt on it.

    A MerchFlow `export_products_*.csv` is a WHOLE catalog, so adopting one
    deletes the older ones (they are ~2GB each). A Snap for MOD
    `snap-grid-export-*.csv` is a CHUNK of at most 100k rows and the catalog is
    the merge of every chunk, so nothing is deleted for one.

    Returns a summary, or None for a custom-named file, which is left alone."""
    import shutil
    csv_path = _resolve_intake_csv(csv_path)
    name = os.path.basename(csv_path)
    is_snap = name.startswith("snap-grid-export") and name.endswith(".csv")
    is_merchflow = name.startswith("export_products_") and name.endswith(".csv")
    if not (is_snap or is_merchflow):
        return None
    pod = os.path.dirname(HERE)
    dest = os.path.join(pod, name)
    moved = False
    if os.path.abspath(csv_path) != os.path.abspath(dest):
        shutil.move(csv_path, dest)
        moved = True
    keep = dest

    # Housekeeping deletes only SUPERSEDED MerchFlow exports. A Snap for MOD
    # file is a CHUNK of at most 100k rows, and the catalog is the merge of
    # them all, so deleting the older chunks would delete most of the coverage.
    # Only a full MerchFlow catalog can stand alone, and only a newer MerchFlow
    # catalog supersedes one.
    freed = 0
    removed = []
    if is_merchflow:
        superseded = set()
        for folder in (pod, HERE):
            superseded |= {os.path.abspath(p)
                           for p in glob.glob(os.path.join(folder, "export_products_*.csv"))}
        superseded = {p for p in superseded
                      if os.path.exists(p) and os.path.abspath(p) != os.path.abspath(keep)}
        for p in superseded:
            try:
                freed += os.path.getsize(p)
                os.remove(p)
                removed.append(os.path.basename(p))
            except OSError:
                pass
    # The catalogue just changed, so the banked merge no longer matches it and
    # every read falls back to parsing 1.1 GB of CSV. Rebuild it here, while the
    # operator is already waiting on an import, rather than leaving the cost to
    # tonight's run. This is only ever an optimisation: if it fails, reads are
    # slow and still correct, so it must never fail the adoption.
    cache = None
    try:
        import catalog_cache
        cache = {"rows": catalog_cache.build(verbose=True)}
    except Exception as exc:
        cache = {"error": f"{type(exc).__name__}: {exc}"[:200],
                 "note": "catalogue reads fall back to the CSV files"}

    # The canonical remap + metadata transaction ALWAYS runs under ADS_MARKET=US
    # regardless of the caller's market — the US-only econ gate must never be
    # left stale by a DE/UK-context adoption (PLAN.md §8). On failure the US DB
    # gets a STALE marker that keeps the gate closed until a successful map.
    env = dict(os.environ, ADS_MARKET="US")
    remap_error = None
    try:
        proc = subprocess.run([sys.executable, _engine_script("map_products.py")],
                              cwd=HERE, env=env, capture_output=True, text=True,
                              timeout=1800)
        rc = proc.returncode
        if rc != 0:
            # The reason used to be thrown away, leaving "FAILED" with nothing to
            # act on. Keep the tail of what it said — that is the whole diagnosis.
            tail = ((proc.stderr or "") + (proc.stdout or "")).strip().splitlines()
            remap_error = " / ".join(tail[-3:])[:400] if tail else None
    except subprocess.TimeoutExpired:
        rc = -1
        remap_error = "map_products timed out after 1800s"
    except Exception as exc:
        rc = -1
        remap_error = f"{type(exc).__name__}: {exc}"[:400]
    if rc != 0:
        try:
            c = sqlite3.connect(os.path.join(HERE, "ads_data.sqlite"))
            c.execute("CREATE TABLE IF NOT EXISTS engine_meta"
                      " (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
            c.execute("INSERT INTO engine_meta(key,value,updated_at)"
                      " VALUES('econ_stale','1',datetime('now'))"
                      " ON CONFLICT(key) DO UPDATE SET value='1',"
                      " updated_at=datetime('now')")
            c.commit(); c.close()
        except Exception:
            pass
    return {"adopted": os.path.basename(dest),
            # The ABSOLUTE path the file now lives at. The app used to rebuild
            # this itself from its engine-folder setting, which points at
            # Ads/engine, so it guessed Ads/ and every follow-up call looked for
            # the file one folder too deep.
            "path": dest,
            "moved_to_pod": moved,
            "catalog_cache": cache,
            "removed": removed, "freed_mb": round(freed / 1e6),
            "us_remap": "ok" if rc == 0 else "FAILED — US economics marked STALE",
            "us_remap_error": remap_error}


def _resolve_intake_csv(path):
    """The intake CSV, following a file that was adopted mid-flight.

    Dropping an export MOVES it into the POD folder and then rebuilds the
    product map, which takes minutes. The app only learns the new path when that
    whole call returns, so a build started in the meantime still carried the old
    path and died with "no such file" before touching Amazon.

    Resolving it here is not guessing at a folder: it is the same filename, gone
    from where the caller looked, sitting in the one folder the engine adopts
    exports into. Anything else is left exactly as the caller passed it."""
    if os.path.exists(path):
        return path
    moved = os.path.join(paths.POD_ROOT, os.path.basename(path))
    return moved if os.path.exists(moved) else path


def cmd_import_preview(args):
    args.csv = _resolve_intake_csv(args.csv)
    if not os.path.exists(args.csv):
        err(f"no such file: {args.csv}")
    mkt = markets.current()
    # Read-only: a preview reads the catalogue CSV and asks the database what
    # is already advertised. It writes nothing, so it must not create or
    # migrate the market file either.
    conn = db.connect(ro=True)
    designs, skipped = _intake_scan(args.csv, args.days)
    advertised = _advertised_asins(conn)
    new = [d for d in designs
           if d["asin"] not in advertised and not (set(d["ad_asins"]) & advertised)]
    routes = {}
    for d in new:
        key = f"Scavenger {d['series']}" if not d["lottery"] else f"Lottery + Scavenger {d['series']}"
        routes.setdefault(key, []).append(d)
    out({"market": mkt, "csv": os.path.basename(args.csv), "days": int(args.days),
         "designs_in_market": len(designs),
         "already_advertised": len(designs) - len(new),
         "new": len(new),
         "us_lottery_note": ("US tees fill 'Lotto N' campaigns to 1000, then 'Lotto 9', 'Lotto 10'…"
                             if markets.is_default() else None),
         "routes": [_intake_route(k, v) for k, v in sorted(routes.items())],
         "skipped_types": skipped})


# How many designs one route may carry back. 0 means every one of them.
#
# It used to be 2000, silently: `count` was the true total and `designs` was
# the first 2000, with nothing saying so. The app builds the designs it was
# SENT — "select all" ticks that slice and the build writes it — so a cohort
# of 5,000 read as 5,000 on the header, showed 2,000, and gave the other 3,000
# no ads at all. scavenger_build's own coverage report could not catch it
# either: it is measured against the scope file, which only ever held the
# 2,000. The whole 90-day US window is 29,485 designs at ~224 bytes each, so
# sending all of them is 6.6 MB on the largest market in the account —
# the same order as `accumulated-asins --limit 0`, which is one-shot for the
# same reason. `returned` and `truncated` are reported so a cap can never
# again be invisible.
INTAKE_ROUTE_CAP = 0


def _intake_route(name, designs):
    listed = designs[:INTAKE_ROUTE_CAP] if INTAKE_ROUTE_CAP else designs
    return {"route": name, "count": len(designs), "returned": len(listed),
            "truncated": len(listed) < len(designs), "designs": listed}


def _scavenger_coverage(path, scoped):
    """Read scavenger_build's coverage file back, or say plainly that it is missing.

    The Import screen used to report the REQUEST and call it Complete. On
    2026-08-22 that read "US · Drinkware 723" over zero drinkware ads: the
    series had been dropped from the plan before a single write, and nothing in
    the reply could tell the difference. This carries the builder's own count of
    what it planned, what it added, what it dropped past the shard cap, and
    which campaigns took ads while PAUSED.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            rep = json.load(fh)
    except (OSError, ValueError) as e:
        return {"available": False,
                "note": f"the builder wrote no coverage report ({type(e).__name__}); "
                        f"treat this run as unverified", "scoped": scoped}
    rep["available"] = True
    rep.pop("unplanned_sample", None)   # bulk; counts are what a screen needs
    return rep


def cmd_import_apply(args):
    """Build campaigns for the APPROVED intake ASINs (stdin: {"asins": [...]} — retail
    ASINs from the preview; omit stdin to build every new design in the file).
    Runs lottery_build (standard tees, all markets) + scavenger_build scoped to those designs."""
    _guard_kill()
    args.csv = _resolve_intake_csv(args.csv)
    if not os.path.exists(args.csv):
        err(f"no such file: {args.csv}")
    mkt = markets.current()
    if markets.is_kdp():
        err(f"import-apply builds Merch lottery/scavenger campaigns and refuses a KDP "
            f"profile (market={mkt}, kind=kdp); KDP campaigns are built by kdp_build.py. "
            f"This is the belt to the builders' suspenders after the Aug 2026 "
            f"'LOTTO - N'-under-books incident.")
    conn = db.connect()
    designs, _ = _intake_scan(args.csv, args.days)
    advertised = _advertised_asins(conn)
    new = [d for d in designs
           if d["asin"] not in advertised and not (set(d["ad_asins"]) & advertised)]
    if not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
        if raw:
            try:
                approved = {a.upper() for a in json.loads(raw).get("asins", [])}
            except Exception as e:
                err(f"could not parse approved ASINs from stdin: {e}")
            new = [d for d in new if d["asin"] in approved]
    if not new:
        result = {"market": mkt, "built": 0, "note": "nothing new to build"}
        if not args.no_adopt:
            try:
                result["export"] = _adopt_export(args.csv)
            except Exception as e:
                result["export_error"] = str(e)
        out(result)
        return

    os.makedirs(os.path.join(HERE, "outputs"), exist_ok=True)
    import datetime
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    # Per-cohort counts for the app's result row. "1673 designs · lottery scoped
    # to 295" says how the builders were called; this says what was actually
    # built, in the operator's own words — Tees, Drinkware, Hats, Hoodies.
    cohorts = {}
    for d in new:
        cohorts[d["series"]] = cohorts.get(d["series"], 0) + 1
    result = {"market": mkt, "designs": len(new),
              "cohorts": [{"series": k, "count": v} for k, v in
                          sorted(cohorts.items(), key=lambda kv: (-kv[1], kv[0]))]}

    lotto_tees = [d["asin"] for d in new if d["lottery"]]
    scav_asins = sorted({a for d in new for a in d["ad_asins"]})

    def run_builder(script, scope, label):
        scope_path = os.path.join(HERE, "outputs", f"intake_{label}_{stamp}.txt")
        with open(scope_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(scope))
        p = subprocess.run(
            [sys.executable, _engine_script(script),
             "--apply", "--auto", "--export", os.path.abspath(args.csv),
             "--asins-file", scope_path],
            cwd=HERE, env=dict(os.environ), capture_output=True, text=True, timeout=3600)
        return {"scoped_to": len(scope), "code": p.returncode,
                "text": (p.stdout or "")[-2500:], "stderr": (p.stderr or "")[-500:]}

    if lotto_tees:
        result["lottery"] = run_builder("lottery_build.py", lotto_tees, "lottery")
    # The builder's own account of what it could NOT cover. Delete any older copy
    # first: a coverage file left by a previous run reads exactly like this run's,
    # and "the check did not run" must never be indistinguishable from "the check
    # passed". Absent afterwards is itself reported.
    cov_path = os.path.join(HERE, "outputs", f"scav_build_{mkt}.json")
    try:
        os.remove(cov_path)
    except OSError:
        pass
    result["scavenger"] = run_builder("scavenger_build.py", scav_asins, "scavenger")
    result["coverage"] = _scavenger_coverage(cov_path, len(scav_asins))
    db.log_write(conn, "intake_build", "import", os.path.basename(args.csv),
                 f"{len(new)} designs (lottery {len(lotto_tees)}, scavenger {len(scav_asins)})",
                 "", "submitted")
    # AFTER the builders (they read the file at its original path): adopt the
    # export as the engine's canonical catalog + delete superseded ones.
    # --no-adopt = the app is looping several markets and adopts once at the end.
    if not args.no_adopt:
        try:
            result["export"] = _adopt_export(args.csv)
        except Exception as e:
            result["export_error"] = str(e)
    out(result)


def cmd_adopt_export(args):
    """Adopt the newest export as canonical + prune superseded ones — the app
    calls this once after an all-markets intake loop."""
    out({"export": _adopt_export(args.csv)})


def cmd_status(args):
    """LIVE status via status.py (already market-aware). Returns its text for now."""
    p = subprocess.run([sys.executable, _engine_script("status.py"), *[a.upper() for a in args.asins]],
                       cwd=HERE, env=dict(os.environ), capture_output=True, text=True, timeout=180)
    out({"market": markets.current(), "asins": [a.upper() for a in args.asins],
         "text": (p.stdout or "").strip(), "stderr": p.stderr.strip(), "code": p.returncode})


# The full nightly is a seven-market loop that really does take hours; a single
# phase is one script against one market. Both are ceilings against a hang, not
# expectations — see the measurement in cmd_run.
FULL_RUN_TIMEOUT_SECS = 6 * 3600
PHASE_TIMEOUT_SECS = 2 * 3600


def _nightly_script():
    """Absolute path to run_scheduled.sh.

    It sits BESIDE the engine folder — repo root in a checkout,
    Contents/Resources in the bundle — and never in the DATA folder. This used
    to be os.path.join(HERE, ...), and HERE is the data folder, so a standalone
    install whose data directory holds databases but no checkout ran a script
    that is not there and got exit 127. Same class as the bug the comment on
    _engine_script describes, two functions above it.
    """
    beside = os.path.join(os.path.dirname(paths.ENGINE_DIR), "run_scheduled.sh")
    if os.path.exists(beside):
        return beside
    return os.path.join(HERE, "run_scheduled.sh")   # last resort: the old place


def _kill_group(proc):
    """Stop a timed-out child AND everything it started.

    `subprocess.run(timeout=…)` kills only the direct child. For the nightly
    that is /bin/bash, and the market loop underneath it keeps running — and
    keeps writing to the live account — while the app reports the run failed.
    The child is started in its own session so the whole group can be signalled.
    """
    import signal
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.wait(timeout=10)
    except Exception:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def _last_run_status(newer_than=None):
    """What the nightly wrote about itself — but only if THIS run wrote it.

    run_scheduled.sh sends everything it prints into outputs/scheduled_runs.log,
    so the caller's stdout says almost nothing however the night went. The failed
    steps and the gated markets are in the status file.

    A file left by YESTERDAY's run reads exactly like this one's, so a run that
    died before writing one would be reported with the previous outcome.
    `newer_than` is the moment this run was launched; anything older is no
    answer at all.
    """
    path = os.path.join(HERE, "outputs", "last_run_status.json")
    try:
        with open(path, encoding="utf-8") as fh:
            got = json.load(fh)
    except (OSError, ValueError):
        return None
    if newer_than and (got.get("started") or "") < newer_than:
        return None
    return got


def cmd_run(args):
    """Trigger a phase (or the whole market run) for the current market.

    A full run answers with what it wrote about itself. Its own exit code was
    the exit code of the last `echo` in the script — 0 forever — and its output
    went to the log, so the reply used to be an empty pane and a cheerful zero
    whatever failed.
    """
    script = {"phase2": "phase2_apply.py", "phase3": "phase3_bids.py",
              "harvest": "harvest.py", "pull": "phase0_pull.py",
              "promote": "phase4_harvest_create.py",
              "promote-asins": "phase4b_harvest_asins.py"}.get(args.phase)
    if args.phase and not script:
        err(f"unknown phase '{args.phase}'")
    if args.phase != "pull":
        _check_econ_gate()
    cmd = ([sys.executable, _engine_script(script), "--apply", "--auto"] if script
           else ["/bin/bash", _nightly_script()])
    # Measured, not guessed. The last complete nightly ran 2026-08-23 10:00:03
    # to 12:43:16 — 9,793 seconds across all seven markets, and it succeeded.
    # The ceiling here was 3,600, so the app's own full-run button killed a
    # healthy run after some markets had already written to the live account
    # and before the rest had run at all. A single phase is a fraction of that.
    timeout = PHASE_TIMEOUT_SECS if script else FULL_RUN_TIMEOUT_SECS
    started_at = datetime.datetime.now().isoformat(timespec="seconds")
    # Popen, not run(): a timeout has to reach the whole process group, and
    # run() hands back no handle to signal.
    proc = subprocess.Popen(cmd, cwd=HERE, env=dict(os.environ),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True)
    try:
        stdout, _stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        err(f"the run passed {timeout}s and was stopped. Partial work may have "
            f"been applied — check outputs/scheduled_runs.log and the Audit Trail.")
    result = {"ran": args.phase or "full", "text": (stdout or "")[-4000:],
              "code": proc.returncode}
    if not script:
        # The whole nightly. Its own outcome is in the status file, including
        # the markets whose economics gate closed — those ran their reads and
        # applied nothing, which is not a failed step and must still be said.
        status = _last_run_status(newer_than=started_at)
        result["last_run"] = status
        if status is None:
            result["note"] = ("this run wrote no status file, so what it did could "
                              "not be read back. Treat it as unverified and check "
                              "outputs/scheduled_runs.log.")
    out(result)


def cmd_serve(args):
    """Long-running mode for the app's fast bridge: one JSON argv array per
    stdin line (e.g. ["campaigns","--type","lottery"]), one envelope per stdout
    line. Skips python startup per call. The app runs one serve process per
    market (db.py binds its DB file at import from ADS_MARKET).

    The app reads exactly ONE stdout line per request, so this loop guarantees
    exactly one envelope per request no matter what a handler does. While a
    handler runs, sys.stdout is redirected into a throwaway sink, so any stray
    print() (a warning, a progress note anywhere in the huge call tree) can no
    longer land in the pipe ahead of the envelope and desync every reply after
    it — the USKDP contract-mismatch cascade of Aug 2026. Envelopes go straight
    to the pinned real pipe via _RESP_STREAM; stray text is echoed to stderr so
    it stays visible without corrupting the protocol."""
    global _RESPONDED, _RESP_STREAM
    real_stdout = sys.stdout
    _RESP_STREAM = real_stdout          # out()/err() always write here
    sink = io.StringIO()

    def _fail(message):
        """AT MOST ONE error envelope for this request, and mark it answered.

        Exactly one line per request is the whole protocol, and the error path
        broke it in both directions.

        Two lines: the handlers below printed without setting the flag, so the
        "no response produced" backstop after them fired as well. The app reads
        one line per request, so from that moment every reply on this worker
        belonged to the PREVIOUS request — silently, with no error, until the
        app was restarted. That is how the dashboard came to show the kill
        list's numbers under "Monthly history". It only surfaced when a request
        failed, which in practice meant while the nightly held the database.

        Two lines again, differently: err() already prints an envelope and then
        exits, so wrapping that in an unconditional print here produced its own
        duplicate. Hence the flag is checked, not just set.

        Same failure mode the stray-print sink was built to stop. The error path
        was simply never covered by it.
        """
        global _RESPONDED
        if _RESPONDED:
            return
        _RESPONDED = True
        print(json.dumps({"ok": False, "error": message}), file=real_stdout)

    try:
        while True:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            _RESPONDED = False
            sink.seek(0)
            sink.truncate(0)
            sys.stdout = sink           # swallow any stray handler stdout
            try:
                argv = json.loads(line)
                ns = PARSER.parse_args(argv)
                if ns.cmd == "serve":
                    raise ValueError("nested serve is not allowed")
                DISPATCH[ns.cmd](ns)
            except SystemExit as e:
                # A string code carries the reason (an unknown ADS_MARKET, a bad
                # flag). "bad arguments" threw that away and made the worker path
                # harder to diagnose than the one-shot path.
                _fail(e.code if isinstance(e.code, str) else "bad arguments")
            except Exception as e:
                _fail(str(e))
            finally:
                sys.stdout = real_stdout
            if not _RESPONDED:
                # a handler returned without emitting an envelope — never leave
                # the pipe silent, or the app blocks waiting for a line.
                print(json.dumps({"ok": False, "error": "no response produced"}),
                      file=real_stdout)
            stray = sink.getvalue()
            if stray.strip():
                sys.stderr.write(stray)
                sys.stderr.flush()
            real_stdout.flush()
    finally:
        sys.stdout = real_stdout
        _RESP_STREAM = None



# ---- Amazon Marketing Stream ------------------------------------------------
# Stream pushes hourly performance rows into an SQS queue we own, instead of us
# asking Amazon to build a report and waiting out its 25-minute poll. It cannot
# replace phase0_pull.py: a subscription starts the clock and Stream never sends
# anything about the past, so history and the Monday true-up stay with reports.
def cmd_stream_status(args):
    """Subscriptions for this market, queue depth, and what has been banked."""
    import ads_client
    import stream_drain
    import stream_api

    env = ads_client.load_env()
    data = stream_drain.status(env)
    data["market"] = markets.current()
    try:
        api = stream_api.StreamAPI()
        data["realm"] = api.realm
        data["region"] = api.region
        data["subscriptions"] = api.list_subscriptions()
    except Exception as e:
        # A queue summary is still useful when Amazon is unreachable, so this
        # reports the reason instead of failing the whole command.
        data["subscriptions_error"] = str(e)
    out(data)


def cmd_stream_setup(args):
    """The AWS side, spelled out: queue names to create, and the policy to paste.

    Generated rather than written down, because each dataset publishes from a
    DIFFERENT Amazon AWS account. A policy naming the wrong one fails silently —
    the subscription reads ACTIVE and no message ever arrives.
    """
    import ads_client
    import stream_config
    import stream_api

    env = ads_client.load_env()
    api = stream_api.StreamAPI()
    realm, region = api.realm, api.region

    queues = []
    for dataset in stream_config.DATASETS:
        entry = {
            "dataset": dataset,
            "realm": realm,
            "region": region,
            "suggested_queue_name": f"merchads-{dataset}-{realm.lower()}",
            "env_key": stream_config.env_key(realm, dataset),
            "configured": bool(stream_config.queue_url(env, realm, dataset)),
            "publisher_account": stream_config.PUBLISHER_ACCOUNT[realm][dataset],
        }
        url = stream_config.queue_url(env, realm, dataset)
        if url:
            info = stream_config.parse_queue_url(url)
            entry["queue_arn"] = info["arn"]
            entry["policy"] = stream_config.queue_policy(info["arn"], realm, dataset)
        elif getattr(args, "queue_url", None):
            info = stream_config.parse_queue_url(args.queue_url)
            entry["queue_arn"] = info["arn"]
            entry["policy"] = stream_config.queue_policy(info["arn"], realm, dataset)
        queues.append(entry)

    out({"market": markets.current(), "realm": realm, "region": region,
         "aws_configured": bool(stream_config.aws_keys(env)),
         "reviewer_role": stream_config.REVIEWER_ROLE_ARN,
         "queues": queues,
         "note": "Create the queue in the region named above — a NA subscription "
                 "cannot deliver to an EU queue. Then put its URL in .env under "
                 "env_key and run stream-subscribe."})


def cmd_stream_subscribe(args):
    """LIVE: start the hourly push for one dataset on this market.

    Writes nothing to any campaign, but it does change the advertising account,
    so it goes through the same KILL gate and writes_log as every other write.
    """
    import ads_client
    import stream_config
    import stream_api

    _guard_kill()
    dataset = args.dataset
    env = ads_client.load_env()
    api = stream_api.StreamAPI()
    url = stream_config.queue_url(env, api.realm, dataset)
    if not url:
        err(f"No queue configured for {api.realm}/{dataset}. Add "
            f"{stream_config.env_key(api.realm, dataset)}=<sqs queue url> to .env "
            "(appctl stream-setup prints what to create).")
    try:
        arn = stream_config.parse_queue_url(url)["arn"]
    except ValueError as e:
        err(str(e))
    try:
        result = api.create_subscription(dataset, arn, notes=args.notes or "")
    except Exception as e:
        err(e)

    conn = db.connect()
    db.log_write(conn, "stream_subscribe", "subscription",
                 str(result.get("subscriptionId") or ""),
                 f"{dataset} -> {arn}", None, "success")
    conn.close()
    out({"market": markets.current(), "dataset": dataset, "destination": arn,
         "subscription": result,
         "next": "Run `appctl stream-drain` once to answer the SNS handshake. "
                 "Until that is done the subscription stays PENDING and no data "
                 "arrives."})


def cmd_stream_unsubscribe(args):
    """LIVE: archive one subscription. Amazon has no delete; ARCHIVED is off."""
    import stream_api

    _guard_kill()
    api = stream_api.StreamAPI()
    try:
        result = api.archive_subscription(args.subscription)
    except Exception as e:
        err(e)
    conn = db.connect()
    db.log_write(conn, "stream_unsubscribe", "subscription", str(args.subscription),
                 "archived", None, "success")
    conn.close()
    out({"market": markets.current(), "subscription": result})


def cmd_stream_drain(args):
    """Pull whatever is waiting in the queues into stream_data.sqlite.

    Also answers the SNS handshake, which is what actually starts the flow after
    a new subscription. Reads AWS, writes a local file — no Amazon Ads write, so
    it is safe alongside the nightly.
    """
    import ads_client
    import stream_config
    import stream_drain
    import stream_store

    env = ads_client.load_env()
    keys = stream_config.aws_keys(env)
    if not keys:
        err("AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY are not in .env, so the "
            "queue cannot be read. See docs/marketing-stream.md.")
    queues = stream_drain.configured_queues(env, getattr(args, "realm", None))
    if not queues:
        err("No STREAM_QUEUE_* entries in .env — nothing to drain. "
            "See docs/marketing-stream.md.")

    conn = stream_store.connect()
    summaries = []
    try:
        for realm, dataset, url, region in queues:
            summaries.append(stream_drain.drain_queue(
                conn, keys, realm, dataset, url, region, int(args.seconds),
                verbose=False))
        banked = stream_store.coverage(conn)
    finally:
        conn.close()
    out({"queues": summaries, "banked": banked,
         "total_banked": sum(s["banked"] for s in summaries),
         "handshakes_confirmed": sum(s["confirmations"] for s in summaries)})


def cmd_stream_today(args):
    """What Marketing Stream knows about this market's day, about an hour behind.

    The report pipeline is a day behind by design, so until Stream landed the
    app's freshest number was yesterday. This is the first endpoint that can
    answer "what has today cost me so far".

    It reports cost, impressions and clicks, and REFUSES to report sales, ACOS or
    conversion rate: those live in the sp-conversion dataset, which is a separate
    subscription. `conversions.available` says which, and no zero is ever put in
    place of a number we do not have.
    """
    import stream_map

    day = _iso_date_arg(getattr(args, "day", None), "--day")
    out(stream_map.summary(market=markets.current(), day=day))


def cmd_stream_advertisers(args):
    """Which advertising account each Stream message belongs to, and how we know.

    One SQS queue serves a whole realm, so the five EU markets arrive mixed and
    the payload carries no country. Resolution goes through the campaign ids,
    not the marketplace id — Merch US and KDP US share marketplace ATVPDKIKX0DER.
    `--refresh` re-resolves instead of reading the cache.
    """
    import stream_map, stream_store

    # A read must not create the database. Opening read-write here was enough
    # to leave a stream_data.sqlite behind on a folder Stream had never touched,
    # after which `stream-fields` and `stream-today` dropped the sentence saying
    # nothing had ever been drained.
    if not os.path.exists(stream_store.db_path()):
        out({"advertisers": [], "resolved": 0, "unresolved": 0,
             "banked": False,
             "note": "No stream_data.sqlite yet — nothing drained."})
        return
    conn = stream_store.connect(ro=False)
    try:
        resolved = stream_map.advertiser_map(conn, refresh=bool(getattr(args, "refresh", False)))
    finally:
        conn.close()
    out({"advertisers": sorted(resolved.values(), key=lambda e: e["advertiser_id"]),
         "resolved": sum(1 for e in resolved.values() if e.get("market")),
         "unresolved": sum(1 for e in resolved.values() if not e.get("market"))})


def cmd_stream_verify(args):
    """Did Stream actually deliver the whole day? Compared against the report.

    Every other check on the pipeline proves it reads faithfully what ARRIVED.
    None of them can prove Amazon SENT everything, and that is the failure that
    hides: the totals stay internally consistent, the queues stay empty, the
    drain log stays green, and the number is simply low.

    So this measures one SETTLED day twice — once from Stream, once from
    `campaign_daily`, which the nightly banks from Amazon's own daily report —
    and compares them per campaign. With no `--day` it picks the newest day
    Stream holds whole.

    It REFUSES to compare a day Stream could not have seen whole, or one the
    report has not banked yet. A day that is expected to read low proves
    nothing, and calling it a discrepancy would teach the reader to ignore the
    check.
    """
    import stream_verify
    out(stream_verify.verify(market=markets.current(), day=getattr(args, "day", None)))


def cmd_stream_fields(args):
    """Which fields the banked payloads actually carry, counted from real rows.

    The mapping into per-market daily tables is written against THIS, not against
    a documentation page. Empty until the first hour of data lands.
    """
    import stream_store

    conn = stream_store.connect(ro=True)
    if conn is None:
        out({"banked": False, "note": "No stream_data.sqlite yet — nothing drained."})
        return
    try:
        coverage = stream_store.coverage(conn)
        # An EMPTY database means the same thing to a reader as no database at
        # all, so it gets the same sentence. Without this the reply was a bare
        # `{"datasets": {}, "coverage": []}`, which reads as "the census found
        # nothing" rather than "nothing has ever been drained".
        if not coverage:
            out({"banked": False, "datasets": {}, "coverage": [],
                 "note": "No Stream messages banked yet — nothing drained."})
            return
        data = {"banked": True,
                "datasets": {d: stream_store.field_census(conn, d)
                             for d in [row["dataset"] for row in coverage]},
                "coverage": coverage}
    finally:
        conn.close()
    out(data)


class JSONArgumentParser(argparse.ArgumentParser):
    """An argparse error has to arrive as the same envelope as everything else.

    The bridge contract is "exactly one JSON object on stdout". A missing or
    misspelled flag broke it: argparse wrote usage to stderr and exited 2, so
    the app said "appctl exited with code 2" and the actual reason sat in a
    truncated stderr tail. Subparsers are built with the parent's class, so
    every subcommand reports this way without being listed here.

    `--help` still prints plain usage and exits 0. That path is for a human at a
    terminal and never reaches the app.
    """

    def error(self, message):
        err(f"{self.prog}: {message}")


def build_parser():
    ap = JSONArgumentParser(prog="appctl")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("markets")
    sub.add_parser("metrics")
    sub.add_parser("monthly")
    sub.add_parser("periods")
    sub.add_parser("crosspurchase")
    sub.add_parser("sales-history")
    _cc = sub.add_parser("catalog-cache")
    _cc.add_argument("--rebuild", action="store_true")
    sub.add_parser("export-date")
    p = sub.add_parser("report"); p.add_argument("--start"); p.add_argument("--end")
    p = sub.add_parser("campaigndaily"); p.add_argument("--campaigns")
    p = sub.add_parser("backfill-daily"); p.add_argument("--days", default=92)
    p = sub.add_parser("campaigns"); p.add_argument("--type"); p.add_argument("--state")
    p = sub.add_parser("adgroups"); p.add_argument("--campaign", required=True)
    p = sub.add_parser("targets"); p.add_argument("--adgroup", required=True); p.add_argument("--live", action="store_true")
    p = sub.add_parser("alltargets"); p.add_argument("--limit", default=2000)
    p = sub.add_parser("searchterms"); p.add_argument("--adgroup", required=True); p.add_argument("--limit", default=200)
    p = sub.add_parser("asin"); p.add_argument("asins", nargs=1)
    p = sub.add_parser("bidhistory"); p.add_argument("--target", required=True)
    p = sub.add_parser("history"); p.add_argument("--campaign"); p.add_argument("--adgroup"); p.add_argument("--target")
    p = sub.add_parser("negatives"); p.add_argument("--adgroup", required=True)
    p = sub.add_parser("daily"); p.add_argument("--days", default=30)
    sub.add_parser("killlist")
    sub.add_parser("health")
    sub.add_parser("run-status")
    sub.add_parser("econ-gate")
    sub.add_parser("stream-status")
    p = sub.add_parser("stream-setup"); p.add_argument("--queue-url")
    p = sub.add_parser("stream-subscribe"); p.add_argument("--dataset", required=True, choices=["sp-traffic", "sp-conversion"]); p.add_argument("--notes")
    p = sub.add_parser("stream-unsubscribe"); p.add_argument("--subscription", required=True)
    p = sub.add_parser("stream-drain"); p.add_argument("--seconds", default=300); p.add_argument("--realm", choices=["NA", "EU", "FE"])
    sub.add_parser("stream-fields")
    p = sub.add_parser("stream-today"); p.add_argument("--day", help="YYYY-MM-DD in the account timezone")
    p = sub.add_parser("stream-advertisers"); p.add_argument("--refresh", action="store_true")
    p = sub.add_parser("stream-verify"); p.add_argument("--day", help="YYYY-MM-DD in the account timezone; default = newest whole day")
    p = sub.add_parser("overview"); p.add_argument("--kind", choices=["merch", "kdp"])
    p = sub.add_parser("digest"); p.add_argument("--since", required=True)
    p = sub.add_parser("bidreport"); p.add_argument("--days", default=7)
    sub.add_parser("harvest")
    sp = sub.add_parser("harvest-suggest")
    sp.add_argument("--term", required=True)
    sp.add_argument("--limit", type=int, default=50)
    sub.add_parser("stale")
    p = sub.add_parser("halo")
    p.add_argument("--min-spend", default=1.0, help="ignore designs below this ad spend")
    p.add_argument("--limit", type=int, default=300, help="0 = every design")
    sub.add_parser("alerts")
    p = sub.add_parser("demandfeed"); p.add_argument("--refresh", action="store_true")
    sub.add_parser("profit")
    p = sub.add_parser("status"); p.add_argument("asins", nargs="+")
    p = sub.add_parser("livestate"); p.add_argument("asins", nargs=1)
    p = sub.add_parser("run"); p.add_argument("--phase")
    p = sub.add_parser("kill"); p.add_argument("--on", action="store_true"); p.add_argument("--off", action="store_true")
    p = sub.add_parser("approval-mode"); p.add_argument("--on", action="store_true"); p.add_argument("--off", action="store_true")
    p = sub.add_parser("pause"); p.add_argument("--adgroup", required=True)
    p = sub.add_parser("enable"); p.add_argument("--adgroup", required=True)
    p = sub.add_parser("pause-campaign"); p.add_argument("--campaign", required=True)
    p = sub.add_parser("enable-campaign"); p.add_argument("--campaign", required=True)
    p = sub.add_parser("archive-campaign"); p.add_argument("--campaign", required=True); p.add_argument("--confirm", action="store_true")
    p = sub.add_parser("pause-target"); p.add_argument("--target", required=True)
    p = sub.add_parser("enable-target"); p.add_argument("--target", required=True)
    p = sub.add_parser("setbid"); p.add_argument("--target", required=True); p.add_argument("--bid", required=True); p.add_argument("--prev")
    p = sub.add_parser("setbudget"); p.add_argument("--campaign", required=True); p.add_argument("--budget", required=True); p.add_argument("--prev")
    p = sub.add_parser("resetbids"); p.add_argument("--apply", action="store_true")
    sub.add_parser("negatives-preview")
    sub.add_parser("negatives-apply")
    sub.add_parser("harvest-prune")
    sub.add_parser("harvest-prune-apply")
    p = sub.add_parser("negate"); p.add_argument("--campaign", required=True); p.add_argument("--adgroup", required=True); p.add_argument("--term", required=True)
    sub.add_parser("promote")
    sp = sub.add_parser("harvest-promote-group")
    sp.add_argument("--apply", action="store_true")
    p = sub.add_parser("audit"); p.add_argument("--limit", default=200); p.add_argument("--before")
    p = sub.add_parser("undo"); p.add_argument("--row", required=True)
    p = sub.add_parser("import-preview"); p.add_argument("csv"); p.add_argument("--days", default=14)
    p = sub.add_parser("import-apply"); p.add_argument("csv"); p.add_argument("--days", default=14); p.add_argument("--no-adopt", action="store_true")
    p = sub.add_parser("adopt-export"); p.add_argument("csv")
    p = sub.add_parser("sales-report"); p.add_argument("--import", dest="import_path")
    p = sub.add_parser("history-import"); p.add_argument("csv", nargs="?"); p.add_argument("--year")
    p = sub.add_parser("maxbid"); p.add_argument("--set", action="store_true"); p.add_argument("--clear", action="store_true"); p.add_argument("--target"); p.add_argument("--keyword"); p.add_argument("--budget")
    p = sub.add_parser("portfolio-cap"); p.add_argument("--set", dest="set"); p.add_argument("--clear", action="store_true")
    p = sub.add_parser("change-cap"); p.add_argument("--set", dest="set"); p.add_argument("--clear", action="store_true"); p.add_argument("--set-build", dest="set_build"); p.add_argument("--clear-build", action="store_true")
    p = sub.add_parser("prune-snapshots"); p.add_argument("--days", type=int); p.add_argument("--apply", action="store_true")
    sub.add_parser("royalties")
    p = sub.add_parser("royalty-set"); p.add_argument("--type"); p.add_argument("--price"); p.add_argument("--royalty"); p.add_argument("--note")
    p = sub.add_parser("royalty-clear"); p.add_argument("--type"); p.add_argument("--price")
    p = sub.add_parser("kdp-book"); p.add_argument("--asin"); p.add_argument("--list-price", dest="list_price"); p.add_argument("--royalty"); p.add_argument("--format"); p.add_argument("--pages"); p.add_argument("--ink"); p.add_argument("--marketplace"); p.add_argument("--file-size-mb", dest="file_size_mb"); p.add_argument("--clear", action="store_true")
    p = sub.add_parser("kdp-titles"); p.add_argument("--refresh", action="store_true")
    p = sub.add_parser("accumulated-asins"); p.add_argument("--limit", default=500); p.add_argument("--expand"); p.add_argument("--csv", action="store_true")
    p = sub.add_parser("accumulated-keywords"); p.add_argument("--limit", default=500); p.add_argument("--expand"); p.add_argument("--csv", action="store_true")
    sub.add_parser("everywhere-preview")
    sub.add_parser("everywhere-apply")
    sub.add_parser("synccal")
    sub.add_parser("watchlist")
    p = sub.add_parser("rules-validate"); p.add_argument("--rule")
    p = sub.add_parser("rules-preview"); p.add_argument("--rule")
    p = sub.add_parser("rules-run"); p.add_argument("--apply", action="store_true"); p.add_argument("--rule")
    sub.add_parser("rules-list")
    p = sub.add_parser("rules-get"); p.add_argument("--rule", required=True)
    sub.add_parser("rules-save")
    p = sub.add_parser("rules-delete"); p.add_argument("--rule", required=True)
    sub.add_parser("rules-nightly")
    sub.add_parser("rules-collect")
    sub.add_parser("rules-pending")
    sub.add_parser("rules-approve")
    sub.add_parser("rules-discard")
    sub.add_parser("seasons")
    p = sub.add_parser("season-tag"); p.add_argument("--asin", required=True); p.add_argument("--season"); p.add_argument("--clear", action="store_true")
    p = sub.add_parser("season-define"); p.add_argument("--name", required=True); p.add_argument("--label"); p.add_argument("--resume", required=True); p.add_argument("--pause", required=True)
    p = sub.add_parser("season-suggest"); p.add_argument("--apply", action="store_true")
    p = sub.add_parser("season-tag-csv"); p.add_argument("--csv", required=True); p.add_argument("--season", required=True); p.add_argument("--apply", action="store_true")
    sub.add_parser("seasonal-preview")
    sub.add_parser("seasonal-apply")
    sub.add_parser("serve")
    return ap


def cmd_maxbid(args):
    """Per-market write ceilings. --get (default) reads; --set writes the given
    --target/--keyword/--budget (empty string clears that surface); --clear
    unsets all. budget caps the DAILY campaign budget any write may set — bids
    had a ceiling, budgets had nothing between a typo and a $400/day campaign.
    Local config only (no Amazon call)."""
    _guard_kill()
    # A pure read opens read-only, so asking what the ceiling is cannot create
    # or migrate the market database. Only --set/--clear need a writer.
    writing = bool(args.set or args.clear)
    conn = db.connect() if writing else db.connect(ro=True)
    if args.set:
        for surface, value in (("target", args.target), ("keyword", args.keyword),
                               ("budget", args.budget)):
            if value is not None:
                db.set_bid_ceiling(conn, surface,
                                   float(value) if value != "" else None)
    elif args.clear:
        for surface in ("target", "keyword", "budget"):
            db.set_bid_ceiling(conn, surface, None)
    t = db.get_bid_ceiling(conn, "target")
    k = db.get_bid_ceiling(conn, "keyword")
    b = db.get_bid_ceiling(conn, "budget")
    out({"market": markets.current(),
         "target": f"{t:.2f}" if t is not None else None,
         "keyword": f"{k:.2f}" if k is not None else None,
         "budget": f"{b:.2f}" if b is not None else None})


def cmd_change_cap(args):
    """Show, set or clear this market's VOLUME cap on one automatic run.

    No flags = show. `--set N` sets it, `--set 0` turns it off, `--clear` puts
    the shipped default back. Local config only (no Amazon call).

    There are TWO caps. `--set` / `--clear` are the CHANGE cap: bids, budgets,
    states, negatives, archives, and creating a campaign. `--set-build` /
    `--clear-build` are the BUILD cap: ad groups, product ads, keywords and
    targeting clauses created inside a campaign by the two campaign builders.
    They are separate numbers because they have separate normal volumes — a
    busy change night is tens of writes, a busy build night is thousands.

    Unlike every other guard, these count rather than judge. A rule whose
    condition is one character too loose matches tens of thousands of rows, and
    the KILL file, the econ gate, the snapshot gate, the conflict guard and the
    bid ceiling would every one of them wave it through. Past the cap the run
    applies NOTHING — see db.get_auto_change_cap for where the default comes
    from. `rules-approve` is exempt: those ids were picked by hand.
    """
    _guard_kill()
    writing = bool(args.clear or args.set is not None
                   or getattr(args, "clear_build", False)
                   or getattr(args, "set_build", None) is not None)
    conn = db.connect() if writing else db.connect(ro=True)
    # Validate BOTH numbers before writing EITHER. This used to store the
    # change cap and then refuse on the build cap, so `--set 0 --set-build junk`
    # reported an error and left the change guard switched off — a command that
    # looks like it failed, and a nightly that runs uncapped.
    change_n = build_n = None
    if args.set is not None:
        try:
            change_n = int(args.set)
        except (TypeError, ValueError):
            err("--set needs a whole number of changes, e.g. --set 500")
            return
        if change_n < 0:
            err("a change cap cannot be negative; use --set 0 for no cap")
            return
    if getattr(args, "set_build", None) is not None:
        try:
            build_n = int(args.set_build)
        except (TypeError, ValueError):
            err("--set-build needs a whole number of entities, e.g. --set-build 50000")
            return
        if build_n < 0:
            err("a build cap cannot be negative; use --set-build 0 for no cap")
            return
    # One transaction for both, so a failure on the second cannot leave the
    # first standing under a command that reports an error.
    edits = {}
    if args.clear:
        edits["change"] = None
    elif change_n is not None:
        edits["change"] = change_n
    if getattr(args, "clear_build", False):
        edits["build"] = None
    elif build_n is not None:
        edits["build"] = build_n
    if edits:
        db.set_auto_caps(conn, **edits)
    cap = db.get_auto_change_cap(conn)
    build = db.get_auto_build_cap(conn)
    out({"market": markets.current(), "auto_change_cap": cap,
         "default": db.AUTO_CHANGE_CAP_DEFAULT,
         "capped": bool(cap),
         "auto_build_cap": build,
         "build_default": db.AUTO_BUILD_CAP_DEFAULT,
         "build_capped": bool(build),
         "note": ("no cap — one automatic run may apply any number of changes"
                  if not cap else
                  f"one automatic run applies nothing if it proposes more than "
                  f"{cap} changes"),
         "build_note": ("no cap — one automatic run may create any number of "
                        "entities inside campaigns"
                        if not build else
                        f"one automatic build stops if it would create more than "
                        f"{build} entities inside campaigns")})


def cmd_prune_snapshots(args):
    """Count (or delete) perf-snapshot rows older than the retention window.

    Preview by default; `--apply` deletes. `--days N` overrides the window.
    Local database only, no Amazon call.

    The three perf tables gain a row per entity per pull and nothing had ever
    removed one. The default window is deliberately far past anything on disk,
    so this reports zero until the account is more than a year old — it caps the
    future rather than reclaiming the present. Deleting does not shrink the
    file; SQLite reuses the pages, which is exactly what bounds growth.
    """
    conn = db.connect() if args.apply else db.connect(ro=True)
    try:
        res = db.prune_snapshots(conn, days=args.days, apply=bool(args.apply))
    except ValueError as e:
        err(str(e))
        return
    out({"market": markets.current(), **res,
         "note": ("deleted" if args.apply and res["total"] else
                  "nothing is older than the window" if not res["total"] else
                  "preview only — nothing was deleted; pass --apply")})


def cmd_portfolio_cap(args):
    """Show, set, or clear the current market's monthly portfolio-spend cap (the
    R8 guard). No flags = show; --set N sets the dollar cap; --clear removes it.
    Nothing enforces it as a hard stop — the alerts feed warns as month-to-date
    pooled spend nears it. Local config only (no Amazon call)."""
    _guard_kill()
    writing = bool(args.clear or args.set is not None)
    conn = db.connect() if writing else db.connect(ro=True)
    if args.clear:
        db.set_portfolio_cap(conn, None)
    elif args.set is not None:
        db.set_portfolio_cap(conn, float(args.set))
    cap = db.get_portfolio_cap(conn)
    out({"market": markets.current(),
         "portfolio_monthly_cap": f"{cap:.2f}" if cap is not None else None})


def _royalty_design_counts():
    """product type -> how many ad groups advertise it in this market. Tells the
    operator which royalties actually move money, so the list can lead with them."""
    try:
        conn = db.connect(ro=True)
    except Exception:
        return {}
    try:
        return {r[0]: r[1] for r in conn.execute(
            """SELECT product_type, COUNT(*) FROM ad_group_product
               WHERE product_type IS NOT NULL GROUP BY product_type""")}
    finally:
        conn.close()


def _royalty_label(product_type):
    return (product_type or "").replace("_", " ").strip().title()


def cmd_royalties(args):
    """Every royalty the engine prices with, and where each number came from.

    EVERY market is editable, and the reply says so unconditionally. An
    untouched row comes from the built-in tables in products.py (US) or from what
    derive_econ.py worked out of the export (everywhere else); the operator's
    royalty_overrides.json is merged over both and always wins. Amazon fixes a
    maximum price per product per market, so the figure read off the Merch
    dashboard beats a derived median. Only the US TEE LADDER is US-only — every
    other market prices one royalty per product type.

    Local config + DB only — no Amazon call."""
    import royalty_config
    mkt = markets.current()
    counts = _royalty_design_counts()
    over = royalty_config.load(mkt)
    has_ladder = mkt == markets.DEFAULT      # only US tees are priced per rung

    tee_rows = []
    if has_ladder:
        table = products.tee_royalty_table()
        extrapolated = products.tee_extrapolated()
        for cents in sorted(table):
            roy = table[cents]
            ov = over["tee_prices"].get(cents)
            raw = (_royalty_raw_overrides().get("tee_prices") or {}).get(str(cents)) or {}
            tee_rows.append({
                "price_cents": cents, "price": round(cents / 100.0, 2),
                "royalty_cents": roy, "royalty": round(roy / 100.0, 2),
                "break_even": round(roy / cents, 6),
                "source": "operator" if ov is not None else "built-in",
                "extrapolated": cents in extrapolated,
                "growth_priced": cents < products.US_TEE_GROWTH_FLOOR_CENTS,
                "note": raw.get("note"), "updated_at": raw.get("updated_at"),
            })

    type_rows = []
    if has_ladder:
        for name in sorted(products.product_econ_table()):
            e = products.get_econ(name)
            ov = over["product_types"].get(name)
            price = products.list_price_for(name, market=mkt)
            type_rows.append({
                "product_type": name, "label": _royalty_label(name),
                "royalty": round(e["royalty"], 2),
                "price": round(price, 2) if price else None,
                "break_even": round(e["break_even"], 6),
                "model": e["model"],
                "neg_threshold": e["neg_threshold"],
                "pause_threshold": e["pause_threshold"],
                "ad_groups": counts.get(name, 0),
                "source": "operator" if ov else "built-in",
                "note": (ov or {}).get("note"), "updated_at": (ov or {}).get("updated_at"),
            })
    else:
        conn = db.connect(ro=True)
        try:
            derived = db.get_market_econ(conn, mkt)
        finally:
            conn.close()
        # An operator number wins; a derived median fills the rest.
        shipped = products.MARKET_PRODUCT_ECON.get(mkt, {})
        for name in sorted(set(derived) | set(over["product_types"]) | set(shipped)):
            rec = derived.get(name) or {}
            ov = over["product_types"].get(name)
            e = products.get_econ(name, market=mkt)
            type_rows.append({
                "product_type": name, "label": _royalty_label(name),
                "royalty": round(e["royalty"], 2),
                "price": (lambda pr: round(pr, 2) if pr else None)(
                    products.list_price_for(name, market=mkt)),
                "break_even": round(e["break_even"], 6),
                "model": e["model"],
                "neg_threshold": e["neg_threshold"], "pause_threshold": e["pause_threshold"],
                "ad_groups": counts.get(name, 0),
                "source": ("operator" if ov else
                           "built-in" if name in shipped else "derived"),
                "listings": rec.get("n"),
                "note": (ov or {}).get("note"), "updated_at": (ov or {}).get("updated_at"),
            })

    out({
        "market": mkt, "currency": markets.cfg(mkt).get("currency"),
        "editable": True,
        "basis": _royalty_basis(tee_rows + type_rows),
        "growth_floor": round(products.US_TEE_GROWTH_FLOOR_CENTS / 100.0, 2),
        "model_version": products.US_TEE_ROYALTY_V,
        "errors": over["errors"],
        "tee_prices": tee_rows, "product_types": type_rows,
        "overrides": len(over["tee_prices"]) + len(over["product_types"]),
    })


_ROYALTY_SOURCE_LABEL = {"built-in": "the built-in table",
                         "derived": "your product export",
                         "operator": "your own edits"}


def _royalty_basis(rows):
    """Where these numbers came from, COUNTED rather than assumed.

    The old sentence was one string per market: US said "built-in table" and
    every other market said "worked out from your product export". DE is 13
    built-in rows and one derived, so the caption contradicted every badge on
    the screen and sent the operator off to re-export a catalogue that could
    not change 13 of the 14 numbers (found 2026-08-24).
    """
    counts = {}
    for row in rows:
        counts[row["source"]] = counts.get(row["source"], 0) + 1
    named = [(_ROYALTY_SOURCE_LABEL.get(k, k), counts[k])
             for k in ("built-in", "derived", "operator") if counts.get(k)]
    if not named:
        return "nothing priced for this market yet"
    if len(named) == 1:
        label = named[0][0]
        return label if label == "your own edits" else f"{label}, with your edits on top"
    return ", ".join(f"{label} for {n}" for label, n in named)


def _royalty_raw_overrides():
    import royalty_config
    try:
        with open(royalty_config.CONFIG, encoding="utf-8") as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def cmd_royalty_set(args):
    """Save one royalty override for the current market.

    Either a US tee list price: --price 25.99 --royalty 10.07
    or a product type:          --type mug --price 18.99 --royalty 3.00

    Scoped to ADS_MARKET. Only US carries the tee price ladder; every other
    market edits product types, and an operator number WINS over whatever
    derive_econ.py worked out from the export. Amazon fixes a maximum price per
    product per market, so the Merch dashboard figure is the definitive one.

    Break-even is COMPUTED from royalty / price, never typed. A value that
    cannot be a real royalty is refused and NOTHING is written."""
    import royalty_config
    if not getattr(args, "type", None) and markets.current() != markets.DEFAULT:
        err(f"{markets.current()} has no tee price ladder — name the product "
            "type instead, e.g. --type standard_tshirt.")
        return
    if args.price is None or args.royalty is None:
        err("both --price and --royalty are required")
        return
    try:
        if getattr(args, "type", None):
            saved = royalty_config.set_product_type(
                args.type, royalty=args.royalty, price=args.price, note=args.note)
        else:
            saved = royalty_config.set_tee_price(
                round(float(args.price) * 100), round(float(args.royalty) * 100),
                note=args.note)
            saved = {"price_cents": saved["price_cents"],
                     "royalty_cents": saved["royalty_cents"],
                     "break_even": round(saved["royalty_cents"] / saved["price_cents"], 6)}
    except ValueError as exc:
        err(str(exc))
        return
    out({"saved": True, **saved})


def cmd_royalty_clear(args):
    """Drop one override so the built-in value rules again."""
    import royalty_config
    if getattr(args, "type", None):
        removed = royalty_config.clear_product_type(args.type)
    elif args.price is not None and markets.current() == markets.DEFAULT:
        removed = royalty_config.clear_tee_price(round(float(args.price) * 100))
    else:
        err("--type is required (only US has a tee price ladder to clear by price)")
        return
    out({"cleared": bool(removed), "market": markets.current()})


def cmd_history_import(args):
    """Bank a monthly account-history CSV exported from the Ads CONSOLE — the
    only source that reaches back past the API's ~95-day retention. Feeds the
    `periods` back-extension (`ads_history_monthly`); once banked it is the
    only copy. No csv = report what is banked. `--year` resolves files whose
    rows don't name one (the importer refuses to guess)."""
    import history_import
    if not getattr(args, "csv", None):
        out({"imported": False, "coverage": history_import.coverage()})
        return
    meta = history_import.bank(args.csv, year=int(args.year) if getattr(args, "year", None) else None)
    out({"imported": True, "file": meta, "coverage": history_import.coverage()})


def _kdp_book_ads():
    """Per book ASIN, what the KDP ad data knows: {asin: {ad_title, advertised}}.

    `ad_title` is the ad-group name each book's campaign uses — KDP names the ad
    group after the book (e.g. "Strong on a Plate"). `advertised` is True when
    the book has at least one ENABLED ad group in an ENABLED campaign, i.e. it is
    serving right now. Reads every KDP-kind market's DB directly, because kdp-book
    is a global config command with no market of its own. A book that was never
    advertised has no ad group here, so it is absent (no ad_title, not advertised).
    Read-only and best-effort: a missing or unreadable DB is skipped."""
    info = {}
    for code, cfg in markets.MARKETS.items():
        if cfg.get("kind") != "kdp":
            continue
        path = _market_db_path(code)
        if not os.path.exists(path):
            continue
        try:
            c = db.open_readonly(path)
            try:
                for asin, name, ag_state, camp_state in c.execute(
                    """SELECT p.asin, g.name, g.state, c.state
                         FROM ad_group_product p
                         JOIN ad_groups g ON g.ad_group_id = p.ad_group_id
                         JOIN campaigns c ON c.campaign_id = g.campaign_id
                        WHERE p.asin IS NOT NULL"""):
                    a = asin.upper()
                    rec = info.setdefault(a, {"ad_title": None, "advertised": False})
                    if name and not rec["ad_title"]:
                        rec["ad_title"] = name
                    if ag_state == "ENABLED" and camp_state == "ENABLED":
                        rec["advertised"] = True
            finally:
                c.close()
        except Exception:
            continue
    return info


# Cached book titles pulled live from Amazon (SP product metadata). This is the
# ONLY title source for an un-advertised book. Regenerated by `kdp-titles
# --refresh`; the fast kdp-book read never calls Amazon.
_KDP_TITLES_PATH = os.path.join(HERE, "kdp_titles.json")


def _kdp_titles_cache():
    try:
        with open(_KDP_TITLES_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _clean_book_title(title):
    """Trim the trailing format suffix Amazon appends (", Kindle" / ", Paperback"
    / ", Hardcover" / ", Kindle Edition") so the column reads as the book's name,
    not its format. Leaves the rest of the title — subtitle and series — intact."""
    if not title:
        return title
    t = title.strip()
    for suffix in (", Kindle Edition", ", Kindle", ", Paperback", ", Hardcover", ", Audible Audiobook"):
        if t.endswith(suffix):
            return t[: -len(suffix)].strip()
    return t


def cmd_kdp_titles(args):
    """Refresh cached book titles from Amazon (SP product metadata) for every
    configured KDP book, across all KDP-kind markets. Live READ. Writes
    kdp_titles.json. Without --refresh, just reports what is cached."""
    import kdp_econ
    asins = sorted(kdp_econ.load_books().keys())
    if not getattr(args, "refresh", False):
        cache = _kdp_titles_cache()
        out({"cached": len([a for a in asins if a in cache]), "total": len(asins),
             "titles": {a: cache.get(a, {}).get("title") for a in asins}})
        return
    import ads_client
    fetched = {}
    for code, cfg in markets.MARKETS.items():
        if cfg.get("kind") != "kdp":
            continue
        missing = [a for a in asins if a not in fetched]
        if not missing:
            break
        try:
            cli = ads_client.AdsClient(market=code)
            fetched.update(cli.product_metadata(missing))
        except Exception as e:
            print(f"  {code} product-metadata failed: {e}", file=sys.stderr)
            continue
    cache = _kdp_titles_cache()
    for a, meta in fetched.items():
        if meta.get("title"):
            cache[a] = meta
    tmp = _KDP_TITLES_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, sort_keys=True)
    os.replace(tmp, _KDP_TITLES_PATH)
    out({"refreshed": True, "fetched": len(fetched),
         "total": len(asins),
         "cached": len([a for a in asins if a in cache]),
         "missing": [a for a in asins if a not in cache]})


def cmd_kdp_book(args):
    """KDP book economics config. No args = list all books with resolved econ.
    --asin X [--list-price P] [--royalty R] [--format paperback|hardcover|ebook]
    [--pages N] [--ink bw|color] [--marketplace US] [--file-size-mb M] = save.
    --asin X --clear = remove. Enter --royalty straight off your KDP dashboard
    (most accurate), or the inputs to compute. Local config only (no Amazon)."""
    import kdp_econ
    if not getattr(args, "asin", None):
        books = kdp_econ.load_books()
        ads = _kdp_book_ads()
        cache = _kdp_titles_cache()
        rows = []
        for asin, b in sorted(books.items()):
            e = kdp_econ.book_econ(asin)
            a = asin.upper()
            info = ads.get(a, {})
            # Title: the full Amazon title (covers every book) wins; the KDP
            # ad-group name is the fallback for a book fetched before the cache
            # was populated.
            title = _clean_book_title((cache.get(a) or {}).get("title")) or info.get("ad_title")
            rows.append({"asin": asin, "title": title,
                         "advertised": bool(info.get("advertised")), **b,
                         "royalty_resolved": e.get("royalty") if e else None,
                         "break_even": e.get("break_even") if e else None,
                         "known": bool(e)})
        out({"books": rows, "count": len(rows)})
        return
    asin = args.asin.strip().upper()
    if args.clear:
        kdp_econ.clear_book(asin)
        out({"asin": asin, "cleared": True})
        return
    data = {
        "list_price": float(args.list_price) if args.list_price is not None else None,
        "royalty": float(args.royalty) if args.royalty is not None else None,
        "format": args.format, "page_count": int(args.pages) if args.pages else None,
        "ink": args.ink, "marketplace": args.marketplace,
        "file_size_mb": float(args.file_size_mb) if args.file_size_mb is not None else None,
    }
    kdp_econ.save_book(asin, data)
    e = kdp_econ.book_econ(asin)
    out({"asin": asin, "saved": True,
         "royalty": e.get("royalty") if e else None,
         "break_even": e.get("break_even") if e else None,
         "known": bool(e)})


def cmd_seasons(args):
    """Seasonal scheduler config + computed status. Config is global (calling
    without ADS_MARKET is fine); per-design ad-group counts use the current market."""
    import datetime as dt
    import seasonal_pause as sp
    cfg = sp.load_config()
    seasons, tags = cfg.get("seasons", {}), cfg.get("asins", {})
    today = dt.date.today()
    mkt = markets.current()
    conn = db.connect(ro=True)
    states, types = {}, {}
    for asin, agid, state, ptype in conn.execute(
            """SELECT p.asin, p.ad_group_id, g.state, p.product_type
                 FROM ad_group_product p JOIN ad_groups g ON g.ad_group_id = p.ad_group_id
                WHERE p.asin IS NOT NULL"""):
        states.setdefault(asin, []).append(state)
        types.setdefault(asin, ptype)
    season_list = [{
        "key": k, "label": s.get("label", k), "resume": s["resume"], "pause": s["pause"],
        "active": sp.in_window(today, s["resume"], s["pause"]),
        "next_transition": sp.next_transition(today, s["resume"], s["pause"]),
        "tagged_count": sum(1 for v in tags.values() if v == k),
    } for k, s in seasons.items()]
    tag_list = []
    for asin, key in tags.items():
        s = seasons.get(key, {})
        st = states.get(asin, [])
        tag_list.append({
            "asin": asin, "season": key, "label": s.get("label", key),
            "active": sp.in_window(today, s["resume"], s["pause"]) if s else None,
            "product_type": types.get(asin), "ad_groups": len(st),
            "enabled": sum(1 for x in st if x == "ENABLED"),
            "paused": sum(1 for x in st if x == "PAUSED")})
    out({"market": mkt, "today": today.isoformat(),
         "seasons": sorted(season_list, key=lambda x: x["label"]),
         "tags": sorted(tag_list, key=lambda x: (x["label"], x["asin"]))})


def cmd_season_tag(args):
    """Tag/untag a design (ASIN) to a season. Local config write — no Amazon call."""
    import seasonal_pause as sp
    cfg = sp.load_config()
    asin = args.asin.strip().upper()
    if args.clear:
        cfg.setdefault("asins", {}).pop(asin, None)
        sp.save_config(cfg)
        out({"asin": asin, "season": None})
        return
    if not args.season or args.season not in cfg.get("seasons", {}):
        err(f"unknown season '{args.season}'. Known: {', '.join(cfg.get('seasons', {}))}")
    cfg.setdefault("asins", {})[asin] = args.season
    sp.save_config(cfg)
    out({"asin": asin, "season": args.season})


def cmd_season_define(args):
    """Add or update a season's yearly window (MM-DD). Local config write."""
    import datetime as dt
    import seasonal_pause as sp

    def _valid(md):
        try:
            m, d = md.split("-")
            dt.date(2000, int(m), int(d))
            return True
        except Exception:
            return False

    if not (_valid(args.resume) and _valid(args.pause)):
        err("resume/pause must be MM-DD (e.g. 06-05)")
    cfg = sp.load_config()
    key = args.name.strip().lower().replace(" ", "")
    if not key:
        err("season name is empty")
    cfg.setdefault("seasons", {})[key] = {
        "label": args.label or args.name, "resume": args.resume, "pause": args.pause}
    sp.save_config(cfg)
    out({"season": key, "label": cfg["seasons"][key]["label"],
         "resume": args.resume, "pause": args.pause})


def _asins_from_csv(path):
    """Pull ASINs from a CSV: prefer an 'asin' column (any case); else scan every
    cell for a Merch ASIN pattern. Deduped, order preserved. For curated seasonal
    lists the operator exports from Merch (whole file = one season's designs)."""
    import csv as csvmod
    import re
    any10 = re.compile(r"^[A-Z0-9]{10}$")
    b0only = re.compile(r"^B0[A-Z0-9]{8}$")
    found = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csvmod.DictReader(fh)
        fields = [f.lower() for f in (reader.fieldnames or [])]
        if "asin" in fields:
            key = reader.fieldnames[fields.index("asin")]
            for row in reader:
                v = (row.get(key) or "").strip().upper()
                if any10.match(v):
                    found.append(v)
        else:
            fh.seek(0)
            for row in csvmod.reader(fh):
                for cell in row:
                    v = (cell or "").strip().upper()
                    if b0only.match(v):
                        found.append(v)
    seen, out_list = set(), []
    for a in found:
        if a not in seen:
            seen.add(a)
            out_list.append(a)
    return out_list


def cmd_season_tag_csv(args):
    """Tag every design in a CSV to one season (for lists auto-detect can't catch).
    Preview by default; --apply writes. Local config write — no Amazon call."""
    import seasonal_pause as sp
    cfg = sp.load_config()
    if args.season not in cfg.get("seasons", {}):
        err(f"unknown season '{args.season}'. Known: {', '.join(cfg.get('seasons', {}))}")
    if not os.path.exists(args.csv):
        err(f"CSV not found: {args.csv}")
    asins = _asins_from_csv(args.csv)
    tags = cfg.get("asins", {})
    new = [a for a in asins if tags.get(a) != args.season]
    already = [a for a in asins if tags.get(a) == args.season]
    label = cfg["seasons"][args.season].get("label", args.season)
    base = {"season": args.season, "label": label, "csv": os.path.basename(args.csv),
            "found": len(asins), "already": len(already)}
    if not args.apply:
        out({**base, "new": len(new), "sample": asins[:10]})
        return
    for a in new:
        cfg.setdefault("asins", {})[a] = args.season
    sp.save_config(cfg)
    out({**base, "tagged": len(new)})


def cmd_season_suggest(args):
    """Auto-detect seasonal designs by title keyword. Read-only unless --apply,
    which tags the matches (optionally scoped to stdin {"asins":[…]}; empty = all)."""
    import seasonal_pause as sp
    mkt = markets.current()
    conn = db.connect(ro=True)
    suggestions = sp.suggest(conn)
    if not args.apply:
        out({"market": mkt, "suggestions": suggestions})
        return
    scope = None
    if not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
        if raw:
            try:
                scope = set(json.loads(raw).get("asins") or [])
            except Exception as e:
                err(f"could not parse approved asins from stdin: {e}")
    cfg = sp.load_config()
    applied = []
    for s in suggestions:
        if s["already_tagged"] or (scope is not None and s["asin"] not in scope):
            continue
        cfg.setdefault("asins", {})[s["asin"]] = s["season"]
        applied.append({"asin": s["asin"], "season": s["season"], "label": s["label"]})
    sp.save_config(cfg)
    out({"market": mkt, "tagged": applied, "count": len(applied)})


def cmd_seasonal_preview(args):
    """What the seasonal scheduler would do RIGHT NOW for this market (read-only)."""
    import seasonal_pause as sp
    mkt = markets.current()
    conn = db.connect(ro=True)
    to_pause, to_enable = sp.plan(conn)
    names = dict(conn.execute("SELECT ad_group_id, name FROM ad_groups"))
    enrich = lambda rows: [{**r, "name": names.get(r["ad_group_id"])} for r in rows]
    out({"market": mkt, "pause": enrich(to_pause), "enable": enrich(to_enable)})


def cmd_seasonal_apply(args):
    """Execute the seasonal pause/enable plan for this market (LIVE, KILL-gated)."""
    _guard_kill()
    import seasonal_pause as sp
    mkt = markets.current()
    conn = db.connect()
    to_pause, to_enable = sp.plan(conn)
    res = sp.apply(conn, to_pause, to_enable)
    out({"market": mkt, **res})


def cmd_catalog_cache(args):
    """The banked product catalogue: what it holds, and whether it still matches
    the export files on disk.

    The catalogue is several CSV chunks merged at read time, and the nightly
    performs that merge about twenty times across seven markets. `catalog_cache`
    banks it. It is a PURE OPTIMISATION — when the table does not match the
    files, every reader falls back to the CSVs, so a stale cache costs seconds
    and never an answer. `matches: false` is therefore a note, not a fault.

    `--rebuild` banks it now. That reads the whole catalogue once, so it takes
    about as long as one uncached read."""
    import catalog_cache
    if getattr(args, "rebuild", False):
        rows = catalog_cache.build(verbose=True)
        return out(dict(catalog_cache.status(), rebuilt=rows))
    out(catalog_cache.status())


def cmd_export_date(args):
    """New Designs 'last recorded' date: the newest design-upload date INSIDE the
    current catalogue export (max createdDate), read from the data, not the
    filename. The export is ~1.8GB / 2M+ rows and takes ~18s to scan, so the
    result is cached by the export's signature (filename|mtime) in
    outputs/export_meta.json. A fresh export scans once; every later read is
    instant."""
    import products
    import json as _json
    path = products._newest_export()
    if not path or not os.path.exists(path):
        return out({"available": False,
                    "note": "no catalogue export in the POD folder"})
    sig = products.export_signature(path)
    cache_path = os.path.join(paths.REPO_ROOT,
                              "outputs", "export_meta.json")
    cache = {}
    try:
        with open(cache_path, encoding="utf-8") as f:
            cache = _json.load(f)
    except Exception:
        cache = {}
    hit = cache.get(sig)
    if hit and hit.get("last_recorded"):
        return out({"available": True, "last_recorded": hit["last_recorded"],
                    "source": os.path.basename(path), "rows": hit.get("rows"),
                    "cached": True})
    # Read through export_reader: `createdDate` is a MerchFlow column name, and
    # a raw DictReader over a Snap file found it on no row at all — the scan
    # counted 30,000 rows and still reported "no catalogue on file".
    import export_reader
    newest = ""
    rows = 0
    for row in export_reader.rows(path):
        rows += 1
        d = (row.get("createdDate") or "")[:10]
        if d > newest:
            newest = d
    last = newest or None
    cache[sig] = {"last_recorded": last, "rows": rows}
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            _json.dump(cache, f)
    except Exception:
        pass
    return out({"available": bool(last), "last_recorded": last,
                "source": os.path.basename(path), "rows": rows, "cached": False})


def cmd_run_status(args):
    """A nightly run happening RIGHT NOW, parsed live from scheduled_runs.log
    (the status file lands only when the run finishes). {active:false} = idle."""
    import run_status
    out(run_status.current_run() or {"active": False})


DISPATCH = {"markets": cmd_markets, "metrics": cmd_metrics,
            "monthly": cmd_monthly, "periods": cmd_periods, "crosspurchase": cmd_crosspurchase, "sales-history": cmd_sales_history, "report": cmd_report, "campaigndaily": cmd_campaigndaily,
            "backfill-daily": cmd_backfill_daily,
            "campaigns": cmd_campaigns,
            "adgroups": cmd_adgroups, "targets": cmd_targets, "alltargets": cmd_alltargets,
            "searchterms": cmd_searchterms,
            "asin": cmd_asin, "bidhistory": cmd_bidhistory,
            "history": cmd_history, "negatives": cmd_negatives, "daily": cmd_daily,
            "killlist": cmd_killlist, "health": cmd_health, "run-status": cmd_run_status, "econ-gate": cmd_econ_gate,
            "overview": cmd_overview, "digest": cmd_digest,
            "bidreport": cmd_bidreport, "harvest": cmd_harvest,
            "harvest-suggest": cmd_harvest_suggest,
            "stale": cmd_stale, "halo": cmd_halo,
            "alerts": cmd_alerts,
            "sales-report": cmd_sales_report,
            "history-import": cmd_history_import,
            "catalog-cache": cmd_catalog_cache,
            "export-date": cmd_export_date,
            "demandfeed": cmd_demandfeed, "profit": cmd_profit,
            "status": cmd_status, "livestate": cmd_livestate,
            "run": cmd_run, "kill": cmd_kill, "approval-mode": cmd_approval_mode,
            "pause": cmd_pause, "enable": cmd_enable,
            "pause-campaign": cmd_pause_campaign, "enable-campaign": cmd_enable_campaign,
            "archive-campaign": cmd_archive_campaign,
            "pause-target": cmd_pause_target, "enable-target": cmd_enable_target,
            "setbid": cmd_setbid, "setbudget": cmd_setbudget, "resetbids": cmd_resetbids,
            "negatives-preview": cmd_negatives_preview, "negatives-apply": cmd_negatives_apply,
            "harvest-prune": cmd_harvest_prune, "harvest-prune-apply": cmd_harvest_prune_apply,
            "negate": cmd_negate, "promote": cmd_promote,
            "harvest-promote-group": cmd_harvest_promote_group,
            "audit": cmd_audit, "undo": cmd_undo,
            "import-preview": cmd_import_preview, "import-apply": cmd_import_apply,
            "adopt-export": cmd_adopt_export,
            "maxbid": cmd_maxbid, "portfolio-cap": cmd_portfolio_cap,
            "change-cap": cmd_change_cap,
            "prune-snapshots": cmd_prune_snapshots, "kdp-book": cmd_kdp_book,
            "royalties": cmd_royalties, "royalty-set": cmd_royalty_set,
            "royalty-clear": cmd_royalty_clear,
            "kdp-titles": cmd_kdp_titles,
            "accumulated-asins": cmd_accumulated_asins,
            "accumulated-keywords": cmd_accumulated_keywords,
            "everywhere-preview": cmd_everywhere_preview,
            "everywhere-apply": cmd_everywhere_apply,
            "synccal": cmd_synccal, "watchlist": cmd_watchlist,
            "rules-validate": cmd_rules_validate,
            "rules-preview": cmd_rules_preview,
            "rules-run": cmd_rules_run, "rules-list": cmd_rules_list,
            "rules-get": cmd_rules_get, "rules-save": cmd_rules_save,
            "rules-delete": cmd_rules_delete, "rules-nightly": cmd_rules_nightly,
            "rules-collect": cmd_rules_collect, "rules-pending": cmd_rules_pending,
            "rules-approve": cmd_rules_approve, "rules-discard": cmd_rules_discard,
            "seasons": cmd_seasons, "season-tag": cmd_season_tag,
            "season-define": cmd_season_define, "season-suggest": cmd_season_suggest,
            "season-tag-csv": cmd_season_tag_csv,
            "seasonal-preview": cmd_seasonal_preview,
            "seasonal-apply": cmd_seasonal_apply,
            "stream-status": cmd_stream_status, "stream-setup": cmd_stream_setup,
            "stream-subscribe": cmd_stream_subscribe,
            "stream-unsubscribe": cmd_stream_unsubscribe,
            "stream-drain": cmd_stream_drain, "stream-fields": cmd_stream_fields,
            "stream-today": cmd_stream_today, "stream-advertisers": cmd_stream_advertisers,
            "stream-verify": cmd_stream_verify,
            "serve": cmd_serve}

PARSER = build_parser()


def main():
    args = PARSER.parse_args()
    try:
        DISPATCH[args.cmd](args)
    except SystemExit as e:
        # Engine modules stop the process with a plain-text SystemExit —
        # markets.current() does it for an unknown ADS_MARKET. That text went to
        # stderr and left stdout empty, so the app reported an exit code with no
        # reason attached. Turn the message into the envelope, unless one was
        # already sent: err() itself exits this way, and re-wrapping it would
        # print two objects on a stream that must carry exactly one.
        if _RESPONDED or not isinstance(e.code, str):
            raise
        err(e.code)
    except Exception as e:
        err(e)


if __name__ == "__main__":
    main()
