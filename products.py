#!/usr/bin/env python3
"""
Per-product-type economics (US, Plus tier) — the brain behind per-type rules.
Source: royalty-reference.md. Each type carries its royalty, break-even ACOS,
optimization model, target ACOS, and stop-loss thresholds.

Models:
  A = standard tee only: CVR-first, push toward a 30% ceiling.
  B = everything else: bid to the product's own break-even.

Thresholds (from bidding-rules.md):
  negative-keyword: royalty * 0.5
  ad-group pause  : royalty * 0.5  (EXCEPT standard tee = $5 flat)
"""

# --- US standard-tee price-aware economics (PLAN.md v6, 2026-07-12) -----------
# Royalty is a function of the LIVE list price (from the Merch export via
# ad_group_product.list_price), NOT one hardcoded assumption. The table below is
# operator-confirmed off the Merch dashboard; the export's historical royalty
# columns LAG a reprice, so they are never used as the source (only as a
# divergence alarm). Keys are integer cents; supported domain = EXACTLY these
# keys — anything else is `unknown` and excluded from monetary actions.

US_TEE_ROYALTY_V = "2026-07-12"
US_TEE_ROYALTY_CENTS = {
    1999: 528,   # EXTRAPOLATED (dashboard-confirmed range starts at $21.99)
    2099: 608,   # EXTRAPOLATED
    2199: 688,   # operator-confirmed 2026-07-12
    2299: 767,   # operator-confirmed
    2399: 847,   # operator-confirmed
    2499: 927,   # operator-confirmed
}
US_TEE_EXTRAPOLATED = {1999, 2099}
US_TEE_GROWTH_TARGET = 0.30          # Model A growth ceiling (policy, unchanged)

# self-assert the confirmed pairs — a bad edit here must fail loudly, closing
# the write gates, not silently mis-price the account
for _p, _r in ((2199, 688), (2299, 767), (2399, 847), (2499, 927)):
    if US_TEE_ROYALTY_CENTS.get(_p) != _r:
        raise RuntimeError(f"US tee royalty table corrupted at {_p} — refusing to run")


def parse_price_cents(price):
    """Export/DB price string -> integer cents via Decimal, or None."""
    from decimal import Decimal, InvalidOperation
    if price is None:
        return None
    try:
        cents = int((Decimal(str(price).strip()) * 100).quantize(Decimal("1")))
    except (InvalidOperation, ValueError):
        return None
    return cents if cents > 0 else None


def tee_econ_for_cents(cents):
    """Price-specific US standard-tee economics, or None if the price is
    outside the supported domain. break_even/target are fractions."""
    roy = US_TEE_ROYALTY_CENTS.get(cents)
    if roy is None:
        return None
    be = roy / cents
    return {"price_cents": cents, "royalty_cents": roy, "royalty": roy / 100.0,
            "break_even": round(be, 4),
            "target_acos": round(min(US_TEE_GROWTH_TARGET, be), 4),
            "extrapolated": cents in US_TEE_EXTRAPOLATED,
            "model_version": US_TEE_ROYALTY_V}


def transition_break_even(current_cents, legs):
    """max break-even across the current price and every supported leg of the
    active price_change rows. Returns (break_even, unknown): unknown=True when
    any leg (or the current price) is missing/unsupported -> the caller must
    treat the design as transition-unknown (skip destructive, no bid-ups)."""
    cands, unknown = [], False
    for c in [current_cents] + [x for leg in legs for x in leg[:2]]:
        if c is None:
            unknown = True
            continue
        e = tee_econ_for_cents(c)
        if e is None:
            unknown = True
        else:
            cands.append(e["break_even"])
    return (max(cands) if cands else None), unknown


