import SwiftUI

/// Two lists of designs that deserve the axe, with one-click pause:
/// "Bleeding" = CVR under the floor AND ACOS over break-even (csmetro's rule).
/// "Stale" = plenty of impressions, no clicks, zero lifetime sales.
struct KillListView: View {
    @Environment(AppState.self) private var appState

    enum Mode: String, CaseIterable {
        case bleeding = "Bleeding"
        case stale = "Stale"
    }

    @AppStorage("killList.mode") private var mode: Mode = .bleeding
    @State private var killList: KillListResponse?
    @State private var stale: StaleResponse?
    @State private var loadError: String?
    @State private var actionError: String?
    @State private var isLoading = false
    @State private var sortOrder = SortPrefs.load(
        TableID.killBleeding, fields: sortFields,
        fallback: defaultSort)
    @State private var pausingId: String?
    @State private var pendingPause: PendingStateChange?
    @State private var selection = Set<KillDesign.ID>()
    @State private var primaryID: KillDesign.ID?
    @State private var staleSelection = Set<StaleDesign.ID>()
    @State private var sparedExpanded = false
    @State private var pendingBulkPause: [ActionIntent]?
    @State private var colPrefs: TableColumnCustomization<KillDesign> = ColumnPrefs.load(TableID.killBleeding)
    @State private var staleColPrefs: TableColumnCustomization<StaleDesign> = ColumnPrefs.load(TableID.killStale)
    @State private var staleSort = SortPrefs.load(
        TableID.killStale, fields: staleSortFields,
        fallback: staleDefaultSort)
    @State private var filterText = ""

    private static let sortFields: [String: KeyPathComparator<KillDesign>] = [
        "asin": .init(\.asinValue), "state": .init(\.stateValue),
        "clicks": .init(\.clicks), "orders": .init(\.orders), "cvr": .init(\.cvr),
        "spend": .init(\.spend), "sales": .init(\.sales),
        "acos": .init(\.acosValue), "breakeven": .init(\.breakEvenValue),
    ]
    private static let defaultSort = [KeyPathComparator(\KillDesign.spend, order: .reverse)]

    private static let staleSortFields: [String: KeyPathComparator<StaleDesign>] = [
        "asin": .init(\.asinValue), "type": .init(\.typeValue), "name": .init(\.nameValue),
        "impressions": .init(\.impressions), "clicks": .init(\.clicks), "spend": .init(\.spend),
    ]
    private static let staleDefaultSort = [KeyPathComparator(\StaleDesign.spend, order: .reverse)]

    private var currency: String? { appState.currentMarket?.currency }
    private var primaryDesign: KillDesign? { killList?.designs.first { $0.id == primaryID } }

    /// Actionable first: still-ENABLED designs sort above already-paused ones,
    /// then the user's chosen order within each group.
    private var designs: [KillDesign] {
        (killList?.designs ?? [])
            .filter { filterText.isEmpty || ($0.asin ?? "").localizedStandardContains(filterText) }
            .sorted(using: sortOrder)
            .sorted { ($0.state == "ENABLED" ? 0 : 1) < ($1.state == "ENABLED" ? 0 : 1) }
    }

    /// Selected designs that are still enabled — the bulk-pause set.
    private var selectedEnabled: [KillDesign] {
        (killList?.designs ?? []).filter { selection.contains($0.id) && $0.state == "ENABLED" }
    }

