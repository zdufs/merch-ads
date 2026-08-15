# Unified Import Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fold the app's three file-import surfaces into one "Import" tab with two sub-tabs — New Designs (catalogue-export build workflow) and Sales & History (sales-report + console-history banking) — with a shared classifier that cross-routes a file dropped on the wrong sub-tab.

**Architecture:** A thin `ImportHubView` container hosts a segmented control and renders one of two self-contained child views. The existing `ImportView` (banking) and `IntakeView` (build) are renamed to those children and gain three inputs each for cross-routing. The `.intake` sidebar screen is removed; `.dataImport` moves to the Manage group and points at the container. No engine, database, or `appctl.py` changes.

**Tech Stack:** SwiftUI (macOS 14+), Swift Testing / XCTest under target `MerchAdsTests`, `xcodebuild`. Xcode project uses file-system-synchronized groups (objectVersion 77) — new files auto-join the target.

## Global Constraints

- App-only change. Do NOT edit any `.py`, SQLite, or `appctl.py` contract.
- Follow existing SwiftUI patterns in `MerchAds/` (Theme, Layout, PageHeader, `appState.makeBridge()`, `bridge.call(...)`).
- New Swift files auto-join the target via synchronized groups — no `project.pbxproj` edits.
- Work on branch `feat/unified-import-tab` (already created; the design spec is its first commit).
- Commit messages: plain language, short sentences. Use `git commit -F -` heredoc for any message containing parentheses/apostrophes.
- Tests run with: `xcodebuild test -project MerchAds.xcodeproj -scheme MerchAds -destination 'platform=macOS' -derivedDataPath /tmp/merchads-derived`. A single class: append `-only-testing:MerchAdsTests/<ClassName>`.
- Fast compile check: `xcodebuild -project MerchAds.xcodeproj -scheme MerchAds -configuration Debug -derivedDataPath /tmp/merchads-derived build`.
- Do NOT run any of these while `run_scheduled.sh` is running (it edits nothing Swift, but keep the machine free). Check `ps ax | grep run_scheduled`.
- STANDING RULE: the turn that finishes this is not done until `bash scripts/package_app.sh --install` has run and the app is relaunched from `/Applications`. That is Task 6.
- Cross-route input names are fixed and identical across tasks: `incomingFile: URL?`, `onConsumeIncoming: () -> Void`, and the misroute callback (`onMisroutedExport`/`onMisroutedDataCSV`). The classifier is `ImportFileKind.classify(filename:) -> ImportFileKind`.

---

### Task 1: File-type classifier

**Files:**
- Create: `MerchAds/Components/ImportFileKind.swift`
- Test: `MerchAdsTests/ImportFileKindTests.swift`

**Interfaces:**
- Consumes: nothing.
- Produces: `enum ImportFileKind { case catalogExport, dataCSV }` and `static func ImportFileKind.classify(filename: String) -> ImportFileKind`. Tasks 2–4 call it.

- [ ] **Step 1: Write the failing test**

Create `MerchAdsTests/ImportFileKindTests.swift`:

```swift
import XCTest
@testable import Merch_Ads

final class ImportFileKindTests: XCTestCase {
    func testCatalogExportByPrefix() {
        XCTAssertEqual(ImportFileKind.classify(filename: "export_products_2026-08-04T16_30_41.366Z.csv"), .catalogExport)
    }
    func testCatalogExportIsCaseInsensitive() {
        XCTAssertEqual(ImportFileKind.classify(filename: "EXPORT_PRODUCTS_x.csv"), .catalogExport)
    }
    func testSalesReportIsDataCSV() {
        XCTAssertEqual(ImportFileKind.classify(filename: "SALES_REPORT-8_1_26-8_12_26.csv"), .dataCSV)
    }
    func testConsoleHistoryIsDataCSV() {
        XCTAssertEqual(ImportFileKind.classify(filename: "Sponsored Products Search term report.csv"), .dataCSV)
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `xcodebuild test -project MerchAds.xcodeproj -scheme MerchAds -destination 'platform=macOS' -derivedDataPath /tmp/merchads-derived -only-testing:MerchAdsTests/ImportFileKindTests`
Expected: FAIL to compile — `ImportFileKind` is undefined.

- [ ] **Step 3: Write minimal implementation**

Create `MerchAds/Components/ImportFileKind.swift`:

```swift
import Foundation

