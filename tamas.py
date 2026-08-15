#!/usr/bin/env python3
"""
TAMAS module — shared config + helpers. A separate entity from the standard
per-type ACOS rules. TAMAS campaigns are name-prefixed and run on TRAZ-driven
rules (Total Royalties − Ad Spend), per the TAMAS method:
  - one broad keyword + one ASIN per manual campaign, FIXED bids
  - spend up to TEST_MULT x royalty hunting for a sale, else pause
  - scale bids slowly while TRAZ positive and CVR >= CVR_TARGET
  - judge on TRAZ/EPC, not ACOS (the organic halo is the point)

Pricing (low $13.99 testing) and broad-design/keyword choice stay manual — no API.
"""

PREFIX = "TAMAS - "

# launch defaults
DEFAULT_BID = 0.25          # low starting fixed bid
DEFAULT_BUDGET = 5.00       # low daily budget for testing
DEFAULT_MATCH = "phrase"    # presenter's best performer; broad/exact also valid
BIDDING_STRATEGY = "MANUAL" # FIXED bids (Amazon's MANUAL strategy)

# optimize rules
TEST_MULT = 10.0            # spend up to 10x royalty with 0 sales before pausing
CVR_TARGET = 0.10           # 10% conversion rate is the success bar
SCALE_UP = 1.05             # +5% when TRAZ positive & CVR good (scale slowly)
SCALE_DOWN = 0.85           # -15% when TRAZ negative
MIN_CLICKS = 10             # need some data before acting on a TAMAS campaign
MIN_BID, MAX_BID = 0.10, 2.00


def is_tamas(campaign_name):
    return (campaign_name or "").startswith(PREFIX)


def camp_name(keyword, asin):
    return f"{PREFIX}{keyword} - {asin}"
