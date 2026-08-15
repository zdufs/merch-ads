import SwiftUI

struct StatCard: View {
    let title: String
    let value: String
    var delta: DashboardDelta?
    var tint: Color = .primary
    var symbol: String?
    var subtitle: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            // The symbol is decorative — it repeats the title, so it stays out
            // of the combined accessibility element.
            HStack(spacing: 5) {
                if let symbol {
                    Image(systemName: symbol)
                        .font(Typography.cardLabel)
                        .imageScale(.small)
                        .foregroundStyle(Theme.Colors.muted)
                        .accessibilityHidden(true)
                }
                Text(title.uppercased())
                    .font(Typography.cardLabel)
                    .tracking(0.55)
                    .foregroundStyle(Theme.Colors.muted)
            }
            Text(value)
                .font(Typography.cardValue)
                .tracking(-0.2)
                .monospacedDigit()
                .foregroundStyle(tint == .primary ? Theme.Colors.textPrimary : tint)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
            if let delta {
                Label(delta.displayText, systemImage: delta.symbol)
                    .font(Typography.cardCaptionEmphasis)
                    .foregroundStyle(deltaColor(delta))
            } else if let subtitle {
                Text(subtitle)
                    .font(Typography.cardCaption)
                    .foregroundStyle(Theme.Colors.muted)
            }
        }
        .padding(.vertical, 16)
        .padding(.horizontal, 18)
        .frame(maxWidth: .infinity, minHeight: 92, alignment: .topLeading)
        .accessibilityElement(children: .combine)
    }

    private func deltaColor(_ delta: DashboardDelta) -> Color {
        switch delta.tone {
        case .positive: Theme.Colors.positive
        case .negative: Theme.Colors.critical
        case .neutral: .secondary
        }
    }
}
