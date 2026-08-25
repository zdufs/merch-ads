import XCTest
@testable import Merch_Ads

/// A refused write must not read as a quiet success.
///
/// Both screens below printed a number that is true of two completely different
/// outcomes: "Paused 0 keywords." in the success colour over 40 keywords Amazon
/// rejected, and "Applied 0 change(s)" for a run the economics gate blocked
/// entirely. Found by the 2026-08-24 review.
final class RefusedWriteReportingTests: XCTestCase {

    /// The app's own decoder. A fixture read with a locally built JSONDecoder
    /// keeps passing after the bridge stops converting snake_case.
    private func decoder() -> JSONDecoder { PythonBridge.makeDecoder() }

    // MARK: - harvest-prune-apply: refused is not "nothing to do"

    func testABatchAmazonRefusedEntirelyDoesNotReadAsAPauseThatWorked() throws {
        let json = """
        {"market":"US","requested":40,"paused":0,"failed":40,"unconfirmed":0}
        """.data(using: .utf8)!
        let got = try decoder().decode(HarvestPruneApplyResponse.self, from: json)
        let note = try XCTUnwrap(got.shortfallNote,
                                 "40 approved keywords, none paused — that cannot be silent")
        XCTAssertTrue(note.contains("refused 40"), note)
        XCTAssertTrue(note.contains("0 of 40"), note)
    }

    func testAnUnreachableAmazonIsUnconfirmedNotRefused() throws {
        let json = """
        {"market":"US","requested":12,"paused":0,"failed":0,"unconfirmed":12}
        """.data(using: .utf8)!
        let got = try decoder().decode(HarvestPruneApplyResponse.self, from: json)
        let note = try XCTUnwrap(got.shortfallNote)
        XCTAssertTrue(note.contains("12 unconfirmed"), note)
        XCTAssertFalse(note.contains("refused"),
                       "nobody knows what Amazon did with these: \(note)")
    }

    func testEveryRequestedPauseConfirmedStaysASuccess() throws {
        let json = """
        {"market":"US","requested":7,"paused":7,"failed":0,"unconfirmed":0}
        """.data(using: .utf8)!
        let got = try decoder().decode(HarvestPruneApplyResponse.self, from: json)
        XCTAssertNil(got.shortfallNote)
    }

    /// An engine that predates the counts sends `paused` alone. The screen goes
    /// back to what it said before rather than inventing a shortfall.
    func testAnOlderEngineReplyRaisesNothing() throws {
        let json = #"{"market":"US","paused":3}"#.data(using: .utf8)!
        let got = try decoder().decode(HarvestPruneApplyResponse.self, from: json)
        XCTAssertNil(got.shortfallNote)
        XCTAssertEqual(got.paused, 3)
    }

    // MARK: - rules-run --apply: blocked is not "matched nothing"

    func testARunWhoseEveryChangeWasBlockedDoesNotReadLikeARuleThatMatchedNothing() throws {
        let json = """
        {"market":"US","evaluated":900,"matched":3,"applied":true,"count":0,
         "econ_gate_ok":false,
         "results":[
           {"entity_kind":"target","entity_id":"t1","label":"one","action":"setBid",
            "status":"blocked_econ_gate","reasons":["export is 34 days old"]},
           {"entity_kind":"target","entity_id":"t2","label":"two","action":"setBid",
            "status":"blocked_econ_gate","reasons":["export is 34 days old"]},
           {"entity_kind":"target","entity_id":"t3","label":"three","action":"pause",
            "status":"failed","http":[422]}]}
        """.data(using: .utf8)!
        let got = try decoder().decode(RulesApproveResponse.self, from: json)
        let summary = got.runSummary
        XCTAssertNotEqual(summary, "Applied 0 change(s).")
        XCTAssertTrue(summary.contains("0 of 3"), summary)
        XCTAssertTrue(summary.contains("blocked econ gate"), summary)
        XCTAssertTrue(summary.contains("HTTP 422"), summary)
    }

    func testARuleThatMatchedNothingSaysSo() throws {
        let json = """
        {"market":"US","evaluated":900,"matched":0,"applied":true,"count":0,
         "econ_gate_ok":true,"results":[]}
        """.data(using: .utf8)!
        let got = try decoder().decode(RulesApproveResponse.self, from: json)
        XCTAssertEqual(got.runSummary, "No rows matched — nothing to apply.")
    }

    func testACleanRunStillReadsAsApplied() throws {
        let json = """
        {"market":"US","evaluated":900,"matched":2,"applied":true,"count":2,
         "econ_gate_ok":true,
         "results":[
           {"entity_kind":"target","entity_id":"t1","label":"one","action":"setBid",
            "status":"applied"},
           {"entity_kind":"target","entity_id":"t2","label":"two","action":"setBid",
            "status":"applied"}]}
        """.data(using: .utf8)!
        let got = try decoder().decode(RulesApproveResponse.self, from: json)
        XCTAssertEqual(got.runSummary, "Applied 2 change(s).")
    }

    /// The volume cap and the KILL freeze refuse the whole run before anything
    /// is judged, and the engine explains why in `message`. Dropping that left
    /// the operator with the word "change_volume" and no number.
    func testAWholeRunRefusedRepeatsTheEnginesReason() throws {
        let json = """
        {"applied":false,"blocked":"change_volume","count":10422,"cap":500,
         "results":[],
         "message":"10422 writes proposed in US, over the 500-change limit."}
        """.data(using: .utf8)!
        let got = try decoder().decode(RulesApproveResponse.self, from: json)
        let summary = got.runSummary
        XCTAssertTrue(summary.contains("change_volume"), summary)
        XCTAssertTrue(summary.contains("nothing was applied"), summary)
        XCTAssertTrue(summary.contains("500-change limit"), summary)
    }
}
