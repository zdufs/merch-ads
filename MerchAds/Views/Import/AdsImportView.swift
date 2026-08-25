import SwiftUI
import UniformTypeIdentifiers

/// The Ads sub-tab of Import: banks the Amazon Ads CONSOLE monthly-history
/// export — the only source that reaches past the API's ~95-day retention.
/// Once banked, it is the only copy, and it feeds the Dashboard's year-to-date
/// back-extension.
///
/// The catalogue export is NOT handled here — it is New Designs' workflow, so
/// a catalogue export dropped here is handed back to the container. Likewise
/// a Merch sales report belongs to the Sales sub-tab; a console export that
/// fails to parse hints at that rather than silently falling through.
struct AdsImportView: View {
    @Environment(AppState.self) private var appState

    /// A file handed over from another sub-tab (New Designs or Sales). Non-nil
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

    @State private var coverage: HistoryCoverage?
    @State private var status: String?
    @State private var statusIsWarning = false
    @State private var errorText: String?
    @State private var isWorking = false
    @State private var showingPicker = false
    @State private var isDropTargeted = false

    private static let howToSteps = [
        "Amazon Ads console → Reports → create a report → Customize columns.",
        "Dimensions: Month, Year, Budget currency, Country, Country code, Advertiser account ID, Advertiser account name.",
        "Metrics: Impressions, Clicks, Total cost, Sales, Purchases, Units sold.",
        "Report range: This year, then download the CSV.",
    ]

    var body: some View {
        if showsHeader {
            content.navigationTitle("Ads")
        } else {
            content
        }
    }

    private var content: some View {
        VStack(alignment: .leading, spacing: 0) {
            if showsHeader {
                PageHeader(title: "Ads", subtitle: subtitle, help: .dataImport)
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
                }
                .padding(Layout.Spacing.lg)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .background(Theme.Colors.canvas)
        .task(id: appState.viewKey) { await loadCoverage() }   // market switch reloads — the current-market group changes
        .task(id: incomingFile) {
            guard let url = incomingFile else { return }
            await importAdsHistory(url)
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
        guard let bucket = marketBucket, bucket.months > 0 else {
            return "console monthly history · nothing banked for \(appState.selectedMarket)"
        }
        return "console monthly history · \(bucket.months) months banked for \(bucket.currency)"
    }

    private var lastRecordedText: String {
        guard let last = marketBucket?.lastMonth else { return "Nothing imported yet" }
        return Format.monthName(last)
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
                    Text("The Ads console monthly-history export — the only way past Amazon's ~95-day API retention. Every import adds to the history rather than replacing it.")
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

    /// The banked series THIS market reads its history out of.
    ///
    /// The top-level coverage is the union of every currency series, so it
    /// belongs to no market. Printed as this market's own it told DE "60
    /// months banked, continuous" while DE's own table held nothing at all
    /// (2026-08-24). One console export covers every marketplace and carries
    /// no country, so DE, FR, ES and IT genuinely share one euro series.
    private var marketBucket: HistoryCoverageMarket? {
        coverage?.bucket(market: appState.selectedMarket,
                         currency: appState.currentMarket?.currency)
    }

    private var bucketIsShared: Bool {
        coverage?.bucketIsShared(market: appState.selectedMarket,
                                 currency: appState.currentMarket?.currency) ?? false
    }

    @ViewBuilder
    private var gapsSection: some View {
        if let cov = coverage {
            let bucket = marketBucket
            let gaps = bucket?.monthGaps
            let clear = bucket != nil && gaps == 0
            VStack(alignment: .leading, spacing: Layout.Spacing.xxs) {
                HStack(spacing: Layout.Spacing.xs) {
                    Image(systemName: clear ? "checkmark.circle" : "exclamationmark.triangle")
                        .foregroundStyle(clear ? Theme.Colors.positive : Theme.Colors.caution)
                    Text(bucketHeadline(bucket, gaps: gaps))
                        .font(.callout)
                        .foregroundStyle(clear ? Theme.Colors.positive : Theme.Colors.caution)
                    Spacer()
                }
                Text(bucketDetail(cov, bucket: bucket))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(Layout.Spacing.sm)
            .frame(maxWidth: .infinity, alignment: .leading)
            .mdCard()
        }
    }

    private func bucketHeadline(_ bucket: HistoryCoverageMarket?, gaps: Int?) -> String {
        guard let bucket, bucket.months > 0 else {
            return "Nothing banked for \(appState.selectedMarket) — import the console export"
        }
        let months = "\(bucket.months) month\(bucket.months == 1 ? "" : "s")"
        guard let gaps else { return "\(months) banked" }
        return gaps == 0
            ? "No gaps — \(months) banked, continuous"
            : "\(gaps) month\(gaps == 1 ? "" : "s") missing in the banked range"
    }

    private func bucketDetail(_ cov: HistoryCoverage,
                              bucket: HistoryCoverageMarket?) -> String {
        guard let bucket else {
            return "This history is banked per currency. Nothing here covers "
                + "\(appState.selectedMarket) yet, so its year-to-date reaches only as far "
                + "back as the daily pull does — about 95 days."
        }
        var text = ""
        if let first = bucket.firstMonth, let last = bucket.lastMonth {
            text += "\(Format.monthName(first))–\(Format.monthName(last)) · "
        }
        text += "\(bucket.currency) series"
        if bucketIsShared {
            text += ", shared by every \(bucket.currency) market — one console export "
                + "covers them all and carries no country"
        }
        if cov.months != bucket.months {
            text += ". \(cov.months) months are banked across all currencies."
        }
        return text
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
        if ImportFileKind.classify(url: url) == .catalogExport {
            onMisroutedExport(url)
            // Pre-container fallback hint (harmless once cross-route switches away).
            statusIsWarning = true
            status = "That looks like a catalogue export — open New Designs to route and build it."
            return
        }
        await importAdsHistory(url)
    }

    private func importAdsHistory(_ url: URL) async {
        isWorking = true
        defer { isWorking = false }
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        do {
            let bridge = try appState.makeBridge()
            let response = try await bridge.call(HistoryImportResponse.self,
                                                  ["history-import", url.path],
                                                  market: appState.selectedMarket,
                                                  preferWorker: false)
            let newRows = response.file?.newRows ?? 0
            statusIsWarning = newRows == 0
            status = "\(response.file?.filename ?? url.lastPathComponent): "
                + "\(Format.count(newRows)) new month-rows banked "
                + "(\(Format.count(response.file?.totalRows ?? 0)) total). Feeds the Dashboard's period stack."
            coverage = response.coverage ?? coverage
        } catch {
            // A wrong-file refusal most often means the Merch sales report — point
            // at the tab that reads it. Any OTHER engine message (e.g. the
            // year-ambiguity refusal) tells the user how to fix the export, so it
            // must pass through instead of being swallowed by the hint.
            let msg = error.localizedDescription
            errorText = msg.contains("not an Ads console history export")
                ? "This doesn't look like an Ads history export — if it's a sales report, use the Sales tab."
                : msg
        }
    }

    private func loadCoverage() async {
        do {
            let bridge = try appState.makeBridge()
            let response = try await bridge.call(HistoryImportResponse.self, ["history-import"],
                                                  market: appState.selectedMarket)
            coverage = response.coverage
        } catch {
            errorText = error.localizedDescription
        }
    }
}

#Preview {
    AdsImportView()
        .environment(AppState())
}
