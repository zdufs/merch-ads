import SwiftUI

/// Shared "act everywhere" flow for the Accumulated screens. Resolve a selection
/// to its instance count (so the operator sees the blast radius), confirm, then
/// apply through the ActionCoordinator — KILL-gated, every write logged and
/// undoable. Operator decisions (2026-08-09): pause never archives; negate is
/// exact. Backend: `everywhere-preview` / `everywhere-apply`.
struct EverywherePending: Identifiable, Equatable {
    let id = UUID()
    /// The market this plan was RESOLVED in. `everywhere-apply` re-resolves
    /// plain keys (ASIN strings, keyword text) against fresh state, so the same
    /// plan applied under another market writes there successfully — the one
    /// path here that would not fail on a foreign id.
    let market: String
    let kind: String        // "asin" | "keyword"
    let action: String      // "pause" | "negate" | "setbid"
    let keys: [String]
    let verbLabel: String   // "Pause" | "Negate" | "Set bid"
    let noun: String        // "ASIN" | "keyword"
    var match: String = "exact"     // negate: exact | phrase
    var bid: Double?                 // setbid: the bid to write
    var applicable: Int?    // instances that would actually change (from preview)
    var campaigns: Int?
    var skipped: Int?       // every instance the plan will not write
    /// Of `skipped`, the ones already in the requested state — real no-ops.
    var skippedAlreadyInState: Int?
    /// Of `skipped`, the ones with no target id. NOT no-ops: the app cannot
    /// address them at all, so calling them "already at that state" hid a
    /// selection the operator made and never got.
    var skippedUnaddressable: Int?
    var skippedStateUnknown: Int?
    /// The accumulated snapshot the counts were resolved against.
    var asOf: String?

    var stdin: Data {
        var payload: [String: Any] = ["kind": kind, "action": action, "keys": keys]
        if action == "negate" { payload["match"] = match }
        if action == "setbid", let bid { payload["bid"] = bid }
        return (try? JSONSerialization.data(withJSONObject: payload)) ?? Data()
    }
    var selectionLabel: String {
        keys.count == 1 ? keys[0] : "\(keys.count) \(noun)s"
    }
}

struct EverywhereApplySummary: Equatable {
    let message: String
    let isPartialFailure: Bool

    init(applied: Int, skipped: Int, failed: Int) {
        message = "Applied \(applied) · skipped \(skipped) · failed \(failed)."
        isPartialFailure = failed > 0
    }
}

/// Fetch the resolved instance count for a selection so the confirm sheet can
/// state the blast radius. A preview failure is thrown: without exact counts no
/// confirmation exists and no live write can follow.
@MainActor
func resolveEverywhere(_ appState: AppState, kind: String, action: String,
                       keys: [String], verbLabel: String, noun: String,
                       match: String = "exact", bid: Double? = nil) async throws -> EverywherePending? {
    guard !keys.isEmpty else { return nil }
    let market = appState.selectedMarket
    var p = EverywherePending(market: market, kind: kind, action: action, keys: keys,
                              verbLabel: verbLabel, noun: noun, match: match, bid: bid)
    let bridge = try appState.makeBridge()
    let r = try await bridge.call(EverywherePreviewResponse.self, ["everywhere-preview"],
                                  market: market, stdin: p.stdin,
                                  preferWorker: false)
    guard market == appState.selectedMarket, !Task.isCancelled else {
        throw CancellationError()
    }
    p.applicable = r.applicable
    p.campaigns = r.campaigns
    p.skipped = r.skippedNoop
    p.skippedAlreadyInState = r.skippedAlreadyInState
    p.skippedUnaddressable = r.skippedUnaddressable
    p.skippedStateUnknown = r.skippedStateUnknown
    p.asOf = r.asOf
    return p
}

extension View {
    /// Present the confirm sheet for a pending "everywhere" action and apply it.
    func everywhereConfirm(_ pending: Binding<EverywherePending?>,
                           onApplied: @escaping () async -> Void) -> some View {
        modifier(EverywhereConfirmModifier(pending: pending, onApplied: onApplied))
    }
}

private struct EverywhereConfirmModifier: ViewModifier {
    @Environment(AppState.self) private var appState
    @Binding var pending: EverywherePending?
    let onApplied: () async -> Void
    @State private var error: String?
    @State private var result: String?

