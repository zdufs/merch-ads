import SwiftUI

/// Every keyword/target (what you bid on) summed across every campaign/ad group
/// it runs in (latest cumulative snapshot). A term can look fine in one campaign
/// but be a disaster across ten; this rolls it up. Select a row for the
/// per-campaign breakdown.
struct AccumulatedKeywordsView: View {
    @Environment(AppState.self) private var appState

    @State private var response: AccumulatedKeywordsResponse?
    @State private var loadError: String?
    @State private var isLoading = false
    @State private var selection = Set<AccumulatedKeywordRow.ID>()
    @State private var primaryID: AccumulatedKeywordRow.ID?
    @State private var primaryTargeting: String?
    @State private var breakdown: [AccumulatedBreakdownRow] = []
    @State private var breakdownLoading = false
    @State private var breakdownError: String?
    @State private var generated = false
    @State private var breakdownLoadID = 0   // stale-selection guard: only the newest breakdown lands
    @State private var everywherePending: EverywherePending?
    @State private var everywherePreviewError: String?
    @State private var bidEntryKeys: [String]?       // keys awaiting a bid value
    @State private var bidText = ""

    private static let sortFields: [String: KeyPathComparator<AccumulatedKeywordRow>] = [
        "campaigns": .init(\.campaigns), "clicks": .init(\.clicks), "spend": .init(\.spend),
        "orders": .init(\.orders), "sales": .init(\.sales),
    ]
    @State private var sort = SortPrefs.load(
        TableID.accumulatedKeywords, fields: sortFields,
        fallback: [KeyPathComparator(\AccumulatedKeywordRow.spend, order: .reverse)])
    @State private var colPrefs: TableColumnCustomization<AccumulatedKeywordRow> =
        ColumnPrefs.load(TableID.accumulatedKeywords)

    private var currency: String? { appState.currentMarket?.currency }
    // Sorted once into state, not on every read. The response can be 31k+ rows, and
    // `rows` is read several times per body pass and again on selection — a computed
    // `sorted(using:)` re-sorted the whole array each time, so selecting a row or
    // dragging a column divider re-sorted 31k rows repeatedly and janked. Rebuilt
    // only when the response lands or the sort changes.
    @State private var rows: [AccumulatedKeywordRow] = []

