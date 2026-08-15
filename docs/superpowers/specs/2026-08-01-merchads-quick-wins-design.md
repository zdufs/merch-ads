# Spec A — MerchAds quick wins (MerchDash-inspired)

**Date:** 2026-08-01
**Branch:** `tamas-method-halo-candidates` (feature work; never straight to `main`)
**Status:** design approved 2026-08-01, ready to plan

## Context

MerchDash.net (see `memory/merchdash-competitor-benchmark.md`) is the closest competitor to
MerchAds / MerchAds. A docs review surfaced six features worth borrowing. The operator
chose all four "quick wins" here (the fifth, a full rules DSL, is Spec B, blocked by this
spec). MerchAds' moat is royalty-aware economics MerchDash structurally cannot match; these
four features add MerchDash's UX/safety strengths **without** diluting that moat.

**Overriding design principle: enforcement and computation live in the engine, not the app.**
The nightly launchd job (`run_scheduled.sh` → `phase3_bids`, `lottery_build`, `tamas_*`)
writes to Amazon with the app closed. Any guardrail or derived value that must always hold
therefore belongs engine-side; the Swift app is editor/viewer only. This is the same rule as
the existing golden rules (Swift never calls Amazon; everything mutating goes through
`appctl.py` / `ads_client.py`).

## Scope

Four features, built in this order (reusable primitives first):

1. **Max-bid ceiling** — a per-market hard cap enforced on every bid write.
2. **Debug traces** — per-entity evaluated condition values on the existing previews.
3. **Accumulated reports** — cross-campaign ASIN and keyword rollups.
4. **Watchlist** — a private per-market pinboard view with an aggregate trend line.

Out of scope: the rules DSL (Spec B); portfolios-as-filter (low value for the merch
campaign architecture); Sponsored Brands / Display bid granularity (MerchAds is SP-only).

---

## Feature 1 — Max-bid ceiling

### Goal
No bid MerchAds writes to Amazon can exceed a per-market ceiling, whatever the caller
(manual `setbid`, `resetbids --apply`, nightly `phase3`, `lottery_build`, `tamas_optimize`,
harvested-keyword creation). A write above the ceiling is written **at** the ceiling and
marked `adjusted` in the audit trail. The ceiling only caps; it never raises a bid, and never
turns a write into a failure.

### Storage
Per-market `engine_meta` KV table (`db.meta_get` `db.py:162` / `db.meta_set` `db.py:167`),
inside each market's `ads_data*.sqlite`. Keys:
- `max_bid_target` — ceiling for Sponsored Products target bids (auto + manual keyword targets).
- `max_bid_keyword` — ceiling for TAMAS keyword bids.

Values are dollar strings (e.g. `"1.20"`). Absent / blank key = no ceiling for that surface
(default). Stored per-market because US and EU bid at different levels.

### Enforcement chokepoint
Clamp inside `ads_client.py`, the single funnel every bid write passes through:
- `update_target_bids(items)` `ads_client.py:258` (bid rounded at `:264`) → clamp against `max_bid_target`.
- `update_keyword_bids(items)` `ads_client.py:322` (`:327`) → clamp against `max_bid_keyword`.
- `create_keywords(items)` `ads_client.py:304` (bid at `:310`) → clamp against `max_bid_keyword`.

`AdsClient` is market-aware already; it reads its ceilings once at construction (or lazily
from a passed-in `conn` / a light `db.connect(ro=True)`), so the clamp needs no new argument
on every call site. Clamp math: `written = min(requested, ceiling)` when `ceiling` set and
`requested > ceiling`; Amazon's $0.02 (/$0.25 SB) floor still applies under it. When a value
is clamped, `ads_client` records the pair `(requested, written)` so the caller can log it.

### Audit
Every bid write already calls `db.log_write(..., action="bid_change", detail="snap=<src> <old>-><new> (<reason>)")`.
When a clamp fired, append a versioned suffix to `detail` (NOT hand-formatted around the
existing ` econ_v1=` marker — add a new ` cap_v1={"req":<requested>,"cap":<ceiling>}` marker,
and any reader strips via `db.detail_prefix()` per the `db.py:216-219` convention). The
human prefix gains ` [adjusted]`. This keeps `RX_BID` (`appctl.py:61`) parsing of `old->new`
intact (new = the written/clamped value).

