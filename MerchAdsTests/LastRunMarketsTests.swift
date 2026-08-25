import XCTest
@testable import Merch_Ads

/// A nightly that runs fewer markets than are configured is not a failed step.
///
/// The loop discovers its market list once, at start-up. When that discovery
/// came back short the run still finished every step it started, so the status
/// file said `ok: true, failures: []` — and System Health said "all steps OK"
/// for five nights while only US was being advertised. The list of markets is
/// the only evidence, so the app compares it against what is configured.
final class LastRunMarketsTests: XCTestCase {

    private func run(_ markets: [String]?) -> LastRunStatus {
        LastRunStatus(started: "2026-08-20T10:00:02",
                      finished: "2026-08-20T10:51:59",
                      ok: true, failures: [], markets: markets)
    }

    private let configured = ["DE", "ES", "FR", "IT", "UK", "US", "USKDP"]

    func testAFullRunSkipsNothing() {
        XCTAssertEqual(run(configured).skippedMarkets(configured: configured), [])
    }

    /// The exact 2026-08-16 → 08-20 incident.
    func testAUSOnlyRunNamesTheSixSkippedMarkets() {
        XCTAssertEqual(run(["US"]).skippedMarkets(configured: configured),
                       ["DE", "ES", "FR", "IT", "UK", "USKDP"])
    }

    /// Order follows the health table, not the status file, so the banner reads
    /// in the same order as the rows underneath it.
    func testSkippedMarketsKeepTheHealthTableOrder() {
        XCTAssertEqual(run(["USKDP", "US"]).skippedMarkets(configured: configured),
                       ["DE", "ES", "FR", "IT", "UK"])
    }

    /// An engine too old to report the list must not paint an amber banner every
    /// morning — a warning nobody can act on is a warning the operator learns to
    /// ignore, and this one has to still work the night it matters.
    func testAMissingMarketListIsNotAWarning() {
        XCTAssertEqual(run(nil).skippedMarkets(configured: configured), [])
        XCTAssertEqual(run([]).skippedMarkets(configured: configured), [])
    }

    /// A market that ran but is no longer configured is not a skip.
    func testRunningMoreThanIsConfiguredIsNotASkip() {
        XCTAssertEqual(run(configured + ["JP"]).skippedMarkets(configured: configured), [])
    }

    /// The field has to survive the real status file's shape.
    func testDecodesTheStatusFileTheNightlyWrites() throws {
        let json = """
        {"started":"2026-08-20T10:00:02","finished":"2026-08-20T10:51:59",
         "markets":["US"],"ok":true,"failures":[]}
        """
        let decoded = try PythonBridge.makeDecoder().decode(LastRunStatus.self, from: Data(json.utf8))
        XCTAssertEqual(decoded.markets, ["US"])
        XCTAssertTrue(decoded.ok)
    }

    /// Older status files have no `markets` key at all. Decoding must not throw.
    func testDecodesAStatusFileWrittenBeforeTheKeyExisted() throws {
        let json = #"{"started":null,"finished":"2026-08-01T10:40:00","ok":true,"failures":[]}"#
        let decoded = try PythonBridge.makeDecoder().decode(LastRunStatus.self, from: Data(json.utf8))
        XCTAssertNil(decoded.markets)
    }
}
