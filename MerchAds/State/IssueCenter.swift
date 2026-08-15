import Foundation
import Observation
import SwiftUI

// One home for everything wrong: transient app-side failures captured as they
// happen (Stream A) merged with live "wrong right now" conditions derived from
// health / econ-gate / alerts (Stream B). Nothing is persisted — the session
// list clears on quit, and live issues recompute from already-loaded state.

enum IssueSeverity: Int, Comparable, CaseIterable {
    case warning = 0, error = 1, blocking = 2   // higher raw = more severe

    static func < (lhs: IssueSeverity, rhs: IssueSeverity) -> Bool {
        lhs.rawValue < rhs.rawValue
    }

    var label: String {
        switch self {
        case .blocking: "Blocking"
        case .error: "Error"
        case .warning: "Warning"
        }
    }

    var icon: String {
        switch self {
        case .blocking: "exclamationmark.octagon.fill"
        case .error: "exclamationmark.triangle.fill"
        case .warning: "exclamationmark.circle.fill"
        }
    }

    var tint: Color {
        switch self {
        case .blocking: Theme.Colors.critical
        case .error: Theme.Colors.critical
        case .warning: Theme.Colors.caution
        }
    }
}

enum IssueSource: String {
    case appCall, adopt, health, econGate, alert, stale

    var chip: String {
        switch self {
        case .appCall: "appctl"
        case .adopt: "adopt"
        case .health: "health"
        case .econGate: "econ gate"
        case .alert: "alert"
        case .stale: "data"
        }
    }
}

/// How an issue can be remedied. `pull` is the only thing the app fires itself
/// (the existing `run --phase pull`); `operatorCommand` holds a raw appctl
/// fragment the operator runs via `!`; `review` navigates to the screen where a
/// human decides. `nil` fix = no safe mechanical remedy (manual / self-clearing).
enum IssueFix: Hashable {
    case pull(market: String)
    case operatorCommand(String)     // e.g. "ADS_MARKET=US python3 engine/appctl.py kill --off"
    case reviewRoute(Route)          // deep-link to the exact campaign / ad group / ASIN / screen
}

struct AppIssue: Identifiable, Hashable {
    let id: String              // UUID for app failures; dedupKey for live issues
    let severity: IssueSeverity
    let source: IssueSource
    let title: String
    let detail: String?
    let market: String?
    let timestamp: Date
    let dedupKey: String
    let dismissable: Bool
    var count: Int = 1
    var fix: IssueFix? = nil

    /// A live (Stream B) issue: stable id, not dismissable, recomputed each load.
    static func live(_ severity: IssueSeverity, _ source: IssueSource, key: String,
                     title: String, detail: String? = nil, market: String? = nil,
                     fix: IssueFix? = nil, at: Date) -> AppIssue {
        AppIssue(id: key, severity: severity, source: source, title: title,
                 detail: detail, market: market, timestamp: at, dedupKey: key,
                 dismissable: false, fix: fix)
    }
}

// MARK: - Stream A: session capture

@MainActor
@Observable
final class IssueCenter {
    static let shared = IssueCenter()

    private(set) var appIssues: [AppIssue] = []

    /// Append a failure, collapsing repeats: same dedupKey bumps the count and
    /// refreshes the timestamp instead of stacking identical rows.
    func add(_ issue: AppIssue) {
        if let idx = appIssues.firstIndex(where: { $0.dedupKey == issue.dedupKey }) {
            let bumped = appIssues[idx].count + 1
            appIssues[idx] = AppIssue(
                id: appIssues[idx].id, severity: issue.severity, source: issue.source,
                title: issue.title, detail: issue.detail, market: issue.market,
                timestamp: issue.timestamp, dedupKey: issue.dedupKey,
                dismissable: issue.dismissable, count: bumped, fix: issue.fix)
        } else {
            appIssues.append(issue)
        }
    }

    func dismiss(_ id: AppIssue.ID) {
        appIssues.removeAll { $0.id == id }
    }

    func clearApp() {
        appIssues.removeAll()
    }

    /// Record a bridge failure. Rehearsal denials and cancellations are policy /
    /// lifecycle events, not problems, so callers filter those out before here.
    nonisolated static func record(command: String?, market: String?, error: BridgeError) {
        if case .rehearsalDenied = error { return }
        let title: String
        switch error {
        case .engineError(let message): title = message
        case .badOutput: title = error.errorDescription ?? "appctl call failed"
        default: title = error.errorDescription ?? "appctl call failed"
        }
        let cmd = command ?? "appctl"
        let detail = "Command: \(cmd)" + (market.map { " · market \($0)" } ?? "")
        let issue = AppIssue(
            id: UUID().uuidString, severity: .error, source: .appCall,
            title: "\(cmd): \(title)", detail: detail, market: market,
            timestamp: Date(), dedupKey: "appCall:\(cmd):\(title)", dismissable: true)
        Task { @MainActor in shared.add(issue) }
    }

