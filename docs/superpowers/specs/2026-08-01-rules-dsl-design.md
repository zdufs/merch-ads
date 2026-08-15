# Spec B — Economics-aware rules DSL

**Date:** 2026-08-01
**Branch:** `tamas-method-halo-candidates` (feature work; never `main`)
**Status:** design approved 2026-08-01, ready to plan
**Blocked-by:** Spec A (`2026-08-01-merchads-quick-wins-design.md`) — SHIPPED. Reuses its
max-bid clamp (safety primitive) and debug-trace infra (`ConditionTrace`/`DebugTraceBlock`,
`_cond`) as the DSL's preview surface.

## Context

MerchDash's biggest structural advantage over MerchAds is a **user-editable plain-language
automation language** (see `memory/merchdash-competitor-benchmark.md`). MerchAds' strategies
are hardcoded Python phases. This spec adds a text DSL over the engine — but economics-first,
exposing royalty/break-even/halo as first-class fields MerchDash structurally cannot (it only
reads the Ads API, never the Merch `SALES_REPORT`). Operator decisions (2026-08-01):
- **Text DSL + in-app editor with preview** (not a visual builder; not files-only).
- **Nightly + on-demand** execution (no always-on scheduler process).
- Expose **all** economics groups: break_even/royalty/profit, TAMAS halo/organic,
  econ-gate/transition guards, cohort/product_type/lifetime.

## THE load-bearing constraint — data shape

MerchAds' `campaign_perf` / `targeting_perf` / `search_term_perf` are **cumulative
trailing-~30-day snapshots at the latest pull date** — one row per (entity, latest date), NOT
per-day history (repo invariant: read the latest snapshot, SUM across entities within it,
never across dates; see `memory/search-term-perf-cumulative.md`). `daily_totals` is the only
true per-day table and it is **account-wide**, not per-entity.

**Therefore the DSL CANNOT offer MerchDash-style rolling windows** (`LAST 7 DAYS`,
`YESTERDAY`, `FROM x TO y`) — the per-entity daily data to back them does not exist. The DSL
window model is deliberately small and honest:
- **`CURRENT`** (default) — the latest cumulative snapshot (~trailing 30 days).
- **`LIFETIME`** — stored `ad_group_product.lifetime_sales` (units only; no lifetime spend/acos).
- **change-recency** via `days_since_bid_change` / `days_since_budget_change` (from `writes_log`).

