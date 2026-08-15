#!/usr/bin/env python3
"""Rules DSL entity loaders (Spec B Layer 1, Task 4). Loads each entity kind
from the latest cumulative snapshot (read the latest date, SUM across entities
within it — never across dates) into EntityRow objects the evaluator reads.

Economics fields (break_even/royalty/profit/halo/…) are attached separately by
rules.econ_fields (Task 5), which reuses the phase resolvers."""


class FieldError(Exception):
    pass


# The entity kinds that have a TRUE per-day table behind them: targets, keywords
# and ad groups read target_daily, campaigns read campaign_daily. Search terms
# and products have no per-day source at all.
#
# This set is the one authority on the question. rules.runner reads it to reject
# a rolling window while the operator is typing, and load() below refuses the
# same thing at read time. Both are needed: the save-time check is skipped by
# preview, and preview is what the nightly, collect and run paths all go
# through. A kind missing from here must never be quietly served CURRENT rows
# instead — that would hand the executor numbers from one table to gate against
# another.
ROLLING_ENTITIES = {"target", "keyword", "adgroup", "campaign"}


def _acos(cost, sales):
    return round(cost / sales, 4) if sales else None


def _cvr(orders, clicks):
    return round(orders / clicks, 4) if clicks else 0.0


def _ctr(clicks, imps):
    return round(clicks / imps, 4) if imps else 0.0


def _cpc(cost, clicks):
    return round(cost / clicks, 4) if clicks else None


def _roas(sales, cost):
    return round(sales / cost, 4) if cost else None


def _aov(sales, orders):
    return round(sales / orders, 4) if orders else None


class EntityRow:
    """One entity with a flat field dict. `.field(name)` reads metric/setting
    fields; economics fields are merged in by econ_fields.resolve()."""

    def __init__(self, kind, rid, fields, label=None):
        self.kind = kind
        self.id = rid
        self.fields = fields
        self.label = label or fields.get("name") or fields.get("keyword_text") or str(rid)

    def field(self, name):
        key = name.lower()
        if key in self.fields:
            return self.fields[key]
        raise FieldError(f"unknown field {name!r} on {self.kind}")

    def has(self, name):
        return name.lower() in self.fields

    def merge(self, extra):
        self.fields.update({k.lower(): v for k, v in extra.items()})


def _latest(conn, table):
    row = conn.execute(f"SELECT MAX(date) FROM {table}").fetchone()
    return row[0] if row else None


# How old a never-touched entity's last change counts as.
#
# An entity the engine has NEVER changed has waited forever — that is knowledge,
# not a gap. This used to return None, and every shipped cooldown reads
# `days_since_bid_change > 7`, which is false against NONE. So the cooldown
# rules only ever fired on the handful of targets already in writes_log: 55 out
# of 43,370 in US on 2026-08-06, which is why two of the three shipped rules and
# five of the library templates matched nothing at all and read as broken.
#
# writes_log records only the changes THIS engine made, so "no row" means "we
# have no cooldown obligation here" — exactly what a cooldown is asking. A bid
# somebody changed by hand in the Amazon console was never ours to wait on.
NEVER_CHANGED_DAYS = 99999


def _days_since_change_map(conn, action):
    """{entity_id: days since our most recent <action> on it}.

    ONE query for the whole table. This used to run per row inside the entity
    loop — 43k queries for a single US target preview.
    """
    import datetime
    now = datetime.datetime.now()
    ages = {}
    for entity_id, applied_at in conn.execute(
            """SELECT entity_id, MAX(applied_at) FROM writes_log
                WHERE action=? AND entity_id IS NOT NULL GROUP BY entity_id""",
            (action,)):
        if not applied_at:
            continue
        try:
            at = datetime.datetime.fromisoformat(applied_at)
        except ValueError:
            continue
        ages[str(entity_id)] = (now - at).days
    return ages


