# Build Brief for Claude Code — Merch Ads Mac App (SwiftUI)

You are building a **native macOS SwiftUI app** that sits on top of an existing, working
Python ads-automation engine in this folder. Read this whole brief, then
`docs/swiftui-app-plan.md` (the product spec). Everything below is ground truth.

For where the product actually stands — which features earn their place, what was
found broken and fixed, and which design questions are already settled — see
**`docs/review-2026-08-04.md`** (full review + remediation record).

## Golden rules
- **Approach A (do NOT rewrite the engine in Swift).** Swift is the UI. The Python here is
  the brain. Reuse it.
- **Never read or print `.env`.** It holds secrets (Amazon client secret, refresh token,
  Discord tokens). Python reads it; Swift never touches it.
- **Reads:** Swift opens the SQLite DBs **read-only** (`file:...?mode=ro`) — the nightly
  launchd job writes to them; don't fight it for locks.
- **Writes/live/actions:** never write the DB or call Amazon from Swift. Shell out to
  `appctl.py`, which goes through the engine's safety rails (`KILL` file, preview,
  `writes_log`, local-state write-back).
- **Verify as you go:** you can run `appctl.py` commands and `xcodebuild` locally. Build,
  run, fix compiler errors in a loop. That local loop is why we chose you for this.

## What already exists (done, tested)
- `appctl.py` — the JSON API the app calls (see contract below). Read endpoints tested.
- `status.py` — live per-ASIN status from the Amazon API (market-aware).
- `reset_inflated_bids.py`, `phase2_apply.py`, `phase3_bids.py`, `scavenger*.py`,
  `lottery*.py`, `daily_metrics.py`, `ads_client.py`, `db.py`, `markets.py`, `products.py`.
- `docs/swiftui-app-plan.md` — the feature spec (v1 = viewer + actions).
- Nightly job: `run_scheduled.sh` via `io.github.zdufs.merchads.plist` (launchd), 10:00.

## Markets & databases
- Markets: **US, UK, DE, FR, ES, IT**. US is the default/original.
- Per-market SQLite: US = `ads_data.sqlite`; others = `ads_data_<CODE>.sqlite`
  (e.g. `ads_data_DE.sqlite`). All in this folder.
- **Perf tables.** `campaign_perf`, `targeting_perf` and `search_term_perf` hold
  CUMULATIVE trailing-30 snapshots, one row per pull date — never sum them, and never
  date one from another's `MAX(date)` (see the standing rule in CLAUDE.md).
  `daily_totals` and `campaign_daily` hold true per-day account and campaign totals.
  **`campaign_daily` is banked nightly by `daily_metrics.py`** — its daily report is
  already grouped by campaign, so the per-campaign rows cost no extra report — and
  trued up ~90 days each Monday by `backfill_daily.py`. Before the nightly banking
  (added 2026-08-09) it refreshed ONLY on Mondays, so campaign rolling-window rules
  (`FOR EACH campaign IN LAST n DAYS`) passed their freshness gate right after the
  Monday run and then went silent for the rest of the week.
  **`target_daily` (added 2026-08-06) holds true per-day, per-target rows** — banked
  nightly by phase 0 as a fifth report (`targeting_daily`) and backfilled by
  `backfill_target_daily.py`. It is what makes rolling `IN LAST N DAYS` windows
  possible; before it existed there was no per-entity daily data at all.
- The engine convention is per-market via the `ADS_MARKET` env var (default US). The app
  must set it when calling `appctl.py`, e.g. `ADS_MARKET=DE python3 engine/appctl.py metrics`.

## The product catalog — economics comes from a SET of files (2026-08-16)

MerchFlow is gone. **Snap for MOD** is the only extension exporting the product
grid, and it exports at most **100k rows per file** while the account has ~1.3M
live listings. So the catalog is no longer one file. It is every product export
in the POD folder, merged.

- `engine/export_reader.py` is the ONLY place that knows this. `catalog_files()`
  lists the exports newest first; `catalog_rows(folder, marketplace=…)` yields
  the merge with each (marketplace, ASIN) served **once, from the newest file
  that carries it**. That is what makes an incremental refresh work: export the
  part that changed, drop it in the POD folder, and those rows take over.
- Every row gains `_source` and `_as_of`, because a chunked catalog has no one
  as-of date. `map_products.py` writes `outputs/catalog_coverage[_M].json` from
  those, and `products.catalog_status()` reads it back into the `econ-gate`
  reply, so prices aging quietly in an old chunk are visible instead of silent.
- **Freshness still gates on the NEWEST file** (`products.MAX_EXPORT_AGE_DAYS`,
  21 days), exactly as before. `prices_older_than_gate` in the coverage file is
  what says how much of the catalog the newest chunk did not refresh.
- **Never prune Snap files automatically.** Each is a chunk, so deleting the
  older ones deletes coverage. `run_scheduled.sh` prunes only superseded
  MerchFlow exports, and `_adopt_export` does the same.
- Readers all go through the catalog now: `map_products.py`, `export_snapshot.py`
  (banks EVERY unbanked file, not just the newest), `derive_econ.py`, `traz.py`,
  `demand_feed.py`, `export_paused_asins.py`, `lottery_build.py`,
  `scavenger_build.py`.

**What Snap carries, verified 2026-08-15** against the 2026-08-04 MerchFlow
export over 82,963 shared listings: `Sales` and `Royalties` are **all-time**
totals (99.6% exact match on `salesTotal` and `royaltyTotal`; the rest had sold
again in between). `Price` is the current list price — 5% of listings differed
from the eleven-day-old MerchFlow figure, which is exactly the reprice the
engine needs to see.

**What Snap does NOT carry: `salesLast30` / `royaltyLast30`.** Those stay EMPTY
on a Snap row rather than borrowing the lifetime number. The royalty-per-unit
rate that `cmd_profit` needs now comes from `traz.royalty_per_unit()`, which
sums the dated Merch SALES_REPORT over the last 30 days. That report is per-day
and covers every channel, so its rate is the truer one. `demand_feed`'s proven
sellers fall back to all-time royalty and say so in `royalty_basis`.

## Python bridge (how Swift calls the engine)
- Invoke: `MERCHADS_DATA_DIR=<data> ADS_MARKET=<code> <python3> <engine>/appctl.py <cmd> [args…]`,
  `cwd` = the DATA folder.
- **`<engine>` and `<data>` are two different folders now (2026-08-21).** The app ships
  the modules at `Contents/Resources/engine` and a relocatable CPython 3.12 with
  `requests` at `Contents/Resources/python`, so a bare Mac with no Homebrew, no pip
  and no checkout runs it. The databases, `.env` and `outputs/` stay in the operator's
  folder, and `MERCHADS_DATA_DIR` is what says where. `MERCHADS_POD_DIR` names the
  catalogue folder (Snap exports, dated SALES_REPORTs), defaulting to the parent.
- **`engine/paths.py` is the only place that reads them, and it fails closed.** A
  variable set to a folder that is not there stops the process with a sentence saying
  so. Unset, both fall back to the old `__file__` derivation, so a checkout, the test
  suite and the nightly behave exactly as before. Do not re-derive a data path from
  `__file__` anywhere else: inside the bundle that lands on `Contents/Resources`,
  which is real, readable and holds no databases — the reply was a cheerful
  `{"ok": true, "empty": true}` for every market, indistinguishable from a fresh
  account. Guarded by `tests/paths_data_dir_tests.py`.
- Interpreter order: the Settings override, then the bundled one, then the login shell.
  The bundle carries its own OpenSSL and SQLite 3.53 — the app's own process links
  Apple's 3.51, which cannot read a sidecar-less WAL database read-only.
- Output: exactly one JSON object on stdout: `{"ok": true, "data": …}` or
  `{"ok": false, "error": "…"}`. Decode with Codable. Treat non-zero exit or `ok:false`
  as an error to surface in the UI. **Startup and argument failures are in the envelope
  too**: a bad flag, an unknown `ADS_MARKET`, and a missing data folder used to print
  plain text to stderr and exit 1 or 2, which reached the operator as an exit code with
  the reason cut off.
- The nightly ships in the bundle as well (`Contents/Resources/run_scheduled.sh`).
  `bash scripts/install_launchd.sh --app [--data DIR]` points launchd at it, and the
  scheduled run stops needing a checkout too. Guarded by `tests/nightly_paths_tests.py`.

## appctl.py contract (current)
READ (fast, DB-only, safe anytime):
- `markets` → `{markets:[{code,currency,region,is_default,has_data,kind,label}], current}`
  **`has_data` means the database HOLDS pulled rows**, not that a file exists there
  (`db.has_data`, one row in any of campaigns / ad_groups / the perf tables /
  daily_totals / campaign_daily). It used to be `os.path.exists`, which said yes to
  three files that hold nothing: a schema-only database, 4 KB of random bytes, and a
  DIRECTORY wearing the name. The schema-only case was self-inflicted — a read on a
  fresh folder created the file — so opening the Dashboard once made a never-pulled
  account read as populated, and the app gates its profile picker on this field.
  **A READ never creates a market database now.** `db.connect(ro=True)` answers from
  an empty in-memory copy of the schema when the file is absent, so the same empty
  reply comes back and nothing lands on disk. Only a pull creates the file.
  Guarded by `tests/read_side_effects_tests.py`.
- `metrics` → `{market,currency, trailing30:{spend,sales,orders,clicks,acos,cvr,as_of},
  daily:{window,spend,sales,orders,acos,settling}, mtd:{…}, trend:[{date,spend,sales,acos}],
  movers:[{campaign,delta}]}`  — **use `trailing30` as the headline ACOS** (stable);
  `daily.settling==true` means under-attributed, show muted / labelled.
- `campaigns [--type standard|lottery|scavenger|harvested] [--state ENABLED|PAUSED]`
  → `{count, campaigns:[{campaign_id,name,type,state,budget,bidding,spend,sales,orders,
  clicks,acos,cvr}]}`
- `adgroups --campaign <id>` → `{ad_groups:[{ad_group_id,name,state,default_bid,asin,type,
  lifetime_sales,spend,sales,orders,clicks,acos,cvr}]}`
- `targets --adgroup <id>` → `{as_of, targets:[{target_id,targeting,match_type,impressions,
  clicks,spend,sales,orders,acos,cvr,last_bid,bid_changes}]}`  (`last_bid` parsed
  from the newest writes_log bid_change)
