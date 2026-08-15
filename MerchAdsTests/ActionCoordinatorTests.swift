import XCTest
@testable import Merch_Ads

private actor FakeActionExecutor: ActionExecuting {
    private var previews: [ActionIntent] = []
    private var executions: [ActionIntent] = []
    private let result: ActionExecutionResult
    private let previewResult: ActionPreviewResult

    init(result: ActionExecutionResult = .none,
         previewResult: ActionPreviewResult = .none) {
        self.result = result
        self.previewResult = previewResult
    }

    func preview(_ intent: ActionIntent) async throws -> ActionPreviewReceipt {
        previews.append(intent)
        return ActionPreviewReceipt(intentID: intent.id, summary: "fake preview",
                                    result: previewResult)
    }

    func execute(_ intent: ActionIntent) async throws -> ActionExecutionReceipt {
        executions.append(intent)
        return ActionExecutionReceipt(intentID: intent.id, scope: intent.scope,
                                      auditVisibility: intent.auditVisibility,
                                      rehearsed: false, summary: "fake execute", result: result)
    }

    func previewed() -> [ActionIntent] { previews }
    func executed() -> [ActionIntent] { executions }
}

final class ActionCoordinatorTests: XCTestCase {
    func testFullPolicyCartesianMatrixAgainstFakeExecutor() async throws {
        let scopes: [ActionScope] = [.global, .market("US"), .allMarkets]
        for alwaysConfirm in [false, true] {
            for cardinality in [ActionCardinality.single, .bulk] {
                for killActive in [false, true] {
                    for hasPreview in [false, true] {
                        for scope in scopes {
                            let fake = FakeActionExecutor()
                            let coordinator = ActionCoordinator(executor: fake)
                            let intent = makeIntent(
                                scope: scope,
                                cardinality: cardinality,
                                preview: hasPreview
                                    ? ActionPreview(arguments: ["negatives-preview"])
                                    : nil)
                            let context = ActionPolicyContext(
                                alwaysConfirm: alwaysConfirm,
                                killActive: killActive)

                            let initial = coordinator.requirement(for: intent, context: context)
                            if killActive {
                                XCTAssertEqual(initial, .blocked(.killActive(scope: scope)))
                                do {
                                    if hasPreview {
                                        _ = try await coordinator.preview(intent, context: context)
                                    } else {
                                        _ = try await coordinator.execute(intent, context: context,
                                                                          confirmed: true)
                                    }
                                    XCTFail("KILL-active action reached the executor")
                                } catch {
                                    XCTAssertEqual(error as? ActionCoordinatorError,
                                                   .killActive(scope))
                                }
                                let previews = await fake.previewed()
                                let executions = await fake.executed()
                                XCTAssertTrue(previews.isEmpty)
                                XCTAssertTrue(executions.isEmpty)
                                continue
                            }

                            var previewReceipt: ActionPreviewReceipt?
                            if hasPreview {
                                XCTAssertEqual(initial, .preview)
                                previewReceipt = try await coordinator.preview(intent, context: context)
                            }
                            let needsConfirmation = alwaysConfirm || cardinality == .bulk
                            XCTAssertEqual(
                                coordinator.requirement(for: intent, context: context,
                                                        preview: previewReceipt),
                                needsConfirmation ? .confirmation : .ready)
                            let receipt = try await coordinator.execute(
                                intent, context: context, preview: previewReceipt,
                                confirmed: needsConfirmation)
                            XCTAssertEqual(receipt.scope, scope)
                            let previews = await fake.previewed()
                            let executions = await fake.executed()
                            XCTAssertEqual(previews.count, hasPreview ? 1 : 0)
                            XCTAssertEqual(executions.map(\.scope), [scope])
                        }
                    }
                }
            }
        }
    }

    func testConfirmationKillAndCardinalityPolicyMatrixAgainstFakeExecutor() async throws {
        for alwaysConfirm in [false, true] {
            for cardinality in [ActionCardinality.single, .bulk] {
                for killActive in [false, true] {
                    let fake = FakeActionExecutor()
                    let coordinator = ActionCoordinator(executor: fake)
                    let intent = makeIntent(scope: .market("US"), cardinality: cardinality)
                    let context = ActionPolicyContext(alwaysConfirm: alwaysConfirm,
                                                      killActive: killActive)
                    let requirement = coordinator.requirement(for: intent, context: context)

                    if killActive {
                        XCTAssertEqual(requirement, .blocked(.killActive(scope: .market("US"))))
                        do {
                            _ = try await coordinator.execute(intent, context: context,
                                                              confirmed: true)
                            XCTFail("KILL-active intent executed")
                        } catch {
                            XCTAssertEqual(error as? ActionCoordinatorError,
                                           .killActive(.market("US")))
                        }
                        let executions = await fake.executed()
                        XCTAssertTrue(executions.isEmpty)
                    } else {
                        let needsConfirmation = alwaysConfirm || cardinality == .bulk
                        XCTAssertEqual(requirement, needsConfirmation ? .confirmation : .ready)
                        if needsConfirmation {
                            do {
                                _ = try await coordinator.execute(intent, context: context)
                                XCTFail("Unconfirmed intent executed")
                            } catch {
                                XCTAssertEqual(error as? ActionCoordinatorError,
                                               .confirmationRequired)
                            }
                        }
                        _ = try await coordinator.execute(intent, context: context,
                                                          confirmed: needsConfirmation)
                        let executions = await fake.executed()
                        XCTAssertEqual(executions.map(\.id), [intent.id])
                    }
                }
            }
        }
    }

