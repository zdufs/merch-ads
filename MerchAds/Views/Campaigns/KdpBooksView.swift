import SwiftUI

/// KDP Books — each book's royalty per ASIN.
///
/// This is the KDP analog of the Merch catalogue export: the book economics
/// that the rest of the app leans on. Break-even, true profit and the kill list
/// all read the royalty entered here, and a book with no entry fails closed —
/// its economics read as unavailable, never guessed.
///
/// Config only: a local `kdp_books.json` file, no market and no Amazon (the
/// same direct-call precedent as the max-bid ceiling). KDP-only — the screen
/// appears in the sidebar and command palette only for a KDP advertiser
/// account. A Merch design gets its economics from the catalogue export, so the
/// screen is hidden for Merch accounts (see `Screen.isKDPOnly`).
struct KdpBooksView: View {
    @Environment(AppState.self) private var appState

    @State private var books: [KdpBook] = []
    @State private var isLoading = false
    @State private var loadError: String?
    @State private var filterText = ""
    @State private var selection = Set<KdpBook.ID>()
    // Column order/width/visibility and the sort persist across launches, the
    // same as every other table in the app (ColumnPrefs/SortPrefs → UserDefaults).
    @State private var colPrefs: TableColumnCustomization<KdpBook> = ColumnPrefs.load(TableID.kdpBooks)
    @State private var sort = SortPrefs.load(
        TableID.kdpBooks, fields: sortFields,
        fallback: [KeyPathComparator(\KdpBook.asin, order: .forward)])

    private static let sortFields: [String: KeyPathComparator<KdpBook>] = [
        "asin": .init(\.asin), "title": .init(\.titleSort),
        "advertising": .init(\.advertisedSort), "list": .init(\.listPriceSort),
        "royalty": .init(\.royaltySort), "breakeven": .init(\.breakEvenSort),
    ]

    @State private var newAsin = ""
    @State private var newPrice = ""
    @State private var newRoyalty = ""
    @State private var savingBook = false
    @State private var refreshingTitles = false
    @State private var actionError: String?

    // KDP US royalties are in USD. These are entered numbers, not a market pull,
    // so the currency is fixed rather than read from the market picker.
    private let currency = "USD"

    private var rows: [KdpBook] {
        books
            .filter { filterText.isEmpty
                || $0.asin.localizedStandardContains(filterText)
                || ($0.title ?? "").localizedStandardContains(filterText) }
            .sorted(using: sort)
    }

