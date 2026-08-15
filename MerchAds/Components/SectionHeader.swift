import SwiftUI

struct SectionHeader: View {
    let title: String
    var subtitle: String?
    var count: Int?

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: Layout.Spacing.xs) {
            Text(title)
                .font(Typography.sectionTitle)
            if let count {
                Text(Format.count(count))
                    .font(Typography.sectionCount)
                    .foregroundStyle(Theme.Colors.textSecondary)
            }
            if let subtitle {
                Text(subtitle)
                    .font(Typography.sectionSubtitle)
                    .foregroundStyle(Theme.Colors.muted)
            }
            Spacer()
        }
        .padding(.vertical, Layout.Spacing.xs)
    }
}
