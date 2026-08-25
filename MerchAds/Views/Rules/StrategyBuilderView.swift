import SwiftUI

/// Smart Rules — guided strategy builder (MerchDash parity). Turns search-term
/// data into two moves in one place: promote converting terms into exact-match
/// keywords, and negate wasteful terms. Reuses the harvest + negatives engine
/// flows; every write goes through the ActionCoordinator and the Audit Trail.
struct StrategyBuilderView: View {
    @Environment(AppState.self) private var appState

    @State private var harvest: HarvestResponse?
    @State private var negatives: NegativesPreviewResponse?
    @State private var promoteSel = Set<HarvestWinner.ID>()
    @State private var negateSel = Set<ProposedNegative.ID>()
    @State private var isLoading = false
    @State private var loadError: String?
    @State private var actionError: String?
    @State private var lastResult: String?
    @State private var toastID = 0            // bumped per result so each toast gets its own 4s
    @State private var pendingPromote: ActionIntent?
    @State private var pendingNegatives: ActionIntent?
    @State private var busy = false

    private var currency: String? { appState.currentMarket?.currency }
    private var winners: [HarvestWinner] {
        (harvest?.winners ?? []).filter { !$0.promoted }.sorted { $0.orders > $1.orders }
    }
    private var candidates: [ProposedNegative] {
        (negatives?.negatives ?? []).sorted { $0.spend > $1.spend }
    }

    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "Strategy Builder", subtitle: appState.selectedMarket, help: .strategyBuilder)
            ActionErrorBar(message: $actionError)
            Divider()
            if isLoading && harvest == nil && negatives == nil {
                ProgressView("Reading search-term data…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let loadError {
                ContentUnavailableView {
                    Label("Unavailable", systemImage: "wand.and.stars")
                } description: { Text(loadError) } actions: {
                    Button("Retry") { Task { await load() } }
                }
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: Layout.Spacing.xl) {
                        promoteStep
                        negateStep
                    }
                    .padding(Layout.Spacing.lg)
                }
            }
        }
        .background(Theme.Colors.canvas)
        .task(id: appState.viewKey) {
            // Selections are per-market: a term picked in DE must not stay armed in US.
            promoteSel = []
            negateSel = []
            await load()
        }
        .overlay(alignment: .bottom) {
            if let lastResult {
                Text(lastResult)
                    .font(.callout).padding(.horizontal, 14).padding(.vertical, 8)
                    .background(Theme.Colors.positive.opacity(0.15), in: Capsule())
                    .padding(.bottom, 10)
                    // id: toastID — a second result inside 4s restarts the timer
                    // instead of inheriting the first toast's deadline.
                    .task(id: toastID) {
                        try? await Task.sleep(for: .seconds(4))
                        guard !Task.isCancelled else { return }
                        self.lastResult = nil
                    }
            }
        }
        .confirmationDialog("Promote \(promoteSel.count) terms to exact-match keywords?",
                            isPresented: Binding(get: { pendingPromote != nil },
                                                 set: { if !$0 { pendingPromote = nil } }),
                            presenting: pendingPromote) { intent in
            Button("Promote", role: .destructive) { Task { await runPromote(intent) } }
        } message: { _ in Text("Creates keywords on Amazon. Logged to the Audit Trail.") }
        .confirmationDialog("Add \(negateSel.count) exact negatives?",
                            isPresented: Binding(get: { pendingNegatives != nil },
                                                 set: { if !$0 { pendingNegatives = nil } }),
                            presenting: pendingNegatives) { intent in
            Button("Add Negatives", role: .destructive) { Task { await runNegatives(intent) } }
        } message: { _ in Text("Negatives can be undone from the Audit Trail.") }
    }

    // MARK: step 1 — promote

    private var promoteStep: some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.sm) {
            stepHeader(1, "Promote converting terms",
                       "Search terms that are already converting — promote the winners into their own exact-match keywords so you can bid them directly.")
            if winners.isEmpty {
                emptyNote("No un-promoted converting terms right now.")
            } else {
                HStack {
                    Button(promoteSel.count == winners.count ? "Deselect all" : "Select all") {
                        promoteSel = promoteSel.count == winners.count ? [] : Set(winners.map(\.id))
                    }
                    Spacer()
                    Button("Promote \(promoteSel.count) selected") { requestPromote() }
                        .buttonStyle(.borderedProminent)
                        .disabled(busy || promoteSel.isEmpty || appState.killActive)
                }
                Table(winners, selection: $promoteSel) {
                    TableColumn("Search term") { Text($0.searchTerm).lineLimit(1).truncationMode(.tail) }
                    TableColumn("Kind") { Text($0.kind ?? "—").foregroundStyle(.secondary) }
                        .width(min: 50, ideal: 80)
                    TableColumn("Clicks") { CountText(value: $0.clicks) }.width(min: 40, ideal: 55)
                    TableColumn("Orders") { CountText(value: $0.orders) }.width(min: 40, ideal: 55)
                    TableColumn("Sales") { MoneyText(value: $0.sales, currency: currency) }.width(min: 44, ideal: 80)
                    TableColumn("ACOS") { PercentText(value: $0.acos, label: "ACOS") }.width(min: 40, ideal: 60)
                }
                .contentSizedTable(rows: winners.count, cap: 420)
                .background(Theme.Colors.surface)
                .copyableRows(winners, primaryLabel: "Search Term",
                              primary: { $0.searchTerm },
                              row: { "\($0.searchTerm)\t\($0.kind ?? "")\t\($0.clicks)\t\($0.orders)\t\($0.sales)" })
            }
        }
    }

    // MARK: step 2 — negate

    private var negateStep: some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.sm) {
            stepHeader(2, "Cut wasteful terms",
                       "Search terms spending with nothing to show — add them as exact negatives so the spend stops.")
            if candidates.isEmpty {
                emptyNote("No wasteful-term negatives proposed right now.")
            } else {
                HStack {
                    Button(negateSel.count == candidates.count ? "Deselect all" : "Select all") {
                        negateSel = negateSel.count == candidates.count ? [] : Set(candidates.map(\.id))
                    }
                    Spacer()
                    Button("Negate \(negateSel.count) selected") { requestNegatives() }
                        .buttonStyle(.borderedProminent)
                        .disabled(busy || negateSel.isEmpty || appState.killActive)
                }
                Table(candidates, selection: $negateSel) {
                    TableColumn("Search term") { Text($0.searchTerm).lineLimit(1).truncationMode(.tail) }
                    TableColumn("Spend") { MoneyText(value: $0.spend, currency: currency) }.width(min: 44, ideal: 80)
                    TableColumn("Why") { Text($0.reason).font(.caption).foregroundStyle(.secondary).lineLimit(1) }
                }
                .contentSizedTable(rows: candidates.count, cap: 420)
                .background(Theme.Colors.surface)
                .copyableRows(candidates, primaryLabel: "Search Term",
                              primary: { $0.searchTerm },
                              row: { "\($0.searchTerm)\t\($0.spend)\t\($0.reason)" })
            }
        }
    }

    private func stepHeader(_ n: Int, _ title: String, _ subtitle: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: Layout.Spacing.sm) {
            Text("\(n)")
                .font(Typography.cardTitle)
                .foregroundStyle(.white)
                .frame(width: 26, height: 26)
                .background(Theme.Colors.accent, in: Circle())
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(Typography.pageTitle)
                Text(subtitle).font(.caption).foregroundStyle(Theme.Colors.muted)
            }
        }
    }

    private func emptyNote(_ text: String) -> some View {
        Text(text).font(.callout).foregroundStyle(.secondary)
            .padding(Layout.Spacing.md)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.Colors.surface, in: RoundedRectangle(cornerRadius: Layout.Radius.medium))
    }

    // MARK: actions

    private func requestPromote() {
        let terms = winners.filter { promoteSel.contains($0.id) }.map(\.searchTerm)
        guard !terms.isEmpty else { return }
        do {
            let stdin = try JSONSerialization.data(withJSONObject: ["terms": terms])
            let intent = appState.marketIntent(
                title: "Promote \(terms.count) harvest winners", arguments: ["promote"],
                stdin: stdin, cardinality: .bulk, responseKind: .promote)
            switch appState.actionCoordinator.requirement(
                for: intent, context: appState.actionPolicyContext) {
            case .confirmation: pendingPromote = intent
            case .blocked(.killActive(let scope)):
                actionError = ActionCoordinatorError.killActive(scope).localizedDescription
            case .preview, .ready: Task { await runPromote(intent, requested: terms.count) }
            }
        } catch { actionError = error.localizedDescription }
    }

    private func runPromote(_ intent: ActionIntent, requested: Int? = nil) async {
        busy = true; defer { busy = false }
        actionError = nil
        // The selection is cleared below, so freeze what we asked for first.
        let asked = requested ?? promoteSel.count
        do {
            let receipt = try await appState.actionCoordinator.execute(
                intent, context: appState.actionPolicyContext, confirmed: true)
            guard !receipt.rehearsed else { return }
            promoteSel = []
            // The engine returns phase exit codes, not a per-term count: a
            // non-zero code means some or all of the promotion failed, so don't
            // claim the terms landed.
            if case .promote(let keywordExit, let asinExit) = receipt.result,
               (keywordExit ?? 0) != 0 || (asinExit ?? 0) != 0 {
                actionError = "Promotion did not finish cleanly (keywords exit \(keywordExit ?? 0), ASINs exit \(asinExit ?? 0)). Check the Audit Trail for what landed."
            } else {
                showToast("Promoted \(asked) term\(asked == 1 ? "" : "s").")
            }
            await load()
        } catch { actionError = error.localizedDescription }
    }

    private func showToast(_ message: String) {
        lastResult = message
        toastID += 1
    }

    private func requestNegatives() {
        let picked = candidates.filter { negateSel.contains($0.id) }
        guard !picked.isEmpty else { return }
        let plan: [String: Any] = [
            "negatives": picked.map {
                ["search_term": $0.searchTerm, "campaign_id": $0.campaignId, "ad_group_id": $0.adGroupId]
            },
            "pauses": [String](),
        ]
        do {
            let stdin = try JSONSerialization.data(withJSONObject: plan)
            let intent = appState.marketIntent(
                title: "Add \(picked.count) negatives", arguments: ["negatives-apply"],
                stdin: stdin, cardinality: .bulk, responseKind: .negativesApply)
            switch appState.actionCoordinator.requirement(
                for: intent, context: appState.actionPolicyContext) {
            case .confirmation: pendingNegatives = intent
            case .blocked(.killActive(let scope)):
                actionError = ActionCoordinatorError.killActive(scope).localizedDescription
            case .preview, .ready: Task { await runNegatives(intent, requested: picked.count) }
            }
        } catch { actionError = error.localizedDescription }
    }

    private func runNegatives(_ intent: ActionIntent, requested: Int? = nil) async {
        busy = true; defer { busy = false }
        actionError = nil
        let asked = requested ?? negateSel.count
        do {
            let receipt = try await appState.actionCoordinator.execute(
                intent, context: appState.actionPolicyContext, confirmed: true)
            guard !receipt.rehearsed else { return }
            negateSel = []
            // Report the engine's count, not the selection — a partial apply must
            // not read as a full one.
            let applied: Int
            if case .negativesApply(let negatives, _, _) = receipt.result { applied = negatives }
            else { applied = asked }
            showToast("Added \(applied) negative\(applied == 1 ? "" : "s").")
            if applied < asked {
                actionError = "\(asked - applied) of \(asked) negatives were not applied — check the Audit Trail."
            }
            await load()
        } catch { actionError = error.localizedDescription }
    }

    private func load() async {
        isLoading = true
        defer { if !Task.isCancelled { isLoading = false } }
        loadError = nil
        do {
            let bridge = try appState.makeBridge()
            async let h = bridge.call(HarvestResponse.self, ["harvest"], market: appState.selectedMarket)
            async let n = bridge.call(NegativesPreviewResponse.self, ["negatives-preview"], market: appState.selectedMarket)
            let (harvestResp, negResp) = try await (h, n)
            guard !Task.isCancelled else { return }
            harvest = harvestResp
            negatives = negResp
        } catch {
            guard !Task.isCancelled else { return }
            loadError = error.localizedDescription
        }
    }
}

#Preview {
    StrategyBuilderView()
        .environment(AppState())
}
