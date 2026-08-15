import SwiftUI

struct StatusBadge: View {
    let text: String
    let symbol: String?
    let tint: Color

    // A tinted capsule on a tinted 16%-opacity ground has nowhere near enough
    // contrast once a table row is filled with the selection colour. On a
    // selected row the badge drops its tint and rides the row's own foreground
    // instead: white text in a translucent white capsule. See
    // SelectionAdaptive.swift.
    @Environment(\.backgroundProminence) private var prominence

    var body: some View {
        let selected = prominence.isSelectedRow
        HStack(spacing: Layout.Spacing.xxs) {
            if let symbol {
                Image(systemName: symbol)
                    .font(.caption2.weight(.semibold))
            }
            Text(text)
                .lineLimit(1)
        }
        .font(.caption2.weight(.medium))
        .padding(.horizontal, Layout.Spacing.xs)
        .padding(.vertical, Layout.Spacing.xxs)
        .background(selected ? AnyShapeStyle(.quaternary)
                             : AnyShapeStyle(tint.opacity(0.16)),
                    in: Capsule())
        .rowAdaptiveForeground(tint, selected: selected)
        .accessibilityElement(children: .combine)
    }

    static func campaignType(_ type: String) -> StatusBadge {
        StatusBadge(text: type, symbol: nil, tint: Theme.Colors.campaignType(type))
    }

    static func campaignType(_ type: String?) -> StatusBadge {
        guard let type, !type.isEmpty else {
            return StatusBadge(text: "—", symbol: nil, tint: Theme.Colors.muted)
        }
        return campaignType(type)
    }

    static func entityState(_ state: String?) -> StatusBadge {
        let normalized = state?.uppercased()
        let symbol: String?
        switch normalized {
        case "ENABLED", "ACTIVE": symbol = "checkmark.circle.fill"
        case "PAUSED": symbol = "pause.circle.fill"
        case "ARCHIVED", "DISABLED": symbol = "archivebox.fill"
        default: symbol = nil
        }
        return StatusBadge(text: state?.capitalized ?? "—", symbol: symbol,
                           tint: Theme.Colors.entityState(state))
    }
}
