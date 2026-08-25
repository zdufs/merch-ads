import XCTest
@testable import Merch_Ads

/// The engine reports what it could NOT do. The app has to say so.
///
/// Several fields exist for one reason: to stop a screen claiming more than the
/// data behind it supports. `skipped` says which designs never reached a
/// verdict. `partial` says the "year" is two months. `unresolved_advertisers`
/// says a chunk of the day is missing from every total on the panel. Each was
/// decoded into nothing and dropped on the floor, and every one of those
/// screens went on reading as complete and correct.
///
/// Found by the 2026-08-22 review, on live data: the Kill List said "Nothing to
/// kill — No design in US is below the CVR floor while over break-even" while
/// 49 designs had been excluded before any threshold ran.
final class EngineTruthFieldsTests: XCTestCase {

    /// The app's decoder, not one of our own. A fixture read with a
    /// locally-built JSONDecoder keeps passing after the bridge stops
    /// converting snake_case, which is how all 259 Swift tests stayed
    /// green through that mutation on 2026-08-24.
    private func decoder() -> JSONDecoder { PythonBridge.makeDecoder() }

    // MARK: - import: the request is not the result

    /// The 2026-08-22 US import, verbatim in shape: 2,324 designs approved,
    /// "Drinkware 723" on the screen, zero drinkware ads created. The cohort
    /// counts describe what the builders were ASKED for, so they cannot ever
    /// show this. The builder's coverage report can, and the screen must.
    func testAnImportThatBuiltFourOfFiveCohortsDoesNotReadAsComplete() throws {
        let json = """
        {"market":"US","designs":2324,
         "cohorts":[{"series":"Hoodies","count":728},{"series":"Drinkware","count":723}],
         "coverage":{"available":true,"scoped":4453,"planned":1601,"unplanned":2852,
                     "paused_campaigns":[],"series":[]}}
        """.data(using: .utf8)!
        let got = try decoder().decode(ImportApplyResponse.self, from: json)
        let warning = try XCTUnwrap(got.coverage?.warning,
                                    "2,852 unbuilt ASINs must reach the screen")
        // Format.count is locale-grouped ("2,852" or "2.852"), so ask it, don't guess.
        XCTAssertTrue(warning.contains(Format.count(2852)), warning)
        XCTAssertTrue(warning.contains("NOT built"), warning)
    }

    /// 446 hat ads were created inside a campaign paused since June. Amazon
    /// accepted every one of them, so every count in the reply looked healthy.
    func testAdsAddedToAPausedCampaignAreCalledOut() throws {
        let json = """
        {"market":"US","designs":446,
         "coverage":{"available":true,"scoped":446,"planned":446,"unplanned":0,
                     "paused_campaigns":["SCAVENGER - Hats 1"],"series":[]}}
        """.data(using: .utf8)!
        let got = try decoder().decode(ImportApplyResponse.self, from: json)
        let warning = try XCTUnwrap(got.coverage?.warning)
        XCTAssertTrue(warning.contains("SCAVENGER - Hats 1"), warning)
        XCTAssertTrue(warning.contains("cannot serve"), warning)
    }

    /// Amazon refused about 873 product ads a night, in every market, from
    /// 2026-06-25 to 2026-08-25. `added: 0` was the only figure the report
    /// carried, and that is also what a market with nothing new to add writes,
    /// so the screen read Complete for sixty nights. The 2026-08-23 US run is
    /// the shape here: 3,769 submitted, 3,031 created, 738 turned down.
    func testProductAdsAmazonRefusedReachTheScreen() throws {
        let json = """
        {"market":"US","designs":3769,
         "coverage":{"available":true,"scoped":3769,"planned":3769,"unplanned":0,
                     "refused":738,"paused_campaigns":[],
                     "series":[{"series":"Hats","matched":479,"planned":479,
                                "added":0,"refused":194,"over_cap":0,
                                "paused_campaigns":[]}]}}
        """.data(using: .utf8)!
        let got = try decoder().decode(ImportApplyResponse.self, from: json)
        let warning = try XCTUnwrap(got.coverage?.warning,
                                    "738 refused ads must not read as a clean build")
        XCTAssertTrue(warning.contains(Format.count(738)), warning)
        XCTAssertTrue(warning.contains("REFUSED"), warning)
        XCTAssertEqual(got.coverage?.series?.first?.refused, 194)
    }

