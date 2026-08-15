import SwiftUI

/// Reports (MerchDash parity): an account rollup for any date range, built from
/// the true per-day daily_totals bank — totals + derived ratios, with a per-day
/// CSV export. Range is bounded by what's banked (shown under the pickers).
struct ReportsView: View {
    @Environment(AppState.self) private var appState

    @State private var report: ReportResponse?
    @State private var reportMarket: String?   // which market the numbers belong to
    @State private var startDate = Calendar.current.date(byAdding: .day, value: -29, to: Date()) ?? Date()
    @State private var endDate = Date()
    // Remembered across launches (and tab switches). Stored as reference time
    // intervals; 0 = never set, so the relative default above stands.
    @AppStorage("reports.startTI") private var startTI: Double = 0
    @AppStorage("reports.endTI") private var endTI: Double = 0
    @State private var isLoading = false
    @State private var loadError: String?
    @State private var daySel = Set<String>()

    private var currency: String? { appState.currentMarket?.currency }

    /// The pickers hand back dates in the operator's OWN calendar, and the engine
    /// wants calendar days rather than UTC instants — so serialize in the local
    /// time zone. Pinning this to UTC shifted "3 Aug" to "2026-08-02" for anyone
    /// east of Greenwich before their offset had elapsed.
    private static let iso: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd"
        f.calendar = Calendar.autoupdatingCurrent
        f.timeZone = TimeZone.autoupdatingCurrent
        f.locale = Locale(identifier: "en_US_POSIX")
        return f
    }()

    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "Reports", subtitle: appState.selectedMarket, help: .reports)
            FilterBar {
                DatePicker("From", selection: $startDate, displayedComponents: .date)
                    .fixedSize()
                DatePicker("To", selection: $endDate, displayedComponents: .date)
                    .fixedSize()
                Button("Generate") { Task { await load() } }
                    .buttonStyle(.borderedProminent)
                    .disabled(isLoading)
            } trailing: {
                if let a = report?.available, let lo = a.min, let hi = a.max {
                    Text("banked \(Format.euDate(lo)) → \(Format.euDate(hi))")
                        .font(.caption).foregroundStyle(.secondary)
                }
                if let r = report {
                    ExportButton(filename: "report-\(appState.selectedMarket)-\(r.start ?? "")-\(r.end ?? "")") {
                        CSVDocument(
                            headers: ["date", "spend", "sales", "orders",
                                      "impressions", "clicks", "units"],
                            rows: r.days.map { d in
                                [d.date, String(format: "%.2f", d.spend),
                                 String(format: "%.2f", d.sales), String(d.orders),
                                 d.impressions.map(String.init) ?? "",
                                 d.clicks.map(String.init) ?? "",
                                 d.units.map(String.init) ?? ""]
                            })
                    }
                }
            }
            Divider()
            content
        }
        .background(Theme.Colors.canvas)
        // Reload whenever the report on screen isn't this market's — otherwise the
        // header flips to DE while every number stays US, reformatted as euros.
        .task(id: appState.viewKey) {
            // Restore the saved range before the first load, so the pickers and
            // the numbers always agree.
            if startTI != 0 { startDate = Date(timeIntervalSinceReferenceDate: startTI) }
            if endTI != 0 { endDate = Date(timeIntervalSinceReferenceDate: endTI) }
            guard report == nil || reportMarket != appState.selectedMarket else { return }
            report = nil   // don't show the old market's totals under the new header
            await load()
        }
        .onChange(of: startDate) { startTI = startDate.timeIntervalSinceReferenceDate }
        .onChange(of: endDate) { endTI = endDate.timeIntervalSinceReferenceDate }
    }

    @ViewBuilder private var content: some View {
        if isLoading && report == nil {
            ProgressView("Building report…").frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if let loadError {
            ContentUnavailableView {
                Label("Report unavailable", systemImage: "doc.text.magnifyingglass")
            } description: { Text(loadError) } actions: {
                Button("Retry") { Task { await load() } }
            }
        } else if let r = report {
            // Summary cards as a fixed top band; the per-day table below is greedy
            // and fills the window (no dead space). The table is a bare greedy table
            // in this if/else whose other branches are also greedy — the safe shape.
            VStack(spacing: 0) {
                VStack(alignment: .leading, spacing: Layout.Spacing.md) {
                    Text(rangeCaption(r))
                        .font(.callout).foregroundStyle(.secondary)
                    LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: Layout.Spacing.md),
                                             count: 4), spacing: Layout.Spacing.md) {
                        ForEach(cards(r.totals), id: \.title) { card in
                            StatCard(title: card.title, value: card.value, symbol: card.symbol)
                                .mdCard()
                        }
                    }
                }
                .padding(Layout.Spacing.lg)
                Divider()
                SectionHeader(title: "Per day", subtitle: "newest first", count: r.days.count)
                    .padding(.horizontal, Layout.Spacing.sm)
                    .padding(.top, Layout.Spacing.xs)
                daysTable(r.days)
            }
        } else {
            ContentUnavailableView("Pick a range and Generate",
                                   systemImage: "calendar",
                                   description: Text("Rolls up spend, sales, and the derived ad metrics for the dates you choose."))
        }
    }

    // Local Identifiable wrapper — SyncDay isn't Identifiable, and Table needs it
    // for a selection binding. Keyed on the date, which is unique per day.
    private struct DayRow: Identifiable {
        let day: SyncDay
        var id: String { day.date }
    }

    /// Greedy per-day table — fills below the summary cards. Newest day on top.
    /// Same per-day rows the CSV export carries, now shown on screen.
    private func daysTable(_ days: [SyncDay]) -> some View {
        Table(days.reversed().map(DayRow.init), selection: $daySel) {
            TableColumn("Date") { Text(Format.euDate($0.day.date)).monospacedDigit() }
            TableColumn("Spend") { Text(Format.money($0.day.spend, currency: currency)).monospacedDigit() }
            TableColumn("Sales") { Text(Format.money($0.day.sales, currency: currency)).monospacedDigit() }
            TableColumn("Orders") { Text(Format.count($0.day.orders)).monospacedDigit() }
            TableColumn("Impressions") { Text($0.day.impressions.map { Format.count($0) } ?? "—").monospacedDigit() }
            TableColumn("Clicks") { Text($0.day.clicks.map { Format.count($0) } ?? "—").monospacedDigit() }
            TableColumn("Units") { Text($0.day.units.map { Format.count($0) } ?? "—").monospacedDigit() }
        }
        .frame(minHeight: 160)
    }

    private func rangeCaption(_ r: ReportResponse) -> String {
        "\(Format.euDate(r.start)) → \(Format.euDate(r.end)) · \(r.dayCount) day\(r.dayCount == 1 ? "" : "s") banked"
    }

    private struct Card { let title: String; let value: String; let symbol: String }

    private func cards(_ t: ReportTotals) -> [Card] {
        [
            Card(title: "Spend", value: Format.money(t.spend, currency: currency), symbol: "creditcard.fill"),
            Card(title: "Sales", value: Format.money(t.sales, currency: currency), symbol: "chart.line.uptrend.xyaxis"),
            Card(title: "Orders", value: Format.count(t.orders), symbol: "shippingbox.fill"),
            Card(title: "Units", value: Format.count(t.units), symbol: "cube.box.fill"),
            Card(title: "ACOS", value: Format.percent(t.acos), symbol: "percent"),
            Card(title: "ROAS", value: t.roas.map { String(format: "%.2f×", $0) } ?? "—", symbol: "arrow.up.right.circle.fill"),
            Card(title: "Impressions", value: Format.count(t.impressions), symbol: "eye.fill"),
            Card(title: "Clicks", value: Format.count(t.clicks), symbol: "cursorarrow.click"),
            Card(title: "CTR", value: Format.percent(t.ctr, digits: 2), symbol: "hand.tap.fill"),
            Card(title: "CPC", value: Format.money(t.cpc, currency: currency), symbol: "dollarsign.circle"),
            Card(title: "CVR", value: Format.percent(t.cvr), symbol: "arrow.triangle.branch"),
            Card(title: "CPO", value: Format.money(t.cpo, currency: currency), symbol: "cart.fill"),
        ]
    }

    private func load() async {
        let market = appState.selectedMarket   // only this market's response may land
        isLoading = true
        defer { if !Task.isCancelled { isLoading = false } }
        loadError = nil
        let start = Self.iso.string(from: startDate)
        let end = Self.iso.string(from: endDate)
        do {
            let bridge = try appState.makeBridge()
            let resp = try await bridge.call(ReportResponse.self,
                                             ["report", "--start", start, "--end", end],
                                             market: market)
            guard !Task.isCancelled, market == appState.selectedMarket else { return }
            report = resp
            reportMarket = market
        } catch {
            guard !Task.isCancelled, market == appState.selectedMarket else { return }
            report = nil
            reportMarket = market
            loadError = error.localizedDescription
        }
    }
}

#Preview {
    ReportsView()
        .environment(AppState())
}
