import SwiftUI

// Level 1: campaigns

struct CampaignListView: View {
    @Environment(AppState.self) private var appState

    private static let sortFields: [String: KeyPathComparator<Campaign>] = [
        "name": .init(\.nameValue), "state": .init(\.stateValue), "budget": .init(\.budgetValue),
        "spend": .init(\.spend), "sales": .init(\.sales), "acos": .init(\.acosValue),
        "cvr": .init(\.cvrValue), "orders": .init(\.orders), "clicks": .init(\.clicks),
        "ctr": .init(\.ctrValue), "cpc": .init(\.cpcValue),
    ]
    private static let defaultSort = [KeyPathComparator(\Campaign.spend, order: .reverse)]

    @State private var campaigns: [Campaign] = []
    @State private var syncDays: [SyncDay] = []
    @State private var scopedDays: [SyncDay] = []   // chart for the selected campaigns
    @State private var isLoading = false
    @State private var loadError: String?
    @State private var actionError: String?    // pause/enable/budget failures — inline
    @State private var selection = Set<Campaign.ID>()
    @State private var primaryID: Campaign.ID?
    @State private var sortOrder = SortPrefs.load(
        TableID.campaigns, fields: sortFields,
        fallback: defaultSort)
    @State private var colPrefs: TableColumnCustomization<Campaign> = ColumnPrefs.load(TableID.campaigns)
    @State private var typeFilter = "all"
    @State private var stateFilter = "all"
    @State private var searchText = ""
    @State private var pendingChange: PendingStateChange?
    @State private var pendingBulk: PendingBulkChange?
    @State private var pendingArchive: PendingBulkChange?   // irreversible — its own dialog
    @State private var editingBudget: Campaign?
    @State private var historyTarget: Campaign?
    @State private var loadedMarket: String?   // whose campaigns/selection are on screen

    private static let types = ["all", "standard", "lottery", "scavenger", "harvested"]
    private static let states = ["all", "ENABLED", "PAUSED", "ARCHIVED"]

    private var currency: String? { appState.currentMarket?.currency }

    // Main chart re-scopes to the selected campaign rows (MerchDash behaviour):
    // none selected → account-wide synccal; one or more → their summed daily series.
    // Never blank the chart: fall back to the account-wide series while the scoped
    // per-campaign series is loading (or unavailable).
    private var chartIsScoped: Bool { !selection.isEmpty && !scopedDays.isEmpty }
    private var chartDisplayDays: [SyncDay] { chartIsScoped ? scopedDays : syncDays }
    // The label follows the series actually on screen — while the scoped series is
    // still loading (or failed) the chart is the account-wide one, and saying
    // "3 selected campaigns" over it would be a lie.
    private var chartScopeLabel: String {
        chartIsScoped
            ? "\(selection.count) selected campaign\(selection.count == 1 ? "" : "s")"
            : "all campaigns"
    }
    private var primaryCampaign: Campaign? { campaigns.first { $0.id == primaryID } }

    /// In the order the rows are shown, so the inspector's list reads down the
    /// table rather than in an order of its own.
    private var selectedCampaigns: [Campaign] {
        filtered.filter { selection.contains($0.id) }
    }

    /// One selected row gets the detail inspector; several get the list of all
    /// of them. The inspector opens and closes on `primaryID`, so closing it
    /// leaves the selection — and the chart scoped to it — alone.
    @ViewBuilder
    private var inspectorContent: some View {
        let selected = selectedCampaigns
        if selected.count > 1 {
            CampaignMultiInspectorView(
                campaigns: selected, currency: currency,
                pauseAll: { requestBulk($0.map(\.campaignId), "PAUSED") },
                enableAll: { requestBulk($0.map(\.campaignId), "ENABLED") })
        } else if let campaign = primaryCampaign {
            CampaignInspectorView(
                campaign: campaign, currency: currency,
                toggleState: {
                    request(campaign.id, campaign.nameValue,
                            campaign.state == "ENABLED" ? "PAUSED" : "ENABLED")
                },
                editBudget: { editingBudget = campaign })
        }
    }

    private var filtered: [Campaign] {
        campaigns.filter { campaign in
            (typeFilter == "all" || campaign.type == typeFilter)
                && (stateFilter == "all" || campaign.state == stateFilter)
                && (searchText.isEmpty
                    || campaign.nameValue.localizedStandardContains(searchText))
        }
        .sorted(using: sortOrder)
    }

