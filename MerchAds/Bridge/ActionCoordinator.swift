import Foundation

enum ActionScope: Codable, Hashable, Sendable {
    case market(String)
    case allMarkets
    case global

    var market: String? {
        if case .market(let code) = self { return code }
        return nil
    }

    var confirmationDescription: String {
        switch self {
        case .market(let code): "market \(code)"
        case .allMarkets: "all markets"
        case .global: "global engine configuration"
        }
    }
}

enum ActionCardinality: String, Codable, Hashable, Sendable {
    case single
    case bulk
}

enum ActionAuditVisibility: Codable, Hashable, Sendable {
    case auditTrail
    case globalConfiguration
}

enum ActionConfirmationPolicy: String, Codable, Hashable, Sendable {
    case standard
    case required
}

enum ActionResponseKind: String, Codable, Hashable, Sendable {
    case none
    case negate
    case promote
    case promoteGroup
    case harvestPruneApply
    case seasonalApply
    case seasonSuggestApply
    case seasonCsvApply
    case importApply
    case adoptExport
    case negativesApply
    case rulesApprove
    case resetBids
    case run
    case undo
    case everywhereApply
}

enum ActionExecutionResult: Equatable, Sendable {
    case none
    case negate(applied: Bool)
    case promote(keywordExit: Int?, asinExit: Int?)
    case promoteGroup(promoted: Bool)
    /// `note` is set only when Amazon did not confirm every requested pause.
    case harvestPruneApply(paused: Int, note: String?)
    case seasonalApply(paused: Int, enabled: Int, note: String?)
    case seasonSuggestApply(count: Int)
    case seasonCsvApply(tagged: Int, label: String, csv: String)
    case importApply(ImportApplyResponse)
    case adoptExport(AdoptedExport?)
    /// `note` is set only when Amazon refused part of the batch.
    case negativesApply(negatives: Int, pauses: Int, note: String?)
    case rulesApprove(count: Int, blocked: String?, conflictsSkipped: Int,
                      staleSkipped: Int, notApplied: Int, note: String?)
    /// `note` is set only when Amazon refused part of the plan.
    case resetBids(count: Int, totalReduction: Double, note: String?)
    case run(code: Int, text: String)
    case undo(applied: Bool, entityID: String, newState: String?, restoredBid: Double?)
    case everywhereApply(applied: Int, skipped: Int, failed: Int)
}

enum ActionPreviewResponseKind: String, Codable, Hashable, Sendable {
    case none
    case importPreview
    case resetBids
}

enum ActionPreviewResult: Equatable, Sendable {
    case none
    case importPreview(ImportPreviewResponse)
    case resetBids(ResetBidsResponse)
}

struct ActionPreview: Codable, Hashable, Sendable {
    let arguments: [String]
    var stdin: Data?
    let responseKind: ActionPreviewResponseKind

    init(arguments: [String], stdin: Data? = nil,
         responseKind: ActionPreviewResponseKind = .none) {
        self.arguments = arguments
        self.stdin = stdin
        self.responseKind = responseKind
    }
}

struct ActionIntent: Codable, Hashable, Identifiable, Sendable {
    let id: UUID
    let title: String
    let arguments: [String]
    let stdin: Data?
    let scope: ActionScope
    let cardinality: ActionCardinality
    let preview: ActionPreview?
    let auditVisibility: ActionAuditVisibility
    let allowedWhenKillActive: Bool
    let confirmationPolicy: ActionConfirmationPolicy
    let responseKind: ActionResponseKind
    let createdAt: Date
    /// The resolved engine/data-root configuration that produced the screen
    /// this intent came from. A settings change makes the intent stale.
    let executionContextID: String?

