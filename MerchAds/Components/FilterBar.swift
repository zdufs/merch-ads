import SwiftUI

struct FilterBar<Content: View, Trailing: View>: View {
    @ViewBuilder let content: Content
    @ViewBuilder let trailing: Trailing

    init(@ViewBuilder content: () -> Content,
         @ViewBuilder trailing: () -> Trailing = { EmptyView() }) {
        self.content = content()
        self.trailing = trailing()
    }

    var body: some View {
        HStack(spacing: Layout.Spacing.sm) {
            content
            Spacer(minLength: Layout.Spacing.md)
            trailing
        }
        .controlSize(.small)
        .padding(.horizontal, Layout.Spacing.sm)
        .padding(.vertical, Layout.Spacing.xs)
        .background(Theme.Colors.surface)
    }
}
