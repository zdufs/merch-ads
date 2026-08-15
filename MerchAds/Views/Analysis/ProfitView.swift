import SwiftUI

/// Royalty-aware TRUE profit (trailing-30): units × per-unit royalty − ad spend.
/// ACOS measures efficiency; this measures dollars. The suggestion column uses
/// the same economics phase3 bids with: royalty ROI > 1.5 = room to bid up,
/// < 1 = ads cost more than the royalty they earn.
struct ProfitView: View {
    @Environment(AppState.self) private var appState

    @State private var profit: ProfitResponse?
    @State private var loadError: String?
    @State private var isLoading = false
    private static let sortFields: [String: KeyPathComparator<ProfitDesign>] = [
        "asin": .init(\.asinValue), "type": .init(\.typeValue),
        "orders": .init(\.orders), "spend": .init(\.spend),
        "royalty": .init(\.royaltyEst), "profit": .init(\.profit),
        "roi": .init(\.royaltyRoiValue),
    ]
    private static let typeSortFields: [String: KeyPathComparator<ProfitTypeRow>] = [
        "type": .init(\.type), "designs": .init(\.designs), "profitable": .init(\.profitable),
        "orders": .init(\.orders), "spend": .init(\.spend),
        "royalty": .init(\.royaltyEst), "profit": .init(\.profit),
    ]

    @State private var designSort = SortPrefs.load(
        TableID.profitDesigns, fields: sortFields,
        fallback: [KeyPathComparator(\ProfitDesign.profit)])
    @State private var typeSort = SortPrefs.load(
        TableID.profitTypes, fields: typeSortFields,
        fallback: [KeyPathComparator(\ProfitTypeRow.profit, order: .reverse)])
    @State private var typeColPrefs: TableColumnCustomization<ProfitTypeRow> = ColumnPrefs.load(TableID.profitTypes)
    @State private var designColPrefs: TableColumnCustomization<ProfitDesign> = ColumnPrefs.load(TableID.profitDesigns)
    @State private var typeSelection = Set<ProfitTypeRow.ID>()
    @State private var designSelection = Set<ProfitDesign.ID>()

    private var currency: String? { appState.currentMarket?.currency }

    private var designs: [ProfitDesign] {
        (profit?.designs ?? []).sorted(using: designSort)
    }

