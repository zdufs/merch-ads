import SwiftUI

/// The "?" beside a page title. Click it (or press ⌘/) for the full
/// description and instructions for that screen.
///
/// The glyph is deliberately a step smaller than the 26pt page heading and
/// baseline-aligned with it: a question mark at title size reads as a second
/// heading and fights the one that matters.
struct HelpButton: View {
    let screen: Screen
    @State private var showing = false

    var body: some View {
        Button {
            showing.toggle()
        } label: {
            Image(systemName: "questionmark.circle")
                .font(.title3)
                .foregroundStyle(showing ? Theme.Colors.accent : Theme.Colors.muted)
                .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .keyboardShortcut("/", modifiers: .command)
        .help("What is \(screen.title) for, and how do I use it? (⌘/)")
        .accessibilityLabel("Help for \(screen.title)")
        .popover(isPresented: $showing, arrowEdge: .bottom) {
            ScreenHelpCard(screen: screen) { showing = false }
        }
    }
}

/// The popover body: summary, where the numbers come from, numbered steps, then
/// the caveats. Fixed width so the prose keeps a readable measure, and capped in
/// height so a long screen scrolls instead of running off the display.
struct ScreenHelpCard: View {
    let screen: Screen
    var dismiss: (() -> Void)?

    private var help: ScreenHelp { screen.help }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: Layout.Spacing.lg) {
                    Text(help.summary)
                        .font(Typography.cardBody)
                        .foregroundStyle(Theme.Colors.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)

                    section("Where the numbers come from", symbol: "cylinder.split.1x2") {
                        Text(help.source)
                            .font(Typography.cardCaption)
                            .foregroundStyle(Theme.Colors.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }

                    section("How to use it", symbol: "list.number") {
                        VStack(alignment: .leading, spacing: Layout.Spacing.sm) {
                            ForEach(Array(help.steps.enumerated()), id: \.offset) { index, step in
                                numberedRow(index + 1, step)
                            }
                        }
                    }

                    if !help.notes.isEmpty {
                        section("Good to know", symbol: "exclamationmark.circle") {
                            VStack(alignment: .leading, spacing: Layout.Spacing.sm) {
                                ForEach(Array(help.notes.enumerated()), id: \.offset) { _, note in
                                    bulletRow(note)
                                }
                            }
                        }
                    }
                }
                .padding(Layout.Spacing.lg)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .frame(width: 440)
        .frame(maxHeight: 560)
        .background(Theme.Colors.surface)
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: Layout.Spacing.sm) {
            Image(systemName: screen.icon)
                .font(Typography.cardBody)
                .foregroundStyle(Theme.Colors.accent)
            Text(screen.title)
                .font(Typography.cardTitle)
                .foregroundStyle(Theme.Colors.textPrimary)
            Spacer(minLength: Layout.Spacing.sm)
            if let dismiss {
                Button("Done", action: dismiss)
                    .buttonStyle(.borderless)
                    .font(Typography.cardCaption)
            }
        }
        .padding(.horizontal, Layout.Spacing.lg)
        .padding(.vertical, Layout.Spacing.md)
    }

    private func section<Content: View>(_ title: String, symbol: String,
                                        @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.sm) {
            HStack(spacing: 6) {
                Image(systemName: symbol)
                    .font(Typography.microLabel)
                Text(title.uppercased())
                    .font(Typography.cardLabel)
                    .tracking(0.5)
            }
            .foregroundStyle(Theme.Colors.muted)
            content()
        }
    }

    private func numberedRow(_ number: Int, _ text: String) -> some View {
        HStack(alignment: .top, spacing: Layout.Spacing.sm) {
            Text("\(number)")
                .font(Typography.cardLabel.monospacedDigit())
                .foregroundStyle(Theme.Colors.accent)
                .frame(width: 18, height: 18)
                .background(Theme.Colors.accentSoft, in: Circle())
            Text(text)
                .font(Typography.cardCaption)
                .foregroundStyle(Theme.Colors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func bulletRow(_ text: String) -> some View {
        HStack(alignment: .top, spacing: Layout.Spacing.sm) {
            Circle()
                .fill(Theme.Colors.caution)
                .frame(width: 5, height: 5)
                .padding(.top, 6)
                .padding(.leading, 6)
            Text(text)
                .font(Typography.cardCaption)
                .foregroundStyle(Theme.Colors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

#Preview {
    ScreenHelpCard(screen: .dashboard)
}
