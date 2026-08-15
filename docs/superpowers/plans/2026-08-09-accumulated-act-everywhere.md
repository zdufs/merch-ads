# Scope: Accumulated "act everywhere" (MerchDash parity gap #2)

Status: **FULLY BUILT** — 2026-08-09. All phases shipped the day of the re-audit:
Phase 1 (app bulk), Phase 1.5 (setBid-everywhere + phrase negatives), and Phase 2
(the DSL loop-entity form). Companions also shipped: no-op protection, reversible
negatives, inline baseline/trend windows.

**Phase 2 as built:** `accumulated_asin` / `accumulated_keyword` are loop entities
(`rules/entities.py`), read the CURRENT rollup with `campaigns`/`ad_groups` counts,
and take the everywhere verbs `pauseEverywhere` / `setBidEverywhere` /
`negateEverywhere`. The executor fans one change out to every instance, reusing the
Phase 1 resolver + apply loop (`appctl._everywhere_plan` / `_everywhere_apply_ops`,
lazy-imported). Entity/verb pairing is validated at save time (everywhere verbs
only on accumulated; bare pause/setbid rejected on accumulated; negate/setbid are
keyword-only). Tests in `tests/rules_accumulated_tests.py`. So the cross-campaign
cleanup can now run as a nightly REVIEW rule, e.g. "pause any ASIN over $25 with 0
orders, everywhere."

**Phase 1 as built:** `everywhere-preview` / `everywhere-apply` in appctl.py
(resolver `_everywhere_plan` + tests in `tests/everywhere_tests.py`), and a
context-menu action on both Accumulated screens — "Pause ASIN(s) everywhere" and,
for keywords, "Negate everywhere" / "Pause everywhere". The confirm sheet states
the resolved blast radius (N instances across M campaigns) from the preview call,
routes the apply through the ActionCoordinator (KILL-gated, bulk-confirm), skips
already-paused no-ops, and every write is logged and undoable. Not yet built:
setBid-everywhere and phrase negatives (Phase 1.5), and the DSL loop-entity form
(Phase 2 below).

## The gap

MerchDash's Accumulated view is not just a report — it is an action surface. Its
whole point is to catch a keyword or ASIN that looks harmless in any one campaign
but bleeds across all of them, then fix it in one shot:

- Loop entities `accumulated_keyword` / `accumulated_asin` (via
  `CREATE ACCUMULATED ... REPORT IN LAST n DAYS`).
- Actions `pauseEverywhere()`, `setBidEverywhere(v)`, `negateEverywhere([match])`
  — applied to every still-live instance.
- The same actions from the UI by selecting rows.

Their cookbook leans on this: "Pause Accumulated Keyword Bleeders" ($40 spend, 0
orders, across N campaigns) and "Stop Silent ASIN Budget Eaters."

**What we have.** The read half only. `appctl accumulated-asins` /
`accumulated-keywords` return the summed rollup (one row per ASIN / per keyword
text), and `--expand <asin|keyword>` returns the per-(campaign, ad group)
`breakdown`. The app's Accumulated screens show the rollup and let you pin to the
Watchlist or export — no action. So you can SEE a wasteful ASIN spread over 8
campaigns but cannot pause it everywhere without opening each by hand.

## What already exists (so this is smaller than it looks)

- The instances to act on are one call away: `_accumulated_asins(conn, expand=asin)`
  already returns `breakdown` = every `(campaign_id, ad_group_id)` the ASIN ran in,
  with per-instance spend/orders. Same for keywords.
- The batch-apply-through-safety-rails pattern exists twice: `cmd_negatives_apply`
  and `cmd_import_apply` take a stdin JSON subset and apply it through the engine's
  guards. The new endpoints mirror them exactly.
- The guards the writes must pass all exist: KILL file, per-run change cap, max
  bid/budget ceilings (clamped + logged), `writes_log` + Undo, and — as of today —
  no-op protection and reversible negatives.

## Proposed design

**Phase 1 — app-side bulk action (recommended first).** The read data and the
expand breakdown already exist, so this is mostly plumbing.

New `appctl` endpoints, stdin-driven like `negatives-apply`:

- `accumulated-pause-everywhere` — stdin `{"asins":[...]}` (or `{"keywords":[...]}`).
  Resolves each to its live `(campaign_id, ad_group_id)` instances via the same
  breakdown query, then pauses the ad groups (ASINs) / keywords through
  `ads_client`, logging one `writes_log` row each so every pause is individually
  undoable.
- `accumulated-negate-everywhere` — stdin `{"keywords":[...], "match":"exact|phrase"}`.
  Adds the keyword as a negative in every ad group it ran in. Reuses the
  now-reversible negative path (logs `negid=`).
- `accumulated-setbid-everywhere` — stdin `{"keywords":[...], "bid":x}`. Clamped by
  the max-bid ceiling like every other bid write.
- A read-only `accumulated-*-everywhere --preview` (or a dry-run flag) that returns
  the exact instance list and count BEFORE applying, so the app can confirm ("pause
  this ASIN in 8 ad groups across 6 campaigns?").

App: the Accumulated ASINs / Keywords screens gain a multi-select + an "Everywhere"
action menu, and a confirm sheet showing the resolved instance count (bulk always
confirms, per the v1 rule). Results feed the Audit trail; Undo reverts each
instance, or the whole batch.

**Phase 2 — DSL support (larger follow-on).** Add `accumulated_keyword` /
`accumulated_asin` as loop entities and `pauseEverywhere` / `setBidEverywhere` /
`negateEverywhere` as actions, so the cross-campaign cleanup can run as a nightly
REVIEW rule. This needs: a new loader that returns one row per accumulated entity
carrying its instance list; executor verbs that fan a single change out to every
instance (and count them against the change cap correctly); and a preview that
shows the fan-out. Bigger, and only worth it once Phase 1 proves the workflow.

## Safety notes

- **Fan-out and the change cap.** One "pause everywhere" can touch dozens of
  instances. Count each instance against the 50,000 cap, and surface the resolved
  count in the preview so a broad selection can't silently rewrite the account.
- **No-op protection (already shipped) matters more here.** An accumulated ASIN
  lists instances that may already be paused; skip those rather than churn them.
- **Live vs cached state.** The breakdown is from the last pull. Resolve instances
  against the freshest mirror, and let Amazon's own idempotent response be the final
  word — do not assume a cached ENABLED is still enabled.
- Everything routes through `ads_client` (ceilings) and `writes_log` (Undo); these
  are operator-run live writes, pre-staged like the rest.

## Effort estimate

- Phase 1: ~1 focused session. Three thin stdin endpoints over the existing
  breakdown query + a batch executor that reuses `pause` / `addNegative` / `setbid`,
  plus the app's multi-select and confirm sheet.
- Phase 2 (DSL): ~2–3 sessions; new entity loader, fan-out executor semantics,
  preview, validation, tests.

## Decisions

- **"Pause everywhere" pauses ad groups, never archives.** Decided by the operator
  2026-08-09. Pausing is reversible (Undo restores it); archiving a campaign is
  permanent and Amazon has no un-archive. So even a single-ASIN campaign gets its
  ad groups paused, not archived.
- **`negateEverywhere` defaults to exact match.** Decided by the operator 2026-08-09.
  Exact is surgical — it blocks only that query, no risk to good variants. Phrase
  only when explicitly asked.

## Still open

1. Phase 1 first (app bulk over the existing endpoints), or go straight to the DSL
   rule form? (Recommendation stands: app bulk first, it's the smaller piece.)
