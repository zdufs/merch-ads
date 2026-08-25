import XCTest
@testable import Merch_Ads

@MainActor
final class IssueCenterTests: XCTestCase {

    private func makeAppIssue(key: String, severity: IssueSeverity = .error,
                              at: Date = Date()) -> AppIssue {
        AppIssue(id: UUID().uuidString, severity: severity, source: .appCall,
                 title: "t", detail: nil, market: nil, timestamp: at,
                 dedupKey: key, dismissable: true)
    }

    // MARK: - IssueCenter add / dismiss / clear / dedup

    func testAddAppendsDistinctKeys() {
        let center = IssueCenter()
        center.add(makeAppIssue(key: "a"))
        center.add(makeAppIssue(key: "b"))
        XCTAssertEqual(center.appIssues.count, 2)
    }

    func testAddDedupesSameKeyAndBumpsCount() {
        let center = IssueCenter()
        let early = Date(timeIntervalSince1970: 1_000)
        let late = Date(timeIntervalSince1970: 2_000)
        center.add(makeAppIssue(key: "dup", at: early))
        center.add(makeAppIssue(key: "dup", at: late))
        XCTAssertEqual(center.appIssues.count, 1)
        XCTAssertEqual(center.appIssues[0].count, 2)
        XCTAssertEqual(center.appIssues[0].timestamp, late)   // latest wins
    }

    func testDismissRemovesById() {
        let center = IssueCenter()
        let issue = makeAppIssue(key: "a")
        center.add(issue)
        center.dismiss(issue.id)
        XCTAssertTrue(center.appIssues.isEmpty)
    }

    func testClearAppRemovesEverything() {
        let center = IssueCenter()
        center.add(makeAppIssue(key: "a"))
        center.add(makeAppIssue(key: "b"))
        center.clearApp()
        XCTAssertTrue(center.appIssues.isEmpty)
    }

    // MARK: - IssueDerivation.live

    private let now = Date(timeIntervalSince1970: 1_800_000_000)

    private func market(_ code: String, configured: Bool = true, hasData: Bool = true,
                        latest: String? = nil, note: PullNote? = nil,
                        pending: Int? = nil, staleTables: [String]? = nil,
                        targetDaily: TargetDailyCoverage? = nil,
                        lastPull: String? = nil,
                        dayHistory: DayHistoryCoverage? = nil) -> MarketHealth {
        MarketHealth(market: code, configured: configured, hasData: hasData,
                     latestData: latest, lastPull: lastPull, lastWrite: nil,
                     campaigns: 1, campaignsEnabled: 1,
                     lastNote: note, reportsPending: pending, staleTables: staleTables,
                     targetDaily: targetDaily, dailyTotals: dayHistory,
                     bidCeiling: nil, error: nil)
    }

    private func dayHistory(last: String, behind: Int, stale: Bool) -> DayHistoryCoverage {
        DayHistoryCoverage(days: 52, first: "2026-06-24", last: last,
                           behindDays: behind, stale: stale, reason: stale ? "stale" : "")
    }

    /// The engine's own timestamp form, N hours before `now`.
    private func pulled(hoursAgo: Double) -> String {
        Format.engineTimestamp(of: now.addingTimeInterval(-hoursAgo * 3_600))
    }

    func testHealthyInputsProduceNoIssues() {
        let today = Format.dayString(of: now)
        let health = HealthResponse(killActive: false, approvalRequired: false,
                                    markets: [market("US", latest: today)])
        let gate = EconGateResponse(ok: true, reasons: [], market: "US", modelVersion: "v", currency: "USD", catalog: nil, econCoverage: nil)
        let issues = IssueDerivation.live(health: health, econGate: gate, alerts: [], now: now)
        XCTAssertTrue(issues.isEmpty)
    }

    func testKillActiveIsBlocking() {
        let health = HealthResponse(killActive: true, approvalRequired: false, markets: [])
        let issues = IssueDerivation.live(health: health, econGate: nil, alerts: [], now: now)
        XCTAssertEqual(issues.filter { $0.severity == .blocking && $0.dedupKey == "kill" }.count, 1)
    }

    func testClosedEconGateIsBlockingWithReasons() {
        let gate = EconGateResponse(ok: false, reasons: ["stale export", "STALE marker"],
                                    market: "US", modelVersion: "v", currency: "USD", catalog: nil, econCoverage: nil)
        let issues = IssueDerivation.live(health: nil, econGate: gate, alerts: [], now: now)
        let gateIssue = issues.first { $0.dedupKey == "econGate" }
        XCTAssertNotNil(gateIssue)
        XCTAssertEqual(gateIssue?.severity, .blocking)
        XCTAssertTrue(gateIssue?.detail?.contains("stale export") ?? false)
    }