    init(id: UUID = UUID(), title: String, arguments: [String], stdin: Data? = nil,
         scope: ActionScope, cardinality: ActionCardinality = .single,
         preview: ActionPreview? = nil,
         auditVisibility: ActionAuditVisibility = .auditTrail,
         allowedWhenKillActive: Bool = false,
         confirmationPolicy: ActionConfirmationPolicy = .standard,
         responseKind: ActionResponseKind = .none,
         createdAt: Date = Date(), executionContextID: String? = nil) {
        self.id = id
        self.title = title
        self.arguments = arguments
        self.stdin = stdin
        self.scope = scope
        self.cardinality = cardinality
        self.preview = preview
        self.auditVisibility = auditVisibility
        self.allowedWhenKillActive = allowedWhenKillActive
        self.confirmationPolicy = confirmationPolicy
        self.responseKind = responseKind
        self.createdAt = createdAt
        self.executionContextID = executionContextID
    }

    /// What makes two clicks THE SAME live write.
    ///
    /// `id` is a fresh UUID per click, so it cannot answer this. On 2026-08-06
    /// the app sent the same pause for campaign 900000000012345 twice, ten
    /// seconds apart: `ENABLED->PAUSED`, then `PAUSED->PAUSED`, both logged as
    /// submitted. The second row is undoable as a pause, so an Undo would have
    /// ENABLED a campaign that was paused on purpose.
    var fingerprint: String {
        let body = stdin.map { String(decoding: $0, as: UTF8.self) } ?? ""
        return ([scope.confirmationDescription] + arguments + [body])
            .joined(separator: "\u{1F}")
    }
}

struct ActionPolicyContext: Equatable, Sendable {
    let alwaysConfirm: Bool
    let killActive: Bool
}

enum ActionBlockReason: Equatable, Sendable {
    case killActive(scope: ActionScope)
}

enum ActionRequirement: Equatable, Sendable {
    case blocked(ActionBlockReason)
    case preview
    case confirmation
    case ready
}

enum ActionPolicy {
    static func requirement(for intent: ActionIntent, context: ActionPolicyContext,
                            hasPreview: Bool = false) -> ActionRequirement {
        if context.killActive && !intent.allowedWhenKillActive {
            return .blocked(.killActive(scope: intent.scope))
        }
        if intent.preview != nil && !hasPreview {
            return .preview
        }
        if context.alwaysConfirm || intent.cardinality == .bulk
            || intent.confirmationPolicy == .required {
            return .confirmation
        }
        return .ready
    }
}

/// A plan resolved in one market must never be applied in another.
///
/// Every action screen holds ids or keys that mean something only in the market
/// they were read from, and the profile picker can move under them while a
/// preview is in flight or a confirmation dialog is open. The engine cannot
/// catch it: `negatives-apply` checks snapshot DATES, not which account an ad
/// group belongs to, and two markets usually share the same date. So the check
/// belongs here, at the moment the plan turns into a write.
enum PlanMarket {
    /// nil when the plan may proceed. Otherwise the sentence to put on screen,
    /// which has to say that nothing was sent — a refusal the operator does not
    /// see is the same as a plan that quietly went to the wrong account.
    static func refusal(planned: String?, current: String) -> String? {
        guard let planned, planned != current else { return nil }
        return "This plan was prepared for \(planned) and the app is now on "
             + "\(current). Nothing was sent. Reload this screen to build a plan "
             + "for \(current)."
    }
}

struct ActionPreviewReceipt: Equatable, Sendable {
    let intentID: UUID
    let summary: String
    let result: ActionPreviewResult

    init(intentID: UUID, summary: String, result: ActionPreviewResult = .none) {
        self.intentID = intentID
        self.summary = summary
        self.result = result
    }
}

struct ActionExecutionReceipt: Equatable, Sendable {
    let intentID: UUID
    let scope: ActionScope
    let auditVisibility: ActionAuditVisibility
    let rehearsed: Bool
    let summary: String
    let result: ActionExecutionResult

    init(intentID: UUID, scope: ActionScope, auditVisibility: ActionAuditVisibility,
         rehearsed: Bool, summary: String, result: ActionExecutionResult = .none) {
        self.intentID = intentID
        self.scope = scope
        self.auditVisibility = auditVisibility
        self.rehearsed = rehearsed
        self.summary = summary
        self.result = result
    }
}

