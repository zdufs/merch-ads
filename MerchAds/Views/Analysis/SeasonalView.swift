import SwiftUI

/// Seasonal scheduler: tag designs to a holiday season so the nightly job pauses
/// them once the season is over and re-enables them a few weeks ahead of the next
/// one. Re-enable only touches ad groups the scheduler itself paused, so a design
/// killed for bad performance is never resurrected by the calendar.
struct SeasonalView: View {
    @Environment(AppState.self) private var appState

    @State private var seasons: SeasonsResponse?
    @State private var preview: SeasonalPreviewResponse?
    @State private var loadError: String?
    @State private var actionError: String?
    @State private var isLoading = false
    @State private var applying = false
    @State private var pendingSeasonalApply: ActionIntent?
    @State private var pendingTagIntent: ActionIntent?
    @State private var lastResult: String?

    @State private var newAsin = ""
    @State private var newSeason = ""
    @State private var scanning = false
    @State private var showingSuggestions = false
    @State private var suggestions: [SeasonSuggestion] = []
    @State private var suggestionMarket: String?
    @State private var alreadyTaggedCount = 0
    @State private var showingImporter = false
    @State private var csvPreview: SeasonCsvPreview?
    @State private var csvURL: URL?
    @State private var confirmingCsv = false
    @State private var pendingCsvIntent: ActionIntent?

    @State private var tagSort = SortPrefs.load(
        TableID.seasonalTags, fields: tagSortFields,
        fallback: [KeyPathComparator(\SeasonTag.label)])
    @State private var tagColPrefs: TableColumnCustomization<SeasonTag> = ColumnPrefs.load(TableID.seasonalTags)
    @State private var tagSelection = Set<SeasonTag.ID>()
    @State private var seasonSort = SortPrefs.load(
        TableID.seasonalSeasons, fields: seasonSortFields,
        fallback: [KeyPathComparator(\SeasonInfo.label)])
    @State private var seasonColPrefs: TableColumnCustomization<SeasonInfo> = ColumnPrefs.load(TableID.seasonalSeasons)
    @State private var seasonSelection = Set<SeasonInfo.ID>()

    private static let tagSortFields: [String: KeyPathComparator<SeasonTag>] = [
        "asin": .init(\.asin), "label": .init(\.label),
        "adGroups": .init(\.adGroups), "enabled": .init(\.enabled), "paused": .init(\.paused),
    ]
    private static let seasonSortFields: [String: KeyPathComparator<SeasonInfo>] = [
        "label": .init(\.label), "taggedCount": .init(\.taggedCount),
    ]

