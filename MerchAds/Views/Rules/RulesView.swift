import SwiftUI

/// Author economics-aware automation rules in a plain-language DSL. Preview is
/// always read-only (proposed changes + per-condition traces); enabled+auto
/// rules run in the nightly job, or apply on demand (KILL + econ-gate enforced
/// engine-side). Syntax highlighting is a follow-up — this ships a plain
/// monospaced editor with server-side validation.
struct RulesView: View {
    @Environment(AppState.self) private var appState

    @State private var rules: [RuleSummary] = []
    @State private var selection: String?
    @State private var draft = RuleDraft()
    @State private var saved = RuleDraft()          // last persisted/loaded state, for dirty checks
    @State private var errors: [RuleValidationError] = []
    @State private var preview: RulePreviewResponse?
    @State private var previewedText: String?       // the exact text the preview describes
    @State private var status: String?
    @State private var busy = false
    @State private var listLoading = false
    @State private var listLoadError: String?
    @State private var ruleLoading = false
    @State private var ruleLoadError: String?
    @State private var ruleLoadID = 0
    @State private var confirmingApply = false
    @AppStorage("rules.tab") private var tab: RulesTab = .mine
    @State private var pendingDelete: String?
    @State private var pendingRename: RenameRequest?
    @State private var pendingSwitch: String??      // discard-unsaved-edits target
    @State private var lastBridgeError: String?     // surfaced by the Library sheet
    // Table cells can't be text-selected on macOS — right-click Copy instead.
    @State private var previewSel = Set<RuleChange.ID>()

    enum RulesTab: String, CaseIterable { case mine = "My Rules", library = "Library" }

    /// My Rules lists EVERY saved rule, running or not, with the off ones last.
    ///
    /// It listed only the enabled ones. The Library's own caption points here
    /// to edit a rule that is switched off — so a disabled rule could be
    /// opened nowhere in the app, and the only way to read its text was to
    /// turn it on, which arms an unreviewed bid-writing rule for that night's
    /// automatic run (found 2026-08-24 on "Nudge starved apparel", saved
    /// disabled precisely so it could be reviewed before it ran). The row
    /// already draws a grey dot and says "disabled".
    private var listedRules: [RuleSummary] { Self.editableList(rules) }

    static func editableList(_ rules: [RuleSummary]) -> [RuleSummary] {
        rules.sorted { a, b in
            if a.enabled != b.enabled { return a.enabled }
            return a.name.localizedCaseInsensitiveCompare(b.name) == .orderedAscending
        }
    }

    private var currency: String? { appState.currentMarket?.currency }

    /// True once the editor holds edits that are not on disk.
    private var isDirty: Bool { draft != saved }

