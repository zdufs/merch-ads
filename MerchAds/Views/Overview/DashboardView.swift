import SwiftUI
import Charts

/// At-a-glance dashboard: profitability and safety stay above the fold;
/// trends and movers follow in reading order. Every source fails independently.
struct DashboardView: View {
    @Environment(AppState.self) private var appState
    @State private var coordinator = DashboardSnapshotCoordinator()
    @State private var syncCal: SyncCalResponse?
    @State private var syncCalMarket: String?
    @State private var streamToday: StreamTodayResponse?
    @State private var streamTodayMarket: String?
    @State private var streamTodayError: String?
    @State private var streamTodayLoading = false

    private var snapshot: DashboardSnapshot? {
        guard coordinator.snapshot?.market == appState.selectedMarket else { return nil }
        return coordinator.snapshot
    }

    /// Same market guard as `snapshot`: a sync calendar fetched for another
    /// market must never render under this market's header.
    /// nil until the CURRENT market's answer is in. Same last-request-wins guard
    /// as the sync calendar: a slow US reply must never render on a DE dashboard.
    private var stream: StreamTodayResponse? {
        streamTodayMarket == appState.selectedMarket ? streamToday : nil
    }

    private var streamPanelState: DashboardStreamPanelState {
        guard streamTodayMarket == appState.selectedMarket else { return .loading }
        return .resolve(responseSupported: streamToday?.supported,
                        error: streamTodayError, isLoading: streamTodayLoading)
    }

    private var syncCalendar: SyncCalResponse? {
        guard syncCalMarket == appState.selectedMarket else { return nil }
        return syncCal
    }

