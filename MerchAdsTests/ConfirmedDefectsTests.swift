import Foundation
import XCTest
@testable import Merch_Ads

final class ConfirmedDefectsTests: XCTestCase {
    /// Reads a captured engine reply with the decoder the APP uses.
    ///
    /// This used to build its own JSONDecoder and set
    /// `.convertFromSnakeCase` on it. Every test below then decoded correctly
    /// whatever the bridge did — proved on 2026-08-24 by switching the
    /// bridge's strategy to `.useDefaultKeys` and watching all 259 tests pass.
    /// Borrowing the real one turns these fixtures into a guard on the wire
    /// contract instead of a guard on this line.
    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try PythonBridge.makeDecoder().decode(T.self, from: Data(json.utf8))
    }

    private func source(_ relativePath: String) throws -> String {
        let root = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent()
        return try String(contentsOf: root.appendingPathComponent(relativePath),
                          encoding: .utf8)
    }

    func testC1EverywherePreviewFailsClosed() throws {
        let text = try source("MerchAds/Views/Actions/EverywhereActions.swift")
        XCTAssertTrue(text.contains("async throws -> EverywherePending?"))
        XCTAssertTrue(text.contains("A preview failure is thrown"))
        XCTAssertFalse(text.contains("dialog degrades gracefully"))
    }

    func testC2EverywhereApplyReportsAllCountsAndPartialFailure() {
        let partial = EverywhereApplySummary(applied: 7, skipped: 2, failed: 1)
        XCTAssertEqual(partial.message, "Applied 7 · skipped 2 · failed 1.")
        XCTAssertTrue(partial.isPartialFailure)
        XCTAssertFalse(EverywhereApplySummary(applied: 7, skipped: 2, failed: 0)
            .isPartialFailure)
    }

    func testC3RoyaltyEditingRequiresTheLoadedMarket() {
        XCTAssertTrue(ProductRoyaltyView.canEdit(
            payloadMarket: "DE", selectedMarket: "DE", engineEditable: true))
        XCTAssertFalse(ProductRoyaltyView.canEdit(
            payloadMarket: "US", selectedMarket: "DE", engineEditable: true))
        XCTAssertFalse(ProductRoyaltyView.canEdit(
            payloadMarket: "DE", selectedMarket: "DE", engineEditable: false))
    }

    func testC4LiveStatusTokenRejectsOldGenerationMarketAndQuery() {
        let token = LiveStatusRequestToken(
            generation: 4, market: "US", query: "B0TEST", allMarkets: false)
        XCTAssertTrue(token.matches(generation: 4, market: "US",
                                    query: "B0TEST", allMarkets: false))
        XCTAssertFalse(token.matches(generation: 5, market: "US",
                                     query: "B0TEST", allMarkets: false))
        XCTAssertFalse(token.matches(generation: 4, market: "DE",
                                     query: "B0TEST", allMarkets: false))
        XCTAssertFalse(token.matches(generation: 4, market: "US",
                                     query: "B0OTHER", allMarkets: false))
    }

    func testC5BuilderExitCodesControlOutcomeAndStage() throws {
        let partial = try decode(ImportApplyResponse.self, """
        {"market":"US",
         "lottery":{"scoped_to":2,"code":0,"text":"ok"},
         "scavenger":{"scoped_to":3,"code":9,"text":"failed","stderr":"boom"}}
        """)
        XCTAssertEqual(partial.builderOutcome, .partialFailure)
        XCTAssertEqual(partial.builderFailureSummary, "Scavenger exited 9")
        XCTAssertEqual(NewDesignsBuildView.stageLabel(responses: [partial], hasPreview: true),
                       "Partial failure")

        let failed = try decode(ImportApplyResponse.self, """
        {"market":"DE",
         "lottery":{"scoped_to":2,"code":3,"text":"failed"},
         "scavenger":{"scoped_to":3,"code":9,"text":"failed"}}
        """)
        XCTAssertEqual(failed.builderOutcome, .failure)
        XCTAssertEqual(NewDesignsBuildView.stageLabel(responses: [failed], hasPreview: true),
                       "Failed")
    }

    /// The wire keys are `ytd_partial` and `ytd_first_month`, matching
    /// ytd_spend / ytd_sales / ytd_supplemented / ytd_basis in the same row.
    /// This fixture first guessed `partial` / `first_month` and passed, because
    /// a key nothing decodes just leaves the property nil and the FALLBACK
    /// answered instead — the assertion could not tell the two apart.
    /// tests/overview_ytd_contract_tests.py pins the engine's half.
    func testC6OverviewCarriesOrInfersTheActualYTDStart() throws {
        let explicit = try decode(OverviewMarket.self, """
        {"market":"UK","currency":"GBP","as_of":"2026-08-23",
         "spend":1,"sales":2,"orders":1,"clicks":3,
         "ytd_spend":10,"ytd_sales":20,"ytd_partial":true,
         "ytd_first_month":"2026-06"}
        """)
        XCTAssertEqual(explicit.ytdStartLabel(fallbackFirstDay: nil),
                       "Partial · since Jun 2026")

        let fallback = try decode(OverviewMarket.self, """
        {"market":"DE","currency":"EUR","as_of":"2026-08-23",
         "spend":1,"sales":2,"orders":1,"clicks":3,
         "ytd_spend":10,"ytd_sales":20}
        """)
        XCTAssertEqual(fallback.ytdStartLabel(fallbackFirstDay: "2026-06-24"),
                       "Partial · since Jun 2026")
    }

    func testC7StreamErrorsAreUnavailableUnlessSupportIsCleanlyFalse() throws {
        XCTAssertEqual(DashboardStreamPanelState.resolve(
            responseSupported: nil, error: "database is locked", isLoading: false),
                       .unavailable("database is locked"))
        XCTAssertEqual(DashboardStreamPanelState.resolve(
            responseSupported: false, error: nil, isLoading: false), .hidden)

        let health = try decode(StreamHealth.self, """
        {"configured":false,"error":"database is locked"}
        """)
        XCTAssertEqual(health.error, "database is locked")
        XCTAssertFalse(health.configured)
    }

    func testC8RulesListAndDetailHaveExplicitFailureStates() throws {
        let text = try source("MerchAds/Views/Rules/RulesView.swift")
        XCTAssertTrue(text.contains("Rules unavailable"))
        XCTAssertTrue(text.contains("Enabled Auto rules may still be running nightly"))
        XCTAssertTrue(text.contains("selection = previous"))
        XCTAssertFalse(text.contains("try? await bridge.call(RuleListResponse.self"))
        XCTAssertFalse(text.contains("try? await bridge.call(Rule.self, [\"rules-get\""))
    }

    func testC9PromotionRequiresEveryPhaseToExitZero() {
        let failed = PromotionApplySummary(keywordExit: 0, asinExit: 2)
        XCTAssertEqual(failed.partialFailureMessage,
                       "Promotion partial failure: ASIN phase exited 2")
        XCTAssertNil(PromotionApplySummary(keywordExit: 0, asinExit: 0)
            .partialFailureMessage)
    }

    func testC10BidReportKeepsItsTableMounted() throws {
        let text = try source("MerchAds/Views/Analysis/BidReportView.swift")
        XCTAssertTrue(text.contains("reportContent(report)"))
        XCTAssertTrue(text.contains("private func reportContent(_ report: BidReportResponse?)"))
        XCTAssertFalse(text.contains("if let report { reportContent(report) }"))
    }

    func testC11BreakdownFailureIsNotAnEmptyResult() {
        XCTAssertEqual(BreakdownPresentation.resolve(
            isLoading: false, error: "read failed", isEmpty: true),
                       .unavailable("read failed"))
        XCTAssertEqual(BreakdownPresentation.resolve(
            isLoading: false, error: nil, isEmpty: true), .empty)
    }

    func testC12PreSwitchMissingHourIsNotCalledLost() {
        let partial = StreamTodayView.hourHelp(
            "05", hour: nil, isPartial: true, isMissing: false, currency: "USD")
        XCTAssertTrue(partial.contains("before Stream was switched on"))
        XCTAssertFalse(partial.contains("never delivered"))
        XCTAssertTrue(StreamTodayView.hourHelp(
            "06", hour: nil, isPartial: false, isMissing: true, currency: "USD")
            .contains("never delivered"))
        XCTAssertFalse(StreamTodayView.hourHelp(
            "07", hour: nil, isPartial: false, isMissing: false, currency: "USD")
            .contains("never delivered"))
    }

    func testC13RulesApproveDecodesIntegerAppliedAndRowFailures() throws {
        let response = try decode(RulesApproveResponse.self, """
        {"market":"US","applied":0,"count":0,
         "results":[{"entity_kind":"target","entity_id":"t1","label":"kw",
                     "action":"set_bid","status":"failed","http":[400]},
                    {"entity_kind":"target","entity_id":"t2","label":"kw2",
                     "action":"set_bid","status":"skipped_noop"}],
         "note":"nothing moved","message":"review failures"}
        """)
        XCTAssertEqual(response.applied, false)
        XCTAssertEqual(response.notAppliedResults.count, 2)
        XCTAssertTrue(response.resultFailureNote?.contains("HTTP 400") == true)
        XCTAssertTrue(response.resultFailureNote?.contains("skipped noop") == true)
        XCTAssertEqual(response.note, "nothing moved")
        XCTAssertEqual(response.message, "review failures")
    }

    func testC13SeasonalStreamBackfillAndExportTruthFieldsDecode() throws {
        let seasonal = try decode(SeasonalApplyResponse.self, """
        {"market":"US","paused":0,"enabled":1,
         "errors":[{"seasonal_pause":[207],"rejected":["ag-7"]}]}
        """)
        XCTAssertTrue(seasonal.partialFailureNote?.contains("ag-7") == true)

        let stream = try decode(StreamHealth.self, """
        {"configured":true,"drain_stale":true,"drain_stale_realms":["EU"],
         "drain_by_realm":{"NA":{"last_drain":"now","age_minutes":4,"stale":false},
                           "EU":{"last_drain":"old","age_minutes":190,"stale":true}},
         "datasets":[
           {"realm":"NA","dataset":"sp-traffic","messages":1,"state":"flowing"},
           {"realm":"EU","dataset":"sp-traffic","messages":2,"state":"quiet"}]}
        """)
        XCTAssertEqual(stream.datasets?.map(\.id), ["NA|sp-traffic", "EU|sp-traffic"])
        XCTAssertEqual(stream.drainByRealm?["EU"]?.ageMinutes, 190)

        let backfill = try decode(BackfillResponse.self, """
        {"market":"DE","code":1,"text":"","stderr":"Traceback: disk full"}
        """)
        XCTAssertEqual(backfill.failureTail, "Traceback: disk full")

        let export = try decode(ExportDateResponse.self, """
        {"available":false,"note":"no catalogue export in the POD folder"}
        """)
        XCTAssertEqual(export.note, "no catalogue export in the POD folder")
    }

    func testC14CoordinatorRejectsAnIntentFromTheOldSettingsContext() async {
        let fake = ConfirmedDefectFakeExecutor()
        let coordinator = ActionCoordinator(executor: fake, executionContextID: "new")
        let intent = ActionIntent(title: "Pause", arguments: ["pause"],
                                  scope: .market("US"), executionContextID: "old")
        do {
            _ = try await coordinator.execute(
                intent, context: ActionPolicyContext(alwaysConfirm: false, killActive: false))
            XCTFail("stale settings context executed")
        } catch {
            XCTAssertEqual(error as? ActionCoordinatorError, .staleExecutionContext)
        }
        let executionCount = await fake.executionCount()
        XCTAssertEqual(executionCount, 0)
    }

    func testC14SettingsContextIncludesRootDataRootAndPython() {
        let root = URL(fileURLWithPath: "/tmp/one/engine")
        let first = AppSettings.actionExecutionContextID(
            engineRoot: root, pythonOverride: "/usr/bin/python3")
        let second = AppSettings.actionExecutionContextID(
            engineRoot: root, pythonOverride: "/opt/python3")
        XCTAssertNotEqual(first, second)
        XCTAssertTrue(first.contains("/tmp/one"))
    }

    func testC15NightlyPathsUseDataRootAndWatchFinalStatus() throws {
        let root = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("nightly-watch-\(UUID().uuidString)")
        let engine = root.appendingPathComponent("engine")
        let outputs = root.appendingPathComponent("outputs")
        try FileManager.default.createDirectory(at: engine, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: outputs, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        XCTAssertEqual(NightlyRunMonitor.logURL(engineRoot: engine.path),
                       outputs.appendingPathComponent("scheduled_runs.log"))
        try Data("{}".utf8).write(to: outputs.appendingPathComponent("last_run_status.json"))
        let stamps = AppState.currentTimestamps(markets: [], dataRoot: root)
        XCTAssertNotNil(stamps[AppState.lastRunStatusWatchKey])
    }
}

private actor ConfirmedDefectFakeExecutor: ActionExecuting {
    private var executions = 0

    func preview(_ intent: ActionIntent) async throws -> ActionPreviewReceipt {
        ActionPreviewReceipt(intentID: intent.id, summary: "preview")
    }

    func execute(_ intent: ActionIntent) async throws -> ActionExecutionReceipt {
        executions += 1
        return ActionExecutionReceipt(intentID: intent.id, scope: intent.scope,
                                      auditVisibility: intent.auditVisibility,
                                      rehearsed: false, summary: "executed")
    }

    func executionCount() -> Int { executions }
}
