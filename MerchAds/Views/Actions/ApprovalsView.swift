import SwiftUI

/// The approval queue: what phase2 WANTS to do (negatives + pauses). Review,
/// deselect anything you disagree with, approve the rest. "The robot surprised
/// me" → "I clicked yes."
struct ApprovalsView: View {
    @Environment(AppState.self) private var appState

    @State private var preview: NegativesPreviewResponse?
    /// The market `preview` was read from. Its campaign and ad-group ids mean
    /// nothing in any other account, and the engine's staleness guard compares
    /// DATES, which two markets usually share.
    @State private var previewMarket: String?
    @State private var loadError: String?
    @State private var isLoading = false
    @State private var approvedNegatives = Set<ProposedNegative.ID>()
    @State private var approvedPauses = Set<ProposedPause.ID>()
    @State private var pendingApplyIntent: ActionIntent?
    @State private var applying = false
    @State private var pendingApprovalIntent: ActionIntent?
    @State private var lastResult: String?
    @State private var applyError: String?    // action failure — inline, never replaces the queue
    @State private var negColPrefs: TableColumnCustomization<ProposedNegative> = ColumnPrefs.load(TableID.approvalNegatives)
    @State private var pauseColPrefs: TableColumnCustomization<ProposedPause> = ColumnPrefs.load(TableID.approvalPauses)
    @State private var negSelection = Set<ProposedNegative.ID>()
    @State private var pauseSelection = Set<ProposedPause.ID>()
    @State private var negSort = SortPrefs.load(
        TableID.approvalNegatives, fields: negSortFields,
        fallback: [KeyPathComparator(\ProposedNegative.spend, order: .reverse)])
    @State private var pauseSort = SortPrefs.load(
        TableID.approvalPauses, fields: pauseSortFields,
        fallback: [KeyPathComparator(\ProposedPause.spend, order: .reverse)])

    private static let negSortFields: [String: KeyPathComparator<ProposedNegative>] = [
        "searchTerm": .init(\.searchTerm), "spend": .init(\.spend), "reason": .init(\.reason),
        "adGroupId": .init(\.adGroupId),
    ]
    private static let pauseSortFields: [String: KeyPathComparator<ProposedPause>] = [
        "spend": .init(\.spend), "reason": .init(\.reason), "adGroupId": .init(\.adGroupId),
    ]

    private var currency: String? { appState.currentMarket?.currency }
    private var approvedCount: Int { approvedNegatives.count + approvedPauses.count }

    @AppStorage("approvals.source") private var source: ApprovalSource = .phase2

