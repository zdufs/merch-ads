import SwiftUI

/// The actions panel: the KILL freeze switch, reset-inflated-bids, and manual
/// run triggers. Every write goes through appctl (KILL-guarded, logged).
struct ActionsView: View {
    @Environment(AppState.self) private var appState
    @State private var resetPreview: ResetBidsResponse?
    @State private var resetIntent: ActionIntent?
    @State private var resetPreviewReceipt: ActionPreviewReceipt?
    @State private var pendingReset: ActionIntent?
    @State private var pendingKill: ActionIntent?
    @State private var pendingApproval: ActionIntent?
    @State private var pendingGlobal: ActionIntent?
    @State private var pendingRun: PendingRunAction?
    @State private var runningPhase: String?
    @State private var backfilling = false
    @State private var backfillResult: String?
    @State private var runOutput: String?
    @State private var actionError: String?
    @State private var busy = false

    private static let phases: [(label: String, phase: String?, hint: String)] = [
        ("Pull data", "pull", "phase0: refresh campaigns + reports for this market"),
        ("Negatives & pauses", "phase2", "phase2: auto-apply reactive negatives + pauses"),
        ("Bid tuning", "phase3", "phase3: per-type bid engine"),
        ("Harvest", "harvest", "collect winning search terms for promotion"),
    ]

    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "Actions", subtitle: "\(appState.selectedMarket) · engine controls", help: .actions)
            ActionErrorBar(message: $actionError)
            ScrollView {
                LazyVStack(alignment: .leading, spacing: Layout.Spacing.lg) {
                    safetySection
                    bidsSection
                    runsSection
                    if let runOutput { outputSection(runOutput) }
                }
                .padding(Layout.Spacing.lg)
                .frame(maxWidth: 900)
                .frame(maxWidth: .infinity)
            }
            .scrollEdgeEffectStyle(.soft, for: .top)
        }
        .background(Theme.Colors.canvas)
        .task(id: appState.viewKey) { await loadResetPreview() }   // viewKey also re-fires after a nightly run (dataStamp)
        .confirmationDialog(
            pendingKill?.title ?? "",
            isPresented: Binding(get: { pendingKill != nil },
                                 set: { if !$0 { pendingKill = nil } }),
            presenting: pendingKill
        ) { intent in
            Button("Release — allow writes again", role: .destructive) {
                Task { await executeGlobal(intent, confirmed: true) }
            }
        } message: { intent in
            Text("This changes \(intent.scope.confirmationDescription). The nightly engine and app actions will be able to write again.")
        }
        .confirmationDialog(
            pendingGlobal?.title ?? "",
            isPresented: Binding(get: { pendingGlobal != nil },
                                 set: { if !$0 { pendingGlobal = nil } }),
            presenting: pendingGlobal
        ) { intent in
            Button("Apply", role: .destructive) {
                Task { await executeGlobal(intent, confirmed: true) }
            }
        } message: { intent in
            Text("Apply this change to \(intent.scope.confirmationDescription).")
        }
        .confirmationDialog(
            pendingRun?.intent.title ?? "",
            isPresented: Binding(get: { pendingRun != nil },
                                 set: { if !$0 { pendingRun = nil } }),
            presenting: pendingRun
        ) { pending in
            Button("Run", role: .destructive) {
                Task { await executeRun(pending, confirmed: true) }
            }
        } message: { pending in
            Text("Run this long engine job for \(pending.intent.scope.confirmationDescription).")
        }
        .confirmationDialog(
            pendingApproval?.title ?? "",
            isPresented: Binding(get: { pendingApproval != nil },
                                 set: { if !$0 { pendingApproval = nil } }),
            presenting: pendingApproval
        ) { intent in
            Button("Change Global Approval Mode", role: .destructive) {
                Task { await executeGlobal(intent, confirmed: true) }
            }
        } message: { intent in
            Text("This changes \(intent.scope.confirmationDescription), affecting every market and the nightly engine.")
        }
    }

    private var safetySection: some View {
        groupedSection(title: "Safety", subtitle: "global engine configuration") {
            Toggle(isOn: killBinding) {
                VStack(alignment: .leading, spacing: Layout.Spacing.xxs) {
                    Label("Freeze all writes (KILL)", systemImage: "exclamationmark.octagon.fill")
                        .foregroundStyle(appState.killActive
                            ? Theme.Colors.critical : Color.primary)
                    Text("The nightly job and every app action refuse writes until released. Use Audit Trail for per-action undo.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .toggleStyle(.switch)
            .tint(Theme.Colors.critical)

            Divider()

            Toggle(isOn: approvalBinding) {
                VStack(alignment: .leading, spacing: Layout.Spacing.xxs) {
                    Label("Require approval for negatives & pauses", systemImage: "checklist")
                    Text("Phase 2 collects proposals for the Approval Queue instead of applying automatically. Rules set to Auto still apply themselves — set them to Review to queue them too.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .toggleStyle(.switch)
        }
    }

    private var bidsSection: some View {
        groupedSection(title: "Reset inflated bids", subtitle: appState.selectedMarket) {
            HStack(spacing: Layout.Spacing.sm) {
                Text(resetSummary).font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("Preview") { Task { await loadResetPreview() } }
                    .disabled(busy)
                Button("Apply") { pendingReset = resetIntent }
                    .disabled(busy || (resetPreview?.count ?? 0) == 0)
            }
        }
        .confirmationDialog(
            "Reset \(resetPreview?.count ?? 0) inflated bids?",
            isPresented: Binding(get: { pendingReset != nil },
                                 set: { if !$0 { pendingReset = nil } })) {
            Button("Reset bids", role: .destructive) {
                if let intent = pendingReset { Task { await applyReset(intent) } }
            }
        } message: {
            Text("Restore original bids minus 10% in market \(appState.selectedMarket). Each write is logged and undoable.")
        }
    }

    private var runsSection: some View {
        groupedSection(title: "Trigger a run", subtitle: "long engine jobs") {
            ForEach(Self.phases, id: \.label) { item in
                runRow(title: item.label, hint: item.hint,
                       phase: item.phase, progressKey: item.phase ?? "")
                Divider()
            }
            runRow(title: "Full nightly run — all markets",
                   hint: "The complete scheduled workflow; can take up to an hour.",
                   phase: nil, progressKey: "full")
            Divider()
            HStack(spacing: Layout.Spacing.sm) {
                VStack(alignment: .leading, spacing: Layout.Spacing.xxs) {
                    Text("Backfill daily history")
                    Text("Re-banks ~92 days of true per-day spend/sales for this market (the API's reach). Runs automatically on Mondays; use after gaps. Several minutes; banks locally, writes nothing to Amazon.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if backfilling { ProgressView().controlSize(.small) }
                Button("Run") { Task { await runBackfill() } }
                    .disabled(backfilling || runningPhase != nil)
            }
            if let backfillResult {
                Text(backfillResult).font(.caption).foregroundStyle(.secondary)
            }
            if appState.killActive {
                StatusBadge(text: "KILL active · runs blocked",
                            symbol: "exclamationmark.octagon.fill",
                            tint: Theme.Colors.critical)
            }
        }
    }

    private func runRow(title: String, hint: String, phase: String?,
                        progressKey: String) -> some View {
        HStack(spacing: Layout.Spacing.sm) {
            VStack(alignment: .leading, spacing: Layout.Spacing.xxs) {
                Text(title)
                Text(hint).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            if runningPhase == progressKey { ProgressView().controlSize(.small) }
            Button("Run") { Task { await requestRun(phase: phase) } }
                .disabled(runningPhase != nil || appState.killActive)
        }
    }

    private func outputSection(_ output: String) -> some View {
        groupedSection(title: "Last run output", subtitle: "engine response") {
            Text(output)
                .font(.caption.monospaced())
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func groupedSection<Content: View>(title: String, subtitle: String,
                                               @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.sm) {
            SectionHeader(title: title, subtitle: subtitle)
            content()
        }
        .padding(Layout.Spacing.md)
        .mdCard()
    }

    private var resetSummary: String {
        guard let preview = resetPreview else {
            return "targets whose bid compounded above its original — resets to original −10%"
        }
        if preview.count == 0 { return "nothing net-inflated in this market right now" }
        return "\(preview.count) targets, total reduction \(Format.money(preview.totalReduction, currency: appState.currentMarket?.currency))"
    }

    private var approvalBinding: Binding<Bool> {
        Binding(
            get: { appState.approvalRequired },
            set: { newValue in
                pendingApproval = appState.globalIntent(
                    title: newValue ? "Require approval globally?" : "Disable global approval requirement?",
                    arguments: ["approval-mode", newValue ? "--on" : "--off"],
                    confirmationPolicy: .required)
            }
        )
    }

    private var killBinding: Binding<Bool> {
        Binding(
            get: { appState.killActive },
            set: { newValue in
                if newValue {
                    let intent = appState.globalIntent(
                        title: "Engage global KILL freeze",
                        arguments: ["kill", "--on"])
                    requestGlobal(intent)   // engaging the brake: no confirm unless policy asks
                } else {
                    pendingKill = appState.globalIntent(
                        title: "Release the global KILL freeze?",
                        arguments: ["kill", "--off"],
                        allowedWhenKillActive: true,
                        confirmationPolicy: .required)
                }
            }
        )
    }

    private func requestGlobal(_ intent: ActionIntent) {
        switch appState.actionCoordinator.requirement(
            for: intent, context: appState.actionPolicyContext) {
        case .confirmation:
            pendingGlobal = intent
        case .blocked(.killActive(let scope)):
            // Say it here rather than making a round trip the coordinator will refuse.
            actionError = ActionCoordinatorError.killActive(scope).localizedDescription
        case .preview, .ready:
            Task { await executeGlobal(intent) }
        }
    }

    private func executeGlobal(_ intent: ActionIntent, confirmed: Bool = false) async {
        actionError = nil
        do {
            let receipt = try await appState.actionCoordinator.execute(
                intent, context: appState.actionPolicyContext, confirmed: confirmed)
            guard !receipt.rehearsed else { return }
            await appState.refresh()
        } catch {
            actionError = error.localizedDescription
        }
    }

    private func loadResetPreview() async {
        actionError = nil
        do {
            let intent = appState.marketIntent(
                title: "Reset inflated bids",
                arguments: ["resetbids", "--apply"], cardinality: .bulk,
                preview: ActionPreview(arguments: ["resetbids"], responseKind: .resetBids),
                responseKind: .resetBids)
            let receipt = try await appState.actionCoordinator.preview(
                intent, context: appState.actionPolicyContext)
            guard case .resetBids(let response) = receipt.result else { return }
            resetIntent = intent
            resetPreviewReceipt = receipt
            resetPreview = response
        } catch {
            resetPreview = nil
            actionError = error.localizedDescription
        }
    }

    private func applyReset(_ intent: ActionIntent) async {
        busy = true
        defer { busy = false }
        actionError = nil
        do {
            let receipt = try await appState.actionCoordinator.execute(
                intent, context: appState.actionPolicyContext,
                preview: resetPreviewReceipt, confirmed: true)
            guard !receipt.rehearsed else { return }
            guard case .resetBids(let count, let reduction) = receipt.result else { return }
            runOutput = "Reset \(count) bids (reduction \(Format.money(reduction, currency: appState.currentMarket?.currency)))."
            if intent.scope.market == appState.selectedMarket { await loadResetPreview() }
        } catch {
            actionError = error.localizedDescription
        }
    }

    /// backfill-daily banks report data locally — no Amazon writes, not
    /// KILL-gated by the engine, so it runs directly (like a pull).
    private func runBackfill() async {
        backfilling = true
        defer { backfilling = false }
        actionError = nil
        backfillResult = nil
        do {
            let bridge = try appState.makeBridge()
            let r = try await bridge.call(PhaseResult.self, ["backfill-daily"],
                                          market: appState.selectedMarket,
                                          preferWorker: false)
            backfillResult = r.code == 0
                ? "Backfill finished — daily history re-banked."
                : "Backfill exited \(r.code) — tail: \(r.text.suffix(200))"
            await appState.refresh()
        } catch {
            actionError = error.localizedDescription
        }
    }

    private func requestRun(phase: String?) async {
        var args = ["run"]
        if let phase { args += ["--phase", phase] }
        let intent = phase == nil
            ? appState.allMarketsIntent(
                title: "Run full nightly workflow for all markets",
                arguments: args, responseKind: .run)
            : appState.marketIntent(
                title: "Run \(phase ?? "workflow")",
                arguments: args, responseKind: .run)
        let pending = PendingRunAction(intent: intent, phaseKey: phase ?? "full")
        switch appState.actionCoordinator.requirement(
            for: intent, context: appState.actionPolicyContext) {
        case .confirmation:
            pendingRun = pending
        case .blocked(.killActive(let scope)):
            actionError = ActionCoordinatorError.killActive(scope).localizedDescription
        case .preview, .ready:
            await executeRun(pending)
        }
    }

    private func executeRun(_ pending: PendingRunAction, confirmed: Bool = false) async {
        runningPhase = pending.phaseKey
        defer { runningPhase = nil }
        actionError = nil
        do {
            let receipt = try await appState.actionCoordinator.execute(
                pending.intent, context: appState.actionPolicyContext,
                confirmed: confirmed)
            guard !receipt.rehearsed else { return }
            guard case .run(let code, let text) = receipt.result else { return }
            runOutput = text
            if code != 0 {
                actionError = "run exited with code \(code)"
            }
            await appState.refresh()
        } catch {
            actionError = error.localizedDescription
        }
    }
}

private struct PendingRunAction: Identifiable {
    let intent: ActionIntent
    let phaseKey: String
    var id: UUID { intent.id }
}

#Preview {
    ActionsView()
        .environment(AppState())
}
