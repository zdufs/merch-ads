import XCTest
@testable import Merch_Ads

/// System Health has to be able to tell three states apart:
/// capped, NOT capped, and "this engine can't say".
///
/// The middle one is the reason the column exists. The ceiling is per market and
/// only Settings showed it, one market at a time — so five EU markets ran with
/// no cap for months while the nightly rules wrote bids in all of them, and
/// nothing in the app looked any different. A blank field reads as "not filled
/// in yet"; it has to read as "nothing is stopping these bids".
final class BidCeilingHealthTests: XCTestCase {

    private func health(_ ceiling: BidCeilingRow?) -> MarketHealth {
        MarketHealth(market: "UK", configured: true, hasData: true,
                     latestData: nil, lastPull: nil, lastWrite: nil,
                     campaigns: 1, campaignsEnabled: 1, lastNote: nil,
                     reportsPending: nil, staleTables: nil, targetDaily: nil,
                     dailyTotals: nil, bidCeiling: ceiling, error: nil)
    }

    func testBothSurfacesBlankIsUncapped() {
        let h = health(BidCeilingRow(target: nil, keyword: nil, budget: nil))
        XCTAssertTrue(h.bidsAreUncapped)
    }

    func testACappedMarketIsNotFlagged() {
        let h = health(BidCeilingRow(target: 0.35, keyword: 0.35, budget: nil))
        XCTAssertFalse(h.bidsAreUncapped)
    }

    func testOneSurfaceCappedIsNotTheSameAsUncapped() {
        let h = health(BidCeilingRow(target: 0.50, keyword: nil, budget: nil))
        XCTAssertFalse(h.bidsAreUncapped,
                       "half-capped is its own state — the cell shows both numbers")
    }

    /// A blank daily-budget cap must never raise the warning on its own.
    /// Budgets are almost never written by automation; bids are written nightly.
    /// Counting budget would flag all seven markets and the warning would stop
    /// meaning anything.
    func testABlankBudgetCapAloneDoesNotFlagTheMarket() {
        let h = health(BidCeilingRow(target: 0.35, keyword: 0.35, budget: nil))
        XCTAssertFalse(h.bidsAreUncapped)
    }

    /// An engine too old to report the field sends nothing. That is unknown, not
    /// uncapped, and borrowing the warning would cry wolf on every row.
    func testAMissingFieldIsUnknownRatherThanUncapped() {
        XCTAssertFalse(health(nil).bidsAreUncapped)
    }

    /// The engine sends every surface explicitly so this decode is unambiguous.
    func testAnExplicitNullDecodesToNoCeiling() throws {
        let json = Data(#"{"target": null, "keyword": null, "budget": null}"#.utf8)
        let row = try PythonBridge.makeDecoder().decode(BidCeilingRow.self, from: json)
        XCTAssertNil(row.target)
        XCTAssertNil(row.keyword)
        XCTAssertTrue(health(row).bidsAreUncapped)
    }

    func testNumbersDecodeFromTheEnginesShape() throws {
        let json = Data(#"{"target": 0.65, "keyword": 0.65, "budget": null}"#.utf8)
        let row = try PythonBridge.makeDecoder().decode(BidCeilingRow.self, from: json)
        XCTAssertEqual(row.target, 0.65)
        XCTAssertEqual(row.keyword, 0.65)
        XCTAssertFalse(health(row).bidsAreUncapped)
    }
}