    func body(content: Content) -> some View {
        content
            .confirmationDialog("Act everywhere?", isPresented: Binding(
                get: { pending != nil }, set: { if !$0 { pending = nil } }),
                titleVisibility: .visible, presenting: pending) { p in
                Button(confirmLabel(p), role: .destructive) { Task { await apply(p) } }
                Button("Cancel", role: .cancel) { pending = nil }
            } message: { p in
                Text(message(p))
            }
            .alert("Couldn't apply", isPresented: Binding(
                get: { error != nil }, set: { if !$0 { error = nil } })) {
                Button("OK") { error = nil }
            } message: { Text(error ?? "") }
            .alert("Everywhere result", isPresented: Binding(
                get: { result != nil }, set: { if !$0 { result = nil } })) {
                Button("OK") { result = nil }
            } message: { Text(result ?? "") }
    }

    private func confirmLabel(_ p: EverywherePending) -> String {
        if let n = p.applicable, n > 0 {
            return "\(p.verbLabel) \(n) \(n == 1 ? "instance" : "instances")"
        }
        return "\(p.verbLabel) everywhere"
    }

    private func message(_ p: EverywherePending) -> String {
        let verb: String
        switch p.action {
        case "negate": verb = "add a \(p.match) negative in"
        case "setbid": verb = "set the bid on"
        default: verb = "pause"
        }
        var s = "\(p.verbLabel) \(p.selectionLabel) everywhere."
        if p.action == "setbid", let bid = p.bid {
            s = "Set the bid on \(p.selectionLabel) to " + String(format: "%.2f", bid) + " everywhere."
        }
        if let n = p.applicable {
            let camps = p.campaigns ?? 0
            s += " This will \(verb) \(n) \(n == 1 ? "instance" : "instances") across "
                + "\(camps) campaign\(camps == 1 ? "" : "s")."
        } else {
            s += " This applies across every campaign they run in."
        }
        // Two different reasons share the skip flag and they need different
        // sentences. "Already at that state" is true only for the ones that
        // are already paused; an instance with no target id is one the app
        // cannot write to at all, and describing it as a no-op is how part of
        // the operator's selection disappeared without being mentioned.
        if let sk = p.skipped, sk > 0 {
            let inState = p.skippedAlreadyInState ?? 0
            let unaddressable = p.skippedUnaddressable ?? 0
            let unknown = p.skippedStateUnknown ?? 0
            if inState > 0 { s += " \(inState) already at that state — skipped." }
            if unaddressable > 0 {
                s += " \(unaddressable) cannot be changed from here "
                   + "(no target id on the latest snapshot)."
            }
            // A row whose state was never mirrored is neither of the above. It
            // was counted into no bucket at all, so a selection could shrink
            // with nothing on screen accounting for the difference.
            if unknown > 0 {
                s += " \(unknown) skipped because their current state is unknown."
            }
            if inState + unaddressable + unknown < sk {
                s += " \(sk - inState - unaddressable - unknown) skipped."
            }
        }
        s += " Reversible from the Audit trail."
        return s
    }

    private func apply(_ p: EverywherePending) async {
        pending = nil
        if let refusal = PlanMarket.refusal(planned: p.market,
                                            current: appState.selectedMarket) {
            error = refusal
            return
        }
        // Scoped to the market the plan was resolved in, not to the picker.
        let intent = appState.marketIntent(
            for: p.market,
            title: "\(p.verbLabel) \(p.selectionLabel) everywhere",
            arguments: ["everywhere-apply"], stdin: p.stdin,
            cardinality: .bulk, responseKind: .everywhereApply)
        do {
            let receipt = try await appState.actionCoordinator.execute(
                intent, context: appState.actionPolicyContext, confirmed: true)
            guard !receipt.rehearsed else { return }
            guard case .everywhereApply(let applied, let skipped, let failed) = receipt.result else {
                error = "The engine returned no everywhere result."
                return
            }
            let summary = EverywhereApplySummary(applied: applied, skipped: skipped, failed: failed)
            if applied > 0 { await onApplied() }
            if summary.isPartialFailure {
                error = "Partial failure. \(summary.message) Check the Audit trail for the rejected rows."
            } else {
                result = summary.message
            }
        } catch {
            self.error = error.localizedDescription
        }
    }
}
