import SwiftUI

struct PaletteEntity: Hashable, Sendable {
    let campaignID: String
    let campaignName: String
    let campaignState: String?
    let adGroupID: String
    let adGroupName: String
    let adGroupState: String?
    let asin: String?
}

enum PaletteSearch {
    static let entitySQL = """
        SELECT c.campaign_id,
               COALESCE(c.name, '') AS campaign_name,
               c.state AS campaign_state,
               ag.ad_group_id,
               COALESCE(ag.name, '') AS ad_group_name,
               ag.state AS ad_group_state,
               agp.asin
        FROM campaigns AS c
        JOIN ad_groups AS ag ON ag.campaign_id = c.campaign_id
        JOIN ad_group_product AS agp ON agp.ad_group_id = ag.ad_group_id
        WHERE lower(COALESCE(c.name, '')) LIKE ? ESCAPE '\\'
           OR lower(COALESCE(ag.name, '')) LIKE ? ESCAPE '\\'
           OR lower(COALESCE(agp.asin, '')) LIKE ? ESCAPE '\\'
           OR lower(c.campaign_id || ' ' || ag.ad_group_id) LIKE ? ESCAPE '\\'
        ORDER BY CASE
                   WHEN lower(COALESCE(agp.asin, '')) = ? THEN 0
                   WHEN lower(COALESCE(c.name, '')) = ? THEN 1
                   WHEN lower(COALESCE(ag.name, '')) = ? THEN 2
                   ELSE 3
                 END,
                 c.name COLLATE NOCASE,
                 ag.name COLLATE NOCASE
        LIMIT 50
        """

    static func entities(market: String, query: String) throws -> [PaletteEntity] {
        let store = try SQLiteStore(path: AppSettings.databaseURL(market: market).path)
        let normalized = query.lowercased()
        let escaped = normalized
            .replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "%", with: "\\%")
            .replacingOccurrences(of: "_", with: "\\_")
        let pattern = "%\(escaped)%"
        let rows = try store.rows(entitySQL, bind: [
            .text(pattern), .text(pattern), .text(pattern), .text(pattern),
            .text(normalized), .text(normalized), .text(normalized),
        ])
        return rows.compactMap { row in
            guard let campaignID = row["campaign_id"]?.stringValue,
                  let campaignName = row["campaign_name"]?.stringValue,
                  let adGroupID = row["ad_group_id"]?.stringValue,
                  let adGroupName = row["ad_group_name"]?.stringValue else { return nil }
            return PaletteEntity(
                campaignID: campaignID, campaignName: campaignName,
                campaignState: row["campaign_state"]?.stringValue,
                adGroupID: adGroupID, adGroupName: adGroupName,
                adGroupState: row["ad_group_state"]?.stringValue,
                asin: row["asin"]?.stringValue)
        }
    }
}

private struct PaletteEntry: Identifiable {
    enum Destination {
        case route(Route)
        case action(ActionIntent)
    }

    let id: String
    let title: String
    let subtitle: String
    let symbol: String
    let destination: Destination
}

/// ⌘K fuzzy-find over static destinations and one mirror query per keystroke.
struct CommandPaletteView: View {
    @Environment(AppState.self) private var appState
    @Environment(\.dismiss) private var dismiss
    @State private var query = ""
    @State private var entities: [PaletteEntity] = []
    @State private var isSearching = false
    @State private var isExecuting = false
    @State private var queryError: String?
    @State private var actionError: String?
    @State private var pendingIntent: ActionIntent?
    @FocusState private var searchFocused: Bool

