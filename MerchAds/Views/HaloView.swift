import SwiftUI

/// Organic halo — does advertising a design move its ORGANIC royalty?
///
/// For each advertised design, this windows the dated Merch sales report to the
/// design's ad-serving period and compares the royalty rate after ads started
/// against that design's own pre-ad baseline. The difference is `halo_est`.
///
/// **It is an upper bound.** The comparison is correlational, not causal — a
/// design that started selling for a seasonal reason gets the same shape as one
/// the ads genuinely lifted, which is what the `peak-before-ad` and
/// `no-ad-traffic` flags are for. Treat it as a question, not an answer.
///
/// This outlived the TAMAS method it was built for (removed 2026-08-06): the
/// question "do ads move organic sales?" applies to every campaign type, and
/// the Ads API can never answer it because it reports ad-attributed sales only.
/// Backed by the `halo` read endpoint (US-only — the Merch sales report is).
struct HaloView: View {
    @Environment(AppState.self) private var appState

    @State private var halo: HaloResponse?
    @State private var loadError: String?
    @State private var isLoading = false
    @State private var filterText = ""
    @State private var selection = Set<HaloDesign.ID>()
    @State private var colPrefs: TableColumnCustomization<HaloDesign> = ColumnPrefs.load(TableID.halo)
    @State private var sort = SortPrefs.load(
        TableID.halo, fields: sortFields,
        fallback: [KeyPathComparator(\HaloDesign.adSpend, order: .reverse)])

    private static let sortFields: [String: KeyPathComparator<HaloDesign>] = [
        "spend": .init(\.adSpend), "clicks": .init(\.adClicks),
        "halo": .init(\.haloEst), "base": .init(\.baseRate),
    ]

    private var currency: String? { appState.currentMarket?.currency }
    private var isSupported: Bool { halo?.supported ?? true }

