import Foundation
import Observation
import UserNotifications

@MainActor
@Observable
final class AppState {
    private(set) var actionCoordinator: ActionCoordinator
    private(set) var actionExecutionContextID: String
    private(set) var actionContextGeneration = 0
    var markets: [Market] = Market.fallback
    var selectedMarket: String {
        didSet { UserDefaults.standard.set(selectedMarket, forKey: AppSettings.selectedMarketKey) }
    }
    var health: HealthResponse?
    var econGate: EconGateResponse?
    var currentAlerts: [EngineAlert] = []
    var isLoading = false
    var lastError: String?

    /// Session log of app-side failures (Stream A of the Errors tab).
    var issues: IssueCenter { IssueCenter.shared }

    /// Live "wrong right now" conditions derived from already-loaded state.
    var liveIssues: [AppIssue] {
        IssueDerivation.live(health: health, econGate: econGate, alerts: currentAlerts)
    }

    /// Blocking + error count across both streams, for the sidebar badge.
    var openIssueCount: Int {
        let live = liveIssues.filter { $0.severity >= .error }.count
        let app = IssueCenter.shared.appIssues.filter { $0.severity >= .error }.count
        return live + app
    }

    init() {
        LegacyPreferenceMigration.migrate()
        selectedMarket = UserDefaults.standard.string(forKey: AppSettings.selectedMarketKey) ?? "US"
        let contextID = AppSettings.actionExecutionContextID
        actionExecutionContextID = contextID
        if PythonBridge.isRehearsal {
            actionCoordinator = ActionCoordinator(executor: RehearsalActionExecutor(),
                                                  executionContextID: contextID)
        } else {
            actionCoordinator = ActionCoordinator(executor: BridgeActionExecutor(
                engineRoot: AppSettings.engineRoot,
                pythonOverride: AppSettings.pythonOverride),
                executionContextID: contextID)
        }
    }

    var actionPolicyContext: ActionPolicyContext {
        ActionPolicyContext(
            alwaysConfirm: UserDefaults.standard.bool(forKey: AppSettings.alwaysConfirmKey),
            killActive: killActive)
    }

    /// Captures the market now. Callers keep this intent through preview and
    /// confirmation, so a later market switch cannot retarget execution.
    func marketIntent(title: String, arguments: [String], stdin: Data? = nil,
                      cardinality: ActionCardinality = .single,
                      preview: ActionPreview? = nil,
                      confirmationPolicy: ActionConfirmationPolicy = .standard,
                      responseKind: ActionResponseKind = .none) -> ActionIntent {
        marketIntent(for: selectedMarket, title: title, arguments: arguments, stdin: stdin,
                     cardinality: cardinality, preview: preview,
                     confirmationPolicy: confirmationPolicy, responseKind: responseKind)
    }

    func marketIntent(for market: String, title: String, arguments: [String],
                      stdin: Data? = nil,
                      cardinality: ActionCardinality = .single,
                      preview: ActionPreview? = nil,
                      confirmationPolicy: ActionConfirmationPolicy = .standard,
                      responseKind: ActionResponseKind = .none) -> ActionIntent {
        let screenContextID = actionExecutionContextID
        ensureActionExecutorCurrent()
        return ActionIntent(title: title, arguments: arguments, stdin: stdin,
                     scope: .market(market), cardinality: cardinality,
                     preview: preview, confirmationPolicy: confirmationPolicy,
                     responseKind: responseKind,
                     executionContextID: screenContextID)
    }

    func globalIntent(title: String, arguments: [String],
                      allowedWhenKillActive: Bool = false,
                      confirmationPolicy: ActionConfirmationPolicy = .standard,
                      responseKind: ActionResponseKind = .none) -> ActionIntent {
        let screenContextID = actionExecutionContextID
        ensureActionExecutorCurrent()
        return ActionIntent(title: title, arguments: arguments, scope: .global,
                     auditVisibility: .globalConfiguration,
                     allowedWhenKillActive: allowedWhenKillActive,
                     confirmationPolicy: confirmationPolicy,
                     responseKind: responseKind,
                     executionContextID: screenContextID)
    }

