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
    /// Where `appctl.py` and its modules live — the bundled copy when the app
    /// ships one, otherwise the folder from Settings.
    let engineRoot: URL

    /// Where the databases, `.env` and the operator config live. Always a real
    /// folder on disk, never inside the bundle: replacing the app must not be
    /// able to touch a row of banked history.
    let dataRoot: URL

    let pythonPath: String

    /// Where `appctl.py` lives under a chosen engine folder.
    ///
    /// The modules moved into `engine/`, so that is checked first. The flat
    /// layout is still accepted: an existing Settings path keeps working, and so
    /// does pointing Settings straight at the `engine` folder itself. Returns nil
    /// when neither exists, which is what produces the "set the engine folder"
    /// error rather than a confusing failure later.
    static func appctlURL(under root: URL) -> URL? {
        for candidate in [root.appendingPathComponent("engine/appctl.py"),
                          root.appendingPathComponent("appctl.py")] {
            if FileManager.default.fileExists(atPath: candidate.path) { return candidate }
        }
        return nil
    }

    /// - Parameter engineRoot: the folder from Settings. It is the DATA folder;
    ///   whether it also holds the engine only matters when the app ships none.
    init(engineRoot: URL, pythonOverride: String? = nil) throws {
        // Bundled engine first. A checkout that has moved on — or been deleted —
        // must not be able to change what the installed app runs.
        if let bundled = AppSettings.bundledEngineRoot {
            self.engineRoot = bundled
        } else if Self.appctlURL(under: engineRoot) != nil {
            self.engineRoot = engineRoot
        } else {
            throw BridgeError.engineFolderMissing(engineRoot.path)
        }
        self.dataRoot = AppSettings.dataRoot(under: engineRoot)

        // An explicit Settings path always wins — it is the operator's escape
        // hatch. Then the bundled interpreter, then the login shell.
        if let override = pythonOverride, !override.isEmpty {
            self.pythonPath = override
        } else if let bundled = AppSettings.bundledPython {
            self.pythonPath = bundled.path
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

    /// The environment EVERY appctl process gets — persistent worker or
    /// one-shot spawn.
    ///
    /// Built in one place on purpose. The two paths each assembled their own,
    /// and the moment code and data stopped being the same folder they drifted:
    /// the one-shot spawn carried MERCHADS_DATA_DIR and the worker did not, so
    /// the worker resolved the data folder from its own __file__ — which inside
    /// the bundle is Contents/Resources. That folder exists and is readable, so
    /// nothing failed. Every screen fed by a worker just answered "unavailable"
    /// while the same command on the command line answered correctly.
    static func engineEnvironment(dataRoot: URL, market: String?) -> [String: String] {
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONUNBUFFERED"] = "1"
        // Never let the bundled interpreter write into the app bundle.
        //
        // `cp -R` does not preserve mtimes, so after an install every engine
        // .py file looks NEWER than the .pyc shipped beside it. CPython then
        // rewrites those .pyc — inside `Contents/Resources`, which is sealed by
        // the code signature. The app's own signature is invalid from the first
        // command it runs, and `codesign --verify --deep --strict` fails on the
        // installed copy. That is what `package_app.sh --install` reported on
        // 2026-08-24, twice, and it is the app doing it to itself.
        //
        // A signed bundle is read-only by definition. Bytecode caching buys a
        // few milliseconds on a process that already pays for a python launch.
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["MERCHADS_DATA_DIR"] = dataRoot.path
        environment["MERCHADS_POD_DIR"] = dataRoot.deletingLastPathComponent().path
        if let market {
            environment["ADS_MARKET"] = market
        } else {
            environment.removeValue(forKey: "ADS_MARKET")   // cross-market cmds (health)
        }
        return environment
    }

    /// Local DB reads that are safe + quick on the persistent serve worker.
    /// Writes, live Amazon calls and long jobs always get a fresh process.
    /// Commands with a write FORM (maxbid --set, sales-report --import) are
    /// rejected argument-wise by `mutatingFlags` before this list matters.
    private static let fastCommands: Set<String> = [
        "markets", "metrics", "monthly", "periods", "crosspurchase", "sales-history", "campaigns", "adgroups", "targets",
        "searchterms", "asin", "bidhistory", "history", "negatives", "daily",
        "killlist", "health", "overview", "digest", "econ-gate",
        "bidreport", "harvest", "harvest-prune", "stale", "alerts", "audit", "profit",
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
                                                                  engineRoot: engineRoot,
                                                                  dataRoot: dataRoot,
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

    /// The decoder every engine reply is read with.
    ///
    /// Not private, and not built inline, so a test can read a captured reply
    /// with the SAME configuration the app uses. A test that constructs its own
    /// JSONDecoder proves nothing about this one: on 2026-08-24 a mutation
    /// switched `convertFromSnakeCase` off here and all 259 Swift tests still
    /// passed, because the only ones that decode anything had each built a
    /// decoder of their own a few lines above the assertion.
    static func makeDecoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }

    private func decode<T: Decodable>(_ type: T.Type, stdout: Data, stderr: Data,
                                      exitCode: Int32) throws -> T {

        let decoder = Self.makeDecoder()
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
        let appctlPath = (Self.appctlURL(under: engineRoot) ?? engineRoot.appendingPathComponent("engine/appctl.py")).path
        let root = dataRoot
        let python = pythonPath
        return try await withCheckedThrowingContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                let process = Process()
                process.executableURL = URL(fileURLWithPath: python)
                process.arguments = [appctlPath] + args
                process.currentDirectoryURL = root
                process.environment = Self.engineEnvironment(dataRoot: root, market: market)

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
                //
                // Locked boxes, not plain `var`s. group.wait() below does order
                // these writes against the read that follows, but the compiler
                // cannot see that and warned on every build. This is the buffer
                // carrying appctl's JSON reply, so an unchecked capture here
                // would be one refactor away from a real race on the thing every
                // screen decodes. Same lock the watchdog above uses.
                //
                // readToEnd() blocks, so it runs OUTSIDE the lock — only the
                // handoff is guarded.
                let stdoutBox = OSAllocatedUnfairLock(initialState: Data())
                let stderrBox = OSAllocatedUnfairLock(initialState: Data())
                let group = DispatchGroup()
                group.enter()
                DispatchQueue.global(qos: .userInitiated).async {
                    let data = (try? outPipe.fileHandleForReading.readToEnd()) ?? Data()
                    stdoutBox.withLock { $0 = data }
                    group.leave()
                }
                group.enter()
                DispatchQueue.global(qos: .userInitiated).async {
                    let data = (try? errPipe.fileHandleForReading.readToEnd()) ?? Data()
                    stderrBox.withLock { $0 = data }
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
                continuation.resume(returning: (stdoutBox.withLock { $0 },
                                                stderrBox.withLock { $0 },
                                                process.terminationStatus))
            }
        }
    }
}
