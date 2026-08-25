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

US_TEE_ROYALTY_V = "2026-08-21"
US_TEE_ROYALTY_CENTS = {
    1399: 48,
    1499: 128,
    1599: 208,
    1699: 288,
    1799: 368,
    1899: 448,
    1999: 528,
    2099: 608,
    2199: 688,
    2299: 767,
    2399: 847,
    2499: 927,
}
US_TEE_EXTRAPOLATED = set()   # every rung confirmed off the dashboard 2026-08-21
US_TEE_GROWTH_TARGET = 0.30          # Model A growth ceiling (policy, unchanged)

# --- rank-push pricing (operator decision, 2026-08-16) ------------------------
# Some designs are priced BELOW $19.99 on purpose. The point is sales velocity:
# a better BSR, and a shot at page one. A $14.99 tee earns $1.28, so its own
# break-even ACOS is 8.5%, and every automatic rule would pause exactly the
# campaigns the price cut was meant to feed.
#
# So a tee priced under this floor is ACTED ON with the $19.99 economics
# (break-even 26.4%) instead of its own. Reported royalty and profit stay TRUE —
# a $14.99 sale still books $1.28. Only the decision thresholds are lifted.
#
# The design carries `growth_priced: True` so any screen can say which number it
# is looking at, and `true_break_even` is always the honest arithmetic.
US_TEE_GROWTH_FLOOR_CENTS = 1999

# self-assert the confirmed pairs — a bad edit here must fail loudly, closing
# the write gates, not silently mis-price the account
for _p, _r in ((1399, 48), (1499, 128), (1599, 208), (1699, 288), (1799, 368),
               (1899, 448), (1999, 528), (2099, 608), (2199, 688), (2299, 767),
               (2399, 847), (2499, 927)):
    if US_TEE_ROYALTY_CENTS.get(_p) != _r:
        raise RuntimeError(f"US tee royalty table corrupted at {_p} — refusing to run")
if US_TEE_GROWTH_FLOOR_CENTS not in US_TEE_ROYALTY_CENTS:
    raise RuntimeError("US tee growth floor has no royalty — refusing to run")


# --- operator overrides (2026-08-20) -----------------------------------------
# The two tables above are the shipped defaults. The operator edits royalties in
# the app, which writes royalty_overrides.json; royalty_config merges it on top
# here. Read through these two accessors, never the raw dicts, or an edit is
# silently ignored. The built-ins stay the floor: an override replaces a value
# or adds a new one, and can never remove a shipped price point.

def tee_royalty_table():
    """US tee list price (cents) -> royalty (cents), operator overrides applied."""
    import royalty_config
    table = dict(US_TEE_ROYALTY_CENTS)
    table.update(royalty_config.load()["tee_prices"])
    return table


def tee_extrapolated():
    """Price points whose royalty we GUESSED. An operator-confirmed override is
    not a guess, so confirming one clears its flag."""
    import royalty_config
    return US_TEE_EXTRAPOLATED - set(royalty_config.load()["tee_prices"])


def product_econ_table():
    """product type -> (royalty, break_even, model, target_acos), overrides applied.
    An override supplies royalty + price; break-even is computed from them, and
    the model stays whatever the built-in row used (or B for a new type)."""
    import royalty_config
    table = dict(PRODUCT_ECON)
    for name, rec in royalty_config.load()["product_types"].items():
        model = PRODUCT_ECON.get(name, DEFAULT_ECON)[2] if name in PRODUCT_ECON else "B"
        be = rec["break_even"]
        table[name] = (rec["royalty"], be, model, be)
    return table


