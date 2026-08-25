import SwiftUI

// Level 3: targets, search terms, and applied negatives

struct AdGroupDetailView: View {
    @Environment(AppState.self) private var appState
    let route: AdGroupRoute

    @State private var targets: [TargetRow] = []
    @State private var searchTerms: [SearchTermRow] = []
    @State private var loadError: String?
    @State private var actionError: String?
    @State private var isLoading = false
    @State private var selectedTarget: TargetRow.ID?
    @State private var showingBidHistory = false
    @State private var editingBid: TargetRow?
    @State private var liveBidsLoaded = false
    @State private var loadingLive = false
    @State private var negating: PendingNegation?
    @State private var selectedTerm: SearchTermRow.ID?
    @State private var negateResult: String?
    @State private var appliedNegatives: [AppliedNegative] = []   // already-negated terms (writes_log)
    @State private var showingNegatives = false
    @State private var targetColPrefs: TableColumnCustomization<TargetRow> = ColumnPrefs.load(TableID.targets)
    @State private var termColPrefs: TableColumnCustomization<SearchTermRow> = ColumnPrefs.load(TableID.searchTerms)

    private static let targetSortFields: [String: KeyPathComparator<TargetRow>] = [
        "targeting": .init(\.targetingValue), "match": .init(\.matchValue), "bid": .init(\.currentBidValue),
        "impr": .init(\.impressions), "clicks": .init(\.clicks), "ctr": .init(\.ctrValue),
        "cpc": .init(\.cpcValue), "spend": .init(\.spend), "sales": .init(\.sales),
        "acos": .init(\.acosValue), "cvr": .init(\.cvrValue),
    ]
    private static let targetDefaultSort = [KeyPathComparator(\TargetRow.spend, order: .reverse)]
    private static let termSortFields: [String: KeyPathComparator<SearchTermRow>] = [
        "term": .init(\.searchTerm), "via": .init(\.targetingValue),
        "impr": .init(\.impressions), "clicks": .init(\.clicks), "ctr": .init(\.ctrValue),
        "cpc": .init(\.cpcValue), "spend": .init(\.spend), "sales": .init(\.sales),
        "orders": .init(\.orders), "acos": .init(\.acosValue),
    ]
    private static let termDefaultSort = [KeyPathComparator(\SearchTermRow.spend, order: .reverse)]

    @State private var targetSort = SortPrefs.load(
        TableID.targets, fields: targetSortFields, fallback: targetDefaultSort)
    @State private var termSort = SortPrefs.load(
        TableID.searchTerms, fields: termSortFields, fallback: termDefaultSort)

    private var negatedTerms: Set<String> {
        Set(appliedNegatives.map { $0.term.lowercased() })
    }

    private var currency: String? { appState.currentMarket?.currency }

    // Extracted so the targets Table stays within the 10-element TableColumnBuilder
    // limit once CTR + CPC are added (Targeting/Match/Bid live inline).
    @TableColumnBuilder<TargetRow, KeyPathComparator<TargetRow>>
    private var targetMetricColumns: some TableColumnContent<TargetRow, KeyPathComparator<TargetRow>> {
        TableColumn("Impr.", value: \.impressions) { CountText(value: $0.impressions) }
            .width(min: 38, ideal: 70)
            .customizationID("impr")
        TableColumn("Clicks", value: \.clicks) { CountText(value: $0.clicks) }
            .width(min: 30, ideal: 55)
            .customizationID("clicks")
        TableColumn("CTR", value: \.ctrValue) { PercentText(value: $0.ctr, label: "CTR", color: .primary, digits: 2) }
            .width(min: 33, ideal: 60)
            .customizationID("ctr")
        TableColumn("CPC", value: \.cpcValue) { MoneyText(value: $0.cpc, currency: currency) }
            .width(min: 38, ideal: 70)
            .customizationID("cpc")
        TableColumn("Spend", value: \.spend) { MoneyText(value: $0.spend, currency: currency) }
            .width(min: 38, ideal: 70)
            .customizationID("spend")
        TableColumn("Sales", value: \.sales) { MoneyText(value: $0.sales, currency: currency) }
            .width(min: 38, ideal: 70)
            .customizationID("sales")
        TableColumn("ACOS", value: \.acosValue) { PercentText(value: $0.acos, label: "ACOS") }
            .width(min: 33, ideal: 60)
            .customizationID("acos")
        TableColumn("CVR", value: \.cvrValue) { PercentText(value: $0.cvr, label: "CVR", color: .primary) }
            .width(min: 30, ideal: 55)
            .customizationID("cvr")
    }

