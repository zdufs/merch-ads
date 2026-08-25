import SwiftUI

struct SettingsView: View {
    /// `false` = the ⌘, Settings scene, which sizes itself to a fixed 520pt column.
    /// `true` = embedded as the sidebar's Settings tab, where it fills the detail
    /// pane (its own PageHeader, a scroll, and a readable capped content column).
    var embedded = false

    @Environment(AppState.self) private var appState
    @AppStorage(AppSettings.engineRootKey) private var engineRootPath = ""
    @AppStorage(AppSettings.pythonPathKey) private var pythonPath = ""
    @AppStorage(AppSettings.alwaysConfirmKey) private var alwaysConfirm = false
    @AppStorage(AppSettings.fastBridgeKey) private var fastBridge = true
    @AppStorage(AppSettings.showMenuBarKey) private var showMenuBar = true
    @AppStorage(AppSettings.appearanceKey) private var appearanceRaw = AppAppearance.system.rawValue
    @State private var showingFolderPicker = false
    @State private var targetCeiling = ""
    @State private var keywordCeiling = ""
    @State private var budgetCeiling = ""
    @State private var savingCeiling = false
    @State private var ceilingError: String?   // never fail a bid-safety cap silently
    @State private var savedCeiling: String?   // what the engine confirmed it stored

    private var effectiveRoot: String {
        engineRootPath.isEmpty ? AppSettings.defaultEngineRoot : engineRootPath
    }

    /// The data folder is judged on the DATABASES, not on appctl.py.
    ///
    /// appctl.py now ships inside the app, so its absence from this folder says
    /// nothing at all — while a folder with no `ads_data.sqlite` is the one
    /// mistake that makes every screen read empty.
    private var databasesFound: Bool {
        let root = AppSettings.dataRoot(under: URL(fileURLWithPath: effectiveRoot))
        return FileManager.default.fileExists(
            atPath: root.appendingPathComponent("ads_data.sqlite").path)
    }

    /// The folder the badge above is actually vouching for.
    private var resolvedDataRoot: String {
        AppSettings.dataRoot(under: URL(fileURLWithPath: effectiveRoot)).path
    }

    /// Which engine the app will actually run — bundled, or this folder's.
    ///
    /// It used to append "/engine" to whatever the field held, so a field
    /// already pointing at the engine folder printed a doubled path that
    /// exists nowhere. Ask the bridge where it found appctl.py instead.
    private var engineSourceLabel: String {
        if AppSettings.bundledEngineRoot != nil { return "inside the app" }
        guard let appctl = PythonBridge.appctlURL(under: URL(fileURLWithPath: effectiveRoot))
        else { return "not found" }
        return appctl.deletingLastPathComponent().path
    }

    /// Which interpreter the app will actually run, in the same order the bridge
    /// picks it: the Settings override, then the bundled one, then the shell.
    private var pythonSourceLabel: String {
        if !pythonPath.isEmpty { return pythonPath }
        if AppSettings.bundledPython != nil { return "inside the app" }
        return PythonBridge.loginShellPython ?? "not found"
    }

    var body: some View {
        Group {
            if embedded {
                VStack(spacing: 0) {
                    PageHeader(title: "Settings",
                               subtitle: "App and engine configuration",
                               help: .settings)
                    Divider()
                    ScrollView {
                        // Cap the form at a readable width and hug the leading
                        // edge. A trailing Spacer does the left-align, instead of
                        // wrapping the capped column in a maxWidth:.infinity frame
                        // (that self-referential nesting fed a layout loop).
                        HStack(alignment: .top, spacing: 0) {
                            sectionStack
                                .frame(maxWidth: 720, alignment: .leading)
                                .padding(Layout.Spacing.lg)
                            Spacer(minLength: 0)
                        }
                    }
                }
                // Opt OUT of the app-wide text-selection overlay. Embedded inside
                // ContentView's `.textSelection(.enabled)`, this screen's long,
                // capped content drove SwiftUI's SelectionOverlay into a layout-
                // invalidation loop that pegged the main thread — a beachball on
                // the Settings tab. TextFields stay fully editable; only static
                // labels lose word-selection here. The ⌘, Settings window keeps
                // selection: it is a separate scene and does not loop. (Same
                // opt-out the command palette and rule list already use.)
                .textSelection(.disabled)
            } else {
                ScrollView {
                    sectionStack
                        .padding(Layout.Spacing.lg)
                }
                .frame(width: 520)
                .fixedSize(horizontal: false, vertical: true)
            }
        }
        .background(Theme.Colors.canvas)
        .tint(Theme.Colors.accent)
        // A separate scene, so it doesn't inherit ContentView's setting — apply
        // the same Appearance preference here directly.
        .preferredColorScheme(AppAppearance.stored(appearanceRaw).colorScheme)
        .onChange(of: engineRootPath) { appState.engineSettingsDidChange() }
        .onChange(of: pythonPath) { appState.engineSettingsDidChange() }
        .fileImporter(isPresented: $showingFolderPicker,
                      allowedContentTypes: [.folder]) { result in
            if case .success(let url) = result {
                engineRootPath = url.path
            }
        }
    }

