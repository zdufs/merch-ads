#!/usr/bin/env python3
"""
Operator-editable royalties — the overlay on top of the built-in US tables.

US economics live in products.py: `US_TEE_ROYALTY_CENTS` (list price -> royalty
for standard tees) and `PRODUCT_ECON` (royalty + break-even per product type).
Those are shipped, self-asserting defaults. Changing one used to mean editing
Python, which is why the app could not stand on its own.

This module is the layer that makes them editable from the app. Edits go into
`royalty_overrides.json` in the repo root — gitignored operator data, the same
shape of thing as kdp_books.json and seasonal.json — and are merged on top of
the built-ins at read time.

Three rules keep money safe:

1. **Validated on the way in.** `set_*` raises ValueError on anything that
   cannot be a real royalty, so the app can never write a number that would
   mis-price the account. Nothing is written when validation fails.
2. **Fail closed on the way out.** A file that is corrupt anyway (hand-edited,
   half-written) has its bad rows DROPPED and recorded in `errors()`.
   products.econ_gate reports those, so every economics-driven write refuses
   rather than quietly pricing off the defaults.
3. **The built-ins are the floor.** An override can replace a value or add a
   new one. It can never delete a shipped price point, and products.py keeps
   its self-assert on the shipped table.

EVERY market can be overridden. US carries an extra price ladder
(`tee_prices`) because a US tee earns a different royalty at each list price.
Other markets keep their per-market royalties under `markets.<CODE>` and fall
back to what derive_econ.py worked out from the product export. An operator
number always wins over a derived one: Amazon fixes a maximum price per product
per market, so the figure read off the Merch dashboard is the definitive one,
while a derived median only reflects whatever mix of listings happens to exist.

File shape (version 2; a version-1 file with no `markets` key still loads):

    {"version": 2,
     "tee_prices":    {"2199": {"royalty_cents": 688}},        # US ladder
     "product_types": {"mug":  {"royalty": 2.54, "price": 16.99}},   # US
     "markets": {"DE": {"product_types": {"standard_tshirt": {...}}}}}
"""

import json
import os

import paths

CONFIG = os.path.join(paths.REPO_ROOT, "royalty_overrides.json")

# Sanity bounds. Wide on purpose — this is a guard against a typo or a stray
# unit (cents typed into a dollars box), not an opinion about pricing.
MIN_PRICE = 0.50
MAX_PRICE = 500.00

_cache = {}          # market -> parsed slice
_cache_stamp = None


def invalidate():
    """Drop the cache. Called after a write, and by tests."""
    global _cache, _cache_stamp
    _cache, _cache_stamp = {}, None


def _stamp():
    try:
        st = os.stat(CONFIG)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


# ---- validation -------------------------------------------------------------

def _number(value, label):
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{label} must be a number")
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number, got {value!r}")
    if out != out or out in (float("inf"), float("-inf")):
        raise ValueError(f"{label} must be a real number")
    return out


def check_product_type(ptype, royalty, price):
    """Raise ValueError unless this is a royalty a product could really earn."""
    name = (ptype or "").strip()
    if not name:
        raise ValueError("product type is required")
    roy = _number(royalty, "royalty")
    prc = _number(price, "price")
    if prc < MIN_PRICE or prc > MAX_PRICE:
        raise ValueError(f"price must be between ${MIN_PRICE:.2f} and ${MAX_PRICE:.2f}")
    if roy <= 0:
        raise ValueError("royalty must be greater than zero")
    if roy >= prc:
        raise ValueError(f"royalty ${roy:.2f} cannot be at or above the ${prc:.2f} price")
    return name, round(roy, 4), round(prc, 2)


def check_tee_price(price_cents, royalty_cents):
    """Same test in the tee table's units — integer cents."""
    try:
        price = int(price_cents)
        roy = int(royalty_cents)
    except (TypeError, ValueError):
        raise ValueError("price and royalty must be whole cents")
    check_product_type("standard_tshirt", roy / 100.0, price / 100.0)
    return price, roy


# ---- read -------------------------------------------------------------------

def _parse_types(section, errors, where):
    types = {}
    for key, value in (section or {}).items():
        try:
            if not isinstance(value, dict):
                raise ValueError("entry must be an object")
            name, roy, prc = check_product_type(key, value.get("royalty"), value.get("price"))
            types[name] = {"royalty": roy, "price": prc,
                           "break_even": round(roy / prc, 6),
                           "note": value.get("note"),
                           "updated_at": value.get("updated_at")}
        except (ValueError, TypeError, AttributeError) as exc:
            errors.append(f"{where}product type {key}: {exc}")
    return types


