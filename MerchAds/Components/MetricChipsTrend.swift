import SwiftUI
import Charts

/// A trend line chart with MerchDash-style metric-toggle chips + a date-range
/// dropdown. Charts the per-day series the engine banks in `daily_totals`
/// (spend/sales/orders/impressions/clicks/units) and the ratios derived from
/// them (ACOS/ROAS/CTR/CVR/CPC/CPO). Fed by the `synccal` days.
struct MetricChipsTrend: View {
    let days: [SyncDay]
    let currency: String?
    var height: CGFloat = 280
    /// Optional scope label shown top-right, e.g. "all campaigns".
    var scopeLabel: String = "all campaigns"
    @State private var active: Set<Metric> = [.spend, .sales]
    // Remembered across launches, shared by every trend chart. @AppStorage can't
    // hold a Set, so the metric selection rides as a comma-joined raw string.
    @AppStorage("trend.metrics") private var activeRaw: String = "Spend,Sales"
    @AppStorage("trend.range") private var range: RangeOption = .last30
    @State private var hoverDate: Date?
    // Derived state. `hoverDate` updates on every mouse move, so anything the
    // chart needs must already be built — parsing dates and rebuilding the point
    // array inside body would run hundreds of times a second while hovering.
    // Rebuilt only when the days, the range, or the active metrics change.
    @State private var series: [DayPoint] = []
    @State private var points: [Point] = []

    enum RangeOption: String, CaseIterable, Identifiable {
        case last7 = "Last 7 days", last14 = "Last 14 days", last30 = "Last 30 days"
        case last60 = "Last 60 days", last90 = "Last 90 days", last180 = "Last 180 days"
        case last365 = "Last 365 days", all = "All stored data"
        var id: String { rawValue }
        /// nil = no cap (all stored days).
        var dayCount: Int? {
            switch self {
            case .last7: 7; case .last14: 14; case .last30: 30; case .last60: 60
            case .last90: 90; case .last180: 180; case .last365: 365; case .all: nil
            }
        }
    }

    enum Metric: String, CaseIterable, Identifiable {
        case impressions = "Impressions", clicks = "Clicks", spend = "Spend"
        case orders = "Orders", units = "Units", sales = "Sales"
        case acos = "ACOS", roas = "ROAS", ctr = "CTR", cvr = "CVR", cpc = "CPC", cpo = "CPO"
        var id: String { rawValue }
        var color: Color {
            switch self {
            case .impressions: Theme.ChartPalette.impressions
            case .clicks: Theme.ChartPalette.clicks
            case .spend: Theme.ChartPalette.spend
            case .orders: Theme.ChartPalette.orders
            case .units: Theme.ChartPalette.units
            case .sales: Theme.ChartPalette.sales
            case .acos: Theme.ChartPalette.acos
            case .roas: Theme.ChartPalette.roas
            case .ctr: Theme.ChartPalette.ctr
            case .cvr: Theme.ChartPalette.cvr
            case .cpc: Theme.ChartPalette.cpc
            case .cpo: Theme.ChartPalette.cpo
            }
        }

        /// Rendered in the market currency.
        var isMoney: Bool { self == .spend || self == .sales || self == .cpc || self == .cpo }
        /// A 0…1 fraction rendered as a percentage.
        var isFraction: Bool { self == .acos || self == .ctr || self == .cvr }
    }

    /// A day paired with its parsed Date, so hovering never re-parses.
    private struct DayPoint {
        let day: SyncDay
        let date: Date
    }

    /// Per-day metric value; nil when the day lacks the inputs (so the line skips
    /// the gap rather than plotting a misleading 0).
    private func value(_ d: SyncDay, _ m: Metric) -> Double? {
        switch m {
        case .impressions: d.impressions.map(Double.init)
        case .clicks: d.clicks.map(Double.init)
        case .spend: d.spend
        case .orders: Double(d.orders)
        case .units: d.units.map(Double.init)
        case .sales: d.sales
        case .acos: d.sales > 0 ? d.spend / d.sales : nil
        case .roas: d.spend > 0 ? d.sales / d.spend : nil
        case .ctr:
            if let i = d.impressions, i > 0, let c = d.clicks { Double(c) / Double(i) } else { nil }
        case .cvr:
            if let c = d.clicks, c > 0 { Double(d.orders) / Double(c) } else { nil }
        case .cpc:
            if let c = d.clicks, c > 0 { d.spend / Double(c) } else { nil }
        case .cpo: d.orders > 0 ? d.spend / Double(d.orders) : nil
        }
    }

