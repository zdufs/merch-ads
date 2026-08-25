import XCTest
@testable import Merch_Ads

/// Three engine fields that said what the data does NOT cover, and reached no
/// screen — found by the 2026-08-23 audit, all in the same shape as the ones
/// the 2026-08-22 review found.
///
/// The pattern is always the same: the engine is careful, the reply carries the
/// caveat, and the app decodes it into nothing. What is left on screen is not a
/// blank — it is a confident, complete-looking answer.
final class AuditTruthFieldsTests: XCTestCase {

    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        // Borrows the app's decoder. A test that builds its own proves
        // nothing about the one the bridge uses — see PythonBridge.makeDecoder.
        return try PythonBridge.makeDecoder().decode(T.self, from: Data(json.utf8))
    }

    // MARK: - killlist.econ

    /// `_design_be_for` returns nothing on a database that has never been
    /// pulled or mapped, and the engine answers with an empty list plus a
    /// sentence. Undecoded, that reply is byte-identical to a healthy market
    /// with nothing worth killing — and it is a FRESH INSTALL that produces it,
    /// so it was the first thing a new operator would have been told wrongly.
    func testEconomicsUnavailableIsNotAnEmptyKillList() throws {
        let r = try decode(KillListResponse.self, """
        {"market":"US","cvr_floor":0.08,"count":0,"designs":[],
         "econ":"unavailable — run a pull/map once to migrate the economics tables"}
        """)
        XCTAssertTrue(r.economicsUnavailable)
        XCTAssertEqual(r.count, 0)
        XCTAssertTrue(r.designs.isEmpty,
                      "the empty list is exactly what makes this dangerous")
    }

    func testAHealthyEmptyKillListIsNotFlagged() throws {
        let r = try decode(KillListResponse.self, """
        {"market":"US","cvr_floor":0.08,"count":0,"designs":[],
         "skipped":{"transition":0,"unknown_price":0,"cohort":0}}
        """)
        XCTAssertFalse(r.economicsUnavailable,
                       "a market with working economics and nothing to kill "
                       + "must keep reading as good news")
    }

    func testAnOlderEngineWithoutTheFieldStillDecodes() throws {
        let r = try decode(KillListResponse.self, """
        {"market":"US","cvr_floor":0.08,"count":0,"designs":[]}
        """)
        XCTAssertNil(r.econ)
        XCTAssertFalse(r.economicsUnavailable)
    }

    func testAnEmptyEconStringIsNotTreatedAsAFault() throws {
        let r = try decode(KillListResponse.self, """
        {"market":"US","cvr_floor":0.08,"count":0,"designs":[],"econ":""}
        """)
        XCTAssertFalse(r.economicsUnavailable)
    }

    // MARK: - import-apply.export_error

    /// Adopting the export is what makes it the engine's economics source. When
    /// that raises, the campaigns were still built, so the envelope is a
    /// SUCCESS and `export` is simply absent — which is also what `--no-adopt`
    /// and an older engine look like. The cost arrives days later as a stale
    /// economics gate, with nothing tying it back to this import.
    func testAFailedAdoptionSurvivesDecoding() throws {
        let r = try decode(ImportApplyResponse.self, """
        {"market":"US","built":12,
         "export_error":"[Errno 13] Permission denied: 'snap-grid-export.csv'"}
        """)
        XCTAssertNotNil(r.exportError)
        XCTAssertNil(r.export, "the two are mutually exclusive by construction")
    }

    func testACleanImportCarriesNoExportError() throws {
        let r = try decode(ImportApplyResponse.self, """
        {"market":"US","built":12,
         "export":{"adopted":"snap.csv","moved_to_pod":true,"removed":[],"freed_mb":0}}
        """)
        XCTAssertNil(r.exportError)
        XCTAssertNotNil(r.export)
    }

    // MARK: - everywhere-preview.instances

    /// The engine KEEPS the instances it will not write, precisely so a
    /// selection of many landing on few can explain itself. The app decoded
    /// none of them, and reported every skip with one sentence: "already at
    /// that state". That is true for a paused ad group and false for a keyword
    /// with no target id — which the app cannot address at all, so part of the
    /// operator's selection went quietly missing under a reassuring word.
    private var mixedSkipsJSON: String {
        """
        {"market":"US","kind":"keyword","action":"pause","as_of":"2026-08-21",
         "count":4,"applicable":1,"skipped_noop":3,"campaigns":2,
         "instances":[
          {"key":"kw","campaign":"C1","campaign_id":"c1","ad_group":"A1",
           "ad_group_id":"ag1","target_id":"t1","state":"ENABLED",
           "skip_reason":null,"skip":false},
          {"key":"kw","campaign":"C1","campaign_id":"c1","ad_group":"A2",
           "ad_group_id":"ag2","target_id":"t2","state":"PAUSED",
           "skip_reason":"already_paused","skip":true},
          {"key":"kw","campaign":"C2","campaign_id":"c2","ad_group":"A3",
           "ad_group_id":"ag3","target_id":null,"state":"ENABLED",
           "skip_reason":"unaddressable","skip":true},
          {"key":"kw","campaign":"C2","campaign_id":"c2","ad_group":"A4",
           "ad_group_id":"ag4","target_id":null,"state":"ENABLED",
           "skip_reason":"unaddressable","skip":true}]}
        """
    }

    func testTheInstanceListReachesTheApp() throws {
        let r = try decode(EverywherePreviewResponse.self, mixedSkipsJSON)
        XCTAssertEqual(r.instances?.count, 4)
        XCTAssertEqual(r.asOf, "2026-08-21")
    }

    func testTheTwoSkipReasonsAreCountedApart() throws {
        let r = try decode(EverywherePreviewResponse.self, mixedSkipsJSON)
        XCTAssertEqual(r.skippedAlreadyInState, 1,
                       "one ad group is genuinely already paused")
        XCTAssertEqual(r.skippedUnaddressable, 2,
                       "two have no target id — the app cannot write them, "
                       + "and they are not no-ops")
        XCTAssertEqual(r.skippedAlreadyInState + r.skippedUnaddressable
                       + r.skippedStateUnknown,
                       r.skippedNoop, "together they must account for every skip")
    }

    /// The fixture above is only worth anything if the engine really sends
    /// these fields. It did not: `_everywhere_slim` stripped `target_id`,
    /// `campaign_id`, `state` and `asin` before the reply left the engine, so
    /// the old version of this test hand-wrote a JSON shape production never
    /// produced — and passed, while the app miscounted every skip it ever saw.
    /// This asserts the reason is what decides the bucket, not a field's
    /// absence, which is the part that could drift back.
    func testTheSkipBucketFollowsTheReasonNotTheTargetId() throws {
        // An ASIN pause acts on AD GROUPS, which never carry a target id. Under
        // the old rule every one of these was "the app cannot address it".
        let r = try decode(EverywherePreviewResponse.self, """
        {"market":"US","kind":"asin","action":"pause","as_of":"2026-08-21",
         "count":2,"applicable":0,"skipped_noop":2,"campaigns":1,
         "instances":[
          {"key":"B0EXAMPLE1","campaign":"C1","campaign_id":"c1","ad_group":"A1",
           "ad_group_id":"ag1","target_id":null,"state":"PAUSED",
           "skip_reason":"already_paused","skip":true},
          {"key":"B0EXAMPLE1","campaign":"C1","campaign_id":"c1","ad_group":"A2",
           "ad_group_id":"ag2","target_id":null,"state":null,
           "skip_reason":"state_unknown","skip":true}]}
        """)
        XCTAssertEqual(r.skippedAlreadyInState, 1,
                       "an ad group with no target id is still a genuine no-op")
        XCTAssertEqual(r.skippedUnaddressable, 0,
                       "nothing here is unaddressable — an ASIN pause has no "
                       + "target ids by design")
        XCTAssertEqual(r.skippedStateUnknown, 1,
                       "a row we never mirrored is not 'already paused'")
    }

    /// An engine older than 2026-08-23 sends no reason at all. Every bucket
    /// must then read zero, so the app falls back to the plain total rather
    /// than drawing a confident and wrong breakdown.
    func testAnEngineWithNoSkipReasonReportsNoBreakdown() throws {
        let r = try decode(EverywherePreviewResponse.self, """
        {"market":"US","kind":"keyword","action":"pause","count":2,
         "applicable":1,"skipped_noop":1,"campaigns":1,
         "instances":[
          {"key":"kw","campaign":"C1","ad_group":"A1","ad_group_id":"ag1","skip":false},
          {"key":"kw","campaign":"C1","ad_group":"A2","ad_group_id":"ag2","skip":true}]}
        """)
        XCTAssertEqual(r.skippedAlreadyInState, 0)
        XCTAssertEqual(r.skippedUnaddressable, 0)
        XCTAssertEqual(r.skippedStateUnknown, 0)
        XCTAssertEqual(r.skippedNoop, 1, "the plain total still stands")
    }

    func testAnOlderEngineWithNoInstancesStillDecodes() throws {
        let r = try decode(EverywherePreviewResponse.self, """
        {"market":"US","kind":"asin","action":"pause","count":3,"applicable":3,
         "skipped_noop":0,"campaigns":1}
        """)
        XCTAssertNil(r.instances)
        XCTAssertEqual(r.skippedAlreadyInState, 0)
        XCTAssertEqual(r.skippedUnaddressable, 0)
    }

    // MARK: - demandfeed proven sellers

    /// The worst of the audit, because it was not a caveat gone missing — it
    /// was every number on the table.
    ///
    /// Snap for MOD exports no `salesLast30` / `royaltyLast30`, which has been
    /// true since it replaced MerchFlow on 2026-08-15. `demand_feed` therefore
    /// falls back to the design's LIFETIME royalty, ranks on it, and honestly
    /// writes 0 into `royalty_last30` because that figure is not a 30-day one.
    /// It says which window it used in `royalty_basis`, and puts the real
    /// number in `royalty`.
    ///
    /// `ProvenSeller` decoded neither. It read the zero. Measured against the
    /// live account on 2026-08-23: 60 of 60 rows drew 0.00 royalty and 0 sales,
    /// under the heading "Royalty 30d", including a design that had earned
    /// four figures across several thousand units. The ORDER was right, which
    /// is what made it
    /// look like a working screen.
    func testALifetimeBasisSellerShowsItsRealRoyalty() throws {
        let s = try decode(ProvenSeller.self, """
        {"asin":"B0EXAMPLE1","title":"A design","product_type":"standard_tshirt",
         "brand":"B","royalty_basis":"lifetime","royalty":192.35,
         "royalty_last30":0,"royalty_total":192.35,
         "sales_last30":0,"sales_total":313,"action":"variation"}
        """)
        XCTAssertEqual(s.royaltyLast30, 0, "the engine is right to send 0 here")
        XCTAssertEqual(s.royaltyShown, 192.35, "and the screen must not show it")
        XCTAssertEqual(s.salesShown, 313)
        XCTAssertTrue(s.isLifetimeBasis)
        XCTAssertEqual(s.basisLabel, "all time",
                       "a big number against the wrong window is its own lie")
    }

    func testALast30SellerStillReadsAsThirtyDays() throws {
        let s = try decode(ProvenSeller.self, """
        {"asin":"B0EXAMPLE1","title":"A design","product_type":"standard_tshirt",
         "brand":"B","royalty_basis":"last30","royalty":42.5,
         "royalty_last30":42.5,"royalty_total":900.0,
         "sales_last30":7,"sales_total":150,"action":"variation"}
        """)
        XCTAssertEqual(s.royaltyShown, 42.5)
        XCTAssertEqual(s.salesShown, 7)
        XCTAssertFalse(s.isLifetimeBasis)
        XCTAssertEqual(s.basisLabel, "30 days")
    }

    /// An engine that predates the `royalty` field must keep working, and with
    /// no basis to name the column stays generic rather than claiming one.
    func testAnOlderEngineFallsBackToTheThirtyDayFigure() throws {
        let s = try decode(ProvenSeller.self, """
        {"asin":"B0EXAMPLE1","title":"A design","product_type":"standard_tshirt",
         "brand":"B","royalty_last30":42.5,"sales_last30":7,"action":"variation"}
        """)
        XCTAssertEqual(s.royaltyShown, 42.5)
        XCTAssertEqual(s.salesShown, 7)
        XCTAssertNil(s.basisLabel)
    }

    // MARK: - health.tables and stream-today.unkeyed_messages

    /// `latestData` is the WORST of the three perf tables — the right number to
    /// gate writes on, and useless for working out what broke. The three are
    /// filled by three independent Amazon report jobs, which is the whole
    /// reason for the standing rule against dating one from another: that
    /// mistake recurred three times and once froze US bids, pauses and harvest
    /// for four nights while `campaign_perf` stayed green throughout.
    ///
    /// `staleTables` only names a table once it is past the 4-day freeze. A
    /// table two days behind was invisible until then, and two days behind is
    /// exactly when it is worth seeing.
    func testALaggingPerfTableIsNamed() throws {
        let h = try decode(MarketHealth.self, """
        {"market":"US","configured":true,"has_data":true,
         "latest_data":"2026-08-19","stale_tables":[],
         "tables":{"campaign_perf":"2026-08-22","targeting_perf":"2026-08-22",
                   "search_term_perf":"2026-08-19"}}
        """)
        let lagging = h.laggingTables
        XCTAssertEqual(lagging.count, 1)
        XCTAssertEqual(lagging.first?.name, "search_term_perf")
        XCTAssertEqual(lagging.first?.daysBehind, 3)
        XCTAssertTrue(h.staleTables?.isEmpty ?? true,
                      "three days is under the freeze threshold, so the ALARM "
                      + "is correctly silent — this is the gap it cannot show")
    }

    func testTablesInStepReportNoLag() throws {
        let h = try decode(MarketHealth.self, """
        {"market":"US","configured":true,"has_data":true,
         "latest_data":"2026-08-22",
         "tables":{"campaign_perf":"2026-08-22","targeting_perf":"2026-08-22",
                   "search_term_perf":"2026-08-22"}}
        """)
        XCTAssertTrue(h.laggingTables.isEmpty)
    }

    /// A perf table that EXISTS and holds nothing reports a null date, not a
    /// missing key. `tables` was typed `[String: String]`, so this threw
    /// `valueNotFound` — and because `health` answers for every market in one
    /// reply, one such market blanked System Health for all seven. It is the
    /// state of a market on the day it is added, and of any market whose report
    /// job has never once succeeded. Found by review, 2026-08-23.
    func testAMarketWithAnEmptyPerfTableStillDecodes() throws {
        let h = try decode(MarketHealth.self, """
        {"market":"NEW","configured":true,"has_data":false,"latest_data":null,
         "tables":{"campaign_perf":null,"targeting_perf":null,
                   "search_term_perf":null}}
        """)
        XCTAssertEqual(h.undatedTables,
                       ["campaign_perf", "search_term_perf", "targeting_perf"],
                       "never filled is a different sentence from stale")
        XCTAssertTrue(h.datedTables.isEmpty)
        XCTAssertTrue(h.laggingTables.isEmpty,
                      "nothing can lag when nothing has a date")
    }

    /// The mixed case: one job has landed, the others never have. The dated
    /// side must still work and must not treat the undated ones as behind.
    func testAPartlyFilledMarketSeparatesUndatedFromLagging() throws {
        let h = try decode(MarketHealth.self, """
        {"market":"NEW","configured":true,"has_data":true,
         "latest_data":null,
         "tables":{"campaign_perf":"2026-08-22","targeting_perf":null,
                   "search_term_perf":null}}
        """)
        XCTAssertEqual(h.undatedTables, ["search_term_perf", "targeting_perf"])
        XCTAssertEqual(h.datedTables, ["campaign_perf": "2026-08-22"])
        XCTAssertTrue(h.laggingTables.isEmpty,
                      "one dated table cannot lag behind itself")
    }

    func testAnOlderEngineWithoutTablesStillDecodes() throws {
        let h = try decode(MarketHealth.self, """
        {"market":"US","configured":true,"has_data":true,"latest_data":"2026-08-22"}
        """)
        XCTAssertNil(h.tables)
        XCTAssertTrue(h.laggingTables.isEmpty)
    }

    /// The one caveat on the live panel that points the other way: every other
    /// Stream warning says the day may read LOW, this one says it may read
    /// HIGH. sp-traffic rows are deltas, so a row with no id is kept rather
    /// than collapsed — collapsing on shape would discard most of an hour of
    /// real traffic — and the price is that a redelivery counts twice.
    ///
    /// It has been 0 every day since the subscription opened. It is rendered
    /// anyway, because the day it is not is the day this panel is wrong, and
    /// `stream-verify` only judges days that have already settled.
    func testUnkeyedMessagesRaiseAWarning() throws {
        let r = try decode(StreamTodayResponse.self, """
        {"market":"US","supported":true,"unkeyed_messages":7}
        """)
        let warning = try XCTUnwrap(r.unkeyedWarning)
        XCTAssertTrue(warning.contains("7"), warning)
        XCTAssertTrue(warning.lowercased().contains("high"),
                      "the direction is the point — every other caveat here "
                      + "says the day reads low")
    }

    func testACleanDaySaysNothing() throws {
        let r = try decode(StreamTodayResponse.self, """
        {"market":"US","supported":true,"unkeyed_messages":0}
        """)
        XCTAssertNil(r.unkeyedWarning)
    }

    func testAnOlderEngineWithoutTheFieldSaysNothing() throws {
        let r = try decode(StreamTodayResponse.self, """
        {"market":"US","supported":true}
        """)
        XCTAssertNil(r.unkeyedMessages)
        XCTAssertNil(r.unkeyedWarning)
    }

    // MARK: - the nightly's per-step timing

    /// The run takes hours and recorded only its start and its finish. Nothing
    /// said which phase owned the time, so a phase that doubled read as a
    /// busier night and no optimisation could be checked afterwards.
    func testTheSlowestStepIsTheFirstOne() throws {
        let run = try decode(LastRunStatus.self, """
        {"started":"2026-08-23T10:00:03","finished":"2026-08-23T12:43:16",
         "ok":true,"failures":[],"markets":["US","UK"],
         "steps":[{"market":"US","step":"phase0_pull","seconds":2451},
                  {"market":"UK","step":"phase0_pull","seconds":1180},
                  {"market":"US","step":"map_products","seconds":310}],
         "total_step_seconds":3941}
        """)
        XCTAssertEqual(run.slowestStep?.step, "phase0_pull")
        XCTAssertEqual(run.slowestStep?.market, "US")
        XCTAssertEqual(run.totalStepSeconds, 3941)
    }

    func testWallTimeIsMeasuredFromBothEnds() throws {
        let run = try decode(LastRunStatus.self, """
        {"started":"2026-08-23T10:00:03","finished":"2026-08-23T12:43:16",
         "ok":true,"failures":[]}
        """)
        XCTAssertEqual(run.wallSeconds, 9_793, "2h43m13s")
    }

    /// The sum of the steps is LESS than the wall time, and that gap is real —
    /// it is what the script does between phases. Presenting either as the
    /// other would be a quiet lie about where the night went.
    func testTheStepsDoNotHaveToAccountForTheWholeRun() throws {
        let run = try decode(LastRunStatus.self, """
        {"started":"2026-08-23T10:00:03","finished":"2026-08-23T12:43:16",
         "ok":true,"failures":[],
         "steps":[{"market":"US","step":"phase0_pull","seconds":2451}],
         "total_step_seconds":2451}
        """)
        let wall = try XCTUnwrap(run.wallSeconds)
        let stepped = try XCTUnwrap(run.totalStepSeconds)
        XCTAssertLessThan(stepped, wall)
    }

    func testDurationsReadAsEnglish() {
        XCTAssertEqual(RunStepTiming(market: "US", step: "x", seconds: 18).readable, "18s")
        XCTAssertEqual(RunStepTiming(market: "US", step: "x", seconds: 60).readable, "1m")
        XCTAssertEqual(RunStepTiming(market: "US", step: "x", seconds: 2_460).readable, "41m")
        XCTAssertEqual(RunStepTiming(market: "US", step: "x", seconds: 9_793).readable, "2h 43m")
        XCTAssertEqual(RunStepTiming(market: "US", step: "x", seconds: 7_200).readable, "2h")
    }

    /// Every run before this change has no `steps`, and the banner must not
    /// break on the first launch after upgrading.
    func testARunFromBeforeTheChangeStillDecodes() throws {
        let run = try decode(LastRunStatus.self, """
        {"started":"2026-08-23T10:00:03","finished":"2026-08-23T12:43:16",
         "ok":true,"failures":[],"markets":["US"]}
        """)
        XCTAssertNil(run.steps)
        XCTAssertNil(run.slowestStep)
        XCTAssertNil(run.totalStepSeconds)
        XCTAssertNotNil(run.wallSeconds)
    }

    // MARK: - "could not confirm" is not "refused"

    /// `_applied_subset` answering an empty list meant two different things:
    /// Amazon refused everything, and nobody knows what Amazon did. The
    /// callers subtracted that empty list from what they asked for and the app
    /// printed "Amazon refused all 40" — a claim about Amazon that nothing in
    /// the reply supports, and one that invites running the whole thing again
    /// when some of it may already have landed.
    /// Found by the second review pass, 2026-08-23.
    func testAnUnconfirmedResetSaysSoRatherThanBlamingAmazon() throws {
        let r = try decode(ResetBidsResponse.self, """
        {"market":"US","count":3,"total_reduction":0.85,"preview":false,
         "applied":false,"applied_count":0,"rejected_count":null,
         "outcome_confirmed":false}
        """)
        let note = try XCTUnwrap(r.partialFailureNote)
        XCTAssertTrue(note.contains("could not be matched"),
                      "it must not say Amazon refused them: \(note)")
        XCTAssertFalse(note.contains("refused 3"))
    }

    func testAConfirmedRejectionStillNamesTheCount() throws {
        let r = try decode(ResetBidsResponse.self, """
        {"market":"US","count":3,"total_reduction":0.85,"preview":false,
         "applied":false,"applied_count":2,"rejected_count":1,
         "applied_reduction":0.35,"outcome_confirmed":true}
        """)
        XCTAssertEqual(r.shownCount, 2)
        XCTAssertEqual(r.shownReduction, 0.35)
        XCTAssertEqual(try XCTUnwrap(r.partialFailureNote).contains("refused 1"), true)
    }

    func testAnUnconfirmedPauseBatchSaysSoToo() throws {
        let r = try decode(NegativesApplyResponse.self, """
        {"market":"US","negatives_applied":4,"pauses_applied":0,
         "negatives_rejected":0,"pauses_rejected":null,"pauses_confirmed":false}
        """)
        let note = try XCTUnwrap(r.partialFailureNote)
        XCTAssertTrue(note.contains("could not be matched"), note)
    }

    func testACleanApplyStillSaysNothing() throws {
        let r = try decode(NegativesApplyResponse.self, """
        {"market":"US","negatives_applied":4,"pauses_applied":2,
         "negatives_rejected":0,"pauses_rejected":0,"pauses_confirmed":true}
        """)
        XCTAssertNil(r.partialFailureNote)
    }
}