    /// A hardgood with no ad-safe ASIN cannot be advertised, and the fix for
    /// that must not hide it.
    ///
    /// The builder used to submit these listings' retail ASINs, which Amazon
    /// refuses with AD_INELIGIBLE every time, so they arrived on this screen as
    /// `refused` and read like a transient Amazon problem. They are permanent:
    /// several hundred US designs are advertised nowhere, and only a fresh
    /// export with the ad-safe column populated recovers them. The count is
    /// measured per account and is not repeated here, because a comment is not
    /// the place to keep one. Skipping them stops the pointless
    /// nightly re-submission; saying so is what stops the skip from being a
    /// quieter version of the same silence.
    func testHardgoodsWithNoAdSafeAsinAreReportedAsLostCoverage() throws {
        let json = """
        {"market":"US","designs":14435,
         "coverage":{"available":true,"scoped":null,"planned":14435,"unplanned":0,
                     "refused":0,"no_ad_safe":474,
                     "no_ad_safe_series":{"Hats":194,"Drinkware":280},
                     "paused_campaigns":[],
                     "series":[{"series":"Hats","matched":285,"planned":285,
                                "added":0,"refused":0,"no_ad_safe":194,
                                "over_cap":0,"paused_campaigns":[]}]}}
        """.data(using: .utf8)!
        let got = try decoder().decode(ImportApplyResponse.self, from: json)
        let warning = try XCTUnwrap(got.coverage?.warning,
                                    "474 designs advertised nowhere must not read as a clean build")
        XCTAssertTrue(warning.contains(Format.count(474)), warning)
        XCTAssertTrue(warning.contains("ad-safe"), warning)
        XCTAssertTrue(warning.contains("Drinkware"), warning)
        XCTAssertTrue(warning.contains("Hats"), warning)
        XCTAssertEqual(got.coverage?.noAdSafe, 474)
        XCTAssertEqual(got.coverage?.series?.first?.noAdSafe, 194)
    }

    /// A build that covered everything must stay quiet, or the warning is noise
    /// and gets ignored on the day it matters.
    func testAFullyCoveredBuildRaisesNothing() throws {
        let json = """
        {"market":"UK","designs":1619,
         "coverage":{"available":true,"scoped":2214,"planned":2214,"unplanned":0,
                     "refused":0,"paused_campaigns":[],"series":[]}}
        """.data(using: .utf8)!
        let got = try decoder().decode(ImportApplyResponse.self, from: json)
        XCTAssertNil(got.coverage?.warning)
    }

    /// No report at all is UNVERIFIED. It must never decode into the same
    /// silence as a clean run.
    func testAMissingCoverageReportIsItselfAWarning() throws {
        let json = """
        {"market":"US","designs":10,
         "coverage":{"available":false,"note":"the builder wrote no coverage report; treat this run as unverified","scoped":10}}
        """.data(using: .utf8)!
        let got = try decoder().decode(ImportApplyResponse.self, from: json)
        XCTAssertEqual(got.coverage?.warning,
                       "the builder wrote no coverage report; treat this run as unverified")
    }

    /// An older engine sends no `coverage` key at all. That is silence, not a gap.
    func testAnOlderEngineWithNoCoverageKeyStillDecodes() throws {
        let json = """
        {"market":"US","designs":10,"cohorts":[{"series":"Hoodies","count":10}]}
        """.data(using: .utf8)!
        let got = try decoder().decode(ImportApplyResponse.self, from: json)
        XCTAssertNil(got.coverage)
    }

    // MARK: - kill list: designs that never reached a verdict