def load(conn, kind, window="CURRENT", window_days=None, today=None):
    """Load one entity kind into EntityRow objects.

    CURRENT and LIFETIME read the latest cumulative snapshot. ROLLING reads the
    true per-day tables over a lagged date range: targets and ad groups from
    target_daily, campaigns from campaign_daily. `today` exists so tests can pin
    the date.
    """
    kind = kind.lower()
    rolling = None
    if window == "ROLLING":
        if kind not in ROLLING_ENTITIES:
            raise FieldError(
                f"{kind} has no per-day history, so it cannot use IN LAST n DAYS "
                f"— rolling windows work on target, keyword, adGroup and campaign")
        import db
        rolling = db.daily_window(int(window_days), today=today)
    if kind in ("target", "keyword"):
        return _load_targets(conn, kind, rolling=rolling)
    if kind == "searchterm":
        return _load_search_terms(conn)
    if kind == "campaign":
        return _load_campaigns(conn, rolling=rolling)
    if kind == "adgroup":
        return _load_ad_groups(conn, rolling=rolling)
    if kind in ("product", "asin"):
        return _load_products(conn)
    if kind == "accumulated_asin":
        return _load_accumulated_asins(conn)
    if kind == "accumulated_keyword":
        return _load_accumulated_keywords(conn)
    raise FieldError(f"unknown entity {kind!r}")


def _base_metrics(imps, clicks, cost, orders, sales, kenp_read=0, kenp_royalties=0):
    imps, clicks, cost, orders, sales = (imps or 0, clicks or 0, cost or 0,
                                         orders or 0, sales or 0)
    # KENP (Kindle Edition Normalized Pages) is a KDP-only CURRENT/snapshot metric,
    # read from the *_perf tables. Loaders that read a *_daily table (rolling
    # windows) do not pass it, so it defaults to 0 — the field resolves on every
    # entity, real where banked and 0 where not yet (target_daily has no kenp
    # column this pass). For Merch it is always 0: the Merch pull never requests it.
    kenp_read, kenp_royalties = kenp_read or 0, kenp_royalties or 0
    return {
        "impressions": imps, "clicks": clicks, "spend": round(cost, 4),
        "cost": round(cost, 4), "sales": round(sales, 4), "orders": orders,
        "units": orders, "acos": _acos(cost, sales), "cvr": _cvr(orders, clicks),
        "ctr": _ctr(clicks, imps), "cpc": _cpc(cost, clicks), "roas": _roas(sales, cost),
        "aov": _aov(sales, orders),
        "kenp": kenp_read, "kenp_royalties": kenp_royalties,
    }


def _daily_key(kind, fields):
    """The identity a per-day window sums under — matches the rolling loaders'
    GROUP BY so an inline baseline window lines up with the FOR EACH row."""
    kind = kind.lower()
    if kind in ("target", "keyword"):
        return (fields.get("campaign_id"), fields.get("ad_group_id"),
                fields.get("targeting"), fields.get("match_type"))
    if kind == "adgroup":
        return fields.get("ad_group_id")
    if kind == "campaign":
        return fields.get("campaign_id")
    return None


def windowed_metrics(conn, kind, start, end):
    """{entity daily-key -> base metrics} over the inclusive [start, end] range,
    keyed exactly like the rolling loaders group. Powers inline baseline/trend
    windows (`metric IN FROM a TO b`). Empty for entity kinds with no per-day
    table — their windowed metrics then read NONE, which fails closed."""
    kind = kind.lower()
    if kind not in ROLLING_ENTITIES:
        return {}
    if kind in ("target", "keyword"):
        sql = """SELECT campaign_id, ad_group_id, targeting, match_type,
                        SUM(impressions), SUM(clicks), SUM(cost), SUM(orders), SUM(sales)
                   FROM target_daily WHERE date BETWEEN ? AND ?
                  GROUP BY campaign_id, ad_group_id, targeting, match_type"""

        def key(r):
            return (str(r[0]) if r[0] is not None else None,
                    str(r[1]) if r[1] is not None else None, r[2], r[3])
        mstart = 4
    elif kind == "adgroup":
        sql = """SELECT ad_group_id, SUM(impressions), SUM(clicks), SUM(cost),
                        SUM(orders), SUM(sales)
                   FROM target_daily WHERE date BETWEEN ? AND ? GROUP BY ad_group_id"""

        def key(r):
            return str(r[0]) if r[0] is not None else None
        mstart = 1
    else:  # campaign
        sql = """SELECT campaign_id, SUM(impressions), SUM(clicks), SUM(cost),
                        SUM(orders), SUM(sales)
                   FROM campaign_daily WHERE date BETWEEN ? AND ? GROUP BY campaign_id"""

        def key(r):
            return str(r[0]) if r[0] is not None else None
        mstart = 1
    out = {}
    for r in conn.execute(sql, (start, end)):
        imps, clicks, cost, orders, sales = r[mstart:mstart + 5]
        out[key(r)] = _base_metrics(imps, clicks, cost, orders, sales)
    return out


