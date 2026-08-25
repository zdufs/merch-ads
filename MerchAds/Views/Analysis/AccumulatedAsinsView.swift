import SwiftUI

/// Every advertised ASIN summed across every campaign it runs in (latest
/// cumulative snapshot). Amazon reports per campaign; this rolls up by the design
/// itself, exposing ASINs quietly bleeding budget across many small campaigns.
/// Select a row to see its per-campaign breakdown.
struct AccumulatedAsinsView: View {
    @Environment(AppState.self) private var appState

    @State private var response: AccumulatedAsinsResponse?
    @State private var loadError: String?
    @State private var isLoading = false
    @State private var selection = Set<AccumulatedAsinRow.ID>()
    @State private var primaryID: AccumulatedAsinRow.ID?
    @State private var breakdown: [AccumulatedBreakdownRow] = []
    @State private var breakdownLoading = false
    @State private var breakdownError: String?
    @State private var generated = false
    @State private var breakdownLoadID = 0   // stale-selection guard: only the newest breakdown lands
    @State private var everywherePending: EverywherePending?
    @State private var everywherePreviewError: String?

    private static let sortFields: [String: KeyPathComparator<AccumulatedAsinRow>] = [
        "campaigns": .init(\.campaigns), "clicks": .init(\.clicks), "spend": .init(\.spend),
        "orders": .init(\.orders), "sales": .init(\.sales),
    ]
    @State private var sort = SortPrefs.load(
        TableID.accumulatedAsins, fields: sortFields,
        fallback: [KeyPathComparator(\AccumulatedAsinRow.spend, order: .reverse)])
    @State private var colPrefs: TableColumnCustomization<AccumulatedAsinRow> =
        ColumnPrefs.load(TableID.accumulatedAsins)

    private var currency: String? { appState.currentMarket?.currency }
    // Sorted once into state, not on every read. The response can be 31k+ rows, and
    // `rows` is read several times per body pass and again on selection — a computed
    // `sorted(using:)` re-sorted the whole array each time, so selecting a row or
    // dragging a column divider re-sorted 31k rows repeatedly and janked. Rebuilt
    // only when the response lands or the sort changes.
    @State private var rows: [AccumulatedAsinRow] = []