- `alltargets [--limit N=2000]` → account-wide flat target list (the app's Targets tab):
  `{as_of, count, returned, truncated, targets:[{target_id,targeting,match_type,campaign_id,campaign,
  ad_group_id,ad_group,asin,bid,bid_inherited,impressions,clicks,spend,sales,orders,acos,
  cvr}]}`. **`count` is the TRUE total and `returned` is what this reply carries** — the
  same split the accumulated-* commands make. `count` used to be computed after the cap,
  so it was always the cap: the tab said "top 2000 by spend" and nothing could say whether
  2,001 or 50,822 targets sat behind it. The true total is one grouped COUNT(*) over the
  same snapshot, measured at 0.08s on US.
  **`bid` is the entity's OWN bid** from the `targets` mirror table that phase0
  banks nightly from /sp/targets/list + /sp/keywords/list (~424k rows, ~9 min US) —
  before that mirror existed, "bid" anywhere in the engine silently meant the ad-group
  default. `bid_inherited:true` = no own bid, the ad-group default rules the auction.
  The DSL's `bid`/`state` fields read the same mirror (fallback: ad-group values).
- `searchterms --adgroup <id> [--limit N]` → `{as_of, search_terms:[{search_term,targeting,
  match_type,impressions,clicks,spend,sales,orders,acos,cvr}]}`  (spend-sorted, default 200)
- `asin <ASIN>` → `{asin,product_type,lifetime_sales,ad_groups:[{ad_group_id,ad_group,
  state_cached,bid,campaign_id,campaign,type,spend,sales,orders,clicks,acos,cvr}]}`
  (`state_cached` = last pull; call `status` for live state)
- `bidhistory --target <id>` → `{changes:[{at,old,new,reason}]}`  (timeline for one target)
- `daily [--days N=30]` → `{market,currency,days:[{date,stored,spend,sales,orders,…}]}` —
  banked per-day account totals (daily_totals), the dashboard's trend source.
- `history --campaign|--adgroup|--target <id>` → `{entity,id,basis,days_banked,first,last,
  points:[{date,spend,sales,…}]}` — the banked series for ONE entity (the drill-down
  charts). **`basis` says what a point MEANS and the app must show it.**
  `"daily"` = one real day, summed from `target_daily` (or `campaign_daily`).
  `"trailing30_snapshot"` = one trailing-30 aggregate per pull date, so consecutive
  points overlap by 29 days and the line shows drift, not days. The two are
  indistinguishable on a chart, so labelling one as the other misleads whoever is
  making a money decision from it. `days_banked` is how many distinct dates the
  points cover and `first`/`last` are the range they span (both `null` when there
  are no points) — a market mid-backfill returns a short series honestly, and the
  app says "43 days banked" rather than drawing a quiet month.
- `negatives --adgroup <id>` → `{count, negatives:[…]}` — negative keywords on one ad group.
- `report --start --end` → custom-timeframe rollup from daily_totals:
  `{available:{min,max}, day_count, totals:{spend,sales,orders,acos,roas,ctr,cpc,cvr,cpo}, days}`.
  **App: Reports.**
- `campaigndaily --campaigns a,b,c` → per-day series summed over the listed campaigns
  (campaign_daily). **App: the Targets screen's scoped trend chart.**
- `synccal` → the Days-stored heat grid: `{days:[{date,stored,spend,orders,adjusted}], totals}`.
- `crosspurchase` → what ad-attributed buyers ALSO bought, from the spPurchasedProduct
  report (own-SKU vs other-SKU split). `supported:false` until the nightly banks the
  first purchased snapshot. **App: Cross-purchase.**
- `sales-history` → what organic (Merch SALES_REPORT) history is banked per day, with
  gaps: `{banked, coverage:{days,first_day,last_day,rows,asins,gaps}, imports}`. **App: Import.**
- `accumulated-asins` / `accumulated-keywords [--limit N=500] [--expand X]` → cross-campaign
  rollups (one row per ASIN / per keyword text summed over every appearance); `--expand`
  returns the per-campaign breakdown for one row. **App: Accumulated ASINs / Keywords.**
  `{as_of, count, returned, truncated, rows}` — **`count` is the TRUE total and
  `returned` is what this response carries**; they used to be conflated, so a 500-row
  reply advertised `count: 31814` and silently dropped 680 ASINs that had actually spent
  money. **`--limit 0` means every row** (US: 31,814 ASINs ≈ 6 MB, ~230 ms — the query
  dominates, so the cap bought nothing). The app asks for 0 and one-shots the call,
  because the serve worker reads its reply line by line.
- `watchlist` (stdin `{pins:[…]}`) → pinned campaigns/ad groups/targets/ASINs resolved to
  current metrics + a combined summary. Pins live app-side. **App: Watchlist.**
- `harvest-prune` → the wasteful Harvested-Exact keyword pause plan (read-only preview;
  `harvest-prune-apply` executes an approved subset via stdin). The apply reply is
  `{market, requested, paused, failed, unconfirmed}`. **`paused` alone cannot be
  read**: it counts only what Amazon CONFIRMED, so a batch it refused entirely and a
  plan with nothing in it both answer 0 — and the Harvest screen printed that as a
  green "Paused 0 keywords." over 40 rejected keywords (found 2026-08-24). `failed`
  is what Amazon NAMED as rejected; anything it neither accepted nor named — a
  transport failure, a multi-status with more errors than it could index — is
  `unconfirmed`, because "Amazon refused these" is a claim a dropped connection
  does not support. The app shows any shortfall in the error bar, never in the
  success colour.
- `killlist` → `{cvr_floor, as_of, evaluated, count, designs:[{asin,ad_group_id,type,state,
  clicks,orders,cvr,
  spend,sales,acos,break_even}], skipped:{transition,unknown_price,cohort}}`  (CVR < 8%
  AND ACOS over the DESIGN'S OWN break-even — US tees are priced per design from the
  export list price, model products.US_TEE_ROYALTY_CENTS; designs in a 30-day price
  transition, with unsupported prices, or in multi-ASIN cohort groups are EXCLUDED
  and counted in `skipped`. `econ:"unavailable…"` when the DB predates migration.)
  **`as_of` is the targeting snapshot the verdict was read from, and `evaluated` is
  how many ad groups the thresholds were applied to.** Without them a market with no
  data answered exactly like a healthy market with nothing worth killing: `count: 0`
  and an empty list. The two differed only in the `skipped` counters, which are
  non-zero today by luck. `as_of: null` plus a `note` is the no-data reply, and it
  must never be drawn as "no design is below the CVR floor" — that sentence is a
  claim about every design.
  **Rank-push prices act on the $19.99 economics** — see below.

### Rank-push pricing (operator decision, 2026-08-16)
Some tees are priced BELOW $19.99 on purpose, to buy sales velocity, a better BSR
and a shot at page one. A $14.99 tee earns $1.28, so its own break-even ACOS is
8.5%, and every automatic rule would pause exactly the campaigns the price cut was
meant to feed.

So a tee priced under `products.US_TEE_GROWTH_FLOOR_CENTS` ($19.99) is ACTED ON
with the $19.99 economics: `break_even` 26.4%, `target_acos` 26.4%, and a
`neg_threshold` of half the $19.99 royalty rather than half of $1.28. Everything
at or above the floor is untouched.

**What it earns stays TRUE.** `royalty` is still $1.28, so profit, cross-purchase
and the period royalty cache do not lie. `true_break_even` is always the honest
arithmetic (8.5%), and `growth_priced: true` marks the design so a screen can say
which number it is showing. Guarded by `tests/econ_tests.py`.
- `royalties` → every royalty the engine prices with and where each number came
  from: `{market,currency,editable,basis,growth_floor,model_version,errors,
  overrides, tee_prices:[{price_cents,price,royalty_cents,royalty,break_even,source,
  extrapolated,growth_priced,note,updated_at}],
  product_types:[{product_type,label,royalty,price,break_even,model,neg_threshold,
  pause_threshold,ad_groups,source,listings?,note,updated_at}]}`.
  **Every market is editable.** An untouched row comes from the built-in tables
  in `products.py` — `PRODUCT_ECON` + `PRODUCT_PRICE` for US, and
  `MARKET_PRODUCT_ECON` for the rest, all operator-confirmed 2026-08-21. A type
  not in those falls through to what `derive_econ.py` worked out of the export
  into `market_econ` (`source: "derived"`). The operator's `royalty_overrides.json` is merged over
  BOTH and always wins — Amazon fixes a maximum price per product per market, so
  the figure read off the Merch dashboard is definitive while a derived median
  only reflects whatever mix of listings exists. `tee_prices` is US-only: a US
  tee earns a different royalty at each rung, everywhere else a tee earns one
  royalty. `ad_groups` is how many ad groups advertise that type in this market —
  which royalties actually move money. **App: Product Royalty.**
  **`basis` COUNTS the rows it describes.** It used to be one string per market —
  US said "built-in table" and every other market said "worked out from your product
  export" — so DE's caption contradicted every badge on its own screen: 13 of its 14
  rows are the shipped table and exactly one is derived. Provenance is the whole
  purpose of that screen, and the wrong sentence sends the operator off to re-export a
  catalogue that cannot change 13 of the numbers. The app renders `basis` rather than
  a sentence of its own, so the two can never drift again.
- `royalty-set [--type X] --price P --royalty R [--note N]` / `royalty-clear
  [--type X | --price P]` → write or drop one override, scoped to `ADS_MARKET`.
  `--price` without `--type` edits the US tee ladder and is refused elsewhere.
  Local config only, no Amazon. Break-even is COMPUTED from royalty ÷ price and never typed. A value
  that cannot be a real royalty (≤ 0, at or above the price, absurd price,
  non-numeric) is REFUSED and nothing is written. **Only the LADDER form is
  refused outside US, never the market.** `--type X --price P --royalty R` works
  in every market and is how DE, FR, ES and IT get an operator number over
  `derive_econ.py`'s export median. This line used to say a non-US edit was
  refused outright, seventeen lines below the sentence saying every market is
  editable; a reader who believed it would hand-edit `products.py`, which the
  Product Royalty tab exists to stop. Overrides are stored per market under
  `markets.<CODE>`, so one market's edit never reaches another.

  **`engine/royalty_config.py` is the only thing that knows about the overlay.**
  The built-in tables stay the floor and keep their self-assert: an override can
  replace a value or add a new price point / product type, never delete a
  shipped one. Pricing a type also makes `products.type_from_export_label`
  resolve it, which is how an unmapped Snap label ("Basketball Jersey") stops
  being unknown — the operator declaring it is what makes it real. A file that is corrupt anyway drops its bad rows and those
  reasons appear in `econ-gate`, so every economics-driven write refuses rather
  than quietly pricing off the defaults. The overlay re-reads on mtime, so the
  long-running `serve` worker picks up an edit without a restart.
- `econ-gate` → `{ok, reasons, market, model_version}` — the US economics freshness
  gate (fresh mapped export ≤21d, no STALE marker). Closed gate = every
  economics-driven write (negatives-apply, run non-pull, promote,
  harvest-prune-apply, resetbids --apply, nightly auto-apply) refuses.
- `health` → `{kill_active, approval_required, last_run, markets:[{market,configured,
  has_data,latest_data,tables,stale_tables,target_daily,last_pull,last_write,campaigns,
  reports_pending,last_note?}]}`  (opens every market DB itself — call without
  ADS_MARKET). **One unreadable market file names itself and nothing else.** The
  per-market open was outside the try that catches it, so a corrupt database made
  every row read "file is not a database" and named none of them. That market now
  carries its own `error` and the other six answer normally.
  **It answers with no `.env` on disk.** `load_env` raises a string
  SystemExit for a missing credentials file, which is a BaseException, so the
  `except Exception` around it never caught it and `health` and `alerts` — the two
  screens an operator opens first on a standalone install — refused to answer at
  all. `configured` is now false on every row when there is no `.env`, which is what
  that column exists to say; it used to claim every market was configured in exactly
  that case. **`latest_data` is the WORST of the three perf tables** (each is
  filled by its own report job and they drift independently — campaign_perf
  alone stayed green through both freezes); `tables` has the per-table dates and
  `stale_tables` names any past the write-freeze threshold (>3d, same as
  db.snapshot_gate). `last_run` mirrors `outputs/last_run_status.json`, written
  by run_scheduled.sh's step tracker: `{started,finished,ok,markets,
  failures:[{market,step,exit}], gated:[{market,reason}], steps, total_step_seconds}`
  — with Discord digests off this is how a
  crashed nightly phase reaches the operator (System Health shows it; the run
  also posts a macOS "RUN FAILED" notification). `null` until the first
  instrumented run.
  **`gated` is the market that ran its reads and applied NOTHING.** A closed
  economics gate skips every auto-apply stage for that market — negatives,
  pauses, both harvest promoters, harvest prune, bids, both builders, seasonal
  and the DSL rules. Nothing crashes, so it is deliberately NOT a failure and
  `ok` stays true; before it was carried here the only place that said a whole
  market did no automation all night was a line in a 4 MB log file. The
  notification names it too, and the run's title reads "digest (gated)". `target_daily` is that market's per-day coverage —
  `{days,first,last}`, or `null` when none is banked. **App: System Health's
  "Per-day history" column.** Rules with a rolling window refuse to write when
  their window has holes, so this column is where the operator finds out why a
  rule went quiet. A market with fewer days than US is not an error: the EU
  markets only began advertising 2026-06-24.
  `bid_ceiling` is that market's write ceilings, `{target,keyword,budget}`, with
  **null meaning NO CEILING on that surface — not "unknown"**. Every surface is
  sent explicitly, because a dropped key and an unset ceiling would decode to the
  same nil in the app. **App: System Health's "Bid ceiling" column**, which shows
  an uncapped market in amber with a warning triangle. The ceiling is edited in
  Settings, which shows ONE market — whichever the profile picker is on — so
  until this column landed a market with no cap was invisible unless you loaded
  all seven. US was capped and the five EU markets were not, for months, while
  the same six auto bid-writing rules ran nightly in all six (found and fixed
  2026-08-21). The daily-budget surface is reported but never raises the warning
  on its own: automation writes bids nightly and budgets almost never.
- `bidreport [--days N=7]` → `{ups,downs,net_delta,count, changes:[{at,target_id,old,new,
  delta,reason,ad_group_id,targeting,asin}]}`  (the weekly what-moved report)
- `harvest` → `{count,pending, winners:[{search_term,kind,type,clicks,orders,sales,acos,
  cpc,first_seen,last_seen,promoted}]}`  (promotion = `run --phase promote|promote-asins`)
- `stale` → `{min_impressions,count, designs:[{ad_group_id,name,asin,type,impressions,
  clicks,spend}]}`  (ENABLED, 0 lifetime sales, ≥1000 impressions, ≤2 clicks; top 500)
- `halo [--min-spend X=1.0] [--limit N=300]` → organic-halo estimate for EVERY advertised
  design (US-only; `supported:false` elsewhere, and `supported:false` + `reason` when
  there is nothing to measure — no per-day ad history, no sales report, or no overlap
  between them. Those are STATES, not faults; every sibling read answers a folder with
  no data with an empty shape, and this one used to answer `ok:false`):
  `{market,report_start,report_end,supported,note,count,returned,truncated,min_spend,
  designs:[{asin,name,title,campaign_types,ad_start,ad_spend,ad_clicks,total_royalty,
  net_units,pre_days,post_days,pre_royalty,post_royalty,base_rate,post_rate,halo_est,
  net_halo,traz_window,flags}]}`. Windows the dated Merch `SALES_REPORT-*.csv` (total
  royalty, all channels — richer than the export's static `royaltyLast30`) to each
  design's ad-serving period and estimates the INCREMENTAL organic lift over that
  design's own pre-ad baseline: `base_rate`/`post_rate` are $/day royalty before/after
  `ad_start` (first impressions), `halo_est = (post_rate−base_rate)×post_days`,
  `traz_window` = post-window royalty − ad_spend. **`halo_est` is an UPPER BOUND —
  correlational, not causal**; `flags` marks `never-served (control)`,
  `no-ad-traffic (0 clicks)`, `peak-before-ad (baseline-confound)`.
  **The unit is the DESIGN, not the campaign.** It was scoped to a retired strategy whose
  campaigns held exactly one ASIN — a shape a 1000-ASIN lottery campaign cannot have. Ad facts
  are now summed per ASIN across every ad group advertising it, from `target_daily`
  (TRUE per-day rows; the old version read the newest CUMULATIVE `campaign_perf`
  snapshot and called a trailing-30 figure "total spend"). `campaign_types` says which
  kinds buy the design. **App: Organic Halo.** Backed by `halo.py`; also writes
  `outputs/halo.csv`. `analyze()` takes an optional `conn` — pass yours, or it opens the
  market DB and reports halo from data the caller never asked about (that is how the
  rules DSL under test read real data). The DSL's `halo_est` field calls it with
  `limit=0`.
- `sales-report [--import PATH]` → the dated Merch SALES_REPORT, which is the ONLY
  source of ORGANIC royalty (the Ads API reports ad-attributed sales only). Read:
  `{imported:false, report:{filename,folder,start,end,named_start,named_end,rows,
  us_rows,asins,age_days,stale}}` (`report:null` + `note` when the POD folder has
  none). `--import PATH` validates the CSV, names it after the period its ROWS
  cover (`SALES_REPORT-<M_D_YY>-<M_D_YY>.csv`, so the newest-report ranking always
  has a range to read) and copies it into the POD folder, returning
  `{imported:true, copied, file:{filename,start,end,rows}, is_newest, report}`.
  `is_newest:false` = an older report was saved and the engine still reads the
  other one. Feeds `halo` and `traz.py`. **App: the Import screen's
  sales-report bar** (picker or drag-drop). `traz.sales_report_path()`
  ranks on the dates parsed out of the filename — sorting the names as text put
  '1_5_27' before '4_15_26' and silently kept an older report (fixed 2026-08-04).
- `history-import [<csv>] [--year Y]` → bank a monthly account-history CSV exported from
  the Ads CONSOLE into `ads_history_monthly` (the only source past the API's ~95-day
  retention; feeds the `periods` back-extension; once banked it is the only copy). No
  csv = `{imported:false, coverage}`. Refuses files whose rows don't name a year unless
  `--year` says so. **App: the Import screen's drop zone** — a CSV that isn't a Merch
  sales report falls through to this importer automatically.
  **`coverage.months` is the UNION of every currency series, so it belongs to no
  market.** One console export covers every marketplace and its Country dimension comes
  back empty, so the finest split is Budget currency: US (USD), UK (GBP) and one EU
  (EUR) series shared by DE, FR, ES and IT. Each `by_market` row now carries its OWN
  `months`, `first_month` and `last_month`, because the account-wide range can say
  nothing about any one of them. The Import screen read the union and told DE "60 months
  banked, continuous" while DE's own table held nothing at all (found 2026-08-24) — so
  the one screen that would have prompted the missing import said the job was done.
  Guarded by `tests/history_import_tests.py`.
- `alerts` → `{alerts:[{kind,key,message,market,campaign_id?,ad_group_id?,asin?}]}`
  kinds: spend_spike / budget_max / kill_candidate / data_stale /
  portfolio_cap / seasonal_tags_lost / rules_lost / stream_undercount /
  stream_check_failed / aws_plan_expiry — the app
  notifies once per `key` (dedup app-side). **seasonal_tags_lost** fires when
  `seasonal.json` has no ASINs but something proves it used to: a
  `seasonal.backup.json` beside it that still holds tags, or ad groups this
  market seasonal-paused that can no longer be released. The file is global, so
  the backup half is reported from the DEFAULT market only and the stranded half
  per market. It exists because the map was deleted on 2026-08-15 and the
  scheduler ran as a silent no-op for six days; `seasonal_pause.load_config()`
  now restores from the backup before it ever falls back to the empty example.
  **rules_lost** fires when no rules load but `rule_defs.backup.json` still
  holds some — `rules/store` writes that backup on every save that leaves a rule
  standing, and restores `rule_defs/` from it when the directory goes missing.
  Without it a deleted `rule_defs/` read exactly like a fresh install: the
  nightly evaluated zero rules, reported success, and wrote nothing. The backup
  path is DERIVED from `RULES_DIR`, so a test that redirects the directory can
  never write over the operator's real one. `rule_defs/` is global, so this is
  reported from the default market only.
  **stream_check_failed** fires when `stream_verify.verify()` itself RAISED.
  That call used to be wrapped in a bare `except: return []`, so a renamed
  column or a schema change would have switched the one drop-detector off for
  good while the alerts feed stayed clean — which is exactly what it looks like
  when everything is fine. A market with no Stream data does not come through
  here (verify returns `comparable:false` with a reason, checked against UK, DE
  and USKDP), so an exception is a real fault. The key carries the exception
  TYPE, so a persistent fault alerts once rather than on every poll.
  **aws_plan_expiry** fires within `stream_config.AWS_PLAN_WARN_DAYS` (60) of
  `stream_config.AWS_PLAN_EXPIRY` (2027-02-21), and keeps speaking after the
  date rather than going quiet at the worst moment. The AWS account holding the
  Stream queues was opened on the FREE plan, which auto-closes six months in.
  The bill is about nothing either way, so this is paperwork — and it is the
  dangerous kind of deadline because of HOW it fails: the queues go, Stream
  stops arriving, and Amazon carries on reporting the subscription ACTIVE. Every
  screen keeps working and the day simply reads quieter, which is
  indistinguishable from a slow sales week. Reported from the DEFAULT market
  only (one AWS account serves every realm); set the constant to None to switch
  it off deliberately.
  **stream_undercount** fires when Stream delivered a whole day and it did not
  match the report. It is the one Stream failure that hides: an empty queue, a
  stale drain, a missing hour and an unresolved market all announce themselves,
  but a pipeline that is simply DROPPING part of what Amazon sends stays
  internally consistent all the way to the screen — the totals add up, the
  placements add up, the hours add up, and the number is quietly low. The key
  carries the day, so a bad day alerts once. Silent on every day
  `stream-verify` refuses to judge, because an alarm that fires on the first day
  of every subscription gets muted and then the real one is missed too.
  data_stale fires when a perf
  table's newest snapshot is 4+ days old — the SAME threshold at which
  db.snapshot_gate freezes writes, ONE threshold everywhere. Not earlier: EU
  markets sit at a structural 2-day Amazon lag, so 3 days behind is a normal
  pre-pull morning (a 3-day alarm false-fired daily once before). Its key
  carries the stuck date (one alert per incident) and "Review →" lands on
  System Health. Structured entity fields deep-link the app's "Review →":
  kill_candidate carries campaign_id/ad_group_id/asin; budget_max carries
  campaign_id; spend_spike is market-wide but the engine attributes it to the
  likely-driver campaign (biggest trailing-30 spend growth between the last two
  snapshots — campaign_perf is cumulative, so there is no exact per-day figure)
  and carries that campaign_id when found (else the app lands on the Dashboard).
- `demandfeed [--refresh]` → the outputs/demand_feed[_M].json contract (keyword_seeds +
  proven_sellers); --refresh reruns demand_feed.py first
- `profit` → royalty-aware true margin: `{total_spend,total_royalty_est,total_profit,
  types:[…], designs:[worst 250 + best 250 w/ royalty_roi],
  unattributed_cohort_{spend,orders,sales,groups}, coverage_pct, modeled_royalty_n}`
  (COVERED profit only: per-ASIN period royalty (royaltyLast30/salesLast30 cache from
  map_products) where recent sales exist, else current modeled royalty (disclosed);
  multi-ASIN cohort spend reported separately, never presented as profit)
- `overview` → all-markets trailing-30 rollup + ytd_spend/ytd_sales/ytd_supplemented/
  ytd_basis (call without ADS_MARKET)
- **One year-to-date, one place.** `periods`, `monthly`, `metrics` and `overview` all
  read `appctl._ytd_totals`. They used to compute it three ways, and two of them
  omitted the imported console months — on 2026-08-06 the US dashboard and All
  Markets were 2.3x apart for the same year. YTD = banked daily
  history since Jan 1, plus the imported months that sit BEFORE the first banked day
  (never the overlap, so nothing is double-counted). `supplemented:false` means the
  market's history could not be extended: only US and UK can be, because one console
  export covers every marketplace and carries no country, so DE/FR/ES/IT share a
  single merged EUR series. `partial:true` means the year starts later than January.
  Guarded by `tests/ytd_definition_tests.py`, which also fails if a second YTD query
  ever appears in appctl.py.
- `monthly` → calendar months + YTD from daily_totals (true per-day history):
  `{months:[{month,spend,sales,orders,acos,days_banked}], ytd, coverage}`. metrics also
  carries `month`/`ytd`/`coverage` now. History comes from backfill_daily.py (~92 days,
  the API's reach — chunked ≤31-day DAILY reports); run_scheduled re-runs it Mondays to
  true-up 30-day attribution; daily_metrics.py banks each new day.
- `periods` → the dashboard's period stack (current month · previous month · YTD ·
  previous year · all time): `{market,currency,coverage:{first_day,last_day},
  retention_note, periods:[{key,label,available,window,requested_window,partial,
  partial_reason,days_banked,spend,sales,orders,acos,profit,royalty_est,
  royalty_per_order,covered_spend,uncovered_spend,basis,modeled}]}`.
  **spend/sales/orders/acos are EXACT** and every period reads the SAME banked
  daily history, so the rows are directly comparable — do not mix in
  `period_totals`/`metrics.mtd` (Amazon's MTD report attributes slightly
  differently, so the current-month row would disagree with the rows under it).
  **`profit` is MODELED**: royalty is per DESIGN and no per-design daily table
  exists, so each type's trailing-30 royalty per order (from `_profit_core`, shared
  with `profit`) is weighted by the campaign-type mix `campaign_daily` saw in that
  window; windows older than campaign_daily's reach fall back to the blended rate
  (`basis` says which). Accuracy DEGRADES going back — today's rates are applied to
  older orders and US tee prices changed 2026-07-12. Never present as exact.
  A period the data cannot cover returns `available:false` + `reason`, never
  zeroes; `partial:true` means the window starts later than asked. **Amazon's
  reporting retention starts ~95 days back and rolls forward** (confirmed by the
  API: "startDate must be equal to or after report type data retention start
  date"), so anything older than the first banked day is gone for good — YTD is
  partial until a full year is banked, and `previous_year` stays empty until 2027.
- `backfill-daily [--days N=92]` → wraps backfill_daily.py for the app's backfill button
- `digest --since ISO` → writes_log counts per action since a timestamp (post-run digest)
- `approval-mode [--on|--off]` → the REQUIRE_APPROVAL file: run_scheduled.sh then runs
  phase2 in PREVIEW mode (collect only) — the app's Approval Queue becomes the real gate;
  state also surfaces in `health` as approval_required
- `negate --campaign X --adgroup Y --term T` → one negative-exact keyword (permanent)
- `livestate ASIN` → structured live states per ad group (heals the local mirror)
- `promote` → promote harvest winners; stdin `{"terms":[…]}` scopes to approved ones
  (phase4/phase4b accept `--terms-file`); empty stdin = all pending.
  Each phase answers `{code,text,reported,phase,requested,created,negatives_requested,
  negatives_landed,negatives_refused,negatives_unconfirmed,promoted,aborted}`.
  **The exit code alone could never say a promotion went through**: Amazon refuses
  individual writes inside a batch and still answers 200 for the batch, and both
  phase4 scripts ended with a bare `apply()` and no `sys.exit`, so the process
  exited 0 whatever Amazon said. A run where every SOURCE NEGATIVE was refused
  reached the app as a green "keywords exit 0" — with each of those terms still
  serving in the ad group it was meant to leave, competing with the replacement
  that had just gone live. The scripts print their counts on a last
  `PROMOTE_RESULT` line and exit non-zero when a source negative was refused or
  when no campaign could be created; `_promote_summary` reads that line back.
  A phase that printed no line is `reported:false` — UNVERIFIED, never clean.
  `negatives_unconfirmed` is phase4b's third state: the ASIN-negative endpoint
  answers a body the engine parses nowhere else, so a 2xx we cannot read is
  counted and named rather than called a failure.
- `targets --adgroup ID --live` → adds live_bid/live_state per target from Amazon
- `serve` → long-running line protocol (JSON argv in, envelope out) — the app keeps one
  per market for fast reads (~5ms vs ~50ms per call); writes/live/long jobs still one-shot
- `seasons` → seasonal scheduler config + status: `{market,today, seasons:[{key,label,
  resume,pause,active,next_transition,tagged_count}], tags:[{asin,season,label,active,
  product_type,ad_groups,enabled,paused}]}`. Config = global `seasonal.json` (recurring
  MM-DD windows + ASIN→season map); resume leads the sales date ~2-3 wk so ads ramp.
- `season-tag --asin X --season Y | --clear` → tag/untag a design (local config write)
- `season-define --name --label --resume --pause` → add/update a season window (MM-DD)
- `season-suggest [--apply]` → auto-detect seasonal designs by title keyword (word-boundary
  match on ad-group names; underscores treated as spaces): `{market, suggestions:[{asin,name,
  season,label,keyword,current_season,already_tagged}]}`. `--apply` tags the matches (stdin
  `{"asins":[…]}` scopes to an approved subset; empty = all). Keywords live per-season in
  seasonal.json; `resume` now leads the holiday by ~2 months.
- `season-tag-csv --csv PATH --season KEY [--apply]` → tag every ASIN in a curated CSV to one
  season (for lists the title scan misses). Reads an 'asin' column (any case) or scans cells
  for the ASIN pattern. Preview: `{season,label,csv,found,new,already,sample}`; `--apply` writes.
- `seasonal-preview` → read-only plan for this market: `{market, pause:[{ad_group_id,asin,
  season,label,name}], enable:[…]}` (what seasonal_pause would pause/enable right now)
- `everywhere-preview` (stdin `{kind,action,keys,match?}`) → resolve an "act everywhere"
  selection to the concrete instances it would touch, WITHOUT writing:
  `{market,kind,action,as_of,count,applicable,skipped_noop,campaigns,
  instances:[{…,skip}]}`. `kind` is `asin` or `keyword`; `action` is `pause`, `setbid`
  (keywords only) or `negate`; `match` is `exact` or `phrase` and applies to `negate`
  alone. **`count` is every instance and `applicable` is the subset that would actually
  change** — an ad group already PAUSED, or a keyword with no target id, is kept in the
  list with `skip:true` rather than dropped, so the operator sees why a selection of 40
  lands on 12. `as_of` comes from the accumulated snapshot the plan was resolved against.
  **App: the Accumulated ASINs / Keywords screens' act-everywhere sheet**
  (`Views/Actions/EverywhereActions.swift`).
- `harvest-suggest --term T [--limit N=50]` → whole-catalogue design suggestions for one
  cohort winner: `{term,count,suggestions:[{asin,title,product_type,matched_words,score,
  lifetime_sales}]}`. `score` is how many words of the term appear in the design's title,
  and the sort is score, then lifetime sales, then ASIN. Score 0 is never returned. It
  reads the catalogue rather than the campaigns, so it can propose a design that has never
  been advertised — which is the point. **App: the Harvest screen's Promote-group sheet**
  (`Views/Actions/PromoteGroupSheet.swift`).
- `catalog-cache [--rebuild]` → the banked product catalogue:
  `{available,matches,rows,built_at,files,note}`. The catalogue is not one file —
  Snap for MOD exports at most 100k rows and the account has ~1.3M live listings —
  so `export_reader.catalog_rows()` merges every chunk at read time, newest file
  winning per listing. That merge is 20 seconds over 2.0M listings and the nightly
  performs it about twenty times (derive_econ + map_products + demand_feed, per
  market), so roughly seven minutes a night went on re-parsing 1.1 GB that had not
  changed. `engine/catalog_cache.py` banks it into `catalog_cache.sqlite`, in the
  DATA folder beside the market databases — its own file, because the catalogue is
  global across seven marketplaces and filing it under `ads_data.sqlite` would make
  US own every other market's listings.
  **It is a PURE OPTIMISATION and that is what makes it safe.** The table carries a
  signature over the export files — name, mtime AND size; `read()` returns None
  whenever that does not match what is on disk, and every reader falls back to the CSVs. A
  cache that is stale, missing or corrupt therefore costs SECONDS and can never
  cost an answer — `matches:false` is a note, not a fault. An explicit `files=`
  list always reads those files, because a scoped request must not be answered
  from a cache of the whole catalogue. Built by `run_scheduled.sh` before the
  market loop and by `adopt-export` / `import-apply` when a new export lands;
  never lazily inside a read, which must not block for a minute.
  Guarded by `tests/catalog_cache_tests.py`. **App: nothing reads it yet.**
- `export-date` → the New Designs screen's "last recorded" date: the newest `createdDate`
  found INSIDE the current catalogue export, read from the rows and not from the filename.
  `{available,last_recorded,source,rows,cached}`, or `{available:false,note}` when the POD
  folder holds no export. The scan is ~18s over a 2M-row export, so the answer is cached in
  `outputs/export_meta.json` under the export's `filename|mtime` signature: a new export
  scans once, every later read is instant. It goes through `export_reader`, because
  `createdDate` is a MerchFlow column name and a raw reader over a Snap file matched no row
  at all — the scan counted 30,000 rows and still answered "no catalogue on file".
  **App: Import → New Designs** (`Views/Import/NewDesignsBuildView.swift`).
- `run-status` → the nightly run happening RIGHT NOW, parsed live out of
  `outputs/scheduled_runs.log`. `{active:false}` when nothing is running. This is the
  live half of `health.last_run`, which is written by the step tracker and therefore only
  lands when the run FINISHES — so between 10:00 and about 15:45 it is the only thing that
  can say a run is in progress. Backed by `run_status.py`. **App: the System Health run
  banner**, fetched through `Bridge/PythonBridge.swift`.
- `portfolio-cap [--set N | --clear]` → show, set or clear this market's monthly
  portfolio-spend cap (the R8 guard): `{market, portfolio_monthly_cap}` (a string, or null
  when unset). No flags = show. **Nothing enforces it as a hard stop** — the `alerts` feed
  raises `portfolio_cap` as month-to-date pooled spend approaches it, and that is the whole
  mechanism. Local config only, no Amazon call. CLI-only: no screen reads it today.
- `prune-snapshots [--days N] [--apply]` → count (or delete) perf-snapshot rows
  older than the retention window: `{market,cutoff,days,applied,tables:{…},total,note}`.
  **Preview by default**; `--apply` deletes. Local database only, no Amazon call.
  The three perf tables gain one row per entity per pull and nothing had ever
  removed one — US `targeting_perf` was 2.0M rows over 45 snapshot dates on
  2026-08-22, about 52,000 a night, and the seven databases came to 2.0 GB.
  `db.SNAPSHOT_RETENTION_DAYS` is **400 days**, chosen to be far past anything on
  disk: the deepest table spanned 67 days, so this deletes NOTHING today and caps
  the future instead. It leaves a year plus a month for a year-over-year look at
  the drift series, and no snapshot is the only copy of anything — Amazon's
  reporting retention is ~95 days and the TRUE per-day history lives in
  `target_daily` / `campaign_daily`. `date < cutoff`, so a row exactly at the
  edge is KEPT. Each table is counted separately, because the three are filled by
  independent report jobs. **Deleting does not shrink the file** — SQLite reuses
  the freed pages, which is what bounds growth, and no VACUUM runs against a
  database the app holds open. Wired into `run_scheduled.sh` on **Mondays**.
- `change-cap [--set N | --clear] [--set-build N | --clear-build]` → show, set or
  clear this market's VOLUME caps on one automatic run:
  `{market,auto_change_cap,default,capped,note,auto_build_cap,build_default,
  build_capped,build_note}`. No flags = show, `--set 0` turns it off, `--clear`
  restores the shipped default (`db.AUTO_CHANGE_CAP_DEFAULT`, 500). Local config
  only, no Amazon call.
  **There are TWO caps, because 500 was measured over one of them.** `--set` is
  the CHANGE cap: bids, budgets, states, negatives, archives, and creating a
  campaign — a campaign is the unit of daily spend and no legitimate night
  creates more than about fifty. `--set-build` is the BUILD cap
  (`db.AUTO_BUILD_CAP_DEFAULT`, 50,000): ad groups, product ads, keywords and
  targeting clauses created INSIDE a campaign by lottery_build and
  scavenger_build. Those two enumerate the catalogue rather than compare a
  metric to a threshold, so an ordinary night is 1,500 to 3,900 entities and
  the busiest day ever recorded is 27,319. Counting them against 500 stopped US
  scavenger_build at 475 of about 700 product ads on 2026-08-24, the first
  night the cap reached `ads_client`, and the nightly recorded
  `scavenger_build (exit 1)` with the account half-built. 50,000 is chosen from
  the builders' own structural caps — scavenger_build cannot exceed 6 series x
  6 campaigns x (1000 ASINs + 200 keywords) = 43,272 — so it never fires on a
  build those caps allow.
  **A write reaches the build budget only when BOTH halves say so**:
  `ads_client._BUILD_ENDPOINTS` lists the endpoints that populate a campaign —
  creating ad groups, product ads and keywords, plus `PUT /sp/targets`, which
  is `lottery_build.set_clause_bids` configuring the clauses Amazon
  auto-generates under each NEW ad group — and the process must have called
  `client.declare_campaign_builder()`.
  The endpoint alone is not enough — `phase4_harvest_create` and
  `phase4b_harvest_asins` create ad groups, product ads and keywords too, and
  they are threshold-driven, so they must keep the 500. Both halves fail SAFE:
  an endpoint nobody lists, and a script that never declares itself, get the
  stricter cap and stop loudly.
  **This is the only guard that COUNTS.** The KILL file, the econ gate, the snapshot
  gate, the conflict guard, the bid ceiling and the no-op check all ask whether ONE
  change is safe. None asks whether there are an absurd NUMBER of them, which is the
  shape a mistyped condition takes: `>= 1` where `>= 15` was meant matches tens of
  thousands of targets and every other gate waves it through. Six rules run on AUTO
  nightly across seven markets with nobody looking.
  **Past the cap a run applies NOTHING.** It used to apply the first N and set
  `truncated: true` — half an account acted on, no refusal, and a flag that reached no
  screen. The reply names the count, the cap, and the three ways out: fix the rule, run
  it in REVIEW mode and approve from the queue, or raise the cap.
  500 is measured, not guessed: counting only actions a rule can emit, the busiest day
  in any market's `writes_log` is US 2026-06-29 at 255, every EU market peaks at 26,
  and a normal night across the whole account is 4 to 49 writes.
  **`rules-approve` is exempt** (it passes `cap=0`): those ids were picked by hand in
  the Approval Queue, so the human gate has already happened. `tests/rules_volume_cap_tests.py`
  fails if any OTHER caller passes an explicit cap. CLI-only: no screen reads it today.

LIVE / ACTION (need Amazon API — run on the Mac):
- `everywhere-apply` (stdin, same shape as `everywhere-preview`, plus `"bid"` for setbid)
  → apply that selection across every instance: pause ad groups, pause target clauses, set
  a bid, or add exact/phrase negatives. `{market,kind,action,applied,skipped_noop,failed,
  count,results}`. **The plan is RE-RESOLVED here against fresh state**, so the app never
  sends back ids it read minutes ago. KILL-gated; every write is logged to `writes_log` and
  is individually undoable; bids are clamped to the market's ceiling; an instance that is
  already paused or already at that bid is skipped rather than written.
- `harvest-promote-group [--apply]` (stdin `{term,source_ad_group_id,source_campaign_id,
  asins}`) → promote one cohort winner onto a chosen family of designs. Dry run by default,
  returning `{plan, applied:false}`; `--apply` writes and returns `{plan, applied:true,
  result:{campaigns_created,ad_groups_created,product_ads_created,keywords_created,
  groups_with_keyword,negations,promoted}}`. Campaigns are reused by name and created only
  when missing, one per product type in the plan. `--apply` is KILL-gated AND econ-gated;
  the dry run opens the DB read-only so it never contends with the nightly writer.
  **App: the Harvest screen** (`Views/Actions/HarvestView.swift`); pairs with
  `harvest-suggest`, which picks the designs.
- `status <ASIN…>` → `{market,asins,text,stderr,code}` (wraps `status.py`; live state)
- `run [--phase phase2|phase3|harvest|pull]` → triggers a phase or the full market run.
  A full run answers `{ran,text,code,last_run,note}`. **The exit code used to be
  meaningless**: run_scheduled.sh ends with an `echo`, whose status is 0 however
  many steps failed, and everything the run prints goes into
  `outputs/scheduled_runs.log`, so the app showed an empty pane and a cheerful
  zero. The script exits 1 when any step failed now, echoes its closing line to
  stdout as well as the log, and `last_run` carries the status file THIS run
  wrote — a file left by an earlier run is refused, and `note` says the run is
  unverified, because yesterday's outcome reads exactly like today's.
- `seasonal-apply` → execute the seasonal pause/enable plan for this market (KILL-gated;
  logs `seasonal_pause`/`seasonal_enable`; re-enable touches only ad groups it paused).
  Runs nightly per-market in `run_scheduled.sh` (`seasonal_pause.py --apply --auto`) —
  no-op until designs are tagged. Pauses a tagged design's ENABLED ad groups out of its
  season; back in season, re-enables the ones it paused (never resurrects perf-paused designs).

Note numbers: `acos`/`cvr` are fractions (0.1816 = 18.16%). Money is in the market currency.

## Amazon Marketing Stream — hourly push instead of report polling (2026-08-21)

Stream is Amazon's PUSH channel: hourly Sponsored Products rows delivered into an
SQS queue we own, about an hour behind the hour they describe. No report job, no
25-minute poll window, no catch-up rounds.

**Access is already granted.** Verified live 2026-08-21 — `GET
/streams/subscriptions` answers `200 {"subscriptions":[]}` on US, UK, DE and
USKDP with the existing credentials. An account already integrated with the Ads
API needs no separate Stream application. The remaining gate is AWS, not Amazon.

**Stream can never replace `phase0_pull.py`.** A subscription starts the clock and
Stream sends nothing about the past. History, backfill and the Monday 30-day
true-up stay with the report pipeline. It also does not change the attribution
lag — the freshest day or two is under-attributed in Amazon's own numbers either
way.

- `engine/stream_config.py` — the ONLY place that knows realms, regions, dataset
  ids and Amazon's per-dataset publisher accounts (copied verbatim from Amazon's
  CloudFormation template). **Each dataset publishes from a DIFFERENT AWS
  account**, so a queue policy written for `sp-traffic` silently drops every
  `sp-conversion` message while the subscription still reads ACTIVE. That is why
  `stream-setup` GENERATES the policy rather than documenting it.
- `engine/stream_api.py` — subscription CRUD on the Ads API. Refuses a second
  subscription to the same dataset, because two mean every row arrives twice.
- `engine/aws_sigv4.py` — SigV4 in the standard library. No boto3: the app's
  bundled CPython carries only `requests` and the bundle's whole point is
  needing no pip. Pinned against AWS's published `get-vanilla` vector in
  `tests/stream_tests.py`.
- `engine/stream_sqs.py` — ReceiveMessage / DeleteMessageBatch / ConfirmSubscription.
- `engine/stream_drain.py` — the loop. **It also answers the SNS handshake.** A
  new subscription parks a `SubscriptionConfirmation` in the queue and sends
  nothing until its Token is confirmed; until then the subscription sits at
  PENDING and every screen looks healthy. Messages are deleted from SQS only
  after they are committed locally, so a crash costs a redelivery, never an hour
  of data Stream will not resend.
- `engine/stream_store.py` — `stream_data.sqlite`, its OWN database beside the
  market DBs. One queue serves a whole realm (all five EU markets share the EU
  queue), so which market a message belongs to is a payload field we have never
  seen. Arrival is therefore kept separate from interpretation: messages are
  banked whole, and the mapping into `target_daily` / `campaign_daily` is written
  later against real payloads. `stream-fields` counts the keys real messages
  carry — the first hour of data is the only chance to learn them, because
  Stream does not replay.

- **Dedupe differs per dataset.** `sp-traffic` rows are DELTAS — `impressions`
  is 1 or 2 and a correction arrives as -1 — so many rows share one
  hour/ad/keyword/placement on purpose. They are keyed on `idempotency_id`
  alone; a row without one is KEPT and reported in `unkeyed_messages`, because
  an overcount shows up the moment `stream-verify` compares a day while an
  undercount shows up nowhere. `sp-conversion` rows are RESTATED SNAPSHOTS
  (a 1d/7d/14d/30d ladder on every message, resent as attribution grows), so
  those are keyed on the row's natural grain with the newest winning — summing
  two restatements would invent sales.
- `engine/stream_map.py` — the OTHER half: interpretation. Banked messages turned
  into one market's day. A message finds its market through its CAMPAIGN ids,
  cached per `advertiser_id`. **Never through `marketplace_id`** — Merch US and
  KDP US both advertise on `ATVPDKIKX0DER` (confirmed 2026-08-21 against
  `/v2/profiles`: "Sponsored ads - KDP" reports that marketplace while live Merch
  US Stream rows carry it under a different entity), so the marketplace would
  merge two separate advertisers into one number. An advertiser nothing claims,
  or one that two markets claim, is REPORTED and never guessed.

READ: `stream-status`, `stream-setup [--queue-url U]`, `stream-fields`,
`stream-today [--day D]`, `stream-advertisers [--refresh]`,
`stream-verify [--day D]`.

- `stream-verify` → `{market,day,comparable,reason,stream,report,delta,campaigns,
  only_in_stream,verdict,coverage}`. **The only check that can prove Stream is not
  quietly dropping data.** Everything else proves the pipeline reads faithfully what
  ARRIVED; this measures one SETTLED day twice — once from Stream, once from
  `campaign_daily`, which the nightly banks from Amazon's own daily report — and
  compares them per campaign, so a systematic gap points at the campaigns behind it
  instead of at one unexplained total. `comparable:false` + `reason` for a day
  Stream could not have seen whole (missing or partial hours, fewer than 24 hours)
  or one the report has not banked yet; those days are EXPECTED to read low and
  calling them a discrepancy would teach the reader to ignore the check. Tolerance
  is 2% — two Amazon pipelines will not agree to the cent. Sales are reported and
  never gated on: conversions are dated to the click hour and restated for days, so
  the two sides settle at different speeds. With no `--day` it picks the newest day
  Stream holds whole. Backed by `engine/stream_verify.py`; the same comparison runs
  by itself as the `stream_undercount` alert.

- `stream-today` → `{market,supported,currency,day,is_today,account_offset,as_of,
  hours_delivered,latest_hour,coverage,totals,hours,placements,campaigns,
  campaign_count,campaigns_truncated,conversions,unresolved_advertisers}`. **App: the Dashboard's "Today so far"
  panel** — the only live section on a screen where everything else is a day old.
  Three refusals are structural, not cosmetic:
  **`totals` has no sales, orders, acos or cvr.** sp-traffic does not carry them.
  They come from sp-conversion and live under `conversions`, which reports on the
  **30-day** window — `stream_map.ATTRIBUTION`, the same one `phase0_pull` and
  `daily_metrics` read, so the two never disagree. `available:false` means
  nothing has ever arrived and `sales` is null, never 0.
  **A conversion is dated to the CLICK hour, not the purchase.** A message
  arriving tonight with a six-day-old window belongs to THAT day. `stream-today`
  keys on the ad day, which is also what makes it comparable to `campaign_daily`.
  Conversions land late and get restated, so a day's figure only ever grows.
  **`conversions.available` is about THIS DAY, not about the dataset.** It used to
  be true as soon as the market had ever received a conversion message, so every day
  with no conversion rows reported `sales: 0` — the exact shape the rule above
  forbids. `messages` still carries the account-wide count, so "cannot see sales yet"
  and "this day has none yet" stay separable, and each gets its own note.
  **`acos_withheld` is a sentence, not a null.** No ACOS is computed for a day in
  progress: the spend is complete and the sales are not, so the ratio is always
  alarming and always wrong. Do not "fix" this by dividing the two fields.
  **`coverage.backlog_pending` is the shortfall the hour counts cannot see.**
  Every other figure there is about what was BANKED, and messages still sitting
  in SQS were never banked. They belong to hours that already read as delivered,
  so the panel said `complete: true` on 2026-08-24 while the NA sp-traffic queue
  held 958 undrained messages and was growing — with System Health saying so two
  clicks away. It names the queues whose newest drain could not empty them, for
  THIS market's realm only (one queue serves a whole realm, so EU's backlog is
  not a US undercount), and `complete` is false while any of them is behind.
  Same source as System Health: `stream_store.drain_backlog()`.
  **`coverage` separates a hole from a switch-on.** `missing_hours` are hours we
  were listening for that never came: Stream does not resend, so each is
  permanent and the day's total is an undercount. `partial_hours` are hours that
  BEGAN before `listening_since` — they hold at most what Amazon's short
  catch-up included, and an hour that predates the subscription counts as
  partial even when it carried nothing, because nobody was listening and so
  nothing was dropped. Calling those "never arrived" accuses Amazon of losing
  data it was never asked to send; on the first live day that turned a
  switch-on into what read like an outage. The expected range stops at the
  newest hour DELIVERED, never at the clock hour — Stream runs about an hour
  behind, so expecting the current hour would false-alarm hourly.
  **`campaigns` is every campaign that served, ranked by IMPRESSIONS.** It
  used to be the twelve biggest SPENDERS with no count beside it, so on
  2026-08-21 the reply carried 12 of 51 campaigns holding 2,478 of 4,465
  impressions and nothing in the shape said the other 39 existed — a truncated
  list that reads exactly like a complete one. A US day is about fifty rows, so
  the cap bought nothing. The sort is the same argument `placements` already
  makes: early in a day almost nothing has spent, so ranking on cost puts a
  campaign that served 89 impressions above one that served 900. `campaign_count`
  is the TRUE total and `campaigns_truncated` says whether the list is all of
  them, so a cap a caller does ask for is reported rather than silent. Guarded by
  `tests/stream_map_tests.py::CampaignRollup` — the older add-up test could never
  have caught this, because its fixture had two campaigns against a cap of twelve.
  **The day is Amazon's.** `time_window_start` arrives as marketplace-local time
  with its offset attached, and the date is read straight out of the string.
  `placements` is the one dimension the report pipeline never carried, and it is
  sorted by IMPRESSIONS: early in a day almost nothing has spent, so a cost sort
  scrambles the list. `impression_share` is the share that is always meaningful.
LIVE / ACTION: `stream-subscribe --dataset sp-traffic|sp-conversion`,
`stream-unsubscribe --subscription ID` (KILL-gated, logged to `writes_log`),
`stream-drain [--seconds N] [--realm NA|EU]` (reads AWS, writes only locally —
safe alongside the nightly).

Full operator walkthrough, including the AWS console steps: **`docs/marketing-stream.md`**.

## KDP support (books as a second profile, 2026-08-02)
KDP is a SEPARATE Amazon Ads advertiser profile (Sponsored ads · KDP) under the same
account — modeled as a new market **`USKDP`** (`markets.py`: kind=kdp, label "KDP US",
`profile_env=AMZN_ADS_PROFILE_ID_US_KDP`, NA endpoint, own `ads_data_USKDP.sqlite`). It
appears in the app's profile switcher ("KDP US"). Book economics (`kdp_econ.py`) implement
Amazon's published royalty formula — paperback rate 50% (<$9.99) / 60% (≥$9.99) minus US
b&w printing ($2.30 ≤110pp else $1.00+$0.012/pp); ebook 70/35% minus delivery; break-even
ACOS = royalty/list_price. Per-book inputs live in `kdp_books.json` via **`appctl kdp-book
[--asin X --list-price P (--royalty R | --format --pages --ink) | --clear]`** — enter the
royalty straight off your KDP dashboard (most accurate) or the inputs to compute; a book
with no data **fails closed** (economics unavailable, never guessed), like the Merch econ
gate. **App: the KDP-only "KDP Books" tab** (own sidebar screen, shown only for a KDP
account — `Screen.isKDPOnly`; it left Settings 2026-08-14) lists the entries and takes the
dashboard-royalty path (asin + list price + royalty); the print-cost compute path stays
CLI-only. `kdp-book`'s list reply also carries `title` and `advertised` per book: `title` is
the full Amazon title (from the `kdp_titles.json` cache, ad-group name as fallback),
`advertised` is True when the book has an ENABLED ad group in an ENABLED campaign. **`appctl
kdp-titles [--refresh]`** (LIVE read) fills `kdp_titles.json` from the SP product-metadata
endpoint (`ads_client.product_metadata`) — the ONLY title source for a book with no campaign,
whose name is nowhere in the pulled data; the fast `kdp-book` read never calls Amazon. The
cache is gitignored, regenerable. `products.design_be_for`, `rules/econ_fields`, and `cmd_profit` are kind-aware (route
to `kdp_econ` for KDP markets), so kill list / DSL `break_even`+`royalty`+`profit` / Profit
all work for books. Nightly (`run_scheduled.sh`) runs pull+metrics+rules for KDP only when
its profile id is in `.env` (no tee phases). **Operator-gated to go live:** add
`AMZN_ADS_PROFILE_ID_US_KDP=<profile id>` to `.env`, then `ADS_MARKET=USKDP python3
appctl.py run --phase pull` (or `phase0_pull.py`).

## Rules DSL (economics-aware automation language, 2026-08-01)
An operator-authored, plain-language automation layer over the engine (parallel to the
hardcoded phases, not a replacement). Package `rules/` (lexer→parser→evaluator→entities→
econ_fields→runner→executor→store); design/plan in `docs/superpowers/`. Windows are
`CURRENT` (latest cumulative snapshot ~trailing-30), `LIFETIME`, and **rolling
`IN LAST N DAYS`** (added 2026-08-06, once `target_daily` gave the engine true per-day
rows). Rolling windows work for targets, keywords, ad groups (all summed from
`target_daily`) and campaigns (from `campaign_daily`). **The window ends two days
before today** (`db.DAILY_ATTRIBUTION_LAG_DAYS`), because the freshest day or two is
still under-attributed and would read as a collapse in sales. N is capped at 92 days
(`db.MAX_DAILY_WINDOW_DAYS`) — Amazon's reporting retention. A rolling change is
gated by `db.daily_window_gate` on the table it was measured over, so a rule with a
hole in its window refuses to write rather than acting on a partial sum.
Economics fields (`break_even`/`royalty`/`profit`/`royalty_roi`/`halo_est`/`in_transition`/
`is_cohort`/`econ_available`/`product_type`/`lifetime_sales`) are first-class and REUSE the
phase economics (`products.get_design_econ` etc.), fail-closed when unavailable.
- `rules-list` → `{rules:[{name,enabled,mode,season,updated}]}`. Reading the
  index also RESTORES `rule_defs/` from `rule_defs.backup.json` when the
  directory is missing (loudly, on **stderr** — stdout carries only the JSON
  envelope). An index that EXISTS is never overwritten, even when it holds zero
  rules: deleting every rule is something an operator may do on purpose, and
  `store.rules_lost()` reports that case rather than undoing it.
- `rules-get --rule <name>` → `{name,text,enabled,mode,season,updated}`
- `rules-save` (stdin `{name,text,enabled?,mode?,season?}`) → validates then writes
  `rule_defs/<slug>.rule` + index; rejects unparseable. `rule_defs/` is gitignored user data.
- `rules-delete --rule <name>`
- `rules-validate` (stdin or `--rule`) → `{ok, errors:[{line,col,message}]}` — lex/parse
  AND semantics (2026-08-05): unknown fields and unknown/not-yet-executable action verbs
  are rejected here, not discovered as a nightly "unsupported" later.
- `rules-preview` (stdin or `--rule`) → READ-ONLY `{ok,market,evaluated,matched,changes:[{entity_kind,
  entity_id,label,action,args,args_text,note,econ_driven,trace:[{condition,actual,threshold,pass}],
  ref}],truncated,errors}` — proposed changes NEVER executed. A row that errors is
  recorded in `errors` and skipped; the preview continues (never aborts on one bad
  row). An action with a NONE argument (NULL bid, unavailable economics) is skipped
  fail-closed. `econ_driven` is true when economics reach the write through the
  condition, a LET binding, or the action's own arguments. `IN LIFETIME` resolves
  snapshot metrics (clicks/spend/acos/…) to NONE — only `lifetime_sales`, identity
  and economics fields carry data there (no per-entity lifetime history exists).
LIVE / ACTION:
- `rules-run [--rule <name>] [--apply]` → preview (default) or apply. `--apply` routes every
  write through `ads_client` (max-bid ceiling clamps), logs `writes_log`, KILL + snapshot
  freshness (db.snapshot_gate on each change's SOURCE perf table — the DSL fails closed on
  stale evidence exactly like phase2/phase3; blocked changes report `blocked_stale_data`)
  + econ-gate (economics-driven changes only) + change-cap enforced. Every Amazon response
  is checked: a 4xx/5xx batch reports status `failed`, logs `result=failed`, and is NOT
  counted applied; successful pause/enable also mirrors local ad-group/campaign state.
- `rules-nightly` → ENABLED + in-season rules for the market: AUTO applies now (self-gates);
  REVIEW queues its changes to the pending Approval store. Wired into `run_scheduled.sh` per
  market; no-op until rules exist.
- Review-mode Approval queue: `rules-collect` (re-evaluate review rules → pending, read-only re:
  Amazon), `rules-pending` → `{market,conflicts,changes:[{id,rule,entity_kind,label,action,
  args_text,note,trace,conflict?,…}]}`, `rules-approve` (stdin `{ids}` → executes the approved
  subset via the executor, clears applied), `rules-discard` (stdin `{ids}` or `{all:true}`).
  App: **Approval Queue** has a Phase-2 / Rules picker; the Rules tab drives collect/approve/discard.
  `rules-collect` and `rules-nightly` also PRUNE the store (`pruned` in the reply) and
  `rules-delete` drops that rule's rows: `set_rule` only replaces its own rule's entries, so a
  rule that stopped being collected used to leave its last proposals in the queue for good.
- **Cross-rule conflict guard (`rules/conflicts.py`, added 2026-08-07).** Every rule is
  previewed and executed on its own, so two enabled rules that both moved one target's bid
  BOTH wrote and whichever ran last silently won. A conflict is narrow: two or more
  DIFFERENT rules proposing a change to the same entity (one rule emitting several
  statements for one entity is authored intent, never flagged). The first rule in rule
  order wins, which is stable. **AUTO** (`rules-nightly`) collects every auto rule's
  changes before executing, keeps the winner and skips the rest — the reply carries
  `conflicts_skipped` and a `conflicts` list, and each rule's summary row carries
  `skipped_conflict`. **REVIEW** keeps every proposal and marks it: each contested row
  gets `conflict:{with,surface,winner,kept}` so the operator sees both options and picks.
  `rules-approve` resolves the same way — approving both sides applies the winner and
  leaves the loser in the queue to approve on its own. App: a banner over the Rules queue,
  a per-row badge, and "Keep winners only".
Actions: `pause/enable` (target/adGroup/campaign), `setBid`, `setBudget`, `addNegative`
(createKeyword/setBiddingStrategy not yet executable). App: **Rules** screen (Manage) — editor +
Validate/Preview(⌘↩)/Save + "Run & apply now".

## Action subcommands (implemented 2026-07-02)
All return the same `{"ok":…}` envelope. Every write checks the `KILL` file first (JSON
error, not killswitch's text+exit), logs to `writes_log`, and mirrors local state.
- `kill [--on|--off]` → `{kill_active}` — the freeze file; no flag = report state.
- `pause --adgroup <id>` / `enable --adgroup <id>` → `{prev_state,new_state,applied,http}`.
- `pause-campaign --campaign <id>` / `enable-campaign --campaign <id>` — same shape.
- `archive-campaign --campaign <id> --confirm` — same shape, but **PERMANENT**:
  Amazon has no un-archive, so the campaign leaves the console for good and can
  never be re-enabled. Refuses without `--confirm`, and `archive_campaign` is
  deliberately absent from `UNDOABLE` so the Audit Trail never offers an Undo it
  cannot honour. Banked history stays in the DB. Pausing is the reversible option.
- `setbid --target <id> --bid <x> [--prev <old>]` → `{prev_bid,new_bid,applied}`
  (`--prev` = app's last-known bid, recorded in the log for undo; on success the
  new bid writes through to the `targets` mirror).
- `pause-target --target <id>` / `enable-target --target <id>` — one targeting clause.
- `setbudget --campaign <id> --budget <x> [--prev <old>]` — daily budget; clamped by
  the budget ceiling below.
- `maxbid [--set --target X --keyword Y --budget Z | --clear]` → the per-market WRITE
  CEILINGS (engine_meta): every bid and daily-budget write through ads_client is
  clamped to them and logged “[adjusted]”. Empty string clears one surface. **App:
  Settings → Max bid ceiling.**
- `adopt-export <csv>` — adopt a product export as the engine's economics source
  (validates, re-maps products, deletes superseded copies — after export_snapshot
  has banked them). A MerchFlow `export_products_*.csv` is a WHOLE catalog, so
  adopting one deletes the older ones. A Snap `snap-grid-export-*.csv` is a CHUNK
  and nothing is deleted for it.
- `resetbids [--apply]` → `{count,total_reduction,preview,items:[{targetId,original,current,new}]}`.
- `negatives-preview` → `{as_of, as_of_search_terms, as_of_targeting,
  negatives:[{search_term,campaign_id,ad_group_id,spend,reason}],
  pauses:[{ad_group_id,campaign_id,spend,reason,name,asin}]}` — the approval-queue data
  (wraps phase2_apply.candidates). **`as_of` is the OLDER of the two dates** — the plan
  is no fresher than the oldest evidence behind it — and each half also reports the
  table it was actually read from: negatives from `search_term_perf`, pauses from
  `targeting_perf`.
- `negatives-apply` — approved subset via **stdin** JSON
  `{"negatives":[{search_term,campaign_id,ad_group_id}…], "pauses":[ad_group_id…],
  "as_of":…, "as_of_search_terms":…, "as_of_targeting":…}`.
  **Each half is re-checked against its OWN table before anything is written.** The
  Approval Queue can sit open across a nightly pull, so a plan whose evidence moved is
  refused with a sentence and nothing is applied. The first version of that guard
  compared `as_of` against `search_term_perf` alone, which failed in both directions
  of the drift: with targeting behind, EVERY apply was refused and re-previewing
  reproduced the same mismatch, and with search terms behind the pause half was never
  checked at all. The two tables are filled by independent report jobs — the US
  database holds 12 days where only one of them had a snapshot. A plan carrying only
  `as_of` comes from an older app and is compared against the older of the two current
  dates, the way the preview built it. Guarded by
  `tests/negatives_evidence_gate_tests.py`.
- `audit [--limit N]` → `{writes:[{row_id,at,action,entity_type,entity_id,detail,prev_state,
  result,undoable}], totals:{today,week,no_ops_today,undoable,window_days}}` (newest first).
  **`totals` is counted in SQL over the WHOLE log; `writes` is one page.** The app used
  to derive its cards from the loaded page, so every one of them was capped by the fetch
  limit: US read "500 writes this week" against a true 10,635, in a week whose busiest
  day logged 9,663 writes. A screen built to catch a runaway rule cannot print the page
  size as the account's number. No-op rows ("0 ASINs" builder runs) are excluded from
  `today` and `week` and counted separately; `undoable` asks `_row_undoable` per row
  rather than restating that rule in SQL, where the two could drift.
- `undo --row <rowid>` — inverse of ONE logged write (pause↔enable, restore old bid);
  negatives/creates are permanent in v1.
- `import-preview <csv> [--days N=14]` → recent uploads for the current market, deduped vs
  DB, routed: tees → Lottery + Scavenger Tees (ALL markets since 2026-07; US fills
  'Lotto N' to 1000 in numeric order, then creates Lotto 9, 10, …);
  other cohort types → their Scavenger series (ad-safe `adAsins` for hardgoods). The
  `--days` recency window is essential when the file is the FULL catalog (2M+ rows).
  Unscoped US lottery_build runs window to the last 60 days of uploads.
  **Two product-grid exports are accepted, and `engine/export_reader.py` is the only
  place that knows the difference.** Snap for MOD writes `snap-grid-export-*.csv`
  (Title Case columns, "Live" status, dashboard type labels like "PopSocket") — that is
  how new designs arrive today: Products tab → select the new products → three-dots menu
  (⋮) → Export selected data → Export full data (CSV). MerchFlow's
  `export_products_*.csv` (camelCase columns) is still read. `export_reader.rows()`
  hands every caller MerchFlow-shaped rows. A Snap product-type label with no entry in
  `products.EXPORT_TYPE_LABELS` is reported in `skipped_types` under its own name and
  never guessed into a cohort — `standard_tshirt` is the lottery money path, so a
  near-miss is worse than a skip.
  **A route carries every design it routed.** Each is
  `{route,count,returned,truncated,designs}`, and `count == returned` today because
  `appctl.INTAKE_ROUTE_CAP` is 0. It used to be 2000, silently: `count` was the true
  total and `designs` was the first 2000, with no flag between them. The app ticks and
  builds the designs it was SENT, so a cohort of 5,000 headlined 5,000, listed 2,000 and
  gave the other 3,000 no ads — and `scavenger_build`'s coverage report could not catch
  it either, because that is measured against the scope file, which only ever held the
  2,000. The whole 90-day US window is 29,485 designs at ~224 bytes, so the uncapped
  reply is 6.6 MB on the largest market in the account, the same order as
  `accumulated-asins --limit 0`. `returned` and `truncated` stay in the reply so a cap
  can never be invisible again. Guarded by `tests/screen_counts_tests.py`.
- `import-apply <csv> [--days N]` — approved retail ASINs via stdin `{"asins":[…]}` (empty
  stdin = all new); writes a scope file to outputs/ and runs `lottery_build.py` /
  `scavenger_build.py` with the new `--export <csv> --asins-file <scope>` flags (both
  builders accept these now; scoped 0-sale designs are allowed into their typed cohort).
  **`cohorts` is the REQUEST and `coverage` is the RESULT.** They are not the same
  number and the screen must not print one as the other. `coverage` is
  `scavenger_build`'s own report, read back from
  `outputs/scav_build_<MARKET>.json`: `{available,scoped,planned,unplanned,refused,
  no_ad_safe,no_ad_safe_series,paused_campaigns,series:[{series,matched,planned,
  added,refused,no_ad_safe,over_cap,paused_campaigns}]}`.
  `unplanned` counts scoped ASINs no cohort claimed, `over_cap` counts the tail
  `shard()` dropped, and `paused_campaigns` names campaigns that took new ads
  while PAUSED — ads Amazon accepted that can never serve. `available:false` means
  the builder wrote no report, which is UNVERIFIED and never clean. appctl removes
  any older copy before the build, so a stale report can never be mistaken for
  this run's. **App: the Import screen** draws a market with any of these in amber
  and says what was missed, instead of a green "Complete".
  **`refused` is product ads Amazon TURNED DOWN** — submitted minus accepted
  (added 2026-08-25). It is not the same silence as `added: 0`, and it used to
  be. `new_asins` is computed against Amazon's live product-ad list and a
  refused ad never joins that list, so the same ASINs go back the next night
  and the night after: about 873 a night across six markets, unchanged since
  2026-06-25, with `added: 0` the only number on disk — which is also exactly
  what a market with nothing new to add writes. WHY each one was refused is on
  STDERR, not in this file: `chunked_create` prints the refused count, the HTTP
  status and the first few entries of Amazon's error block, and
  run_scheduled.sh captures stderr into `outputs/scheduled_runs.log`. Grep it
  for `REFUSED`. `lottery_build.add_asins` prints the same line for ad groups
  and product ads. See `docs/rejected-product-ads-2026-08-25.md`.
  **Amazon names a refused entry by its POSITION in the batch, not by ASIN.**
  All 164 reasons captured on the 2026-08-25 run carried `index` and none
  carried `cause.trigger`, so every line read `0: adEligibilityError
  AD_INELIGIBLE` — the right reason attached to no design. `report_refused`
  takes an `items=` argument, the batch that was submitted, and resolves the
  index back to the ASIN: `B0XXXXXXXX (#3): …`. Pass it from every caller. A
  refusal nobody can attribute proves a whole cohort is broken and cannot pick
  88 bad tees out of 3,682 good ones, which is the half of this that is still
  open.
  **`no_ad_safe` is a hardgood the cohort wanted and CANNOT advertise** (added
  2026-08-25). A hat, mug, tumbler or water bottle is only advertisable through
  the export's ad-safe ASIN (`adAsins`); its retail ASIN returns
  `adEligibilityError AD_INELIGIBLE`, every time. `scavenger.py` said so in a
  comment from the day it was written and `load_build_specs` did the opposite —
  it fell back to the retail ASIN and submitted it — which is 474 of the US
  residue: 194 hats and 280 drinkware, matched to the unit against the 2026-08-25
  log (SCAVENGER - Hats 1 submitted 194 and Amazon refused 194, against exactly
  194 hats in that shard with no ad-safe ASIN; a drinkware shard of 1,000 ad-safe
  ASINs refused none). `scavenger.AD_SAFE_REQUIRED_TYPES` is the one place that
  knows which types those are, and it is a TYPE question, not a series one.
  Those listings are skipped now — and COUNTED, never silent, because skipping
  in silence just moves the lie: the design is still coverage the account has
  lost, and only a fresh export with the ad-safe column populated recovers it.
  The count is kept per series AND as a total, since a series skipped down to
  nothing never reaches `series` at all, and a gap that vanishes when it grows
  total is the worst possible shape to report.

## v1 scope & decisions (from the plan + the operator)
- v1 = **viewer + actions** (all essentials in `swiftui-app-plan.md`).
- Actions confirmation: **one-click for single small actions** (one pause/enable, one bid),
  **confirm dialog for bulk**. Add a **Settings toggle "always confirm"** to force a
  confirm on everything.
- New-design intake: **drag-drop only** (no folder watching).
- Screens: per-market Dashboard (trailing-30 headline + trend + movers + daily "settling"),
  Campaign browser (→ ad groups → targets, with bid-history timeline), Live status lookup
  ("Refresh from Amazon"), Actions + **Approval queue** (review proposed negs/pauses/bids),
  Audit trail (reads `writes_log`, rollback where supported), Kill-list screen, CSV intake,
  Settings, System health.