    /// Report a problem that surfaced from a *successful* envelope (e.g. an
    /// adopt-export warning), which the bridge choke point never sees.
    static func report(source: IssueSource, title: String, detail: String?,
                       market: String?, fix: IssueFix? = nil) {
        let issue = AppIssue(
            id: UUID().uuidString, severity: .error, source: source, title: title,
            detail: detail, market: market, timestamp: Date(),
            dedupKey: "\(source.rawValue):\(title)", dismissable: true, fix: fix)
        shared.add(issue)
    }
}

// MARK: - Stream B: live derivation (pure, unit-tested)

enum IssueDerivation {
    /// Recompute the current "wrong right now" conditions from already-loaded
    /// state. Pure so it can be tested against fixture responses without the UI.
    static func live(health: HealthResponse?, econGate: EconGateResponse?,
                     alerts: [EngineAlert], now: Date = Date()) -> [AppIssue] {
        var out: [AppIssue] = []

        if health?.killActive == true {
            out.append(.live(.blocking, .health, key: "kill",
                             title: "KILL active — writes frozen",
                             detail: "Every mutating appctl command refuses while the KILL file is set. Clear it in Actions.",
                             fix: .operatorCommand("ADS_MARKET=US python3 engine/appctl.py kill --off"),
                             at: now))
        }

        if let gate = econGate, gate.ok == false {
            let reasons = gate.reasons.isEmpty ? "No reason reported." : gate.reasons.joined(separator: "\n")
            out.append(.live(.blocking, .econGate, key: "econGate",
                             title: "US economics gate closed",
                             detail: "Economics-driven writes (negatives, promote, resetbids, nightly auto-apply) refuse.\n\(reasons)",
                             market: gate.market,
                             fix: .operatorCommand("ADS_MARKET=US python3 engine/map_products.py"),
                             at: now))
        }

        // health loaded but the gate read failed (they're fetched together):
        // nil must not read as "gate open" — the state is UNKNOWN, and the
        // engine self-gates, but the operator should know the app is blind.
        if health != nil, econGate == nil {
            out.append(.live(.warning, .econGate, key: "econGateUnreadable",
                             title: "US economics gate unreadable",
                             detail: "The econ-gate read failed, so the gate state is unknown to the app (the engine still self-gates). The bridge failure is in Errors.",
                             fix: .operatorCommand("ADS_MARKET=US python3 engine/appctl.py econ-gate"),
                             at: now))
        }

        for market in health?.markets ?? [] {
            if market.configured == false {
                out.append(.live(.warning, .health, key: "unconfigured:\(market.market)",
                                 title: "\(market.market): market not configured",
                                 market: market.market, at: now))
                continue
            }
            if market.hasData, let latest = market.latestData, let date = Format.date(latest) {
                // Two INDEPENDENT staleness signals — a flat "data date > 48h old"
                // alarm fired every morning for all six markets because Amazon's
                // reporting inherently lags 1-2 days and the calendar gap crosses
                // 48h before the 10:00 pull refreshes it (a false positive).
                //   1. Pull didn't run: last_pull older than 30h. The nightly
                //      re-pulls each market ~every 24h (US ~10:15 → IT ~14:41 stagger),
                //      so 30h clears the normal cycle but catches a skipped night.
                //      Missing/unparseable last_pull → rely on signal 2 only.
                //   2. Data frozen: latest data 4+ calendar days behind. FR/IT sit
                //      at a structural 2-day Amazon lag (= 3 days-behind by morning),
                //      so the backstop must be 4 to stay quiet in steady state while
                //      still catching pulls that run but never advance the data.
                let daysBehind = daysAgo(from: date, now: now)
                let pullStale: Bool = {
                    guard let pulledAt = Format.dateTime(market.lastPull) else { return false }
                    return now.timeIntervalSince(pulledAt) > 30 * 3600
                }()
                if pullStale || daysBehind >= 4 {
                    // Stale data doesn't freeze writes (that's Blocking) — it means the
                    // nightly is behind, which is an Error to chase, not a hard stop.
                    let why = pullStale
                        ? "Last pull was \(market.lastPull ?? "unknown") — the nightly job looks behind."
                        : "Latest local data is \(latest) (\(daysBehind) days behind)."
                    let tables = (market.staleTables?.isEmpty == false)
                        ? " Stuck tables: \(market.staleTables!.joined(separator: ", "))."
                        : ""
                    out.append(.live(.error, .stale, key: "stale:\(market.market)",
                                     title: "\(market.market): data \(daysBehind) days stale",
                                     detail: "\(why)\(tables) Check the nightly job in System Health.",
                                     market: market.market, fix: .pull(market: market.market), at: now))
                }
            }
            if let note = market.lastNote, !note.note.isEmpty, (note.kind ?? "").lowercased().contains("error") {
                out.append(.live(.error, .health, key: "pullnote:\(market.market)",
                                 title: "\(market.market): engine pull error",
                                 detail: "\(note.at) — \(note.note)", market: market.market,
                                 fix: .pull(market: market.market), at: now))
            }
            if let pending = market.reportsPending, pending > 0 {
                out.append(.live(.error, .health, key: "reports:\(market.market)",
                                 title: "\(market.market): \(pending) report\(pending == 1 ? "" : "s") stalled",
                                 detail: "Report jobs requested but not yet downloaded.",
                                 market: market.market, fix: .pull(market: market.market), at: now))
            }
        }

        if health?.approvalRequired == true {
            out.append(.live(.warning, .health, key: "approval",
                             title: "Approval gate on — auto-apply is preview-only",
                             detail: "run_scheduled runs phase2 in preview; the Approval Queue is the real gate.",
                             fix: .operatorCommand("ADS_MARKET=US python3 engine/appctl.py approval-mode --off"),
                             at: now))
        }

        // The nightly loop deliberately continues past a crashed phase (one
        // market must not strand the other five) — this is where the crash
        // finally gets heard, since Discord digests are off.
        if let run = health?.lastRun, !run.ok, !run.failures.isEmpty {
            let list = run.failures
                .map { "\($0.market)/\($0.step) exit \($0.exit)" }
                .joined(separator: ", ")
            out.append(.live(.error, .health, key: "lastRun:\(run.finished ?? "unknown")",
                             title: "Nightly run: \(run.failures.count) step\(run.failures.count == 1 ? "" : "s") failed",
                             detail: "\(list). The run continued past the failure — the full trace is in outputs/scheduled_runs.log.",
                             fix: .operatorCommand("grep -B2 'STEP FAILED' outputs/scheduled_runs.log | tail -20"),
                             at: now))
        }

        for alert in alerts {
            // Deep-link the alert to the exact entity so "Review →" lands on the
            // specific design/campaign, not just the screen.
            out.append(.live(.warning, .alert, key: "alert:\(alert.key)",
                             title: alert.message, market: alert.market,
                             fix: .reviewRoute(alertRoute(alert)), at: now))
        }

        return out
    }

