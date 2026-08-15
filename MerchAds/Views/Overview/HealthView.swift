import SwiftUI

/// One glance = "is the nightly job OK?" — per-market freshness from
/// `appctl health`, cross-checked against a direct read-only SQLite count so a
/// mismatch between the two access paths is immediately visible.
struct HealthView: View {
    @Environment(AppState.self) private var appState
    @State private var directCounts: [String: Int64] = [:]
    @State private var pullingMarket: String?
    @State private var pullError: String?
    @State private var runProgress: NightlyRunProgress?   // live nightly run, polled from the log
    @State private var colPrefs: TableColumnCustomization<HealthRow> = ColumnPrefs.load(TableID.health)
    @State private var selection = Set<HealthRow.ID>()
    @State private var sortOrder = SortPrefs.load(
        TableID.health, fields: sortFields,
        fallback: [KeyPathComparator(\HealthRow.health.market, order: .forward)])

    /// Row/header heights for the market table. Scaled so larger accessibility
    /// text doesn't clip rows the way a hardcoded 28pt did.

    private static let sortFields: [String: KeyPathComparator<HealthRow>] = [
        "market": .init(\.health.market), "configured": .init(\.configuredValue),
        "data": .init(\.latestDataValue), "perday": .init(\.perDayValue),
        "lastpull": .init(\.lastPullValue), "lastwrite": .init(\.lastWriteValue),
        "campaigns": .init(\.campaignsValue), "dbdirect": .init(\.directCountValue),
        "issues": .init(\.issuesValue),
    ]

    private struct HealthRow: Identifiable {
        let health: MarketHealth
        let directCount: Int64?
        var id: String { health.market }

        // Non-optional sort proxies for the columns (Table needs Comparable; Bool isn't).
        var configuredValue: Int { health.configured ? 1 : 0 }
        var latestDataValue: String { health.latestData ?? "" }
        var perDayValue: Int { health.targetDaily?.days ?? -1 }
        var lastPullValue: String { health.lastPull ?? "" }
        var lastWriteValue: String { health.lastWrite ?? "" }
        var campaignsValue: Int { health.campaigns ?? -1 }
        var directCountValue: Int64 { directCount ?? -1 }
        var issuesValue: Int { (health.reportsPending ?? 0) + (health.staleTables?.count ?? 0) }
    }

