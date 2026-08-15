import SwiftUI
import UniformTypeIdentifiers

/// The Sales sub-tab of Import: banks the dated Merch sales report — the ONLY
/// source of organic royalty (the Ads API reports ad-attributed sales only).
/// Rows accumulate per day across every import, so this screen also answers
/// what history is actually banked and where the holes are.
///
/// The catalogue export is NOT handled here — it is New Designs' workflow, so
/// a catalogue export dropped here is handed back to the container. Likewise
/// an Ads console history export belongs to the Ads sub-tab; a sales report
/// that fails to parse hints at that rather than silently falling through.
struct SalesImportView: View {
    @Environment(AppState.self) private var appState

    /// A file handed over from another sub-tab (New Designs or Ads). Non-nil
    /// only when this sub-tab is the cross-route target.
    var incomingFile: URL? = nil
    /// Call once the incoming file has been banked, so the container clears it.
    var onConsumeIncoming: () -> Void = {}
    /// A catalogue export was dropped here — hand it back so the container can
    /// switch to New Designs and load it there.
    var onMisroutedExport: (URL) -> Void = { _ in }
    /// False when hosted inside `ImportHubView`: the container renders the single
    /// page header and owns the navigation title, so this view's own copies stay
    /// suppressed to avoid a duplicate "Import" header and a title fight.
    var showsHeader: Bool = true

    @State private var history: SalesHistoryResponse?
    @State private var status: String?
    @State private var statusIsWarning = false
    @State private var errorText: String?
    @State private var isWorking = false
    @State private var showingPicker = false
    @State private var isDropTargeted = false

    private static let howToSteps = [
        "Amazon Merch on Demand → Analyze → Products.",
        "Set the date range (From / To); Marketplace: All.",
        "Click Download CSV.",
    ]

    var body: some View {
        if showsHeader {
            content.navigationTitle("Sales")
        } else {
            content
        }
    }

