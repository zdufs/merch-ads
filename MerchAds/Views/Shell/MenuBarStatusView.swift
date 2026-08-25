import SwiftUI

/// The menu bar popover: selected market's trailing-30 headline at a glance,
/// with the KILL state front and center when it matters.
struct MenuBarStatusView: View {
    @Environment(AppState.self) private var appState
    @Environment(\.openWindow) private var openWindow
    @AppStorage(AppSettings.appearanceKey) private var appearanceRaw = AppAppearance.system.rawValue

    @State private var metrics: MetricsResponse?
    @State private var isLoading = false
    @State private var loadError: String?

    var body: some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.sm) {
            HStack {
                Text("Merch Ads · \(appState.selectedMarket)")
                    .font(.headline)
                Spacer()
                Button {
                    Task { await load() }
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .buttonStyle(.borderless)
                .accessibilityLabel("Refresh")
                .help("Refetch this market's numbers")
            }

            if appState.killActive {
                StatusBadge(text: "KILL · all writes frozen",
                            symbol: "exclamationmark.octagon.fill",
                            tint: Theme.Colors.critical)
            }
            if appState.approvalRequired {
                StatusBadge(text: "Approval gate on · phase2 waits",
                            symbol: "checklist", tint: Theme.Colors.caution)
            }

            if let month = metrics?.month {
                Text(Format.monthName(month.month))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Grid(alignment: .leading, horizontalSpacing: Layout.Spacing.md,
                     verticalSpacing: Layout.Spacing.xs) {
                    GridRow {
                        menuMetric("Spend") { MoneyText(value: month.spend, currency: metrics?.currency) }
                        menuMetric("Sales") { MoneyText(value: month.sales, currency: metrics?.currency) }
                    }
                    GridRow {
                        menuMetric("ACOS") { PercentText(value: month.acos, label: "ACOS") }
                        menuMetric("Orders") { CountText(value: month.orders) }
                    }
                }
                .padding(Layout.Spacing.sm)
                .mdCard()
                if let ytd = metrics?.ytd {
                    // Say when the "year" is not a year. The Dashboard already
                    // marks a short window partial; without this the menu bar
                    // was the one place a two-month figure read as twelve.
                    Text("YTD\(ytd.partialLabel.map { " (\($0))" } ?? ""): \(Format.money(ytd.spend, currency: metrics?.currency)) → \(Format.money(ytd.sales, currency: metrics?.currency)) · ACOS \(Format.percent(ytd.acos))")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                if let daily = metrics?.daily {
                    Text("latest day: \(Format.money(daily.spend, currency: metrics?.currency)) spend · still settling")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            } else if let trailing = metrics?.trailing30 {
                // no daily history banked yet — fall back to the engine's basis
                Grid(alignment: .leading, horizontalSpacing: Layout.Spacing.md,
                     verticalSpacing: Layout.Spacing.xs) {
                    GridRow {
                        menuMetric("Spend") { MoneyText(value: trailing.spend, currency: metrics?.currency) }
                        menuMetric("Sales") { MoneyText(value: trailing.sales, currency: metrics?.currency) }
                    }
                    GridRow {
                        menuMetric("ACOS") { PercentText(value: trailing.acos, label: "ACOS") }
                        menuMetric("Orders") { CountText(value: trailing.orders) }
                    }
                }
                .padding(Layout.Spacing.sm)
                .mdCard()
                Text("trailing 30 days · as of \(Format.euDate(trailing.asOf))")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            } else if isLoading {
                ProgressView().controlSize(.small)
            }

            if let loadError {
                Label(loadError, systemImage: "exclamationmark.triangle")
                    .font(.caption2)
                    .foregroundStyle(Theme.Colors.caution)
                    .lineLimit(2)
            }

            Divider()
            Button("Open Merch Ads") {
                // openWindow reopens the window if it was fully closed; the
                // AppKit fallback covers the usual minimized / behind cases,
                // since this popover is hosted outside the SwiftUI scene and
                // openWindow may not resolve from here.
                openWindow(id: "main")
                NSApp.activate(ignoringOtherApps: true)
                MenuBarController.bringMainWindowFront()
            }
        }
        .padding(Layout.Spacing.sm)
        .frame(width: 260)
        // Match the main window's Appearance preference so the panel's palette
        // and its chrome agree (the panel is its own hosting view, not a child
        // of ContentView, so it needs the setting applied directly).
        .preferredColorScheme(AppAppearance.stored(appearanceRaw).colorScheme)
        .task(id: appState.viewKey) { await load() }
    }

    private func menuMetric<Content: View>(_ title: String,
                                           @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.xxs) {
            Text(title).font(.caption2).foregroundStyle(.secondary)
            content()
        }
    }

    private func load() async {
        isLoading = true
        defer { if !Task.isCancelled { isLoading = false } }
        loadError = nil
        do {
            let bridge = try appState.makeBridge()
            let response = try await bridge.call(MetricsResponse.self, ["metrics"],
                                                 market: appState.selectedMarket)
            guard !Task.isCancelled else { return }
            metrics = response
        } catch {
            guard !Task.isCancelled else { return }
            // keep the last good numbers on screen; just say the refresh failed
            loadError = "refresh failed — \(error.localizedDescription)"
        }
    }
}