    private var rows: [HealthRow] {
        (appState.health?.markets ?? []).map {
            HealthRow(health: $0, directCount: directCounts[$0.market])
        }.sorted(using: sortOrder)
    }

    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "System Health", subtitle: "data freshness across all markets", help: .health)
            Divider()
            engineHealthContent
        }
        .background(Theme.Colors.canvas)
        // Poll for a run happening RIGHT NOW while this screen is open — the
        // machine-readable status file is only written when the run finishes.
        .task {
            while !Task.isCancelled {
                let p = await loadRunProgress()
                if !Task.isCancelled { runProgress = p }
                try? await Task.sleep(for: .seconds(5))
            }
        }
    }

    /// The in-progress run. Prefer the engine's `run-status` endpoint so the data
    /// comes through the same bridge as everything else here; fall back to
    /// reading the log directly if the bridge is unreachable, so a live run still
    /// shows when appctl is momentarily busy.
    private func loadRunProgress() async -> NightlyRunProgress? {
        if let bridge = try? appState.makeBridge(),
           let resp = try? await bridge.call(RunStatusResponse.self, ["run-status"]) {
            return NightlyRunProgress(resp)
        }
        let root = AppSettings.engineRoot.path
        return await Task.detached(priority: .utility) {
            NightlyRunMonitor.inProgress(engineRoot: root)
        }.value
    }

    private var engineHealthContent: some View {
        VStack(alignment: .leading, spacing: 0) {
            healthStatusBand
            liveRunBanner
            lastRunBanner
            if let pullError {
                Label(pullError, systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(Theme.Colors.critical)
                    .padding(Layout.Spacing.sm)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Theme.Colors.critical.opacity(0.1))
            }
            if appState.killActive {
                Label("KILL switch is ON — the engine previews only, no writes.",
                      systemImage: "exclamationmark.octagon.fill")
                    .foregroundStyle(Theme.Colors.critical)
                    .padding(Layout.Spacing.sm)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Theme.Colors.critical.opacity(0.1))
                    .help("Turn it off in Actions when you're ready to let the engine write again")
            }
            if appState.approvalRequired {
                Label("Approval gate is ON — the nightly run collects negatives & pauses instead of applying them.",
                      systemImage: "checklist")
                    .foregroundStyle(Theme.Colors.caution)
                    .padding(Layout.Spacing.sm)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Theme.Colors.caution.opacity(0.1))
                    .help("Review and apply them in the Approval Queue; toggle the gate in Actions")
            }

            // The table is UNCONDITIONAL; the loading / "no engine data" states are
            // drawn as an overlay LAYER over it. Toggling this greedy table's
            // presence with an if/else is the macOS 26 detail-blanking class (see
            // CrossPurchaseView / DemandFeed / Profit) — keep it in the tree always.
                Table(rows, selection: $selection, sortOrder: $sortOrder.descendingFirst(),
                      columnCustomization: $colPrefs) {
                    TableColumn("Market", value: \.health.market) { row in
                        HStack {
                            Text(row.health.market)
                                .fontWeight(row.health.market == appState.selectedMarket ? .bold : .regular)
                            if row.health.market == appState.selectedMarket {
                                Image(systemName: "checkmark.circle.fill")
                                    .foregroundStyle(.tint)
                                    .accessibilityLabel("Selected market")
                            }
                        }
                    }
                    .width(min: 38, ideal: 70)
                    .customizationID("market")
                    TableColumn("Configured", value: \.configuredValue) { row in
                        StatusBadge(
                            text: row.health.configured ? "Configured" : "Missing",
                            symbol: row.health.configured ? "checkmark.circle.fill" : "xmark.circle.fill",
                            tint: row.health.configured
                                ? Theme.Colors.positive : Theme.Colors.critical)
                    }
                    .width(min: 44, ideal: 80)
                    .customizationID("configured")
                    TableColumn("Data through", value: \.latestDataValue) { row in
                        // Same EU dd.MM.yyyy the rest of the table uses — the raw
                        // engine ISO ("2026-08-12") was the one column reading
                        // differently from Last pull / Last write next to it.
                        Text(Format.euDate(row.health.latestData))
                            .foregroundStyle(stale(row.health.latestData)
                                             ? Theme.Colors.critical : .primary)
                    }
                    .customizationID("data-through")
                    TableColumn("Per-day history", value: \.perDayValue) { row in
                        // Rolling-window rules read target_daily and refuse to
                        // write when the window has holes. This is where the
                        // operator finds out why a rule went quiet.
                        if let coverage = row.health.targetDaily {
                            // compactNumeral, not tableNumeral: the full span is
                            // the point of the cell, and at body size it was
                            // truncating to "92 days · 06.05.–0…".
                            Text(dailySpan(coverage))
                                .font(Typography.compactNumeral)
                                .help("True per-day, per-target totals: \(coverage.days) days, "
                                      + "\(Format.euDate(coverage.first)) to \(Format.euDate(coverage.last)). "
                                      + "Rolling-window rules read this table.")
                        } else {
                            Text("not banked yet")
                                .font(Typography.compactNumeral)
                                .foregroundStyle(.secondary)
                                .help("No per-day, per-target history for this market yet. "
                                      + "Rules with a rolling window stay quiet until it is banked.")
                        }
                    }
                    .width(min: 120, ideal: 190)
                    .customizationID("per-day-history")
                    TableColumn("Last pull", value: \.lastPullValue) { row in
                        Text(shortTimestamp(row.health.lastPull))
                    }
                    .customizationID("last-pull")
                    TableColumn("Last write", value: \.lastWriteValue) { row in
                        Text(shortTimestamp(row.health.lastWrite))
                    }
                    .customizationID("last-write")
                    TableColumn("Campaigns", value: \.campaignsValue) { row in
                        CountText(value: row.health.campaigns)
                    }
                    .width(min: 44, ideal: 80)
                    .customizationID("campaigns")
                    TableColumn("DB direct", value: \.directCountValue) { row in
                        // Same count read straight from SQLite — should match "Campaigns".
                        HStack(spacing: Layout.Spacing.xxs) {
                            Text(row.directCount.map(String.init) ?? "—")
                                .font(Typography.tableNumeral)
                            if let direct = row.directCount, let bridged = row.health.campaigns,
                               direct != Int64(bridged) {
                                Image(systemName: "exclamationmark.triangle.fill")
                                    .foregroundStyle(Theme.Colors.caution)
                                    .help("Direct SQLite count differs from appctl — DB may be mid-update")
                            }
                        }
                    }
                    .width(min: 44, ideal: 80)
                    .customizationID("db-direct")
                    TableColumn("Issues", value: \.issuesValue) { row in
                        // WHY it's stale: last pull_log error note + stalled reports.
                        HStack(spacing: Layout.Spacing.xs) {
                            if let pending = row.health.reportsPending, pending > 0 {
                                StatusBadge(text: "\(pending) reports pending",
                                            symbol: "hourglass",
                                            tint: Theme.Colors.caution)
                                    .help("Report jobs requested but never downloaded — the pull may have stalled")
                            }
                            if let staleT = row.health.staleTables, !staleT.isEmpty {
                                StatusBadge(text: "\(staleT.count) table\(staleT.count == 1 ? "" : "s") stuck",
                                            symbol: "clock.badge.exclamationmark",
                                            tint: Theme.Colors.critical)
                                    .help("Report jobs stopped landing for: \(staleT.joined(separator: ", ")) — writes measured over these tables are frozen for this market")
                            }
                            if let note = row.health.lastNote {
                                Text(note.note)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                                    .truncationMode(.tail)
                                    .help("\(Format.euDateTime(note.at)) · \(note.kind ?? ""): \(note.note)")
                            }
                            if row.health.reportsPending ?? 0 == 0 && row.health.lastNote == nil
                                && (row.health.staleTables ?? []).isEmpty {
                                StatusBadge(text: "Clear", symbol: "checkmark.circle.fill",
                                            tint: Theme.Colors.positive)
                            }
                        }
                    }
                    .customizationID("issues")
                    TableColumn("Pull") { row in
                        // Refresh campaigns + reports for THIS row's market —
                        // the same KILL-gated, audited path Actions uses, so a
                        // new market's campaigns land without hunting for the
                        // Actions screen. One pull at a time keeps it simple.
                        if pullingMarket == row.health.market {
                            ProgressView().controlSize(.small)
                        } else {
                            Button("Pull now", systemImage: "arrow.clockwise") {
                                Task { await pull(row.health.market) }
                            }
                            .buttonStyle(.borderless)
                            .disabled(pullingMarket != nil || !row.health.configured
                                      || appState.killActive)
                            .help(pullHelp(for: row.health))
                        }
                    }
                    .width(min: 80, ideal: 100)
                    .customizationID("pull")
                }
                .copyableRows(rows, primaryLabel: "Market",
                              primary: { $0.health.market },
                              row: { r in
                    [r.health.market,
                     r.health.latestData ?? "",
                     r.health.targetDaily.map { "\($0.days) days" } ?? "not banked yet",
                     r.health.campaigns.map(String.init) ?? "",
                     r.directCount.map(String.init) ?? ""].joined(separator: "\t")
                })
                // Greedy so it fills the window (empty striped rows below a short
                // market list are fine); was content-sized + top-pinned + a Spacer.
                .frame(minHeight: 160)
                .background(Theme.Colors.surface)
                .overlay { healthStateOverlay }
        }
        .task(id: appState.health?.markets.map(\.market)) { await loadDirectCounts() }
        .onChange(of: colPrefs) { ColumnPrefs.save(TableID.health, colPrefs) }
        .onChange(of: sortOrder) { SortPrefs.save(TableID.health, sortOrder, fields: Self.sortFields) }
    }

    // Loading / "no engine data", drawn OVER the always-present health table.
    @ViewBuilder
    private var healthStateOverlay: some View {
        if rows.isEmpty {
            if appState.isLoading {
                ProgressView("Checking engine…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(Theme.Colors.canvas)
            } else {
                ContentUnavailableView {
                    Label("No engine data", systemImage: "heart.text.square")
                } description: {
                    Text("Could not reach appctl.py. Check the engine folder in Settings (⌘,), then refresh (⌘R).")
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Theme.Colors.canvas)
            }
        }
    }

    /// A nightly run happening RIGHT NOW, parsed live from the log (the status
    /// file lands only when the run ends). Renders nothing when no run is active,
    /// so the completed-run line below is what shows the rest of the time.
    @ViewBuilder
    private var liveRunBanner: some View {
        if let run = runProgress {
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: Layout.Spacing.sm) {
                    ProgressView().controlSize(.small)
                    Text("Nightly run in progress").fontWeight(.semibold)
                    if let clock = run.startedClock {
                        Text("· started \(clock)" +
                             (Self.elapsedText(run.elapsedSeconds).map { " · \($0)" } ?? ""))
                            .foregroundStyle(.secondary)
                    }
                    if !run.markets.isEmpty {
                        Text("· \(run.reached.count)/\(run.markets.count) markets")
                            .foregroundStyle(.secondary)
                    }
                    if let m = run.currentMarket {
                        Text("· \(m)").foregroundStyle(.secondary)
                    }
                    if !run.failures.isEmpty {
                        Text("· \(run.failures.count) failed so far")
                            .foregroundStyle(Theme.Colors.critical)
                    }
                    Spacer()
                }
                .font(.callout)
                if let activity = run.lastActivity, !activity.isEmpty {
                    Text(activity)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
            }
            .padding(Layout.Spacing.sm)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.Colors.accent.opacity(0.1))
            .help("Read live from outputs/scheduled_runs.log. The machine-readable status is written only when the run finishes.")
        }
    }

    private static func elapsedText(_ seconds: Int?) -> String? {
        guard let s = seconds, s >= 0 else { return nil }
        let h = s / 3600, m = (s % 3600) / 60
        return h > 0 ? "\(h)h \(m)m" : "\(m)m"
    }

    /// The nightly's outcome, from outputs/last_run_status.json via `health`.
    /// The loop continues past a crashed phase by design — with Discord off,
    /// this banner (plus the run's own macOS notification) is how a crash
    /// reaches the operator. Absent file (no instrumented run yet) = no row.
    @ViewBuilder
    private var lastRunBanner: some View {
        if let run = appState.health?.lastRun {
            if !run.ok, !run.failures.isEmpty {
                Label {
                    Text("Last nightly run (\(shortTimestamp(run.finished))): " +
                         "\(run.failures.count) step\(run.failures.count == 1 ? "" : "s") failed — " +
                         run.failures.map { "\($0.market)/\($0.step)" }.joined(separator: ", "))
                } icon: {
                    Image(systemName: "xmark.octagon.fill")
                }
                .foregroundStyle(Theme.Colors.critical)
                .padding(Layout.Spacing.sm)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.Colors.critical.opacity(0.1))
                .help("The run continued past the failure. Full trace: outputs/scheduled_runs.log")
            } else {
                Label("Last nightly run finished \(shortTimestamp(run.finished)) — all steps OK",
                      systemImage: "checkmark.circle")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, Layout.Spacing.sm)
                    .padding(.vertical, Layout.Spacing.xxs)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    private var healthStatusBand: some View {
        let healthRows = appState.health?.markets ?? []
        let configured = healthRows.filter(\.configured).count
        let withData = healthRows.filter(\.hasData).count
        let issues = healthRows.filter {
            !$0.configured || $0.error != nil || ($0.reportsPending ?? 0) > 0 || $0.lastNote != nil
                || !($0.staleTables ?? []).isEmpty
        }.count
        return Group {
            HStack(spacing: Layout.Spacing.sm) {
                StatCard(title: "Configured", value: Format.count(configured),
                         tint: configured == healthRows.count
                            ? Theme.Colors.positive : Theme.Colors.caution,
                         symbol: "gearshape.fill")
                    .mdCard()
                StatCard(title: "With data", value: Format.count(withData),
                         symbol: "externaldrive.fill")
                    .mdCard()
                StatCard(title: "Issues", value: Format.count(issues),
                         tint: issues > 0 ? Theme.Colors.critical : Theme.Colors.positive,
                         symbol: issues > 0 ? "exclamationmark.triangle.fill" : "checkmark.seal.fill")
                    .mdCard()
            }
        }
        .padding(.horizontal, Layout.Spacing.lg)
        .padding(.vertical, Layout.Spacing.md)
    }

    /// Re-pull one market on demand, then refresh so Last pull / Campaigns /
    /// DB-direct update in place. Reuses AppState.runPull (the coordinator path
    /// Actions and the Errors tab already use); it throws if KILL blocks it.
    private func pull(_ market: String) async {
        pullingMarket = market
        defer { pullingMarket = nil }
        pullError = nil
        do {
            _ = try await appState.runPull(market: market)
            await appState.refresh()
        } catch {
            pullError = "\(market) pull failed: \(error.localizedDescription)"
        }
    }

    private func pullHelp(for health: MarketHealth) -> String {
        if appState.killActive { return "Blocked while the KILL switch is on" }
        if !health.configured { return "\(health.market) has no advertiser profile configured" }
        return "Refresh campaigns and request fresh reports for \(health.market) from Amazon"
    }

    private func loadDirectCounts() async {
        let markets = (appState.health?.markets ?? []).filter(\.hasData).map(\.market)
        let counts = await Task.detached(priority: .utility) {
            var result: [String: Int64] = [:]
            for market in markets {
                result[market] = AppState.directSnapshot(market: market).campaignCount
            }
            return result
        }.value
        directCounts = counts
    }

    /// "92 days · 06.05.–05.08." — compact enough for a table cell. The full
    /// dates ride along in the cell's tooltip.
    private func dailySpan(_ coverage: TargetDailyCoverage) -> String {
        let unit = coverage.days == 1 ? "day" : "days"
        let first = Format.date(coverage.first).map(Format.euDateShort)
        let last = Format.date(coverage.last).map(Format.euDateShort)
        guard let first, let last else { return "\(coverage.days) \(unit)" }
        return "\(coverage.days) \(unit) · \(first)–\(last)"
    }

    /// Data older than ~3 days means the nightly job is missing runs.
    private func stale(_ latest: String?) -> Bool {
        guard let date = Format.date(latest) else { return false }
        return Date().timeIntervalSince(date) > 3 * 24 * 3600
    }

    private func shortTimestamp(_ iso: String?) -> String {
        // "2026-07-01T10:30:29" → EU "01.07.2026 10:30"
        Format.euDateTime(iso)
    }
}

#Preview {
    HealthView()
        .environment(AppState())
}