    func testSkippedDecodesFromTheEnginesShape() throws {
        // exactly what `appctl killlist` returned for US on 2026-08-22
        let json = """
        {"market":"US","cvr_floor":0.08,"count":0,"designs":[],
         "skipped":{"transition":40,"unknown_price":8,"cohort":1,"cross_sell":0}}
        """.data(using: .utf8)!
        let r = try decoder().decode(KillListResponse.self, from: json)
        XCTAssertEqual(r.skipped?.transition, 40)
        XCTAssertEqual(r.skipped?.unknownPrice, 8)
        XCTAssertEqual(r.skipped?.cohort, 1)
    }

    /// A cross-sell spare is a DECISION, not a gap: the design was judged, and
    /// held back because its ads sell other designs. It has its own banner, so
    /// counting it as "could not be judged" would report it twice and overstate
    /// how blind the screen is.
    func testUnjudgedExcludesCrossSellSpares() {
        let s = KillSkipped(transition: 40, unknownPrice: 8, cohort: 1, crossSell: 12)
        XCTAssertEqual(s.unjudged, 49)
    }

    func testUnjudgedIsZeroWhenEveryDesignWasJudged() {
        XCTAssertEqual(KillSkipped(transition: 0, unknownPrice: 0,
                                   cohort: 0, crossSell: 0).unjudged, 0)
        XCTAssertEqual(KillSkipped(transition: nil, unknownPrice: nil,
                                   cohort: nil, crossSell: nil).unjudged, 0)
    }

    func testReasonsAreBiggestFirstAndOmitZeroes() {
        let s = KillSkipped(transition: 3, unknownPrice: 40, cohort: 0, crossSell: 9)
        let reasons = s.reasons
        XCTAssertEqual(reasons.count, 2, "a zero reason was listed: \(reasons)")
        XCTAssertTrue(reasons[0].contains("40"), "not biggest-first: \(reasons)")
        XCTAssertFalse(reasons.joined().contains("cohort"),
                       "a zero cohort count was described")
    }

    // MARK: - year to date that is not a year

    /// Six of the seven markets on 2026-08-22 returned partial:true — the EU
    /// markets only began advertising in June and KDP in August, while US
    /// covered the whole year. Printed identically, they invite a comparison
    /// that is not there.
    func testPartialYearSaysWhenItStarts() {
        let de = YearToDate(year: "2026", spend: 214.74, sales: 1905.4, orders: 124,
                            acos: 0.1127, partial: true, firstMonth: "2026-06",
                            supplemented: false, basis: "banked daily history only")
        XCTAssertEqual(de.partialLabel, "since Jun 2026")
    }

    func testAWholeYearIsNotLabelled() {
        let us = YearToDate(year: "2026", spend: 149.24, sales: 914.03, orders: 431,
                            acos: 0.1633, partial: false, firstMonth: "2026-01",
                            supplemented: true, basis: "banked daily history plus imported console months")
        XCTAssertNil(us.partialLabel, "a full year was labelled partial")
    }

    /// An older engine omits the flag. Absent must not read as "partial".
    func testAMissingFlagIsNotTreatedAsPartial() {
        let old = YearToDate(year: "2026", spend: 1, sales: 2, orders: 3, acos: nil,
                             partial: nil, firstMonth: nil, supplemented: nil, basis: nil)
        XCTAssertNil(old.partialLabel)
    }

    // MARK: - catalogue coverage behind an OPEN gate

    /// An open gate means the prices on hand are FRESH. It says nothing about
    /// how many designs have a price at all, and those two questions share one
    /// green light.
    func testCoverageCountsTheDesignsWithNoPrice() {
        let c = CatalogCoverage(designsMapped: 65_151, designsWanted: 84_328,
                                pricesOlderThanGate: 0, newest: "2026-08-20",
                                oldestPriceDate: "2026-08-04", files: [])
        XCTAssertEqual(c.unpriced, 19_177)
        XCTAssertEqual(c.coverage ?? 0, 0.7726, accuracy: 0.0005)
    }

