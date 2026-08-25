import XCTest
@testable import Merch_Ads

/// The app sent the same live write twice.
///
/// On 2026-08-06 the US `writes_log` recorded two pauses of campaign
/// 900000000012345 ten seconds apart: `ENABLED->PAUSED`, then `PAUSED->PAUSED`,
/// both `submitted`. The second row is undoable as a pause, so an Undo would
/// have ENABLED a campaign that was paused on purpose.
///
/// `ActionIntent.id` is a fresh UUID per click, so it can never answer "is this
/// the same write". The fingerprint does.

/// Holds every execute inside the executor until the gate opens, so a test can
/// have two writes genuinely in flight at once and measure the peak.
private actor GatedExecutor: ActionExecuting {
    private var running = 0
    private var peak = 0
    private var waiters: [CheckedContinuation<Void, Never>] = []
    private var open = false

    func preview(_ intent: ActionIntent) async throws -> ActionPreviewReceipt {
        ActionPreviewReceipt(intentID: intent.id, summary: "preview")
    }

    func execute(_ intent: ActionIntent) async throws -> ActionExecutionReceipt {
        running += 1
        peak = max(peak, running)
        if !open {
            await withCheckedContinuation { waiters.append($0) }
        }
        running -= 1
        return ActionExecutionReceipt(intentID: intent.id, scope: intent.scope,
                                      auditVisibility: intent.auditVisibility,
                                      rehearsed: false, summary: "done", result: .none)
    }

    /// Open for good and release everyone already parked.
    func openGate() {
        open = true
        let parked = waiters
        waiters = []
        for w in parked { w.resume() }
    }

    /// Spin until `n` writes are inside, or give up so a broken test fails
    /// loudly instead of hanging the whole suite for ten minutes.
    func waitForRunning(_ n: Int) async -> Bool {
        for _ in 0..<100_000 {
            if running >= n { return true }
            await Task.yield()
        }
        return false
    }

    func peakConcurrent() -> Int { peak }
}

final class DuplicateWriteGuardTests: XCTestCase {

    /// A static func, not a method: a closure calling a method on the test case
    /// would capture `self`, and Swift 6 refuses to send that into a Task.
    private static func pauseIntent() -> ActionIntent {
        ActionIntent(title: "Pause campaign",
                     arguments: ["pause-campaign", "--campaign", "900000000012345"],
                     scope: .market("US"))
    }

    private static let context = ActionPolicyContext(alwaysConfirm: false, killActive: false)

    func testTwoClicksOnTheSameWriteDoNotBothReachAmazon() async throws {
        let executor = GatedExecutor()
        let coordinator = ActionCoordinator(executor: executor)
        let ctx = Self.context

        let one = Self.pauseIntent()
        let first = Task { try await coordinator.execute(one, context: ctx, confirmed: true) }
        let started = await executor.waitForRunning(1)
        XCTAssertTrue(started, "the first write never entered the executor")

        // Without the guard the duplicate does not throw — it walks into the
        // executor and parks on the gate, and this test would hang the whole
        // suite for ten minutes instead of failing. The valve opens the gate
        // shortly after, so a missing guard fails FAST and says what happened.
        let valve = Task {
            try? await Task.sleep(for: .milliseconds(750))
            await executor.openGate()
        }

        // Second click while the first is still in flight. Different UUID, same
        // write. This is the shape that produced PAUSED->PAUSED.
        do {
            _ = try await coordinator.execute(Self.pauseIntent(), context: ctx, confirmed: true)
            XCTFail("the duplicate write was allowed through")
        } catch let error as ActionCoordinatorError {
            guard case .duplicateInFlight = error else {
                return XCTFail("wrong error: \(error)")
            }
        }
        valve.cancel()

        await executor.openGate()
        _ = try await first.value
        let peak = await executor.peakConcurrent()
        XCTAssertEqual(1, peak, "two identical writes were in flight at once")
    }