    func testPreviewPathsThenApplyConfirmationPolicy() async throws {
        for alwaysConfirm in [false, true] {
            for cardinality in [ActionCardinality.single, .bulk] {
                let fake = FakeActionExecutor()
                let coordinator = ActionCoordinator(executor: fake)
                let intent = makeIntent(scope: .market("DE"), cardinality: cardinality,
                                        preview: ActionPreview(arguments: ["resetbids"]))
                let context = ActionPolicyContext(alwaysConfirm: alwaysConfirm, killActive: false)

                XCTAssertEqual(coordinator.requirement(for: intent, context: context), .preview)
                do {
                    _ = try await coordinator.execute(intent, context: context, confirmed: true)
                    XCTFail("Intent bypassed preview")
                } catch {
                    XCTAssertEqual(error as? ActionCoordinatorError, .previewRequired)
                }

                let receipt = try await coordinator.preview(intent, context: context)
                let needsConfirmation = alwaysConfirm || cardinality == .bulk
                XCTAssertEqual(coordinator.requirement(for: intent, context: context,
                                                       preview: receipt),
                               needsConfirmation ? .confirmation : .ready)
                _ = try await coordinator.execute(intent, context: context, preview: receipt,
                                                  confirmed: needsConfirmation)
                let previews = await fake.previewed()
                let executions = await fake.executed()
                XCTAssertEqual(previews.map(\.scope), [.market("DE")])
                XCTAssertEqual(executions.map(\.scope), [.market("DE")])
            }
        }
    }

    func testKillBlocksPreviewBeforeExecutorIsCalled() async {
        let fake = FakeActionExecutor()
        let coordinator = ActionCoordinator(executor: fake)
        let intent = makeIntent(scope: .market("FR"),
                                preview: ActionPreview(arguments: ["negatives-preview"]))
        do {
            _ = try await coordinator.preview(
                intent, context: ActionPolicyContext(alwaysConfirm: false, killActive: true))
            XCTFail("KILL-active preview path reached executor")
        } catch {
            XCTAssertEqual(error as? ActionCoordinatorError, .killActive(.market("FR")))
        }
        let previews = await fake.previewed()
        XCTAssertTrue(previews.isEmpty)
    }

    func testGlobalMarketAndAllMarketScopesReachExecutorUnchanged() async throws {
        let fake = FakeActionExecutor()
        let coordinator = ActionCoordinator(executor: fake)
        let context = ActionPolicyContext(alwaysConfirm: false, killActive: false)
        let scopes: [ActionScope] = [.global, .market("IT"), .allMarkets]

        for scope in scopes {
            let intent = makeIntent(scope: scope)
            let receipt = try await coordinator.execute(intent, context: context)
            XCTAssertEqual(receipt.scope, scope)
        }
        let executions = await fake.executed()
        XCTAssertEqual(executions.map(\.scope), scopes)
    }

    @MainActor
    func testIntentScopeIsFrozenAcrossMarketSwitch() async throws {
        let oldStored = UserDefaults.standard.string(forKey: AppSettings.selectedMarketKey)
        defer {
            if let oldStored {
                UserDefaults.standard.set(oldStored, forKey: AppSettings.selectedMarketKey)
            } else {
                UserDefaults.standard.removeObject(forKey: AppSettings.selectedMarketKey)
            }
        }

        let appState = AppState()
        appState.selectedMarket = "US"
        let intent = appState.marketIntent(title: "Pause", arguments: ["pause", "--adgroup", "1"])
        appState.selectedMarket = "UK"

        let fake = FakeActionExecutor()
        let coordinator = ActionCoordinator(executor: fake)
        _ = try await coordinator.execute(
            intent, context: ActionPolicyContext(alwaysConfirm: false, killActive: false))
        let executions = await fake.executed()
        XCTAssertEqual(executions.first?.scope, .market("US"))
    }