# Catalogue statuses that mean a listing can still be BOUGHT.
#
# A MerchFlow "all products" export carries every listing the account has ever
# had, in every state, so `status` is the only thing separating a live product
# from a dead one. `published` is the obvious case. The other three are not
# obvious and were confirmed by the operator on 2026-08-22, then checked against
# the export's own 30-day sales:
#
#     timed_out    569 listings,  20 units sold in 30 days
#     locked       114 listings, 348 units sold in 30 days
#     propagated   147 listings,   1 unit
#
# A design that timed out has left the creator's active slots; the listing stays
# up and keeps selling. `locked` sells HARDER per listing than anything else in
# the export. So refusing to price them does not protect anything — it exempts a
# selling design from every economics rule, which is the one outcome worse than
# pricing it wrong. 316k `deleted_*` listings sold 6 units between them, which is
# the attribution tail, and `publishing` / `review` (815 listings) have never
# sold a unit because they are not live yet.
#
# This is the bar for PRICING a design that is already being advertised. It is
# deliberately NOT the bar for choosing new designs to advertise: lottery_build,
# scavenger_build and import-preview still require `published`, because starting
# to advertise a locked or timed-out design is a different decision.
PURCHASABLE_STATUSES = frozenset({"published", "timed_out", "locked", "propagated"})


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


# How far a US tee price may sit off the ladder and still be priced.
#
# Every rung of US_TEE_ROYALTY_CENTS is a .99 price, one dollar apart. A price
# that is not a rung used to resolve to NO economics at all, which exempted the
# design from every bid, pause and negative rule — the outcome that is worse
# than pricing it slightly wrong.
#
# In practice off-ladder prices are formatting artifacts, not pricing decisions.
# Measured across the US account on 2026-08-22: 21,267 advertised tees sat
# exactly on a rung, ONE was off, by a single cent ($20.00), and none were
# outside the ladder's range at all. So this is deliberately tiny. Rungs are 100
# cents apart, so ±5 can never reach two of them, and a genuinely unusual price
# ($20.49, say) still resolves to nothing and is still skipped — fail-closed,
# exactly as before.
US_TEE_PRICE_SNAP_CENTS = 5


def snap_tee_price_cents(cents, table=None):
    """The ladder rung this price is priced AS, or None if it is too far off.

    Returns `cents` unchanged when it is already a rung. Never guesses across
    more than US_TEE_PRICE_SNAP_CENTS.
    """
    table = table if table is not None else tee_royalty_table()
    if cents in table:
        return cents
    if not table:
        return None
    near = min(table, key=lambda rung: (abs(rung - cents), rung))
    return near if abs(near - cents) <= US_TEE_PRICE_SNAP_CENTS else None


