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

## Python bridge (how Swift calls the engine)
- Invoke: `ADS_MARKET=<code> <python3> <folder>/appctl.py <cmd> [args…]`, `cwd` = this folder.
- Use the SAME python3 the launchd job uses (it has `requests` for live/action calls). Read
  endpoints don't need `requests`; live/action ones do. Resolve `python3` via the login
  shell PATH (mirror `run_scheduled.sh`), or make it a Setting.
- Output: exactly one JSON object on stdout: `{"ok": true, "data": …}` or
  `{"ok": false, "error": "…"}`. Decode with Codable. Treat non-zero exit or `ok:false`
  as an error to surface in the UI.

## appctl.py contract (current)
READ (fast, DB-only, safe anytime):
- `markets` → `{markets:[{code,currency,region,is_default,has_data,kind,label}], current}`
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
  clicks,spend,sales,orders,acos,cvr,last_bid,bid_changes}]}`  (added in milestone 3;
  `last_bid` parsed from the newest writes_log bid_change)
- `alltargets [--limit N=2000]` → account-wide flat target list (the app's Targets tab):
  `{as_of, count, truncated, targets:[{target_id,targeting,match_type,campaign_id,campaign,
  ad_group_id,ad_group,asin,bid,bid_inherited,impressions,clicks,spend,sales,orders,acos,
  cvr}]}`. **`bid` is the entity's OWN bid** from the `targets` mirror table that phase0
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
  `harvest-prune-apply` executes an approved subset via stdin).
- `killlist` → `{cvr_floor, count, designs:[{asin,ad_group_id,type,state,clicks,orders,cvr,
  spend,sales,acos,break_even}], skipped:{transition,unknown_price,cohort}}`  (CVR < 8%
  AND ACOS over the DESIGN'S OWN break-even — US tees are priced per design from the
  export list price, model products.US_TEE_ROYALTY_CENTS; designs in a 30-day price
  transition, with unsupported prices, or in multi-ASIN cohort groups are EXCLUDED
  and counted in `skipped`. `econ:"unavailable…"` when the DB predates migration.)
- `econ-gate` → `{ok, reasons, market, model_version}` — the US economics freshness
  gate (fresh mapped export ≤21d, no STALE marker). Closed gate = every
  economics-driven write (negatives-apply, run non-pull, promote,
  harvest-prune-apply, resetbids --apply, nightly auto-apply) refuses.
- `health` → `{kill_active, approval_required, last_run, markets:[{market,configured,
  has_data,latest_data,tables,stale_tables,target_daily,last_pull,last_write,campaigns,
  reports_pending,last_note?}]}`  (opens every market DB itself — call without
  ADS_MARKET). **`latest_data` is the WORST of the three perf tables** (each is
  filled by its own report job and they drift independently — campaign_perf
  alone stayed green through both freezes); `tables` has the per-table dates and
  `stale_tables` names any past the write-freeze threshold (>3d, same as
  db.snapshot_gate). `last_run` mirrors `outputs/last_run_status.json`, written
  by run_scheduled.sh's step tracker: `{started,finished,ok,markets,
  failures:[{market,step,exit}]}` — with Discord digests off this is how a
  crashed nightly phase reaches the operator (System Health shows it; the run
  also posts a macOS "RUN FAILED" notification). `null` until the first
  instrumented run. `target_daily` is that market's per-day coverage —
  `{days,first,last}`, or `null` when none is banked. **App: System Health's
  "Per-day history" column.** Rules with a rolling window refuse to write when
  their window has holes, so this column is where the operator finds out why a
  rule went quiet. A market with fewer days than US is not an error: the EU
  markets only began advertising 2026-06-24.
- `bidreport [--days N=7]` → `{ups,downs,net_delta,count, changes:[{at,target_id,old,new,
  delta,reason,ad_group_id,targeting,asin}]}`  (the weekly what-moved report)
- `harvest` → `{count,pending, winners:[{search_term,kind,type,clicks,orders,sales,acos,
  cpc,first_seen,last_seen,promoted}]}`  (promotion = `run --phase promote|promote-asins`)
- `stale` → `{min_impressions,count, designs:[{ad_group_id,name,asin,type,impressions,
  clicks,spend}]}`  (ENABLED, 0 lifetime sales, ≥1000 impressions, ≤2 clicks; top 500)
- `halo [--min-spend X=1.0] [--limit N=300]` → organic-halo estimate for EVERY advertised
  design (US-only; `supported:false` elsewhere):
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
- `alerts` → `{alerts:[{kind,key,message,market,campaign_id?,ad_group_id?,asin?}]}`
  kinds: spend_spike / budget_max / kill_candidate / data_stale — the app
  notifies once per `key` (dedup app-side). data_stale fires when a perf
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
- `nudges` → playbook checks: `{pricing:[{asin,lifetime_sales,price,suggested}], nudges:
  [{kind,count,screen,message}], max_price, ladder_label, truncated}`  — tee price targets:
  US launch $19.99 → $21.99 after 10 sales; EU is ALWAYS the market cap
  (markets.TEE_PRICE: UK £17.49 · DE €18.45 · FR/ES/IT €19.49). Penny tolerance only at
  the market max. Reminders only — no pricing API.
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
  (phase4/phase4b accept `--terms-file`); empty stdin = all pending
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

LIVE / ACTION (need Amazon API — run on the Mac):
- `status <ASIN…>` → `{market,asins,text,stderr,code}` (wraps `status.py`; live state)
- `run [--phase phase2|phase3|harvest|pull]` → triggers a phase or the full market run
- `seasonal-apply` → execute the seasonal pause/enable plan for this market (KILL-gated;
  logs `seasonal_pause`/`seasonal_enable`; re-enable touches only ad groups it paused).
  Runs nightly per-market in `run_scheduled.sh` (`seasonal_pause.py --apply --auto`) —
  no-op until designs are tagged. Pauses a tagged design's ENABLED ad groups out of its
  season; back in season, re-enables the ones it paused (never resurrects perf-paused designs).

Note numbers: `acos`/`cvr` are fractions (0.1816 = 18.16%). Money is in the market currency.

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
- `rules-list` → `{rules:[{name,enabled,mode,season,updated}]}`
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

## Action subcommands (implemented 2026-07-02, milestones 5–6)
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
- `adopt-export <csv>` — adopt a newer catalogue export as the engine's economics
  source (validates, re-maps products, deletes superseded copies — after
  export_snapshot has banked them).
- `resetbids [--apply]` → `{count,total_reduction,preview,items:[{targetId,original,current,new}]}`.
- `negatives-preview` → `{as_of, negatives:[{search_term,campaign_id,ad_group_id,spend,reason}],
  pauses:[{ad_group_id,campaign_id,spend,reason,name,asin}]}` — the approval-queue data
  (wraps phase2_apply.candidates).
- `negatives-apply` — approved subset via **stdin** JSON
  `{"negatives":[{search_term,campaign_id,ad_group_id}…], "pauses":[ad_group_id…]}`.
- `audit [--limit N]` → `{writes:[{row_id,at,action,entity_type,entity_id,detail,prev_state,
  result,undoable}]}` (newest first).
- `undo --row <rowid>` — inverse of ONE logged write (pause↔enable, restore old bid);
  negatives/creates are permanent in v1.
- `import-preview <csv> [--days N=14]` → recent uploads for the current market, deduped vs
  DB, routed: tees → Lottery + Scavenger Tees (ALL markets since 2026-07 — MerchFlow
  retired; US fills 'Lotto N' to 1000 in numeric order, then creates Lotto 9, 10, …);
  other cohort types → their Scavenger series (ad-safe `adAsins` for hardgoods). The
  `--days` recency window is essential — the export is the FULL catalog (2M+ rows).
  Unscoped US lottery_build runs window to the last 60 days of uploads.
- `import-apply <csv> [--days N]` — approved retail ASINs via stdin `{"asins":[…]}` (empty
  stdin = all new); writes a scope file to outputs/ and runs `lottery_build.py` /
  `scavenger_build.py` with the new `--export <csv> --asins-file <scope>` flags (both
  builders accept these now; scoped 0-sale designs are allowed into their typed cohort).

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

## Suggested tech
- SwiftUI, macOS 14+ target. **Swift Charts** for trends (built-in, no dep).
- SQLite reads: **GRDB.swift** via SPM (ergonomic) or the raw `sqlite3` C API (zero deps) —
  your call; open read-only.
- One `PythonBridge` type: `run(_ args:[String], market:String?) async throws -> Data`,
  plus Codable models mirroring the JSON above. Centralize error surfacing.
- App name suggestion: **MerchAds**. Personal use → no notarization needed; unsigned
  local run is fine.

## Build order (milestones)
1. Xcode project + SQLite read layer + `PythonBridge` + market switcher + app shell.
2. Dashboard (read-only) from `metrics` — verify numbers match `appctl.py metrics`.
3. Campaign browser (`campaigns`→`adgroups`→ targets) + bid-history timeline.
4. Live status lookup (`status`). Kill-list screen (`killlist`).
5. Add the action `appctl` endpoints (above) + Actions panel + Approval queue + Audit/rollback.
6. CSV intake (get sample CSV first). Settings + System health.

## Verify
- Backend: `ADS_MARKET=US python3 engine/appctl.py metrics` (and each cmd) returns valid JSON.
- App: `xcodebuild` builds clean; dashboard figures equal the raw `appctl` output; actions
  show a `writes_log` row after firing.