    private var currency: String? {
        snapshot?.metrics.value?.currency ?? appState.currentMarket?.currency
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            PageHeader(title: "Dashboard", subtitle: navigationSubtitle, help: .dashboard)
            statusBand
            alertStrip
            Divider()
            GeometryReader { geo in
                ScrollView {
                    VStack(alignment: .leading, spacing: Layout.Spacing.md) {
                        // Live, and on a DIFFERENT basis from everything under it:
                        // Stream is about an hour behind, the banked periods below
                        // are a day behind. It carries its own header saying so.
                        // Only when Stream actually has this market's data. Most
                        // installs have no queue at all, and a permanent "not set
                        // up" row on the Dashboard is noise; System Health is
                        // where a configured-but-silent Stream gets reported.
                        if case .unavailable(let error) = streamPanelState {
                            DashboardUnavailableCard(
                                title: "Marketing Stream unavailable", detail: error)
                        } else if streamPanelState == .loading {
                            ProgressView("Loading Marketing Stream…")
                                .frame(maxWidth: .infinity, minHeight: 90)
                        } else if streamPanelState == .available, let stream {
                            StreamTodayView(response: stream, fallbackCurrency: currency)
                        }
                        periodStack
                        if let syncCalendar {
                            // Pin the heat grid to a DEFINITE width from this
                            // GeometryReader. Left to size itself (its own inner
                            // GeometryReader), inside this ScrollView it balloons to a
                            // stale/ideal width and drags the period cards wide with
                            // it, clipping them on a narrow window.
                            SyncCalendarView(response: syncCalendar, currency: currency,
                                             explicitWidth: geo.size.width - Layout.Spacing.lg * 2)
                                .frame(width: geo.size.width - Layout.Spacing.lg * 2)
                        }
                        DashboardMonthlySection(
                            section: snapshot?.monthly,
                            fallbackCurrency: currency,
                            chartHeight: monthlyChartHeight(for: geo.size.height))
                            // Absorb any leftover height so the dashboard fills the
                            // window instead of leaving blank space under the chart —
                            // and still scrolls when the content is taller than the view.
                            .frame(maxHeight: .infinity)
                    }
                    .padding(.horizontal, Layout.Spacing.lg)
                    .padding(.vertical, Layout.Spacing.md)
                    .frame(maxWidth: .infinity, minHeight: geo.size.height, alignment: .top)
                }
                .scrollEdgeEffectStyle(.soft, for: .top)
            }
        }
        .background(Theme.Colors.canvas)
        .task(id: appState.viewKey) { await load() }
    }

    private var navigationSubtitle: String {
        if let asOf = snapshot?.metrics.dataAsOf {
            return "\(appState.selectedMarket) · "
                + Self.freshnessText(snapshotAsOf: asOf, dailyAsOf: snapshot?.periods.dataAsOf)
        }
        if snapshot == nil || snapshot?.metrics.isLoading == true {
            return "\(appState.selectedMarket) · loading data"
        }
        return "\(appState.selectedMarket) · data unavailable"
    }

    /// The subtitle dates the PERF SNAPSHOTS. The period cards underneath read
    /// the banked daily history, which is a different table filled by a
    /// different report job — and on DE, FR and ES it was a day ahead: the
    /// header said "data through 22.08." over three cards covering 01.–23.08.
    /// Both were right; the page simply never said they were two things.
    static func freshnessText(snapshotAsOf: String, dailyAsOf: String?) -> String {
        guard let dailyAsOf, dailyAsOf != snapshotAsOf else {
            return "data through \(Format.euDate(snapshotAsOf))"
        }
        return "snapshots through \(Format.euDate(snapshotAsOf))"
            + " · daily history through \(Format.euDate(dailyAsOf))"
    }

    /// Row one of the period stack, pinned above the fold. It renders through the
    /// same component and the same `periods` payload as the rows below, so the
    /// current month can never disagree with the history underneath it.
    private var statusBand: some View {
        Group {
            if let period = periodRow("current_month") {
                PeriodBandView(period: period, currency: currency)
            } else {
                periodPlaceholder(section: snapshot?.periods, label: "Current month")
            }
        }
        .padding(.horizontal, Layout.Spacing.lg)
        .padding(.top, Layout.Spacing.sm)
        .padding(.bottom, Layout.Spacing.sm)
    }

    /// Rows two onward: previous month, then year to date.
    /// Order comes from the engine so the app never re-sorts periods by hand.
    private var periodStack: some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.lg) {
            ForEach(laterPeriods) { period in
                PeriodBandView(period: period, currency: currency)
            }
        }
    }

    private var laterPeriods: [PeriodRow] {
        PeriodRow.dashboardStack(from: snapshot?.periods.value?.periods ?? [])
    }

    private func periodRow(_ key: String) -> PeriodRow? {
        snapshot?.periods.value?.periods.first { $0.key == key }
    }

    private func periodPlaceholder<Value>(section: DashboardSection<Value>?,
                                          label: String) -> some View {
        HStack(spacing: Layout.Spacing.xs) {
            Text(label.uppercased())
                .font(.caption.weight(.semibold))
                .tracking(0.55)
                .foregroundStyle(Theme.Colors.muted)
            Text(section?.isLoading == true || snapshot == nil
                 ? "loading…" : (section?.error ?? "unavailable"))
                .font(.callout)
                .foregroundStyle(Theme.Colors.muted)
            Spacer(minLength: 0)
        }
        .padding(.vertical, 14)
        .padding(.horizontal, 18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .mdCard()
    }

    /// "2026-08-01→2026-08-03" → "01.–03.08.2026" (or "01.07.–03.08.2026" when
    /// the window straddles two months). Returns nil if the engine's shape changes.
    /// `nonisolated`: pure string work, no view state. Without it the
    /// enclosing View's @MainActor isolation reaches a parser that never
    /// needed it, and every test calling it warns.
    nonisolated static func windowLabel(_ window: String) -> String? {
        let parts = window.components(separatedBy: "→")
        guard parts.count == 2,
              let start = Format.date(parts[0]), let end = Format.date(parts[1]) else { return nil }
        let cal = Calendar.current
        let endLabel = Format.euDate(end)
        if cal.component(.month, from: start) == cal.component(.month, from: end),
           cal.component(.year, from: start) == cal.component(.year, from: end) {
            return "\(String(format: "%02d", cal.component(.day, from: start))).–\(endLabel)"
        }
        return "\(Format.euDate(start))–\(endLabel)"
    }

    private func metricValue<Value>(_ section: DashboardSection<Value>?,
                                    value: (Value) -> String) -> String {
        if let data = section?.value { return value(data) }
        if section?.isLoading == true || snapshot == nil { return "Loading…" }
        return "Unavailable"
    }

    /// What the red kill pill should say.
    ///
    /// It printed the raw count and tinted it red whenever it was above zero.
    /// Both of DE's two candidates were already PAUSED on 2026-08-24, so the
    /// only red thing on the screen named two designs the operator could do
    /// nothing about. A few of those and the pill stops being read — and then
    /// the day a serving design crosses the floor looks exactly the same.
    static func killPill(_ list: KillListResponse?) -> (text: String, critical: Bool)? {
        guard let list else { return nil }
        func phrase(_ n: Int) -> String { "\(n) kill candidate\(n == 1 ? "" : "s")" }
        let live = list.designs.filter {
            ($0.state ?? "ENABLED").caseInsensitiveCompare("ENABLED") == .orderedSame
        }.count
        if live > 0 { return (phrase(live), true) }
        if list.count > 0 { return (phrase(list.count) + " · already paused", false) }
        return (phrase(0), false)
    }

    private var alertStrip: some View {
        let activeAlerts = snapshot?.alerts.value?.alerts.count
        let kill = Self.killPill(snapshot?.killList.value)
        let lastRun = snapshot?.health.value?.lastRun
        return VStack(alignment: .leading, spacing: Layout.Spacing.xs) {
            HStack(spacing: Layout.Spacing.xs) {
                Text("Watch now")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                AlertPill(
                    text: killIsActive ? "KILL active" : "Writes armed",
                    symbol: killIsActive ? "exclamationmark.octagon.fill" : "checkmark.shield.fill",
                    tint: killIsActive ? Theme.Colors.critical : Theme.Colors.positive,
                    action: { appState.navigate(to: .screen(.actions)) })
                    .help("The KILL freeze and write gate live on Actions")
                AlertPill(
                    text: activeAlerts.map { "\($0) active alert\($0 == 1 ? "" : "s")" }
                        ?? unavailableLabel(snapshot?.alerts),
                    symbol: "bell.badge.fill",
                    tint: (activeAlerts ?? 0) > 0 ? Theme.Colors.caution : .secondary,
                    action: { appState.navigate(to: .screen(.errors)) })
                    .help("See every open alert on Errors")
                AlertPill(
                    text: kill?.text ?? unavailableLabel(snapshot?.killList),
                    symbol: "xmark.bin.fill",
                    tint: kill?.critical == true ? Theme.Colors.critical : .secondary,
                    action: { appState.navigate(to: .screen(.killList)) })
                    .help("Review the designs that meet the kill rule on the Kill List. "
                          + "The count is the ones still ENABLED — a paused design cannot spend.")
                // A nightly that failed part-way reaches no other pill: alerts
                // can be genuinely zero while six steps failed, which is what
                // this strip showed on 2026-08-24 with DE's pull among them.
                if let lastRun, !lastRun.ok {
                    let failed = lastRun.failures.count
                    AlertPill(text: "nightly run failed"
                              + (failed > 0 ? " · \(failed) step\(failed == 1 ? "" : "s")" : ""),
                              symbol: "xmark.octagon.fill",
                              tint: Theme.Colors.critical,
                              action: { appState.navigate(to: .screen(.health)) })
                        .help("The last nightly run did not finish cleanly — "
                              + "System Health lists which steps failed")
                }
                if let staleMessage {
                    AlertPill(text: staleMessage, symbol: "clock.badge.exclamationmark.fill",
                              tint: Theme.Colors.caution,
                              action: { appState.navigate(to: .screen(.health)) })
                        .help("Data freshness per market lives on System Health")
                }
                Spacer(minLength: 0)
            }
        }
        .padding(.horizontal, Layout.Spacing.lg)
        .padding(.bottom, Layout.Spacing.md)
    }

    private var killIsActive: Bool {
        snapshot?.health.value?.killActive ?? appState.killActive
    }

    private func unavailableLabel<Value>(_ section: DashboardSection<Value>?) -> String {
        section?.isLoading == true || snapshot == nil ? "evaluating…" : "unavailable"
    }

    /// Days between an engine "yyyy-MM-dd" snapshot date and today, or nil if
    /// the string isn't a parseable day (monthly's "yyyy-MM" deliberately isn't).
    private static func daysStale(_ asOf: String?) -> Int? {
        guard let date = Format.date(asOf) else { return nil }
        return Calendar.current.dateComponents([.day], from: date, to: Date()).day
    }

    private static let staleAfterDays = 3

    /// The dashboard draws from several engine tables that are refreshed by
    /// SEPARATE report jobs, so they drift apart when one job fails. Reporting
    /// only the metrics date let a frozen profit snapshot sit behind a fresh
    /// header for days — badge the OLDEST source and name it.
    private var staleMessage: String? {
        let sources: [(String, String?)] = [
            ("metrics", snapshot?.metrics.dataAsOf),
            ("profit", snapshot?.profit.dataAsOf),
            ("daily", snapshot?.daily.dataAsOf),
        ]
        let dated = sources.compactMap { label, asOf -> (String, Int)? in
            guard let days = Self.daysStale(asOf) else { return nil }
            return (label, days)
        }
        guard let worst = dated.max(by: { $0.1 < $1.1 }), worst.1 >= Self.staleAfterDays else {
            return nil
        }
        return "stale · \(worst.0) \(worst.1)d"
    }

    /// Charts scale with the viewport so tall windows fill instead of leaving
    /// dead space; the Layout.ChartHeight constants remain the floors.
    private func monthlyChartHeight(for viewport: CGFloat) -> CGFloat {
        // A modest MINIMUM only — the monthly section has maxHeight: .infinity, so
        // the chart grows to fill whatever space is left under the grid (no blank
        // bottom). This floor just keeps it readable and stops it forcing a scroll.
        max(Layout.ChartHeight.compact, min(200, viewport * 0.15))
    }


    private func load() async {
        let market = appState.selectedMarket
        streamToday = nil
        streamTodayMarket = market
        streamTodayError = nil
        streamTodayLoading = true
        do {
            let bridge = try appState.makeBridge()
            // health is account-wide and AppState.refresh() fetches it on the
            // same trigger — reuse it instead of a second every-market-DB scan
            await coordinator.load(market: market, bridge: bridge,
                                   preloadedHealth: appState.health)
        } catch {
            coordinator.failToStart(market: market, error: error)
        }
        if let bridge = try? appState.makeBridge() {
            let response = try? await bridge.call(SyncCalResponse.self, ["synccal"], market: market)
            // Last request wins: a slow US response must not land on a DE dashboard,
            // and a failed DE call must clear the old calendar rather than keep it.
            guard !Task.isCancelled, market == appState.selectedMarket else { return }
            syncCal = response
            syncCalMarket = market
        }
        do {
            let bridge = try appState.makeBridge()
            let response = try await bridge.call(StreamTodayResponse.self,
                                                 ["stream-today"], market: market)
            guard !Task.isCancelled, market == appState.selectedMarket else { return }
            streamToday = response
            streamTodayMarket = market
            streamTodayError = nil
            streamTodayLoading = false
        } catch {
            guard !Task.isCancelled, market == appState.selectedMarket else { return }
            streamToday = nil
            streamTodayMarket = market
            streamTodayError = error.localizedDescription
            streamTodayLoading = false
        }
    }
}