    func allMarketsIntent(title: String, arguments: [String],
                          cardinality: ActionCardinality = .single,
                          confirmationPolicy: ActionConfirmationPolicy = .standard,
                          responseKind: ActionResponseKind = .none) -> ActionIntent {
        let screenContextID = actionExecutionContextID
        ensureActionExecutorCurrent()
        return ActionIntent(title: title, arguments: arguments, scope: .allMarkets,
                     cardinality: cardinality,
                     confirmationPolicy: confirmationPolicy,
                     responseKind: responseKind,
                     executionContextID: screenContextID)
    }

    var killActive: Bool { health?.killActive ?? false }
    var approvalRequired: Bool { health?.approvalRequired ?? false }

    /// Bumped whenever a market DB file changes on disk (nightly run, app write)
    /// — screens key their .task on `viewKey` so they reload automatically.
    var dataStamp = 0
    var viewKey: String { "\(selectedMarket)#\(dataStamp)" }

    var showingCommandPalette = false
    var requestedRoute: Route?
    var campaignPath: [Route] = []
    var focusedRoute: Route?

    func navigate(to route: Route) {
        if let market = route.market, market != selectedMarket {
            selectedMarket = market
        }
        requestedRoute = route
    }

    func consumeRequestedRoute(_ route: Route) {
        guard requestedRoute == route else { return }
        switch route {
        case .screen:
            campaignPath.removeAll()
            focusedRoute = nil
        case .campaign:
            campaignPath = [route]
            focusedRoute = nil
        case .adGroup(let market, let campaignID, _):
            campaignPath = [.campaign(market: market, campaignID: campaignID), route]
            focusedRoute = nil
        case .target(let market, let campaignID, let adGroupID, _):
            campaignPath = [
                .campaign(market: market, campaignID: campaignID),
                .adGroup(market: market, campaignID: campaignID, adGroupID: adGroupID),
            ]
            focusedRoute = route
        case .asin:
            campaignPath.removeAll()
            focusedRoute = route
        }
        requestedRoute = nil
    }

    var currentMarket: Market? {
        markets.first { $0.code == selectedMarket }
    }

    func makeBridge() throws -> PythonBridge {
        ensureActionExecutorCurrent()
        return try PythonBridge(engineRoot: AppSettings.engineRoot,
                                pythonOverride: AppSettings.pythonOverride)
    }

    /// Called by Settings and defensively by every bridge/intent factory. It
    /// replaces the write executor and invalidates mounted confirmation state.
    func engineSettingsDidChange() {
        ensureActionExecutorCurrent()
    }

    private func ensureActionExecutorCurrent() {
        let freshID = AppSettings.actionExecutionContextID
        guard freshID != actionExecutionContextID else { return }
        actionExecutionContextID = freshID
        if PythonBridge.isRehearsal {
            actionCoordinator = ActionCoordinator(executor: RehearsalActionExecutor(),
                                                  executionContextID: freshID)
        } else {
            actionCoordinator = ActionCoordinator(executor: BridgeActionExecutor(
                engineRoot: AppSettings.engineRoot,
                pythonOverride: AppSettings.pythonOverride),
                executionContextID: freshID)
        }
        actionContextGeneration += 1
        dataStamp += 1
    }

    /// Fire a data pull for one market through the same coordinator path the
    /// Actions screen uses (KILL-gated, audited). The Errors tab's "Fix all
    /// safe" and per-row Re-pull call this. Throws if KILL blocks it.
    @discardableResult
    func runPull(market: String) async throws -> String {
        let intent = marketIntent(for: market, title: "Re-pull \(market)",
                                  arguments: ["run", "--phase", "pull"],
                                  responseKind: .run)
        let receipt = try await actionCoordinator.execute(
            intent, context: actionPolicyContext, confirmed: true)
        // The exit CODE is the only thing that says whether the pull worked.
        // This matched `.run(_, let text)` and threw the code away, so a phase 0
        // that died after authenticating still returned normally — and callers
        // count a normal return as a completed market. "Fix all safe" then
        // reported "Re-pulled 7/7 markets" over a pull that banked nothing.
        if case .run(let code, let text) = receipt.result {
            guard code == 0 else {
                throw BridgeError.engineError(
                    "Re-pull \(market) exited \(code). \(Self.lastLines(of: text, 3))")
            }
            return text
        }
        return receipt.rehearsed ? "rehearsed" : "done"
    }

