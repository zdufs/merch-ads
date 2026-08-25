import SwiftUI
import Charts

// Campaign browser sheets and history charts

struct BidHistoryView: View {
    @Environment(AppState.self) private var appState
    let target: TargetRow
    let targetId: String
    let currency: String?
    var editBid: (() -> Void)? = nil

    @State private var changes: [BidChange] = []
    @State private var history: HistoryResponse?
    @State private var loadError: String?
    @State private var isLoading = false

    var body: some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.sm) {
            Text("Bid History")
                .font(.headline)
            Text("\(target.targeting ?? "target") · \(targetId)")
                .font(.caption)
                .foregroundStyle(.secondary)
            if let editBid {
                Button("Edit Bid…", action: editBid)
                    .buttonStyle(.bordered)
            }

            if isLoading {
                ProgressView()
                    .frame(maxWidth: .infinity)
            } else if let loadError {
                Text(loadError)
                    .font(.caption)
                    .foregroundStyle(Theme.Colors.critical)
            } else if changes.isEmpty {
                Text("No bid changes logged for this target.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                if chartPoints.count > 1 {
                    Chart(chartPoints, id: \.date) { point in
                        LineMark(
                            x: .value("Date", point.date),
                            y: .value("Bid", point.bid)
                        )
                        .interpolationMethod(.stepEnd)
                        .foregroundStyle(Theme.Colors.neutralAccent)
                        PointMark(
                            x: .value("Date", point.date),
                            y: .value("Bid", point.bid)
                        )
                        .foregroundStyle(Theme.Colors.neutralAccent)
                    }
                    .merchAdsChartStyle(height: Layout.ChartHeight.compact)
                }
                List(changes.reversed()) { change in
                    VStack(alignment: .leading, spacing: Layout.Spacing.xxs) {
                        HStack {
                            Text(shortDate(change.at))
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            Spacer()
                            Text("\(Format.money(change.old, currency: currency)) → \(Format.money(change.new, currency: currency))")
                                .font(.caption.weight(.medium))
                                .monospacedDigit()
                                .foregroundStyle(direction(change))
                        }
                        if let reason = change.reason, !reason.isEmpty {
                            Text(reason)
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .padding(.vertical, Layout.Spacing.xxs)
                }
                .listStyle(.inset)
            }

            // did the bid moves HELP? — the target's banked perf drift below the timeline
            if let history, history.points.count > 1 {
                Divider()
                Text("Performance")
                    .font(.subheadline.weight(.semibold))
                // Capped, not just floored. With no bid changes logged there is
                // no list above to take up the slack, so an uncapped chart grew
                // to the full inspector height and pushed its caption off the
                // bottom of the window.
                HistoryChart(points: history.points, currency: currency)
                    .frame(minHeight: Layout.ChartHeight.compact,
                           maxHeight: Layout.ChartHeight.standard)
                HistorySeriesCaption(history: history)
            }
            Spacer()
        }
        .padding(Layout.Spacing.sm)
        .task(id: targetId) { await load() }
    }

    private var chartPoints: [(date: Date, bid: Double)] {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withFullDate, .withTime, .withColonSeparatorInTime]
        return changes.compactMap { change in
            guard let bid = change.new else { return nil }
            let date = formatter.date(from: change.at) ?? Format.date(String(change.at.prefix(10)))
            guard let date else { return nil }
            return (date, bid)
        }
    }

    private func direction(_ change: BidChange) -> Color {
        guard let old = change.old, let new = change.new else { return .primary }
        return new > old ? Theme.Colors.positive
            : (new < old ? Theme.Colors.critical : .primary)
    }

    private func shortDate(_ timestamp: String) -> String {
        Format.euDateTime(timestamp)
    }

    private func load() async {
        isLoading = true
        defer { if !Task.isCancelled { isLoading = false } }
        loadError = nil
        do {
            let bridge = try appState.makeBridge()
            async let changesCall = bridge.call(
                BidHistoryResponse.self, ["bidhistory", "--target", targetId],
                market: appState.selectedMarket)
            async let historyCall = bridge.call(
                HistoryResponse.self, ["history", "--target", targetId],
                market: appState.selectedMarket)
            let response = try await changesCall
            guard !Task.isCancelled else { return }
            changes = response.changes
            history = try? await historyCall   // best-effort — chart hides if absent
        } catch {
            guard !Task.isCancelled else { return }
            loadError = error.localizedDescription
        }
    }
}