    private func rebuildRows() {
        rows = (response?.rows ?? []).sorted(using: sort)
    }

    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "Accumulated Keywords", subtitle: subtitle, help: .accumulatedKeywords)
            if !generated {
                AccumulatedReportEmptyState(
                    title: "No keywords report yet",
                    description: "Generate a full accumulation of every keyword's performance across every campaign. It builds in the background — you can keep working and it'll be ready in a moment.",
                    isGenerating: isLoading) {
                        generated = true
                        Task { await load() }
                    }
            } else {
                LoadableView(
                    isLoading: isLoading && response == nil,
                    error: loadError,
                    isEmpty: response != nil && rows.isEmpty,
                    loadingTitle: "Rolling up keywords across campaigns…",
                    emptyTitle: "No keywords",
                    emptyDescription: "No targeting data is banked for \(appState.selectedMarket) yet.",
                    systemImage: "text.append",
                    retry: { Task { await load() } }
                ) {
                    table
                }
            }
        }
        .background(Theme.Colors.canvas)
        .toolbar { if generated { toolbarRebuild; toolbarExport } }
        .task(id: appState.viewKey) { restoreOrReset() }
        .onChange(of: sort) {
            rebuildRows()
            SortPrefs.save(TableID.accumulatedKeywords, sort, fields: Self.sortFields)
        }
        .onChange(of: colPrefs) { ColumnPrefs.save(TableID.accumulatedKeywords, colPrefs) }
        .onChange(of: selection) { old, new in
            primaryID = PrimaryRow.latest(old: old, new: new, current: primaryID)
            // Look up the one selected row from the unsorted source — no need to
            // touch the sorted array (or, formerly, re-sort 31k rows) to read one id.
            primaryTargeting = response?.rows.first { $0.id == primaryID }?.targeting
            Task { await loadBreakdown() }
        }
        .inspector(isPresented: Binding(get: { primaryID != nil },
                                        set: { if !$0 { primaryID = nil; primaryTargeting = nil; selection = [] } })) {
            // Opens wide enough to show the whole breakdown (campaign · status ·
            // ad group · metrics) without truncation, and stays user-resizable.
            breakdownInspector
                .inspectorColumnWidth(min: 420, ideal: 680, max: 960)
        }
        .alert("Preview unavailable", isPresented: Binding(
            get: { everywherePreviewError != nil },
            set: { if !$0 { everywherePreviewError = nil } })) {
            Button("OK") { everywherePreviewError = nil }
        } message: {
            Text(everywherePreviewError ?? "")
        }
    }

    /// The engine reads one cumulative `targeting_perf` snapshot — Amazon's
    /// trailing-30 figures as of that date. Say so; there is no other window.
    private var subtitle: String {
        let n = response?.count ?? 0
        if let asOf = response?.asOf {
            // say when the table holds less than the total, instead of printing
            // the total above a shorter table
            let shown = response?.returned ?? n
            let scope = (response?.truncated == true) ? "\(shown) of \(n)" : "\(n)"
            return "\(appState.selectedMarket) · \(scope) keywords · trailing 30 days through \(Format.euDate(asOf))"
        }
        return "\(appState.selectedMarket) · cross-campaign rollup · trailing 30 days"
    }

    private var table: some View {
        Table(rows, selection: $selection, sortOrder: $sort.descendingFirst(), columnCustomization: $colPrefs) {
            TableColumn("Keyword", value: \.targeting) { r in
                Text(r.targeting).entityLink().lineLimit(1)
            }.width(min: 120, ideal: 220).customizationID("targeting")
            TableColumn("Match", value: \.matchValue) { r in
                Text(r.matchType ?? "—").font(.caption).foregroundStyle(.secondary)
            }.width(min: 60, ideal: 110).customizationID("match")
            TableColumn("Camps", value: \.campaigns) { r in
                CountText(value: r.campaigns)
            }.width(min: 34, ideal: 55).customizationID("campaigns")
            TableColumn("Clicks", value: \.clicks) { r in
                CountText(value: r.clicks)
            }.width(min: 34, ideal: 55).customizationID("clicks")
            TableColumn("Spend", value: \.spend) { r in
                MoneyText(value: r.spend, currency: currency)
            }.width(min: 44, ideal: 70).customizationID("spend")
            TableColumn("Orders", value: \.orders) { r in
                CountText(value: r.orders)
            }.width(min: 34, ideal: 55).customizationID("orders")
            TableColumn("Sales", value: \.sales) { r in
                MoneyText(value: r.sales, currency: currency)
            }.width(min: 44, ideal: 70).customizationID("sales")
            TableColumn("ACOS", value: \.acosValue) { r in
                PercentText(value: r.acos, label: "ACOS")
            }.width(min: 40, ideal: 60).customizationID("acos")
        }
        .copyableRows(rows, primaryLabel: "Keyword",
                      primary: { $0.targeting },
                      row: { "\($0.targeting)\t\($0.matchType ?? "")\t\($0.campaigns)\t\($0.clicks)\t\($0.spend)\t\($0.orders)\t\($0.sales)" })
        .contextMenu(forSelectionType: AccumulatedKeywordRow.ID.self) { ids in
            let keys = Array(Set(rows.filter { ids.contains($0.id) }.map(\.targeting)))
            let label = keys.count > 1 ? "\(keys.count) keywords" : "keyword"
            // Negate everywhere blocks the term across every campaign.
            Button("Negate \(label) everywhere (exact)", role: .destructive) {
                Task { await prepareEverywhere(action: "negate", keys: keys,
                                               verb: "Negate", match: "exact") }
            }
            Button("Negate \(label) everywhere (phrase)", role: .destructive) {
                Task { await prepareEverywhere(action: "negate", keys: keys,
                                               verb: "Negate", match: "phrase") }
            }
            // Pause everywhere pauses the keyword's target clauses in every ad group.
            Button("Pause \(label) everywhere", role: .destructive) {
                Task { await prepareEverywhere(action: "pause", keys: keys, verb: "Pause") }
            }
            // Set bid everywhere needs a value first.
            Button("Set bid on \(label) everywhere…") {
                bidText = ""
                bidEntryKeys = keys
            }
        }
        .everywhereConfirm($everywherePending) { await load() }
        .alert("Set bid everywhere", isPresented: Binding(
            get: { bidEntryKeys != nil }, set: { if !$0 { bidEntryKeys = nil } })) {
            TextField("Bid, e.g. 0.45", text: $bidText)
            Button("Continue") {
                if let keys = bidEntryKeys, let bid = Double(bidText), bid > 0 {
                    bidEntryKeys = nil
                    Task { await prepareEverywhere(action: "setbid", keys: keys,
                                                   verb: "Set bid", bid: bid) }
                } else {
                    bidEntryKeys = nil
                }
            }
            Button("Cancel", role: .cancel) { bidEntryKeys = nil }
        } message: {
            Text("The bid is clamped to your max-bid ceiling and applied to every enabled instance of the selected keyword\(bidEntryKeys.map { $0.count > 1 ? "s" : "" } ?? "").")
        }
    }

    @ToolbarContentBuilder
    private var toolbarRebuild: some ToolbarContent {
        ToolbarItem {
            Button {
                Task { await rebuild() }
            } label: {
                Label("Rebuild", systemImage: "arrow.clockwise")
            }
            .disabled(isLoading)
            .help("Rebuild this report from the latest banked snapshot")
        }
    }

    private var toolbarExport: some ToolbarContent {
        ToolbarItem {
            ExportButton(filename: "accumulated-keywords-\(appState.selectedMarket)") {
                CSVDocument(
                    headers: ["targeting", "match_type", "campaigns", "ad_groups", "clicks",
                              "spend", "orders", "sales", "acos", "cvr"],
                    rows: rows.map { r in
                        [r.targeting, r.matchType ?? "", String(r.campaigns), String(r.adGroups),
                         String(r.clicks), String(r.spend), String(r.orders), String(r.sales),
                         r.acos.map { String($0) } ?? "", r.cvr.map { String($0) } ?? ""]
                    })
            }
        }
    }

    @ViewBuilder
    private var breakdownInspector: some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.sm) {
            if let targeting = primaryTargeting {
                SectionHeader(title: targeting, subtitle: "per-campaign breakdown")
                AccumulatedBreakdownTable(rows: breakdown, currency: currency, showMatch: true)
                    .overlay {
                        switch BreakdownPresentation.resolve(
                            isLoading: breakdownLoading, error: breakdownError,
                            isEmpty: breakdown.isEmpty) {
                        case .loading:
                            ProgressView("Loading breakdown…")
                                .frame(maxWidth: .infinity, maxHeight: .infinity)
                                .background(Theme.Colors.surface)
                        case .unavailable(let error):
                            ContentUnavailableView {
                                Label("Breakdown unavailable", systemImage: "exclamationmark.triangle")
                            } description: { Text(error) } actions: {
                                Button("Retry") { Task { await loadBreakdown() } }
                            }
                            .background(Theme.Colors.surface)
                        case .empty:
                            ContentUnavailableView("No campaign instances", systemImage: "rectangle.stack")
                                .background(Theme.Colors.surface)
                        case .content:
                            EmptyView()
                        }
                    }
                }
            Spacer()
        }
        .padding(Layout.Spacing.sm)
    }

    private func load() async {
        isLoading = true
        defer { if !Task.isCancelled { isLoading = false } }
        loadError = nil
        do {
            let key = appState.viewKey
            let bridge = try appState.makeBridge()
            // --limit 0 = every row. The old call took the engine's default of
            // 500, which silently dropped 680 ASINs that had actually spent
            // money. preferWorker: false because a full report is megabytes and
            // the serve worker reads its reply one line at a time.
            let fresh = try await bridge.call(AccumulatedKeywordsResponse.self,
                                              ["accumulated-keywords", "--limit", "0"],
                                              market: appState.selectedMarket,
                                              preferWorker: false)
            guard !Task.isCancelled else { return }
            response = fresh
            rebuildRows()
            AccumulatedStore.shared.store(fresh, for: key)
        } catch {
            guard !Task.isCancelled else { return }
            response = nil
            rebuildRows()
            loadError = error.localizedDescription
        }
    }

    /// Coming back to this screen should show the report you already built.
    /// The view is destroyed on every tab switch, so the report lives in
    /// AccumulatedStore, keyed by market + data stamp.
    private func restoreOrReset() {
        if let cached = AccumulatedStore.shared.keywords(for: appState.viewKey) {
            response = cached
            generated = true
            loadError = nil
        } else {
            response = nil
            generated = false
        }
        rebuildRows()
    }

    /// Force a rebuild of a cached report (new data, or just to be sure).
    private func rebuild() async {
        AccumulatedStore.shared.invalidate(key: appState.viewKey)
        await load()
    }

    private func loadBreakdown() async {
        breakdownLoadID += 1
        let requestID = breakdownLoadID   // two quick selections = two loads; only the newest lands
        breakdown = []
        breakdownError = nil
        guard let targeting = primaryTargeting else { breakdownLoading = false; return }
        breakdownLoading = true
        defer { if breakdownLoadID == requestID { breakdownLoading = false } }
        do {
            let bridge = try appState.makeBridge()
            let resp = try await bridge.call(AccumulatedBreakdownResponse.self,
                                             ["accumulated-keywords", "--expand", targeting],
                                             market: appState.selectedMarket)
            guard breakdownLoadID == requestID, !Task.isCancelled else { return }
            breakdown = resp.breakdown
        } catch {
            guard breakdownLoadID == requestID else { return }
            breakdown = []
            breakdownError = error.localizedDescription
        }
    }

    private func prepareEverywhere(action: String, keys: [String], verb: String,
                                   match: String = "exact", bid: Double? = nil) async {
        do {
            everywherePending = try await resolveEverywhere(
                appState, kind: "keyword", action: action, keys: keys,
                verbLabel: verb, noun: "keyword", match: match, bid: bid)
        } catch is CancellationError {
            return
        } catch {
            everywherePending = nil
            everywherePreviewError = error.localizedDescription
        }
    }
}

#Preview {
    AccumulatedKeywordsView()
        .environment(AppState())
}
