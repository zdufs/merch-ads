# Errors tab — combined problem view

**Date:** 2026-07-19
**Status:** Approved (design), ready for implementation

## Problem

Errors and problem states are scattered across the app with no single home:

- `AppState.lastError` — a top banner for global read failures (markets/health).
- Engine **alerts** (`alerts` endpoint) → fire as native notifications only (spend spike / budget max / kill candidate).
- **System Health** (`health`) — per-market stale data, not configured, kill active, approval required, engine pull-log errors (`lastNote`), stalled reports (`reportsPending`).
- **Econ gate** (`econ-gate`) — closed gate blocks every economics-driven write, and is **not surfaced in the app at all today**.
- **Adopt-export failures** — shown only inside the New Designs screen.
- **appctl failures** (non-zero exit, timeouts, `ok:false` envelopes) fired from views via `try?` are **silently swallowed** — nothing records them.

The user wants one sidebar tab that combines everything wrong.

## Decisions (from brainstorming)

1. **Scope:** "Everything wrong, now" — a live current-problems view. App failures + engine health anomalies + econ gate closed + engine alerts.
2. **No new persistent storage.** In-memory, session-scoped.
3. **Transient app failures:** kept in an in-memory session list so a timeout/failed call stays visible until dismissed or the app quits. Health/gate/alerts shown live alongside.

## Architecture

Two streams merged in one view:

### Stream A — session app failures (`IssueCenter`)
`@MainActor @Observable final class IssueCenter` (singleton `IssueCenter.shared`, also environment-injected). Holds `appIssues: [AppIssue]`.

Captured at **one choke point**: `PythonBridge.call` records any thrown `BridgeError.engineError` / `.badOutput` (incl. timeouts) before re-throwing. This covers **all reads and writes** (writes go through `BridgeActionExecutor` → `PythonBridge.call` too), including `try?`-swallowed call sites. `rehearsalDenied` and `CancellationError` (market switches) are **not** recorded.

Adopt-export failures come from a *successful* envelope field, not a throw, so `IntakeView` reports them explicitly.

Dedup: identical failures (same `dedupKey` = source+command+message) collapse to one row with an occurrence count and the latest timestamp. User can dismiss one or "Clear all". Cleared on quit.

### Stream B — live derived issues (`IssueDerivation.live`)
Pure static function `live(health:econGate:alerts:) -> [AppIssue]`, recomputed from already-loaded state — no new fetch inside the view. Maps:

- `health.killActive` → **Blocking** "KILL active — writes frozen"
- `econGate.ok == false` → **Blocking** "US economics gate closed" + reasons in detail
- market with data but stale > 2 days / no fresh data after nightly → **Blocking** / **Error**
- `health.approvalRequired` → **Warning** "Approval gate on"
- market `configured == false` → **Warning** "Market not configured"
- market `lastNote` (pull-log error) → **Error** with the note
- market `reportsPending > 0` → **Error** "N reports stalled"
- each engine `alert` (spend_spike/budget_max/kill_candidate) → **Warning**

Live issues have stable ids (their `dedupKey`) and are **not** dismissable.

AppState owns the inputs and exposes:
- `var econGate: EconGateResponse?` — fetched in `refresh()` (US gate, `["econ-gate"]`).
- `var currentAlerts: [EngineAlert]` — populated by `checkAlerts()` (in addition to notifying).
- `var liveIssues: [AppIssue]` — `IssueDerivation.live(health:econGate:alerts:)`.
- `var openIssueCount: Int` — blocking+error count across live + app issues, for the sidebar badge.

## Model

```
enum IssueSeverity: Int, Comparable { case warning = 0, error = 1, blocking = 2 }  // higher = more severe
enum IssueSource: String { case appCall, adopt, health, econGate, alert, stale }

struct AppIssue: Identifiable, Hashable {
    let id: String            // UUID for app failures; dedupKey for live issues
    let severity: IssueSeverity
    let source: IssueSource
    let title: String
    let detail: String?
    let market: String?
    let timestamp: Date
    let dedupKey: String
    let dismissable: Bool
    var count: Int = 1
}
```

`EconGateResponse` (new, in Models): `{ ok: Bool, reasons: [String], market: String?, modelVersion: String? }`.

## UI (`ErrorsView`)

