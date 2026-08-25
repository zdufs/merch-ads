import SwiftUI
import AppKit

/// Account-wide Targets tab (MerchDash parity): every keyword / product target
/// across all campaigns in one sortable, filterable, exportable table. Read-only
/// data view — drill into a single ad group's targets from the Campaign browser
/// when you want to act on one.
struct TargetsView: View {
    @Environment(AppState.self) private var appState

    private static let sortFields: [String: KeyPathComparator<AllTargetRow>] = [
        "targeting": .init(\.targetingValue), "match": .init(\.matchValue),
        "campaign": .init(\.campaignValue), "asin": .init(\.asinValue),
        "impressions": .init(\.impressions), "clicks": .init(\.clicks),
        "ctr": .init(\.ctrValue), "cpc": .init(\.cpcValue), "bid": .init(\.bidValue),
        "spend": .init(\.spend), "sales": .init(\.sales), "orders": .init(\.orders),
        "acos": .init(\.acosValue), "cvr": .init(\.cvrValue),
    ]
    private static let defaultSort = [KeyPathComparator(\AllTargetRow.spend, order: .reverse)]

    @State private var response: AllTargetsResponse?
    @State private var isLoading = false
    @State private var loadError: String?
    @State private var actionError: String?
    @State private var pendingState: PendingTargetChange?
    @State private var pendingBulk: PendingTargetBulk?
    @State private var bulkBidRequest: BulkBidRequest?
    @State private var lastResult: String?
    @State private var searchText = ""
    @State private var matchFilter = "all"
    @State private var selection = Set<AllTargetRow.ID>()
    @State private var campaignsList: [Campaign] = []
    @State private var scopedCampaigns = Set<String>()   // empty = all campaigns
    @State private var campaignSearch = ""
    @State private var chartDays: [SyncDay] = []
    @State private var chartError: String?
    @State private var showCampaignPicker = false
    @State private var sortOrder = SortPrefs.load(
        TableID.allTargets, fields: sortFields, fallback: defaultSort)
    @State private var colPrefs: TableColumnCustomization<AllTargetRow> =
        ColumnPrefs.load(TableID.allTargets)

    private var currency: String? { appState.currentMarket?.currency }
    private var rows: [AllTargetRow] { response?.targets ?? [] }

    private var matchTypes: [String] {
        ["all"] + Set(rows.compactMap { $0.matchType }).sorted()
    }

    private var filtered: [AllTargetRow] {
        rows.filter {
            (scopedCampaigns.isEmpty || ($0.campaignId.map(scopedCampaigns.contains) ?? false))
            && (matchFilter == "all" || $0.matchType == matchFilter)
            && (searchText.isEmpty
                || $0.targetingValue.localizedStandardContains(searchText)
                || $0.campaignValue.localizedStandardContains(searchText)
                || $0.asinValue.localizedStandardContains(searchText))
        }
        .sorted(using: sortOrder)
    }

    private var scopeLabel: String {
        scopedCampaigns.isEmpty ? "all campaigns"
            : "\(scopedCampaigns.count) campaign\(scopedCampaigns.count == 1 ? "" : "s")"
    }

