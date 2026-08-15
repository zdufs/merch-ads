import SwiftUI

/// MerchDash-style in-content page header: a 28px bold title with an optional
/// gray subtitle, and an optional trailing accessory (e.g. a profile pill).
///
/// Pass `help:` to put a "?" beside the title. It opens the full description and
/// instructions for that screen (`Screen.help`). Every top-level screen sets it;
/// drill-downs inherit the parent screen's help rather than repeating it.
struct PageHeader<Trailing: View>: View {
    let title: String
    var subtitle: String? = nil
    var help: Screen? = nil
    @ViewBuilder var trailing: () -> Trailing

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 3) {
                HStack(alignment: .firstTextBaseline, spacing: Layout.Spacing.xs) {
                    Text(title)
                        .font(Typography.pageHeading)
                        .foregroundStyle(Theme.Colors.textPrimary)
                    if let help {
                        HelpButton(screen: help)
                    }
                }
                if let subtitle {
                    Text(subtitle)
                        .font(Typography.pageSubtitle)
                        .foregroundStyle(Theme.Colors.muted)
                        // Keep the header a stable one line — a long subtitle on a
                        // narrow detail column otherwise wraps and nudges content down.
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
            }
            Spacer()
            trailing()
        }
        .padding(.horizontal, Layout.Spacing.lg)
        .padding(.top, Layout.Spacing.lg)
        .padding(.bottom, Layout.Spacing.md)
    }
}

extension PageHeader where Trailing == EmptyView {
    init(title: String, subtitle: String? = nil, help: Screen? = nil) {
        self.init(title: title, subtitle: subtitle, help: help) { EmptyView() }
    }
}