    func testStaleMarketIsError() {
        let old = Format.dayString(of: now.addingTimeInterval(-5 * 24 * 3600))
        let health = HealthResponse(killActive: false, approvalRequired: false,
                                    markets: [market("DE", latest: old)])
        let issues = IssueDerivation.live(health: health, econGate: nil, alerts: [], now: now)
        // Stale data is an Error to chase, not a Blocking write-freeze.
        XCTAssertEqual(issues.filter { $0.dedupKey == "stale:DE" && $0.severity == .error }.count, 1)
    }

    // The nightly ran US-only for five nights (Aug 2026) because its market
    // discovery silently fell back to US. The app DID catch it — but titled it
    // "data 3 days stale", an age the engine calls fresh, while System Health
    // showed the same markets "Clear". Both surfaces read one rule now.
    func testASkippedPullIsTitledByTheSignalThatFired() {
        let today = Format.dayString(of: now)
        let behind = market("DE", latest: today, lastPull: pulled(hoursAgo: 37))
        let health = HealthResponse(killActive: false, approvalRequired: false, markets: [behind])
        let issues = IssueDerivation.live(health: health, econGate: nil, alerts: [], now: now)
        let issue = issues.first { $0.dedupKey == "stale:DE" }
        XCTAssertEqual(issue?.severity, .error)
        XCTAssertEqual(issue?.title, "DE: no pull for 37h")
        XCTAssertFalse(issue?.title.contains("days stale") ?? true)
        XCTAssertTrue(behind.pullIsBehind(now: now), "System Health must see the same thing")
    }

    func testAFreshPullRaisesNothing() {
        let today = Format.dayString(of: now)
        let fine = market("UK", latest: today, lastPull: pulled(hoursAgo: 26))
        let health = HealthResponse(killActive: false, approvalRequired: false, markets: [fine])
        let gate = EconGateResponse(ok: true, reasons: [], market: "US", modelVersion: "v", currency: "USD", catalog: nil, econCoverage: nil)
        XCTAssertTrue(IssueDerivation.live(health: health, econGate: gate, alerts: [], now: now).isEmpty)
        XCTAssertFalse(fine.pullIsBehind(now: now))
    }

    func testFrozenDataStillReadsAsAnAgeWhenThePullItselfRan() {
        let old = Format.dayString(of: now.addingTimeInterval(-5 * 24 * 3600))
        let frozen = market("FR", latest: old, lastPull: pulled(hoursAgo: 2))
        let health = HealthResponse(killActive: false, approvalRequired: false, markets: [frozen])
        let issues = IssueDerivation.live(health: health, econGate: nil, alerts: [], now: now)
        XCTAssertEqual(issues.first { $0.dedupKey == "stale:FR" }?.title, "FR: data 5 days stale")
        XCTAssertFalse(frozen.pullIsBehind(now: now))
    }

    // daily_totals is banked by daily_metrics.py, NOT by the perf pull. It sat
    // five days behind for every EU market while "Data through" read fresh, so
    // the dashboard's day grid greyed out and no screen explained it.
    func testAStaleDayHistoryIsItsOwnError() {
        let today = Format.dayString(of: now)
        let m = market("IT", latest: today, lastPull: Format.engineTimestamp(of: now),
                       dayHistory: dayHistory(last: "2026-08-14", behind: 6, stale: true))
        let health = HealthResponse(killActive: false, approvalRequired: false, markets: [m])
        let issues = IssueDerivation.live(health: health, econGate: nil, alerts: [], now: now)
        let issue = issues.first { $0.dedupKey == "dayhistory:IT" }
        XCTAssertEqual(issue?.severity, .error)
        XCTAssertEqual(issue?.title, "IT: day history is 6 days behind")
        XCTAssertTrue(issue?.detail?.contains("2026-08-14") ?? false)
    }

    func testAFreshDayHistoryRaisesNothing() {
        let today = Format.dayString(of: now)
        let m = market("IT", latest: today, lastPull: Format.engineTimestamp(of: now),
                       dayHistory: dayHistory(last: today, behind: 1, stale: false))
        let health = HealthResponse(killActive: false, approvalRequired: false, markets: [m])
        let gate = EconGateResponse(ok: true, reasons: [], market: "US", modelVersion: "v", currency: "USD", catalog: nil, econCoverage: nil)
        XCTAssertTrue(IssueDerivation.live(health: health, econGate: gate, alerts: [], now: now).isEmpty)
    }

