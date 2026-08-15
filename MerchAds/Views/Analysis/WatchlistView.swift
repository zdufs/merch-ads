import SwiftUI

/// A private, per-market pinboard. Pin campaigns / ad groups / targets / ASINs
/// from anywhere in the app and watch them as one focused table with an aggregate
/// summary. Purely a view — pinning never touches Amazon. (A combined time-series
/// trend is a follow-up: it needs per-entity daily history the engine's
/// cumulative snapshots don't currently expose.)
struct WatchlistView: View {
    @Environment(AppState.self) private var appState

    @State private var response: WatchlistResponse?
    @State private var isLoading = false
    @State private var loadError: String?
    @State private var pins: [WatchlistPin] = []
    // Table cells can't be text-selected on macOS — right-click Copy instead.
    @State private var rowSel = Set<WatchlistRow.ID>()

    private var currency: String? { appState.currentMarket?.currency }

    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "Watchlist", subtitle: "\(appState.selectedMarket) · \(pins.count) pinned · private to you", help: .watchlist)
            if pins.isEmpty {
                emptyState
            } else {
                summaryBand
                Divider()
                LoadableView(
                    isLoading: isLoading && response == nil,
                    error: loadError,
                    isEmpty: false,
                    loadingTitle: "Resolving pinned entities…",
                    emptyTitle: "", emptyDescription: "",
                    systemImage: "pin",
                    retry: { Task { await load() } }
                ) {
                    table
                }
            }
        }
        .background(Theme.Colors.canvas)
        .task(id: appState.selectedMarket) { reloadPins(); await load() }
    }

    private var emptyState: some View {
        ContentUnavailableView {
            Label("Nothing pinned yet", systemImage: "pin")
        } description: {
            Text("Right-click a campaign or ASIN and choose “Pin to watchlist”, then come back here to watch them together for \(appState.selectedMarket).")
        }
        // Also what makes the page fill the window: without it the VStack hugs
        // this view and the whole screen ends up centred vertically.
        .topAlignedEmptyState()
    }

    private var summaryBand: some View {
        let s = response?.summary
        return Group {
            HStack(spacing: Layout.Spacing.sm) {
                StatCard(title: "Spend", value: s.map { Format.money($0.spend, currency: currency) } ?? "—",
                         symbol: "creditcard.fill", subtitle: "pinned total")
                    .mdCard()
                StatCard(title: "Sales", value: s.map { Format.money($0.sales, currency: currency) } ?? "—",
                         symbol: "banknote.fill", subtitle: "pinned total")
                    .mdCard()
                StatCard(title: "Orders", value: s.map { String($0.orders) } ?? "—",
                         symbol: "shippingbox.fill", subtitle: "pinned total")
                    .mdCard()
                StatCard(title: "ACOS", value: s?.acos.map { Format.percent($0) } ?? "—",
                         symbol: "percent", subtitle: "blended")
                    .mdCard()
            }
        }
        .padding(.horizontal, Layout.Spacing.lg)
        .padding(.vertical, Layout.Spacing.md)
    }

    private var table: some View {
        Table(response?.rows ?? [], selection: $rowSel) {
            TableColumn("Pinned") { r in
                HStack(spacing: 6) {
                    Image(systemName: icon(r.kind)).foregroundStyle(.secondary).font(.caption)
                    Text(r.label).lineLimit(1)
                    if !r.resolved {
                        Text("no data").font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            TableColumn("Kind") { r in
                Text(r.kind).font(.caption).foregroundStyle(.secondary)
            }.width(min: 54, ideal: 70)
            TableColumn("Clicks") { r in CountText(value: r.clicks) }.width(min: 40, ideal: 60)
            TableColumn("Spend") { r in MoneyText(value: r.spend, currency: currency) }.width(min: 44, ideal: 70)
            TableColumn("Orders") { r in CountText(value: r.orders) }.width(min: 40, ideal: 60)
            TableColumn("Sales") { r in MoneyText(value: r.sales, currency: currency) }.width(min: 44, ideal: 70)
            TableColumn("ACOS") { r in PercentText(value: r.acos, label: "ACOS") }.width(min: 40, ideal: 60)
            TableColumn("") { r in
                Button(role: .destructive) {
                    unpin(r)
                } label: {
                    Image(systemName: "pin.slash")
                }
                .buttonStyle(.borderless)
                .help("Remove from watchlist")
                .accessibilityLabel("Remove \(r.label) from watchlist")
            }.width(30)
        }
        .copyableRows(response?.rows ?? [], primaryLabel: "Pin",
                      primary: { $0.label },
                      row: { "\($0.kind)\t\($0.label)\t\($0.clicks)\t\($0.spend)\t\($0.orders)\t\($0.sales)" })
    }

    private func icon(_ kind: String) -> String {
        switch kind {
        case "campaign": "megaphone"
        case "adgroup": "rectangle.3.group"
        case "target": "scope"
        case "asin": "tag"
        default: "pin"
        }
    }

    private func reloadPins() {
        pins = WatchlistStore.pins(market: appState.selectedMarket)
    }

    private func unpin(_ row: WatchlistRow) {
        guard let pin = pins.first(where: { matches($0, row) }) else { return }
        WatchlistStore.remove(pin, market: appState.selectedMarket)
        reloadPins()
        Task { await load() }
    }

    private func matches(_ pin: WatchlistPin, _ row: WatchlistRow) -> Bool {
        pin.kind.rawValue.lowercased() == row.kind.lowercased()
            && (pin.campaignID == row.id || pin.adGroupID == row.id
                || pin.targetID == row.id || pin.asin == row.id)
    }

    private func load() async {
        let pins = WatchlistStore.pins(market: appState.selectedMarket)
        guard !pins.isEmpty else { response = nil; return }
        isLoading = true
        defer { if !Task.isCancelled { isLoading = false } }
        loadError = nil
        // Drop the previous market's rows BEFORE fetching: currency flips with the
        // market picker, so keeping them would render old money in the new symbol.
        response = nil
        do {
            let payload = ["pins": pins.map(\.engineDict)]
            let data = try JSONSerialization.data(withJSONObject: payload)
            let bridge = try appState.makeBridge()
            response = try await bridge.call(WatchlistResponse.self, ["watchlist"],
                                             market: appState.selectedMarket,
                                             stdin: data, preferWorker: false)
        } catch {
            guard !Task.isCancelled else { return }
            loadError = error.localizedDescription
        }
    }
}

#Preview {
    WatchlistView()
        .environment(AppState())
}
