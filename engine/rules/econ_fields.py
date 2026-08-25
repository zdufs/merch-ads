#!/usr/bin/env python3
"""Rules DSL economics field resolver (Spec B Layer 1, Task 5) — the moat.

Attaches break_even/royalty/profit/royalty_roi/halo/transition/cohort fields to
an EntityRow by REUSING the phase economics (products.get_design_econ,
products.get_econ, db.get_design_map/active_price_changes, halo) — never a
re-implementation. The break-even GATING is the single source products.design_be_for
(also used by appctl's kill list — no drift); break-even and royalty FORMULAS live
in products. When economics is unavailable, every economics numeric field is None
so economics-gated rules skip the row.
"""

import sqlite3

import db
import markets
import products
import cross_sell


class Context:
    """Per-run economics context — precomputed once, reused for every entity."""

    def __init__(self, conn):
        self.conn = conn
        self.market = markets.current()
        self.be_for = products.design_be_for(conn)   # single-source gating (may be None)
        try:
            self.available = db.econ_tables_present(conn)
            self.dmap = db.get_design_map(conn) if self.available else {}
            self.trans = db.active_price_changes(conn) if self.available else {}
        except sqlite3.OperationalError:
            self.available = False
            self.dmap = {}
            self.trans = {}
        # Owned cross-sell royalty per ad group — measured once for the whole run.
        # Guarded so a DB without the purchased-product / econ snapshot (or a test
        # DB) simply carries no cross-sell, never an error.
        # An EXCEPTION here and an empty result are not the same thing, and the
        # difference reaches a rule as the difference between "this design drives
        # no cross-sales" and "we could not look". Both became {} and then 0.0,
        # so a protective condition like `owned_cross_sell < spend` passed on a
        # design that might well have been carrying the catalogue.
        self.cross_available = True
        try:
            self.cross = cross_sell.owned_cross_sell_royalty(conn)
        except Exception:
            self.cross = {}
            self.cross_available = False
        self._halo = None            # lazy; US only

    def halo_map(self):
        """asin -> {halo_est, net_halo, organic_per_day}. US only; empty when the
        SALES_REPORT / halo pipeline is unavailable (e.g. test DBs)."""
        if self._halo is not None:
            return self._halo
        self._halo = {}
        if markets.is_default():
            try:
                import halo
                # limit=0: the DSL matches on every design, not a top-N view.
                # Pass OUR connection — without it halo opened the market DB and a
                # rule evaluated against a temporary database read real halo data.
                data = halo.analyze(limit=0, conn=self.conn) or {}
                for d in data.get("designs", []):
                    self._halo[d["asin"]] = {
                        "halo_est": d.get("halo_est"),
                        "net_halo": d.get("net_halo"),
                        "organic_per_day": d.get("post_rate"),
                    }
            except Exception:
                self._halo = {}
        return self._halo


def _break_even(ctx, agid):
    """(break_even, skip_reason) via the single-source gating in
    products.design_be_for (shared with appctl's kill list — no drift)."""
    if ctx.be_for is None:
        return None, "unmapped"
    return ctx.be_for(agid)


def resolve(ctx, row):
    """Merge economics fields into an EntityRow (in place)."""
    agid = row.fields.get("ad_group_id")
    asin = row.fields.get("asin")
    d = ctx.dmap.get(str(agid)) if agid else None
    pt = (d or {}).get("product_type") or row.fields.get("product_type")

    be, skip = _break_even(ctx, agid) if agid else (None, "unmapped")
    in_transition = bool(asin and ctx.trans.get(asin))
    is_cohort = (skip == "cohort") or (d is not None and d.get("asin") is None)
    econ_available = skip is None

    # Owned cross-sell royalty is a MEASURED figure: a design with no owned
    # cross-purchases carries 0.0, not None, so a guard like
    # `owned_cross_sell < spend` still pauses it. A target inherits its ad
    # group's value; a campaign (no ad_group_id) carries 0.0.
    # But a measurement that COULD NOT RUN carries NONE, because that is not a
    # zero — it is the absence of an answer, and this engine fails closed on
    # those everywhere else.
    xs = ctx.cross.get(str(agid)) if agid else None
    if not getattr(ctx, "cross_available", True):
        # The measurement failed. NONE, so a rule reading this field fails
        # closed like every other unavailable economics field, instead of
        # being handed a confident zero.
        owned_cross_sell = None
    else:
        owned_cross_sell = round(xs["royalty"], 4) if xs else 0.0

    fields = {
        "break_even": be,
        "in_transition": in_transition,
        "is_cohort": is_cohort,
        "econ_available": econ_available,
        "product_type": pt,
        "lifetime_sales": (d or {}).get("lifetime_sales", row.fields.get("lifetime_sales")),
        "owned_cross_sell": owned_cross_sell,
        "royalty": None, "profit": None, "royalty_roi": None,
        "halo_est": None, "net_halo": None, "organic_per_day": None,
    }

    if econ_available:
        if markets.is_kdp():
            import kdp_econ
            be2 = kdp_econ.book_econ(asin) if asin else None
            royalty = be2.get("royalty") if be2 else None
        else:
            e = products.get_design_econ(pt, price=(d or {}).get("list_price")) if pt else {}
            royalty = e.get("royalty")
        fields["royalty"] = royalty
        orders = row.fields.get("orders") or 0
        spend = row.fields.get("spend") or 0
        if royalty is not None:
            royalty_est = royalty * orders
            fields["profit"] = round(royalty_est - spend, 4)
            fields["royalty_roi"] = round(royalty_est / spend, 4) if spend else None

    if asin and markets.is_default():
        h = ctx.halo_map().get(asin)
        if h:
            fields["halo_est"] = h.get("halo_est")
            fields["net_halo"] = h.get("net_halo")
            fields["organic_per_day"] = h.get("organic_per_day")

    row.merge(fields)
    return row