    private static let monthNames = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                                     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    private var willAct: Int { (preview?.pause.count ?? 0) + (preview?.enable.count ?? 0) }

    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "Seasonal", subtitle: "\(appState.selectedMarket) · \(seasons?.today ?? "loading")", help: .seasonal)
            statusBand
            header
            ActionErrorBar(message: $actionError)
            Divider()
            if isLoading && seasons == nil {
                ProgressView("Loading seasons…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let loadError {
                ContentUnavailableView {
                    Label("Seasons unavailable", systemImage: "calendar.badge.exclamationmark")
                } description: {
                    Text(loadError)
                } actions: {
                    Button("Retry") { Task { await load() } }
                }
            } else if seasons != nil {
                VSplitView {
                    taggedPane
                    seasonsPane
                }
                .frame(maxHeight: .infinity, alignment: .top)
            }
        }
        .background(Theme.Colors.canvas)
        .task(id: appState.viewKey) { await load() }
        .onChange(of: tagSort) { SortPrefs.save(TableID.seasonalTags, tagSort, fields: Self.tagSortFields) }
        .onChange(of: tagColPrefs) { ColumnPrefs.save(TableID.seasonalTags, tagColPrefs) }
        .onChange(of: seasonSort) { SortPrefs.save(TableID.seasonalSeasons, seasonSort, fields: Self.seasonSortFields) }
        .onChange(of: seasonColPrefs) { ColumnPrefs.save(TableID.seasonalSeasons, seasonColPrefs) }
        .sheet(isPresented: $showingSuggestions) {
            SeasonSuggestionsSheet(suggestions: suggestions, alreadyTagged: alreadyTaggedCount) { asins in
                await applySuggestions(asins)
            }
        }
        .fileImporter(isPresented: $showingImporter, allowedContentTypes: [.commaSeparatedText, .plainText]) { result in
            if case .success(let url) = result { Task { await previewCsv(url) } }
        }
        .confirmationDialog(csvPreview.map { "Tag \($0.new) design\($0.new == 1 ? "" : "s") as \($0.label)?" } ?? "",
                            isPresented: $confirmingCsv, presenting: csvPreview) { preview in
            Button("Tag \(preview.new) Design\(preview.new == 1 ? "" : "s")") {
                if let intent = pendingCsvIntent {
                    Task { await applyCsv(intent, preview: preview) }
                }
            }
            .disabled(preview.new == 0)
        } message: { preview in
            Text("\(preview.csv): \(preview.found) ASIN\(preview.found == 1 ? "" : "s") found · \(preview.new) new · \(preview.already) already tagged to \(preview.label). Reversible with Untag.")
        }
        .confirmationDialog(
            pendingTagIntent?.title ?? "",
            isPresented: Binding(get: { pendingTagIntent != nil },
                                 set: { if !$0 { pendingTagIntent = nil } }),
            presenting: pendingTagIntent
        ) { intent in
            Button("Apply") { Task { await executeTag(intent, confirmed: true) } }
        } message: { intent in
            Text("Apply this tag change to \(intent.scope.confirmationDescription).")
        }
        .confirmationDialog(
            pendingSeasonalApply?.title ?? "",
            isPresented: Binding(get: { pendingSeasonalApply != nil },
                                 set: { if !$0 { pendingSeasonalApply = nil } }),
            presenting: pendingSeasonalApply
        ) { intent in
            Button("Apply to Amazon", role: .destructive) {
                Task { await apply(intent) }
            }
        } message: { intent in
            Text("Pause \(preview?.pause.count ?? 0) and re-enable \(preview?.enable.count ?? 0) ad group(s) in \(intent.scope.confirmationDescription). The next season transition reverses it automatically.")
        }
    }

    // MARK: header

    private var statusBand: some View {
        let active = seasons?.seasons.filter(\.active) ?? []
        let activeLabel = active.isEmpty ? "None" : active.map(\.label).joined(separator: " · ")
        return Group {
            HStack(spacing: Layout.Spacing.sm) {
                StatCard(title: "Active seasons", value: activeLabel,
                         tint: active.isEmpty ? Theme.Colors.muted : Theme.Colors.positive,
                         symbol: "calendar.badge.checkmark")
                    .mdCard()
                StatCard(title: "Tagged designs",
                         value: seasons.map { Format.count($0.tags.count) } ?? "—",
                         symbol: "tag.fill")
                    .mdCard()
                StatCard(title: "Pause now",
                         value: preview.map { Format.count($0.pause.count) } ?? "—",
                         tint: (preview?.pause.isEmpty == false)
                            ? Theme.Colors.caution : Theme.Colors.muted,
                         symbol: "pause.circle.fill")
                    .mdCard()
                StatCard(title: "Re-enable now",
                         value: preview.map { Format.count($0.enable.count) } ?? "—",
                         tint: (preview?.enable.isEmpty == false)
                            ? Theme.Colors.positive : Theme.Colors.muted,
                         symbol: "play.circle.fill")
                    .mdCard()
            }
        }
        .padding(.horizontal, Layout.Spacing.lg)
        .padding(.vertical, Layout.Spacing.md)
    }

    private var header: some View {
        FilterBar {
            if willAct > 0 {
                Label("\(preview?.pause.count ?? 0) pause · \(preview?.enable.count ?? 0) re-enable now",
                      systemImage: "calendar.badge.clock")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                Text("Nothing to change right now")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            if let lastResult {
                Text(lastResult).font(.caption).foregroundStyle(Theme.Colors.positive)
            }
        } trailing: {
            Button {
                requestSeasonalApply()
            } label: {
                if applying {
                    HStack(spacing: Layout.Spacing.xs) {
                        ProgressView().controlSize(.small)
                        Text("Applying…")
                    }
                } else {
                    Text("Apply Now")
                }
            }
            .disabled(willAct == 0 || applying || appState.killActive)
            .help("Run the seasonal pause/enable for \(appState.selectedMarket) now, instead of waiting for tonight's job")
            if appState.killActive {
                StatusBadge(text: "KILL", symbol: "exclamationmark.octagon.fill",
                            tint: Theme.Colors.critical)
                    .help("Writes are frozen — release KILL in Actions")
            }
        }
    }

    // MARK: tagged designs

    private var taggedPane: some View {
        VStack(spacing: 0) {
            tagBar
            Divider()
            if let tags = seasons?.tags, !tags.isEmpty {
                taggedTable(tags)
            } else {
                ContentUnavailableView {
                    Label("No seasonal designs yet", systemImage: "tag")
                } description: {
                    Text("Tag a design by its ASIN above to have the nightly job pause it out of season.")
                }
            }
        }
        .frame(minHeight: 180)
    }

    private var tagBar: some View {
        FilterBar {
            Text("Tag a design").font(Typography.sectionTitle)
            TextField("ASIN (e.g. B0EXAMPLE1)", text: $newAsin)
                .textFieldStyle(.roundedBorder)
                .font(.body.monospaced())
                .frame(width: 200)
                .onSubmit { Task { await addTag() } }
            Picker("Season", selection: $newSeason) {
                ForEach(seasons?.seasons ?? []) { s in Text(s.label).tag(s.key) }
            }
            .labelsHidden()
            .frame(width: 160)
            Button("Add") { Task { await addTag() } }
                .disabled(newAsin.trimmingCharacters(in: .whitespaces).count < 10 || newSeason.isEmpty)
        } trailing: {
            Button {
                Task { await scan() }
            } label: {
                if scanning {
                    HStack(spacing: Layout.Spacing.xs) {
                        ProgressView().controlSize(.small)
                        Text("Scanning…")
                    }
                } else {
                    Label("Scan Titles", systemImage: "sparkle.magnifyingglass")
                }
            }
            .help("Find designs whose titles name a season (Juneteenth, Christmas…) and tag them in bulk")
            Button {
                showingImporter = true
            } label: {
                Label("Import CSV", systemImage: "square.and.arrow.down")
            }
            .disabled(newSeason.isEmpty)
            .help("Tag every ASIN in a CSV to the season selected above — for lists the title scan can't catch")
        }
    }

    private func taggedTable(_ tags: [SeasonTag]) -> some View {
        let rows = tags.sorted(using: tagSort)
        return Table(rows, selection: $tagSelection, sortOrder: $tagSort.descendingFirst(),
                     columnCustomization: $tagColPrefs) {
            TableColumn("ASIN", value: \.asin) { tag in
                Text(tag.asin).font(.body.monospaced())
            }
            .width(min: 90, ideal: 110)
            .customizationID("asin")
            TableColumn("Season", value: \.label) { tag in
                StatusBadge(text: tag.label, symbol: "calendar",
                            tint: tag.active == true
                                ? Theme.Colors.positive : Theme.Colors.muted)
            }
            .width(min: 90, ideal: 140)
            .customizationID("season")
            TableColumn("Status", value: \.activeValue) { tag in
                seasonStatus(active: tag.active, seasonKey: tag.season)
            }
            .width(min: 110, ideal: 180)
            .customizationID("status")
            TableColumn("Ad groups", value: \.adGroups) { tag in
                if tag.adGroups == 0 {
                    Text("—").foregroundStyle(.secondary)
                } else {
                    HStack(spacing: Layout.Spacing.xs) {
                        CountText(value: tag.enabled)
                        Text("on ·")
                        CountText(value: tag.paused)
                        Text("off")
                    }
                    .foregroundStyle(.secondary)
                }
            }
            .width(min: 80, ideal: 110)
            .customizationID("ad-groups")
            TableColumn("Type", value: \.productTypeValue) { tag in
                StatusBadge.campaignType(tag.productType)
            }
            .width(min: 80, ideal: 130)
            .customizationID("type")
            TableColumn("") { tag in
                Button("Untag", role: .destructive) { Task { await untag(tag.asin) } }
                    .buttonStyle(.borderless)
                    .help("Stop managing this design seasonally (does not change its current state)")
            }
            .width(min: 44, ideal: 60)
            .customizationID("untag")
        }
        .copyableRows(rows, primaryLabel: "ASIN",
                      primary: { $0.asin },
                      row: { "\($0.asin)\t\($0.label)\t\($0.productType ?? "")" })
        .background(Theme.Colors.surface)
    }

    // MARK: seasons reference

    private var seasonsPane: some View {
        VStack(spacing: 0) {
            SectionHeader(title: "Seasons",
                          subtitle: "recurring yearly · resume leads the event date",
                          count: seasons?.seasons.count)
                .padding(.horizontal, Layout.Spacing.sm)
            if let list = seasons?.seasons {
                seasonsTable(list)
            }
        }
        .frame(minHeight: 160)
    }

    private func seasonsTable(_ list: [SeasonInfo]) -> some View {
        let rows = list.sorted(using: seasonSort)
        return Table(rows, selection: $seasonSelection, sortOrder: $seasonSort.descendingFirst(),
                     columnCustomization: $seasonColPrefs) {
            TableColumn("Season", value: \.label) { s in Text(s.label) }
                .width(min: 90, ideal: 150)
                .customizationID("season")
            TableColumn("Active window", value: \.resume) { s in
                Text("\(pretty(s.resume)) → \(pretty(s.pause))")
                    .foregroundStyle(.secondary).monospacedDigit()
            }
            .width(min: 120, ideal: 170)
            .customizationID("window")
            TableColumn("Status", value: \.activeValue) { s in
                seasonStatus(active: s.active, seasonKey: s.key)
            }
            .width(min: 110, ideal: 180)
            .customizationID("status")
            TableColumn("Tagged", value: \.taggedCount) { s in
                CountText(value: s.taggedCount == 0 ? nil : s.taggedCount)
            }
            .width(min: 44, ideal: 60)
            .customizationID("tagged")
        }
        .copyableRows(rows, primaryLabel: "Season",
                      primary: { $0.label },
                      row: { "\($0.label)\t\($0.resume)\t\($0.pause)\t\($0.taggedCount)" })
        .background(Theme.Colors.surface)
    }

    // MARK: status badge

    @ViewBuilder
    private func seasonStatus(active: Bool?, seasonKey: String) -> some View {
        let info = seasons?.seasons.first { $0.key == seasonKey }
        if active == true {
            StatusBadge(text: "In season", symbol: "circle.fill",
                        tint: Theme.Colors.positive)
                .help("Running now — will be paused after \(pretty(info?.pause ?? ""))")
        } else {
            StatusBadge(text: "Off · resumes \(pretty(info?.resume ?? ""))",
                        symbol: "circle", tint: Theme.Colors.muted)
                .help(info?.nextTransition.map { "Next change: \($0)" } ?? "Out of season")
        }
    }

    private func pretty(_ md: String) -> String {
        let parts = md.split(separator: "-")
        guard parts.count == 2, let m = Int(parts[0]), let d = Int(parts[1]),
              (1...12).contains(m) else { return md }
        return "\(Self.monthNames[m - 1]) \(d)"
    }

    // MARK: actions

    private func load() async {
        isLoading = true
        defer { if !Task.isCancelled { isLoading = false } }
        loadError = nil
        // Drop the previous market's rows BEFORE fetching, so a market switch
        // shows the spinner instead of the old market's tags and counts.
        seasons = nil
        preview = nil
        tagSelection.removeAll()
        seasonSelection.removeAll()

        let bridge: PythonBridge
        do {
            bridge = try appState.makeBridge()
        } catch {
            loadError = error.localizedDescription
            return
        }

        // The tags list is the screen. Only a failure here is a page-level error.
        do {
            let s = try await bridge.call(SeasonsResponse.self, ["seasons"],
                                          market: appState.selectedMarket)
            guard !Task.isCancelled else { return }
            seasons = s
            if newSeason.isEmpty { newSeason = s.seasons.first?.key ?? "" }
        } catch {
            guard !Task.isCancelled else { return }
            seasons = nil
            loadError = error.localizedDescription
            return
        }

        // The preview only feeds the two "now" counts and Apply Now. If it fails,
        // say so in the action bar and keep the tags list on screen.
        do {
            let p = try await bridge.call(SeasonalPreviewResponse.self, ["seasonal-preview"],
                                          market: appState.selectedMarket)
            guard !Task.isCancelled else { return }
            preview = p
        } catch {
            guard !Task.isCancelled else { return }
            preview = nil
            actionError = "Seasonal preview unavailable — \(error.localizedDescription)"
        }
    }

    private func scan() async {
        scanning = true
        defer { scanning = false }
        actionError = nil
        do {
            let bridge = try appState.makeBridge()
            let r = try await bridge.call(SeasonSuggestResponse.self, ["season-suggest"],
                                          market: appState.selectedMarket)
            alreadyTaggedCount = r.suggestions.filter(\.alreadyTagged).count
            suggestions = r.suggestions.filter { !$0.alreadyTagged }
            suggestionMarket = r.market
            if suggestions.isEmpty {
                actionError = "No untagged designs match a season keyword\(alreadyTaggedCount > 0 ? " (\(alreadyTaggedCount) already tagged)" : "")."
            } else {
                showingSuggestions = true
            }
        } catch {
            actionError = error.localizedDescription
        }
    }

    private func previewCsv(_ url: URL) async {
        actionError = nil
        csvURL = url
        do {
            let bridge = try appState.makeBridge()
            let p = try await bridge.call(SeasonCsvPreview.self,
                                          ["season-tag-csv", "--csv", url.path, "--season", newSeason],
                                          market: appState.selectedMarket)
            csvPreview = p
            if p.found == 0 {
                actionError = "No ASINs found in \(p.csv)."
            } else if p.new == 0 {
                actionError = "\(p.csv): all \(p.found) already tagged as \(p.label)."
            } else {
                pendingCsvIntent = appState.marketIntent(
                    title: "Tag \(p.new) seasonal designs from CSV",
                    arguments: ["season-tag-csv", "--csv", url.path,
                                "--season", p.season, "--apply"],
                    cardinality: .bulk, responseKind: .seasonCsvApply)
                confirmingCsv = true
            }
        } catch {
            actionError = error.localizedDescription
        }
    }

    private func applyCsv(_ intent: ActionIntent, preview: SeasonCsvPreview) async {
        actionError = nil
        do {
            let receipt = try await appState.actionCoordinator.execute(
                intent, context: appState.actionPolicyContext, confirmed: true)
            guard !receipt.rehearsed else { return }
            guard case .seasonCsvApply(let tagged, let label, let csv) = receipt.result else { return }
            lastResult = "Tagged \(tagged) design\(tagged == 1 ? "" : "s") as \(label) from \(csv)."
            if intent.scope.market == appState.selectedMarket { await load() }
        } catch {
            actionError = error.localizedDescription
        }
    }

    private func applySuggestions(_ asins: [String]) async {
        actionError = nil
        do {
            let payload = try JSONSerialization.data(withJSONObject: ["asins": asins])
            let intent = appState.marketIntent(
                for: suggestionMarket ?? appState.selectedMarket,
                title: "Apply \(asins.count) seasonal title suggestions",
                arguments: ["season-suggest", "--apply"], stdin: payload,
                cardinality: .bulk, responseKind: .seasonSuggestApply)
            // The suggestions sheet is review UI, but it isn't the policy: with
            // KILL on this must say so instead of a doomed round trip, and the
            // coordinator (confirmed: true is the sheet's explicit Apply click)
            // still re-checks server-side like every other write path.
            if case .blocked(.killActive(let scope)) = appState.actionCoordinator.requirement(
                for: intent, context: appState.actionPolicyContext) {
                actionError = ActionCoordinatorError.killActive(scope).localizedDescription
                return
            }
            let receipt = try await appState.actionCoordinator.execute(
                intent, context: appState.actionPolicyContext, confirmed: true)
            guard !receipt.rehearsed else { return }
            guard case .seasonSuggestApply(let count) = receipt.result else { return }
            lastResult = "Tagged \(count) design\(count == 1 ? "" : "s")."
            if intent.scope.market == appState.selectedMarket { await load() }
        } catch {
            actionError = error.localizedDescription
        }
    }

    private func addTag() async {
        let asin = newAsin.trimmingCharacters(in: .whitespaces).uppercased()
        guard asin.count >= 10, !newSeason.isEmpty else { return }
        let intent = appState.marketIntent(
            title: "Tag \(asin) for \(newSeason)",
            arguments: ["season-tag", "--asin", asin, "--season", newSeason])
        requestTag(intent)
    }

    private func untag(_ asin: String) async {
        let intent = appState.marketIntent(
            title: "Untag \(asin)",
            arguments: ["season-tag", "--asin", asin, "--clear"])
        requestTag(intent)
    }

    private func requestTag(_ intent: ActionIntent) {
        switch appState.actionCoordinator.requirement(
            for: intent, context: appState.actionPolicyContext) {
        case .confirmation:
            pendingTagIntent = intent
        case .blocked(.killActive(let scope)):
            actionError = ActionCoordinatorError.killActive(scope).localizedDescription
        case .preview, .ready:
            Task { await executeTag(intent) }
        }
    }

    private func executeTag(_ intent: ActionIntent, confirmed: Bool = false) async {
        actionError = nil
        do {
            let receipt = try await appState.actionCoordinator.execute(
                intent, context: appState.actionPolicyContext, confirmed: confirmed)
            guard !receipt.rehearsed else { return }
            if intent.arguments.contains("--season") { newAsin = "" }
            if intent.scope.market == appState.selectedMarket { await load() }
        } catch {
            actionError = error.localizedDescription
        }
    }

    private func requestSeasonalApply() {
        let intent = appState.marketIntent(
            title: "Apply seasonal changes",
            arguments: ["seasonal-apply"], cardinality: .bulk,
            responseKind: .seasonalApply)
        switch appState.actionCoordinator.requirement(
            for: intent, context: appState.actionPolicyContext) {
        case .confirmation:
            pendingSeasonalApply = intent
        case .blocked(.killActive(let scope)):
            actionError = ActionCoordinatorError.killActive(scope).localizedDescription
        case .preview, .ready:
            Task { await apply(intent) }
        }
    }

    private func apply(_ intent: ActionIntent) async {
        applying = true
        defer { applying = false }
        actionError = nil
        do {
            let receipt = try await appState.actionCoordinator.execute(
                intent, context: appState.actionPolicyContext, confirmed: true)
            guard !receipt.rehearsed else { return }
            guard case .seasonalApply(let paused, let enabled) = receipt.result else { return }
            lastResult = "Paused \(paused), re-enabled \(enabled)."
            if intent.scope.market == appState.selectedMarket { await load() }
        } catch {
            actionError = error.localizedDescription
        }
    }
}

