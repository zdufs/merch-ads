import XCTest
@testable import Merch_Ads

/// Product Royalty is where the operator changes the numbers the whole app
/// prices with, so the row model has to be exactly honest about two things:
/// what each figure is, and where it came from. A royalty shown as "built-in"
/// when the operator set it — or the reverse — would send them hunting through
/// Python for a number that is sitting in their own overrides file.
final class ProductRoyaltyTests: XCTestCase {

    private func tee(price: Double = 21.99, cents: Int = 2199,
                     royalty: Double = 6.88, royaltyCents: Int = 688,
                     source: String = "built-in",
                     extrapolated: Bool = false,
                     growthPriced: Bool = false) -> RoyaltyTeePrice {
        RoyaltyTeePrice(priceCents: cents, price: price,
                        royaltyCents: royaltyCents, royalty: royalty,
                        breakEven: royalty / price, source: source,
                        extrapolated: extrapolated, growthPriced: growthPriced,
                        note: nil, updatedAt: nil)
    }

    private func type(_ name: String = "standard_sweatshirt",
                      label: String = "Standard Sweatshirt",
                      royalty: Double? = 8.10, price: Double? = 36.00,
                      breakEven: Double? = 0.225, model: String? = "B",
                      adGroups: Int? = 12, source: String = "built-in") -> RoyaltyProductType {
        RoyaltyProductType(productType: name, label: label, royalty: royalty,
                           price: price, breakEven: breakEven, model: model,
                           negThreshold: nil, pauseThreshold: nil,
                           adGroups: adGroups, source: source, listings: nil,
                           note: nil, updatedAt: nil)
    }

    // MARK: - What a row says it is

    func testATeeRowIsNamedByItsPrice() {
        let row = RoyaltyRow(tee: tee())
        XCTAssertEqual(row.kind, .tee)
        // The price is money, so it is formatted for the reader's locale
        // ("$21.99" or "21,99 US$"). Only the digits are guaranteed.
        XCTAssertTrue(row.name.contains("21"), row.name)
        XCTAssertTrue(row.name.hasSuffix("tee"), row.name)
        XCTAssertEqual(row.key, "2199", "the engine edits a tee price in cents")
    }

    func testAProductTypeRowIsNamedForAHuman() {
        let row = RoyaltyRow(type: type())
        XCTAssertEqual(row.kind, .type)
        XCTAssertEqual(row.name, "Standard Sweatshirt")
        XCTAssertEqual(row.key, "standard_sweatshirt", "the engine edits a type by its own spelling")
    }

    func testTheTwoKindsCannotCollideInOneTable() {
        XCTAssertNotEqual(RoyaltyRow(tee: tee()).id, RoyaltyRow(type: type()).id)
    }

    // MARK: - Where the number came from

    func testAnOperatorEditIsLabelledAsTheirs() {
        let row = RoyaltyRow(type: type(source: "operator"))
        XCTAssertEqual(row.sourceLabel, "yours")
        XCTAssertTrue(row.sourceHelp.contains("Reset"),
                      "the label must say how to undo it")
    }

    func testAShippedNumberIsLabelledBuiltIn() {
        XCTAssertEqual(RoyaltyRow(type: type()).sourceLabel, "built-in")
    }

    func testADerivedNumberSaysItCameFromTheExport() {
        let row = RoyaltyRow(type: type(source: "derived"))
        XCTAssertEqual(row.sourceLabel, "your export")
        XCTAssertTrue(row.sourceHelp.lowercased().contains("export"))
    }

    // MARK: - Flags

    func testAGuessedRoyaltyIsFlagged() {
        let flags = RoyaltyRow(tee: tee(extrapolated: true)).flags
        XCTAssertEqual(flags.count, 1)
        XCTAssertEqual(flags.first?.text, "guessed")
    }

    func testARankPushPriceIsFlagged() {
        let flags = RoyaltyRow(tee: tee(price: 14.99, cents: 1499, royalty: 1.28,
                                        royaltyCents: 128, growthPriced: true)).flags
        XCTAssertEqual(flags.first?.text, "rank push")
        XCTAssertTrue(flags.first!.help.contains("floor"),
                      "the operator must learn WHY a cheap tee is not auto-paused")
    }

    func testAConfirmedOrdinaryPriceCarriesNoFlags() {
        XCTAssertTrue(RoyaltyRow(tee: tee()).flags.isEmpty)
    }

    // MARK: - Sorting

    func testAMissingFigureSortsBelowEveryRealOne() {
        let missing = RoyaltyRow(type: type(royalty: nil, price: nil, breakEven: nil,
                                            adGroups: nil))
        XCTAssertEqual(missing.priceSort, -1)
        XCTAssertEqual(missing.royaltySort, -1)
        XCTAssertEqual(missing.breakEvenSort, -1)
        XCTAssertEqual(missing.adGroupsSort, -1)
        XCTAssertLessThan(missing.royaltySort, RoyaltyRow(type: type()).royaltySort)
    }

