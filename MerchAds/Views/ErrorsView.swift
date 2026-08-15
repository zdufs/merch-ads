import SwiftUI

/// One combined home for everything wrong: transient app-side failures
/// (Stream A, `IssueCenter`) merged with live conditions derived from health,
/// econ-gate, and alerts (Stream B, `IssueDerivation`). Sorted most-severe
/// first; app failures are dismissable, live conditions clear on their own.
///
/// "Fix all safe" only *fires* the genuinely safe remedy — a data pull for each
/// stale market, through the same KILL-gated coordinator path the Actions
/// screen uses. Operator-gated fixes (KILL off, approval off, econ remap) are
/// never fired: each carries a Copy-command button so the operator runs it via
/// `!`. Judgment/setup issues link to the screen where a human decides.
struct ErrorsView: View {
    @Environment(AppState.self) private var appState

    @State private var expanded: Set<String> = []
    @State private var fixing = false               // a Fix-all-safe run in flight
    @State private var fixProgress: String?         // "Re-pulling UK…"
    @State private var fixMessage: String?          // result / guidance banner
    @State private var perRowFixing: Set<String> = []
    @State private var copiedID: String?
    @State private var showFixAllConfirm = false

    private var root: String { AppSettings.engineRoot.path }

    private var allIssues: [AppIssue] {
        var combined: [AppIssue] = appState.liveIssues
        combined.append(contentsOf: appState.issues.appIssues)
        combined.sort { (lhs: AppIssue, rhs: AppIssue) -> Bool in
            if lhs.severity != rhs.severity {
                return lhs.severity > rhs.severity
            }
            return lhs.timestamp > rhs.timestamp
        }
        return combined
    }

    private var hasAppIssues: Bool { !appState.issues.appIssues.isEmpty }

    /// Distinct markets with a safe pull remedy — what "Fix all safe" runs.
    private var pullMarkets: [String] {
        var seen: [String] = []
        for issue in allIssues {
            if case .pull(let m)? = issue.fix, !seen.contains(m) { seen.append(m) }
        }
        return seen
    }

