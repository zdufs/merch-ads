import SwiftUI

/// The audit trail: every write the engine or the app ever made (writes_log),
/// with one-click Undo where the action is reversible (pauses, enables, bids).
struct AuditView: View {
    @Environment(AppState.self) private var appState
    @State private var audit: AuditResponse?
    @State private var loadError: String?
    @State private var loadingOlder = false
    @State private var actionError: String?   // undo failures — inline, never replaces the table
    @State private var isLoading = false
    @State private var undoTarget: PendingAuditUndo?
    @State private var undoingRow: Int?
    @State private var undoneRows = Set<String>()   // "market#rowid" — rowids restart per market
    @State private var lastResult: String?
    @State private var actionFilter = "all"
    @State private var filterText = ""
    @State private var selection = Set<AuditWrite.ID>()
    @State private var colPrefs: TableColumnCustomization<AuditWrite> = ColumnPrefs.load(TableID.audit)

    private static let sortFields: [String: KeyPathComparator<AuditWrite>] = [
        "when": .init(\.at), "action": .init(\.action), "entity": .init(\.entityId),
    ]
    private static let defaultSort = [KeyPathComparator(\AuditWrite.at, order: .reverse)]

    @State private var sortOrder = SortPrefs.load(
        TableID.audit, fields: sortFields,
        fallback: defaultSort)

    private var actions: [String] {
        let all = Set((audit?.writes ?? []).map(\.action))
        return ["all"] + all.sorted()
    }

    private var filtered: [AuditWrite] {
        (audit?.writes ?? [])
            .filter { actionFilter == "all" || $0.action == actionFilter }
            .filter {
                filterText.isEmpty
                    || $0.entityId.localizedStandardContains(filterText)
                    || ($0.detail ?? "").localizedStandardContains(filterText)
            }
            .sorted(using: sortOrder)
    }

    // No-op rows ("0 ASINs" builder runs) are not writes — counting them made the
    // headline read 15 when nothing had changed. The engine no longer logs them;
    // these filters keep the older rows out of the counts too.
    //
    // Every card below prefers the engine's own SQL counts over the loaded page.
    // Derived from the page they were capped by the fetch limit, so on a busy
    // week the card printed the page size — 500 — while the real seven-day
    // count was many times that (US, 2026-08-24). The page fallback is for an
    // engine too old to send totals, and it says so on the card rather than
    // passing itself off as the account's number.
    private var engineTotals: AuditTotals? { audit?.totals }

    private var countsAreFromThePage: Bool { audit != nil && engineTotals == nil }

    private var writesToday: Int {
        if let totals = engineTotals { return totals.today }
        let today = Format.dayString()
        return (audit?.writes ?? []).filter { $0.at.hasPrefix(today) && !$0.isNoOp }.count
    }

    private var noOpsToday: Int {
        if let totals = engineTotals { return totals.noOpsToday }
        let today = Format.dayString()
        return (audit?.writes ?? []).filter { $0.at.hasPrefix(today) && $0.isNoOp }.count
    }

    private var writesThisWeek: Int {
        if let totals = engineTotals { return totals.week }
        let cutoff = Calendar.current.date(byAdding: .day, value: -6, to: Date()) ?? Date()
        let cutoffKey = Format.dayString(of: cutoff)
        return (audit?.writes ?? []).filter { String($0.at.prefix(10)) >= cutoffKey && !$0.isNoOp }.count
    }

    /// Undone-this-session rows are taken off the engine's count too: the
    /// original row stays undoable in the log, and the operator has already
    /// reversed it here.
    private var undoableCount: Int {
        let undoneNow = (audit?.writes ?? [])
            .filter { $0.undoable && undoneRows.contains(undoKey($0.rowId)) }.count
        if let totals = engineTotals { return max(0, totals.undoable - undoneNow) }
        return (audit?.writes ?? []).filter { $0.undoable && !undoneRows.contains(undoKey($0.rowId)) }.count
    }