def tee_econ_for_cents(cents):
    """Price-specific US standard-tee economics, or None if the price is
    outside the supported domain.

    `royalty` is what the design actually earns. `break_even`, `target_acos` and
    `action_royalty` are what the rules ACT on, which is the same thing at
    $19.99 and above and the $19.99 economics below it (see
    US_TEE_GROWTH_FLOOR_CENTS). `true_break_even` is always royalty / price.
    All ACOS values are fractions."""
    table = tee_royalty_table()
    # A price a few cents off the ladder is priced AS the nearest rung. The
    # REAL price is still what `price_cents` reports and what `true_break_even`
    # divides by — snapping decides which royalty row to read, never what the
    # design is said to cost.
    priced_at = snap_tee_price_cents(cents, table)
    if priced_at is None:
        return None
    roy = table[priced_at]
    action_cents = max(priced_at, US_TEE_GROWTH_FLOOR_CENTS)
    action_roy = table[action_cents]
    be = action_roy / action_cents
    return {"price_cents": cents, "priced_as_cents": priced_at,
            "price_snapped": priced_at != cents,
            "royalty_cents": roy, "royalty": roy / 100.0,
            "break_even": round(be, 4),
            "true_break_even": round(roy / cents, 4),
            # Follows the SNAPPED rung, not the raw price. A $19.95 tee is a
            # $19.99 tee with a rounding artifact, not a deliberate rank-push
            # below the growth floor, and calling it growth-priced would hand it
            # a different action royalty for the sake of four cents.
            "growth_priced": priced_at < US_TEE_GROWTH_FLOOR_CENTS,
            "action_royalty": action_roy / 100.0,
            "target_acos": round(min(US_TEE_GROWTH_TARGET, be), 4),
            "extrapolated": cents in tee_extrapolated(),
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


# productType (as it appears in the Merch export) -> economics.
# Oversized T-Shirt, Samsung Galaxy Case and Quarter Zip were removed 2026-08-21:
# the operator does not sell them. Quarter Zip was replaced by the operator's own
# 'Performance quarter-zip' entry, which is the name the dashboard uses. A label that resolves to nothing is reported as skipped and
# never guessed into a cohort, which is the safe outcome for a product we cannot
# price.
PRODUCT_ECON = {
    'Baseball Jersey': (7.17, 0.239, "B", 0.239),
    'Basketball Jersey': (4.53, 0.181, "B", 0.181),
    'Performance quarter-zip': (5.51, 0.221, "B", 0.221),
    'Soccer Jersey': (7.55, 0.252, "B", 0.252),
    'comfort_colors_crop_sweatshirt': (7.44, 0.165, "B", 0.165),
    'comfort_colors_heavyweight': (7.02, 0.26, "B", 0.26),
    'comfort_colors_sweatshirt': (7.44, 0.165, "B", 0.165),
    'crop_top': (4.91, 0.214, "B", 0.214),
    'long_sleeve': (4.89, 0.204, "B", 0.204),
    'mug': (2.75, 0.162, "B", 0.162),
    'performance_hoodie': (6.77, 0.161, "B", 0.161),
    'performance_tshirt': (5.72, 0.26, "B", 0.26),
    'phone_case_apple_iphone': (4.62, 0.231, "B", 0.231),
    'polo': (4.01, 0.182, "B", 0.182),
    'pop_socket': (1.96, 0.151, "B", 0.151),
    'premium_tshirt': (7.79, 0.312, "B", 0.312),
    'printed_baseball_hat': (3.02, 0.151, "B", 0.151),
    'printed_trucker_hat': (3.02, 0.151, "B", 0.151),
    'raglan': (5.10, 0.21, "B", 0.21),
    'sport_sun_visor': (2.72, 0.151, "B", 0.151),
    'standard_pullover_hoodie': (8.53, 0.237, "B", 0.237),
    'standard_sweatshirt': (7.14, 0.21, "B", 0.21),
    'standard_tshirt': (5.28, 0.264, "A", 0.264),
    'tank_top': (5.08, 0.231, "B", 0.231),
    'throw_pillow': (5.74, 0.239, "B", 0.239),
    'tote_bag': (5.78, 0.263, "B", 0.263),
    'tumbler': (4.08, 0.151, "B", 0.151),
    'vneck': (5.50, 0.25, "B", 0.25),
    'water_bottle': (4.38, 0.151, "B", 0.151),
    'zip_hoodie': (7.04, 0.196, "B", 0.196),
}


# The list price each royalty above was worked out from. Kept alongside rather
# than divided back out of the break-even: royalty / break_even lands a few
# cents off the real price, and a screen that shows "45,03" for a $44.99
# sweatshirt is quietly lying about a number the operator typed.
PRODUCT_PRICE = {
    'Baseball Jersey': 29.99,
    'Basketball Jersey': 24.99,
    'Performance quarter-zip': 24.99,
    'Soccer Jersey': 29.99,
    'comfort_colors_crop_sweatshirt': 44.99,
    'comfort_colors_heavyweight': 26.99,
    'comfort_colors_sweatshirt': 44.99,
    'crop_top': 22.99,
    'long_sleeve': 23.99,
    'mug': 16.99,
    'performance_hoodie': 41.99,
    'performance_tshirt': 21.99,
    'phone_case_apple_iphone': 19.99,
    'polo': 21.99,
    'pop_socket': 12.99,
    'premium_tshirt': 24.99,
    'printed_baseball_hat': 19.99,
    'printed_trucker_hat': 19.99,
    'raglan': 24.29,
    'sport_sun_visor': 17.99,
    'standard_pullover_hoodie': 35.99,
    'standard_sweatshirt': 33.99,
    'standard_tshirt': 19.99,
    'tank_top': 21.99,
    'throw_pillow': 23.99,
    'tote_bag': 21.99,
    'tumbler': 26.99,
    'vneck': 21.99,
    'water_bottle': 28.99,
    'zip_hoodie': 35.99,
}


# Non-US economics, confirmed off the Merch dashboard 2026-08-21. Amazon fixes
# a maximum price per product per market, so these are caps rather than
# estimates and they beat the median derive_econ.py works out of the export.
# (royalty, break_even, price) — everything outside the US is Model B, bidding
# straight to its own break-even. A type NOT listed here still falls through to
# the derived market_econ row, so a market keeps working before it is confirmed.
MARKET_PRODUCT_ECON = {
    "UK": {
        'comfort_colors_heavyweight': (2.55, 0.142, 17.99),
        'long_sleeve': (3.46, 0.157, 21.99),
        'phone_case_apple_iphone': (1.75, 0.125, 13.99),
        'pop_socket': (1.28, 0.117, 10.99),
        'premium_tshirt': (2.55, 0.142, 17.99),
        'standard_pullover_hoodie': (2.15, 0.074, 28.99),
        'standard_sweatshirt': (5.04, 0.158, 31.99),
        'standard_tshirt': (3.57, 0.204, 17.49),
        'tank_top': (2.91, 0.162, 17.99),
        'tumbler': (2.21, 0.116, 18.99),
        'vneck': (3.30, 0.183, 17.99),
        'water_bottle': (2.21, 0.116, 18.99),
        'zip_hoodie': (4.23, 0.141, 29.99),
    },
    "DE": {
        'comfort_colors_heavyweight': (2.86, 0.143, 19.99),
        'long_sleeve': (3.13, 0.136, 22.99),
        'phone_case_apple_iphone': (1.89, 0.126, 14.99),
        'pop_socket': (1.29, 0.117, 10.99),
        'premium_tshirt': (2.86, 0.143, 19.99),
        'standard_pullover_hoodie': (2.62, 0.079, 32.99),
        'standard_sweatshirt': (5.23, 0.149, 34.99),
        'standard_tshirt': (2.70, 0.15, 17.99),
        'tank_top': (3.48, 0.174, 19.99),
        'tumbler': (2.47, 0.118, 20.99),
        'vneck': (3.39, 0.178, 18.99),
        'water_bottle': (2.47, 0.118, 20.99),
        'zip_hoodie': (4.16, 0.126, 32.99),
    },
    "FR": {
        'comfort_colors_heavyweight': (2.83, 0.142, 19.99),
        'long_sleeve': (3.15, 0.15, 20.99),
        'phone_case_apple_iphone': (2.00, 0.125, 15.99),
        'pop_socket': (1.52, 0.117, 12.99),
        'premium_tshirt': (2.83, 0.142, 19.99),
        'standard_pullover_hoodie': (2.29, 0.079, 28.99),
        'standard_sweatshirt': (4.53, 0.151, 29.99),
        'standard_tshirt': (3.76, 0.193, 19.49),
        'tank_top': (3.47, 0.183, 18.99),
        'tumbler': (2.45, 0.117, 20.99),
        'vneck': (3.54, 0.177, 19.99),
        'water_bottle': (2.45, 0.117, 20.99),
        'zip_hoodie': (4.89, 0.148, 32.99),
    },
    "IT": {
        'comfort_colors_heavyweight': (2.79, 0.14, 19.99),
        'long_sleeve': (3.10, 0.148, 20.99),
        'phone_case_apple_iphone': (1.97, 0.123, 15.99),
        'pop_socket': (1.49, 0.115, 12.99),
        'premium_tshirt': (2.79, 0.14, 19.99),
        'standard_pullover_hoodie': (1.98, 0.068, 28.99),
        'standard_sweatshirt': (4.20, 0.14, 29.99),
        'standard_tshirt': (3.70, 0.19, 19.49),
        'tank_top': (3.42, 0.18, 18.99),
        'tumbler': (2.41, 0.115, 20.99),
        'vneck': (3.48, 0.174, 19.99),
        'water_bottle': (2.41, 0.115, 20.99),
        'zip_hoodie': (4.55, 0.138, 32.99),
    },
    "ES": {
        'comfort_colors_heavyweight': (2.81, 0.141, 19.99),
        'long_sleeve': (3.10, 0.148, 20.99),
        'phone_case_apple_iphone': (1.98, 0.124, 15.99),
        'pop_socket': (1.50, 0.116, 12.99),
        'premium_tshirt': (2.81, 0.141, 19.99),
        'standard_pullover_hoodie': (4.87, 0.148, 32.99),
        'standard_sweatshirt': (4.23, 0.141, 29.99),
        'standard_tshirt': (3.98, 0.204, 19.49),
        'tank_top': (3.39, 0.178, 18.99),
        'tumbler': (2.43, 0.116, 20.99),
        'vneck': (3.11, 0.164, 18.99),
        'water_bottle': (2.43, 0.116, 20.99),
        'zip_hoodie': (4.82, 0.146, 32.99),
    },
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


# Export product-type LABEL -> engine product type.
# The MerchFlow export already carries the engine's own type strings. The Snap
# for MOD export carries the human labels the Merch dashboard shows, so those
# get translated here (engine/export_reader.py calls this).
#
# The table is EXPLICIT on purpose. An unknown label returns None, and the
# intake reports it as a skipped type for the operator to read. Fuzzy matching
# is the wrong tool on this path: "standard_tshirt" routes a design into the
# lottery campaigns, so a near-miss on "Performance T-Shirt" would buy ads on
# the wrong economics. Add a label here when a new one shows up.
EXPORT_TYPE_LABELS = {
    # tees
    "t shirt": "standard_tshirt",
    "tshirt": "standard_tshirt",
    "tee": "standard_tshirt",
    "standard t shirt": "standard_tshirt",
    "premium t shirt": "premium_tshirt",
    "performance t shirt": "performance_tshirt",
    "v neck t shirt": "vneck",
    "v neck": "vneck",
    "tank top": "tank_top",
    "long sleeve t shirt": "long_sleeve",
    "long sleeve": "long_sleeve",
    "raglan": "raglan",
    "raglan 3 4 sleeve": "raglan",
    "baseball tee": "raglan",
    "comfort colors t shirt": "comfort_colors_heavyweight",
    "heavyweight t shirt": "comfort_colors_heavyweight",
    "crop top": "crop_top",
    # fleece
    "sweatshirt": "standard_sweatshirt",
    "standard sweatshirt": "standard_sweatshirt",
    "comfort colors sweatshirt": "comfort_colors_sweatshirt",
    "comfort colors crop sweatshirt": "comfort_colors_crop_sweatshirt",
    "hoodie": "standard_pullover_hoodie",
    "pullover hoodie": "standard_pullover_hoodie",
    "zip hoodie": "zip_hoodie",
    "full zip hoodie": "zip_hoodie",
    "performance hoodie": "performance_hoodie",
    "polo": "polo",
    "polo shirt": "polo",
    # hardgoods
    "tote bag": "tote_bag",
    "tote": "tote_bag",
    "throw pillow": "throw_pillow",
    "popsocket": "pop_socket",
    "popsockets": "pop_socket",
    "pop socket": "pop_socket",
    "popsockets grip": "pop_socket",
    "iphone case": "phone_case_apple_iphone",
    "apple iphone case": "phone_case_apple_iphone",
    "phone case": "phone_case_apple_iphone",
    "tumbler": "tumbler",
    "water bottle": "water_bottle",
    "mug": "mug",
    "trucker hat": "printed_trucker_hat",
    "printed trucker hat": "printed_trucker_hat",
    "baseball hat": "printed_baseball_hat",
    "baseball cap": "printed_baseball_hat",
    "printed baseball hat": "printed_baseball_hat",
    "sun visor": "sport_sun_visor",
    "sport sun visor": "sport_sun_visor",
}


def type_from_export_label(label):
    """Engine product type for an export label, or None when we don't know it.

    Accepts the engine's own strings ("standard_tshirt") and the dashboard
    labels Snap for MOD exports ("PopSocket", "iPhone Case")."""
    import re as _re
    raw = (label or "").strip()
    if not raw:
        return None
    known = product_econ_table()      # includes types the operator added
    if raw in known:
        return raw
    key = _re.sub(r"[^a-z0-9]+", " ", raw.lower()).strip()
    if key in EXPORT_TYPE_LABELS:
        return EXPORT_TYPE_LABELS[key]
    # "Standard Tshirt" / "standard-tshirt" spellings of a real engine type.
    # `t` is LOWERCASED here. The shipped types are all snake_case, so it never
    # mattered — but an operator can add a type now, and they name it the way the
    # dashboard does ("Performance quarter-zip"). Without the lowercase, every
    # capital letter fell outside [a-z0-9] and only the exact spelling matched,
    # so a royalty they had entered silently did nothing.
    for t in known:
        if key == _re.sub(r"[^a-z0-9]+", " ", t.lower()).strip():
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
        econ_table = product_econ_table()
        royalty, be, model, target = econ_table.get(product_type, DEFAULT_ECON)
        return {
            "product_type": product_type or "unknown",
            "known": product_type in econ_table,
            "royalty": royalty, "break_even": be, "model": model, "target_acos": target,
            "neg_threshold": round(royalty * 0.5, 2),
            # standard tee uses the flat $5 ad-group pause; others use royalty * 0.5
            "pause_threshold": 5.00 if product_type == TEE else round(royalty * 0.5, 2),
        }

    # non-US: everything is Model B (bid to its own break-even); no flat tee pause.
    # An operator number wins over a derived one. Amazon fixes a maximum price
    # per product per market, so what the Merch dashboard shows is definitive,
    # while a derived median only reflects whatever mix of listings exists.
    import royalty_config
    over = royalty_config.load(market)["product_types"].get(product_type)
    shipped = MARKET_PRODUCT_ECON.get(market, {}).get(product_type)
    if over:
        royalty, be, known = over["royalty"], over["break_even"], True
    elif shipped:
        royalty, be, known = shipped[0], shipped[1], True
    else:
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


def list_price_for(product_type, market=None):
    """The list price a royalty was worked out from, or None.

    An operator entry wins, then the shipped table for that market. Returns a
    real price rather than royalty / break_even, which lands a few cents off.
    """
    import markets
    import royalty_config
    market = market or markets.current()
    over = royalty_config.load(market)["product_types"].get(product_type)
    if over:
        return over["price"]
    if market == markets.DEFAULT:
        return PRODUCT_PRICE.get(product_type)
    shipped = MARKET_PRODUCT_ECON.get(market, {}).get(product_type)
    if shipped:
        return shipped[2]
    rec = _market_econ(market).get(product_type)
    return rec.get("price") if rec else None


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
                 "true_break_even": e["true_break_even"],
                 "growth_priced": e["growth_priced"],
                 "target_acos": e["target_acos"], "known_price": True,
                 "src": "us_tee_table", "extrapolated": e["extrapolated"],
                 "price_cents": cents, "royalty_cents": e["royalty_cents"],
                 # Carried through so nothing hides that the royalty was read
                 # off a neighbouring rung: `price_cents` is the real price,
                 # `priced_as_cents` is the row the economics came from.
                 "priced_as_cents": e["priced_as_cents"],
                 "price_snapped": e["price_snapped"],
                 "model_version": US_TEE_ROYALTY_V,
                 # Spend thresholds are a decision, so they follow the ACTION
                 # royalty. Half of a rank-price royalty would negate keywords
                 # after 64 cents and strangle the very designs being pushed.
                 "neg_threshold": round(e["action_royalty"] * 0.5, 2)})
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
        # A COHORT ad group advertises several designs under one roof, so no
        # per-design claim about it can be true. That test used to sit BELOW the
        # early return, which meant it only ever ran for US tees: a cohort in any
        # other market, or of any other product type, was handed a real
        # break-even and judged as though it were one design.
        if not d.get("asin"):
            return None, "cohort"
        if not (markets.is_default() and pt == TEE):
            e = get_econ(pt)
            # `known` is False when nothing — an operator override, the shipped
            # table, or derive_econ's export median — could price this type, and
            # get_econ then answers with DEFAULT_ECON. That is a display
            # fallback, not economics: acting on it means bidding and pausing
            # against an 18% break-even that belongs to no real product.
            if not e.get("known"):
                return None, "unknown_price"
            return e.get("break_even"), None
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
    """The freshest product-grid export in the POD folder, which sits ABOVE the
    repo. The catalog can be several files now (Snap for MOD exports at most
    100k rows at a time), so this is the file the freshness check reads its date
    from, not the whole catalog. Use export_reader.catalog_files() for that.

    This walked up from its own __file__ and was missed when the modules moved
    into engine/, because its imports are aliased (_os) and the sweep matched the
    plain os. prefix. It then globbed the repo instead of POD, found nothing, and
    closed the economics gate — which silently blocks every economics-driven
    write. paths.POD_ROOT is the single definition; do not walk up from here.
    """
    import export_reader as _er
    return _er.newest_catalog_file()