    func testCoverageIsNilRatherThanOneWhenTheEngineSaidNothing() {
        let c = CatalogCoverage(designsMapped: nil, designsWanted: nil,
                                pricesOlderThanGate: nil, newest: nil,
                                oldestPriceDate: nil, files: nil)
        XCTAssertNil(c.coverage, "an unknown coverage read as a known one")
        XCTAssertEqual(c.unpriced, 0)
    }

    private func gate(catalog: CatalogCoverage? = nil,
                      econ: EconCoverage? = nil) -> EconGateResponse {
        EconGateResponse(ok: true, reasons: [], market: "US",
                         modelVersion: "v", currency: "USD",
                         catalog: catalog, econCoverage: econ)
    }

    /// The number to warn on comes from the GATE, not from the catalogue.
    @MainActor
    func testDesignsTheGateCannotJudgeRaiseAWarning() {
        let econ = EconCoverage(total: 85_287, ok: 79_613, transition: 5_470,
                                unknownPrice: 182, unmapped: 18, cohort: 4,
                                actionable: 200, actionableAsins: 177,
                                actionableLive: 177, actionableRemoved: 0,
                                actionableRemovedEnabled: 0,
                                removedStatuses: nil, excludedArchived: nil,
                                excludedStaleRows: nil, actionableSpend: 66.14)
        let issues = IssueDerivation.live(health: nil, econGate: gate(econ: econ), alerts: [])
        let issue = issues.first { $0.dedupKey == "econCoverage" }
        XCTAssertNotNil(issue)
        XCTAssertEqual(issue?.severity, .warning)
        // The headline counts PRODUCTS. One product can be advertised by several
        // ad groups, so the ad-group figure reads bigger than the thing there is
        // to go and fix — 200 against 177 here, and it was shipped as "200
        // designs" once already.
        // `.formatted()` is locale-aware on purpose, like every other number in
        // the app, so compare against the same formatting rather than a literal.
        XCTAssertTrue(issue?.title.contains(177.formatted()) ?? false,
                      "the headline does not count products: \(issue?.title ?? "-")")
        XCTAssertFalse(issue?.title.contains("design") ?? true,
                       "an ad-group count was called a design again")
        XCTAssertTrue(issue?.detail?.contains(200.formatted()) ?? false,
                      "the ad-group count vanished from the detail")
    }

    /// A count alone reads as bookkeeping. The spend is what says whether to
    /// care: these ad groups are not paused, not flagged and not counted,
    /// because no rule can decide, so no rule acts.
    @MainActor
    func testTheWarningNamesTheSpendAtStake() {
        let econ = EconCoverage(total: 85_287, ok: 79_613, transition: 5_470,
                                unknownPrice: 182, unmapped: 18, cohort: 4,
                                actionable: 200, actionableAsins: 177,
                                actionableLive: 177, actionableRemoved: 0,
                                actionableRemovedEnabled: 0,
                                removedStatuses: nil, excludedArchived: nil,
                                excludedStaleRows: nil, actionableSpend: 66.14)
        let issues = IssueDerivation.live(health: nil, econGate: gate(econ: econ), alerts: [])
        let detail = issues.first { $0.dedupKey == "econCoverage" }?.detail ?? ""
        XCTAssertTrue(detail.contains("66"),
                      "the spend at stake was not shown: \(detail)")
    }

    /// A price transition is a deliberate 30-day leniency after a price change.
    /// It expires by itself, so warning about it would nag for ever about
    /// something working exactly as designed.
    @MainActor
    func testATransitionAloneIsNotAWarning() {
        let econ = EconCoverage(total: 85_287, ok: 79_817, transition: 5_470,
                                unknownPrice: 0, unmapped: 0, cohort: 0,
                                actionable: 0, actionableAsins: 0,
                                actionableLive: 0, actionableRemoved: 0,
                                actionableRemovedEnabled: 0,
                                removedStatuses: nil, excludedArchived: nil,
                                excludedStaleRows: nil, actionableSpend: 0)
        let issues = IssueDerivation.live(health: nil, econGate: gate(econ: econ), alerts: [])
        XCTAssertNil(issues.first { $0.dedupKey == "econCoverage" })
    }

