import Foundation
import os

// The one place Swift talks to the Python engine. Never touches .env, never
// writes the DB — it shells out to appctl.py, which owns all safety rails
// (KILL file, preview, writes_log).

enum BridgeError: LocalizedError {
    case engineFolderMissing(String)
    case pythonNotFound
    case engineError(String)                                   // {"ok": false, "error": ...}
    case badOutput(exitCode: Int32, stdout: String, stderr: String)
    case rehearsalDenied(command: String)

    var errorDescription: String? {
        switch self {
        case .engineFolderMissing(let path):
            return "appctl.py not found in \(path). Set the engine folder in Settings (⌘,)."
        case .pythonNotFound:
            return "python3 not found on the login-shell PATH. Set a Python path in Settings (⌘,)."
        case .engineError(let message):
            return "Engine: \(message)"
        case .badOutput(let code, let stdout, let stderr):
            let detail = stderr.isEmpty ? String(stdout.prefix(300)) : String(stderr.suffix(300))
            if code == 0 && !stdout.isEmpty {
                return "appctl replied, but the app couldn't decode it (contract mismatch?): \(detail)"
            }
            return "appctl exited with code \(code): \(detail)"
        case .rehearsalDenied(let command):
            return "Rehearsal mode blocked the mutating appctl command '\(command)'."
        }
    }
}

struct PythonBridge {
    let engineRoot: URL
    let pythonPath: String

    init(engineRoot: URL, pythonOverride: String? = nil) throws {
        let appctl = engineRoot.appendingPathComponent("appctl.py")
        guard FileManager.default.fileExists(atPath: appctl.path) else {
            throw BridgeError.engineFolderMissing(engineRoot.path)
        }
        self.engineRoot = engineRoot
        if let override = pythonOverride, !override.isEmpty {
            self.pythonPath = override
        } else if let resolved = Self.loginShellPython {
            self.pythonPath = resolved
        } else {
            throw BridgeError.pythonNotFound
        }
    }