    /// Every settings card, shared by the scene and the embedded tab.
    @ViewBuilder private var sectionStack: some View {
        LazyVStack(alignment: .leading, spacing: Layout.Spacing.lg) {
            // The field holds the ENGINE ROOT, which is usually the `engine`
            // subfolder and holds no database and no .env. Calling it the data
            // folder put a path with neither beside a green "Databases: Found",
            // at the exact moment someone is working out where their data
            // lives. The data folder is resolved from it and shown below.
            settingsSection(title: "Engine folder", subtitle: "where appctl.py and the data folder are found") {
                HStack {
                    TextField("Engine folder", text: $engineRootPath,
                              prompt: Text(AppSettings.defaultEngineRoot))
                    Button("Choose…") { showingFolderPicker = true }
                }
                LabeledContent("Data folder", value: resolvedDataRoot)
                LabeledContent("Databases") {
                    StatusBadge(text: databasesFound ? "Found" : "Not found",
                                symbol: databasesFound ? "checkmark.circle.fill" : "xmark.circle.fill",
                                tint: databasesFound
                                    ? Theme.Colors.positive : Theme.Colors.critical)
                }
                Text("The ads_data*.sqlite databases and .env live in the data folder — this folder "
                     + "or the one above it, whichever actually holds them. Neither is touched by an "
                     + "app update, and the app never reads .env — Python does.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            settingsSection(title: "Engine", subtitle: "the Python that does the work") {
                LabeledContent("Modules", value: engineSourceLabel)
                LabeledContent("Interpreter", value: pythonSourceLabel)
                TextField("python3 path", text: $pythonPath,
                          prompt: Text(AppSettings.bundledPython == nil
                                       ? "auto — resolve via login shell"
                                       : "auto — use the Python inside the app"))
                Text(AppSettings.bundledEngineRoot == nil
                     ? "This build ships no engine, so it runs the appctl.py in the data folder with a python3 "
                       + "from your login shell. Both must be present."
                     : "The engine and its Python ship inside the app, so no Homebrew, pip or repo checkout is "
                       + "needed. Fill the field above only to force a different python3.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            settingsSection(title: "Actions", subtitle: "confirmation policy") {
                Toggle("Always confirm actions", isOn: $alwaysConfirm)
                Text("Off: single small actions (one pause, one bid, one undo) apply in one click; bulk actions and negatives always ask. On: every action asks first.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            settingsSection(title: "Max bid ceiling",
                            subtitle: "hard cap on every bid written for \(appState.selectedMarket)") {
                // Stacked label→field rows (the classic macOS settings layout).
                // Laid out horizontally the three labels could not fit the 520pt
                // column and wrapped one character per line.
                LabeledContent("Target bid") {
                    TextField("none", text: $targetCeiling).frame(width: 100)
                        .multilineTextAlignment(.trailing)
                }
                LabeledContent("Keyword bid") {
                    TextField("none", text: $keywordCeiling).frame(width: 100)
                        .multilineTextAlignment(.trailing)
                }
                LabeledContent("Daily budget") {
                    TextField("none", text: $budgetCeiling).frame(width: 100)
                        .multilineTextAlignment(.trailing)
                }
                HStack(spacing: Layout.Spacing.sm) {
                    Button("Save") { Task { await saveCeiling() } }
                        .disabled(savingCeiling)
                    if savingCeiling {
                        ProgressView().controlSize(.small)
                    } else if savedCeiling == ceilingFingerprint {
                        Label("Saved", systemImage: "checkmark.circle.fill")
                            .font(.caption)
                            .foregroundStyle(Theme.Colors.positive)
                    }
                    Spacer()
                }
                ActionErrorBar(message: $ceilingError)
                Text("Caps every bid and DAILY campaign budget MerchAds writes for this market — manual, bulk, and nightly automation. A clamped write is applied at the ceiling and shown as “adjusted” in the Audit trail. Blank = no ceiling. Amazon's $0.02 bid minimum still applies underneath.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .task(id: appState.selectedMarket) { await loadCeiling() }

            settingsSection(title: "Appearance", subtitle: "light, dark, or follow the system") {
                Picker("Appearance", selection: $appearanceRaw) {
                    ForEach(AppAppearance.allCases) { mode in
                        Label(mode.label, systemImage: mode.symbol).tag(mode.rawValue)
                    }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                Text("System follows your macOS Light/Dark setting. Light or Dark pins the app to that mode regardless of the system.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            settingsSection(title: "App", subtitle: "runtime behavior") {
                Toggle("Fast bridge (persistent Python worker)", isOn: $fastBridge)
                Toggle("Show status in the menu bar", isOn: $showMenuBar)
                Text("The fast bridge keeps one appctl serve process per market alive so screens load instantly; turn it off if reads ever behave oddly (every call then spawns a fresh process).")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    /// The three fields as one value, so the "Saved" badge disappears the moment
    /// the operator edits any one — no onChange bookkeeping needed.
    private var ceilingFingerprint: String { "\(targetCeiling)|\(keywordCeiling)|\(budgetCeiling)" }

    private func loadCeiling() async {
        savedCeiling = nil
        do {
            let bridge = try appState.makeBridge()
            let r = try await bridge.call(MaxBidResponse.self, ["maxbid"],
                                          market: appState.selectedMarket)
            targetCeiling = r.target ?? ""
            keywordCeiling = r.keyword ?? ""
            budgetCeiling = r.budget ?? ""
            ceilingError = nil
        } catch {
            // Say what the fields do NOT reflect — a bid cap you can't read is a
            // bid cap you can't trust.
            ceilingError = "Couldn't read the ceiling for \(appState.selectedMarket): \(error.localizedDescription). The fields below may not match the engine."
        }
    }

    /// Blank clears that surface's ceiling engine-side; anything else must be a
    /// positive number before it is sent.
    private func parseCeiling(_ text: String, label: String) throws -> String {
        let trimmed = text.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else { return "" }
        guard let value = Double(trimmed.replacingOccurrences(of: ",", with: ".")),
              value.isFinite, value > 0 else {
            throw CeilingInputError.invalid(label: label, text: trimmed)
        }
        return String(format: "%.2f", value)
    }

    private func saveCeiling() async {
        savedCeiling = nil
        let target: String
        let keyword: String
        let budget: String
        do {
            target = try parseCeiling(targetCeiling, label: "Target bid")
            keyword = try parseCeiling(keywordCeiling, label: "Keyword bid")
            budget = try parseCeiling(budgetCeiling, label: "Daily budget")
        } catch {
            ceilingError = error.localizedDescription
            return
        }
        savingCeiling = true
        defer { savingCeiling = false }
        do {
            let bridge = try appState.makeBridge()
            let r = try await bridge.call(MaxBidResponse.self,
                                          ["maxbid", "--set", "--target", target,
                                           "--keyword", keyword, "--budget", budget],
                                          market: appState.selectedMarket, preferWorker: false)
            // Show what the engine actually holds now, not what was typed.
            targetCeiling = r.target ?? ""
            keywordCeiling = r.keyword ?? ""
            budgetCeiling = r.budget ?? ""
            ceilingError = nil
            savedCeiling = ceilingFingerprint
        } catch {
            // Do NOT reload — reloading would repopulate the fields from disk and
            // hide the fact that the ceiling was never written.
            ceilingError = "Ceiling NOT saved for \(appState.selectedMarket): \(error.localizedDescription)"
        }
    }

    private func settingsSection<Content: View>(title: String, subtitle: String,
                                                @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.sm) {
            SectionHeader(title: title, subtitle: subtitle)
            content()
        }
        .padding(Layout.Spacing.md)
        .mdCard()
    }
}

/// Local input validation for the max-bid ceiling fields.
private enum CeilingInputError: LocalizedError {
    case invalid(label: String, text: String)

    var errorDescription: String? {
        switch self {
        case .invalid(let label, let text):
            "\(label) “\(text)” is not a positive number. Enter an amount like 1.25, or leave it blank for no ceiling."
        }
    }
}

#Preview {
    SettingsView()
        .environment(AppState())
}
