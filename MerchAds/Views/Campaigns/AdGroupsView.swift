import SwiftUI

// Level 2: ad groups

struct AdGroupsView: View {
    @Environment(AppState.self) private var appState
    let campaign: Campaign

    private static let sortFields: [String: KeyPathComparator<AdGroup>] = [
        "name": .init(\.nameValue), "asin": .init(\.asinValue), "state": .init(\.stateValue),
        "bid": .init(\.bidValue), "lifetime": .init(\.lifetimeValue), "spend": .init(\.spend),
        "sales": .init(\.sales), "acos": .init(\.acosValue), "cvr": .init(\.cvrValue),
        "clicks": .init(\.clicks), "ctr": .init(\.ctrValue), "cpc": .init(\.cpcValue),
    ]

    @State private var adGroups: [AdGroup] = []
    @State private var isLoading = false
    @State private var loadError: String?
    @State private var actionError: String?
    @State private var selection = Set<AdGroup.ID>()
    @State private var primaryID: AdGroup.ID?
    @State private var sortOrder = SortPrefs.load(
        TableID.adGroups, fields: sortFields,
        fallback: [KeyPathComparator(\AdGroup.spend, order: .reverse)])
    @State private var colPrefs: TableColumnCustomization<AdGroup> = ColumnPrefs.load(TableID.adGroups)
    @State private var searchText = ""
    @State private var pendingChange: PendingStateChange?
    @State private var pendingBulk: PendingBulkChange?

    private var currency: String? { appState.currentMarket?.currency }
    private var primaryAdGroup: AdGroup? { adGroups.first { $0.id == primaryID } }

    private var filtered: [AdGroup] {
        adGroups.filter {
            searchText.isEmpty
                || $0.nameValue.localizedStandardContains(searchText)
                || $0.asinValue.localizedStandardContains(searchText)
        }
        .sorted(using: sortOrder)
    }