def _target_mirror(conn):
    """target_id -> (own bid, own state) from the pull's `targets` mirror.
    Empty on a DB the updated nightly hasn't touched yet (readers are ro and
    skip schema, so the table may not exist there — treat as no mirror)."""
    import sqlite3
    try:
        return {str(r[0]): (r[1], r[2]) for r in
                conn.execute("SELECT target_id, bid, state FROM targets")}
    except sqlite3.OperationalError:
        return {}


def _load_targets(conn, kind, rolling=None):
    defaults = dict(conn.execute("SELECT ad_group_id, default_bid FROM ad_groups"))
    prod = {r[0]: (r[1], r[2]) for r in
            conn.execute("SELECT ad_group_id, asin, product_type FROM ad_group_product")}
    ag_state = dict(conn.execute("SELECT ad_group_id, state FROM ad_groups"))
    mirror = _target_mirror(conn)
    bid_ages = _days_since_change_map(conn, "bid_change")
    if rolling:
        # True per-day rows summed over the window. Grouped on the same key
        # target_daily is stored under — which does not include target_id.
        # If a keyword is deleted and recreated mid-window under the same
        # targeting text and match type, Amazon gives it a new target_id, and
        # a bare `target_id` column would let SQLite pick an arbitrary row's
        # value for the group. MAX() makes that pick deterministic, and
        # SQLite's MAX skips NULLs, so a real id wins over a NULL when a
        # group mixes them. Do not simplify this back to a bare column.
        # target_daily has no kenp column this pass, so a rolling window reads
        # KENP as 0. The two literal 0s keep this row shape identical to the
        # CURRENT branch below (which reads the real kenp columns), so the shared
        # unpack does not have to branch on window.
        sql = """SELECT MAX(target_id), campaign_id, ad_group_id, targeting, match_type,
                        SUM(impressions), SUM(clicks), SUM(cost),
                        SUM(orders), SUM(sales), 0, 0
                   FROM target_daily WHERE date BETWEEN ? AND ?
                  GROUP BY campaign_id, ad_group_id, targeting, match_type"""
        params = rolling
    else:
        sql = """SELECT target_id, campaign_id, ad_group_id, targeting, match_type,
                        impressions, clicks, cost, orders, sales,
                        kenp_read, kenp_royalties
                   FROM targeting_perf WHERE date=?"""
        params = (_latest(conn, "targeting_perf"),)
    rows = []
    for (tid, cid, agid, targeting, mt, imps, clicks, cost, orders, sales,
         kenp_read, kenp_royalties) in conn.execute(sql, params):
        asin, ptype = prod.get(agid, (None, None))
        f = _base_metrics(imps, clicks, cost, orders, sales, kenp_read, kenp_royalties)
        # The pull's targets mirror carries each clause/keyword's OWN bid and
        # state. Without a mirror row (auto clause with no bid, or a pre-mirror
        # DB) the ad-group default rules the auction: bid_inherited=True.
        own_bid, own_state = mirror.get(str(tid), (None, None))
        f.update({
            "match_type": mt, "keyword_text": targeting, "targeting": targeting,
            "asin": asin, "product_type": ptype, "campaign_id": str(cid),
            "ad_group_id": str(agid), "target_id": str(tid) if tid is not None else None,
            "bid": own_bid if own_bid is not None else defaults.get(agid),
            "bid_inherited": own_bid is None,
            "default_bid": defaults.get(agid),
            "state": own_state or ag_state.get(agid), "ad_type": "SP", "targeting_type": mt,
            "days_since_bid_change": bid_ages.get(str(tid), NEVER_CHANGED_DAYS),
        })
        rows.append(EntityRow(kind, str(tid) if tid is not None else targeting, f,
                              label=targeting))
    return rows