### appctl surface
New `maxbid` subcommand (registered beside `season-*` at `appctl.py:2004`):
- `appctl maxbid` (or `--get`) → `{"ok":true,"data":{"market":..,"target":<str|null>,"keyword":<str|null>}}`
- `appctl maxbid --set --target 1.20 --keyword 0.90` → writes via `db.meta_set`, returns the new state.
- `appctl maxbid --clear [--target|--keyword]` → unset one or both.
Read-only `--get` is a fast command (safe anytime); `--set/--clear` are local writes (no
Amazon call), guarded by `_guard_kill()` for consistency but NOT econ-gated (config only).

### Swift UI
`Views/SettingsView.swift`: new `settingsSection(title: "Max bid ceiling", …)` after the
Actions section (`SettingsView.swift:56`). Two currency `TextField`s (Target bid, Keyword
bid) for the **currently selected market**, loaded via `bridge.call(MaxBidResponse.self,
["maxbid"], market: appState.selectedMarket)` and saved via `["maxbid","--set",…]`. A short
note states it caps every write including nightly runs, and that clamps show as `adjusted` in
the Audit trail. New `MaxBidResponse` Codable struct in `Models.swift`. (No `@AppStorage` —
the source of truth is engine-side per-market, not app UserDefaults.)

### Tests
`tests/maxbid_tests.py` (unittest + temp-SQLite fixture, mirroring `tests/econ_tests.py`):
- clamp caps a bid above ceiling; passes a bid below unchanged.
- no ceiling set → no clamp.
- clamp still respects the $0.02 floor.
- `db.meta_set`/`meta_get` round-trip per market.
- `detail_prefix()` strips the new `cap_v1=` suffix cleanly.
Clamp logic unit-tested by injecting a fake requested bid + ceiling (no live Amazon).

---

## Feature 2 — Debug traces

### Goal
On the existing preview endpoints, expose the per-entity numbers each condition evaluated to,
so the operator (and later the DSL) can answer "why did/didn't this fire?" without re-deriving
by hand. The values are **already computed** at these sites; today they are flattened into a
prose `reason` or dropped.

### Data shape
Add an optional field to the relevant rows:
```
trace: [ {condition: str, actual: <number|str|null>, threshold: <number|str|null>, pass: bool} ]
```
`actual`/`threshold` are raw (fractions for acos/cvr, dollars for bid/spend) — the app formats
them. `null` actual (e.g. ACOS with zero sales) renders as "—" and never falsely matches.

### Endpoint changes
- `killlist` (`cmd_killlist` `appctl.py:517`): per design row (attach at `:545`) add
  `trace` = `[cvr vs FLOOR_CVR (0.08), acos vs break_even]`. `skipped` designs optionally
  carry a one-item trace naming the skip reason (`transition`/`unknown_price`/`cohort`).
- `negatives-preview` (`cmd_negatives_preview` `appctl.py:1466`): surface the econ suffix that
  is currently dropped (`phase2_apply._design_target` builds it, `phase2_apply.py:58,65`).
  Negatives get `trace` = `[clicks vs MIN_CLICKS_NEG, orders vs 0]` or
  `[acos vs ceiling]`; pauses get `[acos vs target, cvr vs 0.08]`.
- `resetbids` preview (`cmd_resetbids` `appctl.py:1430`, plan from
  `reset_inflated_bids.build` `reset_inflated_bids.py:33`): per item add
  `trace` = `[current vs original]` explaining the 10%-below-original reset.

The engine keeps emitting the existing human `reason`/`skipped` fields unchanged — `trace` is
purely additive, so nothing downstream breaks.

### Swift UI
- Add optional `trace: [ConditionTrace]?` to `KillDesign`, `ProposedNegative`, `ProposedPause`,
  and the resetbids item model in `Models.swift` (decoded via existing `.convertFromSnakeCase`).
  New `ConditionTrace` Codable struct.
- Render primarily in the **inspectors**: extend `KillRowInspectorView`
  (`CampaignBrowserInspectors.swift:166-180`) with a "Debug trace" `VStack` of
  `LabeledContent("<condition>", "<actual> vs <threshold>")` rows, pass/fail tinted.
