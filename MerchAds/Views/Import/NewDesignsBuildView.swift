import SwiftUI
import UniformTypeIdentifiers

/// New-design intake: drop a product-grid export → preview which recent
/// uploads are new for this market and where they route (Lottery / Scavenger
/// cohorts) → approve → build, scoped to exactly those ASINs.
///
/// Snap for MOD exports the grid today (`snap-grid-export-*.csv`). The older
/// MerchFlow export (`export_products_*.csv`) still works — the engine reads
/// both through export_reader.py.
struct NewDesignsBuildView: View {
    @Environment(AppState.self) private var appState

    /// A catalogue export handed over from the Sales or Ads sub-tab. Non-nil
    /// only when this sub-tab is the cross-route target.
    var incomingFile: URL? = nil
    /// Call once the incoming export has been loaded, so the container clears it.
    var onConsumeIncoming: () -> Void = {}
    /// A data CSV (sales report / ads history) was dropped here — hand it back so
    /// the container can switch to Sales and bank it there.
    var onMisroutedDataCSV: (URL) -> Void = { _ in }
    /// False when hosted inside `ImportHubView`: the container renders the single
    /// page header and owns the navigation title, so this view's own copies stay
    /// suppressed to avoid a duplicate "Import" header and a title fight.
    var showsHeader: Bool = true

    @State private var csvURL: URL?
    @AppStorage("intake.days") private var days = 14
    @State private var preview: ImportPreviewResponse?
    @State private var approved = Set<IntakeDesign.ID>()
    @State private var loadError: String?
    @State private var buildError: String?      // build failures — inline, keeps the preview
    @State private var adoptWarning: String?    // adopt-export failed after Build All
    // Dropping a product export takes its data in — that is the whole point of
    // the drop, so it happens on its own rather than behind a button.
    @State private var adopting = false
    @State private var adoptResult: AdoptedExport?
    @State private var adoptError: String?
    @State private var adoptedPath: String?     // guard: take each file in once
    @State private var isLoading = false
    @State private var previewLoadID = 0        // stale-scan guard: only the newest scan lands
    @State private var pendingBuild: ActionIntent?
    @State private var pendingBuildAll: PendingAllMarketsBuild?
    @State private var building = false
    @State private var buildResult: ImportApplyResponse?
    @State private var allResults: [MarketBuild] = []
    @State private var adoptedAfterAll: AdoptedExport?
    @State private var buildProgress: String?
    @State private var marketProgress: [MarketBuildProgress] = []
    @State private var isDropTargeted = false
    @State private var showingPicker = false
    @State private var dropRejection: String?    // a rejected drop must say why
    // Row selection exists for the right-click Copy, not for approving: the
    // checkbox column stays the only thing that approves a design.
    @State private var rowSel = Set<IntakeDesign.ID>()
    @State private var exportDate: ExportDateResponse?
    @State private var exportDateLoading = false

    private static let howToSteps = [
        "Amazon Merch on Demand → open the Snap for MOD extension → Products.",
        "Select the new products you want to advertise.",
        "Three-dots menu (⋮) → Export selected data → Export full data (CSV).",
    ]

    /// Row/header heights for the per-route preview tables. Scaled so larger
    /// accessibility text doesn't clip rows.
    @ScaledMetric(relativeTo: .body) private var routeRowHeight: CGFloat = 32
    @ScaledMetric(relativeTo: .body) private var routeHeaderHeight: CGFloat = 36

    /// Past this the table scrolls inside itself instead of growing forever —
    /// a 90-day window can route hundreds of designs into one cohort.
    private static let maxRouteTableHeight: CGFloat = 420

    /// What the file picker accepts (:79) — drag-and-drop must accept the same.
    private static let acceptedTypes: [UTType] = [.commaSeparatedText, .plainText]

    private static func isAcceptedExport(_ url: URL) -> Bool {
        guard let type = UTType(filenameExtension: url.pathExtension.lowercased()) else {
            return false
        }
        return acceptedTypes.contains { type.conforms(to: $0) }
    }

    struct MarketBuild: Identifiable {
        let market: String
        let response: ImportApplyResponse
        var id: String { market }
    }

    struct MarketBuildProgress: Identifiable {
        enum State { case waiting, running, complete, partial, failed }
        let market: String
        var state: State
        var detail: String?
        var id: String { market }
    }

    var body: some View {
        if showsHeader {
            content
                .navigationTitle("New Designs")
                .navigationSubtitle("\(appState.selectedMarket) · last \(days) days")
        } else {
            content
        }
    }