# productType (as it appears in the Merch export) -> economics
PRODUCT_ECON = {
    # type:                         (royalty, break_even, model, target_acos)
    # standard tee row = the FLOOR economics ($19.99 point) — used for tee
    # cohorts (multi-ASIN scavenger groups) and as the conservative fallback
    # when a design's price is unknown. Per-design pricing overrides this via
    # get_design_econ(). Replaces the stale 4.89/24.5% ($19.99 pre-royalty-raise).
    "standard_tshirt":              (5.28, 0.264, "A", 0.264),
    "premium_tshirt":               (6.52, 0.272, "B", 0.272),
    "oversized_tshirt":             (4.89, 0.245, "B", 0.245),  # ~tee economics (not in ref; conservative)
    "performance_tshirt":           (5.30, 0.241, "B", 0.241),
    "vneck":                        (5.09, 0.231, "B", 0.231),
    "tank_top":                     (5.12, 0.233, "B", 0.233),
    "long_sleeve":                  (5.24, 0.210, "B", 0.210),
    "raglan":                       (5.10, 0.210, "B", 0.210),  # not actively sold; conservative
    "comfort_colors_heavyweight":   (5.10, 0.204, "B", 0.204),
    "standard_sweatshirt":          (8.10, 0.225, "B", 0.225),
    "standard_pullover_hoodie":     (7.90, 0.219, "B", 0.219),
    "zip_hoodie":                   (7.23, 0.195, "B", 0.195),
    "quarter_zip":                  (5.10, 0.204, "B", 0.204),
    "polo":                         (5.12, 0.213, "B", 0.213),
    "performance_hoodie":           (6.27, 0.149, "B", 0.149),
    "comfort_colors_sweatshirt":    (6.89, 0.153, "B", 0.153),
    "comfort_colors_crop_sweatshirt": (6.89, 0.153, "B", 0.153),
    "crop_top":                     (5.94, 0.238, "B", 0.238),
    "tote_bag":                     (5.36, 0.244, "B", 0.244),
    "throw_pillow":                 (5.32, 0.222, "B", 0.222),
    "printed_baseball_hat":         (2.80, 0.140, "B", 0.140),
    "printed_trucker_hat":          (2.80, 0.140, "B", 0.140),
    "sport_sun_visor":              (2.52, 0.140, "B", 0.140),
    "pop_socket":                   (2.10, 0.140, "B", 0.140),
    "phone_case_apple_iphone":      (2.89, 0.161, "B", 0.161),
    "phone_case_samsung_galaxy":    (2.89, 0.161, "B", 0.161),
    "tumbler":                      (3.78, 0.140, "B", 0.140),
    "water_bottle":                 (4.06, 0.140, "B", 0.140),
    "mug":                          (2.54, 0.150, "B", 0.150),
}

# Fallback for unknown/new types: conservative Model B, mid royalty.
DEFAULT_ECON = (5.00, 0.180, "B", 0.180)

TEE = "standard_tshirt"

# Campaign-name keyword -> product type (ordered: most specific FIRST).
# Used when an ad group's ASIN isn't in the product export but the campaign
# name says what it is (e.g. "Retro Name Vault Trucker Hats 1").
CAMPAIGN_TYPE_KEYWORDS = [
    ("trucker hat", "printed_trucker_hat"),
    ("baseball hat", "printed_baseball_hat"),
    ("sun visor", "sport_sun_visor"),
    ("zip hoodie", "zip_hoodie"),
    ("quarter zip", "quarter_zip"),
    ("pullover hoodie", "standard_pullover_hoodie"),
    ("hoodie", "standard_pullover_hoodie"),
    ("sweatshirt", "standard_sweatshirt"),
    ("long sleeve", "long_sleeve"),
    ("tank", "tank_top"),
    ("v-neck", "vneck"), ("vneck", "vneck"), ("v neck", "vneck"),
    ("crop top", "crop_top"),
    ("tote", "tote_bag"),
    ("pillow", "throw_pillow"),
    ("tumbler", "tumbler"),
    ("water bottle", "water_bottle"),
    ("popsocket", "pop_socket"), ("pop socket", "pop_socket"),
    ("phone case", "phone_case_apple_iphone"),
    ("polo", "polo"),
    ("mug", "mug"),
    ("tshirt", "standard_tshirt"), ("t-shirt", "standard_tshirt"),
    ("shirt", "standard_tshirt"), ("tee", "standard_tshirt"),
]


def infer_type_from_campaign(name):
    """Best-effort product type from a campaign name, else None."""
    n = (name or "").lower()
    for kw, t in CAMPAIGN_TYPE_KEYWORDS:
        if kw in n:
            return t
    return None


# --- proven-winner pause guardrail ---
# A design with >= PROVEN_SALES_MIN lifetime units gets PROTECT_MULTIPLIER more
# spend runway before the auto-pause rule will cut it (don't kill a proven winner
# on one weak window). Tunable.
PROVEN_SALES_MIN = 25
PROTECT_MULTIPLIER = 3.0


def is_proven(lifetime_sales):
    return (lifetime_sales or 0) >= PROVEN_SALES_MIN


def pause_threshold(product_type, lifetime_sales=0):
    """Ad-group pause threshold, raised for proven winners."""
    base = get_econ(product_type)["pause_threshold"]
    return round(base * PROTECT_MULTIPLIER, 2) if is_proven(lifetime_sales) else base


