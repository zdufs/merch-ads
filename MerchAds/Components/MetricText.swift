import SwiftUI

struct MoneyText: View {
    let value: Double?
    let currency: String?
    var color: Color = .primary

    @Environment(\.backgroundProminence) private var prominence

    var body: some View {
        Text(Format.money(value, currency: currency))
            .font(Typography.tableNumeral)
            .rowAdaptiveForeground(color, selected: prominence.isSelectedRow)
    }
}

struct PercentText: View {
    let value: Double?
    var breakEven: Double?
    var royaltyROI: Double?
    var label: String = "Percent"
    var color: Color?
    var digits = 1

    @Environment(\.backgroundProminence) private var prominence

    private var tier: AcosTier {
        AcosTier.select(acos: value, breakEven: breakEven, royaltyROI: royaltyROI)
    }

    // A profit/loss verdict must not ride on colour alone (WCAG 1.4.1). When a
    // break-even or ROI is attached, the ACOS resolves to profitable (green) or
    // unprofitable (red) — so those two also carry a shape cue: a check for
    // profitable, a warning triangle for unprofitable. Only the economics-attached
    // ACOS gets it (colour == nil, a threshold supplied); CVR / CTR / break-even
    // cells pass their own colour and keep the plain number they had.
    private var verdictSymbol: String? {
        guard color == nil else { return nil }
        switch tier {
        case .profitable: return "checkmark"
        case .unprofitable: return "exclamationmark.triangle.fill"
        default: return nil
        }
    }

    // On a selected row the tier colour is dropped for legibility. The heavier
    // weight and the shape cue survive, so the verdict still reads.
    var body: some View {
        HStack(spacing: 3) {
            Text(Format.percent(value, digits: digits))
                .font(Typography.tableNumeral)
                .fontWeight(tier == .elevated || tier == .high || tier == .unprofitable
                            ? .medium : .regular)
            if let verdictSymbol {
                Image(systemName: verdictSymbol)
                    .font(.caption2)
                    .imageScale(.small)
                    .accessibilityHidden(true)
            }
        }
        .rowAdaptiveForeground(color ?? tier.color,
                               selected: prominence.isSelectedRow)
        .help(color == nil ? tier.help : label)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(label)
        .accessibilityValue(accessibleValue)
    }

    // The number, plus the tier verdict when the cell carries one — so VoiceOver
    // announces "ACOS, 45%, unprofitable" instead of the bare number whose colour a
    // screen-reader user never sees. Manually-coloured cells (CVR / CTR / break-even)
    // have no verdict and announce just the number.
    private var accessibleValue: String {
        let pct = Format.percent(value, digits: digits)
        guard color == nil else { return pct }
        switch tier {
        case .profitable: return "\(pct), profitable"
        case .unprofitable: return "\(pct), unprofitable"
        case .elevated: return "\(pct), elevated"
        case .high: return "\(pct), high"
        default: return pct
        }
    }
}

struct CountText: View {
    let value: Int?

    var body: some View {
        Text(Format.count(value))
            .font(Typography.tableNumeral)
    }
}
