import Foundation

/// A snapshot of an IN-PROGRESS nightly run, parsed from the tail of
/// `outputs/scheduled_runs.log`.
///
/// The nightly writes its machine-readable status (`last_run_status.json`, the
/// source of System Health's "last run" line) only when it FINISHES. So while a
/// run is still going the only live signal is the log it appends to the whole
/// time. This reads that log — nothing here writes, and it never calls the
/// engine, so it is safe to poll even mid-run.
struct NightlyRunProgress: Equatable {
    /// The run header, e.g. "2026-08-14 10:00".
    let label: String
    /// Seconds since the run started, as of when this snapshot was taken.
    let elapsedSeconds: Int?
    /// Markets the run intends to process, from the header's "markets:" list.
    let markets: [String]
    /// Merch markets whose section has begun (a "MARKET X" banner appeared).
    /// KDP runs after the loop with no banner, so it is not counted here.
    let reached: [String]
    /// The market whose section is currently running (the newest banner).
    let currentMarket: String?
    /// Steps that have failed so far this run.
    let failures: [RunStepFailure]
    /// The most recent meaningful log line — what the run is doing right now.
    let lastActivity: String?

    /// Just the clock part of the header, e.g. "10:00".
    var startedClock: String? { label.split(separator: " ").last.map(String.init) }
}

extension NightlyRunProgress {
    /// Build from the engine's `run-status` payload — the primary source, so the
    /// data comes through the same bridge as every other number. Nil when no run
    /// is active.
    init?(_ r: RunStatusResponse) {
        guard r.active else { return nil }
        self.init(label: r.label ?? "", elapsedSeconds: r.elapsedSeconds,
                  markets: r.markets ?? [], reached: r.reached ?? [],
                  currentMarket: r.currentMarket,
                  failures: r.failures ?? [], lastActivity: r.lastActivity)
    }
}

enum NightlyRunMonitor {
    /// A run that has emitted nothing for this long is treated as not running,
    /// so a crashed run (which never wrote its `done:` line) does not read as
    /// "still going" forever.
    static let staleAfter: TimeInterval = 20 * 60

    /// The in-progress run, or nil when the newest run block has already
    /// finished (a `done:` line) or the log has gone quiet past `staleAfter`.
    static func inProgress(engineRoot: String) -> NightlyRunProgress? {
        let path = logURL(engineRoot: engineRoot).path
        let fm = FileManager.default
        guard let attrs = try? fm.attributesOfItem(atPath: path),
              let size = (attrs[.size] as? NSNumber)?.intValue else { return nil }
        if let mtime = attrs[.modificationDate] as? Date,
           Date().timeIntervalSince(mtime) > staleAfter { return nil }
        guard let handle = FileHandle(forReadingAtPath: path) else { return nil }
        defer { try? handle.close() }
        // The last run block is at most a few hundred KB; read a generous tail.
        let tail = 768 * 1024
        let offset = max(0, size - tail)
        if offset > 0 { try? handle.seek(toOffset: UInt64(offset)) }
        guard let data = try? handle.readToEnd() else { return nil }
        return parse(String(decoding: data, as: UTF8.self))
    }

    static func logURL(engineRoot: String) -> URL {
        AppSettings.dataRoot(under: URL(fileURLWithPath: engineRoot))
            .appendingPathComponent("outputs/scheduled_runs.log")
    }

    /// Parse the log text and return the in-progress run, or nil if the newest
    /// block has completed. Split out from the file read so it can be tested.
    static func parse(_ text: String) -> NightlyRunProgress? {
        let lines = text.components(separatedBy: "\n")
        // The newest run header: "==== 2026-08-14 10:00  | markets: US UK ... ===="
        guard let headerIdx = lines.lastIndex(where: { $0.contains("| markets:") }) else { return nil }
        let block = lines[headerIdx...]
        // A "done:" line means that run finished — last_run_status covers it.
        if block.contains(where: { $0.hasPrefix("done:") }) { return nil }

        let (label, markets) = parseHeader(lines[headerIdx])
        var reached: [String] = []
        var failures: [RunStepFailure] = []
        var lastActivity: String?
        for raw in block.dropFirst() {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.isEmpty { continue }
            if line.hasPrefix("#"), line.contains("MARKET ") {
                if let m = tokenAfter("MARKET", in: line) { reached.append(m) }
                continue
            }
            if line.contains("STEP FAILED") {
                if let f = parseFailure(line) { failures.append(f) }
                continue
            }
            if line.allSatisfy({ $0 == "#" || $0 == "=" }) { continue }   // pure decoration
            lastActivity = line
        }
        let elapsed = parseStart(label).map { max(0, Int(Date().timeIntervalSince($0))) }
        return NightlyRunProgress(label: label, elapsedSeconds: elapsed, markets: markets,
                                  reached: reached, currentMarket: reached.last,
                                  failures: failures, lastActivity: lastActivity)
    }

    // MARK: - line parsing

    private static let sepChars = CharacterSet(charactersIn: "= ")

    private static func parseHeader(_ header: String) -> (label: String, markets: [String]) {
        // "================ 2026-08-14 10:00  | markets: US UK DE FR ES IT USKDP ================"
        guard let pipe = header.range(of: "| markets:") else { return ("", []) }
        let label = header[header.startIndex..<pipe.lowerBound]
            .trimmingCharacters(in: sepChars)
        let markets = header[pipe.upperBound...]
            .trimmingCharacters(in: sepChars)
            .split(separator: " ")
            .map(String.init)
        return (label, markets)
    }

    private static func parseStart(_ label: String) -> Date? {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd HH:mm"
        f.calendar = Calendar.autoupdatingCurrent
        f.timeZone = TimeZone.autoupdatingCurrent
        f.locale = Locale(identifier: "en_US_POSIX")
        return f.date(from: label)
    }

    private static func tokenAfter(_ keyword: String, in line: String) -> String? {
        let toks = line.split(whereSeparator: { $0 == " " || $0 == "#" }).map(String.init)
        guard let i = toks.firstIndex(of: keyword), i + 1 < toks.count else { return nil }
        return toks[i + 1]
    }

    private static func parseFailure(_ line: String) -> RunStepFailure? {
        // "*** STEP FAILED [USKDP] lottery_build (exit 1) ***"
        guard let lb = line.firstIndex(of: "["),
              let rb = line.firstIndex(of: "]"), lb < rb else { return nil }
        let market = String(line[line.index(after: lb)..<rb])
        let step = line[line.index(after: rb)...]
            .trimmingCharacters(in: .whitespaces)
            .split(separator: " ").first.map(String.init) ?? "?"
        var exit = 1
        if let er = line.range(of: "(exit "),
           let close = line[er.upperBound...].firstIndex(of: ")") {
            exit = Int(line[er.upperBound..<close]) ?? 1
        }
        return RunStepFailure(market: market, step: step, exit: exit)
    }
}
