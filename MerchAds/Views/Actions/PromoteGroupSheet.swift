import SwiftUI

/// Confirm-designs sheet for a "Needs a design" harvest winner. The search term
/// converted, but it came off a cohort ad group (Scavenger/AUTO) with no single
/// ASIN behind it, so the operator has to say which design(s) earn the new
/// exact-match keyword before promotion can create it.
///
/// Suggestions come from `harvest-suggest` (whole-catalogue title-word overlap
/// with the term) and are pre-ticked when they look confident — unless the term
/// itself is sensitive/trademarked, in which case nothing is pre-ticked and the
/// operator opts in deliberately. A keyword field lets the operator search the
/// catalogue directly and add designs the suggester missed.
///
/// This view only collects the selection — `onPromote` fires with the confirmed
/// ASIN set and the caller (HarvestView) owns the actual write: it builds the
/// `harvest-promote-group --apply` intent, runs it through the
/// ActionCoordinator (confirm dialog, KILL guard, Audit Trail), and reloads
/// the harvest list on success.
struct PromoteGroupSheet: View {
    @Environment(AppState.self) private var appState
    @Environment(\.dismiss) private var dismiss

    let winner: HarvestWinner
    /// Fires with the confirmed ASIN set when the operator presses Promote.
    let onPromote: (Set<String>) -> Void

    @State private var suggestions: [SuggestedDesign] = []
    @State private var selected = Set<String>()
    @State private var addQuery = ""
    @State private var isLoading = false
    @State private var isSearchingAdd = false
    @State private var loadError: String?

    private var currency: String? { appState.currentMarket?.currency }
    private var sensitive: Bool { sensitiveOrTrademark(winner.searchTerm) }