    /// Where "Review →" should land for an engine alert. kill_candidate → the
    /// design's ad group (or its ASIN if the campaign is unknown); budget_max →
    /// the campaign; spend_spike is market-wide, but the engine attributes it to
    /// the campaign whose spend grew the most (best-effort), so land on that
    /// campaign when known, else the market's Dashboard (the caller switches
    /// market from the issue's `market`). Falls back to the relevant screen when
    /// structured ids are missing (older engine output).
    static func alertRoute(_ alert: EngineAlert) -> Route {
        let market = alert.market
        switch alert.kind {
        case "kill_candidate":
            if let m = market, let cid = alert.campaignId, let ag = alert.adGroupId {
                return .adGroup(market: m, campaignID: cid, adGroupID: ag)
            }
            if let m = market, let asin = alert.asin, !asin.isEmpty {
                return .asin(market: m, asin: asin)
            }
            return .screen(.killList)
        case "budget_max":
            if let m = market, let cid = alert.campaignId {
                return .campaign(market: m, campaignID: cid)
            }
            return .screen(.campaigns)
        case "spend_spike":
            // Market-wide daily spend; the engine names the likely driver campaign
            // when it can. Land there, else the market's Dashboard (spend chart).
            if let m = market, let cid = alert.campaignId {
                return .campaign(market: m, campaignID: cid)
            }
            return .screen(.dashboard)
        case "data_stale":
            // A perf table's report job stopped landing — System Health shows
            // which tables and why per market.
            return .screen(.health)
        case "portfolio_cap":
            // Pooled month-to-date spend nearing the market's monthly cap — a
            // portfolio-wide condition, so land on the market's Dashboard.
            return .screen(.dashboard)
        default:
            return .screen(.dashboard)
        }
    }

    private static func daysAgo(from date: Date, now: Date) -> Int {
        max(0, Int(now.timeIntervalSince(date) / 86_400))
    }
}
