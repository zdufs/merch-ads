# Changelog — Multi-market expansion + negatives (2026-06)

## Shipped & LIVE on the account

### Multi-market architecture
- Whole engine runs per marketplace via `ADS_MARKET` (default US = unchanged).
- Markets: **US, UK, DE, FR, ES, IT**. Each isolated: own profile + region endpoint
  (NA vs EU), own SQLite (`ads_data_<M>.sqlite`), own derived economics (local currency).
- `markets.py` config; `ads_client` profile/endpoint aware; `db` path aware;
  `products.get_econ` market-aware; sales-report features locked to US.
- DE/FR/ES/IT profile ids added to `.env` (found via `inspect_accounts.py`).

### Lottery (EU) — built & live
- `lottery.py` + `lottery_build.py`: AUTO targeting, dynamic-bids-DOWN-ONLY,
  one ad group per ASIN, close/loose/substitutes clauses at **royalty-scaled bids**,
  complements paused. Clone of the US MerchFlow Lotto.
- Rolled out: **~227 campaigns, ~111k ad groups** (UK 47 · DE 42 · FR 45 · ES 46 · IT 46).
- Budgets **$5/campaign** (halved). Per-market clause bids:
  UK .15/.13/.11 · DE .12/.10/.08 · FR .16/.14/.12 · ES .17/.15/.12 · IT .16/.14/.11.

### Reporting
- Schedule **6am → 10am**.
- Discord digest: leads with **daily spend, daily ACOS, month spend, month ACOS**
  (`daily_metrics.py`), posts **per market** in local currency.
- `dashboard.py` + `demand_feed.py` now per-market (`dashboard_<M>.html`,
  `demand_feed_<M>.json`); demand feed export filter fixed to per-market products.

### Economics
- Capped tee prices wired in (`markets.TEE_PRICE`): DE €18.45 · FR/ES/IT €19.49 · UK £17.49.
- Break-even ACOS recomputed per market from those prices.

### Reliability
- Rate-limit retry/backoff on all reads + writes; campaign-list caching;
  scavenger keyword cap + auto-retire of chronic-dead campaigns.

### New / changed files
markets.py · derive_econ.py · lottery.py · lottery_build.py · set_lottery_budget.py ·
list_profiles.py · daily_metrics.py · preempt.py · preempt_negatives.py ·
inspect_lotto_bids.py · (edits to ads_client, db, products, phase2, phase3, scavenger*,
notify_discord, dashboard, demand_feed, run_scheduled.sh, io.github.zdufs.merchads.plist)

---

## PENDING — coded + wired, but not yet executed on the account
These all fire automatically on the next 10am run; listed so nothing is a surprise.

- **Reactive negatives (new rules)** — `phase2_apply`: negate after **10 clicks / 0 sales**
  (was $2.45 spend), PLUS negate **converting terms above the product's target ACOS**
  (US tee 30%, everything else its break-even per market). Applies on next pull.
- **Preemptive wrong-format negatives** — `preempt_negatives.py` (campaign-level,
  can't-fulfill terms). NOT yet run; fires tonight or via manual loop.
- **Corrected break-even** — `derive_econ` re-runs per market tonight to pick up the
  capped tee prices (current `market_econ` still has the old export-median break-even).
- **DE/FR/ES/IT scavenger** — first build happens tonight (only US + UK built manually).
- **DE/FR/ES/IT data pull, daily_metrics, dashboard, demand_feed, Discord digest** —
  first run tonight (UK + US already have data).

## Apply pending negatives now (optional, instead of waiting for 10am)
```
# preemptive format negatives — all markets (campaigns already exist):
for M in US UK DE FR ES IT; do ADS_MARKET=$M python3 preempt_negatives.py --apply --auto; done
# reactive negatives on markets that already have data (US, UK):
for M in US UK; do ADS_MARKET=$M python3 phase2_apply.py --apply --auto; done
```
