import SwiftUI

/// Harvest review: converting search terms the engine collected, with the ones
/// still waiting for promotion highlighted — and a one-click "Promote Now" that
/// runs the same phase4/phase4b the nightly job uses.
struct HarvestView: View {
    @Environment(AppState.self) private var appState

    @State private var harvest: HarvestResponse?
    @State private var loadError: String?
    @State private var isLoading = false
    @State private var pendingPromote: ActionIntent?
    @State private var promoting = false
    @State private var promoteOutput: String?
    @State private var promoteError: String?   // promote failures — inline, keeps the table
    @AppStorage("harvest.pendingOnly") private var pendingOnly = false
    @State private var approvedTerms = Set<HarvestWinner.ID>()
    @State private var winnerSelection = Set<HarvestWinner.ID>()
    @State private var colPrefs: TableColumnCustomization<HarvestWinner> = ColumnPrefs.load(TableID.harvest)
    @State private var filterText = ""
    @State private var designSheetWinner: HarvestWinner?
    @State private var pendingPromoteGroup: PendingPromoteGroup?
    @State private var promotingGroup = false
    @State private var groupToast: String?
    @State private var groupToastID = 0            // bumped per toast so each gets its own 4s

    private static let sortFields: [String: KeyPathComparator<HarvestWinner>] = [
        "term": .init(\.searchTerm), "type": .init(\.typeValue),
        "clicks": .init(\.clicks), "orders": .init(\.orders), "sales": .init(\.sales),
        "acos": .init(\.acosValue), "cpc": .init(\.cpcValue),
        "lastseen": .init(\.lastSeenValue), "promoted": .init(\.promotedValue),
    ]
    private static let defaultSort = [KeyPathComparator(\HarvestWinner.sales, order: .reverse)]

    private static let pruneSortFields: [String: KeyPathComparator<PruneKeyword>] = [
        "keyword": .init(\.keyword), "asin": .init(\.asinValue),
        "clicks": .init(\.clicks), "orders": .init(\.orders), "cvr": .init(\.cvrValue),
        "spend": .init(\.spend), "acos": .init(\.acosValue), "reason": .init(\.reason),
    ]
    private static let pruneDefaultSort = [KeyPathComparator(\PruneKeyword.spend, order: .reverse)]

    @State private var sortOrder = SortPrefs.load(
        TableID.harvest, fields: sortFields,
        fallback: defaultSort)

    enum Mode: String, CaseIterable { case promote = "Promote", prune = "Prune" }
    @AppStorage("harvest.mode") private var mode: Mode = .promote

    // Prune: wasteful harvested-exact keywords the engine would pause
    @State private var prune: HarvestPruneResponse?
    @State private var pruneError: String?
    @State private var loadingPrune = false
    @State private var approvedPrune = Set<PruneKeyword.ID>()
    @State private var pruneSelection = Set<PruneKeyword.ID>()
    @State private var pruning = false
    @State private var pendingPrune: ActionIntent?
    @State private var pruneResult: String?
    @State private var pruneApplyError: String?
    @State private var pruneColPrefs: TableColumnCustomization<PruneKeyword> = ColumnPrefs.load(TableID.harvestPrune)
    @State private var pruneSort = SortPrefs.load(
        TableID.harvestPrune, fields: pruneSortFields,
        fallback: pruneDefaultSort)

    private var currency: String? { appState.currentMarket?.currency }

    private var winners: [HarvestWinner] {
        (harvest?.winners ?? [])
            .filter { $0.needsDesign != true }
            .filter { !pendingOnly || !$0.promoted }
            .filter { filterText.isEmpty || $0.searchTerm.localizedStandardContains(filterText) }
            .sorted(using: sortOrder)
    }

