import SwiftUI
import Charts

/// The all-markets rollup: every market's trailing-30 headline side by side,
/// with subtotals per currency (no FX guessing — money stays in its currency).
struct AllMarketsView: View {
    @Environment(AppState.self) private var appState

    @State private var overview: OverviewResponse?
    @State private var loadError: String?
    @State private var isLoading = false
    @State private var colPrefs: TableColumnCustomization<OverviewMarket> = ColumnPrefs.load(TableID.allMarkets)
    @State private var hoveredMarket: String?
    @State private var selection = Set<OverviewMarket.ID>()
    @State private var sortOrder = SortPrefs.load(
        TableID.allMarkets, fields: sortFields,
        fallback: [KeyPathComparator(\OverviewMarket.spend, order: .reverse)])
    private static let sortFields: [String: KeyPathComparator<OverviewMarket>] = [
        "market": .init(\.market), "spend": .init(\.spend),
        "sales": .init(\.sales), "orders": .init(\.orders),
    ]

    private var rows: [OverviewMarket] { overview?.markets ?? [] }

    private var sortedRows: [OverviewMarket] { rows.sorted(using: sortOrder) }

    private var currencyTotals: [(currency: String, spend: Double, sales: Double)] {
        var totals: [String: (Double, Double)] = [:]
        for row in rows {
            let key = row.currency ?? "?"
            let current = totals[key] ?? (0, 0)
            totals[key] = (current.0 + row.spend, current.1 + row.sales)
        }
        return totals.map { ($0.key, $0.value.0, $0.value.1) }
            .sorted { $0.spend > $1.spend }
    }

    private var totalOrders: Int { rows.reduce(0) { $0 + $1.orders } }

    private var rolledUpAcos: Double? {
        let sales = rows.reduce(0) { $0 + $1.sales }
        guard sales > 0 else { return nil }
        return rows.reduce(0) { $0 + $1.spend } / sales
    }

    private var moneySummarySpend: String {
        currencyTotals.map { Format.money($0.spend, currency: $0.currency) }
            .joined(separator: " · ")
    }

