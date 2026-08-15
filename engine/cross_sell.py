"""Owned cross-sell — the measured, catalogue-mine share of Amazon's halo.

A shopper clicks the ad for design A and buys design B. When B is one of MY
own designs (present in the mapped export), that sale is real profit A's ad
created. The campaign and targeting reports credit it nowhere, so A can look
like a loser while quietly earning royalty on the rest of the catalogue.

This module turns that into ONE number per ad group: the ROYALTY (not the
retail sale — I earn a royalty, not the whole price) that a design's ad drove
on my OTHER designs. The pause paths — the kill list and phase 2's nightly
auto-pause — use it to SPARE a bleeding design whose owned cross-sell royalty
covers its own ad spend.

Two deliberate choices:

  * "Mine" means present in the latest `asin_econ_snapshot` for this market.
    A third of the cross-purchased ASINs in real US data are NOT in the
    catalogue, and those must never protect a design — the whole point of the
    guard is that A's ad sold something of MINE, not a competitor's.

  * Royalty comes from `products.get_design_econ` — the same trusted per-design
    number the kill list and Profit use, which resolves US tee royalty from the
    price table rather than the export's stale historical figure.

Fail-open by construction: a market with no purchased_product snapshot (the EU
markets) or no econ snapshot returns {}, so the guard simply does nothing there.
It can only ever SPARE a pause, never cause one, so a missing table is safe.
"""

import sqlite3

import db
import markets
import products


def owned_cross_sell_royalty(conn, market=None, econ_conn=None):
    """ad_group_id (str) -> {royalty, owned_units, others:[{asin,units,royalty}]}.

    `royalty` sums, over every cross-purchased ASIN that is MINE, the modeled
    royalty per unit times the units bought off this ad group's ads. Returns an
    empty dict when the purchased-product or econ snapshot is absent (fail-open).

    `conn` is the CURRENT market's DB — it holds purchased_product. The econ
    snapshot is ACCOUNT-WIDE and lives only in the default DB (one export covers
    every marketplace), so it is read from the shared store, NOT from `conn`.
    That is what lets an EU market resolve royalty: its own DB has no econ rows.
    `econ_conn` overrides the shared connection for tests.
    """
    market = market or markets.current()

    # The measured cross-purchases (latest snapshot only — the table is a
    # trailing-30 cumulative pull, one row set per pull date, same as the perf
    # tables; never summed across dates).
    try:
        latest = db.latest_snapshot(conn, "purchased_product")
    except sqlite3.OperationalError:
        return {}
    if not latest:
        return {}

    export_mkt = markets.MARKETS.get(market, {}).get("export_mkt")
    if not export_mkt:
        return {}

    # Per-ASIN economics for MY catalogue, this marketplace, newest export —
    # from the account-wide default DB (every EU marketplace's rows live there).
    close_econ = econ_conn is None
    econ = econ_conn if econ_conn is not None else db.connect_shared(ro=True)
    econ_rows = {}
    try:
        latest_exp = econ.execute(
            "SELECT MAX(export_date) FROM asin_econ_snapshot WHERE marketplace=?",
            (export_mkt,)).fetchone()[0]
        if not latest_exp:
            return {}
        for asin, ptype, price in econ.execute(
            "SELECT asin, product_type, list_price FROM asin_econ_snapshot"
            " WHERE export_date=? AND marketplace=?", (latest_exp, export_mkt)):
            econ_rows[asin] = (ptype, price)
    except sqlite3.OperationalError:
        return {}
    finally:
        if close_econ:
            econ.close()

    # Royalty per unit is cached per (product_type, price): the tee-table lookup
    # is cheap, but this keeps the work O(distinct designs) not O(purchase rows).
    roy_cache = {}

    def per_unit_royalty(asin):
        rec = econ_rows.get(asin)
        if rec is None:
            return None                 # not mine — never protects a design
        key = rec
        if key not in roy_cache:
            ptype, price = rec
            econ = products.get_design_econ(ptype, market, price=price)
            roy = econ.get("royalty")
            roy_cache[key] = roy if (roy and roy > 0) else None
        return roy_cache[key]

    out = {}
    for agid, purchased, units in conn.execute(
        "SELECT ad_group_id, purchased_asin, SUM(units_sold_other_sku)"
        " FROM purchased_product WHERE date=? AND purchased_asin<>advertised_asin"
        " GROUP BY ad_group_id, purchased_asin", (latest,)):
        units = units or 0
        roy = per_unit_royalty(purchased)
        if not roy or units <= 0:
            continue                    # not mine, no econ, or no owned units
        value = roy * units
        rec = out.setdefault(str(agid), {"royalty": 0.0, "owned_units": 0, "others": []})
        rec["royalty"] = round(rec["royalty"] + value, 2)
        rec["owned_units"] += units
        rec["others"].append({"asin": purchased, "units": units,
                              "royalty": round(value, 2)})
    for rec in out.values():
        rec["others"].sort(key=lambda o: -o["royalty"])
    return out


def spares_pause(cross_map, ad_group_id, spend):
    """True when this ad group's owned cross-sell royalty covers its own ad spend.

    The single threshold, used by both pause paths so they never disagree:
    a design is spared when the royalty its ads earned on MY other designs is at
    least what the design itself spent. `cross_map` comes from
    owned_cross_sell_royalty; an empty map spares nothing.
    """
    rec = cross_map.get(str(ad_group_id))
    if not rec:
        return False
    return rec["royalty"] >= (spend or 0)