    var body: some View {
        let visible = filtered   // filter+sort once per body eval, not per use
        VStack(spacing: 0) {
            PageHeader(title: "Audit Trail", subtitle: appState.selectedMarket, help: .audit)
            statusBand
            FilterBar {
                Picker("Action", selection: $actionFilter) {
                    ForEach(actions, id: \.self) { Text($0).tag($0) }
                }
                .fixedSize()
                .help("Show only one kind of write — e.g. just bid changes or just pauses")
                TextField("Filter by entity or detail", text: $filterText)
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 200)
                if let lastResult {
                    Text(lastResult).font(.caption).foregroundStyle(Theme.Colors.positive)
                }
            } trailing: {
                SavedViewPicker(tableID: TableID.audit,
                                filters: ["action": actionFilter, "search": filterText],
                                sortFields: Self.sortFields, defaultSort: Self.defaultSort,
                                sortOrder: $sortOrder, columns: $colPrefs) { filters in
                    actionFilter = filters["action"] ?? "all"
                    filterText = filters["search"] ?? ""
                }
                ExportButton(filename: "audit-\(appState.selectedMarket)") {
                    CSVDocument(
                        headers: ["at", "action", "entity_type", "entity_id",
                                  "detail", "prev_state", "result"],
                        rows: visible.map { write in
                            [write.at, write.action, write.entityType ?? "",
                             write.entityId, write.detail ?? "",
                             write.prevState ?? "", write.result ?? ""]
                        })
                }
                // "rows shown", not "writes": this is the page that is loaded
                // and filtered, never the account's count of writes.
                Text("\(Format.count(visible.count)) rows shown")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
                    .help("The table pages 500 rows at a time. The cards above count the whole log.")
            }
            ActionErrorBar(message: $actionError)
            Divider()

            if isLoading && audit == nil {
                ProgressView("Loading writes_log…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let loadError {
                ContentUnavailableView {
                    Label("Audit unavailable", systemImage: "clock.arrow.circlepath")
                } description: {
                    Text(loadError)
                } actions: {
                    Button("Retry") { Task { await load() } }
                }
            } else {
                Table(visible, selection: $selection, sortOrder: $sortOrder.descendingFirst(), columnCustomization: $colPrefs) {
                    TableColumn("When", value: \.at) { write in
                        Text(Format.euDateTime(write.at))
                            .font(.caption.monospaced())
                    }
                    .width(min: 60, ideal: 110)
                    .customizationID("when")
                    TableColumn("Action", value: \.action) { write in
                        StatusBadge(text: write.action.replacingOccurrences(of: "_", with: " "),
                                    symbol: nil, tint: Theme.Colors.information)
                    }
                    .width(min: 71, ideal: 130)
                    .customizationID("action")
                    TableColumn("Entity", value: \.entityId) { write in
                        // resolved name where the engine knows it; raw id in the tooltip
                        if let name = write.entityName {
                            Text(name)
                                .lineLimit(1)
                                .truncationMode(.middle)
                                .help("\(write.entityType ?? "") \(write.entityId)")
                        } else {
                            Text(write.entityId)
                                .font(.caption.monospaced())
                                .foregroundStyle(.secondary)
                        }
                    }
                    .width(min: 71, ideal: 170)
                    .customizationID("entity")
                    TableColumn("Detail", value: \.detailValue) { write in
                        Text(write.detail ?? "—")
                            .lineLimit(1)
                            .truncationMode(.tail)
                            // no-op writes ("0 ASINs" builder runs) sit muted out of
                            // the way — muted, not secondary@0.5, which was sub-AA.
                            .foregroundStyle(write.isNoOp ? Theme.Colors.muted : Color.primary)
                            .help(write.detail ?? "")
                    }
                    .customizationID("detail")
                    TableColumn("Result", value: \.resultValue) { write in
                        // Amazon bulk responses are 200/207 = success; the raw payload
                        // (which may carry per-item errors) lives in the tooltip.
                        if write.succeeded {
                            StatusBadge(text: write.result.map { $0.hasPrefix("submitted") ? $0 : "applied" } ?? "—",
                                        symbol: "checkmark.circle.fill",
                                        tint: Theme.Colors.positive)
                                .help(write.result ?? "")
                        } else {
                            StatusBadge(text: write.result ?? "—",
                                        symbol: "xmark.circle.fill",
                                        tint: Theme.Colors.critical)
                                .lineLimit(1)
                                .help(write.result ?? "")
                        }
                    }
                    .width(min: 44, ideal: 80)
                    .customizationID("result")
                    TableColumn("") { write in
                        if write.undoable && !undoneRows.contains(undoKey(write.rowId)) {
                            Button {
                                requestUndo(write)
                            } label: {
                                if undoingRow == write.rowId {
                                    ProgressView().controlSize(.small)
                                } else {
                                    Label("Undo", systemImage: "arrow.uturn.backward")
                                        .labelStyle(.titleOnly)
                                }
                            }
                            .buttonStyle(.borderless)
                            .disabled(undoingRow != nil || appState.killActive)
                            .help(undoHelp(write))
                        }
                    }
                    .width(min: 33, ideal: 60)
                    .customizationID("col6")
                }
                .copyableRows(visible, primaryLabel: "Action",
                              primary: { "\($0.action) \($0.entityId)" },
                              row: { "\($0.at)\t\($0.action)\t\($0.entityId)\t\($0.detail ?? "")\t\($0.result ?? "")" })
                .background(Theme.Colors.surface)
                if audit?.hasMore == true {
                    // the trail used to hard-stop at 500 rows with no way down
                    Button(loadingOlder ? "Loading…" : "Load older writes") {
                        Task { await loadOlder() }
                    }
                    .disabled(loadingOlder)
                    .padding(Layout.Spacing.sm)
                    .frame(maxWidth: .infinity)
                }
            }
        }
        .background(Theme.Colors.canvas)
        .task(id: appState.viewKey) { await load() }
        // Per-market action filter: remembered per market, reloaded on switch.
        .task(id: appState.selectedMarket) {
            actionFilter = FilterPrefs.load("audit.action", market: appState.selectedMarket, default: "all")
        }
        .onChange(of: actionFilter) {
            FilterPrefs.save("audit.action", market: appState.selectedMarket, actionFilter)
        }
        .onChange(of: colPrefs) { ColumnPrefs.save(TableID.audit, colPrefs) }
        .onChange(of: sortOrder) { SortPrefs.save(TableID.audit, sortOrder, fields: Self.sortFields) }
        .confirmationDialog(
            "Undo \(undoTarget?.write.action ?? "") on \(undoTarget?.write.entityId ?? "")?",
            isPresented: Binding(get: { undoTarget != nil },
                                 set: { if !$0 { undoTarget = nil } })) {
            Button("Undo", role: .destructive) {
                if let target = undoTarget {
                    Task { await undo(target, confirmed: true) }
                }
                undoTarget = nil
            }
        } message: {
            Text(undoTarget.map { undoHelp($0.write) } ?? "")
        }
    }