protocol ActionExecuting: Sendable {
    func preview(_ intent: ActionIntent) async throws -> ActionPreviewReceipt
    func execute(_ intent: ActionIntent) async throws -> ActionExecutionReceipt
}

enum ActionCoordinatorError: LocalizedError, Equatable {
    case killActive(ActionScope)
    case previewRequired
    case previewMismatch
    case confirmationRequired
    case duplicateInFlight(String)
    case notApplied(String)
    case staleExecutionContext

    var errorDescription: String? {
        switch self {
        case .killActive(let scope):
            "KILL is active; writes for \(scope.confirmationDescription) are blocked."
        case .previewRequired: "This action must be previewed before it can be applied."
        case .previewMismatch: "The preview belongs to a different action intent."
        case .confirmationRequired: "This action requires explicit confirmation."
        case .duplicateInFlight(let title):
            "\(title) is already running. Wait for it to finish before sending it again."
        case .notApplied(let detail):
            "Amazon did not apply this change: \(detail)"
        case .staleExecutionContext:
            "Settings changed after this action was prepared. Reload the screen and try again."
        }
    }
}

actor ActionCoordinator {
    private let executor: any ActionExecuting
    private let executionContextID: String?

    /// Fingerprints of the writes currently in flight. The check and the insert
    /// below are both synchronous inside this actor, so no `await` can land
    /// between them and let a second identical write through.
    private var inFlight: Set<String> = []

    init(executor: any ActionExecuting, executionContextID: String? = nil) {
        self.executor = executor
        self.executionContextID = executionContextID
    }

    nonisolated func requirement(for intent: ActionIntent, context: ActionPolicyContext,
                                 preview: ActionPreviewReceipt? = nil) -> ActionRequirement {
        ActionPolicy.requirement(for: intent, context: context,
                                 hasPreview: preview?.intentID == intent.id)
    }

    func preview(_ intent: ActionIntent, context: ActionPolicyContext) async throws -> ActionPreviewReceipt {
        try validateExecutionContext(intent)
        if case .blocked(.killActive(let scope)) = requirement(for: intent, context: context) {
            throw ActionCoordinatorError.killActive(scope)
        }
        guard intent.preview != nil else {
            return ActionPreviewReceipt(intentID: intent.id, summary: "No preview required")
        }
        return try await executor.preview(intent)
    }

    func execute(_ intent: ActionIntent, context: ActionPolicyContext,
                 preview: ActionPreviewReceipt? = nil,
                 confirmed: Bool = false) async throws -> ActionExecutionReceipt {
        try validateExecutionContext(intent)
        if let preview, preview.intentID != intent.id {
            throw ActionCoordinatorError.previewMismatch
        }
        switch requirement(for: intent, context: context, preview: preview) {
        case .blocked(.killActive(let scope)):
            throw ActionCoordinatorError.killActive(scope)
        case .preview:
            throw ActionCoordinatorError.previewRequired
        case .confirmation where !confirmed:
            throw ActionCoordinatorError.confirmationRequired
        case .confirmation, .ready:
            let print = intent.fingerprint
            guard !inFlight.contains(print) else {
                throw ActionCoordinatorError.duplicateInFlight(intent.title)
            }
            inFlight.insert(print)
            defer { inFlight.remove(print) }
            return try await executor.execute(intent)
        }
    }

    private func validateExecutionContext(_ intent: ActionIntent) throws {
        guard let executionContextID else { return }
        guard intent.executionContextID == executionContextID else {
            throw ActionCoordinatorError.staleExecutionContext
        }
    }
}

private struct EmptyActionResponse: Decodable {}

/// The shape almost every single-entity write answers with.
///
/// `appctl` reports an Amazon REJECTION inside a successful envelope: the
/// process exits 0, `ok` is true, and `applied` is false. The execute path
/// decoded `EmptyActionResponse` here, which reads none of that, so every
/// rejected pause, enable, bid, budget and archive came back as "Applied".
///
/// Measured on the US database on 2026-08-24: of 57 `archive_campaign` rows,
/// 29 carry `result = http=400`. Amazon refused all 29 and the operator was
/// told each one worked.
///
/// Every field is optional on purpose. A command that reports no `applied` at
/// all — `kill`, the config writers — decodes nil and is treated as success,
/// exactly as before.
private struct AppliedActionResponse: Decodable {
    let applied: Bool?
    let http: Int?
    let error: String?
    let note: String?
    let newState: String?