    /// The same python3 the launchd job gets: resolved through the login shell
    /// PATH (mirrors run_scheduled.sh), so live/action calls have `requests`.
    static let loginShellPython: String? = {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/zsh")
        process.arguments = ["-lc", "command -v python3"]
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = Pipe()
        if (try? process.run()) != nil {
            let data = (try? pipe.fileHandleForReading.readToEnd()) ?? Data()
            process.waitUntilExit()
            let out = String(decoding: data, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
            if process.terminationStatus == 0, !out.isEmpty {
                return out
            }
        }
        return ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"]
            .first { FileManager.default.isExecutableFile(atPath: $0) }
    }()

    /// Local DB reads that are safe + quick on the persistent serve worker.
    /// Writes, live Amazon calls and long jobs always get a fresh process.
    /// Commands with a write FORM (maxbid --set, sales-report --import) are
    /// rejected argument-wise by `mutatingFlags` before this list matters.
    private static let fastCommands: Set<String> = [
        "markets", "metrics", "monthly", "periods", "crosspurchase", "sales-history", "campaigns", "adgroups", "targets",
        "searchterms", "asin", "bidhistory", "history", "negatives", "daily",
        "killlist", "health", "overview", "digest", "econ-gate",
        "bidreport", "harvest", "harvest-prune", "stale", "alerts", "nudges", "audit", "profit",
        "demandfeed", "maxbid", "alltargets", "campaigndaily", "report",
        "accumulated-asins", "accumulated-keywords", "negatives-preview",
        "seasons", "seasonal-preview", "sales-report",
        "rules-list", "rules-get", "rules-pending", "synccal", "run-status",
    ]

    /// Flags that turn a mixed read/write command into its write form. Shared
    /// by rehearsal (deny) and the fast bridge (one-shot, never the worker).
    private static let mutatingFlags: Set<String> = [
        "--apply", "--on", "--off", "--refresh", "--clear", "--set", "--import",
    ]

    static var isRehearsal: Bool {
        ProcessInfo.processInfo.arguments.contains("-rehearsal")
    }

    /// Defense in depth for rehearsal mode. This check runs before worker
    /// selection, stdin handling, or process creation, so no call path can
    /// bypass it. Commands that mix read and write forms are argument-aware.
    static func rehearsalAllows(_ args: [String]) -> Bool {
        guard let command = args.first else { return false }
        if !mutatingFlags.isDisjoint(with: args) { return false }
        if fastCommands.contains(command) { return true }
        let readOnlyOneShots: Set<String> = [
            "status", "livestate", "import-preview", "season-suggest",
            "season-tag-csv", "halo", "watchlist",
            "rules-validate", "rules-preview", "rules-run",
        ]
        return readOnlyOneShots.contains(command)
    }

    private func isFast(_ args: [String]) -> Bool {
        guard UserDefaults.standard.object(forKey: AppSettings.fastBridgeKey) == nil
                || UserDefaults.standard.bool(forKey: AppSettings.fastBridgeKey) else {
            return false   // user turned the fast bridge off in Settings
        }
        guard let command = args.first, Self.fastCommands.contains(command) else { return false }
        return Self.mutatingFlags.isDisjoint(with: args) && !args.contains("--live")
    }

    /// How long a one-shot spawn may run before the watchdog kills it. The
    /// generous tiers cover appctl's own internal subprocess timeouts.
    private static func timeout(for args: [String]) -> TimeInterval {
        switch args.first {
        case "run", "import-apply": 3900
        case "backfill-daily": 3100
        // adopt-export pages the full /sp/productAds/list and scans the ~2GB
        // Merch export; its internal map_products.py timeout is 900s, so the
        // watchdog sits above that (+ file-move headroom) to let Python's own
        // timeout surface a clean error before the blunt SIGTERM.
        case "adopt-export": 1200
        case "promote", "negatives-apply", "import-preview",
             "demandfeed", "status", "livestate": 900
        default: 120
        }
    }

    /// Run `ADS_MARKET=<market> python3 appctl.py <args…>` and decode the
    /// `{"ok": …}` envelope into the expected payload type. `stdin` feeds
    /// commands that read an approved plan (negatives-apply, import-apply).
    /// Background callers (alerts, digests) pass `preferWorker: false` so
    /// they don't spawn a persistent worker for every market at launch.
    func call<T: Decodable>(_ type: T.Type, _ args: [String], market: String? = nil,
                            stdin: Data? = nil, preferWorker: Bool = true) async throws -> T {
        if Self.isRehearsal, !Self.rehearsalAllows(args) {
            throw BridgeError.rehearsalDenied(command: args.first ?? "<empty>")
        }
        do {
            if stdin == nil, preferWorker, isFast(args),
               let data = try? await PythonWorkerPool.shared.call(python: pythonPath,
                                                                  root: engineRoot,
                                                                  args: args, market: market) {
                // a cancelled screen task (market switch) must not decode + publish
                // a stale response over the new market's data
                try Task.checkCancellation()
                return try decode(type, stdout: data, stderr: Data(), exitCode: 0)
            }
            // The worker attempt above swallows CancellationError along with real
            // failures, so re-check here: a market-switch stampede must not fall
            // through and spawn a full interpreter per cancelled screen.
            try Task.checkCancellation()
            // one-shot spawn: writes, live calls, stdin plans — or worker fallback
            let (stdout, stderr, exitCode) = try await execute(args, market: market, stdin: stdin,
                                                               timeout: Self.timeout(for: args))
            try Task.checkCancellation()
            return try decode(type, stdout: stdout, stderr: stderr, exitCode: exitCode)
        } catch let error as BridgeError {
            // Central capture: every engine/output failure — including ones a
            // caller swallows with `try?` — lands in the Errors tab. Rehearsal
            // denials are filtered inside record(); cancellations (a market
            // switch) are CancellationError, not BridgeError, so they pass through.
            IssueCenter.record(command: args.first, market: market, error: error)
            throw error
        }
    }

    private func decode<T: Decodable>(_ type: T.Type, stdout: Data, stderr: Data,
                                      exitCode: Int32) throws -> T {

        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        if let envelope = try? decoder.decode(Envelope<T>.self, from: stdout) {
            if envelope.ok, let data = envelope.data { return data }
            throw BridgeError.engineError(envelope.error ?? "unknown engine error")
        }

        // Tolerate stray prints around the JSON object. The contract is exactly
        // one envelope on stdout, so scan whole lines newest-first: starting at
        // the first '{' instead would be defeated by a diagnostic print that
        // itself contains a brace (a Python dict repr).
        let lines = stdout.split(separator: UInt8(ascii: "\n"), omittingEmptySubsequences: true)
        for line in lines.reversed() {
            guard line.contains(UInt8(ascii: "{")),
                  let envelope = try? decoder.decode(Envelope<T>.self, from: Data(line)) else { continue }
            if envelope.ok, let data = envelope.data { return data }
            throw BridgeError.engineError(envelope.error ?? "unknown engine error")
        }

        throw BridgeError.badOutput(exitCode: exitCode,
                                    stdout: String(decoding: stdout, as: UTF8.self),
                                    stderr: String(decoding: stderr, as: UTF8.self))
    }

    private func execute(_ args: [String], market: String?,
                         stdin: Data? = nil,
                         timeout: TimeInterval = 120) async throws -> (Data, Data, Int32) {
        let appctlPath = engineRoot.appendingPathComponent("appctl.py").path
        let root = engineRoot
        let python = pythonPath
        return try await withCheckedThrowingContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                let process = Process()
                process.executableURL = URL(fileURLWithPath: python)
                process.arguments = [appctlPath] + args
                process.currentDirectoryURL = root
                var environment = ProcessInfo.processInfo.environment
                environment["PYTHONUNBUFFERED"] = "1"
                if let market {
                    environment["ADS_MARKET"] = market
                } else {
                    environment.removeValue(forKey: "ADS_MARKET")   // cross-market cmds (health)
                }
                process.environment = environment

                let outPipe = Pipe()
                let errPipe = Pipe()
                process.standardOutput = outPipe
                process.standardError = errPipe
                let inPipe = Pipe()
                process.standardInput = inPipe   // never a TTY, so stdin-reading cmds see EOF

                do {
                    try process.run()
                } catch {
                    continuation.resume(throwing: error)
                    return
                }
                // watchdog: a hung call gets terminated (which also unblocks the
                // pipe reads and the stdin write below), instead of spinning a
                // view forever. Armed before any blocking I/O so nothing here
                // can outlive the timeout.
                // A real lock, not an @unchecked Sendable flag: the timer queue
                // writes and the worker thread reads with no other ordering.
                let watchdogFired = OSAllocatedUnfairLock(initialState: false)
                DispatchQueue.global().asyncAfter(deadline: .now() + timeout) {
                    if process.isRunning {
                        watchdogFired.withLock { $0 = true }
                        process.terminate()
                    }
                }

                // Drain both pipes concurrently so neither can fill and deadlock.
                var stdout = Data()
                var stderr = Data()
                let group = DispatchGroup()
                group.enter()
                DispatchQueue.global(qos: .userInitiated).async {
                    stdout = (try? outPipe.fileHandleForReading.readToEnd()) ?? Data()
                    group.leave()
                }
                group.enter()
                DispatchQueue.global(qos: .userInitiated).async {
                    stderr = (try? errPipe.fileHandleForReading.readToEnd()) ?? Data()
                    group.leave()
                }

                // Feed stdin off this thread and after the drains are running: an
                // approved plan larger than the pipe buffer blocks until the child
                // reads it, and a child that exits first (KILL file, econ gate,
                // argparse error) closes the pipe mid-write. Both are normal, so
                // the write must neither stall the drains nor raise — the throwing
                // write(contentsOf:) reports EPIPE instead of trapping.
                group.enter()
                DispatchQueue.global(qos: .userInitiated).async {
                    let handle = inPipe.fileHandleForWriting
                    if let stdin { try? handle.write(contentsOf: stdin) }
                    try? handle.close()
                    group.leave()
                }

                group.wait()
                process.waitUntilExit()
                if watchdogFired.withLock({ $0 }) {
                    continuation.resume(throwing: BridgeError.badOutput(
                        exitCode: process.terminationStatus, stdout: "",
                        stderr: "timed out after \(Int(timeout))s — the call was terminated"))
                    return
                }
                continuation.resume(returning: (stdout, stderr, process.terminationStatus))
            }
        }
    }
}
