#!/usr/bin/env python3
"""
SCAVENGER module — shared config + helpers. A separate entity from the standard
per-type ACOS rules and from TAMAS. Scavenger campaigns scoop up CHEAP CLICKS:
  - one campaign, MANY ASINs in a single ad group (product ads)
  - ~200 BROAD keywords (Amazon's suggestions), all at a rock-bottom bid
  - dynamic bids UP & DOWN, low daily budget
  - judged simply: a keyword that wastes clicks with no sales gets paused

Settings locked to the "scavenger campaign" method from the playbook:
  5c bids · $5/day · broad only · dynamic up&down · ~200 suggested keywords ·
  live US standard tees that already have >=1 sale, <=1000 ASINs per campaign.

Pricing and design/research stay manual — no API. Scavenger is DISCOVERY: winners
it finds get promoted to focused manual campaigns by the normal harvest step.
"""

PREFIX = "SCAVENGER - "

# build defaults (from the video)
DEFAULT_BID = 0.05            # rock-bottom bid; forced onto every keyword
DEFAULT_BUDGET = 5.00         # "usually $5 or $10" -> lower, safer end
MATCH = "BROAD"              # broad only (drop the auto category keyword)
BIDDING_STRATEGY = "AUTO_FOR_SALES"   # Amazon's "dynamic bids - up and down"
MAX_KEYWORDS = 200           # "add all" -> ~200 suggested keywords per campaign
MAX_ASINS = 1000             # API/UI cap per campaign
MAX_CAMPAIGNS = 6            # auto-shard the cohort into up to this many campaigns
                            # (1000 ASINs each). Bounds spend: each campaign = $5/day.
REC_DELAY_SEC = 20          # pause between keyword-recommendation calls (avoid HTTP 429)

COHORT_TYPE = "standard_tshirt"   # the type used for the New Uploads cohort


def cohort_market():
    """Export marketplace code for the active market ('us','gb','de',…)."""
    import markets
    return markets.cfg()["export_mkt"]

# Scavenger cohorts. Each = a campaign SERIES grouping related product types, with
# `econ` = the product type whose economics drive that series' keyword-prune stop-loss
# (the dominant type in the group). One series per group keeps pruning per-type-correct.
#   sales cohort = live US designs of those types with >=1 sale (proven demand)
TEES_SERIES = "US Tees"
NEW_SERIES = "New Uploads"        # tees uploaded this year, 0 sales yet (new-design discovery)

# NOTE: hardgoods (hats, mugs, water bottles, some tumblers) ARE advertisable, but
# only via their AD-ELIGIBLE ASIN (the export's `adAsins` field), not the retail ASIN
# — feeding the retail ASIN returns AD_INELIGIBLE. The builder uses adAsins when
# present, else the retail ASIN (apparel has no adAsins; its retail ASIN is eligible).
# Listings with no ad-eligible ASIN simply can't be advertised and are skipped.
COHORTS = [
    {"series": TEES_SERIES,   "types": {"standard_tshirt"},                                                "econ": "standard_tshirt"},
    {"series": "Hoodies",     "types": {"standard_pullover_hoodie", "zip_hoodie", "performance_hoodie"},   "econ": "standard_pullover_hoodie"},
    {"series": "Sweatshirts", "types": {"standard_sweatshirt", "comfort_colors_sweatshirt", "comfort_colors_crop_sweatshirt"}, "econ": "standard_sweatshirt"},
    {"series": "Drinkware",   "types": {"tumbler", "mug", "water_bottle"}, "econ": "mug", "source_kw": "tumbler"},
    {"series": "Hats",        "types": {"printed_trucker_hat", "printed_baseball_hat", "sport_sun_visor"},  "econ": "printed_trucker_hat"},
]
# `source_kw`: load this cohort's ad-safe ASINs from a dedicated MerchFlow export
# (a POD csv whose filename contains this keyword, with an "ASIN (Ad-Safe)" column),
# instead of the main export. Falls back to the main export if no such file is present.

# prune rule (no number given in the video -> reuse your tee stop-loss).
# A keyword with 0 orders, at least MIN_CLICKS clicks, and spend over the
# standard-tee negative-keyword threshold (royalty * 0.5) gets paused.
MIN_CLICKS_PRUNE = 15
# chronic-dead campaign retire ("don't marry it"): after real spend, retire a scavenger
# campaign that is EITHER near-zero orders, OR converting but clearly unprofitable
# (ACOS above the product's target x the discovery buffer below — scavenger is allowed
# to run hotter than a proven campaign while hunting winners, but not to just bleed).
CHRONIC_SPEND = 25.00
CHRONIC_MAX_ORDERS = 1
CHRONIC_ACOS_MULT = 1.5    # retire if campaign ACOS > target_acos x this


def is_scavenger(campaign_name):
    return (campaign_name or "").startswith(PREFIX)


def camp_name(n=1, series=TEES_SERIES):
    # US keeps the legacy "US Tees" name (existing campaigns); other markets get a
    # clean "Tees" (they live in their own market account, no need for a country tag).
    import markets
    if series == TEES_SERIES and not markets.is_default():
        series = "Tees"
    return f"{PREFIX}{series} {n}"


def new_uploads_since():
    """Start of the current year — the video's 'uploaded this year' filter."""
    import datetime
    return f"{datetime.date.today().year}-01-01"


def series_of(campaign_name):
    """'SCAVENGER - Hoodies 3' -> 'Hoodies'. None if not a scavenger campaign."""
    if not is_scavenger(campaign_name):
        return None
    body = campaign_name[len(PREFIX):]      # 'Hoodies 3'
    return body.rsplit(" ", 1)[0] if " " in body else body


def econ_type_for_campaign(campaign_name):
    """The product type whose economics prune this scavenger campaign's keywords."""
    series = series_of(campaign_name)
    if series == NEW_SERIES:
        return COHORT_TYPE
    for c in COHORTS:
        if c["series"] == series:
            return c["econ"]
    return COHORT_TYPE