    private var trimmed: String {
        query.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var entries: [PaletteEntry] {
        var result = staticEntries
        guard !trimmed.isEmpty else { return result }

        var seenCampaigns = Set<String>()
        var seenAdGroups = Set<String>()
        var seenAsins = Set<String>()
        for entity in entities {
            if seenCampaigns.insert(entity.campaignID).inserted {
                result.append(PaletteEntry(
                    id: "campaign|\(entity.campaignID)", title: entity.campaignName,
                    subtitle: "Campaign · \(appState.selectedMarket) · local mirror",
                    symbol: "megaphone",
                    destination: .route(.campaign(market: appState.selectedMarket,
                                                  campaignID: entity.campaignID))))
                result.append(stateAction(
                    id: entity.campaignID, name: entity.campaignName,
                    state: entity.campaignState, entity: "campaign"))
            }
            if seenAdGroups.insert(entity.adGroupID).inserted {
                result.append(PaletteEntry(
                    id: "adgroup|\(entity.campaignID)|\(entity.adGroupID)",
                    title: entity.adGroupName,
                    subtitle: "Ad group · \(entity.campaignName) · local mirror",
                    symbol: "rectangle.3.group",
                    destination: .route(.adGroup(market: appState.selectedMarket,
                                                 campaignID: entity.campaignID,
                                                 adGroupID: entity.adGroupID))))
                result.append(stateAction(
                    id: entity.adGroupID, name: entity.adGroupName,
                    state: entity.adGroupState, entity: "ad group"))
            }
            if let asin = entity.asin, !asin.isEmpty, seenAsins.insert(asin).inserted {
                result.append(PaletteEntry(
                    id: "asin|\(asin)", title: asin,
                    subtitle: "ASIN · Live Status · local mirror",
                    symbol: "barcode.viewfinder",
                    destination: .route(.asin(market: appState.selectedMarket, asin: asin))))
            }
        }
        return result.sorted { fuzzyScore($0.title + " " + $0.subtitle) > fuzzyScore($1.title + " " + $1.subtitle) }
    }

    private var staticEntries: [PaletteEntry] {
        let isKDP = appState.currentMarket?.isKDP == true
        let screens = Screen.allCases.compactMap { screen -> PaletteEntry? in
            guard !screen.isHidden else { return nil }   // retired from navigation
            guard screen.isAvailable(forKDP: isKDP) else { return nil }   // wrong account kind
            let score = fuzzyScore(screen.title + " " + screen.blurb)
            guard trimmed.isEmpty || score > 0 else { return nil }
            return PaletteEntry(id: "screen|\(screen.rawValue)", title: screen.title,
                                subtitle: "Screen", symbol: screen.icon,
                                destination: .route(.screen(screen)))
        }
        let phases = ["pull", "phase2", "phase3", "harvest"]
        let actions = phases.compactMap { phase -> PaletteEntry? in
            let title = "Run \(phase == "pull" ? "Amazon pull" : phase)"
            guard trimmed.isEmpty || fuzzyScore(title + " engine action") > 0 else { return nil }
            let intent = appState.marketIntent(
                title: title, arguments: ["run", "--phase", phase],
                confirmationPolicy: .required, responseKind: .run)
            return PaletteEntry(id: "static-action|\(phase)", title: title,
                                subtitle: "Action · \(appState.selectedMarket) · confirmation required",
                                symbol: "bolt", destination: .action(intent))
        }
        let fullTitle = "Run full nightly workflow"
        let full: [PaletteEntry]
        if trimmed.isEmpty || fuzzyScore(fullTitle + " all markets engine action") > 0 {
            let intent = appState.allMarketsIntent(
                title: "Run full nightly workflow for all markets", arguments: ["run"],
                confirmationPolicy: .required, responseKind: .run)
            full = [PaletteEntry(id: "static-action|full", title: fullTitle,
                                 subtitle: "Action · all markets · confirmation required",
                                 symbol: "globe.badge.chevron.backward",
                                 destination: .action(intent))]
        } else {
            full = []
        }
        return screens + actions + full
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: Layout.Spacing.sm) {
                Image(systemName: "command")
                    .foregroundStyle(.secondary)
                TextField("Find a screen, campaign, ad group, ASIN, or action", text: $query)
                    .textFieldStyle(.plain)
                    .font(.title3)
                    .focused($searchFocused)
                    .onSubmit { if let first = entries.first { choose(first) } }
                if isSearching || isExecuting { ProgressView().controlSize(.small) }
            }
            .padding(Layout.Spacing.md)
            .mdCard()

            ActionErrorBar(message: $actionError)
            Divider()

            if let queryError {
                ContentUnavailableView("Mirror search unavailable",
                                       systemImage: "externaldrive.badge.exclamationmark",
                                       description: Text(queryError))
            } else if entries.isEmpty {
                ContentUnavailableView.search(text: trimmed)
            } else {
                List(entries) { entry in
                    Button { choose(entry) } label: {
                        HStack(spacing: Layout.Spacing.sm) {
                            Image(systemName: entry.symbol)
                                .frame(width: 20)
                                .foregroundStyle(.secondary)
                            VStack(alignment: .leading, spacing: Layout.Spacing.xxs) {
                                Text(entry.title).lineLimit(1)
                                Text(entry.subtitle)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                            }
                            Spacer()
                        }
                        .contentShape(.rect)
                    }
                    .buttonStyle(.plain)
                }
                .listStyle(.inset)
                // Opt out of the app-wide text selection: every row is a button
                // you click to jump somewhere, never text you drag over.
                .textSelection(.disabled)
            }

            HStack {
                Label("Entities reflect the \(appState.selectedMarket) local mirror",
                      systemImage: "cylinder.split.1x2")
                Spacer()
                Text("Amazon-created entities appear after the next pull")
            }
            .font(.caption2)
            .foregroundStyle(.secondary)
            .padding(Layout.Spacing.sm)
        }
        .frame(width: 680, height: 520)
        .padding(Layout.Spacing.sm)
        .background(Theme.Colors.canvas)
        .task { searchFocused = true }
        .task(id: "\(appState.selectedMarket)|\(trimmed)") { await searchMirror() }
        .confirmationDialog(pendingIntent?.title ?? "Confirm action",
                            isPresented: Binding(get: { pendingIntent != nil },
                                                 set: { if !$0 { pendingIntent = nil } }),
                            presenting: pendingIntent) { intent in
            Button(intent.title, role: .destructive) {
                Task { await execute(intent) }
            }
        } message: { intent in
            Text("This writes to \(intent.scope.confirmationDescription). The action is logged and remains subject to KILL.")
        }
    }

    private func stateAction(id: String, name: String, state: String?, entity: String) -> PaletteEntry {
        let pausing = state == "ENABLED"
        let verb: String
        let arguments: [String]
        if entity == "campaign" {
            verb = pausing ? "Pause" : "Enable"
            arguments = [pausing ? "pause-campaign" : "enable-campaign", "--campaign", id]
        } else {
            verb = pausing ? "Pause" : "Enable"
            arguments = [pausing ? "pause" : "enable", "--adgroup", id]
        }
        let intent = appState.marketIntent(
            title: "\(verb) \(entity) \(name)", arguments: arguments,
            confirmationPolicy: .required)
        return PaletteEntry(id: "action|\(entity)|\(id)|\(verb)",
                            title: intent.title,
                            subtitle: "Action · confirmation required · \(appState.selectedMarket)",
                            symbol: pausing ? "pause.circle" : "play.circle",
                            destination: .action(intent))
    }

    private func choose(_ entry: PaletteEntry) {
        switch entry.destination {
        case .route(let route):
            appState.navigate(to: route)
            dismiss()
        case .action(let intent):
            pendingIntent = intent
        }
    }

    private func execute(_ intent: ActionIntent) async {
        isExecuting = true
        defer { isExecuting = false }
        actionError = nil
        do {
            _ = try await appState.actionCoordinator.execute(
                intent, context: appState.actionPolicyContext, confirmed: true)
            pendingIntent = nil
            dismiss()
        } catch {
            actionError = error.localizedDescription
        }
    }

    private func searchMirror() async {
        entities = []
        queryError = nil
        guard !trimmed.isEmpty else { return }
        isSearching = true
        defer { if !Task.isCancelled { isSearching = false } }
        do {
            try await Task.sleep(for: .milliseconds(180))
            let market = appState.selectedMarket
            let text = trimmed
            let queryTask = Task.detached(priority: .userInitiated) {
                try PaletteSearch.entities(market: market, query: text)
            }
            let found = try await withTaskCancellationHandler {
                try await queryTask.value
            } onCancel: {
                queryTask.cancel()
            }
            try Task.checkCancellation()
            guard market == appState.selectedMarket else { return }
            entities = found
        } catch is CancellationError {
            return
        } catch {
            guard !Task.isCancelled else { return }
            queryError = error.localizedDescription
        }
    }

    private func fuzzyScore(_ candidate: String) -> Int {
        guard !trimmed.isEmpty else { return 1 }
        let needle = trimmed.lowercased()
        let haystack = candidate.lowercased()
        if haystack == needle { return 1_000 }
        if haystack.hasPrefix(needle) { return 700 }
        if haystack.contains(needle) { return 500 }
        var index = needle.startIndex
        var score = 0
        for character in haystack where index < needle.endIndex {
            if character == needle[index] {
                score += 10
                index = needle.index(after: index)
            }
        }
        return index == needle.endIndex ? score : 0
    }
}