    private var moneySummarySales: String {
        currencyTotals.map { Format.money($0.sales, currency: $0.currency) }
            .joined(separator: " · ")
    }

    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "All Markets", subtitle: navigationSubtitle, help: .allMarkets)
            LoadableView(
                isLoading: isLoading && overview == nil,
                error: loadError,
                isEmpty: overview != nil && rows.isEmpty,
                loadingTitle: "Rolling up all markets…",
                emptyTitle: "No market data",
                emptyDescription: "No markets have a trailing-30 snapshot yet.",
                systemImage: "globe",
                retry: { Task { await load() } }
            ) {
                content
            }
        }
        .background(Theme.Colors.canvas)
        // Reload when the data refreshes OR the profile family flips (Merch↔KDP),
        // since the rollup is now scoped to the selected kind.
        .task(id: "\(appState.currentMarket?.isKDP == true ? "kdp" : "merch")#\(appState.dataStamp)") { await load() }
        .onChange(of: colPrefs) { ColumnPrefs.save(TableID.allMarkets, colPrefs) }
        .onChange(of: sortOrder) { SortPrefs.save(TableID.allMarkets, sortOrder, fields: Self.sortFields) }
    }

    private var navigationSubtitle: String {
        let newest = rows.compactMap(\.asOf).max()
        return newest.map { "trailing 30 days · data through \(Format.euDate($0))" }
            ?? "trailing 30 days"
    }

    /// Six markets = six rows; an uncapped Table would grab ALL remaining height
    /// and shove the chart to the very bottom of the window behind a wall of
    /// empty striped rows. Cap it to its content instead.
    ///
    /// The cap scales with the text size — at larger accessibility sizes the rows
    /// grow, so the estimate is a ScaledMetric, not a hardcoded number.
    ///
    /// The estimate must be a little GENEROUS. macOS renders these body-text rows
    /// taller than the old 28pt guess, so the cap came out shorter than the real
    /// content and the Table scrolled inside it — a pointless vertical scrollbar
    /// for six rows. A slightly tall cap leaves a thin blank strip below the last
    /// row instead, which is invisible and never a scrollbar. The Table's own
    /// vertical scrolling is turned off as well (see `marketsTable`), so a rounding
    /// pixel can never bring the scroller back.
    @ScaledMetric(relativeTo: .body) private var tableRowHeight: CGFloat = 34
    @ScaledMetric(relativeTo: .body) private var tableHeaderHeight: CGFloat = 40

    private var tableHeight: CGFloat {
        CGFloat(rows.count) * tableRowHeight + tableHeaderHeight
    }

    private var content: some View {
        VStack(alignment: .leading, spacing: 0) {
            statusBand
            Divider()
            ScrollView {
                LazyVStack(alignment: .leading, spacing: Layout.Spacing.lg) {
                    VStack(alignment: .leading, spacing: Layout.Spacing.xs) {
                        SectionHeader(title: "Market performance",
                                      subtitle: "local currencies · trailing 30 days",
                                      count: rows.count)
                        marketsTable
                    }

                    if rows.count > 1 { comparisonChart }
                }
                .padding(Layout.Spacing.lg)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .scrollEdgeEffectStyle(.soft, for: .top)
        }
    }

    private var statusBand: some View {
        Group {
            HStack(spacing: Layout.Spacing.sm) {
                StatCard(title: "Spend", value: moneySummarySpend,
                         symbol: "creditcard.fill", subtitle: "grouped by currency")
                    .mdCard()
                StatCard(title: "Attributed sales", value: moneySummarySales,
                         symbol: "chart.line.uptrend.xyaxis", subtitle: "grouped by currency")
                    .mdCard()
                StatCard(title: "ACOS", value: Format.percent(rolledUpAcos),
                         tint: AcosTier.select(acos: rolledUpAcos).color,
                         symbol: "percent", subtitle: "neutral local-currency rollup")
                    .mdCard()
                StatCard(title: "Orders", value: Format.count(totalOrders),
                         symbol: "shippingbox.fill", subtitle: "all markets · trailing 30d")
                    .mdCard()
            }
        }
        .padding(.horizontal, Layout.Spacing.lg)
        .padding(.vertical, Layout.Spacing.md)
    }

    private var marketsTable: some View {
        Table(sortedRows, selection: $selection, sortOrder: $sortOrder.descendingFirst(), columnCustomization: $colPrefs) {
                TableColumn("Market", value: \.market) { row in
                    HStack(spacing: Layout.Spacing.xs) {
                        Text(row.market).fontWeight(.semibold)
                        if row.market == appState.selectedMarket {
                            Image(systemName: "checkmark.circle.fill")
                                .foregroundStyle(.tint)
                                .accessibilityLabel("Selected market")
                        }
                    }
                }
                .width(min: 38, ideal: 70)
                .customizationID("market")
                TableColumn("Spend", value: \.spend) { row in
                    MoneyText(value: row.spend, currency: row.currency)
                }
                .width(min: 49, ideal: 90)
                .customizationID("spend")
                TableColumn("Sales", value: \.sales) { row in
                    MoneyText(value: row.sales, currency: row.currency)
                }
                .width(min: 55, ideal: 100)
                .customizationID("sales")
                TableColumn("ACOS", value: \.acosValue) { row in
                    PercentText(value: row.acos, label: "ACOS")
                }
                .width(min: 33, ideal: 60)
                .customizationID("acos")
                TableColumn("CVR", value: \.cvrValue) { row in
                    PercentText(value: row.cvr, label: "CVR", color: .primary)
                }
                .width(min: 30, ideal: 55)
                .customizationID("cvr")
                TableColumn("Orders", value: \.orders) { row in
                    CountText(value: row.orders)
                }
                .width(min: 33, ideal: 60)
                .customizationID("orders")
                TableColumn("YTD spend", value: \.ytdSpendValue) { row in
                    MoneyText(value: row.ytdSpend, currency: row.currency)
                        .help(row.ytdBasis ?? "Year-to-date")
                }
                .width(min: 46, ideal: 85)
                .customizationID("ytd-spend")
                TableColumn("YTD sales", value: \.ytdSalesValue) { row in
                    MoneyText(value: row.ytdSales, currency: row.currency)
                }
                .width(min: 49, ideal: 90)
                .customizationID("ytd-sales")
                TableColumn("Data through", value: \.asOfValue) { row in
                    Text(row.asOf.map(Format.euDate) ?? "—").foregroundStyle(.secondary)
                }
                .width(min: 49, ideal: 90)
                .customizationID("data-through")
        }
            .contextMenu(forSelectionType: OverviewMarket.ID.self) { ids in
                let selected = rows.filter { ids.contains($0.id) }
                copyMenuItems(selected, primaryLabel: "Market",
                              primary: { $0.market },
                              row: { "\($0.market)\t\($0.spend)\t\($0.sales)\t\($0.orders)" })
                Divider()
                if let id = ids.first {
                    Button("Switch to \(id)") { appState.selectedMarket = id }
                }
            } primaryAction: { ids in
                if let id = ids.first { appState.selectedMarket = id }
            }
            .frame(height: tableHeight)
            .scrollDisabled(true)
            .background(Theme.Colors.surface)
    }

    /// Every market used to share ONE axis, so 970 US$ stood next to 24 £ as if
    /// the bars were comparable. They are not. The chart is split by currency
    /// instead: each group carries its own labelled scale, which keeps the one
    /// genuinely comparable set — the four euro markets — side by side, and stops
    /// pretending a dollar bar and a pound bar mean the same length.
    private var comparisonChart: some View {
        let groups = currencyGroups
        return VStack(alignment: .leading, spacing: Layout.Spacing.md) {
            SectionHeader(title: "Spend and attributed sales",
                          subtitle: "grouped by currency · one scale per group, never mixed")
            HStack {
                if let hovered = rows.first(where: { $0.market == hoveredMarket }) {
                    Text("\(hovered.market): \(Format.money(hovered.spend, currency: hovered.currency)) spend · \(Format.money(hovered.sales, currency: hovered.currency)) sales · \(Format.percent(hovered.acos)) ACOS · \(Format.count(hovered.orders)) orders")
                        .font(.caption)
                        .monospacedDigit()
                } else {
                    Text("hover a bar for exact values")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                Spacer()
            }
            .padding(.horizontal, Layout.Spacing.sm)
            ForEach(groups) { group in
                // one legend for the whole section — the series colours are shared
                currencyChart(group,
                              showsLegend: group.id == groups.first?.id,
                              height: groups.count > 1 ? Layout.ChartHeight.compact
                                                       : Layout.ChartHeight.standard)
            }
        }
    }

    private func currencyChart(_ group: CurrencyGroup, showsLegend: Bool,
                               height: CGFloat) -> some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.xxs) {
            Text("\(group.currency) · \(group.markets.map(\.market).joined(separator: ", "))")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            Chart(moneyPoints(group.markets)) { point in
                BarMark(
                    x: .value("Market", point.market),
                    y: .value("Amount", point.value),
                    width: .ratio(0.32)
                )
                .position(by: .value("Series", point.series))
                .foregroundStyle(by: .value("Series", point.series))
                .clipShape(.rect(cornerRadius: Layout.Radius.small))
                .opacity(hoveredMarket == nil || hoveredMarket == point.market ? 1 : 0.4)
            }
            .chartForegroundStyleScale(["Sales": Theme.Colors.chartSales,
                                        "Spend": Theme.Colors.chartSpend])
            .chartLegend(showsLegend ? .visible : .hidden)
            .chartYAxis {
                AxisMarks { value in
                    AxisGridLine()
                    AxisValueLabel {
                        if let amount = value.as(Double.self) {
                            Text(axisAmount(amount, currency: group.currency))
                        }
                    }
                }
            }
            .chartOverlay { proxy in
                GeometryReader { geo in
                    Rectangle()
                        .fill(Color.clear)
                        .contentShape(Rectangle())
                        .onContinuousHover { phase in
                            switch phase {
                            case .active(let location):
                                if let frame = proxy.plotFrame {
                                    let x = location.x - geo[frame].origin.x
                                    hoveredMarket = proxy.value(atX: x)
                                }
                            case .ended:
                                hoveredMarket = nil
                            }
                        }
                }
            }
            .merchAdsChartStyle(height: height)
            .accessibilityLabel("Spend and attributed sales in \(group.currency)")
        }
        .padding(.horizontal, Layout.Spacing.sm)
    }

    private func axisAmount(_ amount: Double, currency: String) -> String {
        guard currency.count == 3 else { return Format.count(Int(amount)) }
        return amount.formatted(.currency(code: currency).precision(.fractionLength(0)))
    }

    private struct CurrencyGroup: Identifiable {
        let currency: String
        let markets: [OverviewMarket]
        var id: String { currency }
    }

    /// Biggest-spending currency first; markets inside a group keep the table's order.
    private var currencyGroups: [CurrencyGroup] {
        Dictionary(grouping: sortedRows) { $0.currency ?? "?" }
            .map { CurrencyGroup(currency: $0.key, markets: $0.value) }
            .sorted { lhs, rhs in
                let lhsSpend = lhs.markets.reduce(0) { $0 + $1.spend }
                let rhsSpend = rhs.markets.reduce(0) { $0 + $1.spend }
                return lhsSpend == rhsSpend ? lhs.currency < rhs.currency : lhsSpend > rhsSpend
            }
    }

    private struct MoneyPoint: Identifiable {
        let series: String
        let market: String
        let value: Double
        var id: String { "\(series)|\(market)" }
    }

    private func moneyPoints(_ markets: [OverviewMarket]) -> [MoneyPoint] {
        markets.flatMap { row in
            [MoneyPoint(series: "Sales", market: row.market, value: row.sales),
             MoneyPoint(series: "Spend", market: row.market, value: row.spend)]
        }
    }

    private func load() async {
        isLoading = true
        defer { if !Task.isCancelled { isLoading = false } }
        loadError = nil
        do {
            // KDP is a separate advertiser profile, so its rollup must never mix
            // with Merch. Scope overview to the selected profile's family.
            let kind = appState.currentMarket?.isKDP == true ? "kdp" : "merch"
            let response = try await appState.makeBridge()
                .call(OverviewResponse.self, ["overview", "--kind", kind])
            guard !Task.isCancelled else { return }
            overview = response
        } catch {
            guard !Task.isCancelled else { return }
            overview = nil
            loadError = error.localizedDescription
        }
    }
}

#Preview {
    AllMarketsView()
        .environment(AppState())
}