/// Review sheet for auto-detected seasonal designs. Everything is pre-selected;
/// the operator unchecks any false positives, then bulk-tags the rest.
struct SeasonSuggestionsSheet: View {
    let suggestions: [SeasonSuggestion]
    let alreadyTagged: Int
    let onApply: ([String]) async -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var selection: Set<SeasonSuggestion.ID>
    @State private var sortOrder = [KeyPathComparator(\SeasonSuggestion.label)]
    @State private var applying = false

    init(suggestions: [SeasonSuggestion], alreadyTagged: Int,
         onApply: @escaping ([String]) async -> Void) {
        self.suggestions = suggestions
        self.alreadyTagged = alreadyTagged
        self.onApply = onApply
        _selection = State(initialValue: Set(suggestions.map(\.id)))
    }

    private var rows: [SeasonSuggestion] { suggestions.sorted(using: sortOrder) }

    /// Drop the redundant leading "ASIN_" so the season theme shows in the column.
    private func designTitle(_ s: SeasonSuggestion) -> String {
        let prefix = s.asin + "_"
        return s.name.hasPrefix(prefix) ? String(s.name.dropFirst(prefix.count)) : s.name
    }

    var body: some View {
        VStack(spacing: 0) {
            VStack(alignment: .leading, spacing: Layout.Spacing.xxs) {
                SectionHeader(title: "Auto-detected seasonal designs",
                              subtitle: "review title matches before tagging",
                              count: suggestions.count)
                Text("Matched by a season word in the title. Uncheck any false matches; tagging is reversible (Untag on the main screen).\(alreadyTagged > 0 ? " \(alreadyTagged) already tagged." : "")")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(Layout.Spacing.sm)
            Divider()
            Table(rows, selection: $selection, sortOrder: $sortOrder.descendingFirst()) {
                TableColumn("Season", value: \.label) { Text($0.label) }
                    .width(min: 90, ideal: 130)
                TableColumn("Matched", value: \.keyword) { Text($0.keyword).foregroundStyle(.secondary) }
                    .width(min: 70, ideal: 110)
                TableColumn("ASIN", value: \.asin) { Text($0.asin).font(.body.monospaced()) }
                    .width(min: 90, ideal: 110)
                TableColumn("Design", value: \.name) {
                    Text(designTitle($0)).lineLimit(1).truncationMode(.tail)
                }
            }
            .copyableRows(rows, primaryLabel: "ASIN", primary: { $0.asin },
                          row: { "\($0.asin)\t\($0.label)\t\($0.name)" })
            .background(Theme.Colors.surface)
            Divider()
            HStack {
                Button(selection.count == rows.count ? "Deselect All" : "Select All") {
                    selection = selection.count == rows.count ? [] : Set(rows.map(\.id))
                }
                Text("\(selection.count) selected")
                    .font(.caption).foregroundStyle(.secondary).monospacedDigit()
                Spacer()
                Button("Cancel") { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button {
                    applying = true
                    Task {
                        await onApply(Array(selection))
                        applying = false
                        dismiss()
                    }
                } label: {
                    if applying {
                        HStack(spacing: Layout.Spacing.xs) {
                            ProgressView().controlSize(.small)
                            Text("Tagging…")
                        }
                    } else {
                        Text("Tag \(selection.count) Design\(selection.count == 1 ? "" : "s")")
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(selection.isEmpty || applying)
            }
            .padding(Layout.Spacing.sm)
        }
        .background(Theme.Colors.canvas)
        .frame(minWidth: 660, minHeight: 500)
    }
}

#Preview {
    SeasonalView()
        .environment(AppState())
}