def export_signature(path=None):
    """Signature of the WHOLE catalog, so adding one Snap chunk changes it and
    the gate notices the mapping is out of date. `path` is accepted for older
    callers and ignored — one chunk cannot stand for the catalog."""
    import export_reader as _er
    return _er.catalog_signature()


def _export_fresh(path):
    """Both the date in the filename AND the file mtime must be within
    MAX_EXPORT_AGE_DAYS (mtime alone is spoofable by copying an old file)."""
    import datetime as _dt
    import os as _os
    import export_reader as _er
    if not path or not _os.path.exists(path):
        return False, "no product export in the POD folder"
    stamp = _er.file_date(path)
    if not stamp:
        return False, "export filename carries no date"
    try:
        named = _dt.datetime.strptime(stamp, "%Y-%m-%d")
    except ValueError:
        return False, "unparseable export filename date"
    now = _dt.datetime.now()
    limit = _dt.timedelta(days=MAX_EXPORT_AGE_DAYS)
    if now - named > limit:
        return False, f"newest export named {stamp} is older than {MAX_EXPORT_AGE_DAYS}d"
    if now - _dt.datetime.fromtimestamp(_os.path.getmtime(path)) > limit:
        return False, f"newest export mtime older than {MAX_EXPORT_AGE_DAYS}d"
    return True, None


