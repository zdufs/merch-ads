import SwiftUI

/// Shared "act everywhere" flow for the Accumulated screens. Resolve a selection
/// to its instance count (so the operator sees the blast radius), confirm, then
/// apply through the ActionCoordinator — KILL-gated, every write logged and
/// undoable. Operator decisions (2026-08-09): pause never archives; negate is
/// exact. Backend: `everywhere-preview` / `everywhere-apply`.
struct EverywherePending: Identifiable, Equatable {
    let id = UUID()
    let kind: String        // "asin" | "keyword"
    let action: String      // "pause" | "negate" | "setbid"
    let keys: [String]
    let verbLabel: String   // "Pause" | "Negate" | "Set bid"
    let noun: String        // "ASIN" | "keyword"
    var match: String = "exact"     // negate: exact | phrase
    var bid: Double?                 // setbid: the bid to write
    var applicable: Int?    // instances that would actually change (from preview)
    var campaigns: Int?
    var skipped: Int?       // already-paused no-ops

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

/// Fetch the resolved instance count for a selection so the confirm sheet can
/// state the blast radius. Returns a pending even if the preview call fails — the
/// dialog still works, just without exact numbers.
@MainActor
func resolveEverywhere(_ appState: AppState, kind: String, action: String,
                       keys: [String], verbLabel: String, noun: String,
                       match: String = "exact", bid: Double? = nil) async -> EverywherePending? {
    guard !keys.isEmpty else { return nil }
    var p = EverywherePending(kind: kind, action: action, keys: keys,
                              verbLabel: verbLabel, noun: noun, match: match, bid: bid)
    do {
        let bridge = try appState.makeBridge()
        let r = try await bridge.call(EverywherePreviewResponse.self, ["everywhere-preview"],
                                      market: appState.selectedMarket, stdin: p.stdin,
                                      preferWorker: false)
        p.applicable = r.applicable
        p.campaigns = r.campaigns
        p.skipped = r.skippedNoop
    } catch {
        // leave counts nil; the dialog degrades gracefully
    }
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
        if let sk = p.skipped, sk > 0 { s += " \(sk) already at that state — skipped." }
        s += " Reversible from the Audit trail."
        return s
    }

    private func apply(_ p: EverywherePending) async {
        pending = nil
        let intent = appState.marketIntent(
            title: "\(p.verbLabel) \(p.selectionLabel) everywhere",
            arguments: ["everywhere-apply"], stdin: p.stdin,
            cardinality: .bulk, responseKind: .everywhereApply)
        do {
            let receipt = try await appState.actionCoordinator.execute(
                intent, context: appState.actionPolicyContext, confirmed: true)
            guard !receipt.rehearsed else { return }
            await onApplied()
        } catch {
            self.error = error.localizedDescription
        }
    }
}