enum DashboardStreamPanelState: Equatable {
    case loading
    case hidden
    case available
    case unavailable(String)

    static func resolve(responseSupported: Bool?, error: String?,
                        isLoading: Bool) -> Self {
        if let error { return .unavailable(error) }
        if isLoading { return .loading }
        if responseSupported == false { return .hidden }
        if responseSupported == true { return .available }
        return .unavailable("The Stream endpoint returned no status.")
    }
}

private struct DashboardMonthlySection: View {
    let section: DashboardSection<MonthlyResponse>?
    let fallbackCurrency: String?
    var chartHeight: CGFloat = Layout.ChartHeight.standard

    private var subtitle: String {
        guard let coverage = section?.value?.coverage,
              let first = coverage.firstDay,
              let last = coverage.lastDay else {
            return "calendar months · banked daily totals"
        }
        return "calendar months · \(Format.euDate(first))–\(Format.euDate(last))"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.sm) {
            SectionHeader(title: "Monthly", subtitle: subtitle)
            if let error = section?.error, section?.value == nil {
                DashboardUnavailableCard(title: "Monthly history unavailable", detail: error)
            } else if let response = section?.value, !response.months.isEmpty {
                // Months only — a YTD figure with no banked months would now render
                // an empty card, since the summary lines moved to the period stack.
                DashboardMonthlyCard(response: response, fallbackCurrency: fallbackCurrency,
                                     chartHeight: chartHeight)
                    .frame(maxHeight: .infinity)
            } else if section?.isLoading == true || section == nil {
                ProgressView("Loading monthly history…")
                    .frame(maxWidth: .infinity, minHeight: Layout.ChartHeight.standard)
            } else {
                DashboardUnavailableCard(
                    title: "No monthly history",
                    detail: section?.value?.note
                        ?? "The nightly bank has not completed a calendar month yet.")
            }
        }
    }
}

