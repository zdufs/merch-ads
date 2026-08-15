import XCTest
@testable import Merch_Ads

/// The status band's month-to-date cards label their period from the engine's
/// window string ("2026-08-01→2026-08-03"). If that shape ever changes, the
/// label must degrade to nil so the card falls back to a plain "current month"
/// rather than rendering a wrong or half-parsed date range.
final class MonthWindowLabelTests: XCTestCase {

    func testSameMonthWindowCollapsesTheStartDate() {
        XCTAssertEqual(DashboardView.windowLabel("2026-08-01→2026-08-03"), "01.–03.08.2026")
    }

    func testSingleDayWindow() {
        XCTAssertEqual(DashboardView.windowLabel("2026-08-03→2026-08-03"), "03.–03.08.2026")
    }

    func testWindowStraddlingTwoMonthsKeepsBothDates() {
        XCTAssertEqual(DashboardView.windowLabel("2026-07-28→2026-08-03"),
                       "28.07.2026–03.08.2026")
    }

    func testWindowSpanningNewYearKeepsBothDates() {
        XCTAssertEqual(DashboardView.windowLabel("2025-12-30→2026-01-02"),
                       "30.12.2025–02.01.2026")
    }

    func testMalformedWindowsReturnNil() {
        XCTAssertNil(DashboardView.windowLabel(""))
        XCTAssertNil(DashboardView.windowLabel("2026-08-01"))              // no separator
        XCTAssertNil(DashboardView.windowLabel("2026-08-01 to 2026-08-03"))  // wrong separator
        XCTAssertNil(DashboardView.windowLabel("not-a-date→2026-08-03"))
        XCTAssertNil(DashboardView.windowLabel("2026-08-01→not-a-date"))
        XCTAssertNil(DashboardView.windowLabel("2026-08-01→2026-08-02→2026-08-03"))
    }
}