    /// The tail of a subprocess transcript, for an error the operator has to act
    /// on. The whole log is thousands of lines and none of the useful part is at
    /// the top.
    private static func lastLines(of text: String, _ n: Int) -> String {
        text.split(separator: "\n", omittingEmptySubsequences: true)
            .suffix(n).joined(separator: " · ")
    }

    func bootstrap() async {
        // Resolve the login-shell python path OFF the main thread before the first
        // bridge call. It shells out to `zsh -lc` once, and a heavy .zshrc can take
        // 50–500 ms; the first makeBridge() touched that `static let` on the main
        // actor and froze the first frame. Forcing it from a detached task computes
        // and caches it there, so refresh()'s makeBridge() reads the cache without
        // spawning anything. Skipped when a python path is pinned in Settings — that
        // path never touches zsh.
        if AppSettings.pythonOverride?.isEmpty ?? true {
            await Task.detached(priority: .userInitiated) {
                _ = PythonBridge.loginShellPython
            }.value
        }
        await refresh()
        await checkAlerts()
        startAlertLoop()
        startNightlyWatch()
    }

    isolated deinit {
        watchLoop?.cancel()
        alertLoop?.cancel()
    }

    // MARK: nightly-run watcher → auto-refresh + digest notification

    private var watchLoop: Task<Void, Never>?
    private var watchedTimestamps: [String: Date] = [:]
    private var lastDigestAt = Date()