/// What kind of file the user dropped, decided from the filename alone.
/// The catalogue export always arrives named `export_products_*.csv` from
/// Merch on Demand, and the rest of the app has always relied on that prefix.
/// This is the single source of truth for that decision — both import
/// sub-tabs use it to cross-route a file dropped on the wrong one.
enum ImportFileKind: Equatable {
    case catalogExport   // export_products_*.csv → New Designs build workflow
    case dataCSV         // Merch sales report or console monthly history → banked

    static func classify(filename: String) -> ImportFileKind {
        filename.lowercased().hasPrefix("export_products") ? .catalogExport : .dataCSV
    }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `xcodebuild test -project MerchAds.xcodeproj -scheme MerchAds -destination 'platform=macOS' -derivedDataPath /tmp/merchads-derived -only-testing:MerchAdsTests/ImportFileKindTests`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add MerchAds/Components/ImportFileKind.swift MerchAdsTests/ImportFileKindTests.swift
git commit -m "Add ImportFileKind classifier for import routing"
```

---

### Task 2: Sales & History sub-tab (rename + cross-route inputs)

Rename the current banking view (`ImportView` → `SalesHistoryImportView`) and give it the three cross-route inputs. It stays wired to the `.dataImport` screen for now, so the app keeps working after this task.

**Files:**
- Rename: `MerchAds/Views/ImportView.swift` → `MerchAds/Views/SalesHistoryImportView.swift` (use `git mv`)
- Modify: the renamed file (struct name + inputs + drop handling + incoming loader)
- Modify: `MerchAds/Views/ContentView.swift:284-285` (`.dataImport` maps to `SalesHistoryImportView()`)

**Interfaces:**
- Consumes: `ImportFileKind.classify(filename:)` from Task 1.
- Produces: `struct SalesHistoryImportView` with `var incomingFile: URL? = nil`, `var onConsumeIncoming: () -> Void = {}`, `var onMisroutedExport: (URL) -> Void = { _ in }`. Task 4 constructs it with these.

- [ ] **Step 1: Rename the file and struct**

```bash
git mv MerchAds/Views/ImportView.swift MerchAds/Views/SalesHistoryImportView.swift
```

In the renamed file, rename `struct ImportView: View` to `struct SalesHistoryImportView: View`.

- [ ] **Step 2: Add the three inputs**

Directly under `@Environment(AppState.self) private var appState`, add:

```swift
    /// A file handed over from the New Designs sub-tab (a data CSV dropped there
    /// by mistake). Non-nil only when this sub-tab is the cross-route target.
    var incomingFile: URL? = nil
    /// Call once the incoming file has been banked, so the container clears it.
    var onConsumeIncoming: () -> Void = {}
    /// A catalogue export was dropped here — hand it back so the container can
    /// switch to New Designs and load it there.
    var onMisroutedExport: (URL) -> Void = { _ in }
```

- [ ] **Step 3: Route a misdropped export instead of banking it**

Replace the existing `handle(_:)` and delete the now-unused `looksLikeCatalogExport(_:)`:

```swift
    /// Route by what the file actually is. A catalogue export is not banked here —
    /// it belongs to the build workflow, so hand it back to the container.
    private func handle(_ url: URL) async {
        errorText = nil
        status = nil
        if ImportFileKind.classify(filename: url.lastPathComponent) == .catalogExport {
            onMisroutedExport(url)
            // Pre-container fallback hint (harmless once cross-route switches away).
            statusIsWarning = true
            status = "That looks like a catalogue export — open New Designs to route and build it."
            return
        }
        await importSalesReport(url)
    }
```

- [ ] **Step 4: Bank an incoming cross-routed file**

Add this modifier to the `body`'s root `VStack`, next to the existing `.task(id: appState.viewKey)`:

```swift
        .task(id: incomingFile) {
            guard let url = incomingFile else { return }
            await importSalesReport(url)
            onConsumeIncoming()
        }
```

- [ ] **Step 5: Point the screen at the renamed view**

In `MerchAds/Views/ContentView.swift`, change the `.dataImport` case in `ScreenDetail.body`:

```swift
        case .dataImport:
            SalesHistoryImportView()
```

- [ ] **Step 6: Build to verify it compiles**

Run: `xcodebuild -project MerchAds.xcodeproj -scheme MerchAds -configuration Debug -derivedDataPath /tmp/merchads-derived build`
Expected: BUILD SUCCEEDED.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -F - <<'EOF'
Rename ImportView to SalesHistoryImportView with cross-route inputs

The banking view (sales report + console history) becomes a sub-tab child.
It now hands a misdropped catalogue export back to its container and can
bank a file handed to it. Still wired to the Import screen for now.
EOF
```

---

### Task 3: New Designs sub-tab (rename + cross-route inputs)

Rename the build workflow (`IntakeView` → `NewDesignsBuildView`) and give it the mirror inputs. It stays wired to the `.intake` screen for now.

**Files:**
- Rename: `MerchAds/Views/IntakeView.swift` → `MerchAds/Views/NewDesignsBuildView.swift` (use `git mv`)
- Modify: the renamed file (struct name, `#Preview`, inputs, drop handling, incoming loader)
- Modify: `MerchAds/Views/ContentView.swift:298-299` (`.intake` maps to `NewDesignsBuildView()`)

**Interfaces:**
- Consumes: `ImportFileKind.classify(filename:)` from Task 1.
- Produces: `struct NewDesignsBuildView` with `var incomingFile: URL? = nil`, `var onConsumeIncoming: () -> Void = {}`, `var onMisroutedDataCSV: (URL) -> Void = { _ in }`. Task 4 constructs it with these.

- [ ] **Step 1: Rename the file and struct**

```bash
git mv MerchAds/Views/IntakeView.swift MerchAds/Views/NewDesignsBuildView.swift
```

In the renamed file, rename `struct IntakeView: View` to `struct NewDesignsBuildView: View`, and in the `#Preview` block rename `IntakeView()` to `NewDesignsBuildView()`.

- [ ] **Step 2: Add the three inputs**

Directly under `@Environment(AppState.self) private var appState`, add:

```swift
    /// A catalogue export handed over from the Sales & History sub-tab. Non-nil
    /// only when this sub-tab is the cross-route target.
    var incomingFile: URL? = nil
    /// Call once the incoming export has been loaded, so the container clears it.
    var onConsumeIncoming: () -> Void = {}
    /// A data CSV (sales report / history) was dropped here — hand it back so the
    /// container can switch to Sales & History and bank it there.
    var onMisroutedDataCSV: (URL) -> Void = { _ in }
```

- [ ] **Step 3: Route a misdropped data CSV instead of previewing it**

In `dropTarget`'s `.dropDestination`, after the `isAcceptedExport` guard and before `csvURL = url`, insert the classify branch:

```swift
            guard Self.isAcceptedExport(url) else {
                rejectDrop("“\(url.lastPathComponent)” isn't a text export. Drop a .csv (or .txt) products export.")
                return false
            }
            if ImportFileKind.classify(filename: url.lastPathComponent) == .dataCSV {
                onMisroutedDataCSV(url)
                rejectDrop("That looks like a sales report — banking it under Sales & History.")
                return false
            }
            dropRejection = nil
            csvURL = url
            Task { await loadPreview() }
            return true
```

- [ ] **Step 4: Load an incoming cross-routed export**

Add this modifier to the `body`'s root `VStack`, next to the existing `.onChange(of: appState.selectedMarket)`:

```swift
        .task(id: incomingFile) {
            guard let url = incomingFile else { return }
            csvURL = url
            await loadPreview()
            onConsumeIncoming()
        }
```

- [ ] **Step 5: Point the screen at the renamed view**

In `MerchAds/Views/ContentView.swift`, change the `.intake` case in `ScreenDetail.body`:

```swift
        case .intake:
            NewDesignsBuildView()
```

- [ ] **Step 6: Build to verify it compiles**

Run: `xcodebuild -project MerchAds.xcodeproj -scheme MerchAds -configuration Debug -derivedDataPath /tmp/merchads-derived build`
Expected: BUILD SUCCEEDED.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -F - <<'EOF'
Rename IntakeView to NewDesignsBuildView with cross-route inputs

The catalogue-export build workflow becomes a sub-tab child. It now hands a
misdropped data CSV back to its container and can load an export handed to
it. Still wired to the New Designs screen for now.
EOF
```

---

### Task 4: Import container + navigation flip

Create the `ImportHubView` container, remove the `.intake` screen, move `.dataImport` to Manage, and merge the help text. After this task the feature is live: one Import tab with two sub-tabs and working cross-routing.

**Files:**
- Create: `MerchAds/Views/ImportHubView.swift`
- Modify: `MerchAds/Views/ContentView.swift` (Screen enum, title/icon/blurb, sidebar groups, detail mapping, `restored`)
- Modify: `MerchAds/Components/ScreenHelp.swift` (merge `.dataImport`, remove `.intake`)
- Test: `MerchAdsTests/RouteAndSavedViewTests.swift` (append restore-migration test)

**Interfaces:**
- Consumes: `NewDesignsBuildView` (Task 3) and `SalesHistoryImportView` (Task 2) with their three inputs.
- Produces: `struct ImportHubView` mapped from `Screen.dataImport`; `enum ImportSegment: String { case newDesigns, salesHistory }`.

- [ ] **Step 1: Write the failing restore-migration test**

Append to `MerchAdsTests/RouteAndSavedViewTests.swift` (inside its test class):

```swift
    func testIntakeRawValueRestoresToImport() {
        XCTAssertEqual(Screen.restored(from: "intake"), .dataImport)
    }
    func testPlaybookRawValueStillRestoresToHealth() {
        XCTAssertEqual(Screen.restored(from: "playbook"), .health)
    }
    func testUnknownRawValueRestoresToNil() {
        XCTAssertNil(Screen.restored(from: "no-such-screen"))
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `xcodebuild test -project MerchAds.xcodeproj -scheme MerchAds -destination 'platform=macOS' -derivedDataPath /tmp/merchads-derived -only-testing:MerchAdsTests/RouteAndSavedViewTests`
Expected: FAIL — `testIntakeRawValueRestoresToImport` returns `.intake`, not `.dataImport` (migration not added yet).

- [ ] **Step 3: Create the container**

Create `MerchAds/Views/ImportHubView.swift`:

```swift
import SwiftUI

/// Which sub-tab of the Import screen is showing.
enum ImportSegment: String, CaseIterable {
    case newDesigns, salesHistory
    var label: String {
        switch self {
        case .newDesigns: "New Designs"
        case .salesHistory: "Sales & History"
        }
    }
}

/// One Import tab for every droppable file. A segmented control switches
/// between the New Designs build workflow and the Sales & History banking view.
/// Each child owns its drop zone; a file dropped on the wrong one is handed back
/// here, which flips the segment and loads it in the right child.
struct ImportHubView: View {
    @Environment(AppState.self) private var appState
    @AppStorage("import.segment") private var segmentRaw = ImportSegment.newDesigns.rawValue
    /// A file handed from one sub-tab to the other. It targets whichever segment
    /// is selected after the flip, so only the visible child ever consumes it.
    @State private var incoming: URL?

    private var segment: ImportSegment { ImportSegment(rawValue: segmentRaw) ?? .newDesigns }

    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "Import", subtitle: appState.selectedMarket, help: .dataImport)
            Picker("Import section", selection: Binding(
                get: { segment },
                set: { segmentRaw = $0.rawValue })) {
                ForEach(ImportSegment.allCases, id: \.self) { Text($0.label).tag($0) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .padding(.horizontal, Layout.Spacing.lg)
            .padding(.vertical, Layout.Spacing.sm)
            Divider()
            switch segment {
            case .newDesigns:
                NewDesignsBuildView(
                    incomingFile: incoming,
                    onConsumeIncoming: { incoming = nil },
                    onMisroutedDataCSV: { url in
                        incoming = url
                        segmentRaw = ImportSegment.salesHistory.rawValue
                    })
            case .salesHistory:
                SalesHistoryImportView(
                    incomingFile: incoming,
                    onConsumeIncoming: { incoming = nil },
                    onMisroutedExport: { url in
                        incoming = url
                        segmentRaw = ImportSegment.newDesigns.rawValue
                    })
            }
        }
        .background(Theme.Colors.canvas)
        .navigationTitle("Import")
    }
}

#Preview {
    ImportHubView()
        .environment(AppState())
}
```

- [ ] **Step 4: Update the Screen enum, sidebar, detail mapping, and restore migration**

In `MerchAds/Views/ContentView.swift`:

1. Remove `intake` from the case list (line ~8):
```swift
    case actions, approvals, harvest, dataImport, audit, errors
```
2. Delete the `.intake` lines from `title`, `icon`, and `blurb`.
3. Replace the `.dataImport` `blurb` with the merged copy:
```swift
        case .dataImport:
            "Bring any file in from one place. New Designs routes a catalogue export into Lottery and Scavenger campaigns; Sales & History banks the Merch sales report and the console monthly export, and shows what history is covered."
```
4. Update `restored(from:)`:
```swift
    static func restored(from rawValue: String) -> Screen? {
        if rawValue == "playbook" { return .health }
        if rawValue == "intake" { return .dataImport }   // New Designs folded into Import
        return Screen(rawValue: rawValue)
    }
```
5. Move `.dataImport` into the Manage group and drop it from System (the `sidebarGroup` calls):
```swift
                    sidebarGroup("Manage", [.campaigns, .targets, .watchlist, .rules, .strategyBuilder,
                                            .liveStatus, .killList, .harvest, .approvals, .dataImport])
                    sidebarGroup("Insights", [.profit, .crossPurchase, .accumulatedAsins,
                                              .accumulatedKeywords, .bidReport, .reports,
                                              .demandFeed, .seasonal, .halo])
                    sidebarGroup("System", [.errors, .actions, .audit, .health])
```
6. In `ScreenDetail.body`: delete the `case .intake:` block, and change `.dataImport` to the container:
```swift
        case .dataImport:
            ImportHubView()
```

- [ ] **Step 5: Merge the help text**

In `MerchAds/Components/ScreenHelp.swift`: delete the entire `case .intake:` block, and replace the `.dataImport` `ScreenHelp(...)` with:

```swift
        case .dataImport:
            ScreenHelp(
                summary: """
                One Import tab for every file you drop. New Designs builds campaigns \
                from a catalogue export; Sales & History banks the reports the engine \
                reads.
                """,
                source: """
                The catalogue export (export_products_*.csv), the dated Merch sales \
                report, and the monthly history export from the ads console. Each \
                sub-tab recognizes its own file, and a file dropped on the wrong one \
                is offered to the other.
                """,
                steps: [
                    "New Designs: drop export_products_*.csv, set the recency window, tick what to build, and press Build. There is a build-everywhere option for all markets.",
                    "Sales & History: drop the sales report or the console monthly export. Read the coverage section for banked days and gaps; the ledger lists every import.",
                ],
                notes: [
                    "Building in New Designs writes to the live account. Banking in Sales & History does not.",
                    "The Merch sales report is the only source of organic royalty. The Ads API reports ad-attributed sales only.",
                    "The console monthly export is the only way past Amazon's ~95-day retention. Once banked, it is the only copy.",
                    "Every import ADDS to the history instead of replacing it.",
                ])
```

- [ ] **Step 6: Run the migration test to verify it passes**

Run: `xcodebuild test -project MerchAds.xcodeproj -scheme MerchAds -destination 'platform=macOS' -derivedDataPath /tmp/merchads-derived -only-testing:MerchAdsTests/RouteAndSavedViewTests`
Expected: PASS (including the three new cases).

- [ ] **Step 7: Build the whole app**

Run: `xcodebuild -project MerchAds.xcodeproj -scheme MerchAds -configuration Debug -derivedDataPath /tmp/merchads-derived build`
Expected: BUILD SUCCEEDED (the `.intake` enum case is gone; `ScreenHelp` and `CommandPaletteView` iterate `Screen.allCases` and adjust automatically).

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -F - <<'EOF'
Add ImportHubView and fold New Designs into the Import tab

One Import tab now hosts two sub-tabs (New Designs, Sales & History) with a
segmented control and cross-routing. The .intake screen is removed and old
"intake" links restore to Import. Import moves to the Manage sidebar group.
Help text merged.
EOF
```

---

### Task 5: Remove Halo's inline import bar

**Files:**
- Modify: `MerchAds/Views/HaloView.swift:53-55` (replace `SalesReportBar` with a pointer)
- Delete: `MerchAds/Components/SalesReportBar.swift`

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing. `SalesReportBar` no longer exists.

- [ ] **Step 1: Confirm SalesReportBar has no other users**

Run: `grep -rn "SalesReportBar" MerchAds/`
Expected: only `HaloView.swift:53` and the component's own file. If anything else appears, stop and reassess.

- [ ] **Step 2: Replace the bar with a pointer to the Import tab**

In `MerchAds/Views/HaloView.swift`, replace the `SalesReportBar { ... }.padding(...)` block (lines ~53-55) with:

```swift
            Button {
                appState.requestedRoute = .screen(.dataImport)
            } label: {
                Label("Sales reports are imported on the Import tab", systemImage: "tray.and.arrow.down")
                    .font(.caption)
            }
            .buttonStyle(.link)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, Layout.Spacing.lg)
            .padding(.bottom, Layout.Spacing.xs)
```

(This uses the same `appState.requestedRoute` deep-link mechanism ContentView already consumes in its `.onChange(of: appState.requestedRoute)`.)

- [ ] **Step 3: Delete the component**

```bash
git rm MerchAds/Components/SalesReportBar.swift
```

- [ ] **Step 4: Build to verify it compiles**

Run: `xcodebuild -project MerchAds.xcodeproj -scheme MerchAds -configuration Debug -derivedDataPath /tmp/merchads-derived build`
Expected: BUILD SUCCEEDED.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -F - <<'EOF'
Remove Halo's inline sales-report bar; point to the Import tab

With one Import tab for all files, Halo no longer carries its own import
bar. It shows a link to the Import tab instead. SalesReportBar deleted.
EOF
```

---

### Task 6: Full test run, install, and manual smoke

**Files:** none (verification + deploy).

- [ ] **Step 1: Run the full test suite**

Run: `xcodebuild test -project MerchAds.xcodeproj -scheme MerchAds -destination 'platform=macOS' -derivedDataPath /tmp/merchads-derived`
Expected: TEST SUCCEEDED. If `ScreenHelpTests` or `RouteAndSavedViewTests` fail, fix the referenced code (do not weaken the assertions).

- [ ] **Step 2: Build Release and install to /Applications**

Run: `bash scripts/package_app.sh --install`
Expected: builds Release and installs `/Applications/Merch Ads.app`.

- [ ] **Step 3: Relaunch from /Applications**

Run: `pkill -x "Merch Ads"; sleep 1; open "/Applications/Merch Ads.app"`

- [ ] **Step 4: Manual smoke check (screenshot the running app)**

Verify each:
- Sidebar shows one import entry, "Import", under Manage. No "New Designs" entry. System group has no "Import".
- Import tab shows a segmented control: New Designs | Sales & History. Last-used segment is remembered across relaunch.
- New Designs sub-tab: dropping `export_products_*.csv` shows the routing preview; Build/Build All Markets still work (or are KILL-gated as before).
- Sales & History sub-tab: dropping a `SALES_REPORT-*.csv` banks it and updates coverage; the ledger lists it.
- Cross-route: drop a catalogue export on Sales & History → it flips to New Designs with the file loaded. Drop a sales report on New Designs → it flips to Sales & History and banks it.
- Organic Halo has no import bar; the "imported on the Import tab" link navigates to Import.

- [ ] **Step 5: Final confirmation**

Confirm the Stop hook (`.claude/hooks/check_app_fresh.sh`) does not report a stale `/Applications` build. The branch `feat/unified-import-tab` now carries the full feature.

---

## Self-Review

**Spec coverage:**
- Navigation / `.intake` removal / Manage placement → Task 4. ✓
- Restore migration (`intake`→`dataImport`) → Task 4 (tested). ✓
- Container + segmented control + `@AppStorage("import.segment")` default New Designs → Task 4. ✓
- New Designs sub-tab (reused build workflow) → Task 3. ✓
- Sales & History sub-tab (reused banking) → Task 2. ✓
- Soft cross-routing via `ImportFileKind.classify` → Task 1 (classifier) + Tasks 2/3 (drop handlers) + Task 4 (container wiring). ✓
- Halo bar removed + pointer → Task 5. ✓
- `SalesReportBar` deleted → Task 5. ✓
- Merged help text → Task 4. ✓
- Tests: classifier + migration → Tasks 1 and 4; existing screen-enumeration tests kept green → Task 6. ✓
- App-only, no engine changes → Global Constraints. ✓

**Type consistency:** `ImportFileKind.classify(filename:)`, `incomingFile: URL?`, `onConsumeIncoming: () -> Void`, `onMisroutedExport`/`onMisroutedDataCSV`, `ImportSegment` (`newDesigns`/`salesHistory`), and `@AppStorage("import.segment")` are used identically across Tasks 1–5.

**Placeholder scan:** none — every step has concrete code or an exact command.
