import SwiftUI

/// The Review-mode rules half of the Approval queue: proposed changes from
/// enabled review rules, grouped by rule. Approve a subset and apply (through the
/// rules executor: max-bid clamp, KILL, econ-gate, cap — every change lands in
/// the Audit Trail), or discard. "Refresh" re-evaluates the review rules.
struct RulesApprovalQueue: View {
    @Environment(AppState.self) private var appState

    @State private var changes: [RulePendingChange] = []
    @State private var approved = Set<RulePendingChange.ID>()
    @State private var loading = false
    @State private var busy = false
    @State private var status: String?
    @State private var confirmingApply = false
    @State private var pendingDiscard: DiscardScope?
    // Row selection exists for the right-click Copy. Approving still happens
    // only through the checkbox column.
    @State private var rowSel = Set<RulePendingChange.ID>()

    /// Discard used to reuse the *approval* checkboxes to choose what to throw
    /// away — the opposite of what checking a row means — and with nothing
    /// checked it silently wiped the whole queue. Now it is an explicit choice.
    private enum DiscardScope: String, Identifiable {
        case selected, all
        var id: String { rawValue }
    }

    private var currency: String? { appState.currentMarket?.currency }

    var body: some View {
        VStack(spacing: 0) {
            header
            conflictBanner
            Divider()
            content
        }
        .task(id: appState.viewKey) {
            // Pending ids are per-market; carrying approvals across a switch could
            // land them on a different market's proposals.
            approved.removeAll()
            status = nil
            await load()
        }
        .confirmationDialog("Apply \(approved.count) approved change(s) to \(appState.selectedMarket)?",
                            isPresented: $confirmingApply, titleVisibility: .visible) {
            Button("Apply", role: .destructive) { Task { await apply() } }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Writes to Amazon (bids/pauses/negatives). KILL freeze and the economics gate still apply, and every change is logged in the Audit Trail. Bid changes, pauses and added negatives can all be rolled back there.")
        }
        .confirmationDialog(discardTitle,
                            isPresented: Binding(get: { pendingDiscard != nil },
                                                 set: { if !$0 { pendingDiscard = nil } }),
                            titleVisibility: .visible,
                            presenting: pendingDiscard) { scope in
            Button("Discard", role: .destructive) {
                pendingDiscard = nil
                Task { await discard(scope) }
            }
            Button("Cancel", role: .cancel) { pendingDiscard = nil }
        } message: { _ in
            Text("Discarded proposals are gone from the queue. The rules will propose them again on the next run if the conditions still hold.")
        }
    }

    private var discardTitle: String {
        switch pendingDiscard {
        case .selected: "Discard \(approved.count) checked change(s)?"
        case .all: "Discard all \(changes.count) pending change(s)?"
        case nil: ""
        }
    }

    private var header: some View {
        HStack(spacing: Layout.Spacing.sm) {
            Button("Refresh") { Task { await refresh() } }
                .help("Re-evaluate the enabled review rules and rebuild this queue")
            Button("Approve all") { approved = Set(changes.map(\.id)) }
                .disabled(changes.isEmpty)
            Button("Clear selection") { approved.removeAll() }
                .disabled(approved.isEmpty)
            if let status { Text(status).font(.caption).foregroundStyle(.secondary) }
            Spacer()
            Menu("Discard") {
                Button("Discard \(approved.count) Checked…", role: .destructive) {
                    pendingDiscard = .selected
                }
                .disabled(approved.isEmpty)
                Button("Discard All \(changes.count)…", role: .destructive) {
                    pendingDiscard = .all
                }
            }
            .menuStyle(.button)
            .fixedSize()
            .disabled(changes.isEmpty)
            Button("Apply \(approved.count) approved") { confirmingApply = true }
                .buttonStyle(.borderedProminent)
                .disabled(approved.isEmpty || appState.killActive)
                .help(appState.killActive
                      ? "KILL freeze is active — release it in Actions to apply"
                      : "Apply the checked changes through the rules executor")
        }
        .disabled(busy)
        .padding(Layout.Spacing.sm)
    }

    /// Entities more than one rule wants. Two rules moving one bid is not a
    /// detail to bury in a column: applying both sends both writes and the last
    /// one wins, so the operator needs to see it before they press Apply.
    private var contestedEntities: Int {
        Set(changes.compactMap { $0.conflict == nil ? nil : $0.label }).count
    }