    private var rows: [HaloDesign] {
        (halo?.designs ?? [])
            .filter { filterText.isEmpty
                || $0.asin.localizedStandardContains(filterText)
                || $0.label.localizedStandardContains(filterText) }
            .sorted(using: sort)
    }

    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "Organic Halo", subtitle: navigationSubtitle, help: .halo)
            // Everything below is computed from the sales report. Importing it
            // now lives on the Import tab, so this screen just points there.
            Button {
                appState.requestedRoute = .screen(.dataImport)
            } label: {
                Label("Sales reports are imported on the Import tab", systemImage: "tray.and.arrow.down")
                    .font(.caption)
            }
            .buttonStyle(.link)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, Layout.Spacing.lg)
            .padding(.bottom, Layout.Spacing.xs)
            statusBand
            filterBar
            SectionHeader(title: "Halo — advertised designs",
                          subtitle: "incremental royalty over each design's own pre-ad baseline",
                          count: rows.count)
                .padding(.horizontal, Layout.Spacing.sm)
            Divider()
            content
        }
        .background(Theme.Colors.canvas)
        .task(id: appState.viewKey) { await load() }
        .onChange(of: colPrefs) { ColumnPrefs.save(TableID.halo, colPrefs) }
        .onChange(of: sort) { SortPrefs.save(TableID.halo, sort, fields: Self.sortFields) }
    }

    private var navigationSubtitle: String {
        guard isSupported else { return "\(appState.selectedMarket) · needs the Merch sales report (US)" }
        if let s = halo?.reportStart, let e = halo?.reportEnd {
            return "US · sales \(Format.euDate(s)) → \(Format.euDate(e))"
        }
        return "US · organic lift over each design's pre-ad baseline"
    }

    private var statusBand: some View {
        let measured = rows.filter { ($0.flags ?? "").isEmpty }
        let lifted = measured.filter { $0.haloEst > 0 }
        return HStack(spacing: Layout.Spacing.sm) {
            StatCard(title: "Designs measured", value: Format.count(rows.count),
                     symbol: "waveform.path.ecg",
                     subtitle: "advertised, with a sales-report window")
                .mdCard()
            StatCard(title: "Clean reads", value: Format.count(measured.count),
                     symbol: "checkmark.seal",
                     subtitle: "no baseline-confound flag")
                .mdCard()
            StatCard(title: "Estimated lift",
                     value: lifted.isEmpty ? "—" : Format.count(lifted.count),
                     tint: lifted.isEmpty ? Theme.Colors.muted : Theme.Colors.positive,
                     symbol: "arrow.up.right",
                     subtitle: "positive halo, upper bound")
                .mdCard()
        }
        .fixedSize(horizontal: false, vertical: true)
        .padding(.horizontal, Layout.Spacing.lg)
        .padding(.vertical, Layout.Spacing.sm)
    }

    private var filterBar: some View {
        FilterBar {
            Text("Halo is an upper-bound estimate — correlational, not causal")
                .font(.caption2)
                .foregroundStyle(Theme.Colors.caution)
        } trailing: {
            if isSupported {
                TextField("Filter by ASIN or design", text: $filterText)
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 180)
                exportButton
            }
        }
    }

    @ViewBuilder
    private var exportButton: some View {
        if !rows.isEmpty {
            ExportButton(filename: "organic-halo-\(appState.selectedMarket)") {
                CSVDocument(
                    headers: ["asin", "design", "ad_start", "ad_spend", "ad_clicks",
                              "base_per_day", "post_per_day", "halo_est", "traz_window", "flags"],
                    rows: rows.map { d in
                        [d.asin, d.title ?? d.label, d.adStart ?? "", String(d.adSpend), String(d.adClicks),
                         String(d.baseRate), String(d.postRate), String(d.haloEst),
                         d.trazWindow.map { String($0) } ?? "", d.flags ?? ""]
                    })
            }
        }
    }

    @ViewBuilder
    private var content: some View {
        if isLoading && halo == nil {
            ProgressView("Measuring organic halo…")
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if let loadError {
            ContentUnavailableView {
                Label("Halo unavailable", systemImage: "waveform.path.ecg")
            } description: {
                Text(loadError)
            } actions: {
                Button("Retry") { Task { await load() } }
            }
            .topAlignedEmptyState()
        } else if !isSupported {
            ContentUnavailableView {
                Label("Halo needs the Merch sales report", systemImage: "globe.americas")
            } description: {
                Text("Organic royalty comes from the dated Merch SALES_REPORT, which covers the US store. Switch the market picker to US, and import a report on the Import screen if none is banked.")
            }
            .topAlignedEmptyState()
        } else if rows.isEmpty {
            if !filterText.isEmpty {
                ContentUnavailableView.search(text: filterText)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ContentUnavailableView {
                    Label("No designs to measure", systemImage: "waveform.path.ecg")
                } description: {
                    Text("No advertised design has both a sales-report window and ad-serving history yet.")
                }
                .topAlignedEmptyState()
            }
        } else {
            haloTable
        }
    }

    private var haloTable: some View {
        Table(rows, selection: $selection, sortOrder: $sort.descendingFirst(), columnCustomization: $colPrefs) {
            TableColumn("Design", value: \.label) { d in
                HStack(spacing: Layout.Spacing.xs) {
                    Text(d.label).lineLimit(1).truncationMode(.tail)
                    if let flags = d.flags, !flags.isEmpty {
                        StatusBadge(text: flagShort(flags), symbol: "info.circle",
                                    tint: Theme.Colors.muted)
                            // Never let the badge shrink its own label to "seaso…";
                            // the design name is the thing that truncates instead.
                            .fixedSize()
                            .help(flagHelp(flags))
                    }
                    // Anchor the name to a consistent left edge whether or not a
                    // badge follows it.
                    Spacer(minLength: 0)
                }
            }
            .width(min: 140, ideal: 230)
            .customizationID("design")
            TableColumn("Ad start", value: \.adStartValue) { d in
                Text(d.adStart ?? "—")
                    .font(Typography.tableNumeral)
                    .foregroundStyle(d.adStart == nil ? .secondary : .primary)
            }
            .width(min: 74, ideal: 96)
            .customizationID("adstart")
            TableColumn("Ad spend", value: \.adSpend) { d in
                MoneyText(value: d.adSpend, currency: currency)
            }
            .width(min: 48, ideal: 72)
            .customizationID("adspend")
            TableColumn("Clicks", value: \.adClicks) { d in CountText(value: d.adClicks) }
                .width(min: 40, ideal: 54)
                .customizationID("clicks")
            TableColumn("Base/day", value: \.baseRate) { d in
                MoneyText(value: d.baseRate, currency: currency)
            }
            .width(min: 52, ideal: 74)
            .customizationID("base")
            TableColumn("Ad-window/day", value: \.postRate) { d in
                MoneyText(value: d.postRate, currency: currency)
            }
            .width(min: 60, ideal: 84)
            .customizationID("post")
            TableColumn("Halo est.", value: \.haloEst) { d in
                MoneyText(value: d.haloEst, currency: currency, color: haloColor(d))
            }
            .width(min: 52, ideal: 78)
            .customizationID("halo")
            TableColumn("Royalty − spend", value: \.trazWindowValue) { d in
                MoneyText(value: d.trazWindow, currency: currency,
                          color: (d.trazWindow ?? 0) >= 0 ? Theme.Colors.positive : Theme.Colors.critical)
            }
            .width(min: 60, ideal: 88)
            .customizationID("traz")
        }
        .copyableRows(rows, primaryLabel: "ASIN",
                      primary: { $0.asin },
                      row: { "\($0.asin)\t\($0.label)\t\($0.adSpend)\t\($0.baseRate)\t\($0.postRate)\t\($0.haloEst)" })
        .background(Theme.Colors.surface)
    }

    /// Halo est. is coloured only when the split is trustworthy: a `peak-before-ad`
    /// or `no-ad-traffic` flag means the number is a baseline artifact, so stay neutral.
    private func haloColor(_ d: HaloDesign) -> Color {
        let flags = d.flags ?? ""
        if flags.contains("peak-before-ad") || flags.contains("no-ad-traffic") || flags.contains("control") {
            return .secondary
        }
        return d.haloEst >= 0 ? Theme.Colors.positive : Theme.Colors.critical
    }

    private func flagShort(_ flags: String) -> String {
        if flags.contains("control") { return "control" }
        if flags.contains("peak-before-ad") { return "seasonal?" }
        if flags.contains("no-ad-traffic") { return "no clicks" }
        return "note"
    }

    /// Plain-language reason the badge is there, shown on hover. The raw flag
    /// strings say nothing on their own, and the halo number for a flagged design
    /// is a caution, not a result.
    private func flagHelp(_ flags: String) -> String {
        var parts: [String] = []
        if flags.contains("control") {
            parts.append("Control: this design was never advertised. Its royalty is the organic baseline the advertised designs are measured against, so there is no halo to estimate here.")
        }
        if flags.contains("peak-before-ad") {
            parts.append("Seasonal?: royalty peaked before the ads started, so the lift is likely seasonal rather than caused by advertising. Treat the halo estimate as a maybe.")
        }
        if flags.contains("no-ad-traffic") {
            parts.append("No clicks: the ads served but got no clicks, so any change in royalty is not ad-driven. The halo estimate is not reliable for this design.")
        }
        return parts.isEmpty ? flags : parts.joined(separator: "\n\n")
    }

    private func load() async {
        isLoading = true
        defer { if !Task.isCancelled { isLoading = false } }
        loadError = nil
        // Drop the previous market's rows BEFORE fetching: currency flips with the
        // market picker, so keeping them would render old money in the new symbol.
        halo = nil
        selection.removeAll()
        do {
            let bridge = try appState.makeBridge()
            let fresh = try await bridge.call(HaloResponse.self, ["halo"],
                                              market: appState.selectedMarket)
            guard !Task.isCancelled else { return }
            halo = fresh
        } catch {
            guard !Task.isCancelled else { return }
            halo = nil
            loadError = error.localizedDescription
        }
    }
}

#Preview {
    HaloView()
        .environment(AppState())
}
