import XCTest
import SQLite3
@testable import Merch_Ads

/// A market database between nightly runs has no `-wal` or `-shm` sidecars —
/// SQLite removes them when the last connection closes. Apple's SQLite, which
/// this app links, cannot open that state read-only: the open succeeds and the
/// first query returns SQLITE_CANTOPEN.
///
/// That is not an edge case. It is why "DB direct" read "—" for every market
/// and the sidebar footer said "no local data". These tests build exactly that
/// state and pin both halves of the fix: the store must read it, and it must
/// still be unable to write.
final class SQLiteStoreWALTests: XCTestCase {

    private var folder: URL!
    private var path: String!

    override func setUpWithError() throws {
        folder = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("walstore-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        path = folder.appendingPathComponent("market.sqlite").path

        var handle: OpaquePointer?
        XCTAssertEqual(sqlite3_open(path, &handle), SQLITE_OK)
        let writer = try XCTUnwrap(handle)
        exec(writer, "PRAGMA journal_mode=WAL")
        exec(writer, "CREATE TABLE campaigns(campaign_id TEXT, state TEXT)")
        exec(writer, "INSERT INTO campaigns VALUES ('c1','ENABLED')")
        exec(writer, "PRAGMA wal_checkpoint(TRUNCATE)")
        sqlite3_close(writer)

        // Whatever SQLite left behind, the state under test is "no sidecars".
        for suffix in ["-wal", "-shm"] {
            let sidecar = path + suffix
            if FileManager.default.fileExists(atPath: sidecar) {
                try FileManager.default.removeItem(atPath: sidecar)
            }
        }
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: folder)
    }

    private func exec(_ handle: OpaquePointer, _ sql: String) {
        XCTAssertEqual(sqlite3_exec(handle, sql, nil, nil, nil), SQLITE_OK, sql)
    }

    func testItReadsAWalDatabaseWithNoSidecars() throws {
        let store = try SQLiteStore(path: path)
        XCTAssertEqual(try store.scalarInt("SELECT COUNT(*) FROM campaigns"), 1)
    }

    func testTheHandleStillCannotWrite() throws {
        let store = try SQLiteStore(path: path)
        XCTAssertThrowsError(try store.rows("INSERT INTO campaigns VALUES ('c2','ENABLED')"))
        XCTAssertEqual(try store.scalarInt("SELECT COUNT(*) FROM campaigns"), 1)
    }

    func testAMissingDatabaseIsAnErrorAndIsNotCreated() {
        let missing = folder.appendingPathComponent("no_such_market.sqlite").path
        XCTAssertThrowsError(try SQLiteStore(path: missing))
        XCTAssertFalse(FileManager.default.fileExists(atPath: missing))
    }
}
