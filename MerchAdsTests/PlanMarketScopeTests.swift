import XCTest
@testable import Merch_Ads

/// A plan must not be applied under a market it was never resolved in.
///
/// Found by the 2026-08-24 review on three action paths. The profile picker can
/// move while a preview is in flight or a confirmation dialog is open, and the
/// ids in the plan mean nothing in the other account — or worse, in the
/// "act everywhere" case, they mean something and the write lands there.
final class PlanMarketScopeTests: XCTestCase {

    func testAPlanFromAnotherMarketIsRefusedAndSaysNothingWasSent() throws {
        let refusal = try XCTUnwrap(PlanMarket.refusal(planned: "US", current: "DE"))
        XCTAssertTrue(refusal.contains("US"), "the refusal must name the plan's market")
        XCTAssertTrue(refusal.contains("DE"), "and the market the app is on now")
        XCTAssertTrue(refusal.contains("Nothing was sent"),
                      "a refusal that does not say the write did not happen is worse "
                      + "than none: \(refusal)")
    }

    func testTheSameMarketProceeds() {
        XCTAssertNil(PlanMarket.refusal(planned: "US", current: "US"))
    }

    /// A global or all-markets action carries no market, so there is nothing to
    /// disagree with — refusing those would block the KILL switch.
    func testAPlanWithNoMarketProceeds() {
        XCTAssertNil(PlanMarket.refusal(planned: nil, current: "DE"))
    }
}