    private var content: some View {
        VStack(alignment: .leading, spacing: 0) {
            if showsHeader {
                PageHeader(title: "Sales", subtitle: subtitle, help: .dataImport)
            }
            if showsHeader { Divider() }
            ScrollView {
                LazyVStack(alignment: .leading, spacing: Layout.Spacing.xl) {
                    lastRecordedLine
                    HowToGet(title: "How to get this file", steps: Self.howToSteps)
                    dropZone
                    if let errorText {
                        card(title: "Import failed", detail: errorText, tint: Theme.Colors.critical)
                    } else if let status {
                        card(title: statusIsWarning ? "Nothing changed" : "Imported",
                             detail: status,
                             tint: statusIsWarning ? Theme.Colors.caution : Theme.Colors.positive)
                    }
                    gapsSection
                    importsSection
                }
                .padding(Layout.Spacing.lg)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .background(Theme.Colors.canvas)
        .task(id: appState.viewKey) { await loadHistory() }   // market switch reloads — the banked coverage is per-market
        .task(id: incomingFile) {
            guard let url = incomingFile else { return }
            await importSalesReport(url)
            onConsumeIncoming()
        }
        .fileImporter(isPresented: $showingPicker,
                      allowedContentTypes: [.commaSeparatedText, .plainText, .data],
                      allowsMultipleSelection: false) { result in
            switch result {
            case .success(let urls):
                if let url = urls.first { Task { await handle(url) } }
            case .failure(let error):
                errorText = error.localizedDescription
            }
        }
    }

    private var subtitle: String {
        guard let cov = history?.coverage, cov.days > 0,
              let first = cov.firstDay, let last = cov.lastDay else {
            return "organic royalty · nothing banked yet"
        }
        return "organic royalty · \(cov.days) days banked · \(Format.euDate(first))–\(Format.euDate(last))"
    }

    private var lastRecordedText: String {
        guard let last = history?.coverage?.lastDay else { return "Nothing imported yet" }
        return Format.euDate(last)
    }

    private var lastRecordedLine: some View {
        HStack(spacing: Layout.Spacing.xs) {
            Image(systemName: "calendar")
                .foregroundStyle(Theme.Colors.muted)
            Text("Last recorded:")
                .font(.callout)
                .foregroundStyle(Theme.Colors.textSecondary)
            Text(lastRecordedText)
                .font(.callout.weight(.semibold))
                .foregroundStyle(Theme.Colors.textPrimary)
            Spacer()
        }
        .padding(Layout.Spacing.sm)
        .frame(maxWidth: .infinity, alignment: .leading)
        .mdCard()
    }

    private var dropZone: some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.xs) {
            HStack(spacing: Layout.Spacing.sm) {
                Image(systemName: "square.and.arrow.down.on.square")
                    .font(.title2)
                    .foregroundStyle(.secondary)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Drop a CSV here, or choose one")
                        .font(.callout.weight(.medium))
                    Text("The Merch sales report — the only source of organic royalty. Every import adds to the history rather than replacing it.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: Layout.Spacing.sm)
                if isWorking { ProgressView().controlSize(.small) }
                Button("Choose CSV…") { showingPicker = true }
                    .disabled(isWorking)
            }
        }
        .padding(Layout.Spacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.Colors.surface, in: RoundedRectangle(cornerRadius: Layout.Radius.large))
        .overlay {
            RoundedRectangle(cornerRadius: Layout.Radius.large)
                .strokeBorder(isDropTargeted ? Theme.Colors.accent : Theme.Colors.separator,
                              style: StrokeStyle(lineWidth: isDropTargeted ? 2 : 1,
                                                 dash: isDropTargeted ? [] : [5, 4]))
        }
        .dropDestination(for: URL.self) { urls, _ in
            guard let url = urls.first else { return false }
            Task { await handle(url) }
            return true
        } isTargeted: { isDropTargeted = $0 }
    }

    /// Gaps only — missing days in the banked royalty understate it as a slump.
    /// Continuous history shows a quiet green confirmation.
    @ViewBuilder
    private var gapsSection: some View {
        if let cov = history?.coverage, cov.days > 0 {
            let gaps = cov.gaps ?? []
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: Layout.Spacing.xs) {
                    Image(systemName: gaps.isEmpty ? "checkmark.circle" : "exclamationmark.triangle")
                        .foregroundStyle(gaps.isEmpty ? Theme.Colors.positive : Theme.Colors.caution)
                    Text(gaps.isEmpty
                         ? "No gaps — \(cov.days) days banked, continuous"
                         : "\(gaps.count) gap\(gaps.count == 1 ? "" : "s") — missing days understate royalty")
                        .font(.callout)
                        .foregroundStyle(gaps.isEmpty ? Theme.Colors.positive : Theme.Colors.caution)
                    Spacer()
                }
                if !gaps.isEmpty {
                    ForEach(gaps) { gap in
                        Text("\(Format.euDate(gap.start)) – \(Format.euDate(gap.end))")
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .padding(Layout.Spacing.sm)
            .frame(maxWidth: .infinity, alignment: .leading)
            .mdCard()
        }
    }

    /// The ledger: every sales file ever banked, newest first. The engine has
    /// always sent it; the screen used to drop it, so "did I already import
    /// May?" had no answer.
    @ViewBuilder
    private var importsSection: some View {
        if let imports = history?.imports, !imports.isEmpty {
            VStack(alignment: .leading, spacing: 4) {
                Text("Imports (\(imports.count))")
                    .font(.callout.weight(.semibold))
                ForEach(imports.reversed()) { entry in
                    HStack(spacing: Layout.Spacing.xs) {
                        Text(entry.filename)
                            .font(.caption.monospacedDigit())
                            .lineLimit(1)
                            .truncationMode(.middle)
                        Spacer()
                        if let start = entry.periodStart, let end = entry.periodEnd {
                            Text("\(Format.euDate(start)) – \(Format.euDate(end))")
                                .font(.caption.monospacedDigit())
                                .foregroundStyle(.secondary)
                        }
                        if let banked = entry.rowsBanked {
                            Text("\(Format.count(banked)) rows")
                                .font(.caption.monospacedDigit())
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
            .padding(Layout.Spacing.sm)
            .frame(maxWidth: .infinity, alignment: .leading)
            .mdCard()
        }
    }

    private func card(title: String, detail: String, tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.callout.weight(.semibold)).foregroundStyle(tint)
            Text(detail)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(Layout.Spacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .mdCard()
    }

    /// Route by what the file actually is. A catalogue export is not banked here —
    /// it belongs to the build workflow, so hand it back to the container.
    private func handle(_ url: URL) async {
        errorText = nil
        status = nil
        if ImportFileKind.classify(filename: url.lastPathComponent) == .catalogExport {
            onMisroutedExport(url)
            // Pre-container fallback hint (harmless once cross-route switches away).
            statusIsWarning = true
            status = "That looks like a catalogue export — open New Designs to route and build it."
            return
        }
        await importSalesReport(url)
    }

    private func importSalesReport(_ url: URL) async {
        isWorking = true
        defer { isWorking = false }
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        do {
            let bridge = try appState.makeBridge()
            let response = try await bridge.call(
                SalesReportResponse.self,
                ["sales-report", "--import", url.path],
                market: appState.selectedMarket)
            if let banked = response.banked {
                statusIsWarning = (banked.newRows ?? 0) == 0
                status = "\(response.file?.filename ?? url.lastPathComponent): "
                    + "\(Format.count(banked.newRows ?? 0)) new day-rows banked, "
                    + "\(Format.count(banked.totalRows ?? 0)) in total."
            } else {
                statusIsWarning = true
                status = "Copied, but nothing was banked — check the engine log."
            }
            await loadHistory()
        } catch {
            // A file that isn't a Merch sales report most often IS the Ads
            // console monthly export — point at the tab that actually reads it.
            let message = error.localizedDescription
            if message.contains("no Merch sales rows") {
                errorText = "This doesn't look like a Merch sales report — if it's an Ads report, use the Ads tab."
            } else {
                errorText = message
            }
        }
    }

    private func loadHistory() async {
        do {
            let bridge = try appState.makeBridge()
            history = try await bridge.call(SalesHistoryResponse.self, ["sales-history"],
                                            market: appState.selectedMarket)
        } catch {
            errorText = error.localizedDescription
        }
    }
}

#Preview {
    SalesImportView()
        .environment(AppState())
}