    func testUnconfiguredMarketIsWarning() {
        let health = HealthResponse(killActive: false, approvalRequired: false,
                                    markets: [market("IT", configured: false, hasData: false)])
        let issues = IssueDerivation.live(health: health, econGate: nil, alerts: [], now: now)
        XCTAssertEqual(issues.filter { $0.dedupKey == "unconfigured:IT" && $0.severity == .warning }.count, 1)
    }

    func testPullErrorNoteAndStalledReportsAreErrors() {
        let note = PullNote(at: "2026-07-19", kind: "error", note: "report timeout")
        let health = HealthResponse(killActive: false, approvalRequired: false,
                                    markets: [market("US", latest: Format.dayString(of: now),
                                                     note: note, pending: 3)])
        let issues = IssueDerivation.live(health: health, econGate: nil, alerts: [], now: now)
        XCTAssertEqual(issues.filter { $0.dedupKey == "pullnote:US" && $0.severity == .error }.count, 1)
        XCTAssertEqual(issues.filter { $0.dedupKey == "reports:US" && $0.severity == .error }.count, 1)
    }

    func testAlertsBecomeWarnings() {
        let alerts = [EngineAlert(kind: "spend_spike", key: "k1", message: "Spend spiked")]
        let issues = IssueDerivation.live(health: nil, econGate: nil, alerts: alerts, now: now)
        XCTAssertEqual(issues.filter { $0.dedupKey == "alert:k1" && $0.severity == .warning }.count, 1)
    }

    func testFailedNightlyRunIsError() {
        let run = LastRunStatus(started: "2026-08-05T10:00:00", finished: "2026-08-05T10:41:00",
                                ok: false,
                                failures: [RunStepFailure(market: "US", step: "phase0_pull", exit: 1)],
                                markets: ["US"])
        let health = HealthResponse(killActive: false, approvalRequired: false,
                                    lastRun: run, markets: [])
        let issues = IssueDerivation.live(health: health, econGate: nil, alerts: [], now: now)
        let issue = issues.first { $0.dedupKey == "lastRun:2026-08-05T10:41:00" }
        XCTAssertEqual(issue?.severity, .error)
        XCTAssertTrue(issue?.detail?.contains("phase0_pull") ?? false)
    }

    func testCleanNightlyRunRaisesNoIssue() {
        let run = LastRunStatus(started: "s", finished: "f", ok: true, failures: [], markets: nil)
        let health = HealthResponse(killActive: false, approvalRequired: false,
                                    lastRun: run, markets: [])
        let gate = EconGateResponse(ok: true, reasons: [], market: "US", modelVersion: "v", currency: "USD", catalog: nil, econCoverage: nil)
        let issues = IssueDerivation.live(health: health, econGate: gate, alerts: [], now: now)
        XCTAssertTrue(issues.isEmpty)
    }

    /// A market row that is configured, with nothing else filled in — enough for
    /// the run-coverage comparison and nothing more.
    private func configuredMarket(_ code: String) -> MarketHealth {
        MarketHealth(market: code, configured: true, hasData: true,
                     latestData: nil, lastPull: nil, lastWrite: nil,
                     campaigns: nil, campaignsEnabled: nil, lastNote: nil,
                     reportsPending: nil, staleTables: nil, targetDaily: nil,
                     dailyTotals: nil, bidCeiling: BidCeilingRow(target: 0.5, keyword: 0.5, budget: nil),
                     error: nil)
    }

    /// The 2026-08-16 → 08-20 incident: every step passed, US alone was
    /// advertised, and nothing anywhere in the app said so.
    func testARunThatSkippedMarketsIsAWarning() {
        let run = LastRunStatus(started: "2026-08-20T10:00:02", finished: "2026-08-20T10:51:59",
                                ok: true, failures: [], markets: ["US"])
        let health = HealthResponse(killActive: false, approvalRequired: false, lastRun: run,
                                    markets: ["US", "UK", "DE"].map(configuredMarket))
        let gate = EconGateResponse(ok: true, reasons: [], market: "US", modelVersion: "v", currency: "USD", catalog: nil, econCoverage: nil)
        let issues = IssueDerivation.live(health: health, econGate: gate, alerts: [], now: now)
        let issue = issues.first { $0.dedupKey == "lastRunMarkets:2026-08-20T10:51:59" }
        XCTAssertEqual(issue?.severity, .warning)
        XCTAssertTrue(issue?.detail?.contains("UK DE") ?? false,
                      "the operator has to be told WHICH markets went unadvertised")
    }

