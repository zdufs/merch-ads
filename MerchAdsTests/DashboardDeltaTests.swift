import XCTest
@testable import Merch_Ads

final class DashboardDeltaTests: XCTestCase {
    func testCompleteSixtyDayHistoryComputesAnchoredDeltas() throws {
        let days = makeDays()
        let result = try XCTUnwrap(DashboardDeltaCalculator.compute(days: days.shuffled()))

        XCTAssertEqual(result.current.spend, 60, accuracy: 0.001)
        XCTAssertEqual(result.previous.spend, 30, accuracy: 0.001)
        XCTAssertEqual(result.current.sales, 120, accuracy: 0.001)
        XCTAssertEqual(result.previous.sales, 60, accuracy: 0.001)
        XCTAssertEqual(try XCTUnwrap(result.spend).value, 1, accuracy: 0.001)
        XCTAssertEqual(result.spend?.tone, .negative)
        XCTAssertEqual(try XCTUnwrap(result.sales).value, 1, accuracy: 0.001)
        XCTAssertEqual(result.sales?.tone, .positive)
        XCTAssertEqual(try XCTUnwrap(result.acos).value, 0, accuracy: 0.001)
    }

    func testGapInCurrentWindowSuppressesAllDeltas() {
        var days = makeDays()
        days.removeAll { $0.date == dayString(daysAgo: 10) }
        XCTAssertNil(DashboardDeltaCalculator.compute(days: days))
    }

    func testGapInPriorWindowSuppressesAllDeltas() {
        var days = makeDays()
        days.removeAll { $0.date == dayString(daysAgo: 40) }
        XCTAssertNil(DashboardDeltaCalculator.compute(days: days))
    }

    func testOlderRowsDoNotMoveLatestDateAnchor() throws {
        var days = makeDays()
        let oldDate = try XCTUnwrap(Calendar.current.date(
            byAdding: .day, value: -90, to: anchor))
        days.append(DailyDay(date: Format.dayString(of: oldDate), spend: 999,
                             sales: 999, orders: 999, acos: 1))
        let result = try XCTUnwrap(DashboardDeltaCalculator.compute(days: days))
        XCTAssertEqual(result.current.spend, 60, accuracy: 0.001)
    }

    private var anchor: Date {
        Calendar.current.date(from: DateComponents(year: 2026, month: 3, day: 31))!
    }

    private func dayString(daysAgo: Int) -> String {
        let date = Calendar.current.date(byAdding: .day, value: -daysAgo, to: anchor)!
        return Format.dayString(of: date)
    }

    private func makeDays() -> [DailyDay] {
        (0..<60).map { offset in
            let currentWindow = offset < 30
            return DailyDay(
                date: dayString(daysAgo: offset),
                spend: currentWindow ? 2 : 1,
                sales: currentWindow ? 4 : 2,
                orders: currentWindow ? 2 : 1,
                acos: 0.5)
        }
    }
}