    private var staleDesigns: [StaleDesign] {
        (stale?.designs ?? []).filter {
            filterText.isEmpty
                || ($0.asin ?? "").localizedStandardContains(filterText)
                || ($0.name ?? "").localizedStandardContains(filterText)
        }
        .sorted(using: staleSort)
    }

    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "Kill List", subtitle: appState.selectedMarket, help: .killList)
            statusBand
            FilterBar {
                TextField("Filter by ASIN or name", text: $filterText)
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 180)
            } trailing: {
                SavedViewPicker(tableID: TableID.killBleeding,
                                filters: ["mode": mode.rawValue, "search": filterText],
                                sortFields: Self.sortFields, defaultSort: Self.defaultSort,
                                sortOrder: $sortOrder, columns: $colPrefs) { filters in
                    mode = filters["mode"].flatMap(Mode.init(rawValue:)) ?? .bleeding
                    filterText = filters["search"] ?? ""
                }
                bulkPauseControl
                exportButton
                if appState.killActive {
                    Label("KILL — writes frozen", systemImage: "exclamationmark.octagon.fill")
                        .font(.caption)
                        .foregroundStyle(Theme.Colors.critical)
                }
            }
            ActionErrorBar(message: $actionError)
            SectionHeader(title: mode.rawValue,
                          subtitle: subtitle,
                          count: mode == .bleeding ? designs.count : staleDesigns.count)
                .padding(.horizontal, Layout.Spacing.sm)
            if mode == .bleeding { sparedBanner }
            Divider()

            if isLoading && killList == nil && stale == nil {
                ProgressView("Analyzing designs…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let loadError {
                ContentUnavailableView {
                    Label("Kill list unavailable", systemImage: "xmark.bin")
                } description: {
                    Text(loadError)
                } actions: {
                    Button("Retry") { Task { await load() } }
                }
                .topAlignedEmptyState()
            } else if mode == .stale {
                staleTable
            } else if designs.isEmpty {
                // distinguish "genuinely nothing" from "your filter matched nothing"
                if !(killList?.designs.isEmpty ?? true) && !filterText.isEmpty {
                    ContentUnavailableView.search(text: filterText)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    ContentUnavailableView {
                        Label("Nothing to kill", systemImage: "checkmark.seal")
                    } description: {
                        Text("No design in \(appState.selectedMarket) is below the CVR floor while over break-even.")
                    }
                    .topAlignedEmptyState()
                }
            } else {
                Table(designs, selection: $selection, sortOrder: $sortOrder.descendingFirst(),
                      columnCustomization: $colPrefs) {
                    TableColumn("ASIN", value: \.asinValue) { design in
                        AsinLink(asin: design.asin, hint: design.type, prominent: true)
                    }
                    .width(min: 60, ideal: 110)
                    .customizationID("asin")
                    TableColumn("State", value: \.stateValue) { design in
                        StatusBadge.entityState(design.state)
                    }
                    .width(min: 38, ideal: 70)
                    .customizationID("state")
                    TableColumn("Clicks", value: \.clicks) { design in
                        CountText(value: design.clicks)
                    }
                    .width(min: 30, ideal: 55)
                    .customizationID("clicks")
                    TableColumn("Orders", value: \.orders) { design in
                        CountText(value: design.orders)
                    }
                    .width(min: 30, ideal: 55)
                    .customizationID("orders")
                    TableColumn("CVR", value: \.cvr) { design in
                        PercentText(value: design.cvr, label: "CVR",
                                    color: Theme.Colors.critical)
                    }
                    .width(min: 33, ideal: 60)
                    .customizationID("cvr")
                    TableColumn("Spend", value: \.spend) { design in
                        MoneyText(value: design.spend, currency: currency)
                    }
                    .width(min: 41, ideal: 75)
                    .customizationID("spend")
                    TableColumn("Sales", value: \.sales) { design in
                        MoneyText(value: design.sales, currency: currency)
                    }
                    .width(min: 41, ideal: 75)
                    .customizationID("sales")
                    TableColumn("ACOS", value: \.acosValue) { design in
                        PercentText(value: design.acos, breakEven: design.breakEven, label: "ACOS")
                    }
                    .width(min: 33, ideal: 60)
                    .customizationID("acos")
                    TableColumn("Break-even", value: \.breakEvenValue) { design in
                        PercentText(value: design.breakEven, label: "Break-even ACOS",
                                    color: .secondary)
                    }
                    .width(min: 41, ideal: 75)
                    .customizationID("break-even")
                    TableColumn("") { design in
                        if design.state == "ENABLED" {
                            Button {
                                requestPause(design.adGroupId, design.asin ?? design.adGroupId)
                            } label: {
                                if pausingId == design.adGroupId {
                                    ProgressView().controlSize(.small)
                                } else {
                                    Text("Pause")
                                }
                            }
                            .buttonStyle(.borderless)
                            .disabled(pausingId != nil)
                            .help("Pause this design's ad group (undoable in the Audit Trail)")
                        }
                    }
                    .width(min: 30, ideal: 55)
                    .customizationID("col10")
                }
                .background(Theme.Colors.surface)
                .copyableRows(designs, primaryLabel: "ASIN",
                              primary: { $0.asin ?? $0.adGroupId },
                              row: { "\($0.asin ?? "")\t\($0.state ?? "")\t\($0.clicks)\t\($0.orders)\t\($0.cvr)\t\($0.spend)\t\($0.sales)\t\($0.acos.map { String($0) } ?? "")\t\($0.breakEven.map { String($0) } ?? "")" })
                // Greedy so it fills the window (no dead space below the last row);
                // was content-sized + top-pinned, which capped it at ≤520pt.
                .frame(minHeight: 160)
            }
        }
        .task(id: appState.viewKey) { await load(); await loadStale() }
        .onChange(of: sortOrder) { SortPrefs.save(TableID.killBleeding, sortOrder, fields: Self.sortFields) }
        .onChange(of: staleSort) { SortPrefs.save(TableID.killStale, staleSort, fields: Self.staleSortFields) }
        .onChange(of: colPrefs) { ColumnPrefs.save(TableID.killBleeding, colPrefs) }
        .onChange(of: staleColPrefs) { ColumnPrefs.save(TableID.killStale, staleColPrefs) }
        .onChange(of: selection) { old, new in
            primaryID = PrimaryRow.latest(old: old, new: new, current: primaryID)
        }
        .onChange(of: mode) {
            if mode == .stale && stale == nil { Task { await loadStale() } }
        }
        .confirmationDialog(pendingPause.map { "Pause '\($0.name)'?" } ?? "",
                            isPresented: Binding(get: { pendingPause != nil },
                                                 set: { if !$0 { pendingPause = nil } }),
                            presenting: pendingPause) { pending in
            Button("Pause", role: .destructive) {
                Task { await pauseAdGroup(pending.intent, confirmed: true) }
            }
        }
        .inspector(isPresented: Binding(get: { primaryDesign != nil },
                                        set: { if !$0 { primaryID = nil } })) {
            if let design = primaryDesign {
                KillRowInspectorView(design: design, currency: currency) {
                    requestPause(design.adGroupId, design.asin ?? design.adGroupId)
                }
                .inspectorColumnWidth(min: 280, ideal: 340)
            }
        }
    }

    /// Designs the kill rule wanted, but whose ad drives enough owned cross-sell
    /// royalty to cover their own spend. Pausing them would kill the catalogue
    /// sales the ad creates, so they are held back — and shown here, not hidden.
    @ViewBuilder
    private var sparedBanner: some View {
        let spared = killList?.spared ?? []
        if !spared.isEmpty {
            DisclosureGroup(isExpanded: $sparedExpanded) {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(spared) { sparedRow($0) }
                }
                .padding(.top, 8)
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: "arrow.triangle.branch")
                        .foregroundStyle(Theme.Colors.positive)
                    Text("\(spared.count) design\(spared.count == 1 ? "" : "s") spared — their ads sell your other designs")
                        .font(.callout.weight(.medium))
                    Spacer()
                }
                .contentShape(Rectangle())
            }
            .padding(Layout.Spacing.md)
            .background(Theme.Colors.positive.opacity(0.08),
                        in: RoundedRectangle(cornerRadius: Layout.Radius.medium))
            .padding(.horizontal, Layout.Spacing.sm)
            .padding(.bottom, 4)
        }
    }

    private func sparedRow(_ s: KillSpared) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            AsinLink(asin: s.asin ?? s.adGroupId, prominent: true)
            Text(s.type ?? "").font(.caption).foregroundStyle(.secondary)
            Spacer()
            VStack(alignment: .trailing, spacing: 2) {
                HStack(spacing: 4) {
                    Text("bleeds").font(.caption2).foregroundStyle(.secondary)
                    MoneyText(value: s.spend, currency: currency)
                    Text("· sells").font(.caption2).foregroundStyle(.secondary)
                    MoneyText(value: s.crossSellRoyalty, currency: currency)
                    Text("royalty").font(.caption2).foregroundStyle(.secondary)
                }
                if let top = s.others.first {
                    HStack(spacing: 4) {
                        Text("drove \(s.ownedUnits) unit\(s.ownedUnits == 1 ? "" : "s") of your designs — top")
                            .font(.caption2).foregroundStyle(.secondary)
                        AsinLink(asin: top.asin, font: .caption2.monospaced())
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private var bulkPauseControl: some View {
        if mode == .bleeding && !selectedEnabled.isEmpty {
            Button("Pause \(selectedEnabled.count) Selected") {
                requestPauseSelected()
            }
            .buttonStyle(.borderedProminent)
            .disabled(pausingId != nil)
            .help("Pause every selected design that is still enabled — each is logged and undoable")
            .confirmationDialog(bulkPauseDialogTitle,
                                isPresented: Binding(
                                    get: { pendingBulkPause != nil },
                                    set: { if !$0 { pendingBulkPause = nil } }),
                                presenting: pendingBulkPause) { intents in
                Button("Pause All", role: .destructive) {
                    Task { await pauseSelected(intents) }
                }
            } message: { _ in
                Text("Each pause is logged separately and undoable from the Audit Trail.")
            }
        }
    }

    private var bulkPauseDialogTitle: String {
        "Pause \(pendingBulkPause?.count ?? 0) designs?"
    }

    private var statusBand: some View {
        Group {
            HStack(spacing: Layout.Spacing.sm) {
                StatCardButton(title: "Bleeding", value: killList.map { Format.count($0.count) } ?? "—",
                               tint: (killList?.count ?? 0) > 0 ? Theme.Colors.critical : .primary,
                               symbol: "drop.fill",
                               subtitle: "below CVR floor · over break-even",
                               glassTint: Theme.Colors.critical,
                               isSelected: mode == .bleeding,
                               helpText: "Bleeding = spends but converts too poorly to profit — click to view") {
                    mode = .bleeding
                }
                StatCardButton(title: "Stale", value: stale.map { Format.count($0.count) } ?? "—",
                               tint: (stale?.count ?? 0) > 0 ? Theme.Colors.caution : .primary,
                               symbol: "clock.badge.exclamationmark.fill",
                               subtitle: "visible · ignored by shoppers",
                               glassTint: Theme.Colors.caution,
                               isSelected: mode == .stale,
                               helpText: "Stale = shown by Amazon, ignored by shoppers — click to view") {
                    mode = .stale
                }
                StatCard(title: "CVR floor",
                         value: Format.percent(killList?.cvrFloor, digits: 0),
                         symbol: "scope", subtitle: "economic kill threshold")
                    .mdCard()
            }
        }
        .padding(.horizontal, Layout.Spacing.lg)
        .padding(.vertical, Layout.Spacing.sm)
    }

    /// One-click unless "always confirm" is on (undoable from the Audit Trail).
    private func requestPause(_ adGroupId: String, _ name: String) {
        let intent = appState.marketIntent(
            title: "Pause \(name)", arguments: ["pause", "--adgroup", adGroupId])
        switch appState.actionCoordinator.requirement(
            for: intent, context: appState.actionPolicyContext) {
        case .confirmation:
            pendingPause = PendingStateChange(intent: intent, entityId: adGroupId,
                                              name: name, state: "PAUSED")
        case .blocked(.killActive(let scope)):
            actionError = ActionCoordinatorError.killActive(scope).localizedDescription
        case .preview, .ready:
            Task { await pauseAdGroup(intent) }
        }
    }

    private func pauseAdGroup(_ intent: ActionIntent, confirmed: Bool = false) async {
        pausingId = intent.arguments.last
        defer { pausingId = nil }
        actionError = nil
        do {
            let receipt = try await appState.actionCoordinator.execute(
                intent, context: appState.actionPolicyContext, confirmed: confirmed)
            guard !receipt.rehearsed else { return }
            await load()
        } catch {
            actionError = error.localizedDescription
        }
    }

    private func requestPauseSelected() {
        let intents = selectedEnabled.map { design in
            appState.marketIntent(
                title: "Pause \(design.asin ?? design.adGroupId)",
                arguments: ["pause", "--adgroup", design.adGroupId], cardinality: .bulk)
        }
        guard let first = intents.first else { return }
        switch appState.actionCoordinator.requirement(
            for: first, context: appState.actionPolicyContext) {
        case .confirmation:
            pendingBulkPause = intents
        case .blocked(.killActive(let scope)):
            actionError = ActionCoordinatorError.killActive(scope).localizedDescription
        case .preview, .ready:
            Task { await pauseSelected(intents) }
        }
    }

    /// Bulk pause — sequential per-design calls, each logged and undoable.
    private func pauseSelected(_ intents: [ActionIntent]) async {
        guard !intents.isEmpty else { return }
        pausingId = intents[0].arguments.last
        defer { pausingId = nil }
        actionError = nil
        var failures = 0
        var applied = false
        for intent in intents {
            do {
                let receipt = try await appState.actionCoordinator.execute(
                    intent, context: appState.actionPolicyContext, confirmed: true)
                applied = applied || !receipt.rehearsed
            } catch let error as ActionCoordinatorError {
                actionError = error.localizedDescription
                return
            } catch {
                failures += 1
            }
        }
        if failures > 0 {
            actionError = "\(failures) of \(intents.count) pauses failed — see the Audit Trail"
        }
        guard applied else { return }
        selection.removeAll()
        await load()
    }

    private var subtitle: String {
        switch mode {
        case .bleeding:
            if let killList {
                return "\(killList.count) designs · CVR < \(Format.percent(killList.cvrFloor, digits: 0)) and ACOS over break-even, ≥15 clicks"
            }
            return "CVR under floor and ACOS over break-even"
        case .stale:
            if let stale {
                let cap = stale.count > stale.designs.count
                    ? " · showing top \(stale.designs.count)" : ""
                return "\(stale.count) designs\(cap) · ≥\(stale.minImpressions) impressions, ≤2 clicks, 0 lifetime sales (as of \(stale.asOf.map(Format.euDate) ?? "—"))"
            }
            return "shown by Amazon, skipped by shoppers"
        }
    }

    @ViewBuilder
    private var exportButton: some View {
        switch mode {
        case .bleeding:
            if let killList {
                ExportButton(filename: "kill-list-\(appState.selectedMarket)") {
                    CSVDocument(
                        headers: ["asin", "type", "state", "clicks", "orders", "cvr",
                                  "spend", "sales", "acos", "break_even"],
                        rows: killList.designs.map { design in
                            [design.asin ?? "", design.type ?? "", design.state ?? "",
                             String(design.clicks), String(design.orders), String(design.cvr),
                             String(design.spend), String(design.sales),
                             design.acos.map { String($0) } ?? "",
                             design.breakEven.map { String($0) } ?? ""]
                        })
                }
            }
        case .stale:
            if let stale {
                ExportButton(filename: "stale-designs-\(appState.selectedMarket)") {
                    CSVDocument(
                        headers: ["asin", "type", "name", "impressions", "clicks", "spend"],
                        rows: stale.designs.map { design in
                            [design.asin ?? "", design.type ?? "", design.name ?? "",
                             String(design.impressions), String(design.clicks),
                             String(design.spend)]
                        })
                }
            }
        }
    }

    @ViewBuilder
    private var staleTable: some View {
        if stale != nil, staleDesigns.isEmpty, !(stale?.designs.isEmpty ?? true), !filterText.isEmpty {
            ContentUnavailableView.search(text: filterText)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if stale != nil, !staleDesigns.isEmpty {
            Table(staleDesigns, selection: $staleSelection, sortOrder: $staleSort.descendingFirst(),
                  columnCustomization: $staleColPrefs) {
                TableColumn("ASIN", value: \.asinValue) { design in
                    AsinLink(asin: design.asin, prominent: true)
                }
                .width(min: 60, ideal: 110)
                .customizationID("asin")
                TableColumn("Type", value: \.typeValue) { design in
                    StatusBadge.campaignType(design.type)
                }
                .width(min: 77, ideal: 140)
                .customizationID("type")
                TableColumn("Design", value: \.nameValue) { design in
                    Text(design.name ?? "—")
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                .customizationID("design")
                TableColumn("Impressions", value: \.impressions) { design in
                    CountText(value: design.impressions)
                }
                .width(min: 44, ideal: 80)
                .customizationID("impressions")
                TableColumn("Clicks", value: \.clicks) { design in
                    CountText(value: design.clicks)
                        .foregroundStyle(Theme.Colors.critical)
                }
                .width(min: 27, ideal: 50)
                .customizationID("clicks")
                TableColumn("Spend", value: \.spend) { design in
                    MoneyText(value: design.spend, currency: currency)
                }
                .width(min: 35, ideal: 65)
                .customizationID("spend")
                TableColumn("") { design in
                    Button {
                        requestPause(design.adGroupId, design.asin ?? design.adGroupId)
                    } label: {
                        if pausingId == design.adGroupId {
                            ProgressView().controlSize(.small)
                        } else {
                            Text("Pause")
                        }
                    }
                    .buttonStyle(.borderless)
                    .disabled(pausingId != nil)
                }
                .width(min: 30, ideal: 55)
                .customizationID("col7")
            }
            .background(Theme.Colors.surface)
            .copyableRows(staleDesigns, primaryLabel: "ASIN",
                          primary: { $0.asin ?? $0.adGroupId },
                          row: { "\($0.asin ?? "")\t\($0.type ?? "")\t\($0.name ?? "")\t\($0.impressions)\t\($0.clicks)\t\($0.spend)" })
            // Greedy so it fills the window; was content-sized + top-pinned.
            .frame(minHeight: 160)
        } else if isLoading {
            ProgressView("Finding stale designs…")
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else {
            ContentUnavailableView {
                Label("Nothing stale", systemImage: "checkmark.seal")
            } description: {
                Text("No visible-but-ignored designs in \(appState.selectedMarket).")
            }
            .topAlignedEmptyState()
        }
    }

    private func loadStale() async {
        isLoading = true
        defer { if !Task.isCancelled { isLoading = false } }
        loadError = nil
        stale = nil   // same rule as load(): never show another market's rows
        staleSelection.removeAll()
        do {
            let bridge = try appState.makeBridge()
            let response = try await bridge.call(StaleResponse.self, ["stale"],
                                                 market: appState.selectedMarket)
            guard !Task.isCancelled else { return }
            stale = response
        } catch {
            guard !Task.isCancelled else { return }
            stale = nil
            loadError = error.localizedDescription
        }
    }

    private func load() async {
        isLoading = true
        defer { if !Task.isCancelled { isLoading = false } }
        loadError = nil
        // Drop the previous market's rows BEFORE fetching: currency flips with the
        // market picker, so keeping them would render old money in the new symbol.
        killList = nil
        stale = nil   // market changed (or first load): stale view refetches on demand
        selection.removeAll()
        staleSelection.removeAll()
        primaryID = nil
        do {
            let bridge = try appState.makeBridge()
            let response = try await bridge.call(KillListResponse.self, ["killlist"],
                                                 market: appState.selectedMarket)
            guard !Task.isCancelled else { return }
            killList = response
        } catch {
            guard !Task.isCancelled else { return }
            killList = nil
            loadError = error.localizedDescription
        }
    }
}

#Preview {
    KillListView()
        .environment(AppState())
}