    // MARK: - Honesty about the price

    /// The engine now keeps the real list price beside every royalty, so no row
    /// shows a divided one. The sweatshirt reads 33,99, not 45,03.
    func testNoPriceIsWorkedOutAnyMore() {
        XCTAssertFalse(RoyaltyRow(type: type()).priceIsImplied)
        XCTAssertFalse(RoyaltyRow(type: type(source: "derived")).priceIsImplied)
    }

    func testAPriceTheOperatorTypedIsNotMarked() {
        XCTAssertFalse(RoyaltyRow(type: type(source: "operator")).priceIsImplied)
    }

    func testARungsPriceIsNeverImplied() {
        XCTAssertFalse(RoyaltyRow(tee: tee()).priceIsImplied, "a rung IS its price")
    }

    func testTheTeeRowPointsAtTheLadder() {
        let flags = RoyaltyRow(type: type("standard_tshirt", label: "Standard Tshirt",
                                          model: "A")).flags
        XCTAssertTrue(flags.contains { $0.text == "per design" },
                      "the operator must learn that individual tees price off the ladder")
    }

    // MARK: - Sorting the ladder

    /// Prices sorted as text put $9.99 after $24.99. The ladder must read in
    /// price order or the cheapest rung hides in the middle.
    func testRungsSortByPriceNotByText() {
        let cheap = RoyaltyRow(tee: tee(price: 9.99, cents: 999, royalty: 0.50, royaltyCents: 50))
        let dear = RoyaltyRow(tee: tee(price: 24.99, cents: 2499, royalty: 9.27, royaltyCents: 927))
        XCTAssertLessThan(cheap.nameSort, dear.nameSort)
    }

    func testATypeStillSortsByItsName() {
        XCTAssertEqual(RoyaltyRow(type: type()).nameSort, "Standard Sweatshirt")
    }

    // MARK: - Typing numbers

    /// This operator is in Europe. A comma decimal separator must work, or the
    /// Save button sits dead with no explanation.
    func testACommaDecimalIsAccepted() {
        XCTAssertEqual(ProductRoyaltyView.decimal("21,99"), 21.99)
        XCTAssertEqual(ProductRoyaltyView.decimal("21.99"), 21.99)
        XCTAssertEqual(ProductRoyaltyView.decimal(" 6,88 "), 6.88)
    }

    func testNonsenseIsStillRejected() {
        XCTAssertNil(ProductRoyaltyView.decimal(""))
        XCTAssertNil(ProductRoyaltyView.decimal("   "))
        XCTAssertNil(ProductRoyaltyView.decimal("lots"))
    }

    // MARK: - Per-market data

    /// Amazon fixes a different maximum price in every market, so a royalty
    /// belongs to one market. The screen read `royalties` with no market for a
    /// while and silently showed US figures under a "Merch DE" heading.
    func testTheScreenAsksTheBridgeForAMarket() throws {
        let src = try String(contentsOfFile: Self.viewSourcePath, encoding: .utf8)
        let calls = src.components(separatedBy: "bridge.call(").dropFirst()
        XCTAssertFalse(calls.isEmpty, "no bridge calls found — did the file move?")
        for call in calls {
            let head = String(call.prefix(320))
            XCTAssertTrue(head.contains("market: appState.selectedMarket"),
                          "a royalty bridge call is missing its market: \(head.prefix(90))")
        }
    }

    private static var viewSourcePath: String {
        URL(fileURLWithPath: #filePath)          // …/MerchAdsTests/ProductRoyaltyTests.swift
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("MerchAds/Views/Campaigns/ProductRoyaltyView.swift")
            .path
    }

    // MARK: - Where the screen lives

    func testTheScreenIsHiddenForABookAccount() {
        XCTAssertTrue(Screen.productRoyalty.isMerchOnly,
                      "a KDP account prices books on KDP Books, not here")
        XCTAssertFalse(Screen.productRoyalty.isAvailable(forKDP: true))
        XCTAssertTrue(Screen.productRoyalty.isAvailable(forKDP: false))
    }

    func testTheScreenIsFullyRegistered() {
        XCTAssertEqual(Screen.productRoyalty.title, "Product Royalty")
        XCTAssertFalse(Screen.productRoyalty.icon.isEmpty)
        XCTAssertFalse(Screen.productRoyalty.blurb.isEmpty)
    }

    func testTheScreenSurvivesARelaunch() {
        XCTAssertEqual(Screen.restored(from: "productRoyalty"), .productRoyalty)
    }
}
