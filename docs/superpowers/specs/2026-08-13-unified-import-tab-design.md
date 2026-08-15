# Unified Import Tab — Design

**Date:** 2026-08-13
**Status:** Approved design, ready for implementation planning.
**Scope:** MerchAds macOS app only. No engine (`appctl.py`/Python) changes.

## Goal

Give the app **one Import tab for all droppable files**. Today file intake is
spread across three places. A person has to remember which tab takes which file.
This folds them into a single "Import" tab with two clearly-labeled sub-tabs.

## Current state — the three import surfaces

1. **New Designs** (`Screen.intake`, "New Designs", in the *Manage* sidebar group).
   `MerchAds/Views/IntakeView.swift`. Drop the catalogue export
   (`export_products_*.csv`) → preview a routing plan → approve designs → build
   real Lottery/Scavenger campaigns on Amazon. A full interactive workflow with
   per-design approval, "Build All Markets", KILL gating, confirmation dialogs,
   and export adoption. This branch **writes to the live account**.

2. **Import** (`Screen.dataImport`, "Import", in the *System* sidebar group).
   `MerchAds/Views/ImportView.swift`. Drop a CSV → it auto-detects and banks
   it: the Merch sales report (`sales-report --import`) or the console monthly
   history (`history-import`). Read-only banking. Shows banked coverage and an
   import ledger. It already recognizes a catalogue export and points the user to
   New Designs rather than half-handling it.

3. **Organic Halo** (`Screen.halo`). `MerchAds/Views/HaloView.swift` embeds the
   `SalesReportBar` component (`MerchAds/Components/SalesReportBar.swift`) so
   the sales report can be imported inline where its analysis is read.

## Decisions locked in brainstorming

- **Everything in one tab.** New Designs stops being its own sidebar entry and
  moves inside the Import tab.
- **Placement:** the single "Import" tab lives in the *Manage* sidebar group
  (it now creates campaigns, so it belongs with the active tools).
- **Halo's inline import bar is removed** and replaced with a short pointer to the
  Import tab.
- **Internal structure: two sub-tabs** inside the Import tab (a segmented control),
  not one merged auto-detecting drop zone. The two activities differ in kind —
  passive data banking versus a live-write build workflow — so each gets its own
  clearly-scoped drop zone. This removes the ambiguity of a single "drop anything"
  zone that might silently start creating live campaigns.

## Design

### 1. Navigation and the `Screen` enum

File: `MerchAds/Views/ContentView.swift`.

- Remove the `.intake` case from `enum Screen`. Keep `.dataImport` as the single
  **"Import"** case.
- Move `.dataImport` out of the *System* sidebar group and into the *Manage*
  group, positioned where `.intake` was (end of the Manage list, after
  `.approvals`). *System* becomes `[.errors, .actions, .audit, .health]`.
- Keep the `.dataImport` title "Import". Icon stays `tray.and.arrow.down`.
- Update `Screen.restored(from:)` to migrate the old raw value:
  `rawValue == "intake"` → `.dataImport` (the same pattern already used for
  `"playbook"` → `.health`). This keeps any persisted sidebar selection, saved
  view, or future deep link to New Designs resolving to the unified tab instead
  of returning nil.
- `detailView` in `ContentView.swift`: `.dataImport` maps to the new container
  `ImportView()`. Remove the `.intake` mapping entirely.
- `CommandPaletteView` and any other consumer that iterates `Screen.allCases`
  updates automatically once `.intake` is gone — no per-file edit needed there,
  but verify during implementation.

### 2. The Import container (segmented control)

File: `MerchAds/Views/ImportView.swift` becomes a **thin container**.

- A `PageHeader(title: "Import", …)` plus a segmented control with two segments:
  **"New Designs"** and **"Sales & History"**.
- The selected segment is remembered with `@AppStorage("import.segment")`,
  defaulting to **New Designs**.
- The container hosts one of two self-contained child views by segment. It holds
  almost no logic of its own beyond the segment state and the cross-route handling
  (section 5).

Layout sketch:

```
┌─ Import ─────────────────────────────────────┐
│  [ New Designs ] [ Sales & History ]          │  segmented control
├───────────────────────────────────────────────┤
│  selected sub-tab: its own drop zone + content │
└───────────────────────────────────────────────┘
```

### 3. "New Designs" sub-tab

- The existing catalogue-export build workflow, unchanged in behavior.
- Extract the current `IntakeView` body into a child view (working name
  `NewDesignsBuildView`). It keeps its own drop zone, file picker, days stepper,
  routing preview, per-design approval, "Build N Approved", "Build All Markets",
  confirmation dialogs, KILL gating, and export adoption. All of that logic is
  reused as-is; only the view's identity changes from a top-level `Screen`
  destination to a hosted sub-tab.
- Its drop zone is scoped to `export_products_*.csv`.

### 4. "Sales & History" sub-tab

- The existing banking view, unchanged in behavior.
- Extract the current `ImportView` body (drop zone → `sales-report --import` with
  the `history-import` fallback, plus the banked-coverage panel and the import
  ledger) into a child view (working name `SalesHistoryImportView`).
- Its drop zone is scoped to data CSVs. The runtime sales-report → console-history
  fallback stays exactly as it is today.

### 5. Soft cross-routing (the "all files" guarantee)

So a file dropped on the wrong sub-tab never dead-ends:

