import XCTest
@testable import Merch_Ads

/// Every alert kind the engine emits must land somewhere useful.
///
/// `alertRoute` has a `default` arm that sends anything unknown to the
/// Dashboard, and `title(for:)` falls back to "Merch Ads". Both are the right
/// safety net for an engine that is newer than the app — and both are silent, so
/// a kind that was never wired up looks exactly like one that was. These tests
/// pin the kinds we ship today.
final class AlertRoutingTests: XCTestCase {

    private func alert(_ kind: String, market: String? = "US") -> EngineAlert {
        EngineAlert(kind: kind, key: "\(kind):US", message: "…", market: market)
    }

    /// The seasonal tag map going empty means the scheduler is doing nothing.
    /// Seasonal is the screen that shows the tag count and takes the tags back.
    func testSeasonalTagsLostLandsOnSeasonal() {
        XCTAssertEqual(IssueDerivation.alertRoute(alert("seasonal_tags_lost")),
                       .screen(.seasonal))
    }

    /// Every authored rule vanishing is the loudest thing the engine can say.
    /// Rules is the screen that shows the (now empty) list.
    func testRulesLostLandsOnRules() {
        XCTAssertEqual(IssueDerivation.alertRoute(alert("rules_lost")), .screen(.rules))
    }

    func testEveryShippedKindHasItsOwnRoute() {
        let expected: [String: Route] = [
            "kill_candidate": .screen(.killList),
            "budget_max": .screen(.campaigns),
            "spend_spike": .screen(.dashboard),
            "data_stale": .screen(.health),
            "portfolio_cap": .screen(.dashboard),
            "seasonal_tags_lost": .screen(.seasonal),
            "rules_lost": .screen(.rules),
        ]
        for (kind, route) in expected {
            XCTAssertEqual(IssueDerivation.alertRoute(alert(kind)), route,
                           "\(kind) no longer routes where it should")
        }
    }

    /// A kind with no entity ids must still route rather than crash — that is
    /// what the fallbacks are for.
    func testAnUnknownKindStillRoutes() {
        XCTAssertEqual(IssueDerivation.alertRoute(alert("something_new")),
                       .screen(.dashboard))
    }
}