    var body: some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.md) {
            header
            if sensitive {
                Label("Touches a trademarked name or sensitive language — nothing is pre-selected. Review each design before promoting.",
                      systemImage: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundStyle(Theme.Colors.caution)
            }
            content
            searchField
            footer
        }
        .padding(Layout.Spacing.md)
        .frame(width: 480, height: 560)
        .task { await loadSuggestions() }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.xxs) {
            Text("Choose designs").font(.headline)
            Text("\u{201C}\(winner.searchTerm)\u{201D} · \(Format.count(winner.orders)) orders · \(Format.money(winner.sales, currency: currency)) sales · \(Format.percent(winner.acos)) ACOS")
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .truncationMode(.middle)
        }
    }

    @ViewBuilder
    private var content: some View {
        if isLoading && suggestions.isEmpty {
            ProgressView("Finding matching designs…")
                .frame(maxWidth: .infinity, minHeight: 160)
        } else if let loadError {
            ContentUnavailableView {
                Label("Suggestions unavailable", systemImage: "wand.and.stars")
            } description: {
                Text(loadError)
            } actions: {
                Button("Retry") { Task { await loadSuggestions() } }
            }
            .frame(maxWidth: .infinity, minHeight: 160)
        } else if suggestions.isEmpty {
            ContentUnavailableView {
                Label("No catalogue matches", systemImage: "shippingbox")
            } description: {
                Text("Nothing in the catalogue shares a word with \u{201C}\(winner.searchTerm)\u{201D}. Search by keyword below to add designs by hand.")
            }
            .frame(maxWidth: .infinity, minHeight: 160)
        } else {
            checklist
        }
    }

    private var checklist: some View {
        List(suggestions) { design in
            Toggle(isOn: Binding(
                get: { selected.contains(design.asin) },
                set: { on in
                    if on { selected.insert(design.asin) } else { selected.remove(design.asin) }
                })) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(design.title ?? design.asin)
                        .lineLimit(1)
                        .truncationMode(.tail)
                    HStack(spacing: Layout.Spacing.xxs) {
                        AsinLink(asin: design.asin, font: .caption.monospaced())
                            .foregroundStyle(.secondary)
                        if let pt = design.productType, !pt.isEmpty {
                            Text("· \(productLabel(pt))")
                                .font(.caption.weight(.medium))
                                .foregroundStyle(Theme.Colors.accent)
                        }
                        if let matched = design.matchedWords, !matched.isEmpty {
                            Text("· matched \(matched.joined(separator: ", "))")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
            .toggleStyle(.checkbox)
        }
        .listStyle(.inset)
    }

    /// The PRODUCT behind an ASIN — "standard_pullover_hoodie" -> "Standard Pullover
    /// Hoodie". When several ASINs share one design title, this is how the operator
    /// tells a tee from a hoodie from a tank before promoting.
    private func productLabel(_ raw: String) -> String {
        raw.replacingOccurrences(of: "_", with: " ")
            .split(separator: " ")
            .map { $0.lowercased() == "tshirt" ? "T-Shirt" : $0.capitalized }
            .joined(separator: " ")
    }

    private var searchField: some View {
        HStack(spacing: Layout.Spacing.xs) {
            TextField("Add designs by keyword…", text: $addQuery)
                .textFieldStyle(.roundedBorder)
                .onSubmit { Task { await searchAdd() } }
            if isSearchingAdd {
                ProgressView().controlSize(.small)
            }
        }
        // Re-queries as the operator types (debounced inside searchAdd via the
        // task id churn) as well as on Return via onSubmit above.
        .task(id: addQuery) { await debouncedSearchAdd() }
    }

    private var footer: some View {
        HStack {
            Text("\(selected.count) selected")
                .font(.caption)
                .foregroundStyle(.secondary)
            Spacer()
            Button("Cancel") { dismiss() }
                .keyboardShortcut(.cancelAction)
            Button("Promote \(selected.count) designs") {
                onPromote(selected)
                dismiss()
            }
            .buttonStyle(.borderedProminent)
            .keyboardShortcut(.defaultAction)
            .disabled(selected.isEmpty || appState.killActive)
            .help(appState.killActive ? "Writes are frozen (KILL active)" : "Confirms your selection")
        }
    }

    private func loadSuggestions() async {
        isLoading = true
        defer { if !Task.isCancelled { isLoading = false } }
        loadError = nil
        do {
            let bridge = try appState.makeBridge()
            let response = try await bridge.call(
                HarvestSuggestResponse.self,
                ["harvest-suggest", "--term", winner.searchTerm],
                market: appState.selectedMarket)
            guard !Task.isCancelled else { return }
            suggestions = response.suggestions
            // Pre-tick confident matches — unless the term itself is sensitive,
            // in which case the operator opts in to every design by hand.
            if !sensitive {
                selected = Set(response.suggestions.filter { $0.score >= 2 }.map(\.asin))
            }
        } catch {
            guard !Task.isCancelled else { return }
            loadError = error.localizedDescription
        }
    }

    /// Debounce: waits for a pause in typing before firing the network call, so
    /// each keystroke doesn't queue its own `harvest-suggest` round trip.
    private func debouncedSearchAdd() async {
        let query = addQuery.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return }
        do {
            try await Task.sleep(for: .milliseconds(250))
        } catch {
            return
        }
        guard !Task.isCancelled else { return }
        await searchAdd()
    }

    /// Re-queries `harvest-suggest` with the typed phrase and MERGES new hits
    /// into the existing list — a design already ticked from the term-based
    /// suggestions must not disappear because the operator typed something else.
    private func searchAdd() async {
        let query = addQuery.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return }
        isSearchingAdd = true
        defer { if !Task.isCancelled { isSearchingAdd = false } }
        do {
            let bridge = try appState.makeBridge()
            let response = try await bridge.call(
                HarvestSuggestResponse.self,
                ["harvest-suggest", "--term", query],
                market: appState.selectedMarket)
            guard !Task.isCancelled else { return }
            var byASIN = Dictionary(uniqueKeysWithValues: suggestions.map { ($0.asin, $0) })
            for design in response.suggestions where byASIN[design.asin] == nil {
                byASIN[design.asin] = design
            }
            suggestions = byASIN.values.sorted {
                $0.score != $1.score ? $0.score > $1.score : $0.asin < $1.asin
            }
        } catch {
            // Best-effort: a failed add-search leaves the existing list intact
            // rather than surfacing an error over an otherwise working sheet.
        }
    }
}

#Preview {
    PromoteGroupSheet(
        winner: HarvestWinner(searchTerm: "saint michael", sourceAdGroupId: "1", kind: "keyword",
                              type: "scavenger", sourceCampaignId: "1", clicks: 40, orders: 6,
                              sales: 120, acos: 0.18, cpc: 0.6, firstSeen: nil, lastSeen: nil,
                              promoted: false, needsDesign: true)
    ) { _ in }
        .environment(AppState())
}