def _load_search_terms(conn):
    latest = _latest(conn, "search_term_perf")
    rows = []
    for (st, cid, agid, targeting, mt, imps, clicks, cost, orders, sales,
         kenp_read, kenp_royalties) in conn.execute(
        """SELECT search_term, campaign_id, ad_group_id, targeting, match_type,
                  impressions, clicks, cost, orders, sales,
                  kenp_read, kenp_royalties
             FROM search_term_perf WHERE date=?""", (latest,)):
        f = _base_metrics(imps, clicks, cost, orders, sales, kenp_read, kenp_royalties)
        f.update({"search_term": st, "keyword_text": st, "match_type": mt,
                  "targeting": targeting, "campaign_id": str(cid),
                  "ad_group_id": str(agid), "ad_type": "SP"})
        rows.append(EntityRow("searchterm", st, f, label=st))
    return rows


def _load_campaigns(conn, rolling=None):
    meta = {r[0]: r for r in conn.execute(
        "SELECT campaign_id, name, state, daily_budget, bidding_strategy FROM campaigns")}
    budget_ages = _days_since_change_map(conn, "budget_change")
    if rolling:
        # campaign_daily has held true per-day rows since backfill_daily.py, so
        # campaign rolling windows cost nothing extra.
        sql = """SELECT campaign_id, SUM(impressions), SUM(clicks), SUM(cost),
                        SUM(orders), SUM(sales)
                   FROM campaign_daily WHERE date BETWEEN ? AND ?
                  GROUP BY campaign_id"""
        params = rolling
    else:
        sql = """SELECT campaign_id, SUM(impressions), SUM(clicks), SUM(cost),
                        SUM(orders), SUM(sales)
                   FROM campaign_perf WHERE date=? GROUP BY campaign_id"""
        params = (_latest(conn, "campaign_perf"),)
    rows = []
    for (cid, imps, clicks, cost, orders, sales) in conn.execute(sql, params):
        m = meta.get(cid)
        f = _base_metrics(imps, clicks, cost, orders, sales)
        f.update({"name": m[1] if m else None, "state": m[2] if m else None,
                  "budget": m[3] if m else None, "bidding_strategy": m[4] if m else None,
                  "campaign_id": str(cid), "ad_type": "SP",
                  "days_since_budget_change": budget_ages.get(str(cid), NEVER_CHANGED_DAYS)})
        rows.append(EntityRow("campaign", str(cid), f, label=m[1] if m else str(cid)))
    return rows


def _load_ad_groups(conn, rolling=None):
    meta = {r[0]: r for r in conn.execute(
        "SELECT ad_group_id, name, state, default_bid, campaign_id FROM ad_groups")}
    prod = {r[0]: (r[1], r[2]) for r in
            conn.execute("SELECT ad_group_id, asin, product_type FROM ad_group_product")}
    if rolling:
        # An ad group is its targets summed.
        sql = """SELECT ad_group_id, SUM(impressions), SUM(clicks), SUM(cost),
                        SUM(orders), SUM(sales)
                   FROM target_daily WHERE date BETWEEN ? AND ?
                  GROUP BY ad_group_id"""
        params = rolling
    else:
        sql = """SELECT ad_group_id, SUM(impressions), SUM(clicks), SUM(cost),
                        SUM(orders), SUM(sales)
                   FROM targeting_perf WHERE date=? GROUP BY ad_group_id"""
        params = (_latest(conn, "targeting_perf"),)
    rows = []
    for (agid, imps, clicks, cost, orders, sales) in conn.execute(sql, params):
        m = meta.get(agid)
        asin, ptype = prod.get(agid, (None, None))
        f = _base_metrics(imps, clicks, cost, orders, sales)
        f.update({"name": m[1] if m else None, "state": m[2] if m else None,
                  "default_bid": m[3] if m else None,
                  "campaign_id": str(m[4]) if m else None, "ad_group_id": str(agid),
                  "asin": asin, "product_type": ptype, "ad_type": "SP"})
        rows.append(EntityRow("adgroup", str(agid), f, label=m[1] if m else str(agid)))
    return rows