    private func rebuildRows() {
        rows = (response?.rows ?? []).sorted(using: sort)
    }

    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "Accumulated ASINs", subtitle: subtitle, help: .accumulatedAsins)
            if !generated {
                AccumulatedReportEmptyState(
                    title: "No ASINs report yet",
                    description: "Generate a full accumulation of every ASIN's performance across every campaign. It builds in the background — you can keep working and it'll be ready in a moment.",
                    isGenerating: isLoading) {
                        generated = true
                        Task { await load() }
                    }
            } else {
                LoadableView(
                    isLoading: isLoading && response == nil,
                    error: loadError,
                    isEmpty: response != nil && rows.isEmpty,
                    loadingTitle: "Rolling up ASINs across campaigns…",
                    emptyTitle: "No advertised ASINs",
                    emptyDescription: "No targeting data is banked for \(appState.selectedMarket) yet.",
                    systemImage: "square.stack.3d.up",
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
            SortPrefs.save(TableID.accumulatedAsins, sort, fields: Self.sortFields)
        }
        .onChange(of: colPrefs) { ColumnPrefs.save(TableID.accumulatedAsins, colPrefs) }
        .onChange(of: selection) { old, new in
            primaryID = PrimaryRow.latest(old: old, new: new, current: primaryID)
            Task { await loadBreakdown() }
        }
        .inspector(isPresented: Binding(get: { primaryID != nil },
                                        set: { if !$0 { primaryID = nil; selection = [] } })) {
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
            return "\(appState.selectedMarket) · \(scope) ASINs · trailing 30 days through \(Format.euDate(asOf))"
        }
        return "\(appState.selectedMarket) · cross-campaign rollup · trailing 30 days"
    }

    private var table: some View {
        Table(rows, selection: $selection, sortOrder: $sort.descendingFirst(), columnCustomization: $colPrefs) {
            TableColumn("ASIN", value: \.asin) { r in
                AsinLink(asin: r.asin, prominent: true)
            }.width(min: 66, ideal: 110).customizationID("asin")
            TableColumn("Type", value: \.productTypeValue) { r in
                StatusBadge.campaignType(r.productType)
            }.width(min: 70, ideal: 130).customizationID("type")
            TableColumn("Camps", value: \.campaigns) { r in
                CountText(value: r.campaigns)
            }.width(min: 34, ideal: 55).customizationID("campaigns")
            TableColumn("Ad grps", value: \.adGroups) { r in
                CountText(value: r.adGroups)
            }.width(min: 40, ideal: 60).customizationID("adGroups")
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
        .copyableRows(rows, primaryLabel: "ASIN",
                      primary: { $0.asin },
                      row: { "\($0.asin)\t\($0.campaigns)\t\($0.clicks)\t\($0.spend)\t\($0.orders)\t\($0.sales)" })
        .contextMenu(forSelectionType: AccumulatedAsinRow.ID.self) { ids in
            let picked = rows.filter { ids.contains($0.id) }
            // Pause a bleeding ASIN across every campaign it runs in, in one shot.
            Button("Pause \(picked.count > 1 ? "\(picked.count) ASINs" : "ASIN") everywhere",
                   role: .destructive) {
                let keys = picked.map(\.asin)
                Task { await prepareEverywhere(keys: keys) }
            }
        }
        .everywhereConfirm($everywherePending) { await load() }
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
            ExportButton(filename: "accumulated-asins-\(appState.selectedMarket)") {
                CSVDocument(
                    headers: ["asin", "type", "campaigns", "ad_groups", "clicks",
                              "spend", "orders", "sales", "acos", "cvr"],
                    rows: rows.map { r in
                        [r.asin, r.productType ?? "", String(r.campaigns), String(r.adGroups),
                         String(r.clicks), String(r.spend), String(r.orders), String(r.sales),
                         r.acos.map { String($0) } ?? "", r.cvr.map { String($0) } ?? ""]
                    })
            }
        }
    }

    @ViewBuilder
    private var breakdownInspector: some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.sm) {
            if let id = primaryID {
                SectionHeader(title: id, subtitle: "per-campaign breakdown")
                AccumulatedBreakdownTable(rows: breakdown, currency: currency, showMatch: false)
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
            let fresh = try await bridge.call(AccumulatedAsinsResponse.self,
                                              ["accumulated-asins", "--limit", "0"],
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
        if let cached = AccumulatedStore.shared.asins(for: appState.viewKey) {
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
        guard let id = primaryID else { breakdownLoading = false; return }
        breakdownLoading = true
        defer { if breakdownLoadID == requestID { breakdownLoading = false } }
        do {
            let bridge = try appState.makeBridge()
            let resp = try await bridge.call(AccumulatedBreakdownResponse.self,
                                             ["accumulated-asins", "--expand", id],
                                             market: appState.selectedMarket)
            guard breakdownLoadID == requestID, !Task.isCancelled else { return }
            breakdown = resp.breakdown
        } catch {
            guard breakdownLoadID == requestID else { return }
            breakdown = []
            breakdownError = error.localizedDescription
        }
    }

    private func prepareEverywhere(keys: [String]) async {
        do {
            everywherePending = try await resolveEverywhere(
                appState, kind: "asin", action: "pause", keys: keys,
                verbLabel: "Pause", noun: "ASIN")
        } catch is CancellationError {
            return
        } catch {
            everywherePending = nil
            everywherePreviewError = error.localizedDescription
        }
    }
}

enum BreakdownPresentation: Equatable {
    case loading
    case unavailable(String)
    case empty
    case content

    static func resolve(isLoading: Bool, error: String?, isEmpty: Bool) -> Self {
        if isLoading { return .loading }
        if let error { return .unavailable(error) }
        return isEmpty ? .empty : .content
    }
}

/// "Generate report" empty state for the Accumulated screens. Deliberately has
/// no timeframe picker: `accumulated-asins` / `accumulated-keywords` read the
/// latest cumulative `targeting_perf` snapshot and take no window argument, so
/// a picker here would only pretend to change the numbers.
struct AccumulatedReportEmptyState: View {
    let title: String
    let description: String
    var isGenerating: Bool = false
    let onGenerate: () -> Void

    var body: some View {
        VStack(spacing: 14) {
            ZStack {
                Circle().fill(Theme.Colors.accentSoft).frame(width: 56, height: 56)
                Image(systemName: "chart.line.uptrend.xyaxis")
                    .font(Typography.cardValue)
                    .foregroundStyle(Theme.Colors.accent)
            }
            Text(title)
                .font(Typography.cardTitle)
                .foregroundStyle(Theme.Colors.textPrimary)
            Text(description)
                .font(Typography.cardBody)
                .foregroundStyle(Theme.Colors.textSecondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 460)
            Label("Covers the trailing 30 days — the cumulative snapshot Amazon reports. No other window is available.",
                  systemImage: "calendar")
                .font(.caption)
                .foregroundStyle(Theme.Colors.textSecondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 460)
            Button(action: onGenerate) {
                if isGenerating {
                    HStack(spacing: 6) { ProgressView().controlSize(.small); Text("Building…") }
                } else {
                    Text("Generate report")
                }
            }
            .buttonStyle(.borderedProminent)
            .disabled(isGenerating)
            .padding(.top, 4)
        }
        .padding(Layout.Spacing.xl)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .mdCard()
        .padding(Layout.Spacing.lg)
    }
}

/// Shared per-campaign breakdown table used by both accumulated screens.
struct AccumulatedBreakdownTable: View {
    let rows: [AccumulatedBreakdownRow]
    let currency: String?
    var showMatch: Bool = false
    // Table cells can't be text-selected on macOS — right-click Copy instead.
    @State private var rowSel = Set<AccumulatedBreakdownRow.ID>()
    @State private var sort = [KeyPathComparator(\AccumulatedBreakdownRow.spend, order: .reverse)]

    var body: some View {
        Table(rows.sorted(using: sort), selection: $rowSel, sortOrder: $sort.descendingFirst()) {
            TableColumn("Campaign", value: \.campaignValue) { r in
                Text(r.campaign ?? r.campaignId).lineLimit(1)
            }.width(min: 120, ideal: 200)
            // Whether the campaign is live matters when deciding on a row: a
            // paused or archived campaign's spend is history, not something to act on.
            TableColumn("Status", value: \.stateValue) { r in
                StatusBadge.entityState(r.state)
            }.width(min: 78, ideal: 96)
            TableColumn("Ad group", value: \.adGroupValue) { r in
                Text(r.adGroup ?? r.adGroupId).lineLimit(1).foregroundStyle(.secondary)
            }.width(min: 100, ideal: 150)
            TableColumn("Spend", value: \.spend) { r in MoneyText(value: r.spend, currency: currency) }
                .width(min: 52, ideal: 72)
            TableColumn("Orders", value: \.orders) { r in CountText(value: r.orders) }
                .width(min: 44, ideal: 58)
            TableColumn("ACOS", value: \.acosValue) { r in PercentText(value: r.acos, label: "ACOS") }
                .width(min: 48, ideal: 64)
        }
        .frame(minHeight: 160)
        .copyableRows(rows, primaryLabel: "Campaign",
                      primary: { $0.campaign ?? $0.campaignId },
                      row: { "\($0.campaign ?? $0.campaignId)\t\($0.state ?? "")\t\($0.adGroup ?? $0.adGroupId)\t\($0.spend)\t\($0.orders)" })
    }
}

#Preview {
    AccumulatedAsinsView()
        .environment(AppState())
}