    enum ApprovalSource: String, CaseIterable { case phase2 = "Phase 2", rules = "Rules" }

    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "Approval Queue",
                       subtitle: source == .rules
                           ? "\(appState.selectedMarket) · review-mode rules"
                           : "\(appState.selectedMarket) · data through \(preview?.asOf.map(Format.euDate) ?? "—")",
                       help: .approvals)
            // Left-aligned under the title. A bare .frame(maxWidth:) inside this
            // VStack centred the picker in the window, which read as a floating
            // control belonging to nothing.
            HStack(spacing: 0) {
                Picker("Source", selection: $source) {
                    ForEach(ApprovalSource.allCases, id: \.self) { Text($0.rawValue).tag($0) }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .frame(maxWidth: 320)
                Spacer(minLength: 0)
            }
            .padding(.horizontal, Layout.Spacing.lg)
            .padding(.bottom, Layout.Spacing.sm)

            if source == .rules {
                RulesApprovalQueue()
            } else {
                phase2Body
            }
        }
        .background(Theme.Colors.canvas)
    }

    private var phase2Body: some View {
        VStack(spacing: 0) {
            statusBand
            if !appState.approvalRequired {
                HStack(spacing: Layout.Spacing.xs) {
                    Image(systemName: "info.circle")
                        .foregroundStyle(Theme.Colors.caution)
                    Text("Heads-up: the nightly run still auto-applies this plan at 10:00. Turn on \"Require approval\" in Actions to make this queue the real gate.")
                        .font(.caption)
                    Spacer()
                }
                .padding(Layout.Spacing.xs)
                .background(Theme.Colors.caution.opacity(0.1))
            }
            header
            ActionErrorBar(message: $applyError)
            Divider()
            if isLoading && preview == nil {
                ProgressView("Computing the phase2 plan…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let loadError {
                ContentUnavailableView {
                    Label("Plan unavailable", systemImage: "checklist")
                } description: {
                    Text(loadError)
                } actions: {
                    Button("Retry") { Task { await load() } }
                }
            } else if let preview, preview.negatives.isEmpty && preview.pauses.isEmpty {
                ContentUnavailableView {
                    Label("Queue is empty", systemImage: "checkmark.seal")
                } description: {
                    Text("The automation has nothing pending for \(appState.selectedMarket).")
                }
                .topAlignedEmptyState()
            } else if let preview {
                // Two things were wrong here. A VSplitView sizes to its
                // content's ideal height instead of filling, so the whole screen
                // was shorter than the pane and the VStack centred it — the
                // queue floated in the middle of the window. And the split gives
                // each half an equal share whatever it holds, so with 0
                // negatives and 6 pauses an empty table took half the window
                // while the rows that mattered were squeezed underneath.
                //
                // Split only when both sides actually have rows. The bar above
                // still states both counts, so a hidden empty half is never a
                // hidden fact.
                if preview.negatives.isEmpty {
                    pausesTable(preview.pauses)
                        .frame(maxHeight: .infinity, alignment: .top)
                } else if preview.pauses.isEmpty {
                    negativesTable(preview.negatives)
                        .frame(maxHeight: .infinity, alignment: .top)
                } else {
                    VSplitView {
                        negativesTable(preview.negatives)
                        pausesTable(preview.pauses)
                    }
                    .frame(maxHeight: .infinity, alignment: .top)
                }
            }
        }
        .task(id: appState.viewKey) { await load(fresh: true) }
        .onChange(of: negColPrefs) { ColumnPrefs.save(TableID.approvalNegatives, negColPrefs) }
        .onChange(of: pauseColPrefs) { ColumnPrefs.save(TableID.approvalPauses, pauseColPrefs) }
        .onChange(of: negSort) { SortPrefs.save(TableID.approvalNegatives, negSort, fields: Self.negSortFields) }
        .onChange(of: pauseSort) { SortPrefs.save(TableID.approvalPauses, pauseSort, fields: Self.pauseSortFields) }
        .confirmationDialog(
            pendingApprovalIntent?.title ?? "",
            isPresented: Binding(get: { pendingApprovalIntent != nil },
                                 set: { if !$0 { pendingApprovalIntent = nil } }),
            presenting: pendingApprovalIntent
        ) { intent in
            Button("Change Global Approval Mode", role: .destructive) {
                Task { await setApprovalMode(intent) }
            }
        } message: { intent in
            Text("This changes \(intent.scope.confirmationDescription), affecting every market and the nightly engine.")
        }
    }

    private var statusBand: some View {
        Group {
            HStack(spacing: Layout.Spacing.sm) {
                StatCard(title: "Pending negatives",
                         value: preview.map { Format.count($0.negatives.count) } ?? "—",
                         tint: (preview?.negatives.isEmpty == false)
                            ? Theme.Colors.caution : Theme.Colors.muted,
                         symbol: "text.badge.minus")
                    .mdCard()
                StatCard(title: "Pending pauses",
                         value: preview.map { Format.count($0.pauses.count) } ?? "—",
                         tint: (preview?.pauses.isEmpty == false)
                            ? Theme.Colors.critical : Theme.Colors.muted,
                         symbol: "pause.circle.fill")
                    .mdCard()
                StatCard(title: "Approval mode",
                         value: appState.approvalRequired ? "Required" : "Automatic",
                         tint: appState.approvalRequired
                            ? Theme.Colors.positive : Theme.Colors.caution,
                         symbol: appState.approvalRequired ? "checkmark.shield.fill" : "bolt.fill")
                    .mdCard()
            }
        }
        .padding(.horizontal, Layout.Spacing.lg)
        .padding(.vertical, Layout.Spacing.md)
    }

    private var header: some View {
        FilterBar {
            if let preview {
                Text("as of \(preview.asOf.map(Format.euDate) ?? "—")")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text("\(preview.negatives.count) negatives · \(preview.pauses.count) pauses proposed")
                    .font(.caption)
            }
            if let lastResult {
                Text(lastResult)
                    .font(.caption)
                    .foregroundStyle(Theme.Colors.positive)
            }
            Toggle("Require approval", isOn: Binding(
                get: { appState.approvalRequired },
                set: { requestApprovalMode($0) }))
                .toggleStyle(.switch)
        } trailing: {
            Button("Select All") { selectAll() }
                .disabled(preview == nil)
                .help("Approve everything — you can still uncheck individual items")
            Button("Deselect All") {
                approvedNegatives.removeAll()
                approvedPauses.removeAll()
            }
            .disabled(approvedCount == 0)
            .help("Start from zero and check only what you agree with")
            Button {
                requestApply()
            } label: {
                if applying {
                    HStack(spacing: Layout.Spacing.xs) {
                        ProgressView().controlSize(.small)
                        Text("Applying…")
                    }
                } else {
                    Text("Apply \(approvedCount) Approved")
                }
            }
            .buttonStyle(.borderedProminent)
            .disabled(approvedCount == 0 || applying || isLoading || appState.killActive)
            .help("Writes the checked items to Amazon: negatives and pauses can be undone from the Audit Trail")
            .confirmationDialog(
                pendingApplyIntent?.title ?? "",
                isPresented: Binding(get: { pendingApplyIntent != nil },
                                     set: { if !$0 { pendingApplyIntent = nil } }),
                presenting: pendingApplyIntent) { intent in
                Button("Apply to Amazon", role: .destructive) {
                    Task { await apply(intent) }
                }
            } message: { intent in
                Text("Apply to \(intent.scope.confirmationDescription). Negatives and pauses are undoable from the Audit Trail.")
            }
            if appState.killActive {
                StatusBadge(text: "KILL", symbol: "exclamationmark.octagon.fill",
                            tint: Theme.Colors.critical)
                    .help("Writes are frozen — release KILL in Actions to apply")
            }
        }
    }

    private func negativesTable(_ negatives: [ProposedNegative]) -> some View {
        let rows = negatives.sorted(using: negSort)
        return VStack(spacing: 0) {
            SectionHeader(title: "Negative keywords",
                          subtitle: "\(approvedNegatives.count)/\(negatives.count) approved",
                          count: negatives.count)
                .padding(.horizontal, Layout.Spacing.sm)
            Table(rows, selection: $negSelection, sortOrder: $negSort.descendingFirst(),
                  columnCustomization: $negColPrefs) {
                TableColumn("✓") { negative in
                    Toggle("", isOn: binding(for: negative.id, in: $approvedNegatives))
                        .labelsHidden()
                        .accessibilityLabel("Approve negative \(negative.searchTerm)")
                }
                .width(min: 24, ideal: 28)
                .customizationID("col1")
                TableColumn("Search term", value: \.searchTerm) { negative in
                    Text(negative.searchTerm)
                }
                .width(min: 160, ideal: 260)
                .customizationID("search-term")
                TableColumn("Wasted", value: \.spend) { negative in
                    MoneyText(value: negative.spend, currency: currency)
                }
                .width(min: 38, ideal: 70)
                .customizationID("wasted")
                TableColumn("Why", value: \.reason) { negative in
                    TraceReasonCell(reason: negative.reason, trace: negative.trace)
                }
                .customizationID("why")
                TableColumn("Ad group", value: \.adGroupId) { negative in
                    Text(negative.adGroupId).font(.caption.monospaced()).foregroundStyle(.secondary)
                }
                .width(min: 71, ideal: 130)
                .customizationID("ad-group")
            }
            .copyableRows(rows, primaryLabel: "Search Term",
                          primary: { $0.searchTerm },
                          row: { "\($0.searchTerm)\t\($0.spend)\t\($0.reason)\t\($0.adGroupId)" })
            // Greedy so single-sided fills and the both-case VSplitView fills;
            // was content-sized, which left dead space below.
            .frame(minHeight: 160)
            .background(Theme.Colors.surface)
        }
    }

    private func pausesTable(_ pauses: [ProposedPause]) -> some View {
        let rows = pauses.sorted(using: pauseSort)
        return VStack(spacing: 0) {
            SectionHeader(title: "Ad-group pauses",
                          subtitle: "\(approvedPauses.count)/\(pauses.count) approved",
                          count: pauses.count)
                .padding(.horizontal, Layout.Spacing.sm)
            Table(rows, selection: $pauseSelection, sortOrder: $pauseSort.descendingFirst(),
                  columnCustomization: $pauseColPrefs) {
                TableColumn("✓") { pause in
                    Toggle("", isOn: binding(for: pause.id, in: $approvedPauses))
                        .labelsHidden()
                        .accessibilityLabel("Approve pause \(pause.asin ?? pause.adGroupId)")
                }
                .width(min: 24, ideal: 28)
                .customizationID("col1")
                TableColumn("ASIN", value: \.asinValue) { pause in
                    AsinLink(asin: pause.asin)
                }
                .width(min: 60, ideal: 110)
                .customizationID("asin")
                TableColumn("Ad group", value: \.nameValue) { pause in
                    Text(pause.name ?? pause.adGroupId)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                .width(min: 160, ideal: 280)
                .customizationID("ad-group")
                TableColumn("Spend", value: \.spend) { pause in
                    MoneyText(value: pause.spend, currency: currency)
                }
                .width(min: 38, ideal: 70)
                .customizationID("spend")
                TableColumn("Why", value: \.reason) { pause in
                    TraceReasonCell(reason: pause.reason, trace: pause.trace)
                }
                .customizationID("why")
            }
            .copyableRows(rows, primaryLabel: "ASIN",
                          primary: { $0.asin ?? $0.adGroupId },
                          row: { "\($0.asin ?? "")\t\($0.name ?? $0.adGroupId)\t\($0.spend)\t\($0.reason)" })
            // Greedy so single-sided fills and the both-case VSplitView fills.
            .frame(minHeight: 160)
            .background(Theme.Colors.surface)
        }
    }

    private func binding<ID: Hashable>(for id: ID, in set: Binding<Set<ID>>) -> Binding<Bool> {
        Binding(
            get: { set.wrappedValue.contains(id) },
            set: { approved in
                if approved { set.wrappedValue.insert(id) } else { set.wrappedValue.remove(id) }
            }
        )
    }

    private func selectAll() {
        approvedNegatives = Set(preview?.negatives.map(\.id) ?? [])
        approvedPauses = Set(preview?.pauses.map(\.id) ?? [])
    }

    /// `fresh` = a screen/market-level reload (clears the last apply message);
    /// the reload right after apply() keeps it so the confirmation stays visible.
    private func load(fresh: Bool = false) async {
        isLoading = true
        defer { if !Task.isCancelled { isLoading = false } }
        loadError = nil
        let market = appState.selectedMarket
        if fresh {
            lastResult = nil
            // Clear BEFORE the await. The old market's negatives used to stay
            // on screen under the new market's header, with Apply live, so a
            // click sent US ids to the DE account.
            if previewMarket != market {
                preview = nil
                previewMarket = nil
                approvedNegatives.removeAll()
                approvedPauses.removeAll()
                pendingApplyIntent = nil
            }
        }
        do {
            let bridge = try appState.makeBridge()
            let response = try await bridge.call(NegativesPreviewResponse.self, ["negatives-preview"],
                                                 market: market)
            guard !Task.isCancelled, market == appState.selectedMarket else { return }
            preview = response
            previewMarket = market
            if fresh {
                selectAll()   // reviewed-by-default: uncheck what you disagree with
            } else {
                // Post-apply reload. Re-checking everything here would silently
                // re-approve exactly the rows the operator just rejected, and one
                // more click would fire them.
                approvedNegatives.formIntersection(Set(response.negatives.map(\.id)))
                approvedPauses.formIntersection(Set(response.pauses.map(\.id)))
            }
        } catch {
            guard !Task.isCancelled, market == appState.selectedMarket else { return }
            preview = nil
            previewMarket = nil
            loadError = error.localizedDescription
        }
    }

    private func requestApply() {
        guard let preview else { return }
        applyError = nil
        // These ids were resolved in one market. Building the intent from the
        // picker's CURRENT market is what would run them under another account.
        if let refusal = PlanMarket.refusal(planned: previewMarket,
                                            current: appState.selectedMarket) {
            applyError = refusal
            return
        }
        let negatives = preview.negatives.filter { approvedNegatives.contains($0.id) }
        let pauses = preview.pauses.filter { approvedPauses.contains($0.id) }
        // The snapshot these ids were resolved against. This screen can be left
        // open across a nightly pull, and every other apply path re-resolves
        // before it writes. The engine refuses the plan if the newest snapshot
        // has moved, so a term that has since earned its keep is not negated on
        // yesterday's evidence.
        var plan: [String: Any] = [
            "negatives": negatives.map {
                ["search_term": $0.searchTerm, "campaign_id": $0.campaignId, "ad_group_id": $0.adGroupId]
            },
            "pauses": pauses.map(\.adGroupId),
        ]
        if let asOf = preview.asOf { plan["as_of"] = asOf }
        // Each half's OWN snapshot date. The engine checks the negatives
        // against search_term_perf and the pauses against targeting_perf,
        // because those two tables drift apart.
        if let asOf = preview.asOfSearchTerms { plan["as_of_search_terms"] = asOf }
        if let asOf = preview.asOfTargeting { plan["as_of_targeting"] = asOf }
        do {
            let stdin = try JSONSerialization.data(withJSONObject: plan)
            pendingApplyIntent = appState.marketIntent(
                title: "Apply \(negatives.count) negatives and \(pauses.count) pauses",
                arguments: ["negatives-apply"], stdin: stdin,
                cardinality: .bulk, responseKind: .negativesApply)
        } catch {
            applyError = error.localizedDescription
        }
    }

    private func apply(_ intent: ActionIntent) async {
        // The picker can move while the confirmation dialog is open, and the
        // dialog names the market the intent carries. Refuse rather than write
        // to an account the operator is no longer looking at.
        if let refusal = PlanMarket.refusal(planned: intent.scope.market,
                                            current: appState.selectedMarket) {
            applyError = refusal
            return
        }
        applying = true
        defer { applying = false }
        applyError = nil
        do {
            let receipt = try await appState.actionCoordinator.execute(
                intent, context: appState.actionPolicyContext, confirmed: true)
            guard !receipt.rehearsed else { return }
            guard case .negativesApply(let negativesApplied, let pausesApplied,
                                       let partialNote) = receipt.result else { return }
            // Amazon refuses individual items inside a 207 routinely, so a
            // batch can land partly. Saying only what was applied would leave
            // the operator believing the queue emptied cleanly.
            lastResult = "Applied \(negativesApplied) negatives, \(pausesApplied) pauses."
                       + (partialNote.map { " " + $0 } ?? "")
            if intent.scope.market == appState.selectedMarket {
                await load()   // keeps lastResult — the queue refreshes under the confirmation
            }
        } catch {
            applyError = error.localizedDescription
        }
    }

    private func requestApprovalMode(_ enabled: Bool) {
        pendingApprovalIntent = appState.globalIntent(
            title: enabled ? "Require approval globally?" : "Disable global approval requirement?",
            arguments: ["approval-mode", enabled ? "--on" : "--off"],
            confirmationPolicy: .required)
    }

    private func setApprovalMode(_ intent: ActionIntent) async {
        applyError = nil
        do {
            let receipt = try await appState.actionCoordinator.execute(
                intent, context: appState.actionPolicyContext, confirmed: true)
            guard !receipt.rehearsed else { return }
            await appState.refresh()
        } catch {
            applyError = error.localizedDescription
        }
    }
}

#Preview {
    ApprovalsView()
        .environment(AppState())
}