private struct DashboardMonthlyCard: View {
    let response: MonthlyResponse
    let fallbackCurrency: String?
    var chartHeight: CGFloat = Layout.ChartHeight.standard
    @State private var hoveredMonth: String?

    private struct Point: Identifiable {
        let month: String
        let series: String
        let value: Double
        var id: String { "\(month)|\(series)" }
    }

    private var currency: String? { response.currency ?? fallbackCurrency }

    private var months: [MonthRow] {
        response.months.sorted { $0.month < $1.month }
    }

    private var points: [Point] {
        months.flatMap { month in
            [
                Point(month: month.month, series: "Attributed sales", value: month.sales),
                Point(month: month.month, series: "Ad spend", value: month.spend),
            ]
        }
    }

    /// Month-by-month history only. The current-month and year-to-date figures
    /// that used to head this card now live in the dashboard's period stack, and
    /// two copies of the same number invite exactly the kind of silent
    /// disagreement the period stack was built to avoid.
    var body: some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.sm) {
            if !months.isEmpty {
                hoverSummary
                monthlyChart
            }
        }
        .frame(maxHeight: .infinity)
        .padding(Layout.Spacing.md)
        .background(Theme.Colors.surface,
                    in: RoundedRectangle(cornerRadius: Layout.Radius.large))
    }

    @ViewBuilder
    private var hoverSummary: some View {
        if let month = months.first(where: { $0.month == hoveredMonth }) {
            Text("\(Format.monthName(month.month)) · \(Format.money(month.spend, currency: currency)) spend · \(Format.money(month.sales, currency: currency)) sales · \(Format.count(month.orders)) orders")
                .font(.caption.monospacedDigit())
                .foregroundStyle(.secondary)
        } else {
            Text("Hover for exact monthly values")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private var monthlyChart: some View {
        Chart(points) { point in
            BarMark(x: .value("Month", point.month),
                    y: .value("Amount", point.value),
                    width: .fixed(18))
                .position(by: .value("Series", point.series))
                .foregroundStyle(by: .value("Series", point.series))
        }
        .chartForegroundStyleScale([
            "Attributed sales": Theme.Colors.chartSales,
            "Ad spend": Theme.Colors.chartSpend,
        ])
        .chartXAxis {
            AxisMarks(values: months.map(\.month)) { value in
                AxisGridLine().foregroundStyle(Theme.Colors.chartGrid)
                AxisValueLabel {
                    if let month = value.as(String.self) {
                        Text(axisLabel(for: month))
                            .multilineTextAlignment(.center)
                    }
                }
            }
        }
        .chartOverlay { proxy in
            GeometryReader { geometry in
                Rectangle()
                    .fill(.clear)
                    .contentShape(Rectangle())
                    .onContinuousHover { phase in
                        switch phase {
                        case .active(let location):
                            guard let frame = proxy.plotFrame else { return }
                            let x = location.x - geometry[frame].origin.x
                            hoveredMonth = proxy.value(atX: x, as: String.self)
                        case .ended:
                            hoveredMonth = nil
                        }
                    }
            }
        }
        .merchAdsChartStyle(height: chartHeight)
    }

    private func axisLabel(for month: String) -> String {
        let components = Format.monthName(month).split(separator: " ")
        guard components.count == 2 else { return month }
        return "\(components[0])\n\(components[1].suffix(2))"
    }
}


private struct DashboardUnavailableCard: View {
    let title: String
    let detail: String

    var body: some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.xs) {
            Label(title, systemImage: "exclamationmark.triangle")
                .font(.callout.weight(.semibold))
            Text(detail)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(Layout.Spacing.md)
        .frame(maxWidth: .infinity, minHeight: 96, alignment: .leading)
        .background(Theme.Colors.surface,
                    in: RoundedRectangle(cornerRadius: Layout.Radius.medium))
    }
}

private struct AlertPill: View {
    let text: String
    let symbol: String
    let tint: Color
    /// When set, the pill is a button that navigates somewhere. When nil it is
    /// a plain status chip (the old behaviour) — several pills just report state.
    var action: (() -> Void)? = nil

    var body: some View {
        if let action {
            Button(action: action) { pill }
                .buttonStyle(.plain)
                .pointerStyle(.link)
        } else {
            pill
        }
    }

    private var pill: some View {
        Label(text, systemImage: symbol)
            .font(.caption.weight(.medium))
            .lineLimit(1)
            .padding(.horizontal, Layout.Spacing.xs)
            .padding(.vertical, Layout.Spacing.xxs)
            .background(tint.opacity(0.13), in: Capsule())
            .foregroundStyle(tint)
            .contentShape(Capsule())
    }
}


#Preview {
    DashboardView()
        .environment(AppState())
        .frame(width: 1100, height: 760)
}
