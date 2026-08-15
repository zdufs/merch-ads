import SwiftUI
import Charts

/// The weekly bid-change report: what moved up, down, and by how much —
/// the Monday review at a glance, exportable to CSV.
struct BidReportView: View {
    @Environment(AppState.self) private var appState

    @State private var report: BidReportResponse?
    @State private var loadError: String?
    @State private var isLoading = false
    @AppStorage("bidReport.days") private var days = 7
    @State private var colPrefs: TableColumnCustomization<BidReportChange> = ColumnPrefs.load(TableID.bidReport)
    @State private var filterText = ""
    @State private var selection = Set<BidReportChange.ID>()

    private static let sortFields: [String: KeyPathComparator<BidReportChange>] = [
        "when": .init(\.at), "delta": .init(\.deltaValue),
    ]

    @State private var sortOrder = SortPrefs.load(
        TableID.bidReport, fields: sortFields,
        fallback: [KeyPathComparator(\BidReportChange.at, order: .reverse)])

    private func visibleChanges(_ report: BidReportResponse) -> [BidReportChange] {
        report.changes
            .filter {
                filterText.isEmpty
                    || ($0.asin ?? "").localizedStandardContains(filterText)
                    || ($0.reason ?? "").localizedStandardContains(filterText)
                    || $0.targetId.localizedStandardContains(filterText)
            }
            .sorted(using: sortOrder)
    }

    private var currency: String? { appState.currentMarket?.currency }