- Merges `issueCenter.appIssues` + `appState.liveIssues`, sorted by severity (most severe first) then timestamp (newest first).
- Grouped into **Blocking / Error / Warning** sections; each section header shows a count.
- Row: severity icon+tint, source chip, title, market badge, relative time; tap to expand `detail` (selectable text). App-failure rows have a dismiss (×); a toolbar "Clear all" clears app issues (live ones remain until the condition clears).
- Empty state: "No problems — everything's healthy." with a green check.
- Refresh button re-runs `appState.refresh()` + `checkAlerts()`.

## Sidebar

- New `Screen.errors` (title "Errors", icon `exclamationmark.triangle`, blurb). Placed at the top of the **System** section.
- Row shows `.badge(appState.openIssueCount)` when > 0.
- `detailView` renders `ErrorsView()`.

## Testing

`IssueCenterTests` (XCTest, `@testable import Merch_Ads`):
- `IssueCenter` add / dismiss / clearApp / dedup (same key bumps count + timestamp, distinct keys stay separate).
- `IssueDerivation.live` against fixtures: closed econ gate → one blocking issue with reasons; kill active → blocking; stale market → blocking/error; approval required + unconfigured market + alerts → warnings; healthy inputs → empty.
- `AppIssue(fromBridge:)` maps `.engineError` and `.badOutput`/timeout to error severity with a stable dedupKey; `.rehearsalDenied` is skipped by the recorder.

## Fix-all-safe (added 2026-07-19)

A literal "fix every error" button is unsafe: the issue classes don't share a
remedy, several fixes are operator-gated live mutations (KILL off, approval off),
and some issues need human judgment (kill-candidate alerts) or manual setup
(unconfigured market). Decision (user): **fix what's safe, stage the rest.**

### Remediation model
`AppIssue` gains `fix: IssueFix?`:
```
enum IssueFix: Hashable {
    case pull(market: String)        // safe: app fires `run --phase pull` via the coordinator
    case operatorCommand(String)     // gated: raw appctl fragment, operator runs via `!`
    case review(Screen)              // judgment/setup: navigate to the right screen
}
```
Mapping (in `IssueDerivation` for live issues; on session issues at capture):
- `stale:*`, `reports:*`, `pullnote:*` → `.pull(market)`
- `econGate`, adopt failures → `.operatorCommand("ADS_MARKET=US python3 map_products.py")`
- `kill` → `.operatorCommand("ADS_MARKET=US python3 appctl.py kill --off")`
- `approval` → `.operatorCommand("ADS_MARKET=US python3 appctl.py approval-mode --off")`
- `alert:*` → `.review(.killList)` for kill_candidate, `.review(.campaigns)` otherwise
- `unconfigured:*`, appCall failures → `nil` (no button; manual / handled by refresh)

`operatorCommand` stores only the appctl fragment; the view composes the full
copyable line (`cd '<engineRoot>' && <fragment>`) so `IssueDerivation` stays
free of `AppSettings`.

### "Fix all safe" button (Errors header)
One confirm dialog (bulk), then:
1. **Auto-run:** dedupe `.pull` fixes to distinct markets; run each via
   `AppState.runPull(market:)` → `actionCoordinator.execute` with the existing
   `["run","--phase","pull"]` intent (KILL-gated, audited), sequentially with
   progress. Then `refresh()`.
2. **Stage:** collect distinct `.operatorCommand` fragments into a "Run these
   yourself" panel, each with a Copy button — nothing is fired.
3. If `killActive`: skip pulls (they'd be blocked) and stage `kill --off` first;
   the dialog explains this.

### Per-row Fix affordance
Each row renders the single button matching its `fix`: `.pull` → **Re-pull**
(fires that one pull), `.operatorCommand` → **Copy fix command**, `.review` →
**Review →** (navigates). Rows with `fix == nil` get no button.

### Testing
Extend `IssueCenterTests`: assert the issue→fix mapping (stale→pull, kill→
operatorCommand, kill_candidate alert→review(.killList)), and that distinct
`.pull` markets dedupe.

### Still out of scope
No new appctl endpoints, no new live-write capability — the only action fired is
the pull that already exists on the Actions screen.

## Out of scope (YAGNI)

- Persisting issues across relaunch.
- Replacing native notifications.
- Server-side error aggregation / new appctl endpoints (reuses `health`, `econ-gate`, `alerts`).
