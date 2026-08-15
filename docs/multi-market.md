# Multi-market expansion

The whole engine now runs **one marketplace at a time**, chosen by the `ADS_MARKET`
environment variable (default `US`). Same scripts, any market:

```
ADS_MARKET=UK python3 scavenger_build.py        # preview UK scavenger
ADS_MARKET=UK python3 phase0_pull.py            # pull UK data
```

With no env var everything is **US, exactly as before** (US profile, NA endpoint,
`ads_data.sqlite`, hardcoded US economics, TAMAS on).

## What each market gets
Same strategies as US **except TAMAS** (US-only): scavenger discovery, negative
keywords, ad-group pauses, bid management, search-term harvest — plus a per-market
Discord digest (daily + month-to-date spend/ACOS in local currency).

**Lottery** (auto-targeting, one ad group per ASIN, fixed 15¢) is created for EU
markets by `lottery_build.py` (`LOTTO - N` campaigns from proven sellers). US keeps its
MerchFlow-built `Lotto N` campaigns, so the daily job runs `lottery_build` for non-US
only. Lottery campaigns are managed by the normal engine (phase2 pauses dead per-ASIN
ad groups, phase3 tunes bids, harvest promotes winners) — no separate optimizer.

To preview/build lottery for a market manually:
```
ADS_MARKET=DE python3 lottery_build.py            # preview
ADS_MARKET=DE python3 lottery_build.py --apply
```

## Isolation (nothing collides with US)
| Concern | How |
|---|---|
| API profile + region | `markets.py` → US=NA host, UK/EU=EU host, profile per `.env` key |
| Data | own SQLite file per market: `ads_data_UK.sqlite`, `ads_data_DE.sqlite`, … (US keeps `ads_data.sqlite`) |
| Economics | US = hardcoded table; others **derived from the export** (`derive_econ.py` → `market_econ`), local currency |
| TAMAS | guarded to US only |

## Markets & status
- **US** — live. **UK** — ready (profile already in `.env`).
- **DE / FR / ES / IT** — need their profile id added to `.env` (one-time):

```
python3 list_profiles.py        # prints countryCode + profileId for your account
```
Copy the Merch profile id for each country into `.env`:
```
AMZN_ADS_PROFILE_ID_DE=...
AMZN_ADS_PROFILE_ID_FR=...
AMZN_ADS_PROFILE_ID_ES=...
AMZN_ADS_PROFILE_ID_IT=...
```
That's it — the daily job auto-detects any market with a profile (`markets.available`)
and loops it. No code changes.

## Daily job
`run_scheduled.sh` loops every available market (US first). US also runs TRAZ,
dashboard, and the MerchPirate demand feed; other markets run the core engine +
their own digest. Each market's reports generate independently, so a big multi-market
run takes a while — it's unattended at 10:00.

## First-run order for a new market (e.g. UK)
```
ADS_MARKET=UK python3 phase0_pull.py        # pull structure + 30-day reports
ADS_MARKET=UK python3 map_products.py       # map ad groups -> product type
ADS_MARKET=UK python3 derive_econ.py        # derive UK royalties/break-even
ADS_MARKET=UK python3 scavenger_build.py    # preview, then --apply to create campaigns
```
Fresh markets have no campaigns yet, so the **scavenger is the entry point** — it
creates the cheap-click discovery campaigns; the optimizers then manage them daily.
