import Foundation

// The fast bridge: one long-running `appctl.py serve` process per market
// (db.py binds its DB file at import, so serve can't switch markets). Requests
// are one JSON argv array per line in, one envelope per line out.
//
// CORRECTNESS NOTE: the serve protocol has no request ids, so the ONLY thing
// that maps a response to its request is strict write→read pairing. That
// pairing is enforced structurally here: Worker is an actor holding a request
// queue that a SINGLE drain loop consumes one item at a time. Callers can pile
// in concurrently (e.g. every screen reloading at once after a nightly run) —
// they enqueue and wait; nothing ever reads the pipe out of turn. A previous
// design chained unstructured Tasks and could cross-wire replies under exactly
// that stampede.

actor PythonWorkerPool {
    static let shared = PythonWorkerPool()

    /// Serve processes per market. One pipe = strict FIFO, so a screen fan-out
    /// (the Dashboard fires 7 reads at once on a market switch) used to pay
    /// the SUM of all its reads. A few workers let those run in parallel while
    /// each Worker still enforces the write→read pairing on its own pipe.
    private static let maxWorkersPerMarket = 3

    private struct Config: Equatable {
        let python: String
        let root: String
    }

    private var config: Config?
    private var workers: [String: [Worker]] = [:]   // key = market code ("" = none)

    func call(python: String, root: URL, args: [String], market: String?) async throws -> Data {
        let current = Config(python: python, root: root.path)
        if config != current {
            for worker in workers.values.flatMap({ $0 }) { await worker.shutdown() }
            workers = [:]
            config = current
        }
        let key = market ?? ""
        let worker = try await pick(key: key, python: python, root: root, market: market)
        do {
            return try await worker.request(args)
        } catch {
            await worker.shutdown()
            workers[key] = (workers[key] ?? []).filter { $0 !== worker }
            throw error   // caller falls back to a one-shot spawn
        }
    }

    /// An idle worker if one exists, else grow the pool up to the cap, else
    /// the least-loaded. The `await depth` suspensions mean two callers can
    /// race past each other — the cap is re-checked at insert, so the pool
    /// never exceeds maxWorkersPerMarket; at worst a caller queues on a
    /// near-idle worker instead of the emptiest.
    private func pick(key: String, python: String, root: URL,
                      market: String?) async throws -> Worker {
        let alive = (workers[key] ?? []).filter { $0.isAlive }
        workers[key] = alive
        var leastLoaded: Worker?
        var best = Int.max
        for candidate in alive {
            let depth = await candidate.depth
            if depth == 0 { return candidate }
            if depth < best {
                best = depth
                leastLoaded = candidate
            }
        }
        if (workers[key] ?? []).count < Self.maxWorkersPerMarket {
            let fresh = try Worker(python: python, root: root, market: market)
            workers[key, default: []].append(fresh)
            return fresh
        }
        if let leastLoaded { return leastLoaded }
        let fresh = try Worker(python: python, root: root, market: market)
        workers[key, default: []].append(fresh)
        return fresh
    }
}

/// One serve process. All pipe I/O happens inside the single drain loop —
/// strict FIFO no matter how many callers are waiting.
private actor Worker {
    nonisolated let process: Process
    private let stdinHandle: FileHandle
    private var lineIterator: AsyncLineSequence<FileHandle.AsyncBytes>.AsyncIterator

    private var queue: [(args: [String], continuation: CheckedContinuation<Data, Error>)] = []
    private var draining = false
    private var dead = false

    nonisolated var isAlive: Bool { process.isRunning }

    /// Queued requests plus the one in flight — the pool's load-balance signal.
    var depth: Int { queue.count + (draining ? 1 : 0) }

    init(python: String, root: URL, market: String?) throws {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: python)
        process.arguments = [root.appendingPathComponent("appctl.py").path, "serve"]
        process.currentDirectoryURL = root
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONUNBUFFERED"] = "1"
        if let market {
            environment["ADS_MARKET"] = market
        } else {
            environment.removeValue(forKey: "ADS_MARKET")
        }
        process.environment = environment
        let inPipe = Pipe()
        let outPipe = Pipe()
        process.standardInput = inPipe
        process.standardOutput = outPipe
        process.standardError = FileHandle.nullDevice
        try process.run()
        self.process = process
        stdinHandle = inPipe.fileHandleForWriting
        lineIterator = outPipe.fileHandleForReading.bytes.lines.makeAsyncIterator()
    }

    func request(_ args: [String]) async throws -> Data {
        guard !dead, process.isRunning else {
            throw BridgeError.badOutput(exitCode: process.terminationStatus,
                                        stdout: "", stderr: "serve worker is gone")
        }
        return try await withCheckedThrowingContinuation { continuation in
            queue.append((args, continuation))
            drainIfNeeded()
        }
    }

    private func drainIfNeeded() {
        guard !draining else { return }
        draining = true
        Task { await drain() }
    }

    private func drain() async {
        while !queue.isEmpty {
            let item = queue.removeFirst()
            if dead {
                item.continuation.resume(throwing: BridgeError.badOutput(
                    exitCode: process.terminationStatus, stdout: "",
                    stderr: "serve worker died before this request ran"))
                continue
            }
            do {
                let data = try await perform(item.args)
                item.continuation.resume(returning: data)
            } catch {
                // one failure poisons the pipe pairing — fail everything queued
                // and let every caller fall back to one-shot spawns
                dead = true
                terminateProcess()
                item.continuation.resume(throwing: error)
            }
        }
        draining = false
    }

    /// Only ever executed by the drain loop — exactly one write then one read.
    private func perform(_ args: [String]) async throws -> Data {
        guard process.isRunning else {
            throw BridgeError.badOutput(exitCode: process.terminationStatus,
                                        stdout: "", stderr: "serve worker exited")
        }
        var payload = try JSONEncoder().encode(args)
        payload.append(0x0A)
        try stdinHandle.write(contentsOf: payload)

        // hard timeout: killing the process closes the pipe, which unblocks
        // the pending readline — a wedged worker can't hang the UI.
        // 120s matches the one-shot default so a slow-but-legitimate read
        // doesn't kill the worker only to re-run slower as a spawn.
        let watchdog = Task { [process] in
            try await Task.sleep(for: .seconds(120))
            if process.isRunning {
                process.terminate()
            }
        }
        defer { watchdog.cancel() }

        // copy-out/write-back is safe: perform() is only ever run by the single
        // drain loop, so no other reader exists while this await is in flight
        var iterator = lineIterator
        let line = try await iterator.next()
        lineIterator = iterator
        guard let line else {
            throw BridgeError.badOutput(exitCode: process.terminationStatus,
                                        stdout: "",
                                        stderr: "serve worker closed its pipe (timeout or crash)")
        }
        return Data(line.utf8)
    }

    private func terminateProcess() {
        try? stdinHandle.close()
        if process.isRunning {
            process.terminate()
        }
    }

    func shutdown() {
        dead = true
        terminateProcess()
        while !queue.isEmpty {
            let item = queue.removeFirst()
            item.continuation.resume(throwing: BridgeError.badOutput(
                exitCode: process.terminationStatus, stdout: "",
                stderr: "serve worker shut down"))
        }
    }
}