No arbitrary windows. A rule that needs "this week vs baseline" (MerchDash's trend-brake) is
**not expressible** and the docs/editor must say so. This is a correctness guardrail, not a
limitation to paper over.

## Architecture overview

Four layers, built in order; each independently shippable:

1. **Language core** (`rules/`, Python) — tokenizer → parser → AST → evaluator, read-only
   (validate + preview). No writes. Economics fields resolved here. TDD-heavy.
2. **Actions & safety** — the action verbs, each routed through `ads_client` (max-bid clamp
   applies) + `writes_log` + `KILL`/econ-gate/change-cap. `rules-run [--apply]`.
3. **Storage & scheduling** — rules as files + index; nightly hook in `run_scheduled.sh`;
   season-window gating; on-demand run/preview.
4. **App** — SwiftUI "Rules" screen: list + editor (highlight + inline validate) + preview
   pane (reuses `DebugTraceBlock`) + Run/mode/enable controls; Review-mode → Approval queue.

---

## Layer 1 — Language core

### Grammar (mirrors MerchDash's readable style, minus unavailable windows)
```
program     := statement+
statement   := for_each
for_each    := "FOR EACH" entity ["AS" name] ["IN" window] ":" NEWLINE block
block       := (INDENT (let | if_stmt | action_stmt))+
if_stmt     := ("IF"|"WHEN") condition ":" NEWLINE INDENT stmt+   ; two-space indent
let         := "LET" name "=" expr
condition   := expr (comparator expr) | condition ("AND"|"OR") condition | "NOT" condition | "(" condition ")"
action_stmt := entity_ref "." action "(" args? ")"
window      := "CURRENT" | "LIFETIME"
```
- Case-insensitive keywords/fields/functions; `#` line comments; strings `"..."`.
- Units: money `$0.85`, percent `45%` (=0.45), numbers, `TRUE/FALSE/NONE`, lists `["a","b"]`.
- Operators: `AND OR NOT`, `= == != <> < <= > >=`, `IN / NOT IN`, `CONTAINS / STARTS WITH /
  ENDS WITH`, arithmetic `+ - * / %`. Text equality case-insensitive.
- Functions: `MIN MAX CLAMP ROUND FLOOR CEIL ABS IF(cond,then,else) LOWER UPPER`.
- `LET name = expr` (top-level visible everywhere; body LETs scoped).

### Entities & the data behind them
| Entity | Source (latest snapshot) | Notes |
|---|---|---|
| `keyword` / `target` | `targeting_perf` | keyword = target with match_type; both here |
| `searchTerm` | `search_term_perf` | shopper queries; harvest/negate raw material |
| `campaign` | `campaign_perf` | rollup |
| `adGroup` | `targeting_perf` grouped | carries default_bid |
| `product` / `asin` | `ad_group_product` ⋈ `targeting_perf` | one ASIN across ad groups |
Relations via dot: `keyword.campaign`, `searchTerm.adGroup`, `target.asin`.

### Fields
- **Metrics** (over the window): `impressions clicks spend/cost sales orders units acos roas
  ctr cvr cpc`. Zero-denominator ratios (acos with 0 sales) = `NONE` → never match numeric
  comparisons (so "high acos" rules skip zero-sale rows, matching killlist semantics).
- **Economics (the moat), first-class** — resolved via `products.get_design_econ`,
  `products.econ_gate`, `appctl._design_be_for`-equivalent, `tamas_halo`, `db.active_price_changes`:
  - `break_even` — the design's own break-even ACOS (per-price US tee economics / market_econ).
  - `royalty` — per-unit royalty; `profit` — royalty×orders − spend; `royalty_roi`.
  - `halo_est` `net_halo` `organic_per_day` — US TAMAS organic-lift fields (`supported:false`
    → `NONE` off-US).
  - `in_transition` (bool) — design inside a 30-day price-transition window.
  - `econ_available` (bool) — economics resolvable for this design. **When false, every
    economics FIELD returns `NONE`** so economics-driven conditions skip the row (fail-closed,
    exactly like the phases). The engine never guesses margin.
  - `product_type` `is_cohort` (multi-ASIN NULL-asin group) `lifetime_sales`.
- **Settings/identity**: `bid bidInherited state name match_type keyword_text search_term asin
  ad_type targeting_type budget bidding_strategy days_since_bid_change days_since_budget_change`.

### Windows
`CURRENT` (default) and `LIFETIME` only (see the load-bearing constraint). An `IN LAST N DAYS`
in source is a **validation error** with a message pointing at the snapshot-data explanation.

### Outputs of Layer 1
- `rules-validate` (stdin rule text) → `{ok, errors:[{line,col,message}]}`.
- `rules-preview` (rule text or `--rule <name>`) → per-entity proposed changes with
  `trace:[_cond…]` (reuses the Spec A trace shape) — NO writes.

### Tests (Layer 1)
`tests/rules_lexer_tests.py`, `tests/rules_parser_tests.py`, `tests/rules_eval_tests.py`
(temp-SQLite fixtures): tokenization, parse errors with line/col, condition evaluation incl.
`NONE`-skip semantics, economics fields resolve/skip correctly (break_even match; econ-
unavailable → skip; in_transition guard), window validation rejects `LAST N DAYS`.

---

## Layer 2 — Actions & safety

### Actions
`keyword/target.setBid(x)`, `.pause()` `.enable()` `.setState("PAUSED")`,
`campaign.setBudget(x)` `.setBiddingStrategy("…")`, `*.addNegative(text[,"exact"|"phrase"])`,
`searchTerm.createKeyword(text[,match[,bid]])`, `*.note("… {acos:percent} …")`.
Computable args: `setBid(MAX($0.05, keyword.bid * 0.85))`.

### Safety (all reused from existing rails)
- Every write goes through `ads_client` → **max-bid clamp (Spec A) applies automatically**;
  clamps logged `[adjusted]`/`cap_v1=`.
- `KILL` file check + econ-gate: economics-driven writes (setBid on econ conditions, negate,
  pause) refuse when the gate is closed — same policy as phase scripts.
- Per-run change cap (Settings `max_changes_per_run`, default 50,000): a run stops recording
  at the cap and reports truncation.
- Every change → `writes_log` with the `note()` reason (revertible via existing `undo`).
- **Live writes stay operator-run**: `rules-run --apply` is pre-staged for the operator via `!`;
  the auto-mode classifier blocks agent-initiated production writes (standing convention).

### appctl
- `rules-run [--rule <name>] [--apply]` — preview (default) or apply approved changes;
  `--apply` KILL+econ-gate gated. Review-mode rules collect into the Approval queue.

### Tests (Layer 2)
`tests/rules_actions_tests.py`: each action produces the right `ads_client` call shape (mocked,
no live Amazon) + `writes_log` row + reason; clamp interaction (a `setBid` above ceiling logs
`[adjusted]`); KILL/econ-gate refusal; change-cap truncation.

---

## Layer 3 — Storage & scheduling

- Rules live in `rules/<name>.rule` (text) + `rules/index.json`
  (`{name:{enabled, mode:"review"|"auto", season:{start,end}|null, created, note}}`).
  Global (not per-market) by default; a rule may pin `MARKET IN [...]` in a header comment
  parsed as scope, or run per-market in the nightly loop (default: all markets with data,
  matching engine convention).
- **Nightly**: `run_scheduled.sh` runs enabled rules after the existing phases, inside the
  KILL+approval-gated block (`rules-run --apply --auto` per market; review-mode rules collect
  to the queue). No-op when no rules enabled.
- **Season windows** reuse `seasonal_pause.in_window`/date logic.
- appctl: `rules-list`, `rules-get --rule`, `rules-save` (stdin `{name,text,enabled,mode,season}`),
  `rules-delete --rule`.

### Tests (Layer 3)
`tests/rules_store_tests.py`: save/list/get/delete round-trip; index integrity; season-window
gating (in/out of window); disabled rules skipped.

---

## Layer 4 — App (SwiftUI "Rules" screen, Manage section)

- New `Screen.rules` (Manage). `RulesView`: master list (name, mode badge, enabled toggle,
  next-run/last-run, season chip) + detail editor.
- **Editor**: `TextEditor` with syntax highlight (keyword/field/string/comment via
  `AttributedString` re-tinted on edit, debounced), inline validation from `rules-validate`
  (error line markers), a field/action **reference sidebar** (the economics fields prominent).
- **Preview pane**: runs `rules-preview`, renders proposed changes grouped by entity with the
  `DebugTraceBlock` (reused from Spec A) showing each condition's actual-vs-threshold.
- Controls: Save, enable toggle, mode (Review/Auto) picker, season window, **Run Now / Preview**
  (Run routes through `ActionCoordinator`; `--apply` operator-gated per standing rule). Review-
  mode runs surface in the existing **Approval queue**.
- Models: `Rule`, `RuleListResponse`, `RuleValidateResponse`, `RulePreviewResponse`
  (proposed changes reuse `ConditionTrace`).

### Tests (Layer 4)
`MerchAdsTests`: decode `RulePreviewResponse` incl. `trace`; a highlight tokenizer unit test;
existing suite stays green.

---

## Cross-cutting

- **Docs**: add the `rules-*` contract to `docs/claude-code-handoff.md`; a `docs/rules-dsl.md`
  authoring guide (grammar, fields — especially the `CURRENT`/`LIFETIME`-only window model and
  the economics fields — plus a cookbook of economics-first rules, e.g. "pause where
  `profit < 0 AND clicks >= 15`", "bid up where `royalty_roi > 1.5 AND acos < break_even`").
- **Build/commit/relaunch** standing rules apply (commit per task; `package_app.sh --install`
  on Swift change; relaunch).
- **Seed rules**: ship a few disabled example `.rule` files translating current phase logic
  (kill rule, harvest, bid-down-high-acos) so the operator has working starting points — but
  the hardcoded phases REMAIN the source of truth; the DSL is additive, not a replacement, in v1.

## Risks / notes
- **Scope creep vs the phases**: the DSL does NOT replace phase2/3/4/lottery/scavenger/tamas in
  v1 — they stay. The DSL is a parallel, operator-authored layer. Reconciling overlap (a rule
  and a phase both acting on the same entity in one night) is handled by the existing no-op
  live-state check + change dedup; the docs warn against duplicating phase logic in a rule.
- **Editor effort**: syntax highlight + inline validation in SwiftUI is the largest single
  piece; if it slips, Layer 4 can ship with a plain `TextEditor` + a "Validate" button
  (validation still server-side) and highlight added later.
- **Economics resolver reuse**: Layer 1 must call the SAME resolvers as the phases
  (`products.get_design_econ`, econ-gate, `_design_be_for` logic, `tamas_halo`) — never a
  re-implementation — so DSL economics can't drift from phase economics.

## Build order
1. Layer 1 (language core + validate/preview, read-only) — the foundation, TDD-heavy.
2. Layer 2 (actions + safety wiring + `rules-run`).
3. Layer 3 (storage + nightly + scheduling).
4. Layer 4 (Rules screen + editor + preview pane).
Each layer is its own set of tasks with an independently testable deliverable.
