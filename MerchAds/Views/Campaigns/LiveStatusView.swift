import SwiftUI

/// Search an ASIN → instant cached view (every ad group it runs in, from the
/// local DB) + "Refresh from Amazon" for LIVE state via the API (status.py).
struct LiveStatusView: View {
    @Environment(AppState.self) private var appState

    @State private var query = ""
    @State private var cached: AsinResponse?
    @State private var cachedError: String?
    @State private var isLoadingCached = false

    @State private var liveStates: [String: LiveStateGroup] = [:]   // ad_group_id → live
    @State private var liveError: String?
    @State private var actionError: String?
    @State private var isLoadingLive = false
    @State private var liveFetchedAt: Date?
    @State private var colPrefs: TableColumnCustomization<AsinAdGroup> = ColumnPrefs.load(TableID.liveStatus)
    @State private var selection = Set<AsinAdGroup.ID>()
    @State private var sortOrder = SortPrefs.load(
        TableID.liveStatus, fields: sortFields,
        fallback: defaultSort)

    private static let sortFields: [String: KeyPathComparator<AsinAdGroup>] = [
        "spend": .init(\.spend), "sales": .init(\.sales),
    ]
    private static let defaultSort = [KeyPathComparator(\AsinAdGroup.spend, order: .reverse)]

    // "Search all markets": the same ASIN looked up in every market with data.
    @AppStorage("liveStatus.allMarkets") private var allMarkets = false
    @State private var marketResults: [(market: String, response: AsinResponse)] = []
    @State private var failedMarkets: [String] = []   // markets the search couldn't read
    @State private var isLoadingAll = false
    @State private var pendingChange: PendingStateChange?
    @State private var pendingBulk: PendingBulkChange?