// MARK: - Money entry sheet (bids, budgets — one implementation)

/// The one "type an amount, confirm, write it to Amazon" sheet.
///
/// Every money-entry flow in the app (single bid, bulk bid, daily budget) used
/// to re-implement comma→dot parsing, the minimum-value gate, the saving flag
/// and the Cancel/confirm row. They drifted apart, and the drift cost real
/// writes: `onSubmit` had no saving guard, so two quick Returns fired the live
/// call twice. One component, one `submit()`, one guard.
///
/// Only `title`, `current`, `minimum` and `onSave` are required — the rest
/// shapes the sheet for a particular caller.
struct MoneyEntrySheet: View {
    @Environment(\.dismiss) private var dismiss

    let title: String
    /// Prefills the field. `nil` leaves it empty (bulk edits over mixed values).
    let current: Double?
    /// Amounts below this are rejected — the confirm button stays disabled.
    let minimum: Double
    var subtitle: String? = nil
    /// Optional "what it is now" row above the field.
    var currentLabel: String? = nil
    var currentValue: String? = nil
    /// A warning shown above the field (e.g. a campaign pinned at its budget).
    var caution: String? = nil
    /// A footnote shown below the field.
    var note: String? = nil
    var fieldLabel: String = "Amount"
    var prompt: String = "0.00"
    var confirmLabel: String = "Save"
    var fieldWidth: CGFloat = 140
    var sheetWidth: CGFloat = 340
    let onSave: (Double) async -> Void

    @State private var text = ""
    @State private var saving = false

    private var amount: Double? {
        Double(text.replacingOccurrences(of: ",", with: "."))
    }

    private var isValid: Bool {
        guard let amount else { return false }
        return amount >= minimum
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.md) {
            Text(title)
                .font(.headline)
            if let subtitle {
                Text(subtitle)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            if let currentLabel {
                LabeledContent(currentLabel, value: currentValue ?? "unknown")
            }
            if let caution {
                Label(caution, systemImage: "gauge.high")
                    .font(.caption)
                    .foregroundStyle(Theme.Colors.caution)
            }
            TextField(fieldLabel, text: $text, prompt: Text(prompt))
                .textFieldStyle(.roundedBorder)
                .frame(width: fieldWidth)
                .onSubmit { submit() }
            if let note {
                Text(note)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button(saving ? "Saving…" : confirmLabel) { submit() }
                    .keyboardShortcut(.defaultAction)
                    .buttonStyle(.borderedProminent)
                    .disabled(saving || !isValid)
            }
        }
        .padding(Layout.Spacing.md)
        .frame(width: sheetWidth)
        .onAppear {
            if let current { text = String(format: "%.2f", current) }
        }
    }

    /// The `saving` guard is the whole point: the confirm button disables itself
    /// while the write is in flight, but a second Return in the text field would
    /// otherwise fire a second live write with the same stale `--prev`.
    private func submit() {
        guard !saving, let amount, amount >= minimum else { return }
        saving = true
        Task {
            await onSave(amount)
            dismiss()
        }
    }
}

// MARK: - Performance history (banked snapshots for one campaign/ad group/target)

/// Spend/sales over time — "did last week's change help?" for any entity the
/// `history` endpoint knows. The endpoint returns true per-day totals where
/// they are banked and trailing-30 snapshots where they are not, so the caption
/// under the chart reads `basis` and says which one this is.
struct PerformanceHistorySheet: View {
    @Environment(AppState.self) private var appState
    @Environment(\.dismiss) private var dismiss
    let title: String
    let args: [String]
    let currency: String?

    @State private var history: HistoryResponse?
    @State private var loadError: String?
    @State private var isLoading = false

