import SwiftUI

/// The MerchPirate demand feed, in-app: proven-converting customer searches to
/// design NEW work for, and top recent earners to make variations of.
/// (IP filtering is best-effort — human trademark review still required.)
struct DemandFeedView: View {
    @Environment(AppState.self) private var appState

    @State private var feed: DemandFeedResponse?
    @State private var loadError: String?
    @State private var isLoading = false
    @State private var refreshing = false
    @State private var actionError: String?
    @State private var pendingRefresh: ActionIntent?
    @State private var seedColPrefs: TableColumnCustomization<DemandSeed> = ColumnPrefs.load(TableID.demandSeeds)
    @State private var provenColPrefs: TableColumnCustomization<ProvenSeller> = ColumnPrefs.load(TableID.demandSellers)
    @State private var seedSelection = Set<DemandSeed.ID>()
    @State private var seedSort = SortPrefs.load(
        TableID.demandSeeds, fields: seedSortFields,
        fallback: [KeyPathComparator(\DemandSeed.sales, order: .reverse)])
    private static let seedSortFields: [String: KeyPathComparator<DemandSeed>] = [
        "term": .init(\.term), "orders": .init(\.orders), "sales": .init(\.sales),
    ]
    @State private var provenSelection = Set<ProvenSeller.ID>()
    @State private var provenSort = SortPrefs.load(
        TableID.demandSellers, fields: provenSortFields,
        fallback: [KeyPathComparator(\ProvenSeller.royaltyLast30, order: .reverse)])
    private static let provenSortFields: [String: KeyPathComparator<ProvenSeller>] = [
        "asin": .init(\.asin), "salesLast30": .init(\.salesLast30),
        "royaltyLast30": .init(\.royaltyLast30),
    ]