    /// Distinct operator-gated command fragments present right now.
    private var stagedCommands: [String] {
        var seen: [String] = []
        for issue in allIssues {
            if case .operatorCommand(let frag)? = issue.fix, !seen.contains(frag) { seen.append(frag) }
        }
        return seen
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Layout.Spacing.lg) {
                PageHeader(title: "Errors", subtitle: "issues across the engine and app", help: .errors)
                if let fixProgress {
                    banner(fixProgress, systemImage: "arrow.triangle.2.circlepath", tint: Theme.Colors.information, spinner: true)
                } else if let fixMessage {
                    banner(fixMessage, systemImage: "info.circle.fill", tint: Theme.Colors.information) { self.fixMessage = nil }
                }

                if allIssues.isEmpty {
                    emptyState
                        .frame(maxWidth: .infinity, minHeight: 320)
                } else {
                    ForEach(IssueSeverity.allCases.reversed(), id: \.self) { severity in
                        let group = allIssues.filter { $0.severity == severity }
                        if !group.isEmpty {
                            section(severity, issues: group)
                        }
                    }
                }
            }
            .padding(Layout.Spacing.lg)
        }
        .toolbar {
            if !pullMarkets.isEmpty {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        showFixAllConfirm = true
                    } label: {
                        Label("Fix all safe", systemImage: "wrench.and.screwdriver")
                    }
                    .buttonStyle(.bordered)
                    .disabled(fixing)
                    .help("Re-pull every stale market. Operator-gated and judgment fixes are left for you.")
                }
            }
            if hasAppIssues {
                ToolbarItem(placement: .secondaryAction) {
                    Button("Clear resolved", systemImage: "checkmark.circle") {
                        appState.issues.clearApp()
                    }
                    .help("Dismiss all captured app failures (live conditions stay until they clear)")
                }
            }
        }
        .confirmationDialog("Fix all safe", isPresented: $showFixAllConfirm, titleVisibility: .visible) {
            Button("Re-pull \(pullMarkets.count) market\(pullMarkets.count == 1 ? "" : "s")") {
                Task { await fixAllSafe() }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text(confirmMessage)
        }
    }

    private var confirmMessage: String {
        if appState.killActive {
            return "KILL is active — data pulls are blocked. Clear KILL first (Copy the command on the KILL row), then run this again."
        }
        var msg = "Runs a live Amazon pull for: \(pullMarkets.joined(separator: ", ")). Each takes a few minutes."
        if !stagedCommands.isEmpty {
            msg += "\n\n\(stagedCommands.count) operator-gated fix\(stagedCommands.count == 1 ? "" : "es") won't be run — use the Copy fix command buttons and run them yourself."
        }
        return msg
    }

    // MARK: - sections & rows

    private func section(_ severity: IssueSeverity, issues: [AppIssue]) -> some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.xs) {
            HStack(spacing: Layout.Spacing.xs) {
                Image(systemName: severity.icon).foregroundStyle(severity.tint)
                Text(severity.label).font(.headline)
                Text("\(issues.count)").font(.subheadline.monospacedDigit()).foregroundStyle(.secondary)
            }
            ForEach(issues) { row($0) }
        }
    }

    private func row(_ issue: AppIssue) -> some View {
        let isOpen = expanded.contains(issue.id)
        return VStack(alignment: .leading, spacing: Layout.Spacing.xs) {
            HStack(alignment: .top, spacing: Layout.Spacing.sm) {
                // A real control, not a tap gesture: focusable, keyboard-operable,
                // and it tells sighted users the row has more to show.
                if issue.detail != nil {
                    Button { toggle(issue) } label: {
                        Image(systemName: "chevron.right")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.secondary)
                            .rotationEffect(.degrees(isOpen ? 90 : 0))
                            .frame(width: 14, height: 16)
                            .contentShape(.rect)
                    }
                    .buttonStyle(.borderless)
                    .accessibilityLabel("Details for \(issue.title)")
                    .accessibilityValue(isOpen ? "Expanded" : "Collapsed")
                    .accessibilityHint("Shows or hides the full explanation")
                    .help(isOpen ? "Hide details" : "Show details")
                } else {
                    Color.clear.frame(width: 14, height: 16)
                }
                Image(systemName: issue.severity.icon)
                    .foregroundStyle(issue.severity.tint)
                    .font(.body)
                    .padding(.top, 1)
                VStack(alignment: .leading, spacing: Layout.Spacing.xxs) {
                    Text(issue.title)
                        .font(.callout.weight(.medium))
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                    HStack(spacing: Layout.Spacing.xs) {
                        StatusBadge(text: issue.source.chip, symbol: nil, tint: issue.severity.tint)
                        if let market = issue.market {
                            StatusBadge(text: market, symbol: nil, tint: Theme.Colors.information)
                        }
                        if issue.count > 1 {
                            StatusBadge(text: "×\(issue.count)", symbol: nil, tint: Theme.Colors.muted)
                        }
                        if issue.dismissable {
                            Text(Self.relative.localizedString(for: issue.timestamp, relativeTo: Date()))
                                .font(.caption2).foregroundStyle(.secondary)
                        }
                    }
                }
                Spacer(minLength: Layout.Spacing.sm)
                fixControl(issue)
                if issue.dismissable {
                    Button { appState.issues.dismiss(issue.id) } label: {
                        Image(systemName: "xmark.circle.fill").foregroundStyle(.secondary)
                    }
                    .buttonStyle(.borderless)
                    .accessibilityLabel("Dismiss \(issue.title)")
                    .help("Dismiss")
                }
            }
            if isOpen, let detail = issue.detail {
                Text(detail)
                    .font(.callout).foregroundStyle(.secondary)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.leading, Layout.Spacing.lg)
            }
        }
        .padding(Layout.Spacing.sm)
        .frame(maxWidth: .infinity, alignment: .leading)
        .mdCard()
        .contentShape(.rect)
        .onTapGesture { toggle(issue) }   // convenience for the mouse; the chevron is the real control
    }

    private func toggle(_ issue: AppIssue) {
        guard issue.detail != nil else { return }
        if expanded.contains(issue.id) { expanded.remove(issue.id) } else { expanded.insert(issue.id) }
    }

    /// Entity routes land on the exact campaign / ad group / design, so "Review"
    /// is honest. A bare screen route can't, so name the screen instead of
    /// promising a review of something specific.
    private static func reviewLabel(for route: Route) -> String {
        if case .screen(let screen) = route { return "Open \(screen.title) →" }
        return "Review →"
    }

    /// The single button that fits the issue's remedy — or nothing.
    @ViewBuilder
    private func fixControl(_ issue: AppIssue) -> some View {
        switch issue.fix {
        case .pull(let market):
            if perRowFixing.contains(issue.id) {
                ProgressView().controlSize(.small)
            } else {
                Button("Re-pull") { Task { await rePull(market, id: issue.id) } }
                    .buttonStyle(.bordered).controlSize(.small)
                    .disabled(fixing || appState.killActive)
                    .help(appState.killActive ? "Blocked while KILL is active" : "Run a live Amazon pull for \(market)")
            }
        case .operatorCommand(let fragment):
            Button(copiedID == issue.id ? "Copied ✓" : "Copy fix command") {
                Clipboard.copy("cd '\(root)' && \(fragment)")
                copiedID = issue.id
                Task { try? await Task.sleep(for: .seconds(1.6)); if copiedID == issue.id { copiedID = nil } }
            }
            .buttonStyle(.bordered).controlSize(.small)
            .help("Copy the exact command — run it yourself via ! (operator-gated)")
        case .reviewRoute(let route):
            Button(Self.reviewLabel(for: route)) {
                // Switch market first so market-wide alerts (spend_spike → the
                // market's Dashboard) land in the right market; entity routes
                // carry their own market too.
                if let market = issue.market { appState.selectedMarket = market }
                appState.navigate(to: route)
            }
            .buttonStyle(.bordered).controlSize(.small)
            .help("Go to the exact \(route.screen.title.lowercased()) this alert is about")
        case .none:
            EmptyView()
        }
    }

    // MARK: - actions

    private func fixAllSafe() async {
        guard !appState.killActive else {
            fixMessage = "KILL is active — pulls are blocked. Copy the KILL command, run it via !, then Fix all safe again."
            return
        }
        let targets = pullMarkets
        fixing = true
        defer { fixing = false; fixProgress = nil }
        var done = 0
        var failures: [String] = []
        for market in targets {
            fixProgress = "Re-pulling \(market)…"
            do { _ = try await appState.runPull(market: market); done += 1 }
            catch let error as BridgeError {
                _ = error   // already in the Errors list via IssueCenter.record
                failures.append(market)
            }
            catch {
                // e.g. ActionCoordinatorError.killActive turning on mid-run —
                // not a BridgeError, so nothing else would surface it
                failures.append("\(market) (\(error.localizedDescription))")
            }
        }
        fixProgress = "Refreshing…"
        await appState.refresh()
        await appState.checkAlerts()
        let remaining = stagedCommands.count
        var msg = "Re-pulled \(done)/\(targets.count) market\(targets.count == 1 ? "" : "s")."
        if !failures.isEmpty {
            msg += " Failed: \(failures.joined(separator: ", "))."
        }
        if remaining > 0 {
            msg += " \(remaining) gated fix\(remaining == 1 ? "" : "es") still need you — use the Copy fix command buttons."
        }
        fixMessage = msg
    }

    private func rePull(_ market: String, id: String) async {
        perRowFixing.insert(id)
        defer { perRowFixing.remove(id) }
        do { _ = try await appState.runPull(market: market) }
        catch { fixMessage = "\(market) re-pull failed: \(error.localizedDescription)" }
        await appState.refresh()
    }

    // MARK: - chrome

    private func banner(_ text: String, systemImage: String, tint: Color,
                        spinner: Bool = false, dismiss: (() -> Void)? = nil) -> some View {
        HStack(spacing: Layout.Spacing.sm) {
            if spinner { ProgressView().controlSize(.small) }
            else { Image(systemName: systemImage).foregroundStyle(tint) }
            Text(text).font(.callout).fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
            if let dismiss {
                Button { dismiss() } label: { Image(systemName: "xmark.circle.fill").foregroundStyle(.secondary) }
                    .buttonStyle(.borderless)
                    .accessibilityLabel("Dismiss this message")
                    .help("Dismiss")
            }
        }
        .padding(Layout.Spacing.sm)
        .frame(maxWidth: .infinity, alignment: .leading)
        .mdCard()
    }

    private var emptyState: some View {
        VStack(spacing: Layout.Spacing.sm) {
            Image(systemName: "checkmark.seal.fill")
                .font(.system(.largeTitle).weight(.regular)).imageScale(.large).foregroundStyle(Theme.Colors.positive)
            Text("No problems").font(.title3.weight(.semibold))
            Text("No failed calls, the economics gate is open, data is fresh, and nothing is frozen.")
                .font(.callout).foregroundStyle(.secondary)
                .multilineTextAlignment(.center).frame(maxWidth: 380)
        }
    }

    private static let relative: RelativeDateTimeFormatter = {
        let f = RelativeDateTimeFormatter()
        f.unitsStyle = .abbreviated
        return f
    }()
}

#Preview {
    ErrorsView()
        .environment(AppState())
}
