import SwiftUI

/// Renders a preview row's per-condition debug trace (why it fired / was skipped).
/// Additive: only shown when the engine attached a `trace`. Each value is
/// formatted from the field the condition names — see `value(_:condition:currency:)`
/// for the (deliberately conservative) unit rules.
struct DebugTraceBlock: View {
    let trace: [ConditionTrace]
    var currency: String? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.xs) {
            Text("Debug trace")
                .font(.caption).foregroundStyle(.secondary)
            ForEach(trace) { c in
                LabeledContent {
                    HStack(spacing: 6) {
                        Text(Self.value(c.actual, condition: c.condition, currency: currency))
                        Text("vs").foregroundStyle(.secondary)
                        Text(Self.value(c.threshold, condition: c.condition, currency: currency))
                        Image(systemName: c.pass ? "checkmark.circle.fill" : "xmark.circle")
                            .foregroundStyle(c.pass ? Theme.Colors.positive : Theme.Colors.caution)
                            .font(.caption2)
                            .accessibilityLabel(c.pass ? "condition met" : "condition not met")
                            .help(c.pass ? "This condition matched" : "This condition did not match")
                    }
                    .monospacedDigit()
                } label: {
                    Text(c.condition)
                }
                .font(.caption)
            }
        }
        .padding(Layout.Spacing.sm)
        .background(Theme.Colors.surface, in: .rect(cornerRadius: Layout.Radius.medium))
    }

    // The engine authors these condition strings, so the Swift side only claims
    // a unit when the condition names a field it actually knows. It used to
    // guess from words like "floor"/"ceiling"/"target", which turned a DSL
    // "bid ceiling 0.60" into "60.0%" and an ASIN "target" into a percentage.
    // Anything unrecognized now falls through to a plain number.
    private static let fractionFields = [
        "acos", "cvr", "ctr", "royalty_roi", "break_even",
        "conversion_rate", "click_through_rate",
    ]
    private static let countFields = [
        "click", "order", "impression", "unit", "lifetime_sales", "conversion",
    ]
    private static let moneyFields = [
        "bid", "spend", "sales", "royalty", "profit", "budget", "cpc", "cpo", "price",
    ]

    static func value(_ v: Double?, condition: String, currency: String?) -> String {
        guard let v else { return "—" }
        let name = condition.lowercased()
        if fractionFields.contains(where: { name.contains($0) }) {
            return String(format: "%.1f%%", v * 100)
        }
        if countFields.contains(where: { name.contains($0) }) {
            return String(Int(v.rounded()))
        }
        if moneyFields.contains(where: { name.contains($0) }) {
            return Format.money(v, currency: currency)
        }
        // Unknown field — show the raw number rather than dressing it up in a
        // unit it may not have.
        if v == v.rounded(), abs(v) < 1e15 { return String(Int(v)) }
        return String(format: "%.2f", v)
    }
}

/// A "Why" table cell: the human reason, with the structured debug trace revealed
/// on hover via a popover (a small ⓘ appears when a trace is present).
struct TraceReasonCell: View {
    let reason: String
    let trace: [ConditionTrace]?
    @State private var showing = false

    var body: some View {
        HStack(spacing: 4) {
            Text(reason).foregroundStyle(.secondary)
            if let trace, !trace.isEmpty {
                Button {
                    showing.toggle()
                } label: {
                    Image(systemName: "info.circle").font(.caption2)
                }
                .buttonStyle(.plain)
                .foregroundStyle(.secondary)
                .accessibilityLabel("Show debug trace")
                .help("Show the per-condition debug trace")
                .popover(isPresented: $showing, arrowEdge: .bottom) {
                    DebugTraceBlock(trace: trace)
                        .padding(Layout.Spacing.sm)
                        .frame(minWidth: 220)
                }
            }
        }
    }
}