    private var incompleteCount: Int { books.filter { $0.known != true }.count }
    private var advertisedCount: Int { books.filter { $0.advertised == true }.count }

    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "KDP Books",
                       subtitle: "\(appState.currentMarket?.displayLabel ?? appState.selectedMarket) · royalty per book",
                       help: .kdpBooks)
            statusBand
            filterBar
            SectionHeader(title: "Books",
                          subtitle: "book economics — enter each royalty off your KDP dashboard",
                          count: rows.count)
                .padding(.horizontal, Layout.Spacing.sm)
            Divider()
            // The table stays UNCONDITIONAL in the body; loading, error and empty
            // states ride as an overlay. A greedy Table toggled by an if/else
            // blanks the whole detail on macOS 26 (see the CrossPurchase note).
            booksTable
                .overlay { tableOverlay }
            addBar
        }
        .background(Theme.Colors.canvas)
        .task(id: appState.viewKey) { await load() }
        .onChange(of: colPrefs) { ColumnPrefs.save(TableID.kdpBooks, colPrefs) }
        .onChange(of: sort) { SortPrefs.save(TableID.kdpBooks, sort, fields: Self.sortFields) }
    }

    private var statusBand: some View {
        HStack(spacing: Layout.Spacing.sm) {
            StatCard(title: "Books configured", value: Format.count(books.count),
                     symbol: "books.vertical",
                     subtitle: "with an economics entry")
                .mdCard()
            StatCard(title: "Advertising now", value: Format.count(advertisedCount),
                     tint: advertisedCount == 0 ? Theme.Colors.muted : Theme.Colors.positive,
                     symbol: "dot.radiowaves.left.and.right",
                     subtitle: "serving on Amazon")
                .mdCard()
            StatCard(title: "Incomplete",
                     value: incompleteCount == 0 ? "—" : Format.count(incompleteCount),
                     tint: incompleteCount == 0 ? Theme.Colors.muted : Theme.Colors.caution,
                     symbol: "questionmark.circle",
                     subtitle: "no royalty resolved — fails closed")
                .mdCard()
        }
        .fixedSize(horizontal: false, vertical: true)
        .padding(.horizontal, Layout.Spacing.lg)
        .padding(.vertical, Layout.Spacing.sm)
    }

    private var filterBar: some View {
        FilterBar {
            Text("Royalty is local config; titles are read from Amazon")
                .font(.caption2)
                .foregroundStyle(.secondary)
        } trailing: {
            Button {
                Task { await refreshTitles() }
            } label: {
                if refreshingTitles {
                    ProgressView().controlSize(.small)
                } else {
                    Label("Refresh titles", systemImage: "arrow.clockwise")
                }
            }
            .disabled(refreshingTitles || books.isEmpty)
            .help("Fetch each book's title from Amazon (a quick read). A book with no campaign has no title anywhere else, so this is the only way to fill it in.")
            if !books.isEmpty {
                TextField("Filter by ASIN or title", text: $filterText)
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 180)
            }
        }
    }

    private var booksTable: some View {
        Table(rows, selection: $selection, sortOrder: $sort.descendingFirst(),
              columnCustomization: $colPrefs) {
            TableColumn("ASIN", value: \.asin) { book in
                HStack(spacing: Layout.Spacing.xs) {
                    Text(book.asin).font(.body.monospaced())
                    if book.known != true {
                        StatusBadge(text: "incomplete", symbol: "questionmark.circle",
                                    tint: Theme.Colors.caution)
                            .fixedSize()
                            .help("No royalty resolved yet — this book's economics read as unavailable until you enter one.")
                    }
                    Spacer(minLength: 0)
                }
            }
            .width(min: 150, ideal: 200)
            .customizationID("asin")
            TableColumn("Title", value: \.titleSort) { book in
                Text(book.title ?? "—")
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .foregroundStyle(book.title == nil ? .secondary : .primary)
                    .help(book.title ?? "No title yet — press “Refresh titles” to fetch it from Amazon.")
            }
            .width(min: 200, ideal: 360)
            .customizationID("title")
            TableColumn("Advertising", value: \.advertisedSort) { book in
                if book.advertised == true {
                    StatusBadge(text: "Serving", symbol: "dot.radiowaves.left.and.right",
                                tint: Theme.Colors.positive)
                        .fixedSize()
                        .help("Serving now — at least one enabled ad group in an enabled campaign.")
                } else {
                    StatusBadge(text: "Off", symbol: "pause.circle",
                                tint: Theme.Colors.muted)
                        .fixedSize()
                        .help("Not advertising now — no enabled ad group. Build a KDP campaign for this book to advertise it.")
                }
            }
            .width(min: 84, ideal: 104)
            .customizationID("advertising")
            TableColumn("List price", value: \.listPriceSort) { book in
                MoneyText(value: book.listPrice, currency: currency)
            }
            .width(min: 78, ideal: 108)
            .customizationID("list")
            TableColumn("Royalty", value: \.royaltySort) { book in
                MoneyText(value: book.royaltyResolved, currency: currency)
            }
            .width(min: 78, ideal: 108)
            .customizationID("royalty")
            TableColumn("Break-even", value: \.breakEvenSort) { book in
                Text(Format.percent(book.breakEven))
                    .font(Typography.tableNumeral)
                    .foregroundStyle(.secondary)
            }
            .width(min: 80, ideal: 108)
            .customizationID("breakeven")
            // The action column is deliberately NOT customizable — hiding it
            // would strip the only way to remove a book. No customizationID keeps
            // it pinned last and out of the columns show/hide menu.
            TableColumn("") { book in
                Button("Remove", role: .destructive) {
                    Task { await clearBook(book.asin) }
                }
                .buttonStyle(.borderless)
            }
            .width(72)
        }
        .background(Theme.Colors.surface)
    }

    @ViewBuilder
    private var tableOverlay: some View {
        if isLoading && books.isEmpty {
            ProgressView("Loading books…")
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Theme.Colors.surface)
        } else if let loadError, books.isEmpty {
            ContentUnavailableView {
                Label("Books unavailable", systemImage: "books.vertical")
            } description: {
                Text(loadError)
            } actions: {
                Button("Retry") { Task { await load() } }
            }
            .background(Theme.Colors.surface)
        } else if books.isEmpty {
            ContentUnavailableView {
                Label("No books configured", systemImage: "books.vertical")
            } description: {
                Text("Add a book below — paste the ASIN and its royalty off your KDP dashboard. Until then, its economics read as unavailable and the kill list, profit and break-even rules skip it.")
            }
            .background(Theme.Colors.surface)
        } else if rows.isEmpty {
            ContentUnavailableView.search(text: filterText)
                .background(Theme.Colors.surface)
        }
    }

    /// The add-a-book bar sits below the table and is always visible, so a new
    /// book can be entered whether or not any exist yet.
    private var addBar: some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.xs) {
            Divider()
            HStack(spacing: Layout.Spacing.sm) {
                TextField("ASIN", text: $newAsin).frame(width: 130)
                TextField("List $", text: $newPrice).frame(width: 72)
                    .multilineTextAlignment(.trailing)
                TextField("Royalty $", text: $newRoyalty).frame(width: 72)
                    .multilineTextAlignment(.trailing)
                Button("Add book") { Task { await saveBook() } }
                    .disabled(newAsin.trimmingCharacters(in: .whitespaces).isEmpty || savingBook)
                if savingBook { ProgressView().controlSize(.small) }
                Spacer(minLength: 0)
            }
            .textFieldStyle(.roundedBorder)
            ActionErrorBar(message: $actionError)
            Text("Enter the royalty straight off your KDP dashboard (most accurate). The print-cost compute path — format, pages, ink — lives in the CLI: appctl kdp-book.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, Layout.Spacing.lg)
        .padding(.vertical, Layout.Spacing.sm)
        .background(Theme.Colors.surface)
    }

    // KDP books are a global local-config file (kdp_books.json) — no market, no
    // Amazon, KILL-irrelevant. Same direct-call precedent as the max-bid ceiling.
    private func load() async {
        isLoading = true
        defer { if !Task.isCancelled { isLoading = false } }
        loadError = nil
        do {
            let bridge = try appState.makeBridge()
            let r = try await bridge.call(KdpBooksResponse.self, ["kdp-book"], market: nil)
            guard !Task.isCancelled else { return }
            books = r.books
        } catch {
            guard !Task.isCancelled else { return }
            // Keep any books already shown — a transient reload failure should not
            // wipe the list. The error surfaces only when there is nothing to show.
            loadError = error.localizedDescription
        }
    }

    private func saveBook() async {
        savingBook = true
        defer { savingBook = false }
        actionError = nil
        var args = ["kdp-book", "--asin", newAsin.trimmingCharacters(in: .whitespaces)]
        if !newPrice.isEmpty { args += ["--list-price", newPrice] }
        if !newRoyalty.isEmpty { args += ["--royalty", newRoyalty] }
        do {
            let bridge = try appState.makeBridge()
            let r = try await bridge.call(KdpBookSaveResponse.self, args,
                                          market: nil, preferWorker: false)
            if r.known != true {
                actionError = "\(r.asin) saved but economics are still unresolvable — add a royalty, or a list price plus the print inputs in the CLI."
            } else {
                newAsin = ""; newPrice = ""; newRoyalty = ""
            }
            await load()
        } catch {
            actionError = error.localizedDescription
        }
    }

    /// Fetch every book's title from Amazon (SP product metadata) and cache it,
    /// then reload. A live READ — the only title source for a book with no
    /// campaign, whose name is nowhere in the pulled data.
    private func refreshTitles() async {
        refreshingTitles = true
        defer { refreshingTitles = false }
        actionError = nil
        do {
            let bridge = try appState.makeBridge()
            _ = try await bridge.call(KdpTitlesResponse.self,
                                      ["kdp-titles", "--refresh"],
                                      market: nil, preferWorker: false)
            await load()
        } catch {
            actionError = "Couldn't refresh titles: \(error.localizedDescription)"
        }
    }

    private func clearBook(_ asin: String) async {
        actionError = nil
        do {
            let bridge = try appState.makeBridge()
            _ = try await bridge.call(KdpBookSaveResponse.self,
                                      ["kdp-book", "--asin", asin, "--clear"],
                                      market: nil, preferWorker: false)
            await load()
        } catch {
            actionError = error.localizedDescription
        }
    }
}

/// Non-optional sort keys for the money columns — `Double?` is not `Comparable`,
/// so a missing figure sorts below every real one rather than failing to sort.
private extension KdpBook {
    var titleSort: String { title ?? "" }
    var advertisedSort: Int { advertised == true ? 1 : 0 }
    var listPriceSort: Double { listPrice ?? -1 }
    var royaltySort: Double { royaltyResolved ?? -1 }
    var breakEvenSort: Double { breakEven ?? -1 }
}

#Preview {
    KdpBooksView()
        .environment(AppState())
}