def _load_accumulated_asins(conn):
    """One row per advertised ASIN, summed across every campaign it runs in at the
    latest snapshot. The DSL's cross-campaign lens: `campaigns` / `ad_groups` count
    how widely it spread. Actions are the everywhere verbs, which fan out at apply."""
    latest = _latest(conn, "targeting_perf")
    rows = []
    for asin, ptype, ncamp, nag, imps, clk, cost, orders, sales in conn.execute(
        """SELECT p.asin, MIN(p.product_type),
                  COUNT(DISTINCT t.campaign_id), COUNT(DISTINCT t.ad_group_id),
                  SUM(t.impressions), SUM(t.clicks), SUM(t.cost), SUM(t.orders), SUM(t.sales)
             FROM targeting_perf t
             JOIN ad_group_product p ON p.ad_group_id = t.ad_group_id
            WHERE t.date=? AND p.asin IS NOT NULL
            GROUP BY p.asin""", (latest,)):
        f = _base_metrics(imps, clk, cost, orders, sales)
        f.update({"asin": asin, "product_type": ptype, "targeting": asin,
                  "campaigns": ncamp, "ad_groups": nag, "ad_type": "SP"})
        rows.append(EntityRow("accumulated_asin", asin, f, label=asin))
    return rows


def _load_accumulated_keywords(conn):
    """One row per keyword/target text, summed across every campaign and ad group
    at the latest snapshot. `campaigns` / `ad_groups` say how far it spread."""
    latest = _latest(conn, "targeting_perf")
    rows = []
    for targeting, mt, ncamp, nag, imps, clk, cost, orders, sales in conn.execute(
        """SELECT t.targeting, MIN(t.match_type),
                  COUNT(DISTINCT t.campaign_id), COUNT(DISTINCT t.ad_group_id),
                  SUM(t.impressions), SUM(t.clicks), SUM(t.cost), SUM(t.orders), SUM(t.sales)
             FROM targeting_perf t
            WHERE t.date=? GROUP BY t.targeting""", (latest,)):
        f = _base_metrics(imps, clk, cost, orders, sales)
        f.update({"targeting": targeting, "keyword_text": targeting, "match_type": mt,
                  "campaigns": ncamp, "ad_groups": nag, "ad_type": "SP"})
        rows.append(EntityRow("accumulated_keyword", targeting, f, label=targeting))
    return rows


def _load_products(conn):
    latest = _latest(conn, "targeting_perf")
    rows = []
    for (asin, ptype, ncamp, imps, clicks, cost, orders, sales) in conn.execute(
        """SELECT p.asin, MIN(p.product_type), COUNT(DISTINCT t.campaign_id),
                  SUM(t.impressions), SUM(t.clicks), SUM(t.cost), SUM(t.orders), SUM(t.sales)
             FROM targeting_perf t JOIN ad_group_product p ON p.ad_group_id = t.ad_group_id
            WHERE t.date=? AND p.asin IS NOT NULL
            GROUP BY p.asin""", (latest,)):
        f = _base_metrics(imps, clicks, cost, orders, sales)
        f.update({"asin": asin, "product_type": ptype, "campaigns": ncamp, "ad_type": "SP"})
        rows.append(EntityRow("product", asin, f, label=asin))
    return rows