- A small pure classifier decides intake kind from the filename:
  `classifyImport(filename:) -> ImportFileKind` where the kind is
  `.catalogExport` when the lowercased name starts with `export_products`, else
  `.dataCSV`. This is the single source of truth for the `looksLikeCatalogExport`
  logic that both `ImportView` and `IntakeView` currently hold privately.
- **On the Sales & History drop zone:** if a dropped file classifies as
  `.catalogExport`, don't try to bank it. Show a short inline prompt — "That looks
  like a catalogue export. Switch to New Designs?" — with a button that flips the
  segment to New Designs and loads that file into the build workflow.
- **On the New Designs drop zone:** if a dropped file classifies as `.dataCSV`,
  show the mirror prompt — "That looks like a sales report or history file. Switch
  to Sales & History?" — that flips the segment and banks it there.
- The switch carries the already-resolved file URL so the user does not re-drop.

Only the filename prefix is load-bearing here. The catalogue export always arrives
named `export_products_*.csv` from Merch on Demand, and the current code already
relies on that prefix. A renamed export dropped on New Designs still works (that
sub-tab reads it directly); a renamed export dropped on Sales & History would be
treated as a data CSV and fail the bank with a clear error — an acceptable edge
case, noted, not engineered around.

### 6. Organic Halo change

File: `MerchAds/Views/HaloView.swift`.

- Remove the `SalesReportBar` usage (currently `HaloView.swift:53`).
- Replace it with a one-line pointer, e.g. "Sales reports are imported on the
  Import tab." No drop target on Halo.
- `MerchAds/Components/SalesReportBar.swift` is then unused. Delete it to avoid
  dead code (confirm no other references first — current grep shows Halo is the
  only user).

### 7. Help text

File: `MerchAds/Components/ScreenHelp.swift`.

- Merge the two help entries (`.intake` at ~464 and `.dataImport` at ~486) into a
  single `.dataImport` `ScreenHelp` that covers both sub-tabs: what New Designs
  does and that it writes to the live account, and what Sales & History banks and
  why (organic royalty, past-95-day retention).
- Remove the `.intake` case from the switch (required for the file to compile once
  `.intake` leaves the enum).

## Code structure and files touched

- `MerchAds/Views/ImportView.swift` — rewritten as the thin container (segmented
  control + hosts the two child views + cross-route prompts).
- `MerchAds/Views/IntakeView.swift` — its build workflow becomes the
  `NewDesignsBuildView` child. Logic preserved. May be renamed and/or kept in the
  same file; implementation-plan decision.
- New child view for Sales & History (`SalesHistoryImportView`), holding what
  `ImportView` does today.
- `MerchAds/Components/ImportFileKind.swift` (or similar) — the shared
  `classifyImport(filename:)` classifier and the `ImportFileKind` enum.
- `MerchAds/Views/ContentView.swift` — `Screen` enum (remove `.intake`), sidebar
  groups (move `.dataImport` to Manage), `detailView` mapping, `Screen.restored`
  migration.
- `MerchAds/Components/ScreenHelp.swift` — merged help, `.intake` case removed.
- `MerchAds/Views/HaloView.swift` — remove the import bar, add the pointer.
- `MerchAds/Components/SalesReportBar.swift` — delete (unused after Halo change).

## Migration and back-compat

- The `Screen.restored` mapping is the one migration that matters: any stored
  `"intake"` selection or saved view resolves to `.dataImport`.
- `@AppStorage("intake.days")` inside the build workflow is unchanged and keeps
  working. A new `@AppStorage("import.segment")` remembers the sub-tab.
- No engine, database, or `appctl.py` contract changes. Every underlying command
  (`import-preview`, `import-apply`, `adopt-export`, `sales-report --import`,
  `history-import`, `sales-history`) is called exactly as before.

## Testing

- Unit-test `classifyImport(filename:)`: `export_products_*.csv` → `.catalogExport`;
  `SALES_REPORT-*.csv` and other names → `.dataCSV`; case-insensitivity.
- Unit-test the `Screen.restored` migration: `"intake"` → `.dataImport`, and an
  unknown raw value still returns nil.
- Update the existing screen-enumeration tests (`ScreenHelpTests`,
  `RouteAndSavedViewTests`, and any test referencing `.intake`) so they pass with
  `.intake` removed. Every remaining `Screen` still has help.
- Manual check on the running app: one "Import" entry under Manage; each sub-tab's
  drop zone banks/builds correctly; a catalogue export dropped on Sales & History
  offers the switch; a data CSV dropped on New Designs offers the switch; Halo has
  no drop bar; a build still produces a `writes_log` row.

## Out of scope / non-goals

- No change to how any file is parsed, banked, or built — only where the user drops
  it and how the app routes it.
- No new import types. The three existing file shapes are the whole surface.
- No change to the nightly job or any engine behavior.

## Success criteria

- The sidebar has exactly one import-related entry: "Import", under Manage.
- New Designs is reachable only inside the Import tab, as a sub-tab.
- Dropping any of the three file types anywhere in the Import tab does the right
  thing, including a corrective switch when dropped on the other sub-tab.
- Organic Halo has no inline import bar and points to the Import tab.
- All existing build capabilities (per-market build, Build All Markets, adopt,
  KILL gating, confirmations) are preserved.
- Old New Designs selections/links resolve to the unified tab without error.
