# Architecture

How the pieces fit together, and why they are arranged this way.

---

## The shape of it

```
                  Amazon Ads API
                        ▲
                        │  reads (reports) + writes (bids, pauses, negatives)
                        │
                 ┌──────┴───────┐
                 │  ads_client  │   auth, retries, bid ceilings, batch checking
                 └──────┬───────┘
                        │
   ┌────────────────────┼────────────────────┐
   │                    │                    │
┌──┴───┐          ┌─────┴──────┐      ┌──────┴──────┐
│phases│          │ rules/ DSL │      │  appctl.py  │
│0-4   │          │ your logic │      │  JSON API   │
└──┬───┘          └─────┬──────┘      └──────┬──────┘
   │                    │                    │
   └────────────────────┼────────────────────┘
                        │
                 ┌──────┴───────┐
                 │    db.py     │   schema, gates, freshness rules
                 └──────┬───────┘
                        │
              ads_data[_MARKET].sqlite    one file per marketplace
                        │
                        │  read-only (mode=ro)
                        ▼
              ┌──────────────────┐
              │  Merch Ads.app   │   SwiftUI. Reads SQLite directly for speed,
              │  (SwiftUI)       │   calls appctl.py for everything else.
              └──────────────────┘
```

Three rules hold the whole design together:

1. **The Python engine is the brain. Swift is the window.** No business logic was
   reimplemented in Swift. There is exactly one copy of every rule.
2. **Swift never writes.** It never opens a database for writing and never calls Amazon.
   Every action shells out to `appctl.py`, so the app inherits the kill switch, the
   freshness gates, the economics gate and the audit log for free.
3. **Fail closed.** Missing or stale evidence means refuse, never guess.

---

## The Python engine

### Foundation

| Module | Role |
|---|---|
| `ads_client.py` | The Amazon Ads API client. Token refresh, retries on transient drops, report polling, bid and budget ceiling clamps, and per-batch response checking so a failed batch is never counted as applied. |
| `db.py` | SQLite schema and every data-integrity rule. Freshness gates, rolling-window helpers, bulk-write chunking. If you read one file to understand the system, read this one. |
| `markets.py` | The marketplace table: endpoint, currency, profile environment variable, and whether a market is Merch or KDP. |
| `products.py` | Product types and design economics — list price to royalty to break-even ACOS. |
| `kdp_econ.py` | The same job for books. Amazon's published KDP royalty formula. |
| `killswitch.py` | The `KILL` file. Twenty lines, and the most important safety feature here. |
| `stream_*.py`, `aws_sigv4.py` | Amazon Marketing Stream — the optional hourly push channel. `stream_config` holds realms, regions and Amazon's per-dataset publisher accounts; `stream_api` does subscriptions; `stream_sqs` and `aws_sigv4` talk to AWS with no boto3; `stream_drain` empties the queue and answers the SNS handshake; `stream_store` banks messages. |

### The nightly phases

They run in order, once per market, from `run_scheduled.sh`.

| Phase | Module | What it does |
|---|---|---|
| 0 | `phase0_pull.py` | Download every Amazon report into SQLite. Also mirrors per-target bids and banks per-day rows. The slow step. |
| — | `map_products.py` | Resolve each ASIN to its product type and current list price. |
| 1 | `phase1_dryrun.py` | Compute what would change, write nothing. |
| — | `harvest.py` | Find search terms that converted and deserve their own keyword. |
| 2 | `phase2_apply.py` | Reactive negative keywords and ad-group pauses. |
| — | `preempt_negatives.py` | Preemptive negatives for wrong-format traffic. |
| 4 | `phase4_harvest_create.py`, `phase4b_harvest_asins.py` | Promote harvest winners into their own targets. |
| — | `harvest_prune.py` | Pause harvested keywords that turned wasteful. |
| 3 | `phase3_bids.py` | Bid changes, per product type, against that design's economics. |
| — | `lottery_build.py`, `scavenger_build.py`, `scavenger_optimize.py` | Build and maintain the campaign structures. |
| — | `seasonal_pause.py` | Pause seasonal designs outside their window, re-enable them inside it. |
| — | `appctl.py rules-nightly` | Run your own DSL rules. |
| — | `daily_metrics.py` | Bank today's true per-day totals. |

Two banking steps deserve emphasis. Amazon's reporting only reaches back about **95 days**
and rolls forward. `daily_metrics.py` and `backfill_daily.py` store per-day totals locally
so your history outlives that window. Without them, year-over-year comparison is
impossible.