    /// What to tell the operator when Amazon refused.
    var rejectionDetail: String {
        var parts: [String] = []
        if let http { parts.append("HTTP \(http)") }
        if let error, !error.isEmpty { parts.append(error) }
        if let note, !note.isEmpty { parts.append(note) }
        return parts.isEmpty ? "the engine reported applied: false"
                             : parts.joined(separator: " — ")
    }
}

struct BridgeActionExecutor: ActionExecuting {
    let engineRoot: URL
    let pythonOverride: String?

    func preview(_ intent: ActionIntent) async throws -> ActionPreviewReceipt {
        guard let preview = intent.preview else {
            return ActionPreviewReceipt(intentID: intent.id, summary: "No preview required")
        }
        let bridge = try PythonBridge(engineRoot: engineRoot, pythonOverride: pythonOverride)
        let result: ActionPreviewResult
        switch preview.responseKind {
        case .none:
            _ = try await bridge.call(EmptyActionResponse.self, preview.arguments,
                                      market: intent.scope.market, stdin: preview.stdin,
                                      preferWorker: false)
            result = .none
        case .importPreview:
            let response = try await bridge.call(
                ImportPreviewResponse.self, preview.arguments, market: intent.scope.market,
                stdin: preview.stdin, preferWorker: false)
            result = .importPreview(response)
        case .resetBids:
            let response = try await bridge.call(
                ResetBidsResponse.self, preview.arguments, market: intent.scope.market,
                stdin: preview.stdin, preferWorker: false)
            result = .resetBids(response)
        }
        return ActionPreviewReceipt(intentID: intent.id, summary: "Preview loaded", result: result)
    }

