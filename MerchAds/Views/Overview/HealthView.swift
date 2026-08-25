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
        "ceiling": .init(\.bidCeilingValue), "issues": .init(\.issuesValue),
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
        // Sorts uncapped markets to one end, which is the whole reason the
        // column exists. -1 for "no cap", -2 for an engine that cannot say.
        var bidCeilingValue: Double {
            guard let c = health.bidCeiling else { return -2 }
            guard let cap = c.target ?? c.keyword else { return -1 }
            return cap
        }
        var issuesValue: Int {
            (health.reportsPending ?? 0) + (health.staleTables?.count ?? 0)
                + (health.hasData && health.pullIsBehind() ? 1 : 0)
                + (health.dailyTotals?.stale == true ? 1 : 0)
        }
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
            streamBanner
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
                        // `latestData` is the WORST of the three perf tables,
                        // which is right to gate on and useless to diagnose
                        // from — the three report jobs fail independently. So
                        // when they disagree, say which one is behind.
                        VStack(alignment: .leading, spacing: 1) {
                            Text(Format.euDate(row.health.latestData))
                                .foregroundStyle(stale(row.health.latestData)
                                                 ? Theme.Colors.critical : .primary)
                            let lagging = row.health.laggingTables
                            if !lagging.isEmpty {
                                Text(lagging.map { "\($0.name) −\($0.daysBehind)d" }
                                        .joined(separator: " · "))
                                    .font(.caption2)
                                    .foregroundStyle(Theme.Colors.caution)
                                    .help("These perf tables are behind the freshest one. "
                                          + "Each is filled by its own Amazon report job, "
                                          + "so they fail independently. Writes measured "
                                          + "over a table freeze once it is 4 days stale.")
                            }
                            // A table that EXISTS and has never been filled is
                            // not "lagging" — it has no date to be behind by,
                            // so the line above cannot show it, and the alarm
                            // for staleness never fires either. That is the
                            // state of a market on its first day, and of any
                            // market whose report job has never once landed.
                            let undated = row.health.undatedTables
                            if !undated.isEmpty {
                                Text(undated.map { "\($0) never filled" }
                                        .joined(separator: " · "))
                                    .font(.caption2)
                                    .foregroundStyle(Theme.Colors.critical)
                                    .help("No Amazon report has ever landed for "
                                          + "these tables in this market. This is "
                                          + "normal on a market's first day and a "
                                          + "fault at any other time — the rules and "
                                          + "phases that read them cannot run at all.")
                            }
                        }
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
                    operationsColumns
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
    /// Configured markets the last run skipped, in the health table's order.
    private func skippedMarkets(in run: LastRunStatus) -> [String] {
        run.skippedMarkets(configured: (appState.health?.markets ?? [])
            .filter(\.configured)
            .map(\.market))
    }

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
            } else if !skippedMarkets(in: run).isEmpty {
                let skipped = skippedMarkets(in: run)
                Label {
                    Text("Last nightly run (\(shortTimestamp(run.finished))): every step passed, but it " +
                         "only ran \(run.markets?.joined(separator: " ") ?? "—"). " +
                         "Skipped \(skipped.joined(separator: " ")) — those markets were not advertised that night.")
                } icon: {
                    Image(systemName: "exclamationmark.triangle.fill")
                }
                .font(.caption)
                .foregroundStyle(Theme.Colors.caution)
                .padding(Layout.Spacing.sm)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.Colors.caution.opacity(0.1))
                .help("The nightly discovers its market list at start-up. A short list is not a failed step, "
                      + "so the run still reports OK. Full trace: outputs/scheduled_runs.log")
            } else {
                // The run takes hours, so it is worth saying how long and what
                // owned the time. Without it a phase that doubled reads as a
                // busier night, and there is no way to tell whether an
                // optimisation helped.
                Label("Last nightly run finished \(shortTimestamp(run.finished)) — all steps OK"
                      + (run.wallSeconds.map { " in \(RunStepTiming(market: "", step: "", seconds: $0).readable)" } ?? "")
                      + (run.markets.map { " · ran \($0.joined(separator: " "))" } ?? "")
                      + (run.slowestStep.map { " · slowest \($0.market) \($0.step) \($0.readable)" } ?? ""),
                      systemImage: "checkmark.circle")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, Layout.Spacing.sm)
                    .padding(.vertical, Layout.Spacing.xxs)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .help(run.steps.map { steps in
                        "Where the night went:\n"
                        + steps.prefix(12)
                               .map { "\($0.market) \($0.step) — \($0.readable)" }
                               .joined(separator: "\n")
                    } ?? "Per-step timing arrives after the next nightly run.")
            }
        }
    }

    /// Marketing Stream, in one line.
    ///
    /// Hidden entirely when no queue is configured — most installs have none,
    /// and an empty "Stream: not set up" row every day is noise.
    ///
    /// The loud case is a STOPPED DRAIN, not a quiet dataset. Stream publishes
    /// nothing for an hour in which nothing happened, so an empty hour is
    /// normal. But Stream also never resends: once SQS retention expires the
    /// rows are gone for good. So a drain that has not run in over two hours is
    /// the thing worth interrupting the operator for.
    @ViewBuilder
    private var streamBanner: some View {
        if let stream = appState.health?.stream {
            if let error = stream.error {
                Label("Marketing Stream could not be read: \(error)",
                      systemImage: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundStyle(Theme.Colors.caution)
                    .padding(.horizontal, Layout.Spacing.sm)
                    .padding(.vertical, Layout.Spacing.xxs)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            if stream.configured {
            // Corruption outranks everything else here. A drain that "ran", a
            // healthy message count and a fresh timestamp all read green while
            // the file underneath is unreadable — which is how the 2026-08-22
            // fault reached the operator as seven copies of "the undercount
            // check could not run" and never once as its own name.
            if stream.corrupt == true {
                Label {
                    Text("Marketing Stream database is corrupt"
                         + (stream.corruptDetail.map { " (\($0))" } ?? "")
                         + ". Hours already banked may be unreadable, and Stream never "
                         + "resends. Recover it before the next drain writes over more of it.")
                } icon: {
                    Image(systemName: "externaldrive.badge.exclamationmark")
                }
                .font(.caption)
                .foregroundStyle(Theme.Colors.critical)
                .padding(Layout.Spacing.sm)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.Colors.critical.opacity(0.10),
                            in: RoundedRectangle(cornerRadius: Layout.Radius.medium))
                .padding(.horizontal, Layout.Spacing.sm)
                .padding(.bottom, 4)
            }
            if stream.error == nil, let behind = stream.drainBacklog, !behind.isEmpty {
                // Recent, busy, and still losing data. Every other signal on
                // this card is green in this state, which is exactly why it
                // needs its own line.
                Label {
                    Text("Marketing Stream: \(behind.joined(separator: " and ")) did not "
                         + "drain to empty. Messages are arriving faster than the hourly "
                         + "job reads them, so today's live totals are an undercount and "
                         + "the backlog is growing.")
                } icon: {
                    Image(systemName: "exclamationmark.triangle.fill")
                }
                .font(.caption)
                .foregroundStyle(Theme.Colors.caution)
                .padding(Layout.Spacing.sm)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.Colors.caution.opacity(0.12),
                            in: RoundedRectangle(cornerRadius: Layout.Radius.medium))
            } else if stream.error == nil, stream.drainStale == true {
                Label {
                    Text("Marketing Stream: the hourly drain \(streamDrainAge(stream)). "
                         + "Stream never resends, so anything past the queue's retention is lost.")
                } icon: {
                    Image(systemName: "exclamationmark.triangle.fill")
                }
                .font(.caption)
                .foregroundStyle(Theme.Colors.caution)
                .padding(Layout.Spacing.sm)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.Colors.caution.opacity(0.1))
                .help("Check the hourly job: launchctl list | grep merchads.stream — "
                      + "and outputs/stream_drain.log")
            } else if stream.error == nil {
                Label("Marketing Stream · " + streamSummary(stream),
                      systemImage: "antenna.radiowaves.left.and.right")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, Layout.Spacing.sm)
                    .padding(.vertical, Layout.Spacing.xxs)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .help("Hourly push from Amazon into your own SQS queue. "
                          + "A dataset can sit at 'waiting' for a while — Stream sends "
                          + "nothing for an hour in which nothing happened.")
            }
            }
        }
    }

    /// What the ISSUES card counts: every market row that has something wrong,
    /// plus every banner this screen draws.
    ///
    /// It counted market rows alone, so on 2026-08-24 it read 0 in green with
    /// a red "6 steps failed" banner and an amber Stream backlog banner
    /// directly underneath. A summary that can disagree with the screen it
    /// summarises is worse than no summary: the eye stops at the card.
    static func issueCount(markets: [MarketHealth], health: HealthResponse?) -> Int {
        var count = markets.filter {
            !$0.configured || $0.error != nil || ($0.reportsPending ?? 0) > 0 || $0.lastNote != nil
                || !($0.staleTables ?? []).isEmpty || ($0.hasData && $0.pullIsBehind())
                || $0.dailyTotals?.stale == true
        }.count
        if health?.lastRun?.ok == false { count += 1 }
        if let stream = health?.stream, stream.error == nil {
            if !(stream.drainBacklog ?? []).isEmpty { count += 1 }
            if stream.corrupt == true { count += 1 }
        }
        return count
    }

    /// "last ran 3 h ago", or "has never run" when nothing is banked at all.
    private func streamDrainAge(_ stream: StreamHealth) -> String {
        if let realms = stream.drainStaleRealms, !realms.isEmpty {
            return realms.map { realm in
                guard let minutes = stream.drainByRealm?[realm]?.ageMinutes else {
                    return "\(realm) has never run"
                }
                return "\(realm) last ran \(streamAgeText(minutes)) ago"
            }.joined(separator: " · ")
        }
        guard let minutes = stream.drainAgeMinutes else { return "has never run" }
        return "last ran \(streamAgeText(minutes)) ago"
    }

    /// One phrase per dataset: "sp-traffic 439 msgs, 28 min ago · sp-conversion waiting".
    private func streamSummary(_ stream: StreamHealth) -> String {
        let sets = stream.datasets ?? []
        guard !sets.isEmpty else { return "no data yet" }
        return sets.map { d in
            let name = d.realm.map { "\($0) \(d.dataset)" } ?? d.dataset
            if d.isWaiting { return "\(name) waiting" }
            let age = d.ageMinutes.map { ", \(streamAgeText($0)) ago" } ?? ""
            return "\(name) \(Format.count(d.messages)) msgs\(age)"
        }.joined(separator: " · ")
    }

    private func streamAgeText(_ minutes: Int) -> String {
        if minutes < 60 { return "\(minutes) min" }
        if minutes < 60 * 48 { return "\(minutes / 60) h" }
        return "\(minutes / (60 * 24)) d"
    }

    private var healthStatusBand: some View {
        let healthRows = appState.health?.markets ?? []
        let configured = healthRows.filter(\.configured).count
        let withData = healthRows.filter(\.hasData).count
        // The banners drawn below this card count too. The card read a green
        // ISSUES 0 directly above a red "6 steps failed" banner and an amber
        // Stream backlog banner (2026-08-24) — the summary said clear while
        // the screen it summarises said the opposite.
        let issues = HealthView.issueCount(markets: healthRows, health: appState.health)
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

    /// The operational half of the table: counts, the write ceiling, issues,
    /// and the per-market Pull button.
    ///
    /// Extracted because SwiftUI's TableColumnBuilder tops out at ten columns
    /// and "Bid ceiling" is the eleventh. Splitting it here also keeps the type
    /// checker inside a body it can finish — inlining all eleven made it give up.
    @TableColumnBuilder<HealthRow, KeyPathComparator<HealthRow>>
    private var operationsColumns: some TableColumnContent<HealthRow, KeyPathComparator<HealthRow>> {
        TableColumn("Campaigns", value: \.campaignsValue) { row in
            CountText(value: row.health.campaigns)
        }
        .width(min: 44, ideal: 80)
        .customizationID("campaigns")
        TableColumn("DB direct", value: \.directCountValue) { row in
            // The ENABLED campaigns, read straight from SQLite — it
            // proves the direct path works. Compare it against the
            // ENABLED count, not the total: the mirror also holds
            // PAUSED and ARCHIVED rows (US: 373 rows, 57 serving),
            // so comparing with "Campaigns" flagged every healthy
            // market the moment direct reads started working again.
            HStack(spacing: Layout.Spacing.xxs) {
                Text(row.directCount.map(String.init) ?? "—")
                    .font(Typography.tableNumeral)
                if let direct = row.directCount, let bridged = row.health.campaignsEnabled,
                   direct != Int64(bridged) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(Theme.Colors.caution)
                        .help("Direct SQLite count differs from appctl — DB may be mid-update")
                }
            }
        }
        .width(min: 44, ideal: 80)
        .customizationID("db-direct")
        TableColumn("Bid ceiling", value: \.bidCeilingValue) { row in
            // The hard cap on every bid this market writes. It lives in
            // Settings, which shows one market at a time — so a market with
            // NO cap was invisible unless you loaded all seven. Five EU
            // markets ran uncapped for months exactly that way.
            bidCeilingCell(row.health)
        }
        .width(min: 70, ideal: 110)
        .customizationID("bid-ceiling")
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
                // The pull-behind badge: the SAME rule the Errors
                // screen fires on. Without it this column said
                // "Clear" for six markets that had not been pulled
                // for two nights, while Errors showed six errors.
                if row.health.hasData, row.health.pullIsBehind() {
                    StatusBadge(text: "pull behind",
                                symbol: "arrow.trianglehead.2.clockwise.rotate.90",
                                tint: Theme.Colors.critical)
                        .help("No pull for over \(Int(MarketHealth.pullBehindAfterHours))h — the nightly job did not refresh this market. It is in Errors too.")
                }
                // daily_totals is banked by its own nightly step,
                // so it goes stale on its own. Without this the day
                // grid just greys out and this column still says
                // "Clear".
                if let history = row.health.dailyTotals, history.stale {
                    StatusBadge(text: "day history behind",
                                symbol: "calendar.badge.exclamationmark",
                                tint: Theme.Colors.critical)
                        .help(history.reason ?? "Banked days stop at \(history.last ?? "unknown") — daily_metrics.py has not run for this market.")
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
                    && (row.health.staleTables ?? []).isEmpty
                    && !(row.health.hasData && row.health.pullIsBehind())
                    && row.health.dailyTotals?.stale != true {
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

    /// "92 days · 06.05.–05.08." — compact enough for a table cell. The full
    /// dates ride along in the cell's tooltip.
    private func dailySpan(_ coverage: TargetDailyCoverage) -> String {
        let unit = coverage.days == 1 ? "day" : "days"
        let first = Format.date(coverage.first).map(Format.euDateShort)
        let last = Format.date(coverage.last).map(Format.euDateShort)
        guard let first, let last else { return "\(coverage.days) \(unit)" }
        return "\(coverage.days) \(unit) · \(first)–\(last)"
    }

    /// The market's bid ceiling, written so a MISSING one is the loud case.
    ///
    /// A blank field in Settings reads as "not filled in yet". Here it has to
    /// read as "this market writes bids with nothing stopping them", because
    /// that is what it means and nothing else in the app says it. Edit it in
    /// Settings — this column reports, it does not set.
    @ViewBuilder
    private func bidCeilingCell(_ health: MarketHealth) -> some View {
        if let ceiling = health.bidCeiling {
            HStack(spacing: Layout.Spacing.xxs) {
                if health.bidsAreUncapped {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(Theme.Colors.caution)
                    Text("none")
                        .font(Typography.tableNumeral)
                        .foregroundStyle(Theme.Colors.caution)
                } else {
                    Text(ceilingText(ceiling))
                        .font(Typography.tableNumeral)
                }
            }
            .help(ceilingHelp(health, ceiling))
        } else {
            // An engine that predates this field. Not the same as "no ceiling",
            // so it must not borrow the warning.
            Text("—").foregroundStyle(.secondary)
                .help("This engine build does not report the bid ceiling. Settings still shows it.")
        }
    }

    /// One number when both bid surfaces agree, "target / keyword" when they do
    /// not — a half-capped market is a real state, and hiding it would repeat
    /// the mistake this column exists to fix.
    private func ceilingText(_ c: BidCeilingRow) -> String {
        if let t = c.target, let k = c.keyword, t == k { return Self.cap(t) }
        return "\(Self.cap(c.target)) / \(Self.cap(c.keyword))"
    }

    private static func cap(_ v: Double?) -> String {
        v.map { String(format: "%.2f", $0) } ?? "none"
    }

    private func ceilingHelp(_ health: MarketHealth, _ c: BidCeilingRow) -> String {
        if health.bidsAreUncapped {
            return "\(health.market) writes bids with NO ceiling — manual, bulk and nightly "
                 + "automation alike. Set one in Settings while this market is selected."
        }
        return "Target bid \(Self.cap(c.target)) · keyword bid \(Self.cap(c.keyword)) · "
             + "daily budget \(Self.cap(c.budget)). A write above the cap is applied AT the "
             + "cap and shown as adjusted in the Audit Trail. Existing higher bids stay put."
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