    var body: some View {
        let visible = filtered
        VStack(spacing: 0) {
            PageHeader(title: "Ad Groups", subtitle: "\(appState.selectedMarket) · \(campaign.nameValue)", help: .campaigns)
            FilterBar {
                StatusBadge.campaignType(campaign.type)
                Text(campaign.nameValue)
                    .font(.headline)
                    .lineLimit(1)
                    .truncationMode(.middle)
                TextField("Filter by name or ASIN", text: $searchText)
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 240)
            } trailing: {
                Text("\(visible.count) of \(adGroups.count)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
            }
            SectionHeader(title: "Ad group inventory",
                          subtitle: "double-click to inspect targets and search terms",
                          count: visible.count)
                .padding(.horizontal, Layout.Spacing.sm)
            ActionErrorBar(message: $actionError)
            Divider()
            if isLoading && adGroups.isEmpty {
                ProgressView("Loading ad groups…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let loadError {
                ContentUnavailableView {
                    Label("Ad groups unavailable", systemImage: "rectangle.3.group")
                } description: {
                    Text(loadError)
                } actions: {
                    Button("Retry") { Task { await load() } }
                }
            } else {
                table(visible)
            }
        }
        .task(id: appState.dataStamp) { await load() }   // reload after the nightly run too
        .onChange(of: sortOrder) { SortPrefs.save(TableID.adGroups, sortOrder, fields: Self.sortFields) }
        .onChange(of: colPrefs) { ColumnPrefs.save(TableID.adGroups, colPrefs) }
        .onChange(of: selection) { old, new in
            primaryID = PrimaryRow.latest(old: old, new: new, current: primaryID)
        }
        .inspector(isPresented: Binding(get: { primaryAdGroup != nil },
                                        set: { if !$0 { primaryID = nil } })) {
            if let adGroup = primaryAdGroup {
                AdGroupInspectorView(
                    campaign: campaign, adGroup: adGroup, currency: currency,
                    toggleState: {
                        request(adGroup.id, adGroup.nameValue,
                                adGroup.state == "ENABLED" ? "PAUSED" : "ENABLED")
                    })
                    .inspectorColumnWidth(min: 300, ideal: 360)
            }
        }
    }

    // Split into two builders so the table stays within the 10-element
    // TableColumnBuilder limit once Impr/CTR/CPC are added.
    @TableColumnBuilder<AdGroup, KeyPathComparator<AdGroup>>
    private var adGroupIdentityColumns: some TableColumnContent<AdGroup, KeyPathComparator<AdGroup>> {
        TableColumn("Ad Group", value: \.nameValue) { adGroup in
            Text(adGroup.nameValue).entityLink()
                .lineLimit(1)
                .truncationMode(.middle)
        }
        .width(min: 200, ideal: 320)
        .customizationID("ad-group")
        TableColumn("ASIN", value: \.asinValue) { adGroup in
            AsinLink(asin: adGroup.asin)
        }
        .width(min: 55, ideal: 100)
        .customizationID("asin")
        TableColumn("State", value: \.stateValue) { adGroup in
            StatusBadge.entityState(adGroup.state)
        }
        .width(min: 38, ideal: 70)
        .customizationID("state")
        TableColumn("Bid", value: \.bidValue) { adGroup in
            MoneyText(value: adGroup.defaultBid, currency: currency)
        }
        .width(min: 33, ideal: 60)
        .customizationID("bid")
        TableColumn("Lifetime", value: \.lifetimeValue) { adGroup in
            CountText(value: adGroup.lifetimeSales.map(Int.init))
        }
        .width(min: 33, ideal: 60)
        .customizationID("lifetime")
    }

    @TableColumnBuilder<AdGroup, KeyPathComparator<AdGroup>>
    private var adGroupMetricColumns: some TableColumnContent<AdGroup, KeyPathComparator<AdGroup>> {
        TableColumn("Spend", value: \.spend) { adGroup in
            MoneyText(value: adGroup.spend, currency: currency)
        }
        .width(min: 44, ideal: 80)
        .customizationID("spend")
        TableColumn("Sales", value: \.sales) { adGroup in
            MoneyText(value: adGroup.sales, currency: currency)
        }
        .width(min: 44, ideal: 80)
        .customizationID("sales")
        TableColumn("ACOS", value: \.acosValue) { adGroup in
            PercentText(value: adGroup.acos, label: "ACOS")
        }
        .width(min: 33, ideal: 60)
        .customizationID("acos")
        TableColumn("CVR", value: \.cvrValue) { adGroup in
            PercentText(value: adGroup.cvr, label: "CVR", color: .primary)
        }
        .width(min: 33, ideal: 60)
        .customizationID("cvr")
        TableColumn("Clicks", value: \.clicks) { adGroup in
            CountText(value: adGroup.clicks)
        }
        .width(min: 30, ideal: 55)
        .customizationID("clicks")
        TableColumn("CTR", value: \.ctrValue) { adGroup in
            PercentText(value: adGroup.ctr, label: "CTR", color: .primary, digits: 2)
        }
        .width(min: 33, ideal: 60)
        .customizationID("ctr")
        TableColumn("CPC", value: \.cpcValue) { adGroup in
            MoneyText(value: adGroup.cpc, currency: currency)
        }
        .width(min: 38, ideal: 70)
        .customizationID("cpc")
        TableColumn("Impr.", value: \.impressionsValue) { adGroup in
            CountText(value: adGroup.impressions)
        }
        .width(min: 38, ideal: 70)
        .customizationID("impr")
    }

    private func table(_ visible: [AdGroup]) -> some View {
        Table(visible, selection: $selection, sortOrder: $sortOrder.descendingFirst(),
              columnCustomization: $colPrefs) {
            adGroupIdentityColumns
            adGroupMetricColumns
        }
        .contextMenu(forSelectionType: AdGroup.ID.self) { ids in
            let selected = adGroups.filter { ids.contains($0.id) }
            copyMenuItems(selected, primaryLabel: "ASIN",
                          primary: { $0.asinValue.isEmpty ? $0.nameValue : $0.asinValue },
                          row: { "\($0.asinValue)\t\($0.nameValue)\t\($0.spend)\t\($0.sales)" })
            Divider()
            if selected.count <= 1 {
                Button("Show Targets & Search Terms") { open(ids) }
                Divider()
                if let adGroup = selected.first {
                    if adGroup.state == "ENABLED" {
                        Button("Pause Ad Group") { request(adGroup.id, adGroup.nameValue, "PAUSED") }
                    } else {
                        Button("Enable Ad Group") { request(adGroup.id, adGroup.nameValue, "ENABLED") }
                    }
                }
            } else {
                let enabled = selected.filter { $0.state == "ENABLED" }.map(\.adGroupId)
                let paused = selected.filter { $0.state == "PAUSED" }.map(\.adGroupId)
                if !enabled.isEmpty {
                    Button("Pause \(enabled.count) Ad Groups") {
                        requestBulk(enabled, "PAUSED")
                    }
                }
                if !paused.isEmpty {
                    Button("Enable \(paused.count) Ad Groups") {
                        requestBulk(paused, "ENABLED")
                    }
                }
            }
        } primaryAction: { ids in
            open(ids)
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
        .confirmationDialog(pendingBulk.map { "\($0.state == "PAUSED" ? "Pause" : "Enable") \($0.intents.count) ad groups?" } ?? "",
                            isPresented: Binding(get: { pendingBulk != nil },
                                                 set: { if !$0 { pendingBulk = nil } }),
                            presenting: pendingBulk) { bulk in
            Button(bulk.state == "PAUSED" ? "Pause All" : "Enable All", role: .destructive) {
                Task { await setStateBulk(bulk.intents) }
            }
        } message: { _ in
            Text("Each change is logged separately and undoable from the Audit Trail.")
        }
    }

    /// Single pause/enable = a small action: one-click unless "always confirm"
    /// (appctl guards KILL, logs, and it's undoable from the Audit Trail).
    private func request(_ id: AdGroup.ID, _ name: String, _ state: String) {
        let verb = state == "PAUSED" ? "pause" : "enable"
        let intent = appState.marketIntent(
            title: "\(state == "PAUSED" ? "Pause" : "Enable") ad group \(name)",
            arguments: [verb, "--adgroup", id])
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
        let verb = state == "PAUSED" ? "pause" : "enable"
        let intents = ids.map { id in
            appState.marketIntent(
                title: "\(state == "PAUSED" ? "Pause" : "Enable") ad group",
                arguments: [verb, "--adgroup", id], cardinality: .bulk)
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
            actionError = "\(failures) of \(intents.count) ad groups failed — see the Audit Trail"
        }
        if applied { await load() }
    }

    private func open(_ ids: Set<AdGroup.ID>) {
        guard let id = ids.first,
              let adGroup = adGroups.first(where: { $0.id == id }) else { return }
        appState.campaignPath.append(.adGroup(market: appState.selectedMarket,
                                              campaignID: campaign.campaignId,
                                              adGroupID: adGroup.adGroupId))
    }

    private func load() async {
        isLoading = true
        defer { if !Task.isCancelled { isLoading = false } }
        loadError = nil
        do {
            let bridge = try appState.makeBridge()
            let response = try await bridge.call(
                AdGroupsResponse.self, ["adgroups", "--campaign", campaign.campaignId],
                market: appState.selectedMarket)
            guard !Task.isCancelled else { return }
            adGroups = response.adGroups
        } catch {
            guard !Task.isCancelled else { return }
            adGroups = []
            loadError = error.localizedDescription
        }
    }
}

// MARK: - Level 3: targets + search terms