    func execute(_ intent: ActionIntent) async throws -> ActionExecutionReceipt {
        let bridge = try PythonBridge(engineRoot: engineRoot, pythonOverride: pythonOverride)
        let result: ActionExecutionResult
        switch intent.responseKind {
        case .none:
            let response = try await bridge.call(
                AppliedActionResponse.self, intent.arguments,
                market: intent.scope.market, stdin: intent.stdin, preferWorker: false)
            if response.applied == false {
                throw ActionCoordinatorError.notApplied(response.rejectionDetail)
            }
            result = .none
        case .negate:
            let response = try await bridge.call(
                NegateResponse.self, intent.arguments, market: intent.scope.market,
                stdin: intent.stdin, preferWorker: false)
            result = .negate(applied: response.applied)
        case .promote:
            let response = try await bridge.call(
                PromoteResponse.self, intent.arguments, market: intent.scope.market,
                stdin: intent.stdin, preferWorker: false)
            result = .promote(keywordExit: response.keywords?.code,
                              asinExit: response.asins?.code)
        case .promoteGroup:
            let response = try await bridge.call(
                PromoteGroupResult.self, intent.arguments, market: intent.scope.market,
                stdin: intent.stdin, preferWorker: false)
            result = .promoteGroup(promoted: response.result?.promoted ?? false)
        case .harvestPruneApply:
            let response = try await bridge.call(
                HarvestPruneApplyResponse.self, intent.arguments,
                market: intent.scope.market, stdin: intent.stdin, preferWorker: false)
            result = .harvestPruneApply(paused: response.paused,
                                        note: response.shortfallNote)
        case .seasonalApply:
            let response = try await bridge.call(
                SeasonalApplyResponse.self, intent.arguments, market: intent.scope.market,
                stdin: intent.stdin, preferWorker: false)
            result = .seasonalApply(paused: response.paused, enabled: response.enabled,
                                    note: response.partialFailureNote)
        case .seasonSuggestApply:
            let response = try await bridge.call(
                SeasonSuggestApplyResponse.self, intent.arguments, market: intent.scope.market,
                stdin: intent.stdin, preferWorker: false)
            result = .seasonSuggestApply(count: response.count)
        case .seasonCsvApply:
            let response = try await bridge.call(
                SeasonCsvApplyResponse.self, intent.arguments, market: intent.scope.market,
                stdin: intent.stdin, preferWorker: false)
            result = .seasonCsvApply(tagged: response.tagged, label: response.label,
                                     csv: response.csv)
        case .importApply:
            let response = try await bridge.call(
                ImportApplyResponse.self, intent.arguments, market: intent.scope.market,
                stdin: intent.stdin, preferWorker: false)
            result = .importApply(response)
        case .adoptExport:
            let response = try await bridge.call(
                AdoptExportResponse.self, intent.arguments, market: intent.scope.market,
                stdin: intent.stdin, preferWorker: false)
            result = .adoptExport(response.export)
        case .negativesApply:
            let response = try await bridge.call(
                NegativesApplyResponse.self, intent.arguments, market: intent.scope.market,
                stdin: intent.stdin, preferWorker: false)
            result = .negativesApply(negatives: response.negativesApplied,
                                     pauses: response.pausesApplied,
                                     note: response.partialFailureNote)
        case .rulesApprove:
            let response = try await bridge.call(
                RulesApproveResponse.self, intent.arguments, market: intent.scope.market,
                stdin: intent.stdin, preferWorker: false)
            result = .rulesApprove(count: response.count ?? 0, blocked: response.blocked,
                                   conflictsSkipped: response.conflictsSkipped ?? 0,
                                   staleSkipped: response.staleSkipped ?? 0,
                                   notApplied: response.notAppliedResults.count,
                                   note: response.resultFailureNote)
        case .resetBids:
            let response = try await bridge.call(
                ResetBidsResponse.self, intent.arguments, market: intent.scope.market,
                stdin: intent.stdin, preferWorker: false)
            // What MOVED, not what was planned. `count`/`totalReduction`
            // describe the proposal, and printing those beside "Amazon refused
            // 1 of 3" made the receipt contradict itself.
            result = .resetBids(count: response.shownCount,
                                totalReduction: response.shownReduction,
                                note: response.partialFailureNote)
        case .run:
            let response = try await bridge.call(
                RunResponse.self, intent.arguments, market: intent.scope.market,
                stdin: intent.stdin, preferWorker: false)
            result = .run(code: response.code, text: response.text)
        case .undo:
            let response = try await bridge.call(
                UndoResponse.self, intent.arguments, market: intent.scope.market,
                stdin: intent.stdin, preferWorker: false)
            result = .undo(applied: response.applied, entityID: response.entityId,
                           newState: response.newState, restoredBid: response.restoredBid)
        case .everywhereApply:
            let response = try await bridge.call(
                EverywhereApplyResponse.self, intent.arguments, market: intent.scope.market,
                stdin: intent.stdin, preferWorker: false)
            result = .everywhereApply(applied: response.applied,
                                      skipped: response.skippedNoop, failed: response.failed)
        }
        return ActionExecutionReceipt(intentID: intent.id, scope: intent.scope,
                                      auditVisibility: intent.auditVisibility,
                                      rehearsed: false, summary: "Applied", result: result)
    }
}

actor RehearsalActionExecutor: ActionExecuting {
    private(set) var recordedIntents: [ActionIntent] = []
    private(set) var previewedIntents: [ActionIntent] = []

    func preview(_ intent: ActionIntent) async throws -> ActionPreviewReceipt {
        previewedIntents.append(intent)
        return ActionPreviewReceipt(intentID: intent.id, summary: "Rehearsal preview")
    }

    func execute(_ intent: ActionIntent) async throws -> ActionExecutionReceipt {
        recordedIntents.append(intent)
        return ActionExecutionReceipt(intentID: intent.id, scope: intent.scope,
                                      auditVisibility: intent.auditVisibility,
                                      rehearsed: true, summary: "Recorded in rehearsal")
    }
}