# The derived economics are only as good as the night they were derived. Same
# window as the export they come from — derive_econ reads that export, so its
# output can never be fresher than MAX_EXPORT_AGE_DAYS anyway.
MAX_DERIVED_ECON_AGE_DAYS = 21


def _derived_econ_reasons(market, conn=None):
    """Why this market's derived economics cannot be trusted, if they cannot.

    Empty when there are none to check: a market that has never run derive_econ
    prices everything from the shipped tables, and `be_for` already fails closed
    on a product type nothing can price. This is about economics that EXIST and
    have gone stale, which is the case nothing could see.
    """
    import datetime as _dt
    import sqlite3 as _sqlite3
    import db
    close = False
    if conn is None:
        try:
            conn = db.connect(ro=True)
            close = True
        except Exception as e:
            # "There is nothing to check" and "I could not look" both returned
            # an empty list, which reads as "no problem" — and this list is what
            # closes the economics gate. A database that is locked, corrupt or
            # unreadable landed here and waved every economics-driven write
            # through on whatever the fallback tables happened to say.
            return [f"could not open {market}'s database to check its derived "
                    f"economics ({type(e).__name__}: {e})"]
    try:
        try:
            row = conn.execute(
                "SELECT COUNT(*), MAX(updated_at) FROM market_econ WHERE market=?",
                (market,)).fetchone()
        except _sqlite3.OperationalError as e:
            # A market_econ table that is not there is a real and benign state:
            # this database predates the migration and everything prices from
            # the shipped tables. Any OTHER operational failure is not benign
            # and must not read the same way.
            if "no such table" in str(e).lower():
                return []
            return [f"could not read {market}'s derived economics ({e})"]
        except Exception as e:
            return [f"could not read {market}'s derived economics "
                    f"({type(e).__name__}: {e})"]
    finally:
        if close:
            conn.close()
    count, newest = (row or (0, None))
    if not count or not newest:
        return []
    try:
        age = (_dt.date.today() - _dt.date.fromisoformat(str(newest)[:10])).days
    except ValueError:
        return [f"derived economics for {market} carry an unreadable date ({newest})"]
    if age > MAX_DERIVED_ECON_AGE_DAYS:
        return [f"derived economics for {market} are {age} days old "
                f"(limit {MAX_DERIVED_ECON_AGE_DAYS}) — derive_econ has not run, "
                f"so every non-shipped product type is priced off an old export"]
    return []