    /// The regression this file exists to prevent, in its own right.
    ///
    /// The first version of the coverage warning read `catalog` and fired on
    /// 19,177 designs with no list price. 18,001 of those were hats, which are
    /// priced from the TYPE table and never needed a list price — the gate could
    /// not judge 182. An alarm that overstates by two orders of magnitude gets
    /// muted, and then the real one is missed too.
    @MainActor
    func testCatalogueCoverageAloneNeverRaisesAnIssue() {
        let cov = CatalogCoverage(designsMapped: 65_151, designsWanted: 84_328,
                                  pricesOlderThanGate: 0, newest: "2026-08-20",
                                  oldestPriceDate: "2026-08-04", files: [])
        let issues = IssueDerivation.live(health: nil, econGate: gate(catalog: cov), alerts: [])
        XCTAssertTrue(issues.isEmpty,
                      "a partial catalogue raised an issue on its own: "
                      + issues.map(\.dedupKey).joined(separator: ", "))
    }

    /// Freshness is per DESIGN. The gate reads the newest chunk, so a design
    /// priced from a months-old chunk sits behind an open gate.
    @MainActor
    func testStalePricesBehindAnOpenGateAreReported() {
        let cov = CatalogCoverage(designsMapped: 100, designsWanted: 100,
                                  pricesOlderThanGate: 4_012, newest: "2026-08-20",
                                  oldestPriceDate: "2026-01-02", files: [])
        let issues = IssueDerivation.live(health: nil, econGate: gate(catalog: cov), alerts: [])
        XCTAssertNotNil(issues.first { $0.dedupKey == "catalogStalePrices" })
    }

    // MARK: - a Stream day that is missing an advertiser

    /// Rows are scoped to the advertisers KNOWN to be this market's, so an
    /// unresolved one is left out of every total, hour, placement and campaign.
    /// Nothing else in the reply changes shape — the day just reads quiet.
    func testUnresolvedAdvertisersSurviveDecoding() throws {
        let json = """
        {"market":"US","supported":true,"day":"2026-08-22","hours_delivered":16,
         "campaign_count":51,"campaigns_truncated":true,"campaigns":[],
         "unresolved_advertisers":[
           {"advertiser_id":"ENTITY9","market":null,"matched":0,"sampled":40,
            "learned_at":"2026-08-22T01:00:00","reason":"no campaign matched"}]}
        """.data(using: .utf8)!
        let r = try decoder().decode(StreamTodayResponse.self, from: json)
        XCTAssertEqual(r.unresolvedAdvertisers?.count, 1)
        XCTAssertNil(r.unresolvedAdvertisers?.first?.market)
        XCTAssertEqual(r.unresolvedAdvertisers?.first?.reason, "no campaign matched")
    }

    /// A capped campaign list reads exactly like a complete one.
    func testCampaignCountIsTheTrueTotalNotTheListLength() throws {
        let json = """
        {"market":"US","supported":true,"campaign_count":51,
         "campaigns_truncated":true,
         "campaigns":[{"campaign_id":"c1","campaign":"A","impressions":9,"clicks":1,"cost":0.4}]}
        """.data(using: .utf8)!
        let r = try decoder().decode(StreamTodayResponse.self, from: json)
        XCTAssertEqual(r.campaigns?.count, 1)
        XCTAssertEqual(r.campaignCount, 51)
        XCTAssertEqual(r.campaignsTruncated, true)
    }

    // MARK: - the deadline that kills the data feed quietly

