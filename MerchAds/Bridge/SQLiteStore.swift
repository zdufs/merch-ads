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

    /// Opens the database read-only. Fails if the file doesn't exist.
    init(path: String) throws {
        var handle: OpaquePointer?
        let uri = "file:\(path)?mode=ro"
        let rc = sqlite3_open_v2(uri, &handle, SQLITE_OPEN_READONLY | SQLITE_OPEN_URI, nil)
        guard rc == SQLITE_OK, let opened = handle else {
            let message = handle.map { String(cString: sqlite3_errmsg($0)) } ?? "sqlite error \(rc)"
            sqlite3_close(handle)
            throw SQLiteError.openFailed(path: path, message: message)
        }
        db = opened
        sqlite3_busy_timeout(db, 2000)
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