    private var statusBand: some View {
        Group {
            HStack(spacing: Layout.Spacing.sm) {
                StatCard(title: "Writes today", value: Format.count(writesToday),
                         symbol: "clock.fill",
                         subtitle: todaySubtitle)
                    .mdCard()
                StatCard(title: "Writes this week", value: Format.count(writesThisWeek),
                         symbol: "calendar",
                         subtitle: countsAreFromThePage
                            ? "in the loaded page only" : "last 7 days · whole log")
                    .mdCard()
                StatCard(title: "Undoable", value: Format.count(undoableCount),
                         tint: undoableCount > 0 ? Theme.Colors.information : Theme.Colors.muted,
                         symbol: "arrow.uturn.backward.circle.fill",
                         subtitle: countsAreFromThePage
                            ? "in the loaded page only" : "whole log")
                    .mdCard()
            }
        }
        .padding(.horizontal, Layout.Spacing.lg)
        .padding(.vertical, Layout.Spacing.md)
    }

    /// The no-op line, plus the caveat when the numbers are page-derived.
    private var todaySubtitle: String? {
        var parts: [String] = []
        if noOpsToday > 0 { parts.append("+ \(noOpsToday) no-op row\(noOpsToday == 1 ? "" : "s")") }
        if countsAreFromThePage { parts.append("in the loaded page only") }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    private func undoHelp(_ write: AuditWrite) -> String {
        switch write.action {
        case "pause_ad_group", "pause_campaign": "Re-enables the paused entity"
        case "enable_ad_group", "enable_campaign", "rollback_enable": "Pauses it again"
        case "bid_change": "Restores the previous bid"
        default: "Undo"
        }
    }

    /// Single undo is a "small action": one-click unless "always confirm" is on.
    private func requestUndo(_ write: AuditWrite) {
        let intent = appState.marketIntent(
            title: "Undo \(write.action) on \(write.entityName ?? write.entityId)",
            arguments: ["undo", "--row", String(write.rowId)], responseKind: .undo)
        let pending = PendingAuditUndo(write: write, intent: intent)
        switch appState.actionCoordinator.requirement(
            for: intent, context: appState.actionPolicyContext) {
        case .confirmation:
            undoTarget = pending
        case .blocked(.killActive(let scope)):
            // Say it here rather than making a round trip the coordinator will refuse.
            actionError = ActionCoordinatorError.killActive(scope).localizedDescription
        case .preview, .ready:
            Task { await undo(pending) }
        }
    }

    private func undoKey(_ rowId: Int) -> String {
        "\(appState.selectedMarket)#\(rowId)"
    }

    private func undo(_ pending: PendingAuditUndo, confirmed: Bool = false) async {
        let write = pending.write
        undoingRow = write.rowId
        defer { undoingRow = nil }
        actionError = nil
        do {
            let receipt = try await appState.actionCoordinator.execute(
                pending.intent, context: appState.actionPolicyContext,
                confirmed: confirmed)
            guard !receipt.rehearsed else { return }
            guard case .undo(let applied, let entityID, let state, let bid) = receipt.result else { return }
            if applied {
                if case .market(let market) = pending.intent.scope {
                    undoneRows.insert("\(market)#\(write.rowId)")
                }
                if let state {
                    lastResult = "\(entityID) → \(state)"
                } else if let bid {
                    lastResult = "\(entityID) bid restored to \(Format.money(bid, currency: appState.currentMarket?.currency))"
                }
            } else {
                actionError = "Undo was not accepted by Amazon — check the audit log."
            }
            if pending.intent.scope.market == appState.selectedMarket { await load() }
        } catch {
            actionError = error.localizedDescription
        }
    }

    /// Page older history below the current oldest row (audit --before).
    private func loadOlder() async {
        guard let current = audit, let oldest = current.writes.map(\.rowId).min() else { return }
        loadingOlder = true
        defer { loadingOlder = false }
        do {
            let bridge = try appState.makeBridge()
            let older = try await bridge.call(
                AuditResponse.self,
                ["audit", "--limit", "500", "--before", String(oldest)],
                market: appState.selectedMarket)
            guard !Task.isCancelled else { return }
            audit = AuditResponse(market: current.market,
                                  count: current.count + older.count,
                                  writes: current.writes + older.writes,
                                  // Account-wide counts, so paging must not
                                  // change them — keep the first page's.
                                  totals: current.totals ?? older.totals,
                                  hasMore: older.hasMore)
        } catch {
            loadError = error.localizedDescription
        }
    }

    private func load() async {
        isLoading = true
        defer { if !Task.isCancelled { isLoading = false } }
        loadError = nil
        do {
            let bridge = try appState.makeBridge()
            let response = try await bridge.call(AuditResponse.self, ["audit", "--limit", "500"],
                                                 market: appState.selectedMarket)
            guard !Task.isCancelled else { return }
            audit = response
        } catch {
            guard !Task.isCancelled else { return }
            audit = nil
            loadError = error.localizedDescription
        }
    }
}

private struct PendingAuditUndo: Identifiable {
    let write: AuditWrite
    let intent: ActionIntent
    var id: UUID { intent.id }
}

#Preview {
    AuditView()
        .environment(AppState())
}