    /// Whether ANY day in range can compute this metric — chips for metrics with
    /// no banked inputs (e.g. impressions before the daily bank recorded them)
    /// are shown disabled so the empty toggle isn't mysterious.
    private func hasData(_ m: Metric) -> Bool {
        series.contains { value($0.day, m) != nil }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            headerRow
            chart
            chips
        }
        .padding(18)
        .mdCard()
        .onChange(of: days, initial: true) { rebuildSeries() }
        .onChange(of: range) { rebuildSeries() }
        .onChange(of: active) {
            activeRaw = active.map(\.rawValue).sorted().joined(separator: ",")
            rebuildPoints(from: series)
        }
        .task { active = Self.decodeMetrics(activeRaw) }   // restore the saved chips
    }

    /// The day series for the selected range, each day paired with its parsed
    /// Date. Cheap enough on its own — but it feeds every other derived value,
    /// so it is built once per change rather than once per body pass.
    private func rebuildSeries() {
        let stored = days.filter { $0.stored }.sorted { $0.date < $1.date }
        let windowed = range.dayCount.map { Array(stored.suffix($0)) } ?? stored
        let built = windowed.compactMap { d in
            Self.parseDay(d.date).map { DayPoint(day: d, date: $0) }
        }
        series = built
        rebuildPoints(from: built)
    }

    private func rebuildPoints(from series: [DayPoint]) {
        var out: [Point] = []
        for m in lineMetrics {
            for p in series {
                if let v = value(p.day, m) {
                    out.append(Point(date: p.date, value: v, metric: m.rawValue, color: m.color))
                }
            }
        }
        points = out
    }

    /// Persisted metric selection is a comma-joined list of raw values; an empty
    /// or unrecognized string falls back to spend+sales so the chart is never
    /// blank on first load.
    private static func decodeMetrics(_ raw: String) -> Set<Metric> {
        let set = Set(raw.split(separator: ",").compactMap { Metric(rawValue: String($0)) })
        return set.isEmpty ? [.spend, .sales] : set
    }

    private var rangeSpan: String {
        guard let first = series.first?.day.date, let last = series.last?.day.date
        else { return "no data" }
        return "\(Format.euDate(first)) → \(Format.euDate(last))"
    }

    private var headerRow: some View {
        HStack(alignment: .center) {
            Menu {
                ForEach(RangeOption.allCases) { opt in
                    Button {
                        range = opt
                    } label: {
                        if range == opt { Label(opt.rawValue, systemImage: "checkmark") }
                        else { Text(opt.rawValue) }
                    }
                }
            } label: {
                HStack(spacing: 6) {
                    Text(range.rawValue).font(Typography.chipLabelActive)
                    Image(systemName: "chevron.down").font(Typography.gridLabel)
                }
                .foregroundStyle(Theme.Colors.textPrimary)
                .padding(.horizontal, 10).padding(.vertical, 5)
                .background(Theme.Colors.surface, in: RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8)
                    .strokeBorder(Theme.Colors.separator, lineWidth: 1))
            }
            .menuStyle(.button)
            .buttonStyle(.borderless)
            .menuIndicator(.hidden)   // the label draws its own chevron
            .fixedSize()
            Spacer()
            Text("\(scopeLabel) · \(rangeSpan)")
                .font(Typography.microLabel)
                .foregroundStyle(Theme.Colors.muted)
        }
    }

    private struct Point: Identifiable {
        let date: Date; let value: Double; let metric: String; let color: Color
        /// Derived, not a fresh UUID — Swift Charts diffs on identity, and a new
        /// id every evaluation makes every point look brand new.
        var id: String { "\(metric)@\(date.timeIntervalSinceReferenceDate)" }
    }

    // Impressions render as faint bars (MerchDash), everything else as lines.
    private var lineMetrics: [Metric] {
        active.filter { $0 != .impressions }.sorted { $0.rawValue < $1.rawValue }
    }

    private var impressionsMax: Double {
        series.compactMap { value($0.day, .impressions) }.max() ?? 0
    }
    /// Tallest line value in range — impressions bars are normalised to this so they
    /// sit behind the lines as a backdrop instead of dwarfing them (tens of thousands
    /// vs. dollars). With no lines active, bars use their raw scale.
    private var lineValueMax: Double {
        points.map(\.value).max() ?? 0
    }
    private func impressionBarY(_ d: SyncDay) -> Double? {
        guard active.contains(.impressions), let v = value(d, .impressions) else { return nil }
        if lineMetrics.isEmpty || lineValueMax <= 0 || impressionsMax <= 0 { return v }
        return v / impressionsMax * lineValueMax
    }

    @ViewBuilder private var chart: some View {
        if active.isEmpty {
            Text("Select a metric below")
                .font(Typography.chipLabel).foregroundStyle(Theme.Colors.muted)
                .frame(maxWidth: .infinity, minHeight: height)
        } else {
            Chart {
                if active.contains(.impressions) {
                    ForEach(series, id: \.day.date) { p in
                        if let y = impressionBarY(p.day) {
                            BarMark(x: .value("Date", p.date), y: .value("Impressions", y))
                                .foregroundStyle(Metric.impressions.color.opacity(0.16))
                        }
                    }
                }
                ForEach(points) { p in
                    LineMark(x: .value("Date", p.date),
                             y: .value("Value", p.value),
                             series: .value("Metric", p.metric))
                        .foregroundStyle(p.color)
                }
                if let hovered = hoveredPoint {
                    RuleMark(x: .value("Date", hovered.date))
                        .foregroundStyle(Theme.Colors.muted.opacity(0.5))
                        .lineStyle(StrokeStyle(lineWidth: 1))
                        .annotation(position: .topLeading, alignment: .leading, spacing: 0,
                                    overflowResolution: .init(x: .fit(to: .chart), y: .fit(to: .chart))) {
                            tooltip(for: hovered.day)
                        }
                }
            }
            .chartLegend(.hidden)
            .chartXAxis {
                AxisMarks { value in
                    AxisGridLine()
                    AxisValueLabel {
                        if let d = value.as(Date.self) {
                            Text(Format.euDateShort(d))
                        }
                    }
                }
            }
            .chartYAxis {
                AxisMarks { value in
                    AxisGridLine()
                    AxisValueLabel {
                        // The axis units follow whatever is actually plotted. It
                        // used to always format as money, so a CVR-only chart
                        // read "$0.08" where it meant 8%.
                        if axisUnit != .mixed, let v = value.as(Double.self) {
                            Text(axisLabel(v))
                        }
                    }
                }
            }
            .frame(height: height)
            .chartOverlay { proxy in
                GeometryReader { geo in
                    Rectangle().fill(.clear).contentShape(Rectangle())
                        .onContinuousHover { phase in
                            switch phase {
                            case .active(let loc):
                                guard let plot = proxy.plotFrame else { return }
                                let x = loc.x - geo[plot].origin.x
                                if let d: Date = proxy.value(atX: x) { hoverDate = d }
                            case .ended:
                                hoverDate = nil
                            }
                        }
                }
            }
        }
    }

    /// Day nearest the hovered x-position (nil when not hovering). Dates are
    /// already parsed in `series`, so a mouse move is just a scan of Doubles.
    private var hoveredPoint: DayPoint? {
        guard let hoverDate else { return nil }
        return series.min {
            abs($0.date.timeIntervalSince(hoverDate)) < abs($1.date.timeIntervalSince(hoverDate))
        }
    }

    /// What the Y axis is measuring. Only the metrics that drive the Y scale
    /// count: with lines on screen the impression bars are normalised to them,
    /// and with no lines the bars use their own raw (count) scale.
    private enum AxisUnit { case money, percent, plain, mixed }

    private var axisUnit: AxisUnit {
        let metrics = lineMetrics.isEmpty ? Array(active) : lineMetrics
        guard !metrics.isEmpty else { return .plain }
        if metrics.allSatisfy(\.isMoney) { return .money }
        if metrics.allSatisfy(\.isFraction) { return .percent }
        if metrics.allSatisfy({ !$0.isMoney && !$0.isFraction }) { return .plain }
        return .mixed   // genuinely mixed units — no honest axis label exists
    }

    private func axisLabel(_ v: Double) -> String {
        switch axisUnit {
        case .money: Format.money(v, currency: currency)
        case .percent: Format.percent(v, digits: 0)
        case .plain, .mixed: Format.count(Int(v.rounded()))
        }
    }

    private func tooltip(for day: SyncDay) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(Format.euDate(day.date)).font(Typography.chipLabelActive)
                .foregroundStyle(Theme.Colors.textPrimary)
                .padding(.bottom, 1)
            ForEach(active.sorted { $0.rawValue < $1.rawValue }) { m in
                HStack(spacing: 6) {
                    Circle().fill(m.color).frame(width: 7, height: 7)
                    // Two Texts in a zero-spacing HStack, not `Text + Text`:
                    // concatenation is deprecated on macOS 26. The label already
                    // ends in ": ", so spacing 0 keeps the same look, and the
                    // tooltip is .fixedSize() one-liners — nothing wraps, which
                    // is the only thing concatenation would have bought here.
                    HStack(spacing: 0) {
                        Text("\(m.rawValue): ").font(Typography.chipLabel)
                            .foregroundStyle(Theme.Colors.textSecondary)
                        Text(format(day, m)).font(Typography.chipLabelActive)
                            .foregroundStyle(Theme.Colors.textPrimary)
                    }
                }
            }
        }
        .padding(8)
        .background(Theme.Colors.surface, in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(Theme.Colors.separator, lineWidth: 1))
        .shadow(color: Color(hex: 0x101828, alpha: 0.10), radius: 6, y: 2)
        .fixedSize()
    }

    private func format(_ d: SyncDay, _ m: Metric) -> String {
        guard let v = value(d, m) else { return "—" }
        switch m {
        case .spend, .sales, .cpc, .cpo: return Format.money(v, currency: currency)
        case .acos, .ctr, .cvr: return Format.percent(v, digits: m == .ctr ? 2 : 1)
        case .roas: return String(format: "%.2f×", v)
        case .impressions, .clicks, .orders, .units: return Format.count(Int(v))
        }
    }

    private var chips: some View {
        ChipFlow(spacing: 8, lineSpacing: 8) {
            ForEach(Metric.allCases) { m in
                let on = active.contains(m)
                let enabled = hasData(m)
                Button {
                    if on { active.remove(m) } else { active.insert(m) }
                } label: {
                    HStack(spacing: 6) {
                        Circle().fill(m.color).frame(width: 7, height: 7).opacity(on ? 1 : 0.35)
                        Text(m.rawValue).font(on ? Typography.chipLabelActive : Typography.chipLabel)
                    }
                    .foregroundStyle(on ? Theme.Colors.textPrimary : Theme.Colors.muted)
                    .padding(.horizontal, 10).padding(.vertical, 5)
                    .background(on ? Theme.Colors.surface : Color.clear, in: Capsule())
                    .overlay(Capsule().strokeBorder(on ? Theme.Colors.separator : .clear, lineWidth: 1))
                }
                .buttonStyle(.plain)
                .disabled(!enabled)
                .opacity(enabled ? 1 : 0.4)
                .help(enabled ? "" : "No \(m.rawValue.lowercased()) data banked for this range yet")
            }
        }
    }

    // One shared formatter. This used to build a DateFormatter per call, and it
    // was called for every day × metric on every body pass — which, with hover
    // driving body, meant thousands of allocations a second.
    private static let dayParser: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd"
        f.timeZone = TimeZone(identifier: "UTC"); f.locale = Locale(identifier: "en_US_POSIX")
        return f
    }()

    private static func parseDay(_ s: String) -> Date? { dayParser.date(from: s) }
}

/// Minimal wrapping row layout for the 12 metric chips (they don't fit one line
/// in the narrower app columns the way they do on MerchDash's full-width chart).
private struct ChipFlow: SwiftUI.Layout {
    var spacing: CGFloat = 8
    var lineSpacing: CGFloat = 8

    func sizeThatFits(proposal: ProposedViewSize, subviews: SwiftUI.LayoutSubviews,
                      cache: inout ()) -> CGSize {
        let maxWidth = proposal.width ?? .infinity
        var x: CGFloat = 0, y: CGFloat = 0, rowHeight: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x + size.width > maxWidth, x > 0 {
                x = 0; y += rowHeight + lineSpacing; rowHeight = 0
            }
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
        return CGSize(width: maxWidth == .infinity ? x : maxWidth, height: y + rowHeight)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize,
                       subviews: SwiftUI.LayoutSubviews, cache: inout ()) {
        var x = bounds.minX, y = bounds.minY, rowHeight: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x + size.width > bounds.maxX, x > bounds.minX {
                x = bounds.minX; y += rowHeight + lineSpacing; rowHeight = 0
            }
            view.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
    }
}