    private var profitHasData: Bool {
        guard let profit else { return false }
        if profit.empty == true { return false }
        return !(profit.types ?? []).isEmpty || !(profit.designs ?? []).isEmpty
    }

    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "Profit", subtitle: navigationSubtitle, help: .profit)
            statusBand
            Divider()
            // Tables are UNCONDITIONAL siblings; loading / error / empty are drawn as
            // an overlay LAYER over them (same fix as CrossPurchaseView / DemandFeed).
            // Toggling a greedy Table's presence via if/else blanks the whole detail
            // into empty placeholder rows on macOS 26. The overlay is scoped to the
            // content region so the header and status band stay visible while loading.
            VStack(spacing: 0) {
                // types is ~6 fixed rows — give it exactly that height and let the
                // 500-row designs table take the rest (no dead striped rows).
                exportRow(profit)
                VSplitView {
                    typesTable(profit?.types ?? [])
                    designsTable
                }
            }
            .overlay { stateOverlay }
        }
        .background(Theme.Colors.canvas)
        .task(id: appState.viewKey) { await load() }
        .onChange(of: designSort) { SortPrefs.save(TableID.profitDesigns, designSort, fields: Self.sortFields) }
        .onChange(of: typeColPrefs) { ColumnPrefs.save(TableID.profitTypes, typeColPrefs) }
        .onChange(of: designColPrefs) { ColumnPrefs.save(TableID.profitDesigns, designColPrefs) }
    }

    private var navigationSubtitle: String {
        if let asOf = profit?.asOf { return "\(appState.selectedMarket) · data through \(Format.euDate(asOf))" }
        return "\(appState.selectedMarket) · trailing 30 days"
    }

    // Loading / error / "no data", drawn OVER the always-present tables. A
    // conditional sibling of the greedy tables blanks the detail on macOS 26.
    @ViewBuilder
    private var stateOverlay: some View {
        if let loadError {
            overlayMessage(title: "Profit unavailable", detail: loadError,
                           retry: { Task { await load() } })
        } else if profit == nil && isLoading {
            ProgressView("Computing true margins…")
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Theme.Colors.canvas)
        } else if profit != nil && !profitHasData {
            overlayMessage(
                title: "No royalty data banked",
                detail: "No royalty data is banked for \(appState.selectedMarket); profit cannot be stated honestly yet.")
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
        let missingCaption = profit != nil && !profitHasData
            ? "no royalty data banked for this market" : nil
        let totalProfit = profitHasData ? profit?.totalProfit : nil
        let profitTint: Color = totalProfit.map {
            $0 >= 0 ? Theme.Colors.positive : Theme.Colors.critical
        } ?? .primary
        return Group {
            HStack(spacing: Layout.Spacing.sm) {
                StatCard(title: "True profit",
                         value: totalProfit.map { Format.money($0, currency: currency) } ?? "—",
                         tint: profitTint, symbol: "dollarsign.circle.fill",
                         subtitle: missingCaption ?? "royalty-aware · trailing 30d")
                    .mdCard()
                StatCard(title: "Royalty estimate",
                         value: profitHasData
                            ? Format.money(profit?.totalRoyaltyEst, currency: currency) : "—",
                         symbol: "banknote.fill", subtitle: missingCaption)
                    .mdCard()
                StatCard(title: "Ad spend",
                         value: profitHasData
                            ? Format.money(profit?.totalSpend, currency: currency) : "—",
                         symbol: "creditcard.fill",
                         subtitle: missingCaption ?? "covered spend · cohort spend excluded")
                    .mdCard()
                StatCard(title: "Coverage",
                         value: profitHasData
                            ? Format.percent(profit?.coveragePct) : "—",
                         symbol: "chart.pie.fill",
                         subtitle: missingCaption ?? "of spend assignable to one design")
                    .mdCard()
            }
        }
        .padding(.horizontal, Layout.Spacing.lg)
        .padding(.vertical, Layout.Spacing.md)
    }

    /// The cohort share cannot be assigned to one design, so it is excluded from
    /// every profit figure above — say so where the reader is judging the totals.
    private var cohortCaption: String {
        var caption = "Ads-attributed only · organic halo is not counted"
        if let cohort = profit?.unattributedCohortSpend, cohort > 0 {
            let groups = profit?.unattributedCohortGroups ?? 0
            caption += " · \(Format.money(cohort, currency: currency)) cohort spend"
                + " (\(groups) multi-ASIN group\(groups == 1 ? "" : "s")) is excluded"
        }
        return caption
    }

    private func exportRow(_ profit: ProfitResponse?) -> some View {
        HStack(spacing: Layout.Spacing.sm) {
            Text(cohortCaption)
                .font(.caption)
                .foregroundStyle(.secondary)
            Spacer()
            if let listed = profit?.designs, !listed.isEmpty {
                ExportButton(filename: "profit-\(appState.selectedMarket)") {
                    CSVDocument(
                        headers: ["asin", "type", "orders", "spend", "sales",
                                  "royalty_est", "profit", "royalty_roi"],
                        rows: listed.map { design in
                            [design.asin ?? "", design.type ?? "", String(design.orders),
                             String(design.spend), String(design.sales),
                             String(design.royaltyEst), String(design.profit),
                             design.royaltyRoi.map { String($0) } ?? ""]
                        })
                }
            }
        }
        .controlSize(.small)
        .padding(.horizontal, Layout.Spacing.sm)
        .padding(.vertical, Layout.Spacing.xs)
        .background(Theme.Colors.surface)
    }

    private func typesTable(_ types: [ProfitTypeRow]) -> some View {
        VStack(spacing: 0) {
            SectionHeader(title: "By product type", subtitle: "royalty-aware economics",
                          count: types.count)
                .padding(.horizontal, Layout.Spacing.sm)
            let typeRows = types.sorted(using: typeSort)
            Table(typeRows, selection: $typeSelection, sortOrder: $typeSort.descendingFirst(),
                  columnCustomization: $typeColPrefs) {
                TableColumn("Type", value: \.type) { row in
                    Text(row.type)
                }
                .width(min: 140, ideal: 200)
                .customizationID("type")
                TableColumn("Designs", value: \.designs) { row in
                    CountText(value: row.designs)
                }
                .width(min: 66, ideal: 120)
                .customizationID("designs")
                TableColumn("Profitable", value: \.profitable) { row in
                    CountText(value: row.profitable)
                }
                .width(min: 44, ideal: 80)
                .customizationID("profitable")
                TableColumn("Orders", value: \.orders) { row in
                    CountText(value: row.orders)
                }
                .width(min: 30, ideal: 55)
                .customizationID("orders")
                TableColumn("Spend", value: \.spend) { row in
                    MoneyText(value: row.spend, currency: currency)
                }
                .width(min: 44, ideal: 80)
                .customizationID("spend")
                TableColumn("Royalty", value: \.royaltyEst) { row in
                    MoneyText(value: row.royaltyEst, currency: currency)
                }
                .width(min: 44, ideal: 80)
                .customizationID("royalty")
                TableColumn("Profit", value: \.profit) { row in
                    MoneyText(value: row.profit, currency: currency,
                              color: row.profit >= 0
                                ? Theme.Colors.positive : Theme.Colors.critical)
                        .fontWeight(.medium)
                }
                .width(min: 44, ideal: 80)
                .customizationID("profit")
            }
            .onChange(of: typeSort) { SortPrefs.save(TableID.profitTypes, typeSort, fields: Self.typeSortFields) }
            .copyableRows(typeRows, primaryLabel: "Type",
                          primary: { $0.type },
                          row: { "\($0.type)\t\($0.profitable)/\($0.designs)\t\($0.orders)\t\($0.spend)\t\($0.royaltyEst)\t\($0.profit)" })
            .contentSizedTable(rows: types.count)
            .background(Theme.Colors.surface)
        }
    }

    private var designsTable: some View {
        VStack(spacing: 0) {
            SectionHeader(title: "Worst and best designs",
                          subtitle: "profit extremes returned by the engine",
                          count: designs.count)
                .padding(.horizontal, Layout.Spacing.sm)
            Table(designs, selection: $designSelection, sortOrder: $designSort.descendingFirst(), columnCustomization: $designColPrefs) {
                TableColumn("ASIN", value: \.asinValue) { design in
                    AsinLink(asin: design.asin ?? design.adGroupId, prominent: true)
                }
                .width(min: 66, ideal: 120)
                .customizationID("asin")
                TableColumn("Type", value: \.typeValue) { design in
                    StatusBadge.campaignType(design.type)
                }
                .width(min: 77, ideal: 140)
                .customizationID("type")
                TableColumn("Orders", value: \.orders) { design in
                    CountText(value: design.orders)
                }
                .width(min: 27, ideal: 50)
                .customizationID("orders")
                TableColumn("Spend", value: \.spend) { design in
                    MoneyText(value: design.spend, currency: currency)
                }
                .width(min: 38, ideal: 70)
                .customizationID("spend")
                TableColumn("Royalty", value: \.royaltyEst) { design in
                    MoneyText(value: design.royaltyEst, currency: currency)
                }
                .width(min: 38, ideal: 70)
                .customizationID("royalty")
                TableColumn("Profit", value: \.profit) { design in
                    MoneyText(value: design.profit, currency: currency,
                              color: design.profit >= 0
                                ? Theme.Colors.positive : Theme.Colors.critical)
                        .fontWeight(.medium)
                }
                .width(min: 38, ideal: 70)
                .customizationID("profit")
                TableColumn("ROI", value: \.royaltyRoiValue) { design in
                    Text(design.royaltyRoi.map { String(format: "%.1f×", $0) } ?? "—")
                        .font(Typography.tableNumeral)
                        .foregroundStyle(royaltyROIColor(design.royaltyRoi))
                }
                .width(min: 27, ideal: 50)
                .customizationID("roi")
                TableColumn("Suggests", value: \.royaltyRoiValue) { design in
                    Text(suggestion(design))
                        .font(.caption)
                        .foregroundStyle(suggestionColor(design))
                        .help("From royalty ROI: ≥1.5× royalty per ad dollar = room to bid up · <1× = ads cost more than they earn · 'free sales' = orders with no ad spend")
                }
                .width(min: 38, ideal: 70)
                .customizationID("suggests")
            }
            .copyableRows(designs, primaryLabel: "ASIN",
                          primary: { $0.asin ?? $0.adGroupId },
                          row: { "\($0.asin ?? "")\t\($0.type ?? "")\t\($0.orders)\t\($0.spend)\t\($0.royaltyEst)\t\($0.profit)\t\($0.royaltyRoi.map { String($0) } ?? "")" })
            .frame(minHeight: 160)
            .background(Theme.Colors.surface)
        }
    }

    private func royaltyROIColor(_ roi: Double?) -> Color {
        guard let roi else { return Theme.Colors.muted }
        return roi >= 1 ? Theme.Colors.positive : Theme.Colors.critical
    }

    private func suggestion(_ design: ProfitDesign) -> String {
        guard let roi = design.royaltyRoi else { return design.orders > 0 ? "free sales" : "—" }
        if roi >= 1.5 { return "bid up" }
        if roi < 1.0 { return "bid down" }
        return "hold"
    }

    private func suggestionColor(_ design: ProfitDesign) -> Color {
        guard let roi = design.royaltyRoi else { return Theme.Colors.muted }
        if roi >= 1.5 { return Theme.Colors.positive }
        if roi < 1.0 { return Theme.Colors.critical }
        return Theme.Colors.muted
    }

    private func load() async {
        isLoading = true
        defer { if !Task.isCancelled { isLoading = false } }
        loadError = nil
        // Drop the previous market's rows BEFORE fetching: currency flips with the
        // market picker, so keeping them would render old money in the new symbol.
        profit = nil
        typeSelection.removeAll()
        designSelection.removeAll()
        do {
            let bridge = try appState.makeBridge()
            let response = try await bridge.call(ProfitResponse.self, ["profit"],
                                                 market: appState.selectedMarket)
            guard !Task.isCancelled else { return }
            profit = response
        } catch {
            guard !Task.isCancelled else { return }
            profit = nil
            loadError = error.localizedDescription
        }
    }
}

#Preview {
    ProfitView()
        .environment(AppState())
}