    @ViewBuilder
    private var conflictBanner: some View {
        if contestedEntities > 0 {
            HStack(spacing: Layout.Spacing.xs) {
                Image(systemName: "arrow.triangle.branch")
                    .foregroundStyle(Theme.Colors.caution)
                Text("\(contestedEntities) \(contestedEntities == 1 ? "entity is" : "entities are") wanted by more than one rule. Approve both sides and only the first rule's change is applied — the other is held back and stays in this queue.")
                    .font(.caption)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
                Button("Keep winners only") {
                    approved = Set(changes.filter { $0.conflict?.kept != false }.map(\.id))
                }
                .buttonStyle(.borderless)
                .font(.caption)
                .help("Check every uncontested change plus the winning side of each clash")
            }
            .padding(.horizontal, Layout.Spacing.sm)
            .padding(.vertical, Layout.Spacing.xs)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.Colors.caution.opacity(0.10))
        }
    }

    @ViewBuilder
    private var content: some View {
        if loading && changes.isEmpty {
            ProgressView("Loading pending rule changes…").frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if changes.isEmpty {
            ContentUnavailableView {
                Label("No rules pending review", systemImage: "checkmark.seal")
            } description: {
                Text("Enabled review-mode rules queue their proposed changes here (nightly, or press Refresh). Set a rule to Auto in Rules to apply it automatically instead.")
            }
        } else {
            Table(changes, selection: $rowSel) {
                TableColumn("✓") { c in
                    Toggle("", isOn: binding(for: c))
                        .labelsHidden()
                        .accessibilityLabel("Approve \(c.action) on \(c.label)")
                }.width(28)
                TableColumn("Rule") { c in Text(c.rule).lineLimit(1).foregroundStyle(.secondary) }
                TableColumn("Entity") { c in
                    HStack(spacing: 5) {
                        Text(c.label).lineLimit(1)
                        if let conflict = c.conflict {
                            Image(systemName: conflict.kept
                                  ? "arrow.triangle.branch" : "exclamationmark.triangle.fill")
                                .font(.caption)
                                .foregroundStyle(conflict.kept
                                                 ? Theme.Colors.caution : Theme.Colors.critical)
                                .help(conflictHelp(conflict))
                        }
                    }
                }
                TableColumn("Action") { c in
                    Text(c.argsText.map { "\(c.action)(\($0))" } ?? c.action)
                        .font(.body.monospaced()).foregroundStyle(.tint)
                }
                TableColumn("Why") { c in TraceReasonCell(reason: c.note ?? "", trace: c.trace) }
            }
            .copyableRows(changes, primaryLabel: "Entity",
                          primary: { $0.label },
                          row: { c in
                              let action = c.argsText.map { "\(c.action)(\($0))" } ?? c.action
                              return "\(c.rule)\t\(c.label)\t\(action)\t\(c.note ?? "")"
                          })
        }
    }

    private func conflictHelp(_ c: RuleConflict) -> String {
        let others = c.with.joined(separator: ", ")
        return c.kept
            ? "\(others) also want this \(c.surface). This rule wins — approve both and the other is held back."
            : "“\(c.winner)” also wants this \(c.surface) and runs first. Approve both and this one is held back, still here to approve on its own."
    }

    private func binding(for c: RulePendingChange) -> Binding<Bool> {
        Binding(get: { approved.contains(c.id) },
                set: { if $0 { approved.insert(c.id) } else { approved.remove(c.id) } })
    }

    private func load() async {
        loading = true
        defer { loading = false }
        // A failed load must not read as "nothing pending" — the empty state
        // and a broken bridge are different situations.
        do {
            let bridge = try appState.makeBridge()
            let r = try await bridge.call(RulePendingResponse.self, ["rules-pending"],
                                          market: appState.selectedMarket)
            changes = r.changes
            approved = approved.intersection(Set(r.changes.map(\.id)))
        } catch is CancellationError {
            // market switch mid-load; the new .task run reloads
        } catch {
            status = "Couldn't load the queue: \(error.localizedDescription)"
        }
    }

    private func refresh() async {
        await withBridge { bridge in
            let r = try await bridge.call(RulePendingResponse.self, ["rules-collect"],
                                          market: appState.selectedMarket, preferWorker: false)
            changes = r.changes
            // rules-collect rebuilds the queue with fresh ids; keeping approvals
            // for ids that no longer exist would send stale ids to rules-approve
            // and mis-state the "Apply N approved" count.
            approved = approved.intersection(Set(r.changes.map(\.id)))
            status = "\(r.changes.count) pending"
        }
    }

    /// The one real Amazon write here — routed through ActionCoordinator like
    /// every other write path (KILL re-checked server-side, market captured at
    /// intent creation, rehearsal receipts, audit visibility). The queue used
    /// to call the bridge directly, which skipped all of that.
    private func apply() async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        do {
            let data = try JSONSerialization.data(withJSONObject: ["ids": Array(approved)])
            let intent = appState.marketIntent(
                title: "Apply \(approved.count) approved rule change(s)",
                arguments: ["rules-approve"], stdin: data,
                cardinality: .bulk, responseKind: .rulesApprove)
            let receipt = try await appState.actionCoordinator.execute(
                intent, context: appState.actionPolicyContext, confirmed: true)
            guard !receipt.rehearsed else { return }
            if case .rulesApprove(let count, let blocked, let heldBack) = receipt.result {
                if let blocked {
                    status = "Blocked: \(blocked)"
                } else if heldBack > 0 {
                    status = "Applied \(count) · \(heldBack) held back — another rule had already claimed that entity"
                } else {
                    status = "Applied \(count)"
                }
            }
            approved.removeAll()
            await load()
        } catch {
            status = error.localizedDescription
        }
    }

    private func discard(_ scope: DiscardScope) async {
        await withBridge { bridge in
            let body: [String: Any]
            switch scope {
            case .all: body = ["all": true]
            case .selected:
                guard !approved.isEmpty else { return }
                body = ["ids": Array(approved)]
            }
            let data = try JSONSerialization.data(withJSONObject: body)
            _ = try await bridge.call(RulePendingResponse.self, ["rules-discard"],
                                      market: appState.selectedMarket, stdin: data, preferWorker: false)
            approved.removeAll()
            await load()
        }
    }

    /// One engine operation at a time: `busy` is set synchronously so a second
    /// click can't slip in and have the first call's `defer` re-enable the
    /// buttons mid-write.
    private func withBridge(_ work: (PythonBridge) async throws -> Void) async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        do { try await work(try appState.makeBridge()) }
        catch { status = error.localizedDescription }
    }
}

