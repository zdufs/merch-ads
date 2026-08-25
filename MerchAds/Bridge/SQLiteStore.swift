import Foundation
import SQLite3

// Read-only SQLite access to the per-market databases. The nightly launchd job
// owns all writes — this layer opens with SQLITE_OPEN_READONLY so we can never
// fight it for locks (plus a busy timeout for moments it is mid-write).

private let SQLITE_TRANSIENT = unsafeBitCast(-1, to: sqlite3_destructor_type.self)

enum SQLiteError: LocalizedError {
    case openFailed(path: String, message: String)
    case queryFailed(sql: String, message: String)

    var errorDescription: String? {
        switch self {
        case .openFailed(let path, let message):
            return "Could not open \(path): \(message)"
        case .queryFailed(let sql, let message):
            return "Query failed (\(message)): \(sql.prefix(120))"
        }
    }
}

enum SQLiteValue {
    case null
    case integer(Int64)
    case real(Double)
    case text(String)
    case blob(Data)

    var intValue: Int64? {
        switch self {
        case .integer(let v): v
        case .real(let v): Int64(v)
        case .text(let v): Int64(v)
        default: nil
        }
    }

    var doubleValue: Double? {
        switch self {
        case .integer(let v): Double(v)
        case .real(let v): v
        case .text(let v): Double(v)
        default: nil
        }
    }

    var stringValue: String? {
        switch self {
        case .integer(let v): String(v)
        case .real(let v): String(v)
        case .text(let v): v
        default: nil
        }
    }
}

final class SQLiteStore {
    private let db: OpaquePointer

    /// Opens the database for reading. Fails if the file doesn't exist.
    ///
    /// Read-only is the rule, but it is not always POSSIBLE. A WAL database
    /// needs a `-shm` shared-memory index, and a read-only connection may not
    /// create one. SQLite deletes `-wal` and `-shm` when the last connection
    /// closes, so a market database sits sidecar-less most of the day — and
    /// this app links Apple's SQLite (3.51.0), which cannot read that state at
    /// all. Every direct read failed: "DB direct" showed "—" for every market
    /// and the sidebar footer said "no local data". The engine never saw it,
    /// because its Homebrew python links a newer SQLite that copes.
    ///
    /// So: try read-only, and if it cannot READ, reopen read-write — which may
    /// create the `-shm` — and immediately set `query_only`. After that SQLite
    /// itself refuses every write on this handle (SQLITE_READONLY), so the
    /// promise is kept by the engine rather than by our good intentions.
    /// Neither path passes SQLITE_OPEN_CREATE, so neither can create a file.
    init(path: String) throws {
        var handle: OpaquePointer?
        var rc = sqlite3_open_v2("file:\(path)?mode=ro", &handle,
                                 SQLITE_OPEN_READONLY | SQLITE_OPEN_URI, nil)
        // The open SUCCEEDS even when the -shm is missing; only the first query
        // fails. So probe with a real read before trusting this handle.
        if rc == SQLITE_OK, let opened = handle, !SQLiteStore.canRead(opened) {
            sqlite3_close(opened)
            handle = nil
            rc = sqlite3_open_v2("file:\(path)?mode=rw", &handle,
                                 SQLITE_OPEN_READWRITE | SQLITE_OPEN_URI, nil)
            if rc == SQLITE_OK, let reopened = handle {
                sqlite3_exec(reopened, "PRAGMA query_only = 1", nil, nil, nil)
            }
        }
        guard rc == SQLITE_OK, let opened = handle else {
            let message = handle.map { String(cString: sqlite3_errmsg($0)) } ?? "sqlite error \(rc)"
            sqlite3_close(handle)
            throw SQLiteError.openFailed(path: path, message: message)
        }
        db = opened
        sqlite3_busy_timeout(db, 2000)
    }

    /// True when this handle can actually read the schema — the cheapest query
    /// that forces SQLite to open the WAL index.
    private static func canRead(_ handle: OpaquePointer) -> Bool {
        var statement: OpaquePointer?
        defer { sqlite3_finalize(statement) }
        guard sqlite3_prepare_v2(handle, "SELECT COUNT(*) FROM sqlite_master",
                                 -1, &statement, nil) == SQLITE_OK else { return false }
        return sqlite3_step(statement) == SQLITE_ROW
    }

    deinit {
        sqlite3_close(db)
    }

    func rows(_ sql: String, bind: [SQLiteValue] = []) throws -> [[String: SQLiteValue]] {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &statement, nil) == SQLITE_OK, let statement else {
            throw SQLiteError.queryFailed(sql: sql, message: String(cString: sqlite3_errmsg(db)))
        }
        defer { sqlite3_finalize(statement) }

        for (index, value) in bind.enumerated() {
            let slot = Int32(index + 1)
            switch value {
            case .null: sqlite3_bind_null(statement, slot)
            case .integer(let v): sqlite3_bind_int64(statement, slot, v)
            case .real(let v): sqlite3_bind_double(statement, slot, v)
            case .text(let v): sqlite3_bind_text(statement, slot, v, -1, SQLITE_TRANSIENT)
            case .blob(let v):
                v.withUnsafeBytes { bytes in
                    _ = sqlite3_bind_blob(statement, slot, bytes.baseAddress, Int32(bytes.count), SQLITE_TRANSIENT)
                }
            }
        }

        let columnCount = sqlite3_column_count(statement)
        let names = (0..<columnCount).map { String(cString: sqlite3_column_name(statement, $0)) }

        var result: [[String: SQLiteValue]] = []
        while true {
            if Task.isCancelled { throw CancellationError() }
            let rc = sqlite3_step(statement)
            if rc == SQLITE_DONE { break }
            guard rc == SQLITE_ROW else {
                throw SQLiteError.queryFailed(sql: sql, message: String(cString: sqlite3_errmsg(db)))
            }
            var row: [String: SQLiteValue] = [:]
            for column in 0..<columnCount {
                let value: SQLiteValue
                switch sqlite3_column_type(statement, column) {
                case SQLITE_INTEGER: value = .integer(sqlite3_column_int64(statement, column))
                case SQLITE_FLOAT: value = .real(sqlite3_column_double(statement, column))
                case SQLITE_TEXT: value = .text(String(cString: sqlite3_column_text(statement, column)))
                case SQLITE_BLOB:
                    if let base = sqlite3_column_blob(statement, column) {
                        value = .blob(Data(bytes: base, count: Int(sqlite3_column_bytes(statement, column))))
                    } else {
                        value = .blob(Data())
                    }
                default: value = .null
                }
                row[names[Int(column)]] = value
            }
            result.append(row)
        }
        return result
    }

    func scalarInt(_ sql: String, bind: [SQLiteValue] = []) throws -> Int64? {
        try rows(sql, bind: bind).first?.values.first?.intValue
    }

    func scalarString(_ sql: String, bind: [SQLiteValue] = []) throws -> String? {
        try rows(sql, bind: bind).first?.values.first?.stringValue
    }
}
