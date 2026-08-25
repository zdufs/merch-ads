import XCTest
@testable import Merch_Ads

final class PythonBridgeRehearsalTests: XCTestCase {
    func testReadAllowlistAndArgumentAwareDenials() {
        XCTAssertTrue(PythonBridge.rehearsalAllows(["metrics"]))
        XCTAssertTrue(PythonBridge.rehearsalAllows(["status", "B012345678"]))
        XCTAssertTrue(PythonBridge.rehearsalAllows(["import-preview", "/tmp/input.csv"]))
        XCTAssertFalse(PythonBridge.rehearsalAllows(["pause", "--adgroup", "1"]))
        XCTAssertFalse(PythonBridge.rehearsalAllows(["demandfeed", "--refresh"]))
        XCTAssertFalse(PythonBridge.rehearsalAllows(["season-suggest", "--apply"]))
        XCTAssertFalse(PythonBridge.rehearsalAllows(["kill", "--off"]))
    }

    func testMaxbidSetIsAWriteRehearsalMustDeny() {
        // --set writes the per-market bid ceiling — a bid-safety control.
        // Rehearsal permitted it because --set wasn't in the mutating flags.
        XCTAssertFalse(PythonBridge.rehearsalAllows(
            ["maxbid", "--set", "--target", "1.50"]))
        XCTAssertTrue(PythonBridge.rehearsalAllows(["maxbid"]))       // read form
        XCTAssertFalse(PythonBridge.rehearsalAllows(["maxbid", "--clear"]))
    }

    func testArchivingIsNeverAllowedInRehearsal() {
        // Rehearsal exists to practice safely. Archiving is the one write
        // Amazon cannot undo, so it must never slip through — with or without
        // the --confirm the engine requires.
        XCTAssertFalse(PythonBridge.rehearsalAllows(["archive-campaign", "--campaign", "1"]))
        XCTAssertFalse(PythonBridge.rehearsalAllows(
            ["archive-campaign", "--campaign", "1", "--confirm"]))
    }

    func testSalesReportImportIsAWriteReadFormIsNot() {
        XCTAssertTrue(PythonBridge.rehearsalAllows(["sales-report"]))
        XCTAssertFalse(PythonBridge.rehearsalAllows(
            ["sales-report", "--import", "/tmp/SALES_REPORT.csv"]))
    }

    func testPureDbReadsAreAllowedInRehearsal() {
        // These were blocked, which killed the Targets / Reports / Halo /
        // Rules-preview workflows in exactly the mode meant for practicing.
        for args in [["alltargets"], ["campaigndaily", "--campaigns", "1"],
                     ["report", "--start", "2026-07-01", "--end", "2026-07-31"],
                     ["harvest-prune"], ["econ-gate"],
                     ["rules-validate"], ["rules-preview"],
                     ["halo"], ["watchlist"]] {
            XCTAssertTrue(PythonBridge.rehearsalAllows(args), "\(args) should be allowed")
        }
    }
}
