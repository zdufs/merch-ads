import XCTest
@testable import Merch_Ads

/// A period's four cards do not always cover the same window, and the row has
/// to say which one profit covers.
///
/// `appctl periods` can extend a window backwards with months imported from the
/// Ads console. Spend, sales and ACOS then cover the whole window. Profit
/// cannot: royalty is per design and the imported months carry no per-design
/// economics, so the engine models profit over the daily-banked portion alone
/// and explains that in `profit_note`.
///
/// `PeriodRow` decoded neither `profit_note` nor `months_imported`, so both were
/// dropped. The Dashboard's Year to date row therefore showed a whole year of ad
/// spend beside a profit figure covering only the last 143 of those days, with
/// nothing marking the difference. Three months of spend had no profit next to
/// them, and the card said "modeled royalty" as though it covered everything.
///
/// The All time row carries the same fault and is far starker: five years of
/// spend against that identical profit figure, because both are drawn from the
/// same banked days. It reaches no screen today only because
/// `hiddenFromDashboard` drops it, which is a layout choice, not a guard. The
/// fixtures below use it deliberately: the row the app happens not to draw is
/// the one that shows what the missing sentence was worth.
///
/// The engine sets `partial` FALSE on exactly these rows, and it is right to:
/// the window is not partial for the three figures that do cover it. So the
/// app's existing "partial" badge can never fire here. This is the only thing
/// standing between the reader and that number.
final class PeriodProfitWindowTests: XCTestCase {

    private func decode(_ json: String) throws -> PeriodRow {
        // Borrows the app's decoder. A test that builds its own proves
        // nothing about the one the bridge uses — see PythonBridge.makeDecoder.
        return try PythonBridge.makeDecoder().decode(PeriodRow.self, from: Data(json.utf8))
    }

    /// An All time row in the exact shape `appctl periods` returns. The figures
    /// are synthetic — what is under test is the SPAN each one covers, not its
    /// size — but the window fields and the ratios between them are real.
    private var allTimeJSON: String {
        """
        {"key":"all_time","label":"All time","available":true,
         "window":"2021-09→2026-08-21","requested_window":"2026-04-01→2026-08-21",
         "partial":false,"days_banked":143,"spend":90.00,"sales":625.00,
         "orders":300,"acos":0.144,"royalty_est":110.00,"profit":49.00,
         "covered_spend":66.00,"uncovered_spend":1.75,"royalty_per_order":5.98,
         "basis":"per-campaign product-type mix","modeled":true,
         "months_imported":55,"source":"banked daily + imported monthly",
         "profit_note":"profit covers the daily-banked portion only; imported months have no per-design economics"}
        """
    }

    /// A plain month: no imported history, so every card covers the same window.
    private var currentMonthJSON: String {
        """
        {"key":"current_month","label":"Current month","available":true,
         "window":"2026-08-01→2026-08-21","requested_window":"2026-08-01→2026-08-21",
         "partial":false,"days_banked":21,"spend":634.72,"sales":4042.0,
         "orders":172,"acos":0.157,"profit":274.1,"modeled":true}
        """
    }

    // MARK: - the fields survive decoding at all

    func testTheEnginesProfitNoteReachesTheApp() throws {
        let row = try decode(allTimeJSON)
        XCTAssertEqual(row.profitNote,
                       "profit covers the daily-banked portion only; "
                       + "imported months have no per-design economics")
        XCTAssertEqual(row.monthsImported, 55)
    }

    func testAnOlderEngineWithNeitherFieldStillDecodes() throws {
        let row = try decode(currentMonthJSON)
        XCTAssertNil(row.profitNote)
        XCTAssertNil(row.monthsImported)
    }

    // MARK: - the profit card admits its window

    func testAShortProfitWindowIsFlagged() throws {
        XCTAssertTrue(try decode(allTimeJSON).profitWindowIsShorter)
    }

    func testAWholeWindowIsNotFlagged() throws {
        XCTAssertFalse(try decode(currentMonthJSON).profitWindowIsShorter)
    }

    /// The subtitle is the whole fix. It must NOT be the ordinary footnote, and
    /// it must name the span, because "modeled royalty" beside a five-year spend
    /// figure is exactly what read as five years of profit.
    func testTheProfitCardNamesTheDaysItActuallyCovers() throws {
        let subtitle = try decode(allTimeJSON).profitSubtitle
        XCTAssertNotEqual(subtitle, "modeled royalty")
        XCTAssertTrue(subtitle.contains("143"),
                      "the profit subtitle must name its own span, got \(subtitle)")
    }

    func testAWholeWindowKeepsThePlainModeledFootnote() throws {
        XCTAssertEqual(try decode(currentMonthJSON).profitSubtitle, "modeled royalty")
    }

    /// `previous_year` is entirely imported months: profit is null and there is
    /// no banked day to name. A bare "—" under "modeled royalty" would read as
    /// a broken card rather than a deliberate refusal.
    func testAPeriodWithNoBankedDaysSaysProfitWasNotEstimated() throws {
        let row = try decode("""
        {"key":"previous_year","label":"Previous year","available":true,
         "window":"2025-01→2025-12","partial":false,"months_imported":12,
         "spend":34.50,"sales":240.00,"orders":115,"acos":0.1438,
         "profit":null,
         "profit_note":"no profit estimate: royalty is modeled from today's per-design economics, which cannot be applied to months this old (US tee prices moved $23.99 to $19.99 in a single week)."}
        """)
        XCTAssertNil(row.profit)
        XCTAssertEqual(row.profitSubtitle, "not estimated for imported months")
    }

    // MARK: - the other three cards cover MORE than the banked days

    /// Spend, sales and ACOS include the imported months, so labelling them
    /// with the banked-day count alone understates them — "143 days" sat under
    /// a five-year spend figure.
    func testSpendAndSalesSayTheyIncludeImportedMonths() throws {
        let span = try XCTUnwrap(try decode(allTimeJSON).spanSubtitle)
        XCTAssertTrue(span.contains("143"), span)
        XCTAssertTrue(span.contains("55"), span)
        XCTAssertTrue(span.contains("imported"), span)
    }

    func testAPlainMonthStillJustCountsItsDays() throws {
        XCTAssertEqual(try decode(currentMonthJSON).spanSubtitle, "21 days")
    }

    func testAWhollyImportedPeriodCountsMonths() throws {
        let row = try decode("""
        {"key":"previous_year","label":"Previous year","available":true,
         "partial":false,"months_imported":12,"spend":1.0,"profit":null}
        """)
        XCTAssertEqual(row.spanSubtitle, "12 months imported")
    }

    func testSingularsReadAsEnglish() throws {
        let oneDay = try decode("""
        {"key":"k","label":"L","available":true,"partial":false,"days_banked":1}
        """)
        XCTAssertEqual(oneDay.spanSubtitle, "1 day")

        let oneMonth = try decode("""
        {"key":"k","label":"L","available":true,"partial":false,"months_imported":1}
        """)
        XCTAssertEqual(oneMonth.spanSubtitle, "1 month imported")

        let both = try decode("""
        {"key":"k","label":"L","available":true,"partial":false,
         "days_banked":1,"months_imported":1,
         "profit_note":"profit covers the daily-banked portion only"}
        """)
        XCTAssertEqual(both.spanSubtitle, "1 day + 1 month imported")
        XCTAssertEqual(both.profitSubtitle, "modeled · 1 banked day only")
    }

    func testAPeriodWithNeitherCountSaysNothing() throws {
        let row = try decode("""
        {"key":"k","label":"L","available":false,"partial":false}
        """)
        XCTAssertNil(row.spanSubtitle)
    }
}