    private var currency: String? { appState.currentMarket?.currency }

    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "Demand Feed", subtitle: navigationSubtitle, help: .demandFeed)
            statusBand
            actionBar
            ActionErrorBar(message: $actionError)
            Divider()
            // The two tables are UNCONDITIONAL siblings; loading / error / empty are
            // drawn as an overlay LAYER over them, never as a @ViewBuilder if/else
            // that toggles the tables away. On macOS 26 toggling a greedy Table's
            // presence inside an if/else blanks the whole detail into empty
            // placeholder rows (see CrossPurchaseView's doc comment). The overlay is
            // scoped to the table region so the header, status band and actions stay
            // visible while data loads.
            VSplitView {
                seedsTable(feed?.keywordSeeds ?? [])
                provenTable(feed?.provenSellers ?? [])
            }
            .frame(maxHeight: .infinity, alignment: .top)
            .overlay { stateOverlay }
        }
        .background(Theme.Colors.canvas)
        .task(id: appState.viewKey) { await load() }
        .onChange(of: seedColPrefs) { ColumnPrefs.save(TableID.demandSeeds, seedColPrefs) }
        .onChange(of: provenColPrefs) { ColumnPrefs.save(TableID.demandSellers, provenColPrefs) }
        .onChange(of: seedSort) { SortPrefs.save(TableID.demandSeeds, seedSort, fields: Self.seedSortFields) }
        .onChange(of: provenSort) { SortPrefs.save(TableID.demandSellers, provenSort, fields: Self.provenSortFields) }
        .confirmationDialog(
            "Rebuild demand feed?",
            isPresented: Binding(get: { pendingRefresh != nil },
                                 set: { if !$0 { pendingRefresh = nil } }),
            presenting: pendingRefresh
        ) { intent in
            Button("Rebuild", role: .destructive) {
                Task { await rebuild(intent, confirmed: true) }
            }
        } message: { intent in
            Text("This runs the long demand-feed engine job for \(intent.scope.confirmationDescription).")
        }
    }

    private var navigationSubtitle: String {
        if let generated = feed?.generated {
            return "\(appState.selectedMarket) · as of \(Format.euDateTime(generated))"
        }
        return "\(appState.selectedMarket) · demand signals"
    }

    // Loading / error / "no data", drawn OVER the always-present tables. A
    // conditional sibling of the greedy tables blanks the detail on macOS 26.
    @ViewBuilder
    private var stateOverlay: some View {
        if let loadError {
            overlayMessage(title: "Demand feed unavailable", detail: loadError,
                           retry: { Task { await load() } })
        } else if feed == nil && isLoading {
            ProgressView("Loading demand feed…")
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Theme.Colors.canvas)
        } else if let feed, feed.keywordSeeds.isEmpty, feed.provenSellers.isEmpty {
            overlayMessage(
                title: "No demand signals",
                detail: "The current demand feed has no keyword seeds or proven sellers.")
        }
        // data present → nothing drawn, the tables show through.
    }

    private func overlayMessage(title: String, detail: String,
                                retry: (() -> Void)? = nil) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.callout.weight(.semibold))
            Text(detail)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if let retry {
                Button("Retry", action: retry)
                    .controlSize(.small)
                    .padding(.top, 2)
            }
        }
        .padding(Layout.Spacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .mdCard()
        .padding(Layout.Spacing.lg)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background(Theme.Colors.canvas)
    }

    private var statusBand: some View {
        Group {
            HStack(spacing: Layout.Spacing.sm) {
                StatCard(title: "Keyword seeds",
                         value: feed.map { Format.count($0.keywordSeeds.count) } ?? "—",
                         symbol: "text.magnifyingglass")
                    .mdCard()
                StatCard(title: "Proven sellers",
                         value: feed.map { Format.count($0.provenSellers.count) } ?? "—",
                         symbol: "seal.fill")
                    .mdCard()
                StatCard(title: "As of", value: feed?.generated ?? "—",
                         symbol: "calendar", subtitle: "engine-generated snapshot")
                    .mdCard()
            }
        }
        .padding(.horizontal, Layout.Spacing.lg)
        .padding(.vertical, Layout.Spacing.md)
    }

    private var actionBar: some View {
        FilterBar {
            Text("IP filter is best-effort — human trademark review before upload")
                .font(.caption2)
                .foregroundStyle(Theme.Colors.caution)
        } trailing: {
            if let feed {
                ExportButton(filename: "demand-feed-\(appState.selectedMarket)") {
                    CSVDocument(
                        headers: ["stream", "term_or_asin", "niche_or_title", "product_type",
                                  "orders_or_sales30", "sales_or_royalty30"],
                        rows: feed.keywordSeeds.map { seed in
                            ["keyword_seed", seed.term, seed.niche ?? "",
                             seed.productType ?? "", String(seed.orders), String(seed.sales)]
                        } + feed.provenSellers.map { seller in
                            ["proven_seller", seller.asin, seller.title ?? "",
                             seller.productType ?? "", String(seller.salesLast30),
                             String(seller.royaltyLast30)]
                        })
                }
            }
            Button {
                requestRefresh()
            } label: {
                if refreshing {
                    HStack(spacing: Layout.Spacing.xs) {
                        ProgressView().controlSize(.small)
                        Text("Regenerating…")
                    }
                } else {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
            }
            .disabled(refreshing)
            .help("Rebuilds the demand feed against the latest snapshot")
        }
    }

    private func seedsTable(_ seeds: [DemandSeed]) -> some View {
        let rows = seeds.sorted(using: seedSort)
        return VStack(spacing: 0) {
            SectionHeader(title: "Keyword seeds",
                          subtitle: "converting customer searches · design new work",
                          count: rows.count)
                .padding(.horizontal, Layout.Spacing.sm)
            Table(rows, selection: $seedSelection, sortOrder: $seedSort.descendingFirst(), columnCustomization: $seedColPrefs) {
                TableColumn("Search term", value: \.term) { seed in
                    Text(seed.term)
                        .textSelection(.enabled)
                }
                .width(min: 200, ideal: 300)
                .customizationID("search-term")
                TableColumn("Niche", value: \.nicheValue) { seed in
                    Text(seed.niche ?? "—").foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                .width(min: 77, ideal: 140)
                .customizationID("niche")
                TableColumn("Type", value: \.productTypeValue) { seed in
                    StatusBadge.campaignType(seed.productType)
                }
                .width(min: 66, ideal: 120)
                .customizationID("type")
                TableColumn("Orders", value: \.orders) { seed in
                    CountText(value: seed.orders)
                }
                .width(min: 27, ideal: 50)
                .customizationID("orders")
                TableColumn("Sales", value: \.sales) { seed in
                    MoneyText(value: seed.sales, currency: currency)
                }
                .width(min: 41, ideal: 75)
                .customizationID("sales")
                TableColumn("ACOS", value: \.acosValue) { seed in
                    PercentText(value: seed.acos, label: "ACOS")
                }
                .width(min: 33, ideal: 60)
                .customizationID("acos")
                TableColumn("CVR", value: \.cvrValue) { seed in
                    // orders can exceed clicks (halo attribution) — ">100%" reads
                    // like a bug, so show the multiple with an explanation instead
                    if let cvr = seed.cvr, cvr > 1 {
                        Text(String(format: "%.1f×", cvr))
                            .font(Typography.tableNumeral)
                            .foregroundStyle(.secondary)
                            .help("\(Format.count(seed.orders)) orders from fewer clicks — brand-halo attribution counts orders the click didn't directly cause")
                    } else {
                        PercentText(value: seed.cvr, label: "CVR", color: .primary)
                    }
                }
                .width(min: 30, ideal: 55)
                .customizationID("cvr")
            }
            .copyableRows(rows, primaryLabel: "Search Term",
                          primary: { $0.term },
                          row: { "\($0.term)\t\($0.niche ?? "")\t\($0.productType ?? "")\t\($0.orders)\t\($0.sales)" })
            .frame(minHeight: 150)
            .background(Theme.Colors.surface)
        }
    }

    private func provenTable(_ sellers: [ProvenSeller]) -> some View {
        let rows = sellers.sorted(using: provenSort)
        return VStack(spacing: 0) {
            SectionHeader(title: "Proven sellers",
                          subtitle: "top recent earners · make variations",
                          count: rows.count)
                .padding(.horizontal, Layout.Spacing.sm)
            Table(rows, selection: $provenSelection, sortOrder: $provenSort.descendingFirst(), columnCustomization: $provenColPrefs) {
                TableColumn("ASIN", value: \.asin) { seller in
                    Text(seller.asin).font(.body.monospaced())
                        .textSelection(.enabled)
                }
                .width(min: 60, ideal: 110)
                .customizationID("asin")
                TableColumn("Title", value: \.titleValue) { seller in
                    Text(seller.title ?? "—")
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
                .width(min: 200, ideal: 320)
                .customizationID("title")
                TableColumn("Type", value: \.productTypeValue) { seller in
                    StatusBadge.campaignType(seller.productType)
                }
                .width(min: 66, ideal: 120)
                .customizationID("type")
                TableColumn("Sales 30d", value: \.salesLast30) { seller in
                    CountText(value: seller.salesLast30)
                }
                .width(min: 33, ideal: 60)
                .customizationID("sales-30d")
                TableColumn("Royalty 30d", value: \.royaltyLast30) { seller in
                    MoneyText(value: seller.royaltyLast30, currency: currency,
                              color: Theme.Colors.positive)
                }
                .width(min: 44, ideal: 80)
                .customizationID("royalty-30d")
            }
            .copyableRows(rows, primaryLabel: "ASIN",
                          primary: { $0.asin },
                          row: { "\($0.asin)\t\($0.title ?? "")\t\($0.productType ?? "")\t\($0.salesLast30)\t\($0.royaltyLast30)" })
            .frame(minHeight: 150)
            .background(Theme.Colors.surface)
        }
    }

    private func requestRefresh() {
        let intent = appState.marketIntent(
            title: "Rebuild demand feed",
            arguments: ["demandfeed", "--refresh"])
        switch appState.actionCoordinator.requirement(
            for: intent, context: appState.actionPolicyContext) {
        case .confirmation:
            pendingRefresh = intent
        case .blocked(.killActive(let scope)):
            actionError = ActionCoordinatorError.killActive(scope).localizedDescription
        case .preview, .ready:
            Task { await rebuild(intent) }
        }
    }

    private func rebuild(_ intent: ActionIntent, confirmed: Bool = false) async {
        refreshing = true
        actionError = nil
        defer { refreshing = false }
        do {
            let receipt = try await appState.actionCoordinator.execute(
                intent, context: appState.actionPolicyContext, confirmed: confirmed)
            guard !receipt.rehearsed else {
                actionError = "Rehearsal mode blocked demandfeed --refresh."
                return
            }
            guard intent.scope.market == appState.selectedMarket else { return }
            await load()
        } catch {
            actionError = error.localizedDescription
        }
    }

    private func load() async {
        isLoading = true
        defer { if !Task.isCancelled { isLoading = false } }
        loadError = nil
        // Drop the previous market's rows BEFORE fetching: currency flips with the
        // market picker, so keeping them would render old money in the new symbol.
        feed = nil
        seedSelection.removeAll()
        provenSelection.removeAll()
        do {
            let bridge = try appState.makeBridge()
            let response = try await bridge.call(DemandFeedResponse.self, ["demandfeed"],
                                                 market: appState.selectedMarket)
            guard !Task.isCancelled else { return }
            feed = response
        } catch {
            guard !Task.isCancelled else { return }
            feed = nil
            loadError = error.localizedDescription
        }
    }
}

#Preview {
    DemandFeedView()
        .environment(AppState())
}
