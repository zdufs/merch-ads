import XCTest
@testable import Merch_Ads

/// The dashboard shows a subset of what the `periods` endpoint returns.
///
/// "Previous year" and "All time" were removed on 2026-08-06 at the operator's request.
/// They are still computed and still banked — the console-history import is the
/// only copy of anything older than Amazon's ~95-day retention — so the filter
/// lives here in the app rather than in the engine, and Reports still reaches
/// that history.
final class DashboardPeriodStackTests: XCTestCase {
    private func periods(_ keys: [String]) throws -> [PeriodRow] {
        let objects = keys.map {
            "{\"key\":\"\($0)\",\"label\":\"\($0)\",\"available\":true,\"spend\":1.0}"
        }
        let data = "[\(objects.joined(separator: ","))]".data(using: .utf8)!
        // Borrows the app's decoder. A test that builds its own proves
        // nothing about the one the bridge uses — see PythonBridge.makeDecoder.
        return try PythonBridge.makeDecoder().decode([PeriodRow].self, from: data)
    }

    private let full = ["current_month", "previous_month", "ytd", "previous_year", "all_time"]

    func testStackDropsPreviousYearAndAllTime() throws {
        let stack = PeriodRow.dashboardStack(from: try periods(full))
        XCTAssertEqual(stack.map(\.key), ["previous_month", "ytd"])
    }

    func testCurrentMonthIsExcludedBecauseItIsPinnedAbove() throws {
        // It renders in the status band, so including it here would double it.
        let stack = PeriodRow.dashboardStack(from: try periods(full))
        XCTAssertFalse(stack.contains { $0.key == "current_month" })
    }

    func testEngineOrderIsPreserved() throws {
        // The engine orders the stack; the app must never re-sort it.
        let stack = PeriodRow.dashboardStack(from: try periods(["ytd", "previous_month"]))
        XCTAssertEqual(stack.map(\.key), ["ytd", "previous_month"])
    }

    func testAnUnknownPeriodStillShows() throws {
        // Only the two named rows are hidden. A period the engine adds later
        // must appear rather than being silently swallowed by a whitelist.
        let stack = PeriodRow.dashboardStack(from: try periods(["previous_quarter"]))
        XCTAssertEqual(stack.map(\.key), ["previous_quarter"])
    }
}