    private var content: some View {
        VStack(spacing: 0) {
            if showsHeader {
                PageHeader(title: "New Designs", subtitle: appState.selectedMarket, help: .dataImport)
            }
            // The workflow chrome (stage cards, toolbar) only makes sense once a
            // file is chosen. Before that, show just the drop zone so the empty
            // state reads as one clear call to action instead of placeholder cards.
            if csvURL != nil {
                statusBand
                header
                ActionErrorBar(message: $buildError)
                ActionErrorBar(message: $adoptError)
                if let adoptResult {
                    Label(adoptSummary(adoptResult), systemImage: "checkmark.seal.fill")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, Layout.Spacing.md)
                        .padding(.vertical, Layout.Spacing.xs)
                }
                Divider()
            }
            if isLoading {
                ProgressView("Scanning export for \(appState.selectedMarket)…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let loadError {
                ContentUnavailableView {
                    Label("Preview failed", systemImage: "square.and.arrow.down.on.square")
                } description: {
                    Text(loadError)
                }
            } else if !allResults.isEmpty {
                allMarketsResultView
            } else if !marketProgress.isEmpty {
                allMarketsProgressView
            } else if let buildResult {
                buildResultView(buildResult)
            } else if let preview {
                previewView(preview)
            } else {
                dropZone
            }
        }
        .background(Theme.Colors.canvas)
        .onChange(of: appState.selectedMarket) {
            if csvURL != nil { Task { await loadPreview() } }
        }
        .task(id: incomingFile) {
            guard let url = incomingFile else { return }
            csvURL = url
            await loadPreview()
            onConsumeIncoming()
        }
        .fileImporter(isPresented: $showingPicker,
                      allowedContentTypes: [.commaSeparatedText, .plainText]) { result in
            if case .success(let url) = result {
                if ImportFileKind.classify(url: url) == .dataCSV {
                    onMisroutedDataCSV(url)
                } else {
                    csvURL = url
                    Task { await loadPreview() }
                }
            }
        }
    }

    private var statusBand: some View {
        let responses = allResults.map(\.response) + [buildResult].compactMap { $0 }
        let stage = Self.stageLabel(responses: responses, hasPreview: preview != nil)
        return Group {
            HStack(spacing: Layout.Spacing.sm) {
                StatCard(title: "Stage", value: stage, symbol: "square.stack.3d.up.fill")
                    .mdCard()
                StatCard(title: "New designs", value: preview.map { Format.count($0.new) } ?? "—",
                         symbol: "sparkles")
                    .mdCard()
                StatCard(title: "Approved", value: preview == nil ? "—" : Format.count(approved.count),
                         symbol: "checkmark.seal.fill")
                    .mdCard()
                StatCard(title: "Markets", value: Format.count(appState.markets.filter { $0.hasData && !$0.isKDP }.count),
                         symbol: "globe")
                    .mdCard()
            }
        }
        .padding(.horizontal, Layout.Spacing.lg)
        .padding(.vertical, Layout.Spacing.md)
    }