    var body: some View {
        let visible = filtered   // filter+sort once per body eval
        VStack(spacing: 0) {
            PageHeader(title: "Campaigns",
                       subtitle: "\(visible.count) campaigns · trailing 30-day snapshot", help: .campaigns)
            if !chartDisplayDays.isEmpty {
                MetricChipsTrend(days: chartDisplayDays, currency: currency,
                                 scopeLabel: chartScopeLabel)
                    .padding(.horizontal, Layout.Spacing.lg)
                    .padding(.bottom, Layout.Spacing.sm)
            }
            filterBar(visible)
            ActionErrorBar(message: $actionError)
            Divider()
            if isLoading && campaigns.isEmpty {
                ProgressView("Loading campaigns…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let loadError {
                ContentUnavailableView {
                    Label("Campaigns unavailable", systemImage: "megaphone")
                } description: {
                    Text(loadError)
                } actions: {
                    Button("Retry") { Task { await load() } }
                }
            } else {
                table(visible)
            }
        }
        // A new market means new campaign IDs: drop the old selection and its
        // scoped chart, or a US series stays on screen labelled over DE rows.
        .task(id: appState.viewKey) {
            if loadedMarket != appState.selectedMarket {
                loadedMarket = appState.selectedMarket
                selection = []
                scopedDays = []
                primaryID = nil
            }
            await load()
        }
        // Per-market filters: remember Type/State per market, reload on switch.
        .task(id: appState.selectedMarket) {
            typeFilter = FilterPrefs.load("campaigns.type", market: appState.selectedMarket, default: "all")
            stateFilter = FilterPrefs.load("campaigns.state", market: appState.selectedMarket, default: "all")
        }
        .onChange(of: typeFilter) {
            FilterPrefs.save("campaigns.type", market: appState.selectedMarket, typeFilter)
        }
        .onChange(of: stateFilter) {
            FilterPrefs.save("campaigns.state", market: appState.selectedMarket, stateFilter)
        }
        .onChange(of: sortOrder) { SortPrefs.save(TableID.campaigns, sortOrder, fields: Self.sortFields) }
        .onChange(of: colPrefs) { ColumnPrefs.save(TableID.campaigns, colPrefs) }
        .onChange(of: selection) { old, new in
            primaryID = PrimaryRow.latest(old: old, new: new, current: primaryID)
            Task { await loadScopedChart(new) }
        }
        .sheet(item: $editingBudget) { campaign in
            MoneyEntrySheet(
                title: "Edit Daily Budget",
                current: campaign.budget,
                minimum: 1.0,
                subtitle: campaign.nameValue,
                currentLabel: "Current budget",
                currentValue: campaign.budget.map { Format.money($0, currency: currency) },
                caution: campaign.budgetUse.flatMap { use -> String? in
                    guard use >= 0.9 else { return nil }
                    return "Averaging \(Format.percent(use, digits: 0)) of budget — likely capped"
                },
                fieldLabel: "New daily budget",
                prompt: "10.00",
                confirmLabel: "Set Budget") { newBudget in
                await setBudget(campaign, newBudget)
            }
        }
        .sheet(item: $historyTarget) { campaign in
            PerformanceHistorySheet(title: campaign.nameValue,
                                    args: ["history", "--campaign", campaign.campaignId],
                                    currency: currency)
        }
        .inspector(isPresented: Binding(get: { primaryCampaign != nil },
                                        set: { if !$0 { primaryID = nil } })) {
            inspectorContent
                .inspectorColumnWidth(min: 300, ideal: 360)
        }
    }

    private func filterBar(_ visible: [Campaign]) -> some View {
        FilterBar {
            // Sits directly above the table's checkbox column. Selects every
            // campaign matching the CURRENT filters — that is what pairs with
            // the bulk pause/enable/archive actions in the context menu.
            // (SwiftUI's Table takes only a text title for a column header, so
            // this cannot live in the header row itself.)
            Toggle("", isOn: Binding(
                get: { !visible.isEmpty && visible.allSatisfy { selection.contains($0.id) } },
                set: { on in
                    if on {
                        selection = Set(visible.map(\.id))
                    } else {
                        selection.removeAll()
                    }
                }))
                .toggleStyle(.checkbox)
                .labelsHidden()
                .disabled(visible.isEmpty)
                .accessibilityLabel("Select all campaigns matching the current filters")
                .help(selection.isEmpty
                      ? "Select all \(visible.count) campaigns matching the current filters"
                      : "\(selection.count) selected — click to clear")
            Picker("Type", selection: $typeFilter) {
                ForEach(Self.types, id: \.self) { Text($0.capitalized).tag($0) }
            }
            .fixedSize()
            Picker("State", selection: $stateFilter) {
                ForEach(Self.states, id: \.self) { Text($0 == "all" ? "All" : $0.capitalized).tag($0) }
            }
            .fixedSize()
            TextField("Filter by name", text: $searchText, prompt: Text("Filter by name"))
                .textFieldStyle(.roundedBorder)
                .frame(maxWidth: 260)
        } trailing: {
            SavedViewPicker(tableID: TableID.campaigns,
                            filters: ["type": typeFilter, "state": stateFilter, "search": searchText],
                            sortFields: Self.sortFields, defaultSort: Self.defaultSort,
                            sortOrder: $sortOrder, columns: $colPrefs) { filters in
                typeFilter = filters["type"] ?? "all"
                stateFilter = filters["state"] ?? "all"
                searchText = filters["search"] ?? ""
            }
            ExportButton(filename: "campaigns-\(appState.selectedMarket)") {
                CSVDocument(
                    headers: ["campaign", "type", "state", "budget", "spend", "sales",
                              "orders", "clicks", "impressions", "acos", "cvr"],
                    rows: visible.map { campaign in
                        [campaign.nameValue, campaign.type, campaign.state ?? "",
                         campaign.budget.map { String($0) } ?? "",
                         String(campaign.spend), String(campaign.sales),
                         String(campaign.orders), String(campaign.clicks),
                         campaign.impressions.map { String($0) } ?? "",
                         campaign.acos.map { String($0) } ?? "",
                         campaign.cvr.map { String($0) } ?? ""]
                    })
            }
            Text("\(visible.count) of \(campaigns.count) · trailing 30d")
                .font(.caption)
                .foregroundStyle(.secondary)
                .monospacedDigit()
                .help("Spend/sales/clicks columns are the trailing-30-day snapshot the bid rules act on")
        }
    }

    // Split out so the 12-column TableColumnBuilder stays within its 10-element
    // limit (and type-checks quickly): identity columns / money columns / counts.
    @TableColumnBuilder<Campaign, KeyPathComparator<Campaign>>
    private var identityColumns: some TableColumnContent<Campaign, KeyPathComparator<Campaign>> {
        TableColumn("") { (campaign: Campaign) in
            Toggle("", isOn: Binding(
                get: { selection.contains(campaign.id) },
                set: { on in
                    if on { selection.insert(campaign.id) } else { selection.remove(campaign.id) }
                }))
                .labelsHidden()
                .toggleStyle(.checkbox)
                .accessibilityLabel("Select \(campaign.nameValue) for the chart")
        }
        .width(24)
        .customizationID("select")
        TableColumn("Campaign", value: \.nameValue) { (campaign: Campaign) in
            Text(campaign.nameValue).entityLink()
                .lineLimit(1)
                .truncationMode(.middle)
        }
        .width(min: 180, ideal: 280)
        .customizationID("campaign")
        TableColumn("Type", value: \.type) { (campaign: Campaign) in
            StatusBadge.campaignType(campaign.type)
        }
        .width(min: 49, ideal: 90)
        .customizationID("type")
        TableColumn("State", value: \.stateValue) { (campaign: Campaign) in
            StatusBadge.entityState(campaign.state)
        }
        .width(min: 38, ideal: 70)
        .customizationID("state")
        TableColumn("Budget", value: \.budgetValue) { (campaign: Campaign) in
            MoneyText(value: campaign.budget, currency: currency)
        }
        .width(min: 38, ideal: 70)
        .customizationID("budget")
        TableColumn("Bud. use", value: \.budgetUseValue) { (campaign: Campaign) in
            // avg daily spend / daily budget — ≥90% mirrors the budget_max alert
            if let use = campaign.budgetUse {
                PercentText(value: use, label: "Budget use",
                            color: use >= 0.9 ? Theme.Colors.caution : Color.secondary,
                            digits: 0)
                    .help(use >= 0.9
                          ? "Averaging \(Format.percent(use, digits: 0)) of the daily budget — likely capped; consider raising it (right-click → Edit Budget)"
                          : "Trailing-30 average daily spend vs the daily budget")
            } else {
                Text("—").foregroundStyle(.quaternary)
            }
        }
        .width(min: 40, ideal: 65)
        .customizationID("budget-use")
    }

    @TableColumnBuilder<Campaign, KeyPathComparator<Campaign>>
    private var metricColumns: some TableColumnContent<Campaign, KeyPathComparator<Campaign>> {
        TableColumn("Spend", value: \.spend) { (campaign: Campaign) in
            MoneyText(value: campaign.spend, currency: currency)
        }
        .width(min: 44, ideal: 80)
        .customizationID("spend")
        TableColumn("Sales", value: \.sales) { (campaign: Campaign) in
            MoneyText(value: campaign.sales, currency: currency)
        }
        .width(min: 44, ideal: 80)
        .customizationID("sales")
        TableColumn("ACOS", value: \.acosValue) { (campaign: Campaign) in
            PercentText(value: campaign.acos, label: "ACOS")
        }
        .width(min: 33, ideal: 60)
        .customizationID("acos")
        TableColumn("CVR", value: \.cvrValue) { (campaign: Campaign) in
            PercentText(value: campaign.cvr, label: "CVR", color: .primary)
        }
        .width(min: 33, ideal: 60)
        .customizationID("cvr")
        TableColumn("Orders", value: \.orders) { (campaign: Campaign) in
            CountText(value: campaign.orders)
        }
        .width(min: 30, ideal: 55)
        .customizationID("orders")
        TableColumn("Clicks", value: \.clicks) { (campaign: Campaign) in
            CountText(value: campaign.clicks)
        }
        .width(min: 30, ideal: 55)
        .customizationID("clicks")
        TableColumn("CTR", value: \.ctrValue) { (campaign: Campaign) in
            PercentText(value: campaign.ctr, label: "CTR", color: .primary, digits: 2)
        }
        .width(min: 33, ideal: 60)
        .customizationID("ctr")
        TableColumn("CPC", value: \.cpcValue) { (campaign: Campaign) in
            MoneyText(value: campaign.cpc, currency: currency)
        }
        .width(min: 38, ideal: 70)
        .customizationID("cpc")
        TableColumn("Impr.", value: \.impressionsValue) { (campaign: Campaign) in
            CountText(value: campaign.impressions)
        }
        .width(min: 38, ideal: 70)
        .customizationID("impressions")
    }

    private func table(_ visible: [Campaign]) -> some View {
        Table(visible, selection: $selection, sortOrder: $sortOrder.descendingFirst(),
              columnCustomization: $colPrefs) {
            identityColumns
            metricColumns
        }
        .contextMenu(forSelectionType: Campaign.ID.self) { ids in
            let selected = campaigns.filter { ids.contains($0.id) }
            copyMenuItems(selected, primaryLabel: "Campaign",
                          primary: { $0.nameValue },
                          row: { "\($0.nameValue)\t\($0.campaignId)\t\($0.spend)\t\($0.sales)" })
            Divider()
            if selected.count <= 1 {
                Button("Show Ad Groups") { open(ids) }
                Divider()
                if let campaign = selected.first {
                    if campaign.state == "ENABLED" {
                        Button("Pause Campaign") { request(campaign.id, campaign.nameValue, "PAUSED") }
                    } else {
                        Button("Enable Campaign") { request(campaign.id, campaign.nameValue, "ENABLED") }
                    }
                    Button("Edit Budget…") { editingBudget = campaign }
                    if campaign.state != "ARCHIVED" {
                        Button("Archive Campaign…", role: .destructive) {
                            requestArchive([campaign.campaignId])
                        }
                    }
                    Button("Performance History") { historyTarget = campaign }
                }
            } else {
                // multi-select: bulk state changes (always confirmed)
                let enabled = selected.filter { $0.state == "ENABLED" }.map(\.campaignId)
                let paused = selected.filter { $0.state == "PAUSED" }.map(\.campaignId)
                if !enabled.isEmpty {
                    Button("Pause \(enabled.count) Campaigns") {
                        requestBulk(enabled, "PAUSED")
                    }
                }
                if !paused.isEmpty {
                    Button("Enable \(paused.count) Campaigns") {
                        requestBulk(paused, "ENABLED")
                    }
                }
                let archivable = selected.filter { $0.state != "ARCHIVED" }.map(\.campaignId)
                if !archivable.isEmpty {
                    Button("Archive \(archivable.count) Campaigns…", role: .destructive) {
                        requestArchive(archivable)
                    }
                }
            }
        } primaryAction: { ids in
            open(ids)   // double-click drills in
        }
        .background(Theme.Colors.surface)
        .confirmationDialog(pendingChange.map { "\($0.state == "PAUSED" ? "Pause" : "Enable") '\($0.name)'?" } ?? "",
                            isPresented: Binding(get: { pendingChange != nil },
                                                 set: { if !$0 { pendingChange = nil } }),
                            presenting: pendingChange) { change in
            Button(change.state == "PAUSED" ? "Pause" : "Enable", role: .destructive) {
                Task { await setState(change.intent, confirmed: true) }
            }
        }
        .confirmationDialog(pendingBulk.map { "\($0.state == "PAUSED" ? "Pause" : "Enable") \($0.intents.count) campaigns?" } ?? "",
                            isPresented: Binding(get: { pendingBulk != nil },
                                                 set: { if !$0 { pendingBulk = nil } }),
                            presenting: pendingBulk) { bulk in
            Button(bulk.state == "PAUSED" ? "Pause All" : "Enable All", role: .destructive) {
                Task { await setStateBulk(bulk.intents) }
            }
        } message: { bulk in
            Text("Each change is logged separately and undoable from the Audit Trail.")
        }
        .confirmationDialog(
            pendingArchive.map {
                $0.intents.count == 1
                    ? "Archive this campaign permanently?"
                    : "Archive \($0.intents.count) campaigns permanently?"
            } ?? "",
            isPresented: Binding(get: { pendingArchive != nil },
                                 set: { if !$0 { pendingArchive = nil } }),
            titleVisibility: .visible,
            presenting: pendingArchive) { bulk in
            Button("Archive Permanently", role: .destructive) {
                Task { await setStateBulk(bulk.intents) }
            }
            Button("Cancel", role: .cancel) { pendingArchive = nil }
        } message: { _ in
            Text("Amazon does not allow un-archiving. These campaigns leave the Amazon console for good and can never be re-enabled. Their banked history stays in this app. Pausing is the reversible alternative.")
        }
    }

    /// Single state change = small action: one-click unless "always confirm" is on.
    private func request(_ id: Campaign.ID, _ name: String, _ state: String) {
        let verb = state == "PAUSED" ? "pause-campaign" : "enable-campaign"
        let intent = appState.marketIntent(
            title: "\(state == "PAUSED" ? "Pause" : "Enable") campaign \(name)",
            arguments: [verb, "--campaign", id])
        switch appState.actionCoordinator.requirement(
            for: intent, context: appState.actionPolicyContext) {
        case .confirmation:
            pendingChange = PendingStateChange(intent: intent, entityId: id,
                                               name: name, state: state)
        case .blocked(.killActive(let scope)):
            // Say it here rather than making a round trip the coordinator will refuse.
            actionError = ActionCoordinatorError.killActive(scope).localizedDescription
        case .preview, .ready:
            Task { await setState(intent) }
        }
    }

    private func requestBulk(_ ids: [String], _ state: String) {
        let verb = state == "PAUSED" ? "pause-campaign" : "enable-campaign"
        let intents = ids.map { id in
            appState.marketIntent(
                title: "\(state == "PAUSED" ? "Pause" : "Enable") campaign",
                arguments: [verb, "--campaign", id], cardinality: .bulk)
        }
        guard let first = intents.first else { return }
        switch appState.actionCoordinator.requirement(
            for: first, context: appState.actionPolicyContext) {
        case .confirmation:
            pendingBulk = PendingBulkChange(intents: intents, state: state)
        case .blocked(.killActive(let scope)):
            // Say it here rather than making a round trip the coordinator will refuse.
            actionError = ActionCoordinatorError.killActive(scope).localizedDescription
        case .preview, .ready:
            Task { await setStateBulk(intents) }
        }
    }

    /// Archiving is PERMANENT on Amazon, so it always confirms (regardless of the
    /// "always confirm" setting) and gets its own dialog rather than sharing the
    /// pause/enable one. `--confirm` is required by the engine for the same reason.
    private func requestArchive(_ ids: [String]) {
        let intents = ids.map { id in
            appState.marketIntent(
                title: "Archive campaign \(id)",
                arguments: ["archive-campaign", "--campaign", id, "--confirm"],
                cardinality: ids.count > 1 ? .bulk : .single,
                confirmationPolicy: .required)
        }
        guard let first = intents.first else { return }
        if case .blocked(.killActive(let scope)) = appState.actionCoordinator.requirement(
            for: first, context: appState.actionPolicyContext) {
            actionError = ActionCoordinatorError.killActive(scope).localizedDescription
            return
        }
        pendingArchive = PendingBulkChange(intents: intents, state: "ARCHIVED")
    }

    private func setState(_ intent: ActionIntent, confirmed: Bool = false) async {
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

    /// Sequential per-campaign calls: each is logged and undoable on its own.
    private func setStateBulk(_ intents: [ActionIntent]) async {
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
            actionError = "\(failures) of \(intents.count) campaigns failed — see the Audit Trail"
        }
        if applied { await load() }
    }

    private func setBudget(_ campaign: Campaign, _ newBudget: Double) async {
        actionError = nil
        do {
            var args = ["setbudget", "--campaign", campaign.campaignId,
                        "--budget", String(format: "%.2f", newBudget)]
            if let current = campaign.budget {
                args += ["--prev", String(format: "%.2f", current)]
            }
            let intent = appState.marketIntent(
                title: "Set budget for \(campaign.nameValue)", arguments: args)
            let receipt = try await appState.actionCoordinator.execute(
                intent, context: appState.actionPolicyContext, confirmed: true)
            guard !receipt.rehearsed else { return }
            await load()
        } catch {
            actionError = error.localizedDescription
        }
    }

    private func open(_ ids: Set<Campaign.ID>) {
        guard let id = ids.first,
              let campaign = campaigns.first(where: { $0.id == id }) else { return }
        appState.campaignPath.append(.campaign(market: appState.selectedMarket,
                                               campaignID: campaign.campaignId))
    }

    private func load() async {
        let market = appState.selectedMarket   // only this market's responses may land
        isLoading = true
        defer { if !Task.isCancelled { isLoading = false } }
        loadError = nil
        do {
            let bridge = try appState.makeBridge()
            let response = try await bridge.call(CampaignsResponse.self, ["campaigns"],
                                                 market: market)
            guard !Task.isCancelled, market == appState.selectedMarket else { return }
            campaigns = response.campaigns
        } catch {
            guard !Task.isCancelled, market == appState.selectedMarket else { return }
            campaigns = []
            loadError = error.localizedDescription
        }
        if let bridge = try? appState.makeBridge() {
            let days = (try? await bridge.call(SyncCalResponse.self, ["synccal"],
                                               market: market))?.days ?? []
            guard !Task.isCancelled, market == appState.selectedMarket else { return }
            syncDays = days
        }
    }

    /// Re-scope the main chart to the selected campaign rows (empty = account-wide).
    /// Last request wins: every toggle spawns a call, so a slower earlier response
    /// must be dropped rather than painted under the newer scope.
    private func loadScopedChart(_ ids: Set<Campaign.ID>) async {
        guard !ids.isEmpty else { scopedDays = []; return }
        let market = appState.selectedMarket
        do {
            let bridge = try appState.makeBridge()
            let resp = try await bridge.call(
                CampaignDailyResponse.self,
                ["campaigndaily", "--campaigns", ids.joined(separator: ",")],
                market: market)
            guard !Task.isCancelled, ids == selection,
                  market == appState.selectedMarket else { return }
            scopedDays = resp.days
        } catch {
            guard !Task.isCancelled, ids == selection,
                  market == appState.selectedMarket else { return }
            scopedDays = []
        }
    }
}

// The budget sheet is MoneyEntrySheet (CampaignBrowserSheets.swift), configured
// at the .sheet call site above.

// MARK: - Level 2: ad groups