    var body: some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.sm) {
            HStack {
                VStack(alignment: .leading, spacing: Layout.Spacing.xxs) {
                    Text("Performance History")
                        .font(.headline)
                    Text(title)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                Spacer()
                Button("Done") { dismiss() }
                    .keyboardShortcut(.cancelAction)
            }
            if isLoading {
                ProgressView("Loading banked history…")
                    .frame(maxWidth: .infinity, minHeight: 180)
            } else if let loadError {
                Text(loadError).font(.caption).foregroundStyle(Theme.Colors.critical)
            } else if let history, history.points.count > 1 {
                HistoryChart(points: history.points, currency: currency)
                HistorySeriesCaption(history: history)
            } else {
                Text("Not enough banked history yet — points accrue nightly.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, minHeight: 120)
            }
        }
        .padding(Layout.Spacing.md)
        .frame(width: 560)
        .task { await load() }
    }

    private func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            history = try await appState.makeBridge()
                .call(HistoryResponse.self, args, market: appState.selectedMarket)
        } catch {
            loadError = error.localizedDescription
        }
    }
}

/// Says what the line above it actually is.
///
/// A true per-day series and a trailing-30 snapshot series look identical on a
/// chart and mean completely different things. One shows days, the other shows
/// drift. Labelling one as the other misleads whoever is making a money
/// decision from it, so this caption is the deliverable, not decoration.
///
/// The span line is here because a market mid-backfill returns a short series
/// honestly. Six points should read as six days banked, not as a quiet month.
struct HistorySeriesCaption: View {
    let history: HistoryResponse

    private var meaning: String {
        history.isDaily
            ? "Each point is one real day."
            : "Each point is a trailing-30 total, not one day."
    }

    private var span: String? {
        guard let days = history.daysBanked, days > 0 else { return nil }
        let unit = days == 1 ? "day" : "days"
        guard let first = history.first, let last = history.last else {
            return "\(days) \(unit) banked"
        }
        return "\(days) \(unit) banked · \(Format.euDate(first)) → \(Format.euDate(last))"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.xxs) {
            Text(meaning)
            if let span {
                Text(span).monospacedDigit()
            }
        }
        .font(Typography.microLabel)
        .foregroundStyle(.secondary)
        .accessibilityElement(children: .combine)
    }
}

/// Reusable spend/sales line chart over banked snapshot dates.
struct HistoryChart: View {
    let points: [HistoryPoint]
    let currency: String?
    @State private var hoverDate: Date?

    private struct Series: Identifiable {
        let name: String
        let date: Date
        let value: Double
        var id: String { "\(name)|\(date.timeIntervalSince1970)" }
    }

    private var series: [Series] {
        points.compactMap { point -> [Series]? in
            guard let date = Format.date(point.date) else { return nil }
            return [Series(name: "Sales", date: date, value: point.sales),
                    Series(name: "Spend", date: date, value: point.spend)]
        }.flatMap { $0 }
    }

    private var parsedPoints: [(date: Date, point: HistoryPoint)] {
        points.compactMap { point in Format.date(point.date).map { ($0, point) } }
    }

    /// Point nearest the hovered x-position. Dates are parsed once here, not
    /// twice per comparison inside a 60Hz onContinuousHover closure (the
    /// MetricChipsTrend pattern).
    private var hovered: HistoryPoint? {
        guard let hoverDate else { return nil }
        return parsedPoints.min {
            abs($0.date.timeIntervalSince(hoverDate)) < abs($1.date.timeIntervalSince(hoverDate))
        }?.point
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.xxs) {
            if let hovered {
                Text("\(hovered.date): \(Format.money(hovered.spend, currency: currency)) spend · \(Format.money(hovered.sales, currency: currency)) sales · \(Format.percent(hovered.acos)) ACOS · \(Format.count(hovered.orders)) orders")
                    .font(.caption)
                    .monospacedDigit()
                    .foregroundStyle(.secondary)
            } else {
                Text("hover for exact values")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            Chart(series) { item in
                LineMark(x: .value("Date", item.date),
                         y: .value("Amount", item.value))
                    .foregroundStyle(by: .value("Series", item.name))
                PointMark(x: .value("Date", item.date),
                          y: .value("Amount", item.value))
                    .foregroundStyle(by: .value("Series", item.name))
                    .symbolSize(18)
            }
            .chartForegroundStyleScale(["Sales": Theme.Colors.chartSales,
                                        "Spend": Theme.Colors.chartSpend])
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
                                    if let date: Date = proxy.value(atX: x) {
                                        hoverDate = date
                                    }
                                }
                            case .ended:
                                hoverDate = nil
                            }
                        }
                }
            }
            .merchAdsChartStyle(height: Layout.ChartHeight.standard)
        }
    }
}

// MARK: - Shared badges