    private var header: some View {
        FilterBar {
            Button("Choose CSV…") { showingPicker = true }
                .help("Pick the products export (export_products_….csv) — or just drag it into the window")
            if let csvURL {
                Text(csvURL.lastPathComponent)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            Stepper("uploaded in the last \(days) days", value: $days, in: 1...90)
                .fixedSize()
                .help("The export holds your whole catalog — this window keeps the preview to recent uploads only")
                .onChange(of: days) {
                    if csvURL != nil { Task { await loadPreview() } }
                }
        } trailing: {
            if preview != nil {
                Button("Rescan") { Task { await loadPreview() } }
                    .help("Re-read the file and recompute the routing plan")
                Button {
                    requestBuild()
                } label: {
                    if building {
                        HStack(spacing: Layout.Spacing.xs) {
                            ProgressView().controlSize(.small)
                            Text("Building…")
                        }
                    } else {
                        Text("Build \(approved.count) Approved")
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(approved.isEmpty || building || appState.killActive)
                .confirmationDialog(pendingBuild?.title ?? "",
                                    isPresented: Binding(get: { pendingBuild != nil },
                                                         set: { if !$0 { pendingBuild = nil } }),
                                    presenting: pendingBuild) { intent in
                    Button("Build on Amazon", role: .destructive) {
                        Task { await build(intent) }
                    }
                } message: { intent in
                    Text("Build real campaigns and ads for \(intent.scope.confirmationDescription). The approved ASIN set and market are frozen in this intent.")
                }
                Button {
                    requestBuildAllMarkets()
                } label: {
                    if building && buildProgress != nil {
                        HStack(spacing: Layout.Spacing.xs) {
                            ProgressView().controlSize(.small)
                            Text(buildProgress ?? "")
                        }
                    } else {
                        Text("Build All Markets…")
                    }
                }
                .disabled(building || appState.killActive)
                .help("Loops every market with data and builds ALL its new designs from this window — no per-design selection, same as the nightly job would")
                .confirmationDialog("Build new designs in ALL markets?",
                                    isPresented: Binding(get: { pendingBuildAll != nil },
                                                         set: { if !$0 { pendingBuildAll = nil } }),
                                    presenting: pendingBuildAll) { pending in
                    Button("Build everywhere", role: .destructive) {
                        Task { await buildAllMarkets(pending) }
                    }
                } message: { pending in
                    Text("One frozen market-scoped intent will run for each of \(pending.intents.count) markets. The export is adopted globally after successful builds.")
                }
            }
            if appState.killActive {
                StatusBadge(text: "KILL", symbol: "exclamationmark.octagon.fill",
                            tint: Theme.Colors.critical)
            }
        }
    }

    private var dropZone: some View {
        // Top-aligned under the tabs — not vertically centered, which left the
        // whole group stranded near the bottom of a tall window.
        // Centred by an HStack with Spacers, and the column takes a FIXED
        // width. `.frame(maxWidth: 480).frame(maxWidth: .infinity)` on one view
        // is the self-referential pattern this codebase has been bitten by
        // before: the column proposes one width and is then offered another,
        // and expanding the How-to disclosure inside it scrolled the whole
        // window — sidebar included — so the card was clipped off the top.
        HStack(spacing: 0) {
            Spacer(minLength: 0)
            VStack(spacing: Layout.Spacing.md) {
                exportDateLine
                HowToGet(title: "How to get this file", steps: Self.howToSteps)
                dropTarget
                Spacer(minLength: 0)
            }
            .frame(width: 480)
            Spacer(minLength: 0)
        }
        .padding(Layout.Spacing.lg)
        .task { await loadExportDate() }
    }

    private var exportDateText: String {
        guard let exportDate else { return "Catalogue status unavailable" }
        guard exportDate.available, let last = exportDate.lastRecorded else {
            return exportDate.note ?? "No catalogue on file"
        }
        return Format.euDate(last)
    }

    private var exportDateLine: some View {
        HStack(spacing: Layout.Spacing.xs) {
            Image(systemName: "calendar")
                .foregroundStyle(Theme.Colors.muted)
            Text("Last recorded:")
                .font(.callout)
                .foregroundStyle(Theme.Colors.textSecondary)
            if exportDateLoading {
                ProgressView().controlSize(.small)
                Text("reading catalogue…")
                    .font(.callout.weight(.semibold))
                    .foregroundStyle(Theme.Colors.textPrimary)
            } else {
                Text(exportDateText)
                    .font(.callout.weight(.semibold))
                    .foregroundStyle(Theme.Colors.textPrimary)
            }
            Spacer()
        }
        .padding(Layout.Spacing.sm)
        .frame(maxWidth: .infinity, alignment: .leading)
        .mdCard()
    }

    private func loadExportDate() async {
        exportDateLoading = true
        defer { exportDateLoading = false }
        do {
            let bridge = try appState.makeBridge()
            exportDate = try await bridge.call(ExportDateResponse.self, ["export-date"],
                                               market: appState.selectedMarket)
        } catch {
            exportDate = nil
        }
    }

    /// What the drop did to the price catalog: running, or the result.
    @ViewBuilder private var catalogBand: some View {
        if adopting {
            HStack(spacing: Layout.Spacing.xs) {
                ProgressView().controlSize(.small)
                Text("Reading the new prices in and rebuilding the product map… a few minutes. You can review the routing below while it runs.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, Layout.Spacing.md)
            .padding(.vertical, Layout.Spacing.xs)
        } else if let adoptResult {
            Label(adoptSummary(adoptResult),
                  systemImage: adoptResult.remapFailed
                      ? "exclamationmark.triangle.fill" : "checkmark.seal.fill")
                .font(.callout)
                .foregroundStyle(adoptResult.remapFailed
                                 ? Theme.Colors.caution : Theme.Colors.muted)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, Layout.Spacing.md)
                .padding(.vertical, Layout.Spacing.xs)
        }
    }

    private var dropTarget: some View {
        VStack(spacing: Layout.Spacing.sm) {
            Image(systemName: "square.and.arrow.down.on.square")
                .font(.system(.largeTitle)).imageScale(.large)
                .foregroundStyle(isDropTargeted
                    ? Theme.Colors.information : Theme.Colors.muted)
            Text("Drop a products export CSV here")
                .font(.title3.weight(.semibold))
            Text("snap-grid-export-….csv — the products you selected in Snap for MOD. A MerchFlow export_products_….csv still works.")
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            Text("Dropping a file reads its prices in and rebuilds the product map, so break-even ACOS follows them. Nothing is created on Amazon until you press Build.")
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            Button("Choose CSV…") { showingPicker = true }
                .buttonStyle(.borderedProminent)
                .padding(.top, Layout.Spacing.xxs)
            Label("Only designs uploaded in the last \(days) days are previewed — you can widen the window after dropping.",
                  systemImage: "calendar")
                .font(.caption)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            if let dropRejection {
                Label(dropRejection, systemImage: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundStyle(Theme.Colors.caution)
                    .multilineTextAlignment(.center)
            }
        }
        .padding(Layout.Spacing.xl)
        .frame(maxWidth: 480)
        .frame(minHeight: 240)
        .background(isDropTargeted ? Theme.Colors.accent.opacity(0.06) : Theme.Colors.surface)
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .strokeBorder(isDropTargeted ? Theme.Colors.accent : Theme.Colors.separator,
                              style: StrokeStyle(lineWidth: isDropTargeted ? 2 : 1,
                                                 dash: isDropTargeted ? [] : [6, 4]))
        )
        .dropDestination(for: URL.self) { urls, _ in
            guard let url = urls.first else {
                rejectDrop("That drop carried no file.")
                return false
            }
            guard Self.isAcceptedExport(url) else {
                rejectDrop("“\(url.lastPathComponent)” isn't a text export. Drop a .csv (or .txt) products export.")
                return false
            }
            if ImportFileKind.classify(url: url) == .dataCSV {
                onMisroutedDataCSV(url)
                rejectDrop("That looks like a sales report — banking it under Sales.")
                return false
            }
            dropRejection = nil
            csvURL = url
            Task { await loadPreview() }
            return true
        } isTargeted: { targeted in
            isDropTargeted = targeted
        }
    }

    private func rejectDrop(_ message: String) {
        dropRejection = message
        Task {
            try? await Task.sleep(for: .seconds(6))
            if dropRejection == message { dropRejection = nil }
        }
    }

    @ViewBuilder
    private func previewView(_ preview: ImportPreviewResponse) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: Layout.Spacing.sm) {
                SectionHeader(title: "Routing preview",
                              subtitle: "\(preview.designsInMarket) recent · \(preview.alreadyAdvertised) already advertised",
                              count: preview.new)
                Text("\(preview.designsInMarket) recent in \(preview.market) · \(preview.alreadyAdvertised) already advertised")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if let note = preview.usLotteryNote {
                    Text(note)
                        .font(.caption)
                        .foregroundStyle(Theme.Colors.caution)
                }
                Spacer()
                if !preview.skippedTypes.isEmpty {
                    Text("no cohort for: " + preview.skippedTypes
                        .sorted { $0.value > $1.value }
                        .prefix(4)
                        .map { "\($0.key) (\($0.value))" }
                        .joined(separator: ", "))
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .help("Product types the engine has no scavenger cohort for — these are skipped by design")
                }
            }
            .padding(.horizontal, Layout.Spacing.sm)
            Divider()
            if preview.routes.isEmpty {
                // Anchored under the divider, full width, with a Spacer taking
                // the rest. The parent VStack is leading-aligned, so without the
                // width this sat in the left third and floated vertically in the
                // middle of an empty pane — it read as a broken screen instead
                // of a short answer.
                ContentUnavailableView {
                    Label("Nothing new", systemImage: "checkmark.seal")
                } description: {
                    Text("Every recent upload in this window is already advertised (or has no cohort).")
                }
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity)
                .padding(.top, Layout.Spacing.xl)
                Spacer(minLength: 0)
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: Layout.Spacing.md) {
                    ForEach(preview.routes) { route in
                        previewRoute(route)
                    }
                    }
                    .padding(Layout.Spacing.md)
                }
            }
        }
    }

    private func previewRoute(_ route: IntakeRoute) -> some View {
        VStack(spacing: 0) {
            HStack {
                SectionHeader(title: route.route, subtitle: "destination cohort", count: route.count)
                Button(allApproved(route) ? "Deselect All" : "Select All") {
                    toggleRoute(route)
                }
                .buttonStyle(.borderless)
            }
            // The header counts the whole route; the table and "Select All"
            // can only reach the designs this reply carried. They were the
            // same number in silence until 2026-08-24, when the engine was
            // found capping a route at 2000 — a cohort of 5,000 would have
            // built 2,000 and said nothing. The cap is off; this stays so it
            // can never be invisible again.
            if route.isTruncated {
                Label("Only \(Format.count(route.designs.count)) of \(Format.count(route.count)) "
                      + "designs came back, so \(Format.count(route.missingFromPlan)) cannot be "
                      + "selected and will get no ads. Narrow the day window and build in passes.",
                      systemImage: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundStyle(Theme.Colors.caution)
                    .padding(.horizontal, Layout.Spacing.sm)
                    .padding(.bottom, Layout.Spacing.xxs)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            Table(route.designs, selection: $rowSel) {
                TableColumn("✓") { design in
                    Toggle("", isOn: Binding(
                        get: { approved.contains(design.id) },
                        set: { on in
                            if on { approved.insert(design.id) } else { approved.remove(design.id) }
                        }))
                    .labelsHidden()
                    .accessibilityLabel("Approve \(design.asin)")
                }
                .width(min: 24, ideal: 28)
                TableColumn("ASIN") { design in
                    VStack(alignment: .leading, spacing: Layout.Spacing.xxs) {
                        AsinLink(asin: design.asin)
                        if design.adAsins.first != design.asin {
                            Text("ad: \(design.adAsins.joined(separator: ", "))")
                                .font(.caption.monospaced())
                                .foregroundStyle(Theme.Colors.caution)
                        }
                    }
                }
                .width(min: 100, ideal: 140)
                TableColumn("Type") { design in StatusBadge.campaignType(design.type) }
                    .width(min: 80, ideal: 120)
                TableColumn("Title") { design in
                    Text(design.title ?? "—").lineLimit(1).truncationMode(.tail)
                }
                TableColumn("Lifetime") { design in CountText(value: design.lifetimeSales) }
                    .width(min: 44, ideal: 70)
                TableColumn("Created") { design in
                    Text(design.created ?? "—").font(.caption.monospaced())
                }
                .width(min: 70, ideal: 110)
            }
            // Grow to fit small routes, then cap and let the Table scroll itself —
            // a fixed height per route defeats row virtualization on big cohorts.
            .frame(height: min(Self.maxRouteTableHeight,
                               CGFloat(route.designs.count) * routeRowHeight + routeHeaderHeight))
            .background(Theme.Colors.surface)
            .copyableRows(route.designs, primaryLabel: "ASIN",
                          primary: { $0.asin },
                          row: { "\($0.asin)\t\($0.type)\t\($0.title ?? "")\t\($0.lifetimeSales)\t\($0.created ?? "")" })
        }
    }

    @ViewBuilder
    private func buildResultView(_ result: ImportApplyResponse) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Layout.Spacing.md) {
                if let failure = result.builderFailureSummary {
                    StatusBadge(text: result.builderOutcome == .failure
                                ? "Build failed" : "Build partially failed",
                                symbol: "xmark.circle.fill", tint: Theme.Colors.critical)
                    Label(failure, systemImage: "xmark.circle.fill")
                        .font(.callout)
                        .foregroundStyle(Theme.Colors.critical)
                } else if let warning = result.coverage?.warning {
                    StatusBadge(text: "Built with gaps", symbol: "exclamationmark.triangle.fill",
                                tint: Theme.Colors.caution)
                    Label(warning, systemImage: "exclamationmark.triangle.fill")
                        .font(.callout)
                        .foregroundStyle(Theme.Colors.caution)
                        .frame(maxWidth: 560, alignment: .leading)
                } else if result.exportError != nil {
                    StatusBadge(text: "Built, export not adopted",
                                symbol: "exclamationmark.triangle.fill",
                                tint: Theme.Colors.caution)
                } else {
                    StatusBadge(text: "Build finished", symbol: "checkmark.seal.fill",
                                tint: Theme.Colors.positive)
                }
                if let note = result.note {
                    Text(note)
                }
                // Same cohort breakdown the all-markets rows show.
                if let cohorts = result.cohorts, !cohorts.isEmpty {
                    Text(marketSummary(result))
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
                if let lottery = result.lottery {
                    builderSection("Lottery", lottery)
                }
                if let scavenger = result.scavenger {
                    builderSection("Scavenger", scavenger)
                }
                if let export = result.export {
                    VStack(alignment: .leading, spacing: Layout.Spacing.xxs) {
                        Label("Catalog housekeeping", systemImage: "internaldrive")
                            .font(.headline)
                        Text(adoptSummary(export))
                            .font(.callout)
                            .foregroundStyle(.secondary)
                            .frame(maxWidth: 560, alignment: .leading)
                    }
                } else if let exportError = result.exportError {
                    // The campaigns WERE built, so the reply is a success and
                    // this section is the only place the failure appears. Left
                    // unsaid, the economics keep answering from the previous
                    // export and go stale days later, with nothing tying that
                    // back to an import that said it worked.
                    VStack(alignment: .leading, spacing: Layout.Spacing.xxs) {
                        Label("Catalog not updated", systemImage: "exclamationmark.triangle.fill")
                            .font(.headline)
                            .foregroundStyle(Theme.Colors.caution)
                        Text("The campaigns were built, but this export was not adopted "
                             + "as the catalog. Prices and break-evens still come from "
                             + "the previous export, and they will go stale. \(exportError)")
                            .font(.callout)
                            .foregroundStyle(.secondary)
                            .frame(maxWidth: 560, alignment: .leading)
                    }
                }
                Button("Import Another") {
                    buildResult = nil
                    preview = nil
                    csvURL = nil
                }
            }
            .padding(Layout.Spacing.md)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var allMarketsResultView: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Layout.Spacing.md) {
                let gapped = allResults.filter { $0.response.coverage?.warning != nil }
                let failed = allResults.filter { $0.response.builderOutcome != .success }
                if !failed.isEmpty {
                    StatusBadge(text: "\(failed.count) of \(allResults.count) markets have builder failures",
                                symbol: "xmark.circle.fill", tint: Theme.Colors.critical)
                } else if gapped.isEmpty {
                    StatusBadge(text: "Built in \(allResults.count) markets",
                                symbol: "checkmark.seal.fill", tint: Theme.Colors.positive)
                } else {
                    StatusBadge(text: "\(gapped.count) of \(allResults.count) markets built with gaps",
                                symbol: "exclamationmark.triangle.fill", tint: Theme.Colors.caution)
                }
                VStack(spacing: 0) {
                    ForEach(Array(allResults.enumerated()), id: \.element.id) { index, build in
                        if index > 0 { Divider() }
                        VStack(alignment: .leading, spacing: Layout.Spacing.xxs) {
                            let warning = build.response.coverage?.warning
                            let builderFailed = build.response.builderOutcome != .success
                            HStack(spacing: Layout.Spacing.sm) {
                                StatusBadge(text: build.market,
                                            symbol: builderFailed ? "xmark.circle.fill"
                                                : warning == nil ? "checkmark.circle.fill"
                                                : "exclamationmark.triangle.fill",
                                            tint: builderFailed ? Theme.Colors.critical
                                                : warning == nil ? Theme.Colors.positive
                                                : Theme.Colors.caution)
                                Text(marketSummary(build.response))
                                    .foregroundStyle(.secondary)
                                Spacer()
                            }
                            // A market that built four of its five cohorts must not
                            // read the same as one that built all five.
                            if let warning {
                                Text(warning)
                                    .font(.caption)
                                    .foregroundStyle(Theme.Colors.caution)
                                    .frame(maxWidth: 520, alignment: .leading)
                            }
                            if let failure = build.response.builderFailureSummary {
                                Text(failure)
                                    .font(.caption)
                                    .foregroundStyle(Theme.Colors.critical)
                            }
                        }
                        .padding(.vertical, Layout.Spacing.xs)
                    }
                }
                .frame(maxWidth: 560)
                if let adopted = adoptedAfterAll {
                    VStack(alignment: .leading, spacing: Layout.Spacing.xxs) {
                        Label("Catalog housekeeping", systemImage: "internaldrive")
                            .font(.headline)
                        Text(adoptSummary(adopted))
                            .font(.callout)
                            .foregroundStyle(.secondary)
                            .frame(maxWidth: 560, alignment: .leading)
                    }
                }
                if let adoptWarning {
                    Label(adoptWarning, systemImage: "exclamationmark.triangle.fill")
                        .font(.callout)
                        .foregroundStyle(Theme.Colors.caution)
                        .frame(maxWidth: 560, alignment: .leading)
                }
                Button("Import Another") {
                    allResults = []
                    marketProgress = []
                    adoptedAfterAll = nil
                    buildResult = nil
                    preview = nil
                    csvURL = nil
                }
            }
            .padding(Layout.Spacing.md)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var allMarketsProgressView: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 0) {
                SectionHeader(title: "All-market build progress",
                              subtitle: "one frozen coordinator intent per market",
                              count: marketProgress.count)
                ForEach(marketProgress) { progress in
                    Divider()
                    HStack(spacing: Layout.Spacing.sm) {
                        Text(progress.market).fontWeight(.semibold)
                            .frame(minWidth: 44, alignment: .leading)
                        progressBadge(progress.state)
                        if let detail = progress.detail {
                            Text(detail).font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                    }
                    .padding(.vertical, Layout.Spacing.xs)
                }
            }
            .padding(Layout.Spacing.lg)
            .frame(maxWidth: 640, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func progressBadge(_ state: MarketBuildProgress.State) -> StatusBadge {
        switch state {
        case .waiting:
            StatusBadge(text: "Waiting", symbol: "clock", tint: Theme.Colors.muted)
        case .running:
            StatusBadge(text: "Building", symbol: "gearshape.2.fill", tint: Theme.Colors.information)
        case .complete:
            StatusBadge(text: "Complete", symbol: "checkmark.circle.fill", tint: Theme.Colors.positive)
        case .partial:
            StatusBadge(text: "Partial", symbol: "exclamationmark.triangle.fill", tint: Theme.Colors.caution)
        case .failed:
            StatusBadge(text: "Failed", symbol: "xmark.circle.fill", tint: Theme.Colors.critical)
        }
    }

    /// What a market was ASKED to build, by cohort — "1,673 designs · Tees 295 ·
    /// Drinkware 408 · Hats 328". The old row read "lottery scoped to 295 ·
    /// scavenger scoped to 1673", which describes how the builders were called
    /// and double-counts every tee.
    ///
    /// This is still the request, not the result: on 2026-08-22 it printed
    /// "Drinkware 723" for US over zero drinkware ads. The builder's own
    /// coverage report is what answers, and it is rendered beside this line.
    private func marketSummary(_ response: ImportApplyResponse) -> String {
        if let note = response.note { return note }
        var parts: [String] = []
        if let designs = response.designs { parts.append("\(Format.count(designs)) designs") }
        if let cohorts = response.cohorts, !cohorts.isEmpty {
            parts.append(contentsOf: cohorts.map { "\($0.series) \(Format.count($0.count))" })
        }
        // A builder that exited non-zero is the one thing the counts cannot show.
        for (label, builder) in [("lottery", response.lottery), ("scavenger", response.scavenger)] {
            if let builder, builder.code != 0 {
                parts.append("\(label) FAILED (exit \(builder.code))")
            }
        }
        return parts.isEmpty ? "nothing to do" : parts.joined(separator: " · ")
    }

    /// Loop every market with data: build ALL its new designs (no per-design
    /// selection — the nightly job's behavior), then adopt the export once.
    private func requestBuildAllMarkets() {
        guard let url = csvURL else { return }
        // KDP profiles have no tee campaigns — the engine refuses import-apply
        // there, so looping them would only manufacture a failure row.
        let codes = appState.markets.filter { $0.hasData && !$0.isKDP }.map(\.code)
        let intents = codes.map { market in
            appState.marketIntent(
                for: market,
                title: "Build new designs in \(market)",
                arguments: ["import-apply", url.path, "--days", String(days), "--no-adopt"],
                cardinality: .bulk,
                preview: ActionPreview(
                    arguments: ["import-preview", url.path, "--days", String(days)],
                    responseKind: .importPreview),
                responseKind: .importApply)
        }
        let adoptIntent = appState.globalIntent(
            title: "Adopt catalog export",
            arguments: ["adopt-export", url.path], responseKind: .adoptExport)
        pendingBuildAll = PendingAllMarketsBuild(
            url: url, intents: intents, adoptIntent: adoptIntent)
    }

    private func buildAllMarkets(_ pending: PendingAllMarketsBuild) async {
        building = true
        defer { building = false; buildProgress = nil }
        buildError = nil
        adoptWarning = nil
        var results: [MarketBuild] = []
        do {
            let codes = pending.intents.compactMap(\.scope.market)
            marketProgress = codes.map { MarketBuildProgress(market: $0, state: .waiting) }
            for (index, intent) in pending.intents.enumerated() {
                let market = intent.scope.market ?? "—"
                buildProgress = "Building \(market) (\(index + 1)/\(codes.count))…"
                marketProgress[index].state = .running
                do {
                    let previewReceipt = try await appState.actionCoordinator.preview(
                        intent, context: appState.actionPolicyContext)
                    let receipt = try await appState.actionCoordinator.execute(
                        intent, context: appState.actionPolicyContext,
                        preview: previewReceipt, confirmed: true)
                    guard !receipt.rehearsed else {
                        marketProgress[index].state = .failed
                        marketProgress[index].detail = "Rehearsal mode · no build executed"
                        return
                    }
                    guard case .importApply(let response) = receipt.result else { return }
                    results.append(MarketBuild(market: market, response: response))
                    switch response.builderOutcome {
                    case .success: marketProgress[index].state = .complete
                    case .partialFailure: marketProgress[index].state = .partial
                    case .failure: marketProgress[index].state = .failed
                    }
                    marketProgress[index].detail = marketSummary(response)
                } catch {
                    marketProgress[index].state = .failed
                    marketProgress[index].detail = error.localizedDescription
                    throw error
                }
            }
            buildProgress = "Adopting export…"
            do {
                let receipt = try await appState.actionCoordinator.execute(
                    pending.adoptIntent, context: appState.actionPolicyContext, confirmed: true)
                guard !receipt.rehearsed else { return }
                guard case .adoptExport(let adopted) = receipt.result else { return }
                adoptedAfterAll = adopted
                if let adopted, adopted.movedToPod, let movedPath = adopted.path {
                    // the file moved to the POD folder — use the path the engine
                    // reports, never one derived from the engine-folder setting
                    csvURL = URL(fileURLWithPath: movedPath)
                }
            } catch {
                // builds succeeded but the canonical-catalog switch didn't — say so
                adoptWarning = "Export adoption failed: \(error.localizedDescription). The engine still reads the OLD catalog; re-run adopt from a build, or check disk space."
                // Surface it on the Errors tab too — adoption failures come from a
                // successful build envelope, so the bridge choke point never sees them.
                IssueCenter.report(source: .adopt, title: "Export adoption failed",
                                   detail: adoptWarning, market: appState.selectedMarket,
                                   fix: .operatorCommand("ADS_MARKET=US python3 engine/map_products.py"))
            }
            allResults = results
        } catch {
            buildError = error.localizedDescription
            if !results.isEmpty { allResults = results }   // show what did finish
        }
    }

    /// Take a dropped export into the price catalog: move it into the POD
    /// folder and rebuild the product map on it, so break-even ACOS follows the
    /// list prices in the file.
    ///
    /// This runs on the DROP, not behind a button: dropping a product export is
    /// the operator saying "here is the new data". It creates nothing on
    /// Amazon. The catalog is global rather than per-market — `adopt-export`
    /// always re-maps under US, which is where price-aware tee economics live —
    /// so the other markets pick the file up on the nightly run.
    private func adoptCatalog() async {
        guard let url = csvURL else { return }
        let path = url.path
        guard adoptedPath != path, !adopting else { return }
        adoptedPath = path
        adoptError = nil
        adoptResult = nil
        // allowedWhenKillActive on purpose: KILL stops writes to Amazon, and
        // this writes nothing there — it reads prices into the local catalog.
        // Correct prices are what you want ready for the moment the freeze
        // lifts, and every engine write still checks KILL for itself.
        let intent = appState.globalIntent(
            title: "Add \(url.lastPathComponent) to the price catalog",
            arguments: ["adopt-export", path],
            allowedWhenKillActive: true,
            responseKind: .adoptExport)
        adopting = true
        defer { adopting = false }
        do {
            let receipt = try await appState.actionCoordinator.execute(
                intent, context: appState.actionPolicyContext, confirmed: true)
            guard !receipt.rehearsed else {
                adoptError = "Rehearsal mode · nothing was added to the catalog."
                return
            }
            guard case .adoptExport(let adopted) = receipt.result else { return }
            adoptResult = adopted
            if let adopted, adopted.remapFailed {
                IssueCenter.report(source: .adopt, title: "Product map did not rebuild",
                                   detail: "\(adopted.adopted) was added to the catalog but the US product map failed to rebuild — economics are marked STALE and prices are unchanged. \(adopted.usRemapError ?? "")",
                                   market: appState.selectedMarket,
                                   fix: .operatorCommand("ADS_MARKET=US python3 engine/map_products.py"))
            }
            if let adopted, adopted.movedToPod, let movedPath = adopted.path {
                // Use the path the ENGINE reports. Deriving it from the engine
                // folder setting was wrong: that setting points at Ads/engine,
                // so the guess landed in Ads/ and the follow-up build could not
                // find the file at all.
                let moved = URL(fileURLWithPath: movedPath)
                adoptedPath = moved.path
                csvURL = moved
            }
        } catch {
            adoptError = error.localizedDescription
            adoptedPath = nil                       // a failed take-in may be retried
            IssueCenter.report(source: .adopt, title: "Price catalog update failed",
                               detail: adoptError, market: appState.selectedMarket,
                               fix: .operatorCommand("ADS_MARKET=US python3 engine/map_products.py"))
        }
    }

    private func adoptSummary(_ export: AdoptedExport) -> String {
        var parts: [String] = []
        parts.append(export.movedToPod
            ? "\(export.adopted) was moved to the POD folder — the folder above the repo — and is part of the price catalog now (the nightly job reads it from there)."
            : "\(export.adopted) is part of the price catalog.")
        if export.remapFailed {
            let reason = export.usRemapError.map { " — \($0)" } ?? ""
            parts.append("The product map did NOT rebuild\(reason). Prices are unchanged and US economics are marked stale. Run: ADS_MARKET=US python3 engine/map_products.py")
        } else {
            parts.append("The US product map was rebuilt, so break-even ACOS follows the prices in it. The other markets pick the file up on tonight's run.")
        }
        if !export.removed.isEmpty {
            let gb = Double(export.freedMb) / 1000
            parts.append("Deleted \(export.removed.count) superseded export\(export.removed.count == 1 ? "" : "s") — freed \(String(format: "%.1f", gb)) GB.")
        }
        return parts.joined(separator: " ")
    }

    private func builderSection(_ title: String, _ result: BuilderResult) -> some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.xxs) {
            HStack {
                Text(title).font(.headline)
                Text("scoped to \(result.scopedTo) ASINs")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if result.code != 0 {
                    StatusBadge(text: "exit \(result.code)", symbol: "xmark.circle.fill",
                                tint: Theme.Colors.critical)
                } else {
                    StatusBadge(text: "complete", symbol: "checkmark.circle.fill",
                                tint: Theme.Colors.positive)
                }
            }
            Text(result.text)
                .font(.caption.monospaced())
                .textSelection(.enabled)
                .padding(Layout.Spacing.xs)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.Colors.surface,
                            in: RoundedRectangle(cornerRadius: Layout.Radius.small))
        }
    }

    private func allApproved(_ route: IntakeRoute) -> Bool {
        route.designs.allSatisfy { approved.contains($0.id) }
    }

    private func toggleRoute(_ route: IntakeRoute) {
        if allApproved(route) {
            route.designs.forEach { approved.remove($0.id) }
        } else {
            route.designs.forEach { approved.insert($0.id) }
        }
    }

    private func loadPreview() async {
        guard let csvURL else { return }
        previewLoadID += 1
        let requestID = previewLoadID   // two rapid Stepper clicks = two scans; only the newest lands
        isLoading = true
        defer { if previewLoadID == requestID { isLoading = false } }
        loadError = nil
        buildError = nil
        buildResult = nil
        allResults = []          // leaving the results screen — Rescan/market/days must work again
        marketProgress = []
        adoptedAfterAll = nil
        adoptWarning = nil
        do {
            let bridge = try appState.makeBridge()
            let response = try await bridge.call(
                ImportPreviewResponse.self,
                ["import-preview", csvURL.path, "--days", String(days)],
                market: appState.selectedMarket)
            guard previewLoadID == requestID, !Task.isCancelled else { return }
            preview = response
            approved = Set(response.routes.flatMap { $0.designs.map(\.id) })
            // Take the file's data in. Detached so the routing preview is usable
            // while the product map rebuilds, which takes a few minutes.
            Task { await adoptCatalog() }
        } catch {
            guard previewLoadID == requestID, !Task.isCancelled else { return }
            preview = nil
            loadError = error.localizedDescription
        }
    }

    private func requestBuild() {
        guard let csvURL else { return }
        do {
            let plan = ["asins": Array(approved)]
            let stdin = try JSONSerialization.data(withJSONObject: plan)
            pendingBuild = appState.marketIntent(
                title: "Build \(approved.count) approved designs",
                // --no-adopt: the drop already took this file into the catalog,
                // and a second take-in would rebuild the product map again for
                // nothing — several minutes the operator did not ask for.
                arguments: ["import-apply", csvURL.path, "--days", String(days), "--no-adopt"],
                stdin: stdin, cardinality: .bulk,
                preview: ActionPreview(
                    arguments: ["import-preview", csvURL.path, "--days", String(days)],
                    responseKind: .importPreview),
                responseKind: .importApply)
        } catch {
            buildError = error.localizedDescription
        }
    }

    private func build(_ intent: ActionIntent) async {
        building = true
        defer { building = false }
        buildError = nil
        do {
            let previewReceipt = try await appState.actionCoordinator.preview(
                intent, context: appState.actionPolicyContext)
            let receipt = try await appState.actionCoordinator.execute(
                intent, context: appState.actionPolicyContext,
                preview: previewReceipt, confirmed: true)
            guard !receipt.rehearsed else {
                buildError = "Rehearsal mode · no build executed."
                return
            }
            guard case .importApply(let response) = receipt.result else { return }
            guard intent.scope.market == appState.selectedMarket else { return }
            buildResult = response
            if let failure = response.builderFailureSummary {
                buildError = "Builder failure: \(failure)"
            }
            if let export = buildResult?.export, export.movedToPod,
               let movedPath = export.path {
                // adoption moved the file — follow it to the path the engine
                // reports, never one derived from the engine-folder setting
                self.csvURL = URL(fileURLWithPath: movedPath)
            }
        } catch {
            buildError = error.localizedDescription
        }
    }

    nonisolated static func stageLabel(responses: [ImportApplyResponse],
                                       hasPreview: Bool) -> String {
        guard !responses.isEmpty else { return hasPreview ? "Preview" : "Select export" }
        let outcomes = responses.map(\.builderOutcome)
        if outcomes.allSatisfy({ $0 == .success }) { return "Complete" }
        if outcomes.allSatisfy({ $0 == .failure }) { return "Failed" }
        return "Partial failure"
    }
}

private struct PendingAllMarketsBuild: Identifiable {
    let id = UUID()
    let url: URL
    let intents: [ActionIntent]
    let adoptIntent: ActionIntent
}

#Preview {
    NewDesignsBuildView()
        .environment(AppState())
}