    func testTheSameWriteIsAllowedAgainOnceTheFirstHasFinished() async throws {
        // A deliberate retry must still work. The guard is in-flight only:
        // pause, enable, pause again is a real thing an operator does.
        let executor = GatedExecutor()
        await executor.openGate()
        let coordinator = ActionCoordinator(executor: executor)
        let ctx = Self.context

        _ = try await coordinator.execute(Self.pauseIntent(), context: ctx, confirmed: true)
        _ = try await coordinator.execute(Self.pauseIntent(), context: ctx, confirmed: true)
    }

    func testADifferentEntityIsNotTreatedAsADuplicate() async throws {
        let executor = GatedExecutor()
        let coordinator = ActionCoordinator(executor: executor)
        let ctx = Self.context

        let one = Self.pauseIntent()
        let other = ActionIntent(title: "Pause campaign",
                                 arguments: ["pause-campaign", "--campaign", "999"],
                                 scope: .market("US"))
        let first = Task { try await coordinator.execute(one, context: ctx, confirmed: true) }
        let second = Task { try await coordinator.execute(other, context: ctx, confirmed: true) }

        let both = await executor.waitForRunning(2)
        XCTAssertTrue(both, "two different campaigns should both be allowed in flight")

        await executor.openGate()
        _ = try await first.value
        _ = try await second.value
    }

    /// Two bulk plans differ only in what they carry on stdin.
    ///
    /// Found by mutation on 2026-08-24: dropping `stdin` from the fingerprint
    /// broke nothing in the suite. Every existing fingerprint test varies the
    /// arguments or the market, and the Approval Queue and the act-everywhere
    /// sheet both send the SAME argv with a different approved set in the body.
    /// Collapsed onto one fingerprint, the operator approves one batch, then
    /// approves a second and is told it is already in flight — so the second
    /// batch silently never runs.
    func testTwoPlansWithTheSameArgvButDifferentBodiesAreNotTheSameWrite() {
        func intent(_ body: String) -> ActionIntent {
            ActionIntent(title: "Apply approved negatives",
                         arguments: ["negatives-apply"],
                         stdin: Data(body.utf8),
                         scope: .market("US"))
        }
        let first = intent("{\"negatives\":[{\"search_term\":\"shirt\"}]}")
        let second = intent("{\"negatives\":[{\"search_term\":\"mug\"}]}")
        XCTAssertNotEqual(first.fingerprint, second.fingerprint,
                          "a different approved set is a different write")
    }

    func testTheSameBodyIsStillTheSameWrite() {
        func intent() -> ActionIntent {
            ActionIntent(title: "Apply approved negatives",
                         arguments: ["negatives-apply"],
                         stdin: Data("{\"negatives\":[]}".utf8),
                         scope: .market("US"))
        }
        XCTAssertEqual(intent().fingerprint, intent().fingerprint,
                       "the guard must still stop a genuine double-click")
    }

    func testAnIntentWithNoBodyIsNotConfusedWithAnEmptyOne() {
        let none = ActionIntent(title: "Pause", arguments: ["pause-campaign"],
                                scope: .market("US"))
        let empty = ActionIntent(title: "Pause", arguments: ["pause-campaign"],
                                 stdin: Data(), scope: .market("US"))
        XCTAssertEqual(none.fingerprint, empty.fingerprint,
                       "no body and an empty body are the same request")
    }

    func testTheFingerprintIgnoresTheClickIdButNotTheMarket() {
        let a = Self.pauseIntent()
        let b = Self.pauseIntent()
        XCTAssertNotEqual(a.id, b.id)
        XCTAssertEqual(a.fingerprint, b.fingerprint)

        let de = ActionIntent(title: "Pause campaign",
                              arguments: ["pause-campaign", "--campaign", "900000000012345"],
                              scope: .market("DE"))
        XCTAssertNotEqual(a.fingerprint, de.fingerprint,
                          "the same id in another market is another campaign")
    }
}