    private var currency: String? { appState.currentMarket?.currency }

    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "Live Status", subtitle: navigationSubtitle, help: .liveStatus)
            searchBar
            ActionErrorBar(message: $actionError)
            Divider()
            if allMarkets && (!marketResults.isEmpty || isLoadingAll) {
                allMarketsSection
            } else if let cached {
                cachedSection(cached)
            } else if isLoadingCached {
                ProgressView("Looking up ASIN…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let cachedError {
                ContentUnavailableView {
                    Label("Lookup failed", systemImage: "magnifyingglass")
                } description: {
                    Text(cachedError)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)   // keep the search bar pinned top
            } else {
                ContentUnavailableView {
                    Label("Live Status", systemImage: "dot.radiowaves.left.and.right")
                } description: {
                    Text("Enter an ASIN to see every ad group it runs in (cached), then refresh live state from Amazon.")
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)   // ditto — greedy, not centered-as-a-block
            }
        }
        .background(Theme.Colors.canvas)
        .onAppear { consumeAsinRoute() }
        .onChange(of: appState.focusedRoute) { consumeAsinRoute() }
        .onChange(of: colPrefs) { ColumnPrefs.save(TableID.liveStatus, colPrefs) }
        .onChange(of: sortOrder) { SortPrefs.save(TableID.liveStatus, sortOrder, fields: Self.sortFields) }
        .onChange(of: appState.selectedMarket) {
            cached = nil
            liveStates = [:]
            liveFetchedAt = nil
            liveError = nil
            if !query.isEmpty { Task { await lookupCached() } }
        }
        .confirmationDialog(
            pendingChange.map { "\($0.state == "PAUSED" ? "Pause" : "Enable") '\($0.name)'?" } ?? "",
            isPresented: Binding(get: { pendingChange != nil },
                                 set: { if !$0 { pendingChange = nil } }),
            presenting: pendingChange
        ) { change in
            Button(change.state == "PAUSED" ? "Pause" : "Enable", role: .destructive) {
                Task { await setState(change.intent, confirmed: true) }
            }
        }
        .confirmationDialog(
            pendingBulk.map { "\($0.state == "PAUSED" ? "Pause" : "Enable") \($0.intents.count) ad groups?" } ?? "",
            isPresented: Binding(get: { pendingBulk != nil },
                                 set: { if !$0 { pendingBulk = nil } }),
            presenting: pendingBulk
        ) { bulk in
            Button(bulk.state == "PAUSED" ? "Pause All" : "Enable All", role: .destructive) {
                Task { await setStateBulk(bulk.intents) }
            }
        } message: { _ in
            Text("Each change is logged separately and undoable from the Audit Trail.")
        }
    }

    private var navigationSubtitle: String {
        if let liveFetchedAt {
            return "\(appState.selectedMarket) · live evaluated \(liveFetchedAt.formatted(date: .omitted, time: .shortened))"
        }
        return "\(appState.selectedMarket) · cached last-pull state"
    }

    private var searchBar: some View {
        FilterBar {
            TextField("ASIN (e.g. B0EXAMPLE1)", text: $query)
                .textFieldStyle(.roundedBorder)
                .font(.body.monospaced())
                .frame(maxWidth: 360)
                .onSubmit { Task { await lookupCached() } }
                .autocorrectionDisabled()
            Button("Look Up") {
                Task { await lookupCached() }
            }
            .disabled(trimmedQuery.isEmpty || isLoadingCached)
            .help("Instant: every ad group this ASIN runs in, from last night's local snapshot")
            Button {
                Task { await refreshLive() }
            } label: {
                if isLoadingLive {
                    HStack(spacing: Layout.Spacing.xs) {
                        ProgressView().controlSize(.small)
                        Text("Querying Amazon…")
                    }
                } else {
                    Label("Refresh from Amazon", systemImage: "dot.radiowaves.left.and.right")
                }
            }
            .disabled(trimmedQuery.isEmpty || isLoadingLive)
            .help("Queries the Amazon Ads API live for real ENABLED/PAUSED state (a few seconds)")
        } trailing: {
            SavedViewPicker(tableID: TableID.liveStatus,
                            filters: ["query": query, "allMarkets": String(allMarkets)],
                            sortFields: Self.sortFields, defaultSort: Self.defaultSort,
                            sortOrder: $sortOrder, columns: $colPrefs) { filters in
                query = filters["query"] ?? ""
                allMarkets = filters["allMarkets"] == "true"
                if !query.isEmpty { Task { await lookupCached() } }
            }
            Toggle("All markets", isOn: $allMarkets)
                .toggleStyle(.checkbox)
                .help("Look this design up in every market with data — one row group per market")
                .onChange(of: allMarkets) {
                    if allMarkets, !trimmedQuery.isEmpty { Task { await lookupAllMarkets() } }
                }
        }
    }

    /// The same design across US/UK/DE/FR/ES/IT — spend→sales + cached state
    /// per market, without flipping ⌘1-6 and re-searching six times.
    private var allMarketsSection: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: Layout.Spacing.md) {
                SectionHeader(title: "Market lookup",
                              subtitle: "cached last-pull state", count: marketResults.count)
                if !failedMarkets.isEmpty {
                    Label("No answer from: \(failedMarkets.joined(separator: ", ")) — results below are incomplete",
                          systemImage: "exclamationmark.triangle")
                        .font(.caption)
                        .foregroundStyle(Theme.Colors.caution)
                }
                if isLoadingAll {
                    HStack(spacing: Layout.Spacing.xs) {
                        ProgressView().controlSize(.small)
                        Text("Looking up \(trimmedQuery) in every market…")
                            .foregroundStyle(.secondary)
                    }
                }
                ForEach(marketResults, id: \.market) { result in
                    VStack(alignment: .leading, spacing: Layout.Spacing.xs) {
                        HStack(spacing: Layout.Spacing.xs) {
                            Text(result.market).font(.headline)
                            if let type = result.response.productType {
                                StatusBadge.campaignType(type)
                            }
                            if result.response.adGroups.isEmpty {
                                Text("not advertised").font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        ForEach(result.response.adGroups) { group in
                            HStack(spacing: Layout.Spacing.sm) {
                                StatusBadge.campaignType(group.type ?? "standard")
                                Text(group.campaign ?? "—")
                                    .lineLimit(1).truncationMode(.middle)
                                    .frame(maxWidth: 300, alignment: .leading)
                                StatusBadge.entityState(group.stateCached)
                                Spacer()
                                MoneyText(value: group.spend, currency: marketCurrency(result.market))
                                Image(systemName: "arrow.right")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                                MoneyText(value: group.sales, currency: marketCurrency(result.market))
                                PercentText(value: group.acos, label: "ACOS")
                            }
                            .font(.callout)
                            .padding(.leading, Layout.Spacing.sm)
                        }
                    }
                    .padding(Layout.Spacing.sm)
                    .background(Theme.Colors.surface,
                                in: RoundedRectangle(cornerRadius: Layout.Radius.medium))
                }
            }
            .padding(Layout.Spacing.md)
            .frame(maxWidth: 760, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func marketCurrency(_ code: String) -> String? {
        appState.markets.first { $0.code == code }?.currency
    }

    private func lookupAllMarkets() async {
        guard !trimmedQuery.isEmpty else { return }
        isLoadingAll = true
        defer { if !Task.isCancelled { isLoadingAll = false } }
        marketResults = []
        failedMarkets = []
        guard let bridge = try? appState.makeBridge() else {
            failedMarkets = ["all — engine bridge unavailable"]
            return
        }
        for market in appState.markets.filter(\.hasData).map(\.code) {
            do {
                let response = try await bridge.call(AsinResponse.self, ["asin", trimmedQuery],
                                                     market: market)
                guard !Task.isCancelled else { return }
                marketResults.append((market, response))
            } catch is CancellationError {
                return
            } catch {
                // an omitted market used to be indistinguishable from
                // "no results there" — say which ones didn't answer
                guard !Task.isCancelled else { return }
                failedMarkets.append(market)
            }
        }
    }

    @ViewBuilder
    private func cachedSection(_ asin: AsinResponse) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: Layout.Spacing.sm) {
                AsinLink(asin: asin.asin, font: .title3.weight(.semibold).monospaced())
                if let type = asin.productType { StatusBadge.campaignType(type) }
                provenanceBadge
                if let lifetime = asin.lifetimeSales {
                    Text("\(Format.count(Int(lifetime))) lifetime sales")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
                Spacer()
            }
            .padding(.horizontal, Layout.Spacing.md)
            .padding(.top, Layout.Spacing.md)

            metricBand(asin)

            if let liveError {
                Text(liveError)
                    .font(.caption)
                    .foregroundStyle(Theme.Colors.critical)
                    .padding(.horizontal, Layout.Spacing.md)
                    .padding(.bottom, Layout.Spacing.xs)
            }

            if asin.adGroups.isEmpty {
                ContentUnavailableView {
                    Label("Not advertised", systemImage: "questionmark.circle")
                } description: {
                    Text("This ASIN isn't mapped to any ad group in \(appState.selectedMarket).")
                }
            } else {
                Table(asin.adGroups.sorted(using: sortOrder), selection: $selection,
                      sortOrder: $sortOrder.descendingFirst(), columnCustomization: $colPrefs) {
                    TableColumn("Campaign", value: \.campaignValue) { group in
                        HStack(spacing: Layout.Spacing.xs) {
                            StatusBadge.campaignType(group.type ?? "standard")
                            Text(group.campaign ?? "—").lineLimit(1).truncationMode(.middle)
                        }
                    }
                    .width(min: 180, ideal: 280)
                    .customizationID("campaign")
                    TableColumn("Ad group", value: \.adGroupValue) { group in
                        Text(group.adGroup ?? "—").lineLimit(1).truncationMode(.middle)
                    }
                    .width(min: 140, ideal: 220)
                    .customizationID("ad-group")
                    TableColumn(liveFetchedAt == nil ? "State (cached)" : "State (LIVE)", value: \.stateCachedValue) { group in
                        if let live = liveStates[group.adGroupId] {
                            HStack(spacing: Layout.Spacing.xxs) {
                                StatusBadge.entityState(live.adGroupLive)
                                Image(systemName: "dot.radiowaves.left.and.right")
                                    .font(.caption2)
                                    .foregroundStyle(Theme.Colors.positive)
                                    .help("Live from Amazon · campaign is \(live.campaignLive ?? "?")")
                            }
                        } else {
                            StatusBadge.entityState(group.stateCached)
                        }
                    }
                    .width(min: 55, ideal: 100)
                    .customizationID("col3")
                    TableColumn("Bid", value: \.bidValue) { group in
                        MoneyText(value: group.bid, currency: currency)
                    }
                    .width(min: 33, ideal: 60)
                    .customizationID("bid")
                    TableColumn("Spend", value: \.spend) { group in
                        MoneyText(value: group.spend, currency: currency)
                    }
                    .width(min: 38, ideal: 70)
                    .customizationID("spend")
                    TableColumn("Sales", value: \.sales) { group in
                        MoneyText(value: group.sales, currency: currency)
                    }
                    .width(min: 38, ideal: 70)
                    .customizationID("sales")
                    TableColumn("ACOS") { group in PercentText(value: group.acos, label: "ACOS") }
                        .width(min: 33, ideal: 60)
                        .customizationID("acos")
                    TableColumn("CVR", value: \.cvrValue) { group in
                        PercentText(value: group.cvr, label: "CVR", color: .primary)
                    }
                    .width(min: 30, ideal: 55)
                    .customizationID("cvr")
                }
                .contextMenu(forSelectionType: AsinAdGroup.ID.self) { ids in
                    liveStatusContextMenu(ids: ids, groups: asin.adGroups)
                }
                .background(Theme.Colors.surface)
                .frame(minHeight: 120)
            }

            if let liveFetchedAt {
                Text("Live state fetched \(liveFetchedAt.formatted(date: .omitted, time: .standard)) — the cached mirror was healed with what Amazon returned.")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .padding(Layout.Spacing.xs)
            }
        }
    }

    @ViewBuilder
    private var provenanceBadge: some View {
        if let liveFetchedAt {
            StatusBadge(
                text: "Live · evaluated \(liveFetchedAt.formatted(date: .omitted, time: .shortened))",
                symbol: "dot.radiowaves.left.and.right",
                tint: Theme.Colors.positive)
        } else {
            StatusBadge(text: "Cached · last pull", symbol: "externaldrive.fill",
                        tint: Theme.Colors.muted)
        }
    }

    private func metricBand(_ asin: AsinResponse) -> some View {
        let spend = asin.adGroups.reduce(0) { $0 + $1.spend }
        let sales = asin.adGroups.reduce(0) { $0 + $1.sales }
        let acos = sales > 0 ? spend / sales : nil
        return Group {
            HStack(spacing: Layout.Spacing.sm) {
                StatCard(title: "Ad groups", value: Format.count(asin.adGroups.count),
                         symbol: "rectangle.stack.fill")
                    .mdCard()
                StatCard(title: "Spend", value: Format.money(spend, currency: currency),
                         symbol: "creditcard.fill")
                    .mdCard()
                StatCard(title: "Sales", value: Format.money(sales, currency: currency),
                         symbol: "chart.line.uptrend.xyaxis")
                    .mdCard()
                StatCard(title: "ACOS", value: Format.percent(acos),
                         tint: AcosTier.select(acos: acos).color, symbol: "percent")
                    .mdCard()
            }
        }
        .padding(Layout.Spacing.md)
    }

    @ViewBuilder
    private func liveStatusContextMenu(ids: Set<AsinAdGroup.ID>, groups: [AsinAdGroup]) -> some View {
        let selected = groups.filter { ids.contains($0.id) }
        copyMenuItems(selected, primaryLabel: "Ad Group ID",
                      primary: { $0.adGroupId },
                      row: { group in
            [group.campaign ?? "", group.adGroup ?? "", effectiveState(group) ?? "",
             String(group.spend), String(group.sales)].joined(separator: "\t")
        })
        if !selected.isEmpty {
            Divider()
            let enabled = selected.filter { effectiveState($0) == "ENABLED" }
            let paused = selected.filter { effectiveState($0) == "PAUSED" }
            if selected.count == 1, let group = selected.first {
                if effectiveState(group) == "ENABLED" {
                    Button("Pause Ad Group") {
                        request(group.id, group.adGroup ?? group.id, "PAUSED")
                    }
                } else if effectiveState(group) == "PAUSED" {
                    Button("Enable Ad Group") {
                        request(group.id, group.adGroup ?? group.id, "ENABLED")
                    }
                }
            } else {
                if !enabled.isEmpty {
                    Button("Pause \(enabled.count) Ad Groups") {
                        requestBulk(enabled.map(\.adGroupId), "PAUSED")
                    }
                }
                if !paused.isEmpty {
                    Button("Enable \(paused.count) Ad Groups") {
                        requestBulk(paused.map(\.adGroupId), "ENABLED")
                    }
                }
            }
        }
    }

    private func effectiveState(_ group: AsinAdGroup) -> String? {
        liveStates[group.adGroupId]?.adGroupLive ?? group.stateCached
    }

    private var trimmedQuery: String {
        query.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
    }

    /// A palette route landed here with an ASIN — run the lookup immediately.
    private func consumeAsinRoute() {
        guard case .asin(let market, let asin) = appState.focusedRoute,
              market == appState.selectedMarket else { return }
        appState.focusedRoute = nil
        query = asin
        Task { await lookupCached() }
    }

    private func lookupCached() async {
        guard !trimmedQuery.isEmpty else { return }
        if allMarkets {
            await lookupAllMarkets()
            return
        }
        isLoadingCached = true
        defer { if !Task.isCancelled { isLoadingCached = false } }
        cachedError = nil
        liveStates = [:]
        liveFetchedAt = nil
        liveError = nil
        do {
            let bridge = try appState.makeBridge()
            let response = try await bridge.call(AsinResponse.self, ["asin", trimmedQuery],
                                                 market: appState.selectedMarket)
            guard !Task.isCancelled else { return }
            cached = response
        } catch {
            guard !Task.isCancelled else { return }
            cached = nil
            cachedError = error.localizedDescription
        }
    }

    private func request(_ id: AsinAdGroup.ID, _ name: String, _ state: String) {
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
            await refreshAfterAction(intent)
        } catch {
            actionError = error.localizedDescription
        }
    }

    private func setStateBulk(_ intents: [ActionIntent]) async {
        actionError = nil
        var failures = 0
        var appliedIntent: ActionIntent?
        for intent in intents {
            do {
                let receipt = try await appState.actionCoordinator.execute(
                    intent, context: appState.actionPolicyContext, confirmed: true)
                if !receipt.rehearsed { appliedIntent = intent }
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
        if let appliedIntent { await refreshAfterAction(appliedIntent) }
    }

    private func refreshAfterAction(_ intent: ActionIntent) async {
        guard intent.scope.market == appState.selectedMarket else { return }
        let hadLiveState = liveFetchedAt != nil
        await lookupCached()
        if hadLiveState { await refreshLive() }
    }

    private func refreshLive() async {
        guard !trimmedQuery.isEmpty else { return }
        if cached == nil { await lookupCached() }
        isLoadingLive = true
        defer { isLoadingLive = false }
        liveError = nil
        do {
            let bridge = try appState.makeBridge()
            let response = try await bridge.call(LiveStateResponse.self,
                                                 ["livestate", trimmedQuery],
                                                 market: appState.selectedMarket)
            // The engine can return the same ad group twice; keeping the last one
            // beats `uniqueKeysWithValues`, which traps and takes the app down.
            liveStates = Dictionary(response.groups.map { ($0.adGroupId, $0) },
                                    uniquingKeysWith: { _, last in last })
            liveFetchedAt = Date()
        } catch {
            liveError = error.localizedDescription
        }
    }
}

#Preview {
    LiveStatusView()
        .environment(AppState())
}
