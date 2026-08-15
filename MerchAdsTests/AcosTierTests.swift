import XCTest
@testable import Merch_Ads

final class AcosTierTests: XCTestCase {
    func testNeutralComfortTiersDoNotClaimProfitability() {
        XCTAssertEqual(AcosTier.select(acos: nil), .unavailable)
        XCTAssertEqual(AcosTier.select(acos: 0.22), .comfort)
        XCTAssertEqual(AcosTier.select(acos: 0.221), .elevated)
        XCTAssertEqual(AcosTier.select(acos: 0.30), .elevated)
        XCTAssertEqual(AcosTier.select(acos: 0.301), .high)
    }

    func testBreakEvenEconomicsSelectProfitabilityTier() {
        XCTAssertEqual(AcosTier.select(acos: 0.25, breakEven: 0.25), .profitable)
        XCTAssertEqual(AcosTier.select(acos: 0.251, breakEven: 0.25), .unprofitable)
    }

    func testRoyaltyROISelectsProfitabilityTier() {
        XCTAssertEqual(AcosTier.select(acos: 0.80, royaltyROI: 1.0), .profitable)
        XCTAssertEqual(AcosTier.select(acos: 0.10, royaltyROI: 0.99), .unprofitable)
    }

    func testBreakEvenTakesPrecedenceWhenBothEconomicsArePresent() {
        XCTAssertEqual(AcosTier.select(acos: 0.20, breakEven: 0.25, royaltyROI: 0.5),
                       .profitable)
    }
}