    private var dayWindowPosition: Binding<Double> {
        Binding(
            get: {
                switch days {
                case 14: 1
                case 30: 2
                default: 0
                }
            },
            set: { position in
                days = [7, 14, 30][min(2, max(0, Int(position.rounded())))]
            })
    }

    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "Bid Report", subtitle: "\(appState.selectedMarket) · last \(report?.days ?? days) days", help: .bidReport)
            statusBand
            filterBar
            Divider()
            LoadableView(
                isLoading: isLoading && report == nil,
                error: loadError,
                isEmpty: report?.changes.isEmpty == true,
                loadingTitle: "Reading writes_log…",
                emptyTitle: "No bid changes",
                emptyDescription: "Nothing moved in \(appState.selectedMarket) in the last \(report?.days ?? days) days.",
                systemImage: "chart.line.uptrend.xyaxis",
                retry: { Task { await load() } }
            ) {
                if let report { reportContent(report) }
            }
        }
        .background(Theme.Colors.canvas)
        .task(id: appState.viewKey) { await load() }
        .onChange(of: colPrefs) { ColumnPrefs.save(TableID.bidReport, colPrefs) }
        .onChange(of: sortOrder) { SortPrefs.save(TableID.bidReport, sortOrder, fields: Self.sortFields) }
    }

    private var statusBand: some View {
        Group {
            HStack(spacing: Layout.Spacing.sm) {
                StatCard(title: "Bid increases", value: report.map { Format.count($0.ups) } ?? "—",
                         tint: Theme.Colors.positive, symbol: "arrow.up.right")
                    .mdCard()
                StatCard(title: "Bid decreases", value: report.map { Format.count($0.downs) } ?? "—",
                         tint: Theme.Colors.critical, symbol: "arrow.down.right")
                    .mdCard()
                StatCard(title: "Net delta",
                         value: report.map { Format.money($0.netDelta, currency: currency) } ?? "—",
                         tint: netDeltaTint, symbol: "plusminus",
                         subtitle: "negative means bids came down overall")
                    .mdCard()
            }
        }
        .padding(.horizontal, Layout.Spacing.lg)
        .padding(.vertical, Layout.Spacing.md)
    }

    private var netDeltaTint: Color {
        guard let report else { return .primary }
        return report.netDelta <= 0 ? Theme.Colors.positive : Theme.Colors.caution
    }

    private var filterBar: some View {
        FilterBar {
            HStack(spacing: Layout.Spacing.xs) {
                Text("Window")
                Slider(value: dayWindowPosition, in: 0...2, step: 1)
                    .frame(maxWidth: 180)
                    .accessibilityValue("\(days) days")
                Text("\(days) days")
                    .font(.caption.monospacedDigit())
                    .frame(minWidth: 48, alignment: .trailing)
            }
            .onChange(of: days) { Task { await load() } }
            .help("How far back to look in the bid-change log")
            TextField("Filter by ASIN or reason", text: $filterText)
                .textFieldStyle(.roundedBorder)
                .frame(maxWidth: 260)
        } trailing: {
            if let report {
                ExportButton(filename: "bid-report-\(appState.selectedMarket)-\(report.days)d") {
                    CSVDocument(
                        headers: ["at", "asin", "targeting", "target_id", "old", "new", "delta", "reason"],
                        rows: report.changes.map { change in
                            [change.at, change.asin ?? "", change.targeting ?? "",
                             change.targetId,
                             change.old.map { String($0) } ?? "",
                             change.new.map { String($0) } ?? "",
                             change.delta.map { String($0) } ?? "",
                             change.reason ?? ""]
                        })
                }
            }
        }
    }

    private func reportContent(_ report: BidReportResponse) -> some View {
        let visible = visibleChanges(report)   // filter+sort once per body eval
        return VSplitView {
            ruleChart(report)
            VStack(spacing: 0) {
                SectionHeader(title: "Bid changes",
                              subtitle: "writes log · newest first",
                              count: visible.count)
                    .padding(.horizontal, Layout.Spacing.sm)
                table(visible)
            }
        }
        .frame(maxHeight: .infinity, alignment: .top)
    }

    private func ruleChart(_ report: BidReportResponse) -> some View {
        let distribution = ruleDistribution(report)
        return VStack(alignment: .leading, spacing: Layout.Spacing.xs) {
            SectionHeader(title: "Rule distribution",
                          subtitle: "changes grouped by logged reason",
                          count: distribution.count)
                .padding(.horizontal, Layout.Spacing.sm)
            Chart(distribution) { item in
                BarMark(
                    x: .value("Changes", item.count),
                    y: .value("Rule", item.rule)
                )
                .foregroundStyle(Theme.Colors.information)
                .clipShape(.rect(cornerRadius: Layout.Radius.small))
            }
            .merchAdsChartStyle(height: Layout.ChartHeight.compact)
            .padding(.horizontal, Layout.Spacing.sm)
            .padding(.bottom, Layout.Spacing.sm)
        }
        .background(Theme.Colors.surface)
    }

    private func table(_ rows: [BidReportChange]) -> some View {
        Table(rows, selection: $selection, sortOrder: $sortOrder.descendingFirst(), columnCustomization: $colPrefs) {
            TableColumn("When", value: \.at) { change in
                Text(Format.euDateTime(change.at))
                    .font(.caption.monospaced())
            }
            .width(min: 60, ideal: 110)
            .customizationID("when")
            TableColumn("ASIN", value: \.asinValue) { change in
                Text(change.asin ?? "—").font(.body.monospaced())
            }
            .width(min: 55, ideal: 100)
            .customizationID("asin")
            TableColumn("Targeting", value: \.targetingValue) { change in
                Text(change.targeting ?? "—").foregroundStyle(.secondary)
            }
            .width(min: 49, ideal: 90)
            .customizationID("targeting")
            TableColumn("Change", value: \.newValue) { change in
                HStack(spacing: Layout.Spacing.xxs) {
                    MoneyText(value: change.old, currency: currency)
                    Image(systemName: "arrow.right")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .accessibilityHidden(true)   // decorative separator
                    MoneyText(value: change.new, currency: currency)
                        .fontWeight(.medium)
                }
            }
            .width(min: 71, ideal: 130)
            .customizationID("change")
            TableColumn("Δ", value: \.deltaValue) { change in
                MoneyText(value: change.delta, currency: currency,
                          color: (change.delta ?? 0) > 0 ? Theme.Colors.positive
                            : (change.delta ?? 0) < 0 ? Theme.Colors.critical : Theme.Colors.muted)
            }
            .width(min: 30, ideal: 55)
            .customizationID("delta")
            TableColumn("Why", value: \.reasonValue) { change in
                Text(change.reason ?? "—")
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .help(change.reason ?? "")
            }
            .customizationID("why")
        }
        .copyableRows(rows, primaryLabel: "Target",
                      primary: { $0.targeting ?? $0.targetId },
                      row: { "\($0.targeting ?? "")\t\($0.old.map { String($0) } ?? "")\t\($0.new.map { String($0) } ?? "")\t\($0.delta.map { String($0) } ?? "")\t\($0.reason ?? "")\t\($0.asin ?? "")" })
        .background(Theme.Colors.surface)
    }

    private struct RuleCount: Identifiable {
        let rule: String
        let count: Int
        var id: String { rule }
    }

    private func ruleDistribution(_ report: BidReportResponse) -> [RuleCount] {
        let grouped = Dictionary(grouping: report.changes) {
            let reason = $0.reason?.trimmingCharacters(in: .whitespacesAndNewlines)
            return reason?.isEmpty == false ? reason! : "Unspecified"
        }
        return grouped.map { RuleCount(rule: $0.key, count: $0.value.count) }
            .sorted { lhs, rhs in
                lhs.count == rhs.count ? lhs.rule < rhs.rule : lhs.count > rhs.count
            }
    }

    private func load() async {
        let requestedDays = days   // window may change mid-flight; only the matching response lands
        isLoading = true
        defer { if !Task.isCancelled { isLoading = false } }
        loadError = nil
        do {
            let bridge = try appState.makeBridge()
            let response = try await bridge.call(BidReportResponse.self,
                                                 ["bidreport", "--days", String(requestedDays)],
                                                 market: appState.selectedMarket)
            guard !Task.isCancelled, requestedDays == days else { return }
            report = response
        } catch {
            guard !Task.isCancelled, requestedDays == days else { return }
            report = nil
            loadError = error.localizedDescription
        }
    }
}

#Preview {
    BidReportView()
        .environment(AppState())
}
