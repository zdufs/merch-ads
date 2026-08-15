import SwiftUI

/// A compact, collapsed-by-default guide for where to get one kind of import
/// file. Each Import sub-tab (New Designs, Sales, Ads) passes its own numbered
/// steps — nothing here is specific to one file kind.
struct HowToGet: View {
    let title: String
    let steps: [String]

    @State private var isExpanded = false

    var body: some View {
        DisclosureGroup(isExpanded: $isExpanded) {
            VStack(alignment: .leading, spacing: Layout.Spacing.xs) {
                ForEach(Array(steps.enumerated()), id: \.offset) { index, step in
                    HStack(alignment: .top, spacing: Layout.Spacing.xs) {
                        Text("\(index + 1).")
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(Theme.Colors.muted)
                        Text(step)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            .padding(.top, Layout.Spacing.xs)
            .padding(.leading, Layout.Spacing.xs)
        } label: {
            Label(title, systemImage: "questionmark.circle")
                .font(.caption.weight(.medium))
                .foregroundStyle(Theme.Colors.textSecondary)
        }
        .padding(Layout.Spacing.sm)
        .mdCard()
    }
}

#Preview {
    HowToGet(title: "How to get this file", steps: [
        "Amazon Merch on Demand → Analyze → Products.",
        "Set the date range (From / To); Marketplace: All.",
        "Click Download CSV.",
    ])
    .padding()
    .frame(width: 420)
}