def econ_gate(market=None, conn=None):
    """{'ok': bool, 'reasons': [...]} — must pass before ANY economics-driven
    write for the US market (proposal builders + appctl write commands + the
    nightly). Non-US markets pass through (their derive_econ contract is
    unchanged). Fail-closed on missing tables/stamps."""
    import markets
    market = market or markets.current()
    if market != markets.DEFAULT:
        # Non-US keeps its pass-through contract, EXCEPT for two things it
        # cannot honestly wave through.
        #
        # One: a royalty the operator saved that can no longer be read. Pricing
        # off a stale derived median while they believe their number is live is
        # exactly the silent mis-pricing this gate exists to stop.
        #
        # Two: the DERIVED economics themselves. Outside US and outside the
        # shipped tables, every break-even comes from derive_econ's export
        # median in `market_econ`, and derive_econ runs nightly. Nothing checked
        # whether it had run. A market whose derivation quietly stopped went on
        # bidding and pausing against the break-evens it had when it stopped —
        # through a gate that reported ok, because the gate was only ever
        # looking at the US half of the world.
        import royalty_config
        bad = [f"royalty override unusable — {b}" for b in royalty_config.errors(market)]
        bad += _derived_econ_reasons(market, conn)
        return {"ok": not bad, "reasons": bad, "scope": "non-us (existing contract)"}
    reasons = []
    import royalty_config
    # A royalty we cannot read is worse than no royalty: it would silently
    # price off the shipped defaults while the operator believes their edit
    # is live. Fail closed, exactly like a stale export.
    for bad in royalty_config.errors():
        reasons.append(f"royalty override unusable — {bad}")
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
            if path and sig and sig != export_signature():
                reasons.append("catalog changed since the last mapping "
                               "(signature mismatch — a chunk was added or replaced)")
    finally:
        if close_conn:
            conn.close()
    return {"ok": not reasons, "reasons": reasons, "catalog": catalog_status()}