    /// A removed listing has no price and can never get one, so a re-export
    /// cannot help it. Headlining it, and offering the re-map command for it,
    /// sends the operator on an errand that cannot succeed. On 2026-08-22 that
    /// was 58 of 72 products.
    @MainActor
    func testRemovedListingsAreNotCountedAsFixable() {
        let econ = EconCoverage(total: 85_120, ok: 78_744, transition: 6_458,
                                unknownPrice: 63, unmapped: 18, cohort: 4,
                                actionable: 81, actionableAsins: 72,
                                actionableLive: 14, actionableRemoved: 58,
                                actionableRemovedEnabled: 12,
                                removedStatuses: ["deleted_content_creator": 41,
                                                  "deleted_content_policy_violation": 9,
                                                  "deleted_inactive_no_sales": 6],
                                excludedArchived: 65, excludedStaleRows: 102,
                                actionableSpend: 18.75)
        let issues = IssueDerivation.live(health: nil, econGate: gate(econ: econ), alerts: [])
        let issue = issues.first { $0.dedupKey == "econCoverage" }
        XCTAssertNotNil(issue)
        XCTAssertTrue(issue?.title.contains(14.formatted()) ?? false,
                      "the headline counts removed listings as fixable: \(issue?.title ?? "-")")
        XCTAssertTrue(issue?.detail?.contains(58.formatted()) ?? false,
                      "the removed ones were not mentioned at all")
        XCTAssertTrue(issue?.detail?.contains("deleted content creator") ?? false,
                      "the reasons were not spelled out: \(issue?.detail ?? "-")")
    }

    /// When everything left is a removed listing there is nothing to re-export,
    /// so the fix command must not be offered at all.
    @MainActor
    func testNothingFixableOffersNoCommand() {
        let econ = EconCoverage(total: 100, ok: 90, transition: 0,
                                unknownPrice: 10, unmapped: 0, cohort: 0,
                                actionable: 10, actionableAsins: 10,
                                actionableLive: 0, actionableRemoved: 10,
                                actionableRemovedEnabled: 0,
                                removedStatuses: ["timed_out": 10],
                                excludedArchived: 0, excludedStaleRows: 0,
                                actionableSpend: 1.0)
        let issues = IssueDerivation.live(health: nil, econGate: gate(econ: econ), alerts: [])
        let issue = issues.first { $0.dedupKey == "econCoverage" }
        XCTAssertNotNil(issue)
        XCTAssertNil(issue?.fix, "offered a re-map for listings no export can price")
    }

    func testRemovedSummaryIsBiggestFirstAndReadable() {
        let text = IssueDerivation.removedSummary(["deleted_content_creator": 41,
                                                   "deleted_inactive_no_sales": 6,
                                                   "deleted_content_policy_violation": 9])
        XCTAssertTrue(text.hasPrefix("41 deleted content creator"), text)
        XCTAssertFalse(text.contains("_"), "underscores reached the screen: \(text)")
    }

    /// Telling the operator to pause an ad that is already paused reads exactly
    /// like a task that still needs doing. After the 2026-08-22 pause run, 56 of
    /// the 58 removed listings were already off.
    @MainActor
    func testAlreadyPausedRemovedListingsAskForNothing() {
        let econ = EconCoverage(total: 85_120, ok: 78_682, transition: 6_367,
                                unknownPrice: 60, unmapped: 7, cohort: 4,
                                actionable: 67, actionableAsins: 60,
                                actionableLive: 2, actionableRemoved: 58,
                                actionableRemovedEnabled: 0,
                                removedStatuses: ["deleted_content_creator": 41],
                                excludedArchived: 65, excludedStaleRows: 102,
                                actionableSpend: 1.34)
        let detail = IssueDerivation.live(health: nil, econGate: gate(econ: econ),
                                          alerts: []).first { $0.dedupKey == "econCoverage" }?.detail ?? ""
        XCTAssertTrue(detail.contains("already paused"),
                      "did not say the removed ads are already off: \(detail)")
        XCTAssertFalse(detail.contains("pause those"),
                       "asked for a pause that has already happened")
    }

    func testAwsPlanExpiryLandsOnSystemHealth() {
        let alert = EngineAlert(kind: "aws_plan_expiry", key: "aws:US",
                                message: "…", market: "US")
        XCTAssertEqual(IssueDerivation.alertRoute(alert), .screen(.health))
    }
}