    func testGlobalKillReleaseCanBeExplicitlyAllowedWhileKillIsActive() async throws {
        let fake = FakeActionExecutor()
        let coordinator = ActionCoordinator(executor: fake)
        let intent = ActionIntent(title: "Release KILL", arguments: ["kill", "--off"],
                                  scope: .global, auditVisibility: .globalConfiguration,
                                  allowedWhenKillActive: true)
        let context = ActionPolicyContext(alwaysConfirm: true, killActive: true)
        XCTAssertEqual(coordinator.requirement(for: intent, context: context), .confirmation)
        let receipt = try await coordinator.execute(intent, context: context, confirmed: true)
        XCTAssertEqual(receipt.auditVisibility, .globalConfiguration)
    }

    func testPreviewReceiptCannotBeReusedForAnotherIntent() async throws {
        let fake = FakeActionExecutor()
        let coordinator = ActionCoordinator(executor: fake)
        let first = makeIntent(scope: .market("ES"),
                               preview: ActionPreview(arguments: ["resetbids"]))
        let second = makeIntent(scope: .market("ES"),
                                preview: ActionPreview(arguments: ["resetbids"]))
        let context = ActionPolicyContext(alwaysConfirm: false, killActive: false)
        let receipt = try await coordinator.preview(first, context: context)
        do {
            _ = try await coordinator.execute(second, context: context, preview: receipt)
            XCTFail("Mismatched preview was accepted")
        } catch {
            XCTAssertEqual(error as? ActionCoordinatorError, .previewMismatch)
        }
    }

    func testIntentCanRequireConfirmationIndependentlyOfGlobalSetting() async throws {
        let fake = FakeActionExecutor()
        let coordinator = ActionCoordinator(executor: fake)
        let intent = ActionIntent(
            title: "Permanent negate", arguments: ["negate"], scope: .market("US"),
            confirmationPolicy: .required)
        let context = ActionPolicyContext(alwaysConfirm: false, killActive: false)

        XCTAssertEqual(coordinator.requirement(for: intent, context: context), .confirmation)
        do {
            _ = try await coordinator.execute(intent, context: context)
            XCTFail("Intent-specific confirmation policy was bypassed")
        } catch {
            XCTAssertEqual(error as? ActionCoordinatorError, .confirmationRequired)
        }
        _ = try await coordinator.execute(intent, context: context, confirmed: true)
        let executions = await fake.executed()
        XCTAssertEqual(executions.map(\.id), [intent.id])
    }

    func testTypedExecutionResultAndResponseKindPassThroughCoordinator() async throws {
        let expected = ActionExecutionResult.promote(keywordExit: 0, asinExit: 2)
        let fake = FakeActionExecutor(result: expected)
        let coordinator = ActionCoordinator(executor: fake)
        let intent = ActionIntent(
            title: "Promote", arguments: ["promote"], scope: .market("UK"),
            cardinality: .bulk, responseKind: .promote)
        let receipt = try await coordinator.execute(
            intent, context: ActionPolicyContext(alwaysConfirm: false, killActive: false),
            confirmed: true)

        XCTAssertEqual(receipt.result, expected)
        let executions = await fake.executed()
        XCTAssertEqual(executions.first?.responseKind, .promote)
        XCTAssertEqual(executions.first?.scope, .market("UK"))
    }

    func testTypedPreviewResultAndResponseKindPassThroughCoordinator() async throws {
        let response = ResetBidsResponse(
            market: "DE", count: 2, totalReduction: 0.42,
            preview: true, applied: nil, items: nil)
        let fake = FakeActionExecutor(previewResult: .resetBids(response))
        let coordinator = ActionCoordinator(executor: fake)
        let intent = ActionIntent(
            title: "Reset bids", arguments: ["resetbids", "--apply"],
            scope: .market("DE"), cardinality: .bulk,
            preview: ActionPreview(arguments: ["resetbids"], responseKind: .resetBids),
            responseKind: .resetBids)

        let receipt = try await coordinator.preview(
            intent, context: ActionPolicyContext(alwaysConfirm: false, killActive: false))

        XCTAssertEqual(receipt.result, .resetBids(response))
        let previews = await fake.previewed()
        XCTAssertEqual(previews.first?.preview?.responseKind, .resetBids)
        XCTAssertEqual(previews.first?.scope, .market("DE"))
    }

    private func makeIntent(scope: ActionScope,
                            cardinality: ActionCardinality = .single,
                            preview: ActionPreview? = nil) -> ActionIntent {
        ActionIntent(title: "Test action", arguments: ["pause", "--adgroup", "123"],
                     scope: scope, cardinality: cardinality, preview: preview)
    }
}