- Approvals tables (`ApprovalsView.negativesTable`/`pausesTable`): add an opt-in "Trace" column
  (its own `.customizationID`, hidden by default, toggleable via the existing `ColumnPrefs`
  machinery) that shows the failing/deciding condition; full trace on row inspect.

### Tests
`tests/trace_tests.py`: each endpoint returns a `trace` array whose `pass` flags and
actual/threshold values match the row's own `reason`/inclusion decision (e.g. a killlist row
has `cvr.pass == (cvr < 0.08)` and `acos.pass == (acos > break_even)`, both true for included
rows). Assert `trace` is additive (all pre-existing fields still present).

---

## Feature 3 — Accumulated reports

### Goal
Roll performance up **by the thing itself** instead of by campaign: each advertised ASIN and
each keyword summed across every campaign it runs in, expandable to a per-campaign breakdown.
Exposes designs/keywords quietly bleeding across many small campaigns (the lottery/scavenger
architecture spreads a design across many campaigns, so this is especially useful here).

### Endpoints (read-only, fast, DB-only)
- `accumulated-asins` → `{market, as_of, count, rows:[{asin, product_type, campaigns, ad_groups,
  impressions, clicks, spend, orders, sales, acos, cvr}]}` — `SUM` over `targeting_perf` at the
  **latest snapshot only** (cumulative-not-summed convention, per `db.py` and the map from the
  exploration), joined to `ad_group_product` for ASIN + type, `GROUP BY asin`. NULL-asin
  (multi-ASIN cohort) rows bucketed separately, never merged into a real ASIN.
- `accumulated-keywords` → same envelope with rows keyed by `(targeting, match_type)` summed
  over `targeting_perf` (the things you *bid on* — keyword/product targets), at latest
  snapshot, `GROUP BY targeting + match_type` across all campaigns/ad groups. (This is the
  MerchDash "Accumulated Keywords" sense: keywords, not shopper search terms. A search-term
  rollup over `search_term_perf` is a distinct future addition, out of scope here.)
- Both accept `--limit N` (default e.g. 500, worst-by-spend first) and an optional
  `--expand <asin|term>` returning the per-campaign/per-ad-group breakdown for one row (so the
  UI lazy-loads a row's children on disclosure rather than shipping the whole tree).
- Optional `--csv` writes `outputs/accumulated_asins{_MKT}.csv` / `..._keywords{_MKT}.csv` via
  the established `csv.DictWriter` + market-suffix convention (`tamas_candidates.py:184`).

`cmd_profit` (`appctl.py:706`) is the closest existing template for the aggregation loop.

### Swift UI
Two new screens under the **Insights** sidebar section (`ContentView.swift`):
- Add `accumulatedAsins`, `accumulatedKeywords` cases to `Screen` (title/icon/blurb arms).
- New `AccumulatedAsinsView` / `AccumulatedKeywordsView`, each reusing the `Table` +
  `ColumnPrefs`/`SortPrefs`/`SavedViewPicker` stack (template: `CampaignListView`). Query box
  parity is nice-to-have, not required for v1; date-window follows the app's existing window.
- Row disclosure calls `accumulated-* --expand <id>` to show the per-campaign breakdown.
- Row selection feeds the existing bulk actions (pause ASIN everywhere / negate keyword
  everywhere) through `ActionCoordinator` — same audited path as today. Bulk-everywhere is the
  natural payoff of the accumulated view.
- New `AccumulatedAsinsResponse` / `AccumulatedKeywordsResponse` Codable models.

### Tests
`tests/accumulated_tests.py`: seed a temp DB where one ASIN appears in 3 campaigns and one
keyword in 2 ad groups; assert the rollup sums match hand-computed totals and that `campaigns`/
`ad_groups` counts are right; assert NULL-asin cohorts are bucketed separately; assert the
aggregation reads only the latest snapshot date (never sums across dates).

---

## Feature 4 — Watchlist

### Goal
A private, per-market pinboard: pin arbitrary campaigns / ad groups / targets / ASINs into one
focused view with a combined trend line + aggregate summary row. Purely a view — pinning
changes nothing at Amazon. Fills the current gap in babysitting a TAMAS launch or a bid
experiment.

### Storage (app-side)
No Amazon writes, single operator → store pins in a new UserDefaults store **keyed per-market**
(`"watchlist.v1.<market>"`), a deliberate departure from the current market-global table-prefs
keying (`TablePrefs.swift`). Pins are a Codable `WatchlistPin` modeled on `Route`'s entity
cases (`Route.swift:6-11`), each carrying enough parent IDs to re-resolve:
`{kind: campaign|adGroup|target|asin, market, campaignID?, adGroupID?, targetID?, asin?, label}`.
Capacity cap (e.g. 1,000 pins) to match MerchDash and bound the resolve payload.

### Engine endpoint
`watchlist` reads a JSON list of pins from **stdin** (`{pins:[{kind, campaign_id?, ad_group_id?,
target_id?, asin?}]}`) and returns their current-window metric rows + one aggregate summary
row: `{market, as_of, rows:[{kind, id, label, impressions, clicks, spend, orders, sales, acos,
cvr}], summary:{...}}`. Resolving a handful of pins server-side avoids loading the whole
account. Reuses the per-entity perf queries already behind `cmd_campaigns`/`cmd_adgroups`/
`cmd_targets`/`cmd_asin`.

### Swift UI
- New `watchlist` case in `Screen`, in the **Manage** sidebar section.
- `WatchlistView`: the `Table` stack (reusing `ColumnPrefs`/`SortPrefs`) for the pinned rows +
  a Swift Charts trend line of the aggregate + a summary header (`StatCard`s). Empty state
  explains how to pin.
- "Pin to watchlist" added to existing tables' row **context menus** (Campaigns, Ad groups,
  Targets, ASINs, Accumulated views) — writes a `WatchlistPin` to the per-market store.