    func testAFullRunRaisesNoCoverageWarning() {
        let run = LastRunStatus(started: "s", finished: "f", ok: true, failures: [],
                                markets: ["US", "UK", "DE"])
        let health = HealthResponse(killActive: false, approvalRequired: false, lastRun: run,
                                    markets: ["US", "UK", "DE"].map(configuredMarket))
        let gate = EconGateResponse(ok: true, reasons: [], market: "US", modelVersion: "v", currency: "USD", catalog: nil, econCoverage: nil)
        let issues = IssueDerivation.live(health: health, econGate: gate, alerts: [], now: now)
        XCTAssertTrue(issues.isEmpty)
    }

    /// An engine too old to report the market list must stay silent. A warning
    /// that fires every single morning is one the operator stops reading.
    func testAnEngineWithNoMarketListRaisesNoCoverageWarning() {
        let run = LastRunStatus(started: "s", finished: "f", ok: true, failures: [], markets: nil)
        let health = HealthResponse(killActive: false, approvalRequired: false, lastRun: run,
                                    markets: ["US", "UK"].map(configuredMarket))
        let gate = EconGateResponse(ok: true, reasons: [], market: "US", modelVersion: "v", currency: "USD", catalog: nil, econCoverage: nil)
        let issues = IssueDerivation.live(health: health, econGate: gate, alerts: [], now: now)
        XCTAssertTrue(issues.isEmpty)
    }

    func testUnreadableEconGateIsWarningNotSilence() {
        // health loaded but the econ-gate read failed: nil used to be
        // indistinguishable from "gate open" — automation running blind.
        let health = HealthResponse(killActive: false, approvalRequired: false,
                                    markets: [market("US", latest: Format.dayString(of: now))])
        let issues = IssueDerivation.live(health: health, econGate: nil, alerts: [], now: now)
        XCTAssertEqual(issues.filter { $0.dedupKey == "econGateUnreadable"
                                        && $0.severity == .warning }.count, 1)
    }

    // MARK: - issue → fix mapping

    func testStaleFixIsPull() {
        let old = Format.dayString(of: now.addingTimeInterval(-5 * 24 * 3600))
        let health = HealthResponse(killActive: false, approvalRequired: false,
                                    markets: [market("DE", latest: old)])
        let issues = IssueDerivation.live(health: health, econGate: nil, alerts: [], now: now)
        XCTAssertEqual(issues.first { $0.dedupKey == "stale:DE" }?.fix, .pull(market: "DE"))
    }

    func testStalledReportsFixIsPull() {
        let health = HealthResponse(killActive: false, approvalRequired: false,
                                    markets: [market("US", latest: Format.dayString(of: now), pending: 2)])
        let issues = IssueDerivation.live(health: health, econGate: nil, alerts: [], now: now)
        XCTAssertEqual(issues.first { $0.dedupKey == "reports:US" }?.fix, .pull(market: "US"))
    }

    func testKillFixIsOperatorCommandOff() {
        let health = HealthResponse(killActive: true, approvalRequired: false, markets: [])
        let issues = IssueDerivation.live(health: health, econGate: nil, alerts: [], now: now)
        guard case .operatorCommand(let cmd)? = issues.first(where: { $0.dedupKey == "kill" })?.fix else {
            return XCTFail("kill issue should carry an operatorCommand fix")
        }
        XCTAssertTrue(cmd.contains("kill --off"))
    }

    func testEconGateFixRemapsProducts() {
        let gate = EconGateResponse(ok: false, reasons: ["stale"], market: "US", modelVersion: "v", currency: "USD", catalog: nil, econCoverage: nil)
        let issues = IssueDerivation.live(health: nil, econGate: gate, alerts: [], now: now)
        guard case .operatorCommand(let cmd)? = issues.first(where: { $0.dedupKey == "econGate" })?.fix else {
            return XCTFail("econ gate should carry an operatorCommand fix")
        }
        XCTAssertTrue(cmd.contains("map_products.py"))
    }