    var body: some View {
        let visible = filtered
        VStack(spacing: 0) {
            PageHeader(title: "Targets", subtitle: targetsSubtitle, help: .targets) {
                campaignPicker
            }
            if let chartError {
                // a failed chart load is not the same thing as "no chart data"
                Label("Trend unavailable: \(chartError)", systemImage: "chart.line.downtrend.xyaxis")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, Layout.Spacing.sm)
            }
            if !chartDays.isEmpty {
                MetricChipsTrend(days: chartDays, currency: currency, scopeLabel: scopeLabel)
                    .padding(.horizontal, Layout.Spacing.lg)
                    .padding(.bottom, Layout.Spacing.sm)
            }
            FilterBar {
                Picker("Match", selection: $matchFilter) {
                    ForEach(matchTypes, id: \.self) { Text(shortMatch($0)).tag($0) }
                }
                .fixedSize()
                .help("Show only one targeting kind (keyword match type / auto expression)")
                TextField("Filter by target, campaign, or ASIN", text: $searchText)
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 260)
            } trailing: {
                ExportButton(filename: "targets-\(appState.selectedMarket)") {
                    CSVDocument(
                        headers: ["targeting", "match_type", "campaign", "asin",
                                  "impressions", "clicks", "ctr", "cpc",
                                  "spend", "sales", "orders", "acos", "cvr"],
                        rows: visible.map { t in
                            [t.targetingValue, t.matchValue, t.campaignValue, t.asinValue,
                             String(t.impressions), String(t.clicks),
                             t.ctr.map { String(format: "%.4f", $0) } ?? "",
                             t.cpc.map { String(format: "%.2f", $0) } ?? "",
                             String(format: "%.2f", t.spend), String(format: "%.2f", t.sales),
                             String(t.orders),
                             t.acos.map { String(format: "%.4f", $0) } ?? "",
                             t.cvr.map { String(format: "%.4f", $0) } ?? ""]
                        })
                }
                Text("\(visible.count) of \(rows.count)")
                    .font(.caption).foregroundStyle(.secondary).monospacedDigit()
            }
            ActionErrorBar(message: $actionError)
            Divider()
            if isLoading && response == nil {
                ProgressView("Loading targets…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let loadError {
                ContentUnavailableView {
                    Label("Targets unavailable", systemImage: "target")
                } description: {
                    Text(loadError)
                } actions: {
                    Button("Retry") { Task { await load() } }
                }
            } else {
                table(visible)
            }
        }
        .background(Theme.Colors.canvas)
        .task(id: appState.viewKey) { scopedCampaigns = []; await load() }
        // Per-market match filter: remembered per market, reloaded on switch.
        .task(id: appState.selectedMarket) {
            matchFilter = FilterPrefs.load("targets.match", market: appState.selectedMarket, default: "all")
        }
        .onChange(of: matchFilter) {
            FilterPrefs.save("targets.match", market: appState.selectedMarket, matchFilter)
        }
        .onChange(of: sortOrder) { SortPrefs.save(TableID.allTargets, sortOrder, fields: Self.sortFields) }
        .onChange(of: colPrefs) { ColumnPrefs.save(TableID.allTargets, colPrefs) }
        .confirmationDialog(
            pendingState.map { "\($0.state == "PAUSED" ? "Pause" : "Enable") '\($0.label)'?" } ?? "",
            isPresented: Binding(get: { pendingState != nil },
                                 set: { if !$0 { pendingState = nil } }),
            presenting: pendingState) { change in
            Button(change.state == "PAUSED" ? "Pause" : "Enable", role: .destructive) {
                Task { await setState(change.intent, confirmed: true) }
            }
        } message: { _ in
            Text("Applies to Amazon and is logged — undoable from the Audit Trail.")
        }
        .confirmationDialog(
            pendingBulk.map { "\($0.state == "PAUSED" ? "Pause" : "Enable") \($0.intents.count) targets?" } ?? "",
            isPresented: Binding(get: { pendingBulk != nil },
                                 set: { if !$0 { pendingBulk = nil } }),
            presenting: pendingBulk) { bulk in
            Button(bulk.state == "PAUSED" ? "Pause All" : "Enable All", role: .destructive) {
                Task { await setStateBulk(bulk.intents) }
            }
        } message: { _ in
            Text("Each change is logged separately and undoable from the Audit Trail.")
        }
        .sheet(item: $bulkBidRequest) { req in
            MoneyEntrySheet(
                title: req.rows.count == 1 ? "Set bid" : "Set bid for \(req.rows.count) targets",
                current: nil,                    // mixed values across the selection
                minimum: 0.02,
                note: "Applied to Amazon and logged. Bids below \(Format.money(0.02, currency: currency)) are rejected; the max-bid ceiling still clamps.",
                fieldLabel: currency ?? "$",
                confirmLabel: "Apply"
            ) { bid in
                await applyBulkBid(req.rows, bid)
            }
        }
        .overlay(alignment: .bottom) {
            if let lastResult {
                Text(lastResult)
                    .font(.caption).padding(.horizontal, 12).padding(.vertical, 6)
                    .background(Theme.Colors.positive.opacity(0.15), in: Capsule())
                    .padding(.bottom, 8)
                    .task { try? await Task.sleep(for: .seconds(3)); self.lastResult = nil }
            }
        }
    }

    private var campaignPicker: some View {
        Button { showCampaignPicker.toggle() } label: {
            HStack(spacing: 6) {
                Text("CAMPAIGNS")
                    .font(.caption2.weight(.semibold)).tracking(0.5)
                    .foregroundStyle(Theme.Colors.muted)
                Text(scopeLabel).font(.subheadline.weight(.semibold))
                    .foregroundStyle(Theme.Colors.textPrimary)
                Image(systemName: "chevron.down").font(.caption2).foregroundStyle(Theme.Colors.muted)
            }
            .padding(.horizontal, 10).padding(.vertical, 6)
            .background(Theme.Colors.surface, in: RoundedRectangle(cornerRadius: 8))
            .overlay { RoundedRectangle(cornerRadius: 8).strokeBorder(Theme.Colors.separator, lineWidth: 1) }
        }
        .buttonStyle(.plain)
        .popover(isPresented: $showCampaignPicker, arrowEdge: .bottom) { campaignPickerContent }
    }

    private var campaignPickerContent: some View {
        let matches = campaignsList.filter {
            campaignSearch.isEmpty || $0.nameValue.localizedCaseInsensitiveContains(campaignSearch)
        }
        return VStack(alignment: .leading, spacing: 8) {
            HStack {
                Button("All campaigns") { scopedCampaigns = []; Task { await loadChart() } }
                    .disabled(scopedCampaigns.isEmpty)
                Spacer()
                Text("\(scopedCampaigns.count) selected").font(.caption).foregroundStyle(.secondary)
            }
            TextField("Search campaigns", text: $campaignSearch).textFieldStyle(.roundedBorder)
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    ForEach(matches) { c in
                        Button {
                            if scopedCampaigns.contains(c.campaignId) { scopedCampaigns.remove(c.campaignId) }
                            else { scopedCampaigns.insert(c.campaignId) }
                            Task { await loadChart() }
                        } label: {
                            HStack(spacing: 8) {
                                Image(systemName: scopedCampaigns.contains(c.campaignId) ? "checkmark.square.fill" : "square")
                                    .foregroundStyle(scopedCampaigns.contains(c.campaignId) ? Theme.Colors.accent : .secondary)
                                Text(c.nameValue).lineLimit(1).truncationMode(.middle)
                                Spacer()
                            }
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .padding(.vertical, 3)
                    }
                }
            }
            .frame(width: 320, height: 300)
        }
        .padding(12)
    }

    private var targetsSubtitle: String {
        var parts = [appState.selectedMarket]
        if let asOf = response?.asOf { parts.append("snapshot \(Format.euDate(asOf))") }
        // The true total, not the cap. "top 2000 by spend" said nothing about
        // whether 2,001 or 50,822 targets sat behind it.
        if response?.truncated == true {
            if let total = response?.count, total > rows.count {
                parts.append("top \(rows.count) of \(Format.count(total)) by spend")
            } else {
                parts.append("top \(rows.count) by spend")
            }
        }
        return parts.joined(separator: " · ")
    }

    private func table(_ visible: [AllTargetRow]) -> some View {
        Table(visible, selection: $selection, sortOrder: $sortOrder.descendingFirst(),
              columnCustomization: $colPrefs) {
            identityColumns
            metricColumns
        }
        .background(Theme.Colors.surface)
        .contextMenu(forSelectionType: AllTargetRow.ID.self) { ids in
            let picked = visible.filter { ids.contains($0.id) }
            copyMenuItems(picked, primaryLabel: "Target",
                          primary: { $0.targetingValue },
                          row: { "\($0.targetingValue)\t\($0.matchValue)\t\($0.campaignValue)\t\($0.spend)\t\($0.sales)" })
            Divider()
            if picked.count <= 1, let t = picked.first {
                if t.targetId != nil {
                    Button("Edit Bid…") { bulkBidRequest = BulkBidRequest(rows: [t]) }
                        .disabled(appState.killActive)
                    Button("Pause Target") { request(t, "PAUSED") }
                        .disabled(appState.killActive)
                    Button("Enable Target") { request(t, "ENABLED") }
                        .disabled(appState.killActive)
                }
            } else if !picked.isEmpty {
                let withId = picked.filter { $0.targetId != nil }
                if !withId.isEmpty {
                    Button("Set Bid for \(withId.count)…") { bulkBidRequest = BulkBidRequest(rows: withId) }
                        .disabled(appState.killActive)
                    Button("Pause \(withId.count) Targets") { requestBulk(withId, "PAUSED") }
                        .disabled(appState.killActive)
                    Button("Enable \(withId.count) Targets") { requestBulk(withId, "ENABLED") }
                        .disabled(appState.killActive)
                }
            }
        }
    }

    /// Single state change = small action: one-click unless "always confirm" is on.
    private func request(_ t: AllTargetRow, _ state: String) {
        guard let tid = t.targetId else { return }
        let verb = state == "PAUSED" ? "pause-target" : "enable-target"
        let intent = appState.marketIntent(
            title: "\(state == "PAUSED" ? "Pause" : "Enable") target \(t.targetingValue)",
            arguments: [verb, "--target", tid])
        switch appState.actionCoordinator.requirement(
            for: intent, context: appState.actionPolicyContext) {
        case .confirmation:
            pendingState = PendingTargetChange(intent: intent, label: t.targetingValue, state: state)
        case .blocked(.killActive(let scope)):
            // Say it here rather than making a round trip the coordinator will refuse.
            actionError = ActionCoordinatorError.killActive(scope).localizedDescription
        case .preview, .ready:
            Task { await setState(intent) }
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

    private func requestBulk(_ rows: [AllTargetRow], _ state: String) {
        let verb = state == "PAUSED" ? "pause-target" : "enable-target"
        let intents = rows.compactMap { t -> ActionIntent? in
            guard let tid = t.targetId else { return nil }
            return appState.marketIntent(
                title: "\(state == "PAUSED" ? "Pause" : "Enable") target",
                arguments: [verb, "--target", tid], cardinality: .bulk)
        }
        guard let first = intents.first else { return }
        switch appState.actionCoordinator.requirement(
            for: first, context: appState.actionPolicyContext) {
        case .confirmation:
            pendingBulk = PendingTargetBulk(intents: intents, state: state)
        case .blocked(.killActive(let scope)):
            // Say it here rather than making a round trip the coordinator will refuse.
            actionError = ActionCoordinatorError.killActive(scope).localizedDescription
        case .preview, .ready:
            Task { await setStateBulk(intents) }
        }
    }

    /// Sequential per-target calls — each logged and undoable on its own.
    private func setStateBulk(_ intents: [ActionIntent]) async {
        actionError = nil
        var failures = 0, applied = false
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
            actionError = "\(failures) of \(intents.count) targets failed — see the Audit Trail"
        }
        if applied { await load() }
    }

    private func applyBulkBid(_ rows: [AllTargetRow], _ bid: Double) async {
        actionError = nil
        var failures = 0, applied = false
        for t in rows {
            guard let tid = t.targetId else { continue }
            // No --prev: this table carries no bid column, so the app has no
            // last-known bid to record. Passing a metric here would make Undo
            // restore that metric as the live bid.
            let args = ["setbid", "--target", tid, "--bid", String(format: "%.2f", bid)]
            let intent = appState.marketIntent(
                title: "Set bid \(t.targetingValue)", arguments: args,
                cardinality: rows.count > 1 ? .bulk : .single)
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
        lastResult = failures == 0
            ? "Set \(rows.count) bid\(rows.count == 1 ? "" : "s") to \(Format.money(bid, currency: currency))"
            : nil
        if failures > 0 {
            actionError = "\(failures) of \(rows.count) bids failed — see the Audit Trail"
        }
        if applied { await load() }
    }

    @TableColumnBuilder<AllTargetRow, KeyPathComparator<AllTargetRow>>
    private var identityColumns: some TableColumnContent<AllTargetRow, KeyPathComparator<AllTargetRow>> {
        TableColumn("Targeting", value: \.targetingValue) { t in
            Text(t.targetingValue).lineLimit(1).truncationMode(.tail)
        }
        .width(min: 120, ideal: 200)
        .customizationID("targeting")
        TableColumn("Match", value: \.matchValue) { t in
            Text(shortMatch(t.matchType)).foregroundStyle(.secondary)
        }
        .width(min: 49, ideal: 90)
        .customizationID("match")
        TableColumn("Campaign", value: \.campaignValue) { t in
            let scoped = t.campaignId.map { scopedCampaigns.contains($0) } ?? false
            Button { if let cid = t.campaignId { scopeToCampaign(cid) } } label: {
                Text(t.campaignValue)
                    .lineLimit(1).truncationMode(.middle)
                    .foregroundStyle(scoped ? Theme.Colors.accent : Theme.Colors.textPrimary)
                    .fontWeight(scoped ? .semibold : .regular)
            }
            .buttonStyle(.plain)
            .help("Click to scope the chart to this campaign · ⌘-click to add/remove")
        }
        .width(min: 90, ideal: 160)
        .customizationID("campaign")
        TableColumn("ASIN", value: \.asinValue) { t in
            AsinLink(asin: t.asin)
        }
        .width(min: 55, ideal: 100)
        .customizationID("asin")
    }

    @TableColumnBuilder<AllTargetRow, KeyPathComparator<AllTargetRow>>
    private var metricColumns: some TableColumnContent<AllTargetRow, KeyPathComparator<AllTargetRow>> {
        TableColumn("Impr.", value: \.impressions) { CountText(value: $0.impressions) }
            .width(min: 38, ideal: 70).customizationID("impr")
        TableColumn("Clicks", value: \.clicks) { CountText(value: $0.clicks) }
            .width(min: 30, ideal: 55).customizationID("clicks")
        TableColumn("CTR", value: \.ctrValue) { t in
            PercentText(value: t.ctr, label: "CTR", color: .primary, digits: 2)
        }
        .width(min: 33, ideal: 60).customizationID("ctr")
        TableColumn("Bid", value: \.bidValue) { row in
            HStack(spacing: 2) {
                MoneyText(value: row.bid, currency: currency)
                if row.bidInherited == true, row.bid != nil {
                    Text("ag")
                        .font(.caption2)
                        // muted, not .tertiary — .tertiary fell below the AA contrast
                        // floor for this small "inherited bid" marker.
                        .foregroundStyle(Theme.Colors.muted)
                        .help("No own bid — the ad-group default rules the auction")
                }
            }
        }
        .width(min: 38, ideal: 70).customizationID("bid")
        TableColumn("CPC", value: \.cpcValue) { MoneyText(value: $0.cpc, currency: currency) }
            .width(min: 38, ideal: 70).customizationID("cpc")
        TableColumn("Spend", value: \.spend) { MoneyText(value: $0.spend, currency: currency) }
            .width(min: 44, ideal: 80).customizationID("spend")
        TableColumn("Sales", value: \.sales) { MoneyText(value: $0.sales, currency: currency) }
            .width(min: 44, ideal: 80).customizationID("sales")
        TableColumn("Orders", value: \.orders) { CountText(value: $0.orders) }
            .width(min: 30, ideal: 55).customizationID("orders")
        TableColumn("ACOS", value: \.acosValue) { PercentText(value: $0.acos, label: "ACOS") }
            .width(min: 33, ideal: 60).customizationID("acos")
        TableColumn("CVR", value: \.cvrValue) { t in
            PercentText(value: t.cvr, label: "CVR", color: .primary)
        }
        .width(min: 30, ideal: 55).customizationID("cvr")
    }

    private func shortMatch(_ m: String?) -> String {
        guard let m, m != "all" else { return m == "all" ? "All matches" : "—" }
        return m.replacingOccurrences(of: "TARGETING_EXPRESSION_PREDEFINED", with: "auto")
            .replacingOccurrences(of: "TARGETING_EXPRESSION", with: "product")
            .lowercased()
    }

    private func load() async {
        isLoading = true
        defer { if !Task.isCancelled { isLoading = false } }
        loadError = nil
        do {
            let bridge = try appState.makeBridge()
            async let targets = bridge.call(AllTargetsResponse.self, ["alltargets"],
                                            market: appState.selectedMarket)
            async let campaigns = bridge.call(CampaignsResponse.self, ["campaigns"],
                                              market: appState.selectedMarket)
            let (t, c) = try await (targets, campaigns)
            guard !Task.isCancelled else { return }
            response = t
            campaignsList = c.campaigns.sorted { $0.spend > $1.spend }
            await loadChart()
        } catch {
            guard !Task.isCancelled else { return }
            response = nil
            loadError = error.localizedDescription
        }
    }

    /// Click a campaign in the table to scope the chart + table to it. Plain click
    /// scopes to just that campaign (click it again to clear back to all); ⌘-click
    /// adds/removes it from a multi-campaign selection.
    private func scopeToCampaign(_ cid: String) {
        if NSEvent.modifierFlags.contains(.command) {
            if scopedCampaigns.contains(cid) { scopedCampaigns.remove(cid) }
            else { scopedCampaigns.insert(cid) }
        } else {
            scopedCampaigns = (scopedCampaigns == [cid]) ? [] : [cid]
        }
        Task { await loadChart() }
    }

    /// Reload the metric chart for the current campaign scope (empty = all).
    ///
    /// Scope toggles fire these concurrently, so the result is applied only when
    /// it still matches the scope and market on screen — last request wins, not
    /// last response.
    private func loadChart() async {
        let requested = scopedCampaigns
        let market = appState.selectedMarket
        do {
            let bridge = try appState.makeBridge()
            var args = ["campaigndaily"]
            if !requested.isEmpty {
                args += ["--campaigns", requested.joined(separator: ",")]
            }
            let resp = try await bridge.call(CampaignDailyResponse.self, args, market: market)
            guard !Task.isCancelled,
                  requested == scopedCampaigns, market == appState.selectedMarket else { return }
            chartDays = resp.days
            chartError = nil
        } catch {
            guard !Task.isCancelled,
                  requested == scopedCampaigns, market == appState.selectedMarket else { return }
            chartDays = []
            chartError = error.localizedDescription
        }
    }
}

private struct PendingTargetChange: Identifiable {
    let intent: ActionIntent
    let label: String
    let state: String
    var id: UUID { intent.id }
}

private struct PendingTargetBulk {
    let intents: [ActionIntent]
    let state: String
}

private struct BulkBidRequest: Identifiable {
    let id = UUID()
    let rows: [AllTargetRow]
}

#Preview {
    TargetsView()
        .environment(AppState())
}