- Unpin from within the Watchlist screen. New `WatchlistResponse` Codable model.

### Tests
`MerchAdsTests/WatchlistStoreTests.swift`: per-market pin add/remove/dedup/capacity and
round-trip persistence; that pins for market US don't leak into market DE. Engine side:
`tests/watchlist_tests.py` seeds a temp DB and asserts the aggregate summary equals the sum of
resolved pin rows, and that an unresolvable pin (deleted entity) is reported, not crashed.

---

## Cross-cutting

### Testing
- Engine: `python3 -m unittest tests.<module> -v`, temp-SQLite fixtures only, no Amazon API,
  no production DB (pattern from `tests/econ_tests.py`). TDD per the superpowers skill:
  failing test first, then implementation.
- App: `MerchAdsTests` via `xcodebuild test`; existing 24 tests must stay green.

### Build / commit / relaunch (standing rules)
- Each surviving change committed on this branch the same turn (`git commit -F -` for messages
  with quotes/parens/apostrophes). Never straight to `main`.
- Engine-only change → relaunch the app (`pkill -x "Merch Ads"; open "/Applications/Merch Ads.app"`).
- Swift/plist/xcassets change → `bash scripts/package_app.sh --install` first, then relaunch
  from `/Applications`. The Stop hook (`check_app_fresh.sh`) is the backstop.

### Verify
- `ADS_MARKET=US python3 appctl.py maxbid` / `... accumulated-asins` / `... accumulated-keywords`
  return valid JSON; `killlist`/`negatives-preview`/`resetbids` now carry `trace`.
- App builds clean; new screens render real data; a clamped bid shows `adjusted` in the Audit
  trail with a `writes_log` row.

### Risks / notes
- **Clamp coverage:** the whole safety guarantee rests on `ads_client` being the sole bid
  funnel. The exploration confirmed all 11 write paths route through the three `ads_client`
  methods — the plan must re-verify no caller hand-builds a bid payload around them.
- **`AdsClient` construction cost:** reading ceilings must not add a per-write DB hit; read
  once at client init or cache on the instance.
- **Live-account writes stay operator-run:** the ceiling is enforced automatically, but the
  bid-writing commands themselves remain pre-staged for the operator to run via `!` per the standing
  operator convention. This spec changes none of the human-gate policy.

## Build order within Spec A
1. Max-bid ceiling (engine clamp + config + appctl + Settings UI + tests) — reusable safety primitive.
2. Debug traces (engine `trace` fields + inspector/column rendering + tests) — reused by Spec B previews.
3. Accumulated reports (endpoints + two screens + tests).
4. Watchlist (endpoint + per-market store + screen + pin affordances + tests).

Each is independently shippable and committable.