# cache of derived non-US economics: market -> {product_type: {royalty, price, break_even}}
_MARKET_ECON = {}


def _market_econ(market):
    if market not in _MARKET_ECON:
        import db
        conn = db.connect()
        _MARKET_ECON[market] = db.get_market_econ(conn, market)
        conn.close()
    return _MARKET_ECON[market]


def get_econ(product_type, market=None):
    """Economics for a product type in a market.
    US (default) = authoritative hardcoded table. Non-US = derived from the export
    (Model B, bid-to-break-even), read from market_econ via derive_econ.py."""
    import markets
    market = market or markets.current()

    if market == markets.DEFAULT:
        royalty, be, model, target = PRODUCT_ECON.get(product_type, DEFAULT_ECON)
        return {
            "product_type": product_type or "unknown",
            "known": product_type in PRODUCT_ECON,
            "royalty": royalty, "break_even": be, "model": model, "target_acos": target,
            "neg_threshold": round(royalty * 0.5, 2),
            # standard tee uses the flat $5 ad-group pause; others use royalty * 0.5
            "pause_threshold": 5.00 if product_type == TEE else round(royalty * 0.5, 2),
        }

    # non-US: everything is Model B (bid to its own break-even); no flat tee pause
    rec = _market_econ(market).get(product_type)
    if rec and rec.get("royalty") and rec.get("break_even"):
        royalty, be = rec["royalty"], rec["break_even"]
        known = True
    else:
        royalty, be, _, _ = DEFAULT_ECON
        known = False
    return {
        "product_type": product_type or "unknown",
        "known": known,
        "royalty": royalty, "break_even": be, "model": "B", "target_acos": be,
        "neg_threshold": round(royalty * 0.5, 2),
        "pause_threshold": round(royalty * 0.5, 2),
    }


def get_design_econ(product_type, market=None, price=None):
    """Per-DESIGN economics: US standard tees resolve royalty/break-even from
    the design's own list price (string or cents). Everything else falls back
    to get_econ. Returns get_econ's shape plus:
      known_price  - True when the price is in the supported domain
      src          - 'us_tee_table' | 'tee_floor' | 'type_table'
      extrapolated - True for the sub-$21.99 extrapolated points
    Unknown/unsupported price -> the FLOOR economics with known_price=False;
    callers making automatic monetary decisions must SKIP such designs (plan
    §15) — the floor value is for display only."""
    import markets
    market = market or markets.current()
    base = get_econ(product_type, market)
    if market != markets.DEFAULT or product_type != TEE:
        base.update({"known_price": False, "src": "type_table",
                     "extrapolated": False, "model_version": US_TEE_ROYALTY_V})
        return base
    cents = price if isinstance(price, int) else parse_price_cents(price)
    e = tee_econ_for_cents(cents) if cents else None
    if e is None:
        base.update({"known_price": False, "src": "tee_floor",
                     "extrapolated": False, "model_version": US_TEE_ROYALTY_V,
                     "price_cents": cents})
        return base
    base.update({"royalty": e["royalty"], "break_even": e["break_even"],
                 "target_acos": e["target_acos"], "known_price": True,
                 "src": "us_tee_table", "extrapolated": e["extrapolated"],
                 "price_cents": cents, "royalty_cents": e["royalty_cents"],
                 "model_version": US_TEE_ROYALTY_V,
                 "neg_threshold": round(e["royalty"] * 0.5, 2)})
    return base


# --- per-design break-even resolver (single source) ----------------------------

def design_be_for(conn):
    """Return be_for(ad_group_id) -> (break_even, skip_reason), or None when
    economics are unavailable on this connection (fail closed). skip_reason ∈
    {None,'unmapped','cohort','transition','unknown_price'}; anything non-None
    must be EXCLUDED from per-design monetary claims (PLAN.md §4/§6/§15).

    SINGLE SOURCE for the break-even gating — consumed by appctl (kill list /
    nudges / health) and the rules DSL (rules/econ_fields). Do not fork this."""
    import markets
    import db
    import sqlite3
    try:
        if not db.econ_tables_present(conn):
            return None
        dmap = db.get_design_map(conn)
        trans = db.active_price_changes(conn)
    except sqlite3.OperationalError:
        return None

    # KDP: break-even from the per-book config (fail closed when a book has no
    # data). No tee tables, transitions, or cohorts apply.
    if markets.is_kdp():
        import kdp_econ

        def be_for_kdp(agid):
            d = dmap.get(str(agid))
            asin = d.get("asin") if d else None
            if not asin:
                return None, "unmapped"
            e = kdp_econ.book_econ(asin)
            if not e:
                return None, "unknown_price"   # book data not entered yet
            return e["break_even"], None
        return be_for_kdp

    def be_for(agid):
        d = dmap.get(str(agid))
        if not d or not d.get("product_type"):
            return None, "unmapped"
        pt = d["product_type"]
        if not (markets.is_default() and pt == TEE):
            return get_econ(pt).get("break_even"), None
        if not d.get("asin"):
            return None, "cohort"
        if trans.get(d["asin"]):
            return None, "transition"
        e = get_design_econ(pt, price=d.get("list_price"))
        if not e.get("known_price"):
            return None, "unknown_price"
        return e["break_even"], None
    return be_for