def _parse(raw, market=None):
    """Split a raw file into usable rows and the errors that killed the rest.

    `market` picks the slice: the default market reads the top-level keys (it
    owns the tee ladder), any other reads `markets.<CODE>` and never sees a
    ladder."""
    import markets as _markets
    market = market or _markets.current()
    tee, types, errors = {}, {}, []
    if not isinstance(raw, dict):
        return tee, types, ["royalty_overrides.json is not a JSON object"]

    if market != _markets.DEFAULT:
        section = ((raw.get("markets") or {}).get(market) or {})
        return {}, _parse_types(section.get("product_types"), errors, f"{market} "), errors

    for key, value in (raw.get("tee_prices") or {}).items():
        try:
            cents = int(key)
            roy = value.get("royalty_cents") if isinstance(value, dict) else value
            cents, roy = check_tee_price(cents, roy)
            tee[cents] = roy
        except (ValueError, TypeError, AttributeError) as exc:
            errors.append(f"tee price {key}: {exc}")

    types = _parse_types(raw.get("product_types"), errors, "")
    return tee, types, errors


def load(market=None):
    """{'tee_prices': {cents: royalty_cents}, 'product_types': {...}, 'errors': [...]}
    for one market. Re-reads whenever the file's mtime changes, so the
    long-running serve worker sees an edit without a restart."""
    global _cache, _cache_stamp
    import markets as _markets
    market = market or _markets.current()
    stamp = _stamp()
    if stamp != _cache_stamp:
        _cache, _cache_stamp = {}, stamp
    if market in _cache:
        return _cache[market]
    empty = {"tee_prices": {}, "product_types": {}, "errors": []}
    if stamp is None:
        _cache[market] = empty
        return empty
    try:
        with open(CONFIG, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as exc:
        _cache[market] = {"tee_prices": {}, "product_types": {},
                          "errors": [f"royalty_overrides.json is unreadable: {exc}"]}
        return _cache[market]
    tee, types, errs = _parse(raw, market)
    _cache[market] = {"tee_prices": tee, "product_types": types, "errors": errs}
    return _cache[market]


def errors(market=None):
    return load(market)["errors"]


# ---- write ------------------------------------------------------------------

class OverridesUnreadable(Exception):
    """The overrides file is there and cannot be trusted."""


def _read_raw():
    """The whole overrides file, for a caller that is about to REWRITE it.

    A file that is not there is an empty config, and that is fine. A file that
    IS there and cannot be read is not: every setter takes this dict, adds one
    key and writes the result back over the file. So a corrupt file used to
    turn one royalty edit into "delete every other market's and every other
    product's override", and it did it quietly — the read errors that had been
    closing the economics gate disappeared along with them, which let live
    writes resume on fallback economics.

    Missing -> {}. Unreadable, malformed, or not an object -> raise, and the
    caller writes nothing.
    """
    if not os.path.exists(CONFIG):
        return {}
    try:
        with open(CONFIG, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as e:
        raise OverridesUnreadable(
            f"{os.path.basename(CONFIG)} could not be read ({e}). Nothing was "
            f"changed — fix or move the file, then try again.") from e
    if not isinstance(raw, dict):
        raise OverridesUnreadable(
            f"{os.path.basename(CONFIG)} is not a JSON object. Nothing was "
            f"changed — fix or move the file, then try again.")
    return raw


def _write_raw(raw):
    raw["version"] = 2
    tmp = CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(raw, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, CONFIG)       # atomic: a crash mid-write cannot truncate the file
    invalidate()


def _now():
    import datetime
    return datetime.datetime.now().isoformat(timespec="seconds")


def set_tee_price(price_cents, royalty_cents, note=None):
    """Set the royalty for one US tee list price. Raises ValueError if it is not
    a royalty that price could earn — and writes nothing in that case."""
    cents, roy = check_tee_price(price_cents, royalty_cents)
    raw = _read_raw()
    raw.setdefault("tee_prices", {})[str(cents)] = {
        "royalty_cents": roy, "note": note, "updated_at": _now()}
    _write_raw(raw)
    return {"price_cents": cents, "royalty_cents": roy}


def clear_tee_price(price_cents):
    raw = _read_raw()
    removed = bool((raw.get("tee_prices") or {}).pop(str(int(price_cents)), None))
    _write_raw(raw)
    return removed


def _types_section(raw, market):
    """The dict this market's product types live in, created if missing."""
    import markets as _markets
    if market == _markets.DEFAULT:
        return raw.setdefault("product_types", {})
    return raw.setdefault("markets", {}).setdefault(market, {}).setdefault("product_types", {})


def set_product_type(product_type, royalty, price, note=None, market=None):
    """Set royalty + list price for one product type in one market. Break-even
    is COMPUTED from them, never typed — a percentage entered by hand is the
    easiest number in this whole system to get wrong."""
    import markets as _markets
    market = market or _markets.current()
    name, roy, prc = check_product_type(product_type, royalty, price)
    raw = _read_raw()
    _types_section(raw, market)[name] = {
        "royalty": roy, "price": prc, "note": note, "updated_at": _now()}
    _write_raw(raw)
    return {"market": market, "product_type": name, "royalty": roy, "price": prc,
            "break_even": round(roy / prc, 6)}


def clear_product_type(product_type, market=None):
    import markets as _markets
    market = market or _markets.current()
    raw = _read_raw()
    removed = bool(_types_section(raw, market).pop((product_type or "").strip(), None))
    _write_raw(raw)
    return removed