### The strategies

These encode one particular way of running Merch ads. They are not the only way, and the
Rules DSL exists precisely so you do not have to adopt them.

- **Lottery** (`lottery.py`, `lottery_build.py`) — very wide, very cheap campaigns holding
  up to 1,000 ASINs each, to discover which designs have any demand at all.
- **Scavenger** (`scavenger*.py`) — typed cohort campaigns per product type, pruning what
  dies and retiring what never lived.
- **Organic halo** (`halo.py`, `traz.py`) — estimate the *organic* lift a design got
  while it was advertised, by comparing its royalty per day before and after ads started.
  The Ads API reports ad-attributed sales only, so this is the one view that can ask
  whether advertising moves organic revenue. Covers **every** campaign type: ad facts are
  summed per design across every ad group advertising it, from the true per-day
  `target_daily` rows. **US only** — it needs the dated Merch `SALES_REPORT`, which has no
  marketplace column. **Correlational, not causal.** `halo_est` is an upper bound and the
  code says so.

`campaign_kinds.py` is the single place that decides what kind a campaign name
describes. Amazon has no field for it — the strategy lives in the name the builder gave
the campaign — so every part of the engine that treats types differently has to read the
name, and they all have to read it the same way.

### The Rules DSL

`rules/` is a small language so you can write your own automation without editing the
engine. Lexer, parser, evaluator, entity resolution, economics fields, a runner, an
executor and a conflict guard.

```
lexer -> parser -> ast_nodes -> evaluator -> entities/econ_fields -> runner -> executor
                                                                        │
                                                              conflicts, pending, store
```

Rules are validated before they are saved and previewed before they run. Economics fields
(`break_even`, `royalty`, `profit`, `royalty_roi`) reuse the same code the phases use — no
second implementation to drift.

Full guide: **[rules-dsl.md](rules-dsl.md)**.

### `appctl.py` — the JSON API

One entry point for everything the app can do. Every call prints exactly one JSON object:

```json
{"ok": true,  "data": {...}}
{"ok": false, "error": "..."}
```

```bash
ADS_MARKET=DE python3 engine/appctl.py metrics
```

Roughly 90 subcommands, split into three classes:

- **Read** — database only, safe any time, no network.
- **Live** — calls Amazon to read (`status`, `kdp-titles`).
- **Action** — writes. Checks the kill switch, logs to `writes_log`, mirrors local state.

There is also `appctl.py serve`, a long-running line protocol. The app keeps one worker
per market so a read costs about 5 ms instead of about 50 ms of interpreter startup.
Writes and long jobs still run as one-shot processes.

Full list: **[COMMANDS.md](COMMANDS.md)**.

---

## The data model

One SQLite file per marketplace: `ads_data.sqlite` for US, `ads_data_<CODE>.sqlite` for
the rest. All in WAL mode, so the app can read while the nightly job writes.

**The single most important thing to understand:**

| Table kind | Tables | What one row means |
|---|---|---|
| **Cumulative snapshots** | `campaign_perf`, `targeting_perf`, `search_term_perf` | A trailing-30-day **total** as of one pull date. Consecutive rows overlap by 29 days. |
| **True per-day** | `daily_totals`, `campaign_daily`, `target_daily` | One real day. |

Consequences you cannot ignore:

- **Never sum snapshot rows.** You would be adding overlapping totals.
- **Never date one snapshot table from another's newest date.** Each is filled by its own
  Amazon report job, and those jobs fail independently. Taking `MAX(date)` from one and
  filtering another with it matches **zero rows**, and the caller then reports "no
  changes" instead of "no data". That silently froze bids, pauses and harvesting for four
  nights before the guard existed. Always resolve the date from the table you are about to
  read: `db.latest_snapshot(conn, "<table>")`, or `db.snapshot_gate(...)` when a write
  depends on it.

`tests/snapshot_lint_tests.py` enforces this across the whole engine. If it fails, fix the
module — do not widen the allowlist.

- **The snapshot tables have a retention window.** They gain one row per entity per pull
  and, until 2026-08-22, nothing ever removed one — US `targeting_perf` reached 2.0M rows
  at about 52,000 a night. `db.SNAPSHOT_RETENTION_DAYS` is 400 days and the nightly prunes
  on Mondays (`appctl.py prune-snapshots --apply`). The window is far past anything the
  API can re-fetch, so it caps future growth rather than reclaiming the present, and no
  snapshot is the only copy of anything: Amazon's reporting retention is ~95 days and the
  true per-day history lives in `target_daily` / `campaign_daily`. Deleting does not shrink
  the file — SQLite reuses the pages, which is exactly what bounds growth — and no VACUUM
  runs against a database the app holds open.