    private func startNightlyWatch() {
        watchedTimestamps = Self.currentTimestamps(
            markets: markets.map(\.code), dataRoot: AppSettings.dataRoot)
        lastDigestAt = Date()
        watchLoop?.cancel()
        watchLoop = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(300))
                await self?.checkForNewData()
                await self?.checkNightlyMissed()
            }
        }
    }

    // MARK: nightly-missed watchdog

    private static let nightlyWarnedKey = "nightlyMissedWarnedOn"

    /// The launchd job fires at 10:00 — by 11:00 fresh data should exist. The
    /// DB watcher only fires when something CHANGES, so a silently-missed run
    /// needs this explicit check. Warns once per day.
    private func checkNightlyMissed() async {
        guard Calendar.current.component(.hour, from: Date()) >= 11 else { return }
        let today = Format.dayString()
        guard UserDefaults.standard.string(forKey: Self.nightlyWarnedKey) != today else { return }
        let looksStale: (HealthResponse?) -> Bool = { health in
            let latest = (health?.markets ?? []).filter(\.hasData).compactMap(\.latestData).max()
            guard let latest, let date = Format.date(latest) else { return false }
            return Date().timeIntervalSince(date) > 2 * 24 * 3600
        }
        guard looksStale(health) else { return }
        // confirm against a fresh health read before crying wolf
        guard Bundle.main.bundleIdentifier != nil,
              let bridge = try? makeBridge(),
              let fresh = try? await bridge.call(HealthResponse.self, ["health"],
                                                 preferWorker: false) else { return }
        health = fresh
        guard looksStale(fresh) else { return }
        UserDefaults.standard.set(today, forKey: Self.nightlyWarnedKey)
        let center = UNUserNotificationCenter.current()
        let granted = (try? await center.requestAuthorization(options: [.alert, .sound])) ?? false
        guard granted else { return }
        let content = UNMutableNotificationContent()
        content.title = "Nightly run looks missed"
        content.body = "No market has fresh data after the 10:00 job — check System Health / launchd."
        content.sound = .default
        try? await center.add(UNNotificationRequest(
            identifier: "nightly-missed:\(today)", content: content, trigger: nil))
    }

    nonisolated static let lastRunStatusWatchKey = "__last_run_status__"

    nonisolated static func currentTimestamps(
        markets: [String], dataRoot: URL,
        fileManager: FileManager = .default
    ) -> [String: Date] {
        var stamps: [String: Date] = [:]
        for market in markets {
            let filename = market == "US" ? "ads_data.sqlite" : "ads_data_\(market).sqlite"
            let path = dataRoot.appendingPathComponent(filename).path
            if let date = (try? fileManager.attributesOfItem(atPath: path))?[.modificationDate] as? Date {
                stamps[market] = date
            }
        }
        let statusPath = dataRoot.appendingPathComponent(
            "outputs/last_run_status.json").path
        if let date = (try? fileManager.attributesOfItem(atPath: statusPath))?[.modificationDate] as? Date {
            stamps[lastRunStatusWatchKey] = date
        }
        return stamps
    }

    private func checkForNewData() async {
        let codes = markets.map(\.code)
        let dataRoot = AppSettings.dataRoot
        let fresh = await Task.detached(priority: .utility) {
            Self.currentTimestamps(markets: codes, dataRoot: dataRoot)
        }.value
        let keys = Set(fresh.keys).union(watchedTimestamps.keys)
        let changed = keys.filter { fresh[$0] != watchedTimestamps[$0] }
        watchedTimestamps = fresh
        guard !changed.isEmpty else { return }

        dataStamp += 1              // open screens reload via viewKey
        await refresh()

        // digest notification per changed market (only if there's real activity).
        // Format through the POSIX helper: a plain DateFormatter picks up the
        // user's 12-hour or non-Gregorian settings and silently rewrites the
        // timestamp the engine string-compares against writes_log.
        let since = Format.engineTimestamp(of: lastDigestAt)
        lastDigestAt = Date()
        guard Bundle.main.bundleIdentifier != nil,
              let bridge = try? makeBridge() else { return }
        for market in changed.filter({ codes.contains($0) }).sorted() {
            guard let digest = try? await bridge.call(DigestResponse.self,
                                                      ["digest", "--since", since],
                                                      market: market,
                                                      preferWorker: false) else { continue }
            let interesting = digest.actions.filter { $0.value > 0 }
            let total = interesting.values.reduce(0, +)
            // A run with nothing but failures still has to speak, so the
            // rejected count counts towards being worth a notification.
            guard total + (digest.failedTotal ?? 0) >= 3 else { continue }
            var summary = interesting.sorted { $0.value > $1.value }.prefix(4)
                .map { "\($0.value) \($0.key.replacingOccurrences(of: "_", with: " "))" }
                .joined(separator: " · ")
            // A rejected write is the half of the run worth waking someone for.
            if let bad = digest.failedTotal, bad > 0 {
                summary += "  ·  ⚠︎ \(bad) rejected by Amazon"
            }
            let center = UNUserNotificationCenter.current()
            let granted = (try? await center.requestAuthorization(options: [.alert, .sound])) ?? false
            guard granted else { continue }
            let content = UNMutableNotificationContent()
            content.title = "Run finished · \(market)"
            content.body = summary
            content.sound = .default
            try? await center.add(UNNotificationRequest(
                identifier: "digest:\(market):\(since)", content: content, trigger: nil))
        }
    }

    // MARK: alerts → native notifications

    private var alertLoop: Task<Void, Never>?
    private static let notifiedKeysKey = "notifiedAlertKeys"

    private func startAlertLoop() {
        alertLoop?.cancel()
        alertLoop = Task { [weak self] in
            while !Task.isCancelled {
                // hourly: each sweep spawns one appctl per market and scans the
                // full ad-group tables — alert keys dedup anyway, so more often
                // buys nothing
                try? await Task.sleep(for: .seconds(3600))
                await self?.checkAlerts()
            }
        }
    }

    /// Ask every market with data for alert conditions; notify once per key.
    func checkAlerts() async {
        guard Bundle.main.bundleIdentifier != nil else { return }   // UN needs a bundle
        guard let bridge = try? makeBridge() else { return }
        var fresh: [EngineAlert] = []
        var everyAlert: [EngineAlert] = []
        // Kept oldest-first so the trim below drops the oldest keys. A Set alone
        // has no order, and trimming one would evict *recent* keys at random —
        // making already-notified alerts re-fire on every later sweep.
        var notifiedOrder = UserDefaults.standard.stringArray(forKey: Self.notifiedKeysKey) ?? []
        var seen = Set(notifiedOrder)
        for market in markets.filter(\.hasData).map(\.code) {
            guard let response = try? await bridge.call(AlertsResponse.self, ["alerts"],
                                                        market: market,
                                                        preferWorker: false) else { continue }
            everyAlert.append(contentsOf: response.alerts)
            fresh.append(contentsOf: response.alerts.filter { !seen.contains($0.key) })
        }
        // The Errors tab shows all current alerts regardless of notify-dedup.
        currentAlerts = everyAlert
        guard !fresh.isEmpty else { return }

        let center = UNUserNotificationCenter.current()
        let granted = (try? await center.requestAuthorization(options: [.alert, .sound])) ?? false
        guard granted else { return }
        for alert in fresh.prefix(10) {   // don't firehose the notification center
            let content = UNMutableNotificationContent()
            content.title = title(for: alert.kind)
            content.body = alert.message
            content.sound = .default
            try? await center.add(UNNotificationRequest(identifier: alert.key,
                                                        content: content, trigger: nil))
            if seen.insert(alert.key).inserted { notifiedOrder.append(alert.key) }
        }
        UserDefaults.standard.set(notifiedOrder.suffix(600).map { $0 },
                                  forKey: Self.notifiedKeysKey)
    }

    private func title(for kind: String) -> String {
        switch kind {
        case "spend_spike": "Spend spike"
        case "budget_max": "Budget maxing out"
        case "kill_candidate": "Design crossing kill thresholds"
        case "data_stale": "Data pipeline stale"
        case "portfolio_cap": "Portfolio spend cap"
        case "seasonal_tags_lost": "Seasonal tags lost"
        case "rules_lost": "Rules stopped loading"
        case "stream_undercount": "Marketing Stream is undercounting"
        case "stream_check_failed": "Stream undercount check is not running"
        case "aws_plan_expiry": "AWS account plan is expiring"
        case "stream_db_corrupt": "Marketing Stream database is corrupt"
        case "guard_check_failed": "A data-loss check is not running"
        default: "Merch Ads"
        }
    }

    /// Reload the cheap global state: market list + engine health.
    func refresh() async {
        guard !isLoading else { return }
        isLoading = true
        defer { isLoading = false }
        lastError = nil
        do {
            let bridge = try makeBridge()
            async let marketsCall = bridge.call(MarketsResponse.self, ["markets"])
            async let healthCall = bridge.call(HealthResponse.self, ["health"])   // opens every market DB itself
            let (marketsResponse, healthResponse) = try await (marketsCall, healthCall)
            markets = marketsResponse.markets
            if !markets.contains(where: { $0.code == selectedMarket }) {
                selectedMarket = marketsResponse.current
            }
            health = healthResponse
        } catch {
            lastError = error.localizedDescription
        }
        // US economics gate — best-effort, never blocks the core refresh. A
        // closed gate returns ok:false (not a throw), so it won't log a failure.
        econGate = try? await makeBridge().call(EconGateResponse.self, ["econ-gate"])
    }

    /// Quick facts read straight from the market DB — proves the read-only
    /// SQLite path independently of the Python bridge.
    nonisolated static func directSnapshot(market: String) -> (latestDate: String?, campaignCount: Int64?) {
        let url = AppSettings.databaseURL(market: market)
        guard FileManager.default.fileExists(atPath: url.path),
              let store = try? SQLiteStore(path: url.path) else {
            return (nil, nil)
        }
        let latest = (try? store.scalarString("SELECT MAX(date) FROM campaign_perf")) ?? nil
        // ENABLED only — the campaigns actually serving. The mirror also holds
        // PAUSED and ARCHIVED rows (archived are permanently gone from the console),
        // and counting all three overstated the footer (US: 373 total vs 57 serving).
        let count = (try? store.scalarInt("SELECT COUNT(*) FROM campaigns WHERE state = 'ENABLED'")) ?? nil
        return (latest, count)
    }
}
