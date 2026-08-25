#!/usr/bin/env python3
"""
Market configuration — the system runs one marketplace at a time, selected by the
ADS_MARKET environment variable (default "US"). Every market-aware module
(ads_client, db, products) reads markets.current() so the same scripts work for any
market without code changes:

    ADS_MARKET=UK python3 scavenger_build.py

US is the default, so with no env var the system behaves EXACTLY as before
(US Merch profile, NA endpoint, ads_data.sqlite, hardcoded US economics).

Each market carries:
  profile_env  - the .env key holding that market's Sponsored Products profile id
  endpoint     - the Ads API region host (US = NA, UK + EU = EU)
  export_mkt   - the marketplace code as it appears in the Merch export ('us','gb',…)
  currency     - local currency (informational; ACOS is a unitless ratio)
  plus_x       - royalty multiplier (US Plus tier = 2x; non-US not multiplied)
"""

import os

NA = "https://advertising-api.amazon.com"
EU = "https://advertising-api-eu.amazon.com"

# `kind` distinguishes Merch-on-Demand tee economics ("merch") from KDP book
# economics ("kdp"). `label` is the friendly name shown in the app's profile
# switcher. KDP is a SEPARATE Amazon Ads advertiser profile under the same
# account (Sponsored ads · KDP) — modeled as its own market with its own DB.
MARKETS = {
    "US": dict(profile_env="AMZN_ADS_PROFILE_ID_US", endpoint=NA, export_mkt="us", currency="USD", plus_x=2.0, locale="en_US", kind="merch", label="Merch US"),
    "UK": dict(profile_env="AMZN_ADS_PROFILE_ID_UK", endpoint=EU, export_mkt="gb", currency="GBP", plus_x=1.0, locale="en_GB", kind="merch", label="Merch UK"),
    "DE": dict(profile_env="AMZN_ADS_PROFILE_ID_DE", endpoint=EU, export_mkt="de", currency="EUR", plus_x=1.0, locale="de_DE", kind="merch", label="Merch DE"),
    "FR": dict(profile_env="AMZN_ADS_PROFILE_ID_FR", endpoint=EU, export_mkt="fr", currency="EUR", plus_x=1.0, locale="fr_FR", kind="merch", label="Merch FR"),
    "ES": dict(profile_env="AMZN_ADS_PROFILE_ID_ES", endpoint=EU, export_mkt="es", currency="EUR", plus_x=1.0, locale="es_ES", kind="merch", label="Merch ES"),
    "IT": dict(profile_env="AMZN_ADS_PROFILE_ID_IT", endpoint=EU, export_mkt="it", currency="EUR", plus_x=1.0, locale="it_IT", kind="merch", label="Merch IT"),
    "USKDP": dict(profile_env="AMZN_ADS_PROFILE_ID_US_KDP", endpoint=NA, export_mkt="us", currency="USD", plus_x=1.0, locale="en_US", kind="kdp", label="KDP US"),
}

DEFAULT = "US"


def kind(market=None):
    return MARKETS[market or current()].get("kind", "merch")


def is_kdp(market=None):
    return kind(market) == "kdp"

# Capped tee SELLING price per market (local currency). This is the real price you
# can sell at — overrides the export's median listPrice when computing break-even
# (break-even ACOS = tee royalty / this price). US uses its own hardcoded table.
# Amazon fixes a maximum price per product per market, so these are caps, not
# guesses. Read off the Merch dashboard by the operator 2026-08-20; DE was
# 18.45 before that. derive_econ.py uses this as the tee break-even price.
TEE_PRICE = {"UK": 17.49, "DE": 17.99, "FR": 19.49, "ES": 19.49, "IT": 19.49}


def current():
    """The active market code (from ADS_MARKET env, default US)."""
    m = os.environ.get("ADS_MARKET", DEFAULT).upper()
    if m not in MARKETS:
        raise SystemExit(f"Unknown ADS_MARKET={m}. Known: {', '.join(MARKETS)}")
    return m


def cfg(market=None):
    return MARKETS[market or current()]


def available(env):
    """Markets whose profile id is actually present in .env (so they can run)."""
    return [m for m, c in MARKETS.items() if (env.get(c["profile_env"]) or "").strip()]


def is_default(market=None):
    return (market or current()) == DEFAULT
