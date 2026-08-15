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
    case harvestPruneApply(paused: Int)
    case seasonalApply(paused: Int, enabled: Int)
    case seasonSuggestApply(count: Int)
    case seasonCsvApply(tagged: Int, label: String, csv: String)
    case importApply(ImportApplyResponse)
    case adoptExport(AdoptedExport?)
    case negativesApply(negatives: Int, pauses: Int)
    case rulesApprove(count: Int, blocked: String?, conflictsSkipped: Int)
    case resetBids(count: Int, totalReduction: Double)
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

    init(id: UUID = UUID(), title: String, arguments: [String], stdin: Data? = nil,
         scope: ActionScope, cardinality: ActionCardinality = .single,
         preview: ActionPreview? = nil,
         auditVisibility: ActionAuditVisibility = .auditTrail,
         allowedWhenKillActive: Bool = false,
         confirmationPolicy: ActionConfirmationPolicy = .standard,
         responseKind: ActionResponseKind = .none,
         createdAt: Date = Date()) {
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

    var errorDescription: String? {
        switch self {
        case .killActive(let scope):
            "KILL is active; writes for \(scope.confirmationDescription) are blocked."
        case .previewRequired: "This action must be previewed before it can be applied."
        case .previewMismatch: "The preview belongs to a different action intent."
        case .confirmationRequired: "This action requires explicit confirmation."
        }
    }
}

actor ActionCoordinator {
    private let executor: any ActionExecuting

    init(executor: any ActionExecuting) {
        self.executor = executor
    }

    nonisolated func requirement(for intent: ActionIntent, context: ActionPolicyContext,
                                 preview: ActionPreviewReceipt? = nil) -> ActionRequirement {
        ActionPolicy.requirement(for: intent, context: context,
                                 hasPreview: preview?.intentID == intent.id)
    }

    func preview(_ intent: ActionIntent, context: ActionPolicyContext) async throws -> ActionPreviewReceipt {
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
            return try await executor.execute(intent)
        }
    }
}

private struct EmptyActionResponse: Decodable {}

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
            _ = try await bridge.call(EmptyActionResponse.self, intent.arguments,
                                      market: intent.scope.market, stdin: intent.stdin,
                                      preferWorker: false)
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
            result = .harvestPruneApply(paused: response.paused)
        case .seasonalApply:
            let response = try await bridge.call(
                SeasonalApplyResponse.self, intent.arguments, market: intent.scope.market,
                stdin: intent.stdin, preferWorker: false)
            result = .seasonalApply(paused: response.paused, enabled: response.enabled)
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
                                     pauses: response.pausesApplied)
        case .rulesApprove:
            let response = try await bridge.call(
                RulesApproveResponse.self, intent.arguments, market: intent.scope.market,
                stdin: intent.stdin, preferWorker: false)
            result = .rulesApprove(count: response.count ?? 0, blocked: response.blocked,
                                   conflictsSkipped: response.conflictsSkipped ?? 0)
        case .resetBids:
            let response = try await bridge.call(
                ResetBidsResponse.self, intent.arguments, market: intent.scope.market,
                stdin: intent.stdin, preferWorker: false)
            result = .resetBids(count: response.count,
                                totalReduction: response.totalReduction)
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