Other notable tables: `targets` (a nightly mirror of every entity's own bid, because
before it existed "bid" silently meant the ad-group default), `writes_log` (the audit
trail, with previous values for undo), and `engine_meta` (ceilings, the automatic-change cap, and other per-market settings).

**`stream_data.sqlite` is deliberately outside this scheme.** Marketing Stream delivers
into one SQS queue per API realm, so all five EU markets share a queue and their messages
arrive mixed together. Which market a message belongs to is a field of the payload, so
arrival is kept separate from interpretation: every message is banked whole in its own
database, and the mapping into per-market rows is written against real payloads rather
than against a documentation page. Stream does not replay, so the first hour of data is
the only chance to learn what Amazon actually sends — `stream-fields` counts it.

---

## The Mac app

SwiftUI, Swift 6, macOS 26. Roughly 94 source files, in folders that mirror what the
app actually does. Xcode 16 synchronized groups back the target, so the folders on disk
ARE the project structure — moving a file needs no project edit.

```
MerchAds/
├── Info.plist          pinned by INFOPLIST_FILE; must stay here
├── AppIcon.icns        copied to Contents/Resources; must stay here
├── App/                entry point and the AppKit menu-bar item
├── Bridge/             PythonBridge, PythonWorkerPool, SQLiteStore, ActionCoordinator
├── Models/             Codable mirrors of the appctl replies, plus routing
├── State/              observable app state, settings, stores, table/filter prefs
├── Support/            formatters, CSV export, sort proxies, small helpers
├── Design/             theme, typography, layout
├── Components/         reusable views (StatCard, LoadableView, FilterBar, …)
└── Views/              one folder per sidebar section
    ├── Shell/          ContentView, command palette, menu-bar popover, Settings
    ├── Overview/       Dashboard, All Markets, System Health
    ├── Campaigns/      browser, ad groups, targets, live status, kill list, KDP books
    ├── Analysis/       profit, halo, cross-purchase, accumulated, reports, watchlist
    ├── Rules/          the DSL editor, library, approval queue, strategy builder
    ├── Actions/        actions, approvals, harvest, audit, errors
    └── Import/         the import hub and its three sources
```

| Layer | Where |
|---|---|
| Bridge | `Bridge/` — the process pool, JSON envelope decoding, read-only SQLite, and one funnel for every mutating call. |
| State | `State/` — observable state, settings, and the stores behind watchlist and accumulated views. |
| UI | `Views/` (42 files, 27 screens), `Components/` (21), `Design/` (7) |

The sidebar filters itself by account kind: a KDP profile hides Merch-only screens and
shows the KDP Books screen instead.

Two hard-won implementation notes are recorded in `DESIGN.md`, because both cost many
rebuild cycles to find: the menu bar item is an AppKit `NSStatusItem` rather than a
SwiftUI `MenuBarExtra`, and a greedy `Table` must not have its presence toggled by a
`@ViewBuilder` conditional.

---

## Design decisions worth knowing

**Why SQLite, not a server.** One user, one machine, one account. A server would add
operations work and a place for credentials to leak, and buy nothing.

**Why shell out instead of embedding Python.** Process isolation. A crash in a phase
cannot take the app down, and `appctl.py` can be developed and tested from the command
line without Xcode.

**Why Swift never writes.** Every safety rail lives in one place. If Swift could write,
every rail would need a second implementation, and the two would drift.

**Why fail closed everywhere.** In advertising, acting on wrong data is worse than not
acting. A missed optimisation costs a little. A bid set from a stale or misread number
costs real money and is hard to notice.

---

## Reading order for a new contributor

1. `db.py` — the schema and every data rule.
2. `markets.py` and `products.py` — markets and economics.
3. `phase0_pull.py` — how data arrives.
4. `phase3_bids.py` — a complete decision path, start to finish.
5. `appctl.py` — the API surface.
6. `rules/` — the DSL, if you want to extend the language.

Then run the tests:

```bash
python3 -m unittest discover -s tests -p '*_tests.py' -t .
```

683 of them, no network, no credentials required.
