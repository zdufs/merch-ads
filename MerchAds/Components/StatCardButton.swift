import SwiftUI

/// A StatCard that acts as the screen's tab switcher: clicking it selects the
/// list it summarizes, and the selected card carries a border so the active tab
/// stays legible without a separate segmented control.
struct StatCardButton: View {
    let title: String
    let value: String
    var delta: DashboardDelta?
    var tint: Color = .primary
    var symbol: String?
    var subtitle: String?
    var glassTint: Color?
    var isSelected = false
    var helpText: String?
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            StatCard(title: title, value: value, delta: delta, tint: tint,
                     symbol: symbol, subtitle: subtitle)
                .contentShape(.rect(cornerRadius: MDCard.radius))
        }
        .buttonStyle(.plain)
        .mdCard()
        // Same radius as the card underneath — a wider one leaves the ring's
        // corners floating off the card edge.
        .overlay(
            RoundedRectangle(cornerRadius: MDCard.radius, style: .continuous)
                .strokeBorder(strokeColor, lineWidth: 1)
        )
        .help(helpText ?? "")
        .accessibilityAddTraits(isSelected ? .isSelected : [])
    }

    private var strokeColor: Color {
        isSelected ? (glassTint ?? Color.accentColor).opacity(0.55) : .clear
    }
}
