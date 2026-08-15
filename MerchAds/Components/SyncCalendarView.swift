import SwiftUI

/// GitHub-style contribution heat-grid with four modes (days stored / values
/// adjusted / ad spend / orders), matching MerchDash's dashboard centerpiece.
/// Weeks are columns, weekdays are rows; each cell is tinted by the mode's value
/// with a native hover tooltip. Fed by the `synccal` endpoint.
struct SyncCalendarView: View {
    let response: SyncCalResponse
    let currency: String?
    /// The width cells are sized to, passed from the parent's own GeometryReader —
    /// the grid does NOT measure its own width. Inside a ScrollView a self-measured
    /// width balloons during the scroll view's sizing pass and drags siblings wide,
    /// so the caller owns it. Required (not optional) so a new caller can't forget it
    /// and bring that ballooning back.
    let explicitWidth: CGFloat
    @AppStorage("syncCalendar.mode") private var mode: Mode = .stored
    @State private var hovered: SyncDay?
    // The grid is derived state, not view state: building it costs ~370 date
    // parse/format round-trips plus a dictionary rebuild. Hovering a cell writes
    // `hovered` and re-runs body, so anything computed inline here would be
    // rebuilt on every mouse-enter and mouse-leave. Cache it instead and rebuild
    // only when the days (or the mode, for the color scale) actually change.
    @State private var columns: [[SyncDay?]] = []
    @State private var monthLabels: [String] = []
    @State private var maxValue: Double = 1
    @State private var gridRange: String?

    enum Mode: String, CaseIterable, Identifiable {
        case stored = "Days stored"
        case adjusted = "Values adjusted"
        case spend = "Ad spend"
        case orders = "Orders"
        var id: String { rawValue }
    }