    /// The preview describes text the operator has since edited.
    private var isPreviewStale: Bool {
        preview != nil && previewedText != nil && previewedText != draft.text
    }

    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "Rules", subtitle: "economics-aware automation · preview is always read-only", help: .rules)
            // Left-aligned under the title, like the Approval Queue's picker —
            // a bare .frame(maxWidth:) here centres it in the window.
            HStack(spacing: 0) {
                Picker("", selection: $tab) {
                    ForEach(RulesTab.allCases, id: \.self) { Text($0.rawValue).tag($0) }
                }
                .pickerStyle(.segmented).labelsHidden()
                .frame(maxWidth: 260)
                Spacer(minLength: 0)
            }
            .padding(.horizontal, Layout.Spacing.lg)
            .padding(.bottom, Layout.Spacing.sm)
            Divider()
            if listLoading {
                ProgressView("Loading rules…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let listLoadError {
                ContentUnavailableView {
                    Label("Rules unavailable", systemImage: "exclamationmark.triangle")
                } description: {
                    Text("\(listLoadError) Enabled Auto rules may still be running nightly.")
                } actions: {
                    Button("Retry") { Task { await loadList() } }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if tab == .library {
                RulesLibraryView(
                    kind: appState.currentMarket?.kind ?? "merch",
                    rules: rules,
                    onSetEnabled: { template, enabled in
                        await setTemplateEnabled(template, enabled)
                    },
                    onSetMode: { template, mode in
                        await setTemplateMode(template, mode)
                    },
                    onImport: { template, name, mode, enabled in
                        await importTemplate(template, name: name, mode: mode, enabled: enabled)
                    },
                    onSetRuleEnabled: { r, enabled in await setSavedEnabled(r, enabled) },
                    onSetRuleMode: { r, mode in await setRuleMode(r, mode) })
            } else {
                HSplitView {
                    listPane.frame(minWidth: 200, idealWidth: 240, maxWidth: 320)
                    editorPane.frame(minWidth: 420)
                }
            }
        }
        .background(Theme.Colors.canvas)
        // Keyed on the market: rules, drafts and previews are all per-market, and
        // "Run & apply now" writes to whichever market is selected. A bare .task
        // would leave one market's rule on screen while applying it to another.
        .task(id: appState.viewKey) {
            resetEditor()
            await loadList()
        }
        .onChange(of: draft.text) {
            // The preview table describes the text it was run against; once that
            // text changes the table is history, not a plan.
            errors = []
            if isPreviewStale { preview = nil; previewedText = nil }
        }
    }

    /// Drop every per-market piece of editor state (called on a market switch).
    private func resetEditor() {
        rules = []
        selection = nil
        draft = RuleDraft()
        saved = RuleDraft()
        errors = []
        preview = nil
        previewedText = nil
        status = nil
        listLoadError = nil
        ruleLoadError = nil
        ruleLoadID += 1
        ruleLoading = false
    }

    /// Turn a library template on or off from its card.
    ///
    /// The first time, this writes the template as a rule in REVIEW mode — a
    /// review rule proposes into the Approval Queue and writes nothing, which is
    /// the right default for something enabled with one flick of a switch. After
    /// that it only flips the flag: the operator may have edited the text under
    /// that name, and a toggle must never overwrite their work.
    private func setTemplateEnabled(_ t: RuleTemplate, _ enabled: Bool) async -> String? {
        lastBridgeError = nil
        let existing = rules.first { $0.name == t.name }
        let ok = await withBridge { bridge in
            let text: String
            if existing != nil {
                text = try await bridge.call(Rule.self, ["rules-get", "--rule", t.name],
                                             market: appState.selectedMarket).text
            } else {
                text = t.text
            }
            try await writeRule(bridge, name: t.name, text: text,
                                enabled: enabled, mode: existing?.mode ?? "review")
            await loadList()
            status = enabled
                ? "“\(t.name)” is on · \((existing?.mode ?? "review") == "auto" ? "applies automatically" : "proposes for approval")"
                : "“\(t.name)” is off"
        }
        guard ok else { return lastBridgeError ?? "Could not change that rule." }
        return nil
    }

    /// Switch a library rule between Review and Auto without touching its text.
    private func setTemplateMode(_ t: RuleTemplate, _ mode: String) async -> String? {
        lastBridgeError = nil
        guard let existing = rules.first(where: { $0.name == t.name }) else {
            return "Turn “\(t.name)” on first."
        }
        let ok = await withBridge { bridge in
            let full = try await bridge.call(Rule.self, ["rules-get", "--rule", t.name],
                                             market: appState.selectedMarket)
            try await writeRule(bridge, name: t.name, text: full.text,
                                enabled: existing.enabled, mode: mode)
            await loadList()
            status = mode == "auto"
                ? "“\(t.name)” now applies automatically on the nightly run"
                : "“\(t.name)” now proposes into the Approval Queue"
        }
        guard ok else { return lastBridgeError ?? "Could not change that rule." }
        return nil
    }

    /// Enable/disable a saved (non-template) rule from the Library — returns the
    /// reason on failure so the card can show it. Preserves text and mode.
    private func setSavedEnabled(_ r: RuleSummary, _ enabled: Bool) async -> String? {
        lastBridgeError = nil
        let ok = await withBridge { bridge in
            let full = try await bridge.call(Rule.self, ["rules-get", "--rule", r.name],
                                             market: appState.selectedMarket)
            try await writeRule(bridge, name: r.name, text: full.text,
                                enabled: enabled, mode: r.mode)
            if selection == r.name { draft.enabled = enabled }
            await loadList()
            status = enabled ? "Enabled “\(r.name)”" : "Disabled “\(r.name)”"
        }
        guard ok else { return lastBridgeError ?? "Could not change that rule." }
        return nil
    }

    /// Set a saved rule's mode (review | auto) from the Library. Preserves text.
    private func setRuleMode(_ r: RuleSummary, _ mode: String) async -> String? {
        lastBridgeError = nil
        let ok = await withBridge { bridge in
            let full = try await bridge.call(Rule.self, ["rules-get", "--rule", r.name],
                                             market: appState.selectedMarket)
            try await writeRule(bridge, name: r.name, text: full.text,
                                enabled: r.enabled, mode: mode)
            if selection == r.name { draft.mode = mode }
            await loadList()
            status = mode == "auto"
                ? "“\(r.name)” now applies automatically on the nightly run"
                : "“\(r.name)” now proposes into the Approval Queue"
        }
        guard ok else { return lastBridgeError ?? "Could not change the mode." }
        return nil
    }

    /// The one place a rule is written to disk.
    private func writeRule(_ bridge: PythonBridge, name: String, text: String,
                           enabled: Bool, mode: String) async throws {
        let payload: [String: Any] = ["name": name, "text": text,
                                      "enabled": enabled, "mode": mode]
        _ = try await bridge.call(Rule.self, ["rules-save"], market: appState.selectedMarket,
                                  stdin: try JSONSerialization.data(withJSONObject: payload),
                                  preferWorker: false)
    }

    /// Import a library template. Returns nil on success, else the reason — the
    /// sheet used to report "try a different name" for every failure, including
    /// bridge errors that a different name would never fix.
    private func importTemplate(_ t: RuleTemplate, name: String, mode: String,
                                enabled: Bool) async -> String? {
        // rules-save overwrites by name, so an import must not silently replace a
        // rule the operator has already customised.
        if rules.contains(where: { $0.name == name }) {
            return "“\(name)” already exists — pick another name."
        }
        lastBridgeError = nil
        let ok = await withBridge { bridge in
            try await writeRule(bridge, name: name, text: t.text,
                                enabled: enabled, mode: mode)
            await loadList()
            draft = RuleDraft(name: name, text: t.text, enabled: enabled, mode: mode)
            saved = draft
            selection = name
            tab = .mine
            status = "Copied to “\(name)” · \(enabled ? "on" : "off — preview it first")"
        }
        guard ok else { return lastBridgeError ?? "Import failed." }
        return nil
    }

    // MARK: list

    private var listPane: some View {
        VStack(spacing: 0) {
            HStack {
                Text("Rules").font(.headline)
                Spacer()
                if isDirty {
                    Text("Unsaved")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(Theme.Colors.caution)
                }
                Button {
                    requestSwitch(to: nil)
                } label: { Image(systemName: "plus") }
                    .buttonStyle(.borderless)
                    .help("New rule")
                    .accessibilityLabel("New rule")
            }
            .padding(Layout.Spacing.sm)
            Divider()
            List(selection: $selection) {
                ForEach(listedRules) { r in
                    HStack(spacing: 6) {
                        Circle().fill(r.enabled ? Theme.Colors.positive : Theme.Colors.muted)
                            .frame(width: 7, height: 7)
                        VStack(alignment: .leading, spacing: 1) {
                            Text(r.name).lineLimit(1)
                            Text("\(r.mode)\(r.enabled ? "" : " · disabled")")
                                .font(.caption2).foregroundStyle(.secondary)
                        }
                    }
                    .tag(r.name)
                    .contextMenu {
                        Button("Edit") { requestSwitch(to: r.name) }
                        Button(r.enabled ? "Disable" : "Enable") {
                            Task { await setEnabled(r, !r.enabled) }
                        }
                        Button("Duplicate") { Task { await duplicate(r) } }
                        Divider()
                        Button("Delete…", role: .destructive) { pendingDelete = r.name }
                    }
                    .swipeActions(edge: .trailing) {
                        Button("Delete", role: .destructive) { pendingDelete = r.name }
                    }
                }
            }
            .listStyle(.sidebar)
            // Opt out of the app-wide text selection: here a click picks a rule
            // to edit, and selectable row labels swallow that click.
            .textSelection(.disabled)
            .onChange(of: selection) { old, name in
                guard name != old else { return }
                if isDirty, name != saved.name {
                    // Selection already moved; put it back until the operator decides.
                    selection = old
                    pendingSwitch = .some(name)
                    return
                }
                if let name, name != saved.name {
                    Task { await loadRule(name, revertingTo: old) }
                }
            }
            .confirmationDialog("Delete “\(pendingDelete ?? "")”?",
                                isPresented: Binding(get: { pendingDelete != nil },
                                                     set: { if !$0 { pendingDelete = nil } }),
                                presenting: pendingDelete) { name in
                Button("Delete", role: .destructive) { Task { await deleteRule(name) } }
            } message: { _ in Text("This removes the rule permanently.") }
            .confirmationDialog("Discard unsaved changes to “\(saved.name.isEmpty ? "this rule" : saved.name)”?",
                                isPresented: Binding(get: { pendingSwitch != nil },
                                                     set: { if !$0 { pendingSwitch = nil } }),
                                titleVisibility: .visible,
                                presenting: pendingSwitch) { target in
                Button("Discard Changes", role: .destructive) {
                    pendingSwitch = nil
                    if let name = target {
                        selection = name
                        Task { await loadRule(name, revertingTo: saved.name.isEmpty ? nil : saved.name) }
                    } else {
                        newRule()
                    }
                }
                Button("Keep Editing", role: .cancel) { pendingSwitch = nil }
            } message: { _ in
                Text("The edits in the editor have not been saved. Discarding cannot be undone.")
            }
        }
    }

    /// Move to another rule (or a blank one when `name` is nil), asking first if
    /// the editor holds unsaved work.
    private func requestSwitch(to name: String?) {
        guard isDirty else {
            if let name { selection = name } else { newRule() }
            return
        }
        pendingSwitch = .some(name)
    }

    // MARK: editor

    private var editorPane: some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.sm) {
            HStack {
                TextField("Rule name", text: $draft.name).textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 260)
                Picker("Mode", selection: $draft.mode) {
                    Text("Review").tag("review")
                    Text("Auto").tag("auto")
                }.pickerStyle(.segmented).frame(width: 160)
                Toggle("Enabled", isOn: $draft.enabled).toggleStyle(.switch)
                Spacer()
            }

            RuleSourceEditor(text: $draft.text)
                .frame(minHeight: 200)
                .clipShape(RoundedRectangle(cornerRadius: Layout.Radius.medium))
                .overlay(RoundedRectangle(cornerRadius: Layout.Radius.medium)
                    .stroke(errors.isEmpty ? Color.secondary.opacity(0.25) : Theme.Colors.critical))
                .accessibilityLabel("Rule source")

            if !errors.isEmpty {
                ForEach(errors) { e in
                    Label(errorText(e), systemImage: "exclamationmark.triangle.fill")
                        .font(.caption).foregroundStyle(Theme.Colors.critical)
                }
            }

            if let ruleLoadError {
                Label("Rule unavailable: \(ruleLoadError)",
                      systemImage: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundStyle(Theme.Colors.critical)
            }

            HStack {
                Button("Validate") { Task { await validate() } }
                Button("Preview") { Task { await runPreview() } }.keyboardShortcut(.return, modifiers: .command)
                Button("Save") { Task { await save() } }
                    .keyboardShortcut("s", modifiers: .command)
                Spacer()
                Button("Delete", role: .destructive) { Task { await delete() } }
                    .disabled(selection == nil)
                Button("Run & apply now…") { confirmingApply = true }
                    .buttonStyle(.borderedProminent)
                    .disabled(draft.name.isEmpty)
            }
            .disabled(busy)
            .disabled(ruleLoading)

            if let status {
                Text(status).font(.caption).foregroundStyle(.secondary)
            }

            Divider()
            previewPane
        }
        .padding(Layout.Spacing.md)
        .confirmationDialog("Apply this rule to \(appState.selectedMarket) now?",
                            isPresented: $confirmingApply, titleVisibility: .visible) {
            Button("Save & Apply", role: .destructive) { Task { await apply() } }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Writes to Amazon (bids/pauses/negatives). KILL freeze and the economics gate still apply. Every change lands in the Audit Trail.")
        }
        .confirmationDialog("Rename “\(pendingRename?.from ?? "")” to “\(pendingRename?.to ?? "")”?",
                            isPresented: Binding(get: { pendingRename != nil },
                                                 set: { if !$0 { pendingRename = nil } }),
                            titleVisibility: .visible,
                            presenting: pendingRename) { request in
            Button("Rename") {
                pendingRename = nil
                Task { await renameDraft(request) }
            }
            Button("Save as a New Rule") {
                pendingRename = nil
                Task { await persistDraft() }
            }
            Button("Cancel", role: .cancel) { pendingRename = nil }
        } message: { request in
            Text("Renaming deletes “\(request.from)”. Saving as a new rule keeps it — and if it is enabled it keeps running nightly.")
        }
    }

    /// Validation errors carry a column too; showing it makes long lines findable.
    private func errorText(_ e: RuleValidationError) -> String {
        e.col > 0 ? "Line \(e.line), column \(e.col): \(e.message)"
                  : "Line \(e.line): \(e.message)"
    }

    @ViewBuilder
    private var previewPane: some View {
        if let p = preview {
            if p.ok {
                HStack {
                    Text("\(p.matched ?? 0) matched · \(p.evaluated ?? 0) evaluated")
                        .font(.caption).foregroundStyle(.secondary)
                    if p.truncated == true {
                        Text("(truncated at cap)").font(.caption).foregroundStyle(Theme.Colors.caution)
                    }
                    if isPreviewStale {
                        Label("Rule edited since this preview — press ⌘↩ to re-run",
                              systemImage: "exclamationmark.triangle.fill")
                            .font(.caption).foregroundStyle(Theme.Colors.caution)
                    }
                    Spacer()
                }
                Table(p.changes ?? [], selection: $previewSel) {
                    TableColumn("Entity") { c in
                        Text(c.label).lineLimit(1)
                    }
                    TableColumn("Action") { c in
                        Text(c.argsText.map { "\(c.action)(\($0))" } ?? c.action)
                            .font(.body.monospaced()).foregroundStyle(.tint)
                    }
                    TableColumn("Why") { c in
                        TraceReasonCell(reason: c.note ?? "", trace: c.trace)
                    }
                }
                .frame(minHeight: 140)
                .copyableRows(p.changes ?? [], primaryLabel: "Entity",
                              primary: { $0.label },
                              row: { c in
                                  let action = c.argsText.map { "\(c.action)(\($0))" } ?? c.action
                                  return "\(c.label)\t\(action)\t\(c.note ?? "")"
                              })
            } else {
                Text(p.errors?.first.map { "Line \($0.line): \($0.message)" } ?? "Rule error")
                    .font(.caption).foregroundStyle(Theme.Colors.critical)
            }
        } else {
            ContentUnavailableView("Preview is read-only",
                                   systemImage: "eye",
                                   description: Text("Press Preview (⌘↩) to see exactly what this rule would change — nothing is written."))
                .frame(maxHeight: 220)
        }
    }

    // MARK: actions

    private func newRule() {
        selection = nil
        draft = RuleDraft(text: "FOR EACH target:\n  IF target.clicks >= 15 AND target.orders = 0:\n    target.pause()\n    target.note(\"{clicks} clicks, 0 sales\")\n")
        saved = draft            // a blank template is not unsaved work
        errors = []; preview = nil; previewedText = nil; status = nil
    }

    private func loadList() async {
        let market = appState.selectedMarket
        listLoading = true
        listLoadError = nil
        defer { if market == appState.selectedMarket { listLoading = false } }
        do {
            let bridge = try appState.makeBridge()
            let r = try await bridge.call(RuleListResponse.self, ["rules-list"], market: market)
            guard !Task.isCancelled, market == appState.selectedMarket else { return }
            rules = r.rules
        } catch {
            guard !Task.isCancelled, market == appState.selectedMarket else { return }
            rules = []
            listLoadError = error.localizedDescription
        }
    }

    private func loadRule(_ name: String, revertingTo previous: String?) async {
        ruleLoadID += 1
        let requestID = ruleLoadID
        let market = appState.selectedMarket
        ruleLoading = true
        ruleLoadError = nil
        defer {
            if requestID == ruleLoadID, market == appState.selectedMarket { ruleLoading = false }
        }
        do {
            let bridge = try appState.makeBridge()
            let r = try await bridge.call(Rule.self, ["rules-get", "--rule", name], market: market)
            guard !Task.isCancelled, requestID == ruleLoadID,
                  market == appState.selectedMarket, selection == name else { return }
            draft = RuleDraft(name: r.name, text: r.text, enabled: r.enabled, mode: r.mode)
            saved = draft
            errors = []; preview = nil; previewedText = nil; status = nil
        } catch {
            guard !Task.isCancelled, requestID == ruleLoadID,
                  market == appState.selectedMarket, selection == name else { return }
            selection = previous
            ruleLoadError = error.localizedDescription
            status = "Could not load “\(name)”. The previous rule remains open."
        }
    }

    private func validate() async {
        await withBridge { bridge in
            let r = try await bridge.call(RuleValidateResponse.self, ["rules-validate"],
                                          market: appState.selectedMarket,
                                          stdin: Data(draft.text.utf8), preferWorker: false)
            errors = r.errors
            status = r.ok ? "Valid ✓" : nil
        }
    }

    private func runPreview() async {
        await withBridge { bridge in
            let text = draft.text
            let r = try await bridge.call(RulePreviewResponse.self, ["rules-preview"],
                                          market: appState.selectedMarket,
                                          stdin: Data(text.utf8), preferWorker: false)
            preview = r
            previewedText = text        // so later edits can mark the table stale
            errors = r.ok ? [] : (r.errors ?? [])
        }
    }

    private func save() async {
        // `rules-save` is keyed by name. Saving an existing rule under a new name
        // writes a second rule and leaves the original enabled and running — so
        // ask what the operator meant rather than silently forking.
        if !saved.name.isEmpty, draft.name != saved.name {
            pendingRename = RenameRequest(from: saved.name, to: draft.name)
            return
        }
        await persistDraft()
    }

    /// Write the editor's rule to disk and mark the editor clean.
    private func persistDraft() async {
        await withBridge { bridge in
            let payload: [String: Any] = ["name": draft.name, "text": draft.text,
                                          "enabled": draft.enabled, "mode": draft.mode]
            let data = try JSONSerialization.data(withJSONObject: payload)
            _ = try await bridge.call(Rule.self, ["rules-save"], market: appState.selectedMarket,
                                      stdin: data, preferWorker: false)
            status = "Saved ✓"
            saved = draft
            await loadList()
            selection = draft.name
        }
    }

    /// Save under the new name, then delete the rule it replaced.
    private func renameDraft(_ request: RenameRequest) async {
        await persistDraft()
        guard saved.name == request.to else { return }   // the save failed; keep both
        await withBridge { bridge in
            _ = try await bridge.call(DeleteAck.self, ["rules-delete", "--rule", request.from],
                                      market: appState.selectedMarket, preferWorker: false)
            status = "Renamed “\(request.from)” to “\(request.to)”"
            await loadList()
        }
    }

    private func delete() async {
        guard let name = selection else { return }
        pendingDelete = name
    }

    /// Delete one rule by name (from the list context menu / swipe / editor).
    private func deleteRule(_ name: String) async {
        await withBridge { bridge in
            _ = try await bridge.call(DeleteAck.self, ["rules-delete", "--rule", name],
                                      market: appState.selectedMarket, preferWorker: false)
            if selection == name { selection = nil; newRule() }
            status = "Deleted “\(name)”"
            await loadList()
        }
    }

    /// Enable/disable a rule in place (re-saves it with the flag flipped).
    private func setEnabled(_ r: RuleSummary, _ enabled: Bool) async {
        await withBridge { bridge in
            let full = try await bridge.call(Rule.self, ["rules-get", "--rule", r.name],
                                             market: appState.selectedMarket)
            let payload: [String: Any] = ["name": r.name, "text": full.text,
                                          "enabled": enabled, "mode": r.mode]
            _ = try await bridge.call(Rule.self, ["rules-save"], market: appState.selectedMarket,
                                      stdin: try JSONSerialization.data(withJSONObject: payload),
                                      preferWorker: false)
            if selection == r.name { draft.enabled = enabled }
            status = enabled ? "Enabled “\(r.name)”" : "Disabled “\(r.name)”"
            await loadList()
        }
    }

    /// Duplicate a rule as "<name> copy" (disabled, so it doesn't double-run).
    private func duplicate(_ r: RuleSummary) async {
        await withBridge { bridge in
            let full = try await bridge.call(Rule.self, ["rules-get", "--rule", r.name],
                                             market: appState.selectedMarket)
            let newName = availableName(basedOn: "\(r.name) copy")
            let payload: [String: Any] = ["name": newName, "text": full.text,
                                          "enabled": false, "mode": r.mode]
            _ = try await bridge.call(Rule.self, ["rules-save"], market: appState.selectedMarket,
                                      stdin: try JSONSerialization.data(withJSONObject: payload),
                                      preferWorker: false)
            await loadList()
            selection = newName
            status = "Duplicated as “\(newName)”"
        }
    }

    /// A name no existing rule uses — `rules-save` overwrites by name, so a fixed
    /// "<name> copy" would clobber the previous copy instead of making another.
    private func availableName(basedOn base: String) -> String {
        let taken = Set(rules.map(\.name))
        guard taken.contains(base) else { return base }
        var n = 2
        while taken.contains("\(base) \(n)") { n += 1 }
        return "\(base) \(n)"
    }

    private func apply() async {
        await withBridge { bridge in
            // persist first so --apply runs the saved rule
            let payload: [String: Any] = ["name": draft.name, "text": draft.text,
                                          "enabled": draft.enabled, "mode": draft.mode]
            _ = try await bridge.call(Rule.self, ["rules-save"], market: appState.selectedMarket,
                                      stdin: try JSONSerialization.data(withJSONObject: payload),
                                      preferWorker: false)
            // `rules-run --apply` answers with the executor's own reply, the
            // same shape the approval queue reads. Decoding only `count` made
            // a run whose every change was blocked or refused look like a rule
            // that matched nothing.
            let r = try await bridge.call(RulesApproveResponse.self,
                                          ["rules-run", "--apply", "--rule", draft.name],
                                          market: appState.selectedMarket, preferWorker: false)
            status = r.runSummary
            await loadList()
        }
    }

    /// Runs one engine operation at a time. `busy` is set synchronously here, so a
    /// double-click on Preview/Save/Apply can't enqueue a second call whose
    /// `defer` re-enables the buttons while the first is still in flight.
    @discardableResult
    private func withBridge(_ work: (PythonBridge) async throws -> Void) async -> Bool {
        guard !busy else { return false }
        busy = true
        defer { busy = false }
        do {
            let bridge = try appState.makeBridge()
            try await work(bridge)
            return true
        } catch {
            status = error.localizedDescription
            lastBridgeError = error.localizedDescription
            return false
        }
    }
}

private struct RuleDraft: Equatable {
    var name: String = ""
    var text: String = ""
    var enabled: Bool = false
    var mode: String = "review"
}

/// A save that would rename the rule on disk. `rules-save` is keyed by name, so
/// saving under a new one creates a second rule and leaves the original in place
/// — still enabled, still running nightly. The operator picks which they meant.
private struct RenameRequest: Identifiable {
    let id = UUID()
    let from: String
    let to: String
}

private struct DeleteAck: Codable { let deleted: String }

#Preview {
    RulesView()
        .environment(AppState())
}