    private var selectedTargetRow: TargetRow? {
        targets.first { $0.id == selectedTarget }
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            ActionErrorBar(message: $actionError)
            Divider()
            if isLoading && targets.isEmpty && searchTerms.isEmpty {
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
                VSplitView {
                    targetsSection
                    searchTermsSection
                }
                .frame(maxHeight: .infinity, alignment: .top)
            }
        }
        .navigationTitle(route.adGroup.asin ?? route.adGroup.nameValue)
        .navigationSubtitle("\(appState.selectedMarket) · \(route.campaign.nameValue)")
        .task(id: appState.dataStamp) { await load() }   // reload after the nightly run too
        .onChange(of: targetColPrefs) { ColumnPrefs.save(TableID.targets, targetColPrefs) }
        .onChange(of: termColPrefs) { ColumnPrefs.save(TableID.searchTerms, termColPrefs) }
        .onChange(of: selectedTarget) { _, targetID in
            showingBidHistory = targetID != nil
        }
        .onChange(of: appState.focusedRoute) { consumeTargetRoute() }
        .inspector(isPresented: $showingBidHistory) {
            if let target = selectedTargetRow, let targetId = target.targetId {
                BidHistoryView(target: target, targetId: targetId, currency: currency) {
                    editingBid = target
                }
                    .inspectorColumnWidth(min: 260, ideal: 320)
            } else {
                ContentUnavailableView("Select a target", systemImage: "clock.arrow.circlepath")
            }
        }
    }

    private var header: some View {
        HStack(spacing: Layout.Spacing.sm) {
            StatusBadge.campaignType(route.campaign.type)
            VStack(alignment: .leading, spacing: 3) {
                Text(route.adGroup.nameValue)
                    .font(.title.bold())
                    .foregroundStyle(Theme.Colors.textPrimary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                HStack(spacing: Layout.Spacing.xxs) {
                    Text("Targets in \(route.campaign.nameValue) · bid \(Format.money(route.adGroup.defaultBid, currency: currency))")
                        .font(.callout)
                        .foregroundStyle(Theme.Colors.muted)
                        .lineLimit(1)
                    if route.adGroup.asin != nil {
                        Text("·").font(.callout).foregroundStyle(Theme.Colors.muted)
                        AsinLink(asin: route.adGroup.asin, font: .callout.monospaced())
                            .foregroundStyle(Theme.Colors.muted)
                    }
                }
            }
            Spacer()
            StatusBadge.entityState(route.adGroup.state)
        }
        .padding(.horizontal, Layout.Spacing.sm)
        .padding(.vertical, Layout.Spacing.xs)
        .background(Theme.Colors.surface)
    }

    private var targetsSection: some View {
        VStack(spacing: 0) {
            HStack(spacing: Layout.Spacing.xs) {
                sectionTitle("Targets", count: targets.count,
                             hint: liveBidsLoaded ? "bids are LIVE from Amazon"
                                                  : "double-click a target for its bid timeline")
                Button {
                    Task { await load(live: true) }
                } label: {
                    if loadingLive {
                        ProgressView().controlSize(.small)
                    } else {
                        Label(liveBidsLoaded ? "Live ✓" : "Live Bids",
                              systemImage: "dot.radiowaves.left.and.right")
                            .labelStyle(.titleAndIcon)
                    }
                }
                .buttonStyle(.borderless)
                .disabled(loadingLive)
                // the spinner state has no text label of its own
                .accessibilityLabel(loadingLive ? "Fetching live bids from Amazon"
                                                : "Fetch live bids from Amazon")
                .help("Fetch each target's ACTUAL current bid + state from Amazon")
                .padding(.trailing, Layout.Spacing.sm)
            }
            Table(targets.sorted(using: targetSort), selection: $selectedTarget, sortOrder: $targetSort.descendingFirst(), columnCustomization: $targetColPrefs) {
                TableColumn("Targeting", value: \.targetingValue) { target in
                    Text(target.targeting ?? "—")
                }
                .customizationID("targeting")
                TableColumn("Match", value: \.matchValue) { target in
                    Text(shortMatch(target.matchType))
                        .foregroundStyle(.secondary)
                }
                .width(min: 49, ideal: 90)
                .customizationID("match")
                TableColumn("Bid", value: \.currentBidValue) { target in
                    HStack(spacing: Layout.Spacing.xxs) {
                        MoneyText(value: target.currentBid, currency: currency,
                                  color: target.liveState == "PAUSED" ? .secondary : .primary)
                        if target.liveBid != nil {
                            Image(systemName: "dot.radiowaves.left.and.right")
                                .font(.caption2)
                                .foregroundStyle(Theme.Colors.positive)
                                .accessibilityLabel("Live from Amazon\(target.liveState.map { " · \($0)" } ?? "")")
                                .help("Live from Amazon\(target.liveState.map { " · \($0)" } ?? "")")
                        } else if target.bidChanges > 0 {
                            Text("\(target.bidChanges)×")
                                .font(.caption2)
                                .padding(.horizontal, Layout.Spacing.xxs)
                                .background(Theme.Colors.information.opacity(0.15), in: Capsule())
                                .accessibilityLabel("\(target.bidChanges) bid changes")
                                .help("\(target.bidChanges) bid changes — double-click for the timeline")
                        }
                    }
                }
                .width(min: 49, ideal: 90)
                .customizationID("bid")
                targetMetricColumns
            }
            .background(Theme.Colors.surface)
            .onChange(of: targetSort) { SortPrefs.save(TableID.targets, targetSort, fields: Self.targetSortFields) }
            .contextMenu(forSelectionType: TargetRow.ID.self) { ids in
                let picked = targets.filter { ids.contains($0.id) }
                copyMenuItems(picked, primaryLabel: "Target",
                              primary: { $0.targeting ?? "" },
                              row: { "\($0.targeting ?? "")\t\($0.matchType ?? "")\t\($0.spend)\t\($0.sales)" })
                Divider()
                Button("Bid History") { openHistory(ids) }
                if let id = ids.first,
                   let target = targets.first(where: { $0.id == id }), target.targetId != nil {
                    Button("Edit Bid…") {
                        selectedTarget = id
                        editingBid = target
                    }
                }
            } primaryAction: { ids in
                openHistory(ids)
            }
        }
        .frame(minHeight: 160)
        .sheet(item: $editingBid) { target in
            MoneyEntrySheet(
                title: "Edit Bid",
                current: target.currentBid,
                minimum: 0.02,
                subtitle: "\(target.targeting ?? "target") · \(target.targetId ?? "")",
                currentLabel: target.liveBid != nil ? "Current bid (live)"
                                                    : "Current bid (last logged)",
                currentValue: target.currentBid.map { Format.money($0, currency: currency) }
                    ?? "unknown — use Live Bids",
                fieldLabel: "New bid",
                prompt: "0.25",
                confirmLabel: "Set Bid",
                fieldWidth: 120,
                sheetWidth: 320) { newBid in
                await setBid(target, newBid)
            }
        }
    }

    private func setBid(_ target: TargetRow, _ newBid: Double) async {
        guard let targetId = target.targetId else { return }
        actionError = nil
        do {
            var args = ["setbid", "--target", targetId, "--bid", String(format: "%.2f", newBid)]
            if let current = target.currentBid {
                args += ["--prev", String(format: "%.2f", current)]
            }
            let intent = appState.marketIntent(
                title: "Set bid for \(target.targeting ?? targetId)", arguments: args)
            let receipt = try await appState.actionCoordinator.execute(
                intent, context: appState.actionPolicyContext, confirmed: true)
            guard !receipt.rehearsed else { return }
            await load()
        } catch {
            actionError = error.localizedDescription
        }
    }

    private var searchTermsSection: some View {
        VStack(spacing: 0) {
            HStack(spacing: Layout.Spacing.xs) {
                sectionTitle("Search Terms", count: searchTerms.count,
                             hint: "spend-sorted, top 200 · right-click a bad term to negate it")
                if let negateResult {
                    Text(negateResult)
                        .font(.caption)
                        .foregroundStyle(Theme.Colors.positive)
                }
                if !appliedNegatives.isEmpty {
                    Button {
                        showingNegatives = true
                    } label: {
                        Label("Negatives (\(appliedNegatives.count))", systemImage: "nosign")
                            .font(.caption)
                    }
                    .buttonStyle(.borderless)
                    .help("Negative-exact keywords already applied to this ad group — negation decisions shouldn't be blind")
                    .popover(isPresented: $showingNegatives, arrowEdge: .bottom) {
                        VStack(alignment: .leading, spacing: Layout.Spacing.xs) {
                            Text("Applied negatives").font(.headline)
                            ForEach(appliedNegatives.prefix(30)) { negative in
                                HStack(spacing: Layout.Spacing.xs) {
                                    Text(negative.term)
                                    Spacer()
                                    Text(Format.euDate(String(negative.at.prefix(10))))
                                        .font(.caption2)
                                        .foregroundStyle(.secondary)
                                }
                                .font(.callout)
                            }
                            if appliedNegatives.count > 30 {
                                Text("+ \(appliedNegatives.count - 30) more")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        .padding(Layout.Spacing.sm)
                        .frame(width: 320)
                    }
                    .padding(.trailing, Layout.Spacing.sm)
                }
            }
            Table(searchTerms.sorted(using: termSort), selection: $selectedTerm, sortOrder: $termSort.descendingFirst(), columnCustomization: $termColPrefs) {
                TableColumn("Search term", value: \.searchTerm) { term in
                    Text(term.searchTerm)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
                .width(min: 180, ideal: 280)
                .customizationID("search-term")
                TableColumn("Via", value: \.targetingValue) { term in
                    Text(term.targeting ?? "—").foregroundStyle(.secondary)
                }
                .width(min: 60, ideal: 110)
                .customizationID("via")
                TableColumn("Impr.", value: \.impressions) { CountText(value: $0.impressions) }
                    .width(min: 38, ideal: 70)
                    .customizationID("impr")
                TableColumn("Clicks", value: \.clicks) { CountText(value: $0.clicks) }
                    .width(min: 30, ideal: 55)
                    .customizationID("clicks")
                TableColumn("CTR", value: \.ctrValue) { PercentText(value: $0.ctr, label: "CTR", color: .primary, digits: 2) }
                    .width(min: 33, ideal: 60)
                    .customizationID("ctr")
                TableColumn("CPC", value: \.cpcValue) { MoneyText(value: $0.cpc, currency: currency) }
                    .width(min: 38, ideal: 70)
                    .customizationID("cpc")
                TableColumn("Spend", value: \.spend) { MoneyText(value: $0.spend, currency: currency) }
                    .width(min: 38, ideal: 70)
                    .customizationID("spend")
                TableColumn("Sales", value: \.sales) { MoneyText(value: $0.sales, currency: currency) }
                    .width(min: 38, ideal: 70)
                    .customizationID("sales")
                TableColumn("Orders", value: \.orders) { CountText(value: $0.orders) }
                    .width(min: 30, ideal: 55)
                    .customizationID("orders")
                TableColumn("ACOS", value: \.acosValue) { PercentText(value: $0.acos, label: "ACOS") }
                    .width(min: 33, ideal: 60)
                    .customizationID("acos")
            }
            .background(Theme.Colors.surface)
            .onChange(of: termSort) { SortPrefs.save(TableID.searchTerms, termSort, fields: Self.termSortFields) }
            .contextMenu(forSelectionType: SearchTermRow.ID.self) { ids in
                let picked = searchTerms.filter { ids.contains($0.id) }
                copyMenuItems(picked, primaryLabel: "Search Term",
                              primary: { $0.searchTerm },
                              row: { "\($0.searchTerm)\t\($0.targeting ?? "")\t\($0.spend)\t\($0.sales)" })
                Divider()
                if let id = ids.first, let term = searchTerms.first(where: { $0.id == id }) {
                    if negatedTerms.contains(term.searchTerm.lowercased()) {
                        // A status line, not an action — a disabled Button here
                        // is announced as a button by VoiceOver.
                        Text("Already negated in this ad group")
                    } else {
                        Button("Negate '\(term.searchTerm)'…", role: .destructive) {
                            requestNegation(term)
                        }
                    }
                }
            }
            // negatives are permanent (no undo) — this one ALWAYS confirms
            .confirmationDialog(negating.map { "Negate '\($0.term.searchTerm)'?" } ?? "",
                                isPresented: Binding(get: { negating != nil },
                                                     set: { if !$0 { negating = nil } }),
                                presenting: negating) { pending in
                Button("Add negative-exact keyword", role: .destructive) {
                    Task { await negate(pending) }
                }
            } message: { pending in
                Text("Blocks '\(pending.term.searchTerm)' in this ad group. It stops matching immediately. Undoable from the Audit Trail.")
            }
        }
        .frame(minHeight: 160)
    }

    private func requestNegation(_ term: SearchTermRow) {
        let intent = appState.marketIntent(
            title: "Negate \(term.searchTerm)",
            arguments: ["negate", "--campaign", route.campaign.campaignId,
                        "--adgroup", route.adGroup.adGroupId, "--term", term.searchTerm],
            confirmationPolicy: .required, responseKind: .negate)
        negating = PendingNegation(term: term, intent: intent)
    }

    private func negate(_ pending: PendingNegation) async {
        let term = pending.term
        actionError = nil
        negateResult = nil
        do {
            let receipt = try await appState.actionCoordinator.execute(
                pending.intent, context: appState.actionPolicyContext, confirmed: true)
            guard !receipt.rehearsed else { return }
            if case .negate(applied: true) = receipt.result {
                negateResult = "'\(term.searchTerm)' negated ✓"
                // it stops matching immediately — reflect that in the table + inventory
                searchTerms.removeAll { $0.searchTerm == term.searchTerm }
                appliedNegatives.insert(
                    AppliedNegative(term: term.searchTerm,
                                    at: ISO8601DateFormatter().string(from: Date()),
                                    result: "submitted"),
                    at: 0)
            } else {
                actionError = "Negate was not accepted by Amazon."
            }
        } catch {
            actionError = error.localizedDescription
        }
    }

    private func sectionTitle(_ title: String, count: Int, hint: String) -> some View {
        SectionHeader(title: title, subtitle: hint, count: count)
            .padding(.horizontal, Layout.Spacing.sm)
    }

    private func shortMatch(_ match: String?) -> String {
        switch match {
        case "TARGETING_EXPRESSION_PREDEFINED": "auto"
        case "TARGETING_EXPRESSION": "product"
        case .some(let other): other.lowercased()
        case nil: "—"
        }
    }

    private func openHistory(_ ids: Set<TargetRow.ID>) {
        guard let id = ids.first else { return }
        selectedTarget = id
        showingBidHistory = true
    }

    private func load(live: Bool = false) async {
        if live { loadingLive = true } else { isLoading = true }
        defer { if !Task.isCancelled { isLoading = false; loadingLive = false } }
        loadError = nil
        do {
            let bridge = try appState.makeBridge()
            let adGroupId = route.adGroup.adGroupId
            let targetArgs = ["targets", "--adgroup", adGroupId] + (live ? ["--live"] : [])
            if live {
                let response = try await bridge.call(TargetsResponse.self, targetArgs,
                                                     market: appState.selectedMarket)
                guard !Task.isCancelled else { return }
                targets = response.targets
                consumeTargetRoute()
                liveBidsLoaded = response.live == true
            } else {
                async let targetsCall = bridge.call(TargetsResponse.self, targetArgs,
                                                    market: appState.selectedMarket)
                async let termsCall = bridge.call(
                    SearchTermsResponse.self, ["searchterms", "--adgroup", adGroupId],
                    market: appState.selectedMarket)
                async let negativesCall = bridge.call(
                    NegativesListResponse.self, ["negatives", "--adgroup", adGroupId],
                    market: appState.selectedMarket)
                let (targetsResponse, termsResponse) = try await (targetsCall, termsCall)
                guard !Task.isCancelled else { return }
                targets = targetsResponse.targets
                consumeTargetRoute()
                searchTerms = termsResponse.searchTerms
                liveBidsLoaded = false
                // negatives inventory is best-effort — failure just hides the badge
                appliedNegatives = (try? await negativesCall)?.negatives ?? []
            }
        } catch {
            guard !Task.isCancelled else { return }
            loadError = error.localizedDescription
        }
    }

    private func consumeTargetRoute() {
        guard case .target(let market, let campaignID, let adGroupID, let targetID) = appState.focusedRoute,
              market == appState.selectedMarket,
              campaignID == route.campaign.campaignId,
              adGroupID == route.adGroup.adGroupId else { return }
        if targets.contains(where: { $0.targetId == targetID }) {
            selectedTarget = targetID
            showingBidHistory = true
            appState.focusedRoute = nil
        } else {
            appState.focusedRoute = nil
            // A missed deep link says nothing about the tables, which loaded fine —
            // report it inline instead of replacing them with a Retry that can't help.
            actionError = "Target no longer available — it was deleted or renamed since navigation."
        }
    }
}

private struct PendingNegation: Identifiable {
    let term: SearchTermRow
    let intent: ActionIntent

    var id: UUID { intent.id }
}

// MARK: - Bid history timeline