def catalog_status(market=None):
    """What the product catalog is made of, and how old its prices are.

    The catalog used to be one file with one date. Snap for MOD exports at most
    100k rows, so it is now a set of chunks and a design can be priced from a
    file exported weeks before its neighbour. map_products writes the coverage
    numbers; this reads them back so the gate and System Health can show them."""
    import json as _json
    import os as _os
    import export_reader as _er
    import markets as _markets
    market = market or _markets.current()
    files = _er.catalog_files()
    status = {"files": [_os.path.basename(p) for p in files],
              "newest": _er.file_date(files[0]) if files else None,
              "oldest": _er.file_date(files[-1]) if files else None}
    sfx = "" if market == _markets.DEFAULT else f"_{market}"
    try:
        import paths as _paths
        with open(_paths.repo("outputs", f"catalog_coverage{sfx}.json"), encoding="utf-8") as fh:
            coverage = _json.load(fh)
        # The coverage file is a snapshot of what the LAST MAP was built from.
        # It carries `files` and `newest` of its own, and letting those overwrite
        # the live scan made the reply describe a catalog that is no longer on
        # disk — a chunk removed after the map still showed as present. Keep both,
        # under names that say which is which: the drift between them is exactly
        # what closes the gate.
        for key in ("files", "newest"):
            if key in coverage:
                coverage[f"mapped_{key}"] = coverage.pop(key)
        status.update(coverage)
    except (OSError, ValueError):
        pass
    return status


if __name__ == "__main__":
    print(f"{'type':32} {'roy':>6} {'BE':>6} {'mdl':>3} {'tgt':>6} {'neg':>5} {'pause':>6}")
    for t in product_econ_table():
        e = get_econ(t)
        print(f"{t:32} {e['royalty']:6.2f} {e['break_even']*100:5.1f}% {e['model']:>3} "
              f"{e['target_acos']*100:5.1f}% {e['neg_threshold']:5.2f} {e['pause_threshold']:6.2f}")