# --- economics freshness gate (PLAN.md §8) — US only ---------------------------
MAX_EXPORT_AGE_DAYS = 21


def _newest_export():
    import glob as _glob
    import os as _os
    here = _os.path.dirname(_os.path.abspath(__file__))
    pod = _os.path.dirname(here)
    matches = sorted(_glob.glob(_os.path.join(pod, "export_products_*.csv")))
    return matches[-1] if matches else None


def export_signature(path):
    import os as _os
    return f"{_os.path.basename(path)}|{int(_os.path.getmtime(path))}" if path else None


def _export_fresh(path):
    """Both the parsed filename timestamp AND the file mtime must be within
    MAX_EXPORT_AGE_DAYS (mtime alone is spoofable by copying an old file)."""
    import datetime as _dt
    import os as _os
    import re as _re
    if not path or not _os.path.exists(path):
        return False, "no adopted export"
    m = _re.search(r"export_products_(\d{4}-\d{2}-\d{2})", _os.path.basename(path))
    if not m:
        return False, "export filename carries no timestamp"
    try:
        named = _dt.datetime.strptime(m.group(1), "%Y-%m-%d")
    except ValueError:
        return False, "unparseable export filename timestamp"
    now = _dt.datetime.now()
    limit = _dt.timedelta(days=MAX_EXPORT_AGE_DAYS)
    if now - named > limit:
        return False, f"export named {m.group(1)} is older than {MAX_EXPORT_AGE_DAYS}d"
    if now - _dt.datetime.fromtimestamp(_os.path.getmtime(path)) > limit:
        return False, f"export mtime older than {MAX_EXPORT_AGE_DAYS}d"
    return True, None


def econ_gate(market=None, conn=None):
    """{'ok': bool, 'reasons': [...]} — must pass before ANY economics-driven
    write for the US market (proposal builders + appctl write commands + the
    nightly). Non-US markets pass through (their derive_econ contract is
    unchanged). Fail-closed on missing tables/stamps."""
    import markets
    market = market or markets.current()
    if market != markets.DEFAULT:
        return {"ok": True, "reasons": [], "scope": "non-us (existing contract)"}
    reasons = []
    path = _newest_export()
    fresh, why = _export_fresh(path)
    if not fresh:
        reasons.append(why)
    close_conn = False
    if conn is None:
        import db
        try:
            conn = db.connect(ro=True)
            close_conn = True
        except Exception as exc:                     # no DB at all
            return {"ok": False, "reasons": reasons + [f"db unavailable: {exc}"]}
    import db
    try:
        if not db.econ_tables_present(conn):
            reasons.append("economics tables absent (run a pull/map to migrate)")
        else:
            if db.meta_get(conn, "econ_stale"):
                reasons.append("STALE marker set (last adoption failed to re-map)")
            map_at = db.meta_get(conn, "map_success_at")
            adopt_at = db.meta_get(conn, "export_adopted_at")
            if not map_at:
                reasons.append("no successful product mapping recorded")
            elif adopt_at and map_at < adopt_at:
                reasons.append("mapping is older than the adopted export")
            sig = db.meta_get(conn, "export_signature")
            if path and sig and sig != export_signature(path):
                reasons.append("newest export was never mapped (signature mismatch)")
    finally:
        if close_conn:
            conn.close()
    return {"ok": not reasons, "reasons": reasons}


if __name__ == "__main__":
    print(f"{'type':32} {'roy':>6} {'BE':>6} {'mdl':>3} {'tgt':>6} {'neg':>5} {'pause':>6}")
    for t in PRODUCT_ECON:
        e = get_econ(t)
        print(f"{t:32} {e['royalty']:6.2f} {e['break_even']*100:5.1f}% {e['model']:>3} "
              f"{e['target_acos']*100:5.1f}% {e['neg_threshold']:5.2f} {e['pause_threshold']:6.2f}")