    func testKillCandidateAlertDeepLinksToAdGroup() {
        let alerts = [EngineAlert(kind: "kill_candidate", key: "kill:US:513", message: "kc",
                                  market: "US", campaignId: "124", adGroupId: "513", asin: "B0TESTAAAA")]
        let issues = IssueDerivation.live(health: nil, econGate: nil, alerts: alerts, now: now)
        let issue = issues.first { $0.dedupKey == "alert:kill:US:513" }
        XCTAssertEqual(issue?.fix, .reviewRoute(.adGroup(market: "US", campaignID: "124", adGroupID: "513")))
        XCTAssertEqual(issue?.market, "US")   // button switches to it before navigating
    }

    func testKillCandidateFallsBackToAsinWithoutCampaign() {
        let alerts = [EngineAlert(kind: "kill_candidate", key: "kill:FR:9", message: "kc",
                                  market: "FR", campaignId: nil, adGroupId: "9", asin: "B0TESTBBBB")]
        let issues = IssueDerivation.live(health: nil, econGate: nil, alerts: alerts, now: now)
        XCTAssertEqual(issues.first { $0.dedupKey == "alert:kill:FR:9" }?.fix,
                       .reviewRoute(.asin(market: "FR", asin: "B0TESTBBBB")))
    }

    func testBudgetMaxDeepLinksToCampaign() {
        let alerts = [EngineAlert(kind: "budget_max", key: "budget:DE:77", message: "capped",
                                  market: "DE", campaignId: "77")]
        let issues = IssueDerivation.live(health: nil, econGate: nil, alerts: alerts, now: now)
        XCTAssertEqual(issues.first { $0.dedupKey == "alert:budget:DE:77" }?.fix,
                       .reviewRoute(.campaign(market: "DE", campaignID: "77")))
    }

    func testSpendSpikeFallsBackToDashboardWithoutDriver() {
        let alerts = [EngineAlert(kind: "spend_spike", key: "spike:IT:2026-07-18", message: "spike",
                                  market: "IT")]
        let issues = IssueDerivation.live(health: nil, econGate: nil, alerts: alerts, now: now)
        let issue = issues.first { $0.dedupKey == "alert:spike:IT:2026-07-18" }
        XCTAssertEqual(issue?.fix, .reviewRoute(.screen(.dashboard)))
        XCTAssertEqual(issue?.market, "IT")
    }

    func testSpendSpikeDeepLinksToDriverCampaign() {
        let alerts = [EngineAlert(kind: "spend_spike", key: "spike:FR:2026-07-20",
                                  message: "spike — likely driver: LOTTO - 1",
                                  market: "FR", campaignId: "900000000000004")]
        let issues = IssueDerivation.live(health: nil, econGate: nil, alerts: alerts, now: now)
        let issue = issues.first { $0.dedupKey == "alert:spike:FR:2026-07-20" }
        XCTAssertEqual(issue?.fix, .reviewRoute(.campaign(market: "FR", campaignID: "900000000000004")))
        XCTAssertEqual(issue?.market, "FR")   // button switches to it before navigating
    }

    func testUnconfiguredMarketHasNoFix() {
        let health = HealthResponse(killActive: false, approvalRequired: false,
                                    markets: [market("IT", configured: false, hasData: false)])
        let issues = IssueDerivation.live(health: health, econGate: nil, alerts: [], now: now)
        XCTAssertNil(issues.first { $0.dedupKey == "unconfigured:IT" }?.fix)
    }

    // MARK: - severity ordering

    func testSeverityOrdering() {
        XCTAssertTrue(IssueSeverity.blocking > IssueSeverity.error)
        XCTAssertTrue(IssueSeverity.error > IssueSeverity.warning)
    }
}

// Test-only convenience initialisers.
//
// These exist so that adding a field to HealthResponse touches ONE place
// instead of twenty fixtures. It stopped being true on 2026-08-21: `stream`
// was added, only the memberwise call sites broke, and because CI runs the
// Python suite alone the whole Swift target simply stopped compiling — 22
// files' worth of tests silently not running until the next audit. Every
// fixture below goes through one of these two, so the next field is one edit.
extension HealthResponse {
    init(killActive: Bool, approvalRequired: Bool?, markets: [MarketHealth]) {
        self.init(killActive: killActive, approvalRequired: approvalRequired,
                  lastRun: nil, stream: nil, markets: markets)
    }

    init(killActive: Bool, approvalRequired: Bool?, lastRun: LastRunStatus?,
         markets: [MarketHealth]) {
        self.init(killActive: killActive, approvalRequired: approvalRequired,
                  lastRun: lastRun, stream: nil, markets: markets)
    }
}
