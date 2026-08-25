import XCTest
@testable import Merch_Ads

/// What a screen PRINTS has to be what the data says.
///
/// The 2026-08-24 walk through the running app found fourteen places where it
/// was not, and none of them were engine bugs: the reply was right and the view
/// summarised it into something else. A count capped by the page it happened to
/// fetch. A green "Issues 0" card sitting on top of a red banner. A red pill
/// counting designs that were already paused. A caption naming a source that
/// priced one of the fourteen rows on screen.
///
/// Every one of those is a pure function of the decoded reply, so every one of
/// them can be pinned here without a bridge, a database or Amazon.
final class ScreenTruthfulnessTests: XCTestCase {

    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        // The app's own decoder, never a locally built one — a fixture read
        // with a private JSONDecoder keeps passing after the bridge stops
        // converting snake_case.
        try PythonBridge.makeDecoder().decode(T.self, from: Data(json.utf8))
    }

    // MARK: - Audit Trail: counts must not be capped by the page

    /// The week card read "500 writes this week" — exactly the fetch limit —
    /// on a week whose real count was many times that, most of it banked in a
    /// single day by a rule that had gone wide. Catching a runaway rule is what
    /// the screen is for, and a card that can only ever print the page size
    /// cannot do it.
    ///
    /// The counts here are invented and small, like every other fixture in this
    /// file. What they pin is the SHAPE: whole-log totals that are larger than
    /// the page sitting beside them. A card that went back to counting the page
    /// would read 500 and cannot pass that by accident.
    func testAuditTotalsComeFromTheEngineNotThePage() throws {
        let r = try decode(AuditResponse.self, """
        {"market":"US","count":500,"writes":[],"has_more":true,
         "totals":{"today":58,"week":842,"no_ops_today":0,
                   "undoable":137,"window_days":7}}
        """)
        let totals = try XCTUnwrap(r.totals, "the week card cannot be honest without these")
        XCTAssertEqual(totals.week, 842)
        XCTAssertEqual(totals.today, 58)
        XCTAssertEqual(totals.undoable, 137)
        XCTAssertEqual(totals.windowDays, 7)
        XCTAssertGreaterThan(totals.week, r.count,
                             "the fixture only pins the rule while the whole log is bigger than the page")
    }

    func testAnOlderEngineWithoutTotalsStillDecodes() throws {
        let r = try decode(AuditResponse.self, """
        {"market":"US","count":12,"writes":[],"has_more":false}
        """)
        XCTAssertNil(r.totals, "nil is what makes the view label the cards as page-derived")
    }

    // MARK: - Import: this market's history, not the union of three series

    /// The Ads tab printed the account-wide month count as the selected
    /// market's own: "No gaps — 60 months banked, continuous" on DE, whose
    /// `ads_history_monthly` held nothing at all. One console export covers
    /// every marketplace and carries no country, so DE, FR, ES and IT share
    /// one euro series — and it is 41 months, not 60.
    ///
    /// The amounts are invented and deliberately small. What this fixture pins
    /// is the SHAPE of the reply: three series of different lengths, one of
    /// them shared. A real account total would prove nothing extra here, and a
    /// test file is not a place to keep one.
    private let liveCoverage = """
    {"months":60,"first_month":"2021-09","last_month":"2026-08",
     "by_market":[
       {"market":"EU","currency":"EUR","months":41,"spend":120.50,"sales":640.25,
        "purchases":42,"first_month":"2023-04","last_month":"2026-08"},
       {"market":"UK","currency":"GBP","months":44,"spend":85.75,"sales":410.00,
        "purchases":27,"first_month":"2023-01","last_month":"2026-08"},
       {"market":"US","currency":"USD","months":60,"spend":310.40,"sales":950.80,
        "purchases":61,"first_month":"2021-09","last_month":"2026-08"}]}
    """

    func testAEuroMarketReadsTheEuroSeries() throws {
        let coverage = try decode(HistoryCoverage.self, liveCoverage)
        let bucket = try XCTUnwrap(coverage.bucket(market: "DE", currency: "EUR"))
        XCTAssertEqual(bucket.market, "EU")
        XCTAssertEqual(bucket.months, 41)
        XCTAssertEqual(bucket.spend, 120.50, accuracy: 0.001,
                       "the caption prints this bucket's own totals, not the union's")
        XCTAssertEqual(bucket.purchases, 42)
        XCTAssertNotEqual(bucket.months, coverage.months,
                          "the union is what the screen used to print")
        XCTAssertTrue(coverage.bucketIsShared(market: "DE", currency: "EUR"),
                      "DE, FR, ES and IT are one series and the caption must say so")
    }

    func testTheDefaultMarketMatchesItselfAndIsNotShared() throws {
        let coverage = try decode(HistoryCoverage.self, liveCoverage)
        let bucket = try XCTUnwrap(coverage.bucket(market: "US", currency: "USD"))
        XCTAssertEqual(bucket.months, 60)
        XCTAssertFalse(coverage.bucketIsShared(market: "US", currency: "USD"))
    }

    func testAMarketWithNoBankedSeriesGetsNothingRatherThanTheUnion() throws {
        let coverage = try decode(HistoryCoverage.self, liveCoverage)
        XCTAssertNil(coverage.bucket(market: "JP", currency: "JPY"),
                     "a market with no banked history must not inherit another's")
    }

    func testGapsAreCountedInsideTheBucketsOwnRange() throws {
        let coverage = try decode(HistoryCoverage.self, """
        {"months":5,"first_month":"2026-01","last_month":"2026-05",
         "by_market":[{"market":"EU","currency":"EUR","months":2,"spend":1,"sales":2,
                       "purchases":0,"first_month":"2026-01","last_month":"2026-05"}]}
        """)
        let bucket = try XCTUnwrap(coverage.bucket(market: "FR", currency: "EUR"))
        XCTAssertEqual(bucket.monthGaps, 3,
                       "January to May is five months and only two were banked")
    }

    func testAnOlderEngineWithoutBucketRangesSaysItCannotJudgeGaps() throws {
        let coverage = try decode(HistoryCoverage.self, """
        {"months":41,"first_month":"2023-04","last_month":"2026-08",
         "by_market":[{"market":"EU","currency":"EUR","months":41,"spend":1,"sales":2,
                       "purchases":0}]}
        """)
        let bucket = try XCTUnwrap(coverage.bucket(market: "IT", currency: "EUR"))
        XCTAssertNil(bucket.monthGaps, "nil is 'cannot judge', which must not read as 'no gaps'")
    }

    // MARK: - System Health: the summary cannot disagree with the banners

    private func market(_ code: String) -> MarketHealth {
        MarketHealth(market: code, configured: true, hasData: true,
                     latestData: nil, lastPull: nil, lastWrite: nil, campaigns: 1,
                     campaignsEnabled: 1, lastNote: nil, reportsPending: 0,
                     staleTables: [], tables: nil, targetDaily: nil,
                     dailyTotals: nil, bidCeiling: nil, error: nil)
    }

    private func health(runOK: Bool, backlog: [String]) -> HealthResponse {
        let stream = StreamHealth(configured: true, queuesConfigured: 2, database: true,
                                  datasets: nil, lastDrain: nil, drainAgeMinutes: 10,
                                  drainStale: false, drainByRealm: nil, drainStaleRealms: nil,
                                  drainBacklog: backlog, corrupt: false, corruptDetail: nil,
                                  error: nil)
        let run = LastRunStatus(started: nil, finished: nil, ok: runOK,
                                failures: runOK ? [] : [RunStepFailure(market: "DE",
                                                                       step: "phase0_pull",
                                                                       exit: 1)],
                                markets: nil)
        return HealthResponse(killActive: false, approvalRequired: false,
                              lastRun: run, stream: stream, markets: [market("US")])
    }

    /// The card read ISSUES 0 in green directly above a red "6 steps failed"
    /// banner and an amber Stream backlog banner. The eye stops at the card.
    func testAFailedNightlyIsAnIssue() {
        let payload = health(runOK: false, backlog: [])
        XCTAssertEqual(HealthView.issueCount(markets: payload.markets, health: payload), 1)
    }

    func testADrainBacklogIsAnIssue() {
        let payload = health(runOK: true, backlog: ["NA/sp-traffic"])
        XCTAssertEqual(HealthView.issueCount(markets: payload.markets, health: payload), 1)
    }

    func testACleanNightAndAnEmptyQueueStayAtZero() {
        let payload = health(runOK: true, backlog: [])
        XCTAssertEqual(HealthView.issueCount(markets: payload.markets, health: payload), 0,
                       "a card that counts a healthy account is noise")
    }

    // MARK: - Dashboard: the red pill must name something that can still act

    /// Both of DE's kill candidates were already PAUSED, and the pill drew
    /// them in the screen's only red. A warning about something the operator
    /// cannot change gets ignored, and then the real one is ignored too.
    func testPausedKillCandidatesAreNotRed() throws {
        let list = try decode(KillListResponse.self, """
        {"market":"DE","cvr_floor":0.08,"count":2,"designs":[
          {"asin":"B0TESTAAAA","ad_group_id":"1","state":"PAUSED","clicks":15,"orders":1,
           "spend":3.14,"sales":1.0},
          {"asin":"B0TESTBBBB","ad_group_id":"2","state":"PAUSED","clicks":26,"orders":1,
           "spend":2.88,"sales":1.0}]}
        """)
        let pill = try XCTUnwrap(DashboardView.killPill(list))
        XCTAssertFalse(pill.critical)
        XCTAssertTrue(pill.text.contains("already paused"), pill.text)
    }

    func testAServingKillCandidateIsStillRed() throws {
        let list = try decode(KillListResponse.self, """
        {"market":"DE","cvr_floor":0.08,"count":2,"designs":[
          {"asin":"B0TESTAAAA","ad_group_id":"1","state":"ENABLED","clicks":15,"orders":1,
           "spend":3.14,"sales":1.0},
          {"asin":"B0TESTBBBB","ad_group_id":"2","state":"PAUSED","clicks":26,"orders":1,
           "spend":2.88,"sales":1.0}]}
        """)
        let pill = try XCTUnwrap(DashboardView.killPill(list))
        XCTAssertTrue(pill.critical)
        XCTAssertTrue(pill.text.hasPrefix("1 kill candidate"), pill.text)
    }

    /// An older engine sends no `state`. Treating that as paused would switch
    /// the alarm off for everyone who has not upgraded.
    func testADesignWithNoStateIsAssumedToBeServing() throws {
        let list = try decode(KillListResponse.self, """
        {"market":"US","cvr_floor":0.08,"count":1,"designs":[
          {"asin":"B01","ad_group_id":"1","clicks":15,"orders":1,"spend":3.14,"sales":1.0}]}
        """)
        XCTAssertEqual(DashboardView.killPill(list)?.critical, true)
    }

    // MARK: - Dashboard: two tables, two dates

    /// DE's header said "data through 22.08.2026" over three cards covering
    /// 01.–23.08.2026. Both were right: the subtitle dates the perf snapshots
    /// and the cards read the banked daily history. The page just never said
    /// they were two different things.
    func testTheSubtitleNamesBothDatesWhenTheyDiffer() {
        let text = DashboardView.freshnessText(snapshotAsOf: "2026-08-22",
                                               dailyAsOf: "2026-08-23")
        XCTAssertTrue(text.contains("snapshots through"), text)
        XCTAssertTrue(text.contains("daily history through"), text)
    }

    func testOneDateIsStillPrintedOnce() {
        let text = DashboardView.freshnessText(snapshotAsOf: "2026-08-23",
                                               dailyAsOf: "2026-08-23")
        XCTAssertFalse(text.contains("daily history"), text)
        XCTAssertTrue(text.hasPrefix("data through"), text)
    }

    // MARK: - All Markets: a two-line cell is not 34pt tall

    /// UK, the sixth row, rendered as the top third of a row — sliced through
    /// the glyphs, with the table's own scrolling switched off so it could not
    /// be reached. The YTD columns print a "Partial · since Jun 2026" caption
    /// under the figure, and the fixed height assumed one line per row.
    func testTheTableIsTallEnoughForItsTwoLineRows() {
        let tall = AllMarketsView.tableHeight(rows: 6, captionRows: 5, rowHeight: 34,
                                              captionRowHeight: 50, headerHeight: 40)
        let flat = AllMarketsView.tableHeight(rows: 6, captionRows: 0, rowHeight: 34,
                                              captionRowHeight: 50, headerHeight: 40)
        XCTAssertEqual(flat, 6 * 34 + 40)
        XCTAssertEqual(tall, 34 + 5 * 50 + 40)
        XCTAssertGreaterThan(tall, flat, "the caption rows are what ate the last row")
    }

    func testMoreCaptionRowsThanRowsCannotInflateTheTable() {
        // Defensive: rows and captions come from two different filters.
        let height = AllMarketsView.tableHeight(rows: 2, captionRows: 9, rowHeight: 34,
                                                captionRowHeight: 50, headerHeight: 40)
        XCTAssertEqual(height, 2 * 50 + 40)
    }

    // MARK: - Rules: a rule you switched off is still a rule you can edit

    /// The Library's caption says to edit these in My Rules; My Rules listed
    /// only the enabled ones. The single path the UI offered was to turn the
    /// rule on — which arms an unreviewed bid-writing rule for that night.
    func testMyRulesListsDisabledRulesLast() {
        let rules = [RuleSummary(name: "Zebra", enabled: true, mode: "auto",
                                 season: nil, updated: nil),
                     RuleSummary(name: "Nudge starved apparel", enabled: false,
                                 mode: "review", season: nil, updated: nil),
                     RuleSummary(name: "Apple", enabled: true, mode: "auto",
                                 season: nil, updated: nil)]
        let listed = RulesView.editableList(rules)
        XCTAssertEqual(listed.map(\.name), ["Apple", "Zebra", "Nudge starved apparel"])
        XCTAssertTrue(listed.contains { !$0.enabled },
                      "a disabled rule could be opened nowhere in the app")
    }

    // MARK: - Cross-purchase: a pair that sold nothing measured nothing

    /// 28 of the 51 US pairs had zero sales, zero units and zero purchases,
    /// and all 51 were headlined as "clicked this, bought that". The screen's
    /// own "Designs earning halo" card counted the honest way, so a card and a
    /// section header on one screen disagreed by roughly a factor of two.
    func testOnlyPairsThatSoldSomethingAreCounted() throws {
        let response = try decode(CrossPurchaseResponse.self, """
        {"market":"US","supported":true,"pairs":[
          {"advertised_asin":"A","purchased_asin":"B","sales":12.5,"units":1,"purchases":1},
          {"advertised_asin":"A","purchased_asin":"C","sales":0,"units":0,"purchases":0},
          {"advertised_asin":"D","purchased_asin":"E","sales":0,"units":0,"purchases":0}]}
        """)
        XCTAssertEqual(CrossPurchaseView.convertedPairs(response.pairs ?? []), 1)
    }

    // MARK: - Profit: the row's unit is the ad group

    /// B0TESTCCCC appeared at +58,76 US$ "bid up" and again at −1,58 US$ "bid
    /// down", identified only by ASIN. Acting on the loss-making row means
    /// bidding down one of the account's best earners.
    func testRepeatedAsinsAreCounted() throws {
        let response = try decode(ProfitResponse.self, """
        {"market":"US","designs":[
          {"ad_group_id":"1","asin":"B0TESTCCCC","orders":17,"clicks":5,"spend":39.17,
           "sales":100.0,"royalty_est":97.93,"profit":58.76},
          {"ad_group_id":"2","asin":"B0TESTCCCC","orders":3,"clicks":2,"spend":2.63,
           "sales":20.0,"royalty_est":17.28,"profit":14.65},
          {"ad_group_id":"3","asin":"B0XXXXXXXX","orders":0,"clicks":9,"spend":4.13,
           "sales":0.0,"royalty_est":0.0,"profit":-4.13}]}
        """)
        XCTAssertEqual(ProfitView.repeatedAsins(response.designs ?? []), 1)
    }

    func testOneRowPerDesignRaisesNothing() throws {
        let response = try decode(ProfitResponse.self, """
        {"market":"US","designs":[
          {"ad_group_id":"1","asin":"B01","orders":1,"clicks":1,"spend":1.0,
           "sales":2.0,"royalty_est":2.0,"profit":1.0}]}
        """)
        XCTAssertEqual(ProfitView.repeatedAsins(response.designs ?? []), 0)
    }

    // MARK: - Stream: a queued hour is not a delivered hour

    /// The panel said the day was complete while 958 messages sat undrained in
    /// SQS, growing hourly. Those messages belong to hours that already read
    /// as delivered, so no hour count can ever notice them.
    func testABacklogIsNotAnHoursProblem() throws {
        let coverage = try decode(StreamCoverage.self, """
        {"delivered_hours":8,"expected_hours":8,"missing_hours":[],"partial_hours":[],
         "backlog_pending":["NA/sp-traffic"],"complete":false,
         "note":"The hourly drain did not empty NA/sp-traffic."}
        """)
        XCTAssertEqual(coverage.backlogPending, ["NA/sp-traffic"])
        XCTAssertFalse(coverage.hoursAreIncomplete,
                       "the hours are whole — the drain is what is behind")
    }

    func testAHoleInTheDayIsAnHoursProblem() throws {
        let coverage = try decode(StreamCoverage.self, """
        {"delivered_hours":7,"expected_hours":8,"missing_hours":[3],"partial_hours":[],
         "complete":false,"note":"1 of the 8 hours was never delivered."}
        """)
        XCTAssertTrue(coverage.hoursAreIncomplete)
    }

    func testADayWithNothingDeliveredStillSaysSo() throws {
        let coverage = try decode(StreamCoverage.self, """
        {"delivered_hours":0,"expected_hours":0,"missing_hours":[],"partial_hours":[],
         "complete":false,"note":"No hours delivered for this day yet."}
        """)
        XCTAssertTrue(coverage.hoursAreIncomplete,
                      "otherwise the panel draws no note at all for an empty day")
    }

    // MARK: - Intake: a route that cannot show what it counted

    /// `count` was the true total and `designs` was the first 2000, with
    /// nothing between them saying so. The screen ticks and builds the designs
    /// it was sent, so a cohort of 5,000 would have built 2,000 in silence.
    func testATruncatedRouteSaysHowManyItCannotBuild() throws {
        let route = try decode(IntakeRoute.self, """
        {"route":"Scavenger Tees","count":5000,"returned":2000,"truncated":true,
         "designs":[{"asin":"B01","ad_asins":["B01"],"type":"standard_tshirt",
                     "series":"Tees","title":"x","lifetime_sales":0,"created":"2026-08-01",
                     "lottery":true}]}
        """)
        XCTAssertTrue(route.isTruncated)
        XCTAssertEqual(route.missingFromPlan, 4999)
    }

    func testAWholeRouteStaysQuiet() throws {
        let route = try decode(IntakeRoute.self, """
        {"route":"Scavenger Tees","count":1,"returned":1,"truncated":false,
         "designs":[{"asin":"B01","ad_asins":["B01"],"type":"standard_tshirt",
                     "series":"Tees","title":"x","lifetime_sales":0,"created":"2026-08-01",
                     "lottery":true}]}
        """)
        XCTAssertFalse(route.isTruncated)
        XCTAssertEqual(route.missingFromPlan, 0)
    }

    /// An older engine sends neither flag. The list length against `count` is
    /// then the only evidence, and it must still be believed.
    func testAnOlderEngineIsJudgedByWhatItActuallySent() throws {
        let route = try decode(IntakeRoute.self, """
        {"route":"Scavenger Tees","count":3,
         "designs":[{"asin":"B01","ad_asins":["B01"],"type":"standard_tshirt",
                     "series":"Tees","title":"x","lifetime_sales":0,"created":"2026-08-01",
                     "lottery":true}]}
        """)
        XCTAssertTrue(route.isTruncated)
        XCTAssertEqual(route.missingFromPlan, 2)
    }
}