    /// Cohort winners (Scavenger/AUTO) whose search term converted but whose
    /// source ad group has no single ASIN behind it — the operator has to pick
    /// which design(s) earn the new exact-match keyword before this can promote.
    /// Kept out of the normal table so it stops looking stuck in the pending queue.
    private var needsDesignWinners: [HarvestWinner] {
        (harvest?.winners ?? [])
            .filter { $0.needsDesign == true }
            .filter { !pendingOnly || !$0.promoted }
            .filter { filterText.isEmpty || $0.searchTerm.localizedStandardContains(filterText) }
            .sorted { $0.sales > $1.sales }
    }

    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "Harvest", subtitle: appState.selectedMarket, help: .harvest)
            statusBand
            FilterBar {
            } trailing: {
                SavedViewPicker(tableID: TableID.harvest,
                                filters: ["mode": mode.rawValue,
                                          "pendingOnly": String(pendingOnly),
                                          "search": filterText],
                                sortFields: Self.sortFields, defaultSort: Self.defaultSort,
                                sortOrder: $sortOrder, columns: $colPrefs) { filters in
                    mode = filters["mode"].flatMap(Mode.init(rawValue:)) ?? .promote
                    pendingOnly = filters["pendingOnly"] == "true"
                    filterText = filters["search"] ?? ""
                }
            }
            Divider()
            switch mode {
            case .promote: promoteBody
            case .prune: pruneBody
            }
        }
        .task(id: appState.viewKey) { await load(); await loadPrune() }
        .onChange(of: colPrefs) { ColumnPrefs.save(TableID.harvest, colPrefs) }
        .onChange(of: sortOrder) { SortPrefs.save(TableID.harvest, sortOrder, fields: Self.sortFields) }
        .onChange(of: pruneColPrefs) { ColumnPrefs.save(TableID.harvestPrune, pruneColPrefs) }
        .sheet(item: $designSheetWinner) { winner in
            PromoteGroupSheet(winner: winner) { asins in
                requestPromoteGroup(winner: winner, asins: asins)
            }
        }
        .confirmationDialog(
            pendingPromoteGroup.map {
                "Promote \u{201C}\($0.term)\u{201D} to \($0.asinCount) design\($0.asinCount == 1 ? "" : "s")?"
            } ?? "",
            isPresented: Binding(get: { pendingPromoteGroup != nil },
                                 set: { if !$0 { pendingPromoteGroup = nil } }),
            presenting: pendingPromoteGroup) { pending in
            Button("Promote", role: .destructive) { Task { await runPromoteGroup(pending) } }
        } message: { _ in
            Text("Creates an exact-match keyword under the chosen design(s), negates the source term, and marks the winner promoted. Logged to the Audit Trail.")
        }
        .overlay(alignment: .bottom) {
            if let groupToast {
                Text(groupToast)
                    .font(.callout).padding(.horizontal, 14).padding(.vertical, 8)
                    .background(Theme.Colors.positive.opacity(0.15), in: Capsule())
                    .padding(.bottom, 10)
                    .task(id: groupToastID) {
                        try? await Task.sleep(for: .seconds(4))
                        guard !Task.isCancelled else { return }
                        self.groupToast = nil
                    }
            }
        }
    }

    private var statusBand: some View {
        Group {
            HStack(spacing: Layout.Spacing.sm) {
                StatCardButton(title: "Winners", value: harvest.map { Format.count($0.count) } ?? "—",
                               symbol: "leaf.fill", subtitle: "converting search terms",
                               isSelected: mode == .promote && !pendingOnly,
                               helpText: "All converting search terms — click to view the promotion queue") {
                    mode = .promote
                    pendingOnly = false
                }
                StatCardButton(title: "Pending promotion",
                               value: harvest.map { Format.count($0.pending) } ?? "—",
                               tint: (harvest?.pending ?? 0) > 0 ? Theme.Colors.caution : .primary,
                               symbol: "arrow.up.forward.circle", subtitle: "reviewed by default",
                               glassTint: Theme.Colors.caution,
                               isSelected: mode == .promote && pendingOnly,
                               helpText: "Winners not yet promoted — click to view just those") {
                    mode = .promote
                    pendingOnly = true
                }
                StatCardButton(title: "Prune candidates",
                               value: prune.map { Format.count($0.count) } ?? "—",
                               tint: (prune?.count ?? 0) > 0 ? Theme.Colors.critical : .primary,
                               symbol: "scissors", subtitle: "economic waste checks",
                               glassTint: Theme.Colors.critical,
                               isSelected: mode == .prune,
                               helpText: "Harvested exact keywords that bleed money — click to review") {
                    mode = .prune
                }
            }
        }
        .padding(.horizontal, Layout.Spacing.lg)
        .padding(.vertical, Layout.Spacing.sm)
    }

    private var promoteBody: some View {
        VStack(spacing: 0) {
            if !needsDesignWinners.isEmpty {
                needsDesignSection
                Divider()
            }
            SectionHeader(title: "Promotion queue",
                          subtitle: "winning terms selected for exact-match campaigns",
                          count: winners.count)
                .padding(.horizontal, Layout.Spacing.sm)
            header
            ActionErrorBar(message: $promoteError)
            Divider()
            if isLoading && harvest == nil {
                ProgressView("Loading harvest log…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let loadError {
                ContentUnavailableView {
                    Label("Harvest unavailable", systemImage: "leaf")
                } description: {
                    Text(loadError)
                } actions: {
                    Button("Retry") { Task { await load() } }
                }
                .topAlignedEmptyState()
            } else if winners.isEmpty {
                ContentUnavailableView {
                    Label(pendingOnly ? "Nothing pending" : "No winners yet",
                          systemImage: "leaf")
                } description: {
                    Text(pendingOnly
                         ? "Every harvested winner in \(appState.selectedMarket) is already promoted."
                         : "The harvester hasn't collected converting search terms for \(appState.selectedMarket) yet.")
                }
                .topAlignedEmptyState()
            } else {
                table
            }
        }
    }

    // MARK: needs a design

    /// Cohort winners: the term converted, but there is no single design to
    /// point the new exact-match keyword at yet. "Choose designs…" opens the
    /// confirm-designs sheet; the actual promote write lands in a later step.
    private var needsDesignSection: some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.xs) {
            SectionHeader(title: "Needs a design",
                          subtitle: "cohort winners — pick which design(s) earn the keyword",
                          count: needsDesignWinners.count)
            VStack(spacing: Layout.Spacing.xs) {
                ForEach(needsDesignWinners) { winner in
                    needsDesignRow(winner)
                }
            }
        }
        .padding(.horizontal, Layout.Spacing.sm)
        .padding(.bottom, Layout.Spacing.sm)
    }

    private func needsDesignRow(_ winner: HarvestWinner) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: Layout.Spacing.sm) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: Layout.Spacing.xxs) {
                    Text(winner.searchTerm)
                        .font(.body.weight(.medium))
                        .lineLimit(1)
                        .truncationMode(.tail)
                    if sensitiveOrTrademark(winner.searchTerm) {
                        Image(systemName: "exclamationmark.triangle.fill")
                            .font(.caption)
                            .foregroundStyle(Theme.Colors.caution)
                            .help("Touches a trademarked name or sensitive language — review before promoting")
                    }
                }
                HStack(spacing: Layout.Spacing.xxs) {
                    CountText(value: winner.orders)
                    Text("orders ·").font(.caption).foregroundStyle(.secondary)
                    MoneyText(value: winner.sales, currency: currency)
                    Text("sales ·").font(.caption).foregroundStyle(.secondary)
                    PercentText(value: winner.acos, label: "ACOS")
                }
                .font(.caption)
            }
            Spacer()
            Button("Choose designs…") {
                designSheetWinner = winner
            }
            .buttonStyle(.bordered)
            .disabled(promotingGroup)
        }
        .padding(Layout.Spacing.sm)
        .background(Theme.Colors.surface, in: RoundedRectangle(cornerRadius: Layout.Radius.medium))
    }

    private func requestPromoteGroup(winner: HarvestWinner, asins: Set<String>) {
        guard !asins.isEmpty,
              let sourceAdGroupId = winner.sourceAdGroupId,
              let sourceCampaignId = winner.sourceCampaignId else { return }
        do {
            let payload: [String: Any] = [
                "term": winner.searchTerm,
                "source_ad_group_id": sourceAdGroupId,
                "source_campaign_id": sourceCampaignId,
                "asins": Array(asins),
            ]
            let stdin = try JSONSerialization.data(withJSONObject: payload)
            let intent = appState.marketIntent(
                title: "Promote \u{201C}\(winner.searchTerm)\u{201D} to \(asins.count) designs",
                arguments: ["harvest-promote-group", "--apply"], stdin: stdin,
                cardinality: .bulk, responseKind: .promoteGroup)
            let pending = PendingPromoteGroup(intent: intent, term: winner.searchTerm, asinCount: asins.count)
            switch appState.actionCoordinator.requirement(
                for: intent, context: appState.actionPolicyContext) {
            case .confirmation:
                pendingPromoteGroup = pending
            case .blocked(.killActive(let scope)):
                promoteError = ActionCoordinatorError.killActive(scope).localizedDescription
            case .preview, .ready:
                Task { await runPromoteGroup(pending) }
            }
        } catch {
            promoteError = error.localizedDescription
        }
    }

    private func runPromoteGroup(_ pending: PendingPromoteGroup) async {
        promotingGroup = true
        defer { promotingGroup = false }
        promoteError = nil
        do {
            let receipt = try await appState.actionCoordinator.execute(
                pending.intent, context: appState.actionPolicyContext, confirmed: true)
            guard !receipt.rehearsed else { return }
            guard case .promoteGroup(let promoted) = receipt.result else {
                throw ActionResultError.unexpectedResponse
            }
            if promoted {
                groupToast = "Promoted \u{201C}\(pending.term)\u{201D} to \(pending.asinCount) design\(pending.asinCount == 1 ? "" : "s")."
                groupToastID += 1
                await load()
            } else {
                promoteError = "Amazon didn\u{2019}t create the keyword — nothing was promoted (a trademark or sensitive term can be rejected). The winner stays in the list."
            }
        } catch {
            promoteError = error.localizedDescription
        }
    }

    // MARK: prune

    private var pruneBody: some View {
        VStack(spacing: 0) {
            SectionHeader(title: "Prune review",
                          subtitle: "economic waste candidates in harvested campaigns",
                          count: prune?.count)
                .padding(.horizontal, Layout.Spacing.sm)
            FilterBar {
                if let prune {
                    Text("\(prune.count) wasteful targets")
                        .font(.headline)
                    Text("~\(Format.money(prune.wasted, currency: currency))/mo wasted · keywords + ASIN targets in Harvested … campaigns")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                if let pruneResult {
                    Text(pruneResult).font(.caption).foregroundStyle(Theme.Colors.positive)
                }
            } trailing: {
                Button("Select All") { approvedPrune = Set(prune?.keywords.map(\.id) ?? []) }
                    .disabled((prune?.keywords.isEmpty ?? true))
                Button {
                    requestPrune()
                } label: {
                    if pruning {
                        HStack(spacing: Layout.Spacing.xs) {
                            ProgressView().controlSize(.small)
                            Text("Pausing…")
                        }
                    } else {
                        Text("Pause \(approvedPrune.count) Selected")
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(approvedPrune.isEmpty || pruning)
                .help("Pauses just these keywords (not their ad groups, so good sibling keywords keep running). Undoable in the Audit Trail.")
                .confirmationDialog("Pause \(approvedPrune.count) harvested keywords?",
                                    isPresented: Binding(
                                        get: { pendingPrune != nil },
                                        set: { if !$0 { pendingPrune = nil } }),
                                    presenting: pendingPrune) { intent in
                    Button("Pause on Amazon", role: .destructive) {
                        Task { await applyPrune(intent) }
                    }
                } message: { _ in
                    Text("Each has 15+ clicks and either 0 sales, or ACOS over break-even with CVR under the 8% floor. Reversible from the Audit Trail.")
                }
                if appState.killActive {
                    Label("KILL", systemImage: "exclamationmark.octagon.fill")
                        .foregroundStyle(Theme.Colors.critical)
                }
            }
            ActionErrorBar(message: $pruneApplyError)
            Divider()

            if loadingPrune && prune == nil {
                ProgressView("Checking harvested keywords…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let pruneError {
                ContentUnavailableView {
                    Label("Prune unavailable", systemImage: "scissors")
                } description: { Text(pruneError) } actions: { Button("Retry") { Task { await loadPrune() } } }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if (prune?.keywords.isEmpty ?? true) {
                ContentUnavailableView {
                    Label("Nothing to prune", systemImage: "checkmark.seal")
                } description: {
                    Text("Every harvested exact keyword in \(appState.selectedMarket) is pulling its weight.")
                }
                .topAlignedEmptyState()
            } else {
                pruneTable
            }
        }
    }

    private var pruneTable: some View {
        let rows = (prune?.keywords ?? []).sorted(using: pruneSort)
        return Table(rows, selection: $pruneSelection, sortOrder: $pruneSort.descendingFirst(),
                     columnCustomization: $pruneColPrefs) {
            TableColumn("✓") { kw in
                Toggle("", isOn: Binding(
                    get: { approvedPrune.contains(kw.id) },
                    set: { on in if on { approvedPrune.insert(kw.id) } else { approvedPrune.remove(kw.id) } }))
                    .labelsHidden()
                    .accessibilityLabel("Pause \(kw.keyword)")
            }
            .width(28)
            .customizationID("check")
            TableColumn("Target", value: \.keyword) { kw in
                HStack(spacing: Layout.Spacing.xxs) {
                    StatusBadge(text: kw.kind == "target" ? "Target" : "Keyword",
                                symbol: kw.kind == "target" ? "shippingbox" : "text.magnifyingglass",
                                tint: Theme.Colors.muted)
                    Text(kw.keyword).lineLimit(1).truncationMode(.tail)
                }
            }
            .width(min: 160, ideal: 280)
            .customizationID("keyword")
            TableColumn("ASIN", value: \.asinValue) { kw in
                AsinLink(asin: kw.asin)
            }
            .width(min: 66, ideal: 110)
            .customizationID("asin")
            TableColumn("Clicks", value: \.clicks) { kw in CountText(value: kw.clicks) }
                .width(min: 30, ideal: 55).customizationID("clicks")
            TableColumn("Orders", value: \.orders) { kw in CountText(value: kw.orders) }
                .width(min: 30, ideal: 55).customizationID("orders")
            TableColumn("CVR", value: \.cvrValue) { kw in
                PercentText(value: kw.cvr, label: "CVR", color: Theme.Colors.critical)
            }
            .width(min: 33, ideal: 55).customizationID("cvr")
            TableColumn("Spend", value: \.spend) { kw in MoneyText(value: kw.spend, currency: currency) }
                .width(min: 40, ideal: 70).customizationID("spend")
            TableColumn("ACOS", value: \.acosValue) { kw in
                PercentText(value: kw.acos, breakEven: kw.breakEven, label: "ACOS")
            }
                .width(min: 33, ideal: 60).customizationID("acos")
            TableColumn("Why", value: \.reason) { kw in
                Text(kw.reason).font(.caption).foregroundStyle(.secondary)
            }
            .width(min: 120, ideal: 240).customizationID("why")
        }
        .onChange(of: pruneSort) { SortPrefs.save(TableID.harvestPrune, pruneSort, fields: Self.pruneSortFields) }
        .background(Theme.Colors.surface)
        .copyableRows(rows, primaryLabel: "Keyword",
                      primary: { $0.keyword },
                      row: { "\($0.keyword)\t\($0.asin ?? "")\t\($0.clicks)\t\($0.orders)\t\($0.cvr.map { String($0) } ?? "")\t\($0.spend)\t\($0.acos.map { String($0) } ?? "")\t\($0.reason)" })
    }

    private func loadPrune() async {
        loadingPrune = true
        defer { if !Task.isCancelled { loadingPrune = false } }
        pruneError = nil
        // Same rule as load(): no cross-market rows, no stale keyword IDs.
        prune = nil
        approvedPrune.removeAll()
        pruneSelection.removeAll()
        do {
            let bridge = try appState.makeBridge()
            let response = try await bridge.call(HarvestPruneResponse.self, ["harvest-prune"],
                                                 market: appState.selectedMarket)
            guard !Task.isCancelled else { return }
            prune = response
            approvedPrune = Set(response.keywords.map(\.id))   // reviewed-by-default
        } catch {
            guard !Task.isCancelled else { return }
            prune = nil
            pruneError = error.localizedDescription
        }
    }

    private func requestPrune() {
        do {
            let stdin = try JSONSerialization.data(
                withJSONObject: ["keyword_ids": Array(approvedPrune)])
            let intent = appState.marketIntent(
                title: "Pause \(approvedPrune.count) harvested targets",
                arguments: ["harvest-prune-apply"], stdin: stdin,
                cardinality: .bulk, responseKind: .harvestPruneApply)
            switch appState.actionCoordinator.requirement(
                for: intent, context: appState.actionPolicyContext) {
            case .confirmation:
                pendingPrune = intent
            case .blocked(.killActive(let scope)):
                pruneApplyError = ActionCoordinatorError.killActive(scope).localizedDescription
            case .preview, .ready:
                Task { await applyPrune(intent) }
            }
        } catch {
            pruneApplyError = error.localizedDescription
        }
    }

    private func applyPrune(_ intent: ActionIntent) async {
        pruning = true
        defer { pruning = false }
        pruneApplyError = nil
        do {
            let receipt = try await appState.actionCoordinator.execute(
                intent, context: appState.actionPolicyContext, confirmed: true)
            guard !receipt.rehearsed else { return }
            guard case .harvestPruneApply(let paused) = receipt.result else {
                throw ActionResultError.unexpectedResponse
            }
            pruneResult = "Paused \(paused) keywords."
            await loadPrune()
        } catch {
            pruneResult = nil
            pruneApplyError = error.localizedDescription
        }
    }

    private var header: some View {
        FilterBar {
            if let harvest {
                Text("\(harvest.count) winners")
                    .font(.headline)
                Text("\(harvest.pending) pending promotion")
                    .font(.caption)
                    .foregroundStyle(harvest.pending > 0 ? Theme.Colors.caution : .secondary)
            }
            TextField("Filter terms", text: $filterText)
                .textFieldStyle(.roundedBorder)
                .frame(maxWidth: 180)
            if let promoteOutput {
                Text(promoteOutput)
                    .font(.caption)
                    .foregroundStyle(Theme.Colors.positive)
                    .lineLimit(1)
            }
        } trailing: {
            if let harvest {
                ExportButton(filename: "harvest-\(appState.selectedMarket)") {
                    CSVDocument(
                        headers: ["search_term", "kind", "type", "clicks", "orders",
                                  "sales", "acos", "cpc", "last_seen", "promoted"],
                        rows: harvest.winners.map { winner in
                            [winner.searchTerm, winner.kind ?? "", winner.type ?? "",
                             String(winner.clicks), String(winner.orders),
                             String(winner.sales),
                             winner.acos.map { String($0) } ?? "",
                             winner.cpc.map { String($0) } ?? "",
                             winner.lastSeen ?? "", winner.promoted ? "yes" : "no"]
                        })
                }
            }
            Button {
                requestPromote()
            } label: {
                if promoting {
                    HStack(spacing: Layout.Spacing.xs) {
                        ProgressView().controlSize(.small)
                        Text("Promoting…")
                    }
                } else {
                    Label("Promote \(approvedTerms.count) Selected",
                          systemImage: "arrow.up.forward.circle")
                }
            }
            .buttonStyle(.borderedProminent)
            .disabled(promoting || approvedTerms.isEmpty)
            .help("Runs phase4/phase4b scoped to the checked winners — they become manual exact-match campaigns")
            .confirmationDialog("Promote \(approvedTerms.count) selected winners?",
                                isPresented: Binding(
                                    get: { pendingPromote != nil },
                                    set: { if !$0 { pendingPromote = nil } }),
                                presenting: pendingPromote) { intent in
                Button("Promote on Amazon", role: .destructive) {
                    Task { await promote(intent) }
                }
            } message: { _ in
                Text("Runs the harvest promotion phases for \(appState.selectedMarket), scoped to your selection — the same code the nightly job runs. Creates real campaigns.")
            }
            if appState.killActive {
                Label("KILL", systemImage: "exclamationmark.octagon.fill")
                    .foregroundStyle(Theme.Colors.critical)
            }
        }
    }

    private var table: some View {
        Table(winners, selection: $winnerSelection, sortOrder: $sortOrder.descendingFirst(), columnCustomization: $colPrefs) {
            TableColumn("✓") { winner in
                if !winner.promoted {
                    Toggle("", isOn: Binding(
                        get: { approvedTerms.contains(winner.id) },
                        set: { on in
                            if on { approvedTerms.insert(winner.id) } else { approvedTerms.remove(winner.id) }
                        }))
                        .labelsHidden()
                        .accessibilityLabel("Promote \(winner.searchTerm)")
                }
            }
            .width(min: 24, ideal: 28)
            .customizationID("col1")
            TableColumn("Search term", value: \.searchTerm) { winner in
                HStack(spacing: Layout.Spacing.xxs) {
                    Image(systemName: winner.kind == "asin_target" ? "shippingbox" : "magnifyingglass")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .help(winner.kind == "asin_target" ? "ASIN target winner" : "keyword winner")
                    Text(winner.searchTerm)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
            }
            .width(min: 160, ideal: 260)
            .customizationID("search-term")
            TableColumn("Type", value: \.typeValue) { winner in
                StatusBadge.campaignType(winner.type)
            }
            .width(min: 66, ideal: 120)
            .customizationID("type")
            TableColumn("Clicks", value: \.clicks) { winner in
                CountText(value: winner.clicks)
            }
            .width(min: 27, ideal: 50)
            .customizationID("clicks")
            TableColumn("Orders", value: \.orders) { winner in
                CountText(value: winner.orders)
            }
            .width(min: 27, ideal: 50)
            .customizationID("orders")
            TableColumn("Sales", value: \.sales) { winner in
                MoneyText(value: winner.sales, currency: currency)
            }
            .width(min: 41, ideal: 75)
            .customizationID("sales")
            TableColumn("ACOS", value: \.acosValue) { winner in
                PercentText(value: winner.acos, label: "ACOS")
            }
            .width(min: 33, ideal: 60)
            .customizationID("acos")
            TableColumn("CPC", value: \.cpcValue) { winner in
                MoneyText(value: winner.cpc, currency: currency)
            }
            .width(min: 30, ideal: 55)
            .customizationID("cpc")
            TableColumn("Last seen", value: \.lastSeenValue) { winner in
                Text(winner.lastSeen.map { Format.euDate(String($0.prefix(10))) } ?? "—")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .width(min: 44, ideal: 80)
            .customizationID("last-seen")
            TableColumn("Promoted", value: \.promotedValue) { winner in
                StatusBadge(text: winner.promoted ? "Promoted" : "Pending",
                            symbol: winner.promoted ? "checkmark.circle.fill" : "hourglass",
                            tint: winner.promoted ? Theme.Colors.positive : Theme.Colors.caution)
            }
            .width(min: 35, ideal: 65)
            .customizationID("promoted")
        }
        .background(Theme.Colors.surface)
        .copyableRows(winners, primaryLabel: "Search Term",
                      primary: { $0.searchTerm },
                      row: { "\($0.searchTerm)\t\($0.type ?? "")\t\($0.clicks)\t\($0.orders)\t\($0.sales)\t\($0.acos.map { String($0) } ?? "")\t\($0.cpc.map { String($0) } ?? "")\t\($0.lastSeen ?? "")\t\($0.promoted ? "yes" : "no")" })
    }

    private func requestPromote() {
        do {
            // Scoped off the RAW harvest response, not the display-filtered
            // `winners` list: `winners` is also narrowed by filterText/pendingOnly,
            // so using it here would silently under-promote whenever the operator
            // had something typed into "Filter terms" — the button's count
            // (approvedTerms.count) wouldn't match what actually got sent. Only
            // `needsDesign` narrows the ELIGIBLE set (those have no checkbox to
            // approve them with); the search box is a view filter, not a write scope.
            let terms = (harvest?.winners ?? [])
                .filter { $0.needsDesign != true }
                .filter { approvedTerms.contains($0.id) }
                .map(\.searchTerm)
            let stdin = try JSONSerialization.data(withJSONObject: ["terms": terms])
            let intent = appState.marketIntent(
                title: "Promote \(terms.count) harvest winners", arguments: ["promote"],
                stdin: stdin, cardinality: .bulk, responseKind: .promote)
            switch appState.actionCoordinator.requirement(
                for: intent, context: appState.actionPolicyContext) {
            case .confirmation:
                pendingPromote = intent
            case .blocked(.killActive(let scope)):
                promoteError = ActionCoordinatorError.killActive(scope).localizedDescription
            case .preview, .ready:
                Task { await promote(intent) }
            }
        } catch {
            promoteError = error.localizedDescription
        }
    }

    private func promote(_ intent: ActionIntent) async {
        promoting = true
        defer { promoting = false }
        promoteError = nil
        do {
            let receipt = try await appState.actionCoordinator.execute(
                intent, context: appState.actionPolicyContext, confirmed: true)
            guard !receipt.rehearsed else { return }
            guard case .promote(let keywordExit, let asinExit) = receipt.result else {
                throw ActionResultError.unexpectedResponse
            }
            var parts: [String] = []
            if let keywordExit { parts.append("keywords exit \(keywordExit)") }
            if let asinExit { parts.append("ASIN targets exit \(asinExit)") }
            promoteOutput = parts.isEmpty ? "nothing to promote" : parts.joined(separator: " · ")
            await load()
        } catch {
            promoteError = error.localizedDescription
        }
    }

    private func load() async {
        isLoading = true
        defer { if !Task.isCancelled { isLoading = false } }
        loadError = nil
        // Drop the previous market's rows BEFORE fetching: currency flips with the
        // market picker, and the approval set holds the old market's term IDs.
        harvest = nil
        approvedTerms.removeAll()
        winnerSelection.removeAll()
        do {
            let bridge = try appState.makeBridge()
            let response = try await bridge.call(HarvestResponse.self, ["harvest"],
                                                 market: appState.selectedMarket)
            guard !Task.isCancelled else { return }
            harvest = response
            // reviewed-by-default: everything pending starts checked — but only
            // the terms the table can actually show a checkbox for. needsDesign
            // cohort winners live in their own section with no toggle, so seeding
            // them here would inflate "Promote N Selected" with rows the operator
            // never checked and can't uncheck.
            approvedTerms = Set(response.winners
                .filter { !$0.promoted && $0.needsDesign != true }
                .map(\.id))
        } catch {
            guard !Task.isCancelled else { return }
            harvest = nil
            loadError = error.localizedDescription
        }
    }
}

// MARK: - Sensitive / trademarked term heuristic

// Deliberately small and static — a caution flag for the operator to review,
// never a content-moderation system. Shared with PromoteGroupSheet.swift (same
// module, so no access modifier needed) since it also decides what stays
// un-ticked by default.
private let sensitiveOrTrademarkWords: Set<String> = [
    // bands/brands
    "foo fighters", "metallica", "nirvana", "the beatles", "rolling stones",
    "led zeppelin", "pink floyd", "ac dc", "nike", "disney", "star wars",
    "harry potter", "marvel", "pixar",
    // suicide / self-harm
    "suicide", "self harm", "self-harm", "kill myself", "suicidal",
]

/// True when `term` contains one of `sensitiveOrTrademarkWords` as a whole
/// word/phrase, matched case-insensitively. A substring match would flag
/// "foolproof fighters guide" for containing "foo" — this only matches whole
/// words, so "nirvana" matches "Nirvana Tee" but not "Nirvanadesign".
func sensitiveOrTrademark(_ term: String) -> Bool {
    let lower = term.lowercased()
    return sensitiveOrTrademarkWords.contains { phrase in
        let pattern = "\\b\(NSRegularExpression.escapedPattern(for: phrase))\\b"
        return lower.range(of: pattern, options: .regularExpression) != nil
    }
}

/// A cohort-winner promote-group intent awaiting confirmation, plus the bits
/// the confirmation dialog and success toast need to say (the intent's own
/// title is used for the confirm-dialog request; ActionIntent doesn't carry
/// display-only extras like a bare ASIN count).
private struct PendingPromoteGroup: Identifiable {
    let id = UUID()
    let intent: ActionIntent
    let term: String
    let asinCount: Int
}

private enum ActionResultError: LocalizedError {
    case unexpectedResponse

    var errorDescription: String? { "The action completed without its expected result payload." }
}

#Preview {
    HarvestView()
        .environment(AppState())
}
