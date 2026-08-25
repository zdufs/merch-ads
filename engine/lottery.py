#!/usr/bin/env python3
"""
LOTTERY ("Mark Shaggy lottery") module — config + helpers.

The lottery structure (matches your US "Lotto" campaigns):
  - AUTO targeting (Amazon finds the matches)
  - Dynamic bids DOWN ONLY (LEGACY_FOR_SALES), 30c base bid
  - ONE ad group PER ASIN inside a campaign (so each design has its own bid +
    its own search-term data — the key advantage over a shared-ad-group lottery)
  - all live tees, per market

Unlike scavenger, lottery campaigns are managed by the EXISTING optimizers:
phase2 pauses dead per-ASIN ad groups, phase3 tunes their bids, and harvest
promotes their converting search terms into focused manual campaigns. So this
module only needs a BUILDER; the daily engine takes it from there.

US keeps its MerchFlow-built "Lotto N" campaigns; this creates "LOTTO - N"
(distinct prefix) and is aimed at the markets that have none yet (EU).
"""

PREFIX = "LOTTO - "

DEFAULT_BID = 0.30                  # 30c per-ASIN bid (matches your US Lotto ad groups)
DEFAULT_BUDGET = 5.00               # daily budget per lottery campaign (halved from 10)
BIDDING_STRATEGY = "LEGACY_FOR_SALES"   # Dynamic bids - DOWN ONLY (your US Lotto setting)
TARGETING_TYPE = "AUTO"             # Amazon auto-targets
MAX_ADGROUPS = 500                  # ASINs (= ad groups) per campaign
MAX_CAMPAIGNS = 50                  # up to 25,000 tees/market — covers ALL live tees

# EU lottery cohort: ALL live standard t-shirts (every sales tier), tees only.
COHORT_TYPE = "standard_tshirt"


# Auto-targeting clause bids. US values are yours; other markets scale by tee royalty
# (bid = same % of per-sale royalty). complements are PAUSED (you don't use them).
US_CLAUSE_BIDS = {"close-match": 0.21, "loose-match": 0.18, "substitutes": 0.15}
# Amazon's meaning, not ours. HIGH relevance is the CLOSE match; BROAD
# relevance is the LOOSE one. These two were swapped until 2026-08-20, so every
# lottery campaign launched with the HIGH bid on loose match and the LOW bid on
# close match — paying most for the widest, least intentful clause. Proved by
# joining the targets mirror to Amazon's own report label; guarded by
# tests/clause_expression_tests.py.
EXPRESSION_TYPE = {
    "close-match": "QUERY_HIGH_REL_MATCHES",
    "loose-match": "QUERY_BROAD_REL_MATCHES",
    "substitutes": "ASIN_SUBSTITUTE_RELATED",
    "complements": "ASIN_ACCESSORY_RELATED",
}
# enum -> name, for anything reading clauses back off Amazon. One source of truth.
EXPRESSION_NAME = {v: k for k, v in EXPRESSION_TYPE.items()}
PAUSE_EXPRESSIONS = {"complements"}     # created paused — not used

# LEGACY BID-CALIBRATION CONSTANT — NOT economics (PLAN.md v6). Denominator of
# the EU bid-scaling ratio; the current EU bid schedule was tuned against the
# historical $4.89 US tee royalty. Feeding the live (higher) royalty through
# here would silently cut new UK/EU lottery bids ~29%. Re-tuning the schedule
# is a deliberate operator decision, not a side effect of a royalty refresh.
LOTTERY_BID_REF_US = 4.89


def clause_bids():
    """close/loose/substitutes bids for the active market: US as-is; others scaled by
    (market tee royalty / LOTTERY_BID_REF_US), in local currency."""
    import markets
    import products
    base = dict(US_CLAUSE_BIDS)
    if markets.is_default():
        return base
    mkt_roy = products.get_econ("standard_tshirt")["royalty"] or LOTTERY_BID_REF_US
    ratio = mkt_roy / LOTTERY_BID_REF_US
    return {k: round(v * ratio, 2) for k, v in base.items()}


def is_lottery(campaign_name):
    return (campaign_name or "").startswith(PREFIX)


def camp_name(n=1):
    return f"{PREFIX}{n}"