    private static let parser: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd"
        f.timeZone = TimeZone(identifier: "UTC"); f.locale = Locale(identifier: "en_US_POSIX")
        return f
    }()
    private static let monthFmt: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "MMM"
        f.timeZone = TimeZone(identifier: "UTC"); f.locale = Locale(identifier: "en_US_POSIX")
        return f
    }()

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            header
            Text(subtitle).font(Typography.cardBody).foregroundStyle(Theme.Colors.textSecondary)
            grid
            HStack(spacing: 10) {
                legend
                Spacer()
            }
            modeTabs
        }
        .padding(18)
        .mdCard()
        .onChange(of: response.days, initial: true) { rebuildGrid() }
        .onChange(of: mode) { recomputeMax(in: columns) }
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(mode.rawValue).font(Typography.cardTitle)
                .foregroundStyle(Theme.Colors.textPrimary)
            if let r = rangeText {
                Text(r).font(Typography.cardCaption).foregroundStyle(Theme.Colors.muted)
            }
            Spacer()
        }
    }

    // MARK: grid

    /// Builds the week columns and their month labels. The window is a fixed 52
    /// weeks back from the newest day, snapped to that week's Sunday, so the
    /// grid always reads as a year at a consistent cell size.
    private func rebuildGrid() {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "UTC")!
        // uniquingKeysWith, not uniqueKeysWithValues: a duplicate day from the
        // engine would trap the app rather than just render one cell twice.
        let byDate = Dictionary(response.days.map { ($0.date, $0) },
                                uniquingKeysWith: { _, last in last })
        let keys = byDate.keys.sorted()
        guard let firstKey = keys.first, let lastKey = keys.last,
              let firstDate = Self.parser.date(from: firstKey),
              let lastDate = Self.parser.date(from: lastKey) else {
            columns = []; monthLabels = []; maxValue = 1; gridRange = nil
            return
        }
        let end = c.startOfDay(for: lastDate)
        // Always draw the full 52-week window. Clamping to the first day with
        // data leaves too few columns to fill the card, and since the cells are
        // sized to span that width each square balloons — the grid is meant to
        // read as a year at a glance.
        let dataStart = c.date(byAdding: .day, value: -7 * 52, to: end)
            ?? c.startOfDay(for: firstDate)
        // back up to the Sunday of the first week
        let wd = c.component(.weekday, from: dataStart) - 1
        guard let start = c.date(byAdding: .day, value: -wd, to: dataStart) else {
            columns = []; monthLabels = []; maxValue = 1; gridRange = nil
            return
        }
        var cols: [[SyncDay?]] = []
        var cur = start
        while cur <= end {
            var col: [SyncDay?] = []
            for _ in 0..<7 {
                if cur >= dataStart && cur <= end {
                    let key = Self.parser.string(from: cur)
                    // Gaps INSIDE the range stay in the grid as "not synced"
                    // placeholders — that's the point of the legend.
                    col.append(byDate[key] ?? SyncDay(date: key, stored: false,
                                                      spend: 0, orders: 0, adjusted: 0))
                } else {
                    col.append(nil)   // lead-in of the first week / tail of the last
                }
                cur = c.date(byAdding: .day, value: 1, to: cur)!
            }
            cols.append(col)
        }
        columns = cols
        monthLabels = Self.monthLabels(for: cols, calendar: c)
        let drawnStart = Self.parser.string(from: dataStart)
        gridRange = "\(Format.euDate(drawnStart)) → \(Format.euDate(lastKey))"
        recomputeMax(in: cols)
    }

    /// Month name on the first column of each new month — computed once with the
    /// grid so hovering doesn't re-parse every column's date.
    private static func monthLabels(for cols: [[SyncDay?]], calendar c: Calendar) -> [String] {
        var out: [String] = []
        var prevMonth: Int? = nil
        for col in cols {
            guard let d = col.compactMap({ $0 }).first.flatMap({ parser.date(from: $0.date) }) else {
                out.append("")
                continue
            }
            let day = c.component(.day, from: d)
            let m = c.component(.month, from: d)
            out.append((day <= 7 && prevMonth != m) ? monthFmt.string(from: d) : "")
            prevMonth = m
        }
        return out
    }

    /// The color scale's top of range. Depends on the mode, so it is recomputed
    /// when the mode changes as well as when the grid is rebuilt.
    private func recomputeMax(in cols: [[SyncDay?]]) {
        maxValue = max(1.0, cols.flatMap { $0 }.compactMap { $0.map(value) }.max() ?? 1)
    }

    private var grid: some View {
        let cols = columns
        let maxV = maxValue
        let n = max(cols.count, 1)
        let leftW: CGFloat = 30
        let gap: CGFloat = 3
        // size the cells so all weeks span the full card width
        let usableWidth = explicitWidth
        let cellW = max(8, (usableWidth - leftW - CGFloat(n - 1) * gap) / CGFloat(n))
        return VStack(alignment: .leading, spacing: 4) {
            monthRow(cell: cellW, gap: gap, leftW: leftW)
            HStack(alignment: .top, spacing: gap) {
                VStack(spacing: gap) {
                    ForEach(0..<7, id: \.self) { r in
                        Text(["", "Mon", "", "Wed", "", "Fri", ""][r])
                            .font(Typography.gridLabel).foregroundStyle(Theme.Colors.muted)
                            .frame(width: leftW - gap, height: cellW, alignment: .leading)
                    }
                }
                ForEach(cols.indices, id: \.self) { i in
                    VStack(spacing: gap) {
                        ForEach(0..<7, id: \.self) { r in
                            cellView(cols[i][r], cell: cellW, maxV: maxV)
                        }
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .overlay(alignment: .top) {
            if let h = hovered {
                // Adaptive chip (same treatment as ChartTooltip): a material pill
                // with primary text reads in BOTH appearances. The old fixed
                // textPrimary background broke in dark mode — textPrimary is
                // near-white there, so white text vanished on a white pill.
                Text(tooltip(h))
                    .font(Typography.microLabel.weight(.medium))
                    .foregroundStyle(.primary)
                    .padding(.horizontal, 9).padding(.vertical, 5)
                    .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 6))
                    .overlay {
                        RoundedRectangle(cornerRadius: 6)
                            .strokeBorder(Theme.Colors.separator, lineWidth: 0.5)
                    }
                    .shadow(color: .black.opacity(0.15), radius: 4, y: 1)
                    .offset(y: -6)
            }
        }
    }

    private func monthRow(cell: CGFloat, gap: CGFloat, leftW: CGFloat) -> some View {
        HStack(spacing: gap) {
            Color.clear.frame(width: leftW - gap, height: 13)
            ForEach(monthLabels.indices, id: \.self) { i in
                Text(monthLabels[i]).font(Typography.gridLabel).foregroundStyle(Theme.Colors.muted)
                    .frame(width: cell, height: 13, alignment: .leading)
            }
        }
    }

    private func cellView(_ day: SyncDay?, cell: CGFloat, maxV: Double) -> some View {
        RoundedRectangle(cornerRadius: max(2, cell * 0.16), style: .continuous)
            .fill(color(day, maxV: maxV))
            .frame(width: cell, height: cell)
            .onHover { inside in
                guard let day else { return }
                if inside { hovered = day }
                else if hovered?.date == day.date { hovered = nil }
            }
            // Each real day is its own VoiceOver element carrying the same text the
            // hover tooltip shows, so the grid is readable without a pointer. The
            // lead-in / tail placeholders (nil) hold no data and stay hidden.
            .accessibilityElement()
            .accessibilityLabel(day.map(tooltip) ?? "")
            .accessibilityHidden(day == nil)
    }

    // MARK: color scales

    private func value(_ d: SyncDay) -> Double {
        switch mode {
        case .stored: return d.stored ? 1 : 0
        case .adjusted: return Double(d.adjusted)
        case .spend: return d.spend
        case .orders: return Double(d.orders)
        }
    }

    private func color(_ day: SyncDay?, maxV: Double) -> Color {
        let empty = Theme.Colors.gridEmpty
        guard let day else { return .clear }
        let v = value(day)
        if mode == .stored { return day.stored ? Theme.Colors.accent : empty }
        if v <= 0 { return empty }
        let base: Color
        switch mode {
        case .adjusted: base = Theme.Colors.accent
        case .spend: base = Theme.Colors.positive
        case .orders: base = Theme.Colors.information
        case .stored: base = Theme.Colors.accent
        }
        // 4 quantile-ish buckets by fraction of max
        let frac = min(1, v / maxV)
        let level = frac > 0.66 ? 1.0 : frac > 0.33 ? 0.7 : frac > 0.12 ? 0.45 : 0.25
        return base.opacity(level)
    }

    private func tooltip(_ d: SyncDay) -> String {
        let date = Format.euDate(d.date)
        switch mode {
        case .stored: return "\(date) · \(d.stored ? "synced & stored" : "not synced")"
        case .adjusted: return "\(date) · \(d.adjusted) values adjusted"
        case .spend: return "\(date) · \(Format.money(d.spend, currency: currency)) ad spend"
        case .orders: return "\(date) · \(d.orders) orders"
        }
    }

    // MARK: chrome

    private var subtitle: String {
        let t = response.totals
        switch mode {
        case .stored: return "\(t.days) days saved — a filled square means that date was synced and stored. Hover a square for its date."
        case .adjusted: return "\(t.adjusted) values adjusted across automation runs. Hover a square for its date."
        case .spend: return "\(Format.money(t.spend, currency: currency)) ad spend across \(t.days) days. Hover a square for that day's spend."
        case .orders: return "\(t.orders) orders across \(t.days) days. Hover a square for that day's orders."
        }
    }

    /// The range the grid actually draws — cached with the grid so the header
    /// matches the picture even when the 52-week window trims older data.
    private var rangeText: String? { gridRange }

    private var legend: some View {
        HStack(spacing: 6) {
            if mode == .stored {
                legendSwatch(Theme.Colors.gridEmpty)
                Text("Not synced").font(Typography.microLabel).foregroundStyle(Theme.Colors.muted)
                legendSwatch(Theme.Colors.accent)
                Text("Synced & stored").font(Typography.microLabel).foregroundStyle(Theme.Colors.muted)
            } else {
                Text("Lower").font(Typography.microLabel).foregroundStyle(Theme.Colors.muted)
                let base = mode == .spend ? Theme.Colors.positive : mode == .orders ? Theme.Colors.information : Theme.Colors.accent
                ForEach([0.25, 0.45, 0.7, 1.0], id: \.self) { legendSwatch(base.opacity($0)) }
                Text("Higher").font(Typography.microLabel).foregroundStyle(Theme.Colors.muted)
            }
        }
    }

    private func legendSwatch(_ c: Color) -> some View {
        RoundedRectangle(cornerRadius: 2).fill(c).frame(width: 11, height: 11)
    }

    private var modeTabs: some View {
        HStack(spacing: 0) {
            ForEach(Mode.allCases) { m in
                Button { mode = m } label: {
                    Text(m.rawValue)
                        .font(mode == m ? Typography.chipLabelActive : Typography.chipLabel)
                        .foregroundStyle(mode == m ? Theme.Colors.textPrimary : Theme.Colors.textSecondary)
                        .padding(.horizontal, 12).padding(.vertical, 6)
                        .background(mode == m ? Theme.Colors.surface : Color.clear,
                                    in: RoundedRectangle(cornerRadius: 6))
                        .overlay(RoundedRectangle(cornerRadius: 6)
                            .strokeBorder(mode == m ? Theme.Colors.separator : .clear, lineWidth: 1))
                }
                .buttonStyle(.plain)
            }
        }
        .padding(4)
        .background(Theme.Colors.controlTrack, in: RoundedRectangle(cornerRadius: 8))
    }
}
