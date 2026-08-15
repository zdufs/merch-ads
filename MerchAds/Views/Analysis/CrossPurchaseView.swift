import SwiftUI

/// MEASURED cross-purchase: a shopper clicked one design's ad and bought a
/// different design. Amazon attributes it; the campaign and targeting reports
/// credit it nowhere, so a design can read as a money-loser while quietly
/// selling the rest of the catalogue.
///
/// This is the measured counterpart to the organic-halo estimate. Both belong:
/// this one is Amazon's own attribution but only counts ad clicks; that one
/// reaches organic lift but is correlational and an upper bound.
///
/// Layout note (learned the hard way, 2026-08-14): the two Tables live DIRECTLY
/// in the body's VStack and are never removed by a conditional. On macOS 26,
/// toggling a greedy `Table`'s presence inside a `@ViewBuilder` if/else whose
/// other branches are non-greedy (a spinner, a message) — including the one
/// inside `LoadableView` — makes the whole detail column render as empty
/// placeholder rows and blanks the sidebar. So loading / error / empty states are
/// drawn as an `.overlay` on top of the always-present tables instead.
struct CrossPurchaseView: View {
    @Environment(AppState.self) private var appState
    @State private var response: CrossPurchaseResponse?
    @State private var market: String?
    @State private var error: String?
    @State private var isLoading = false
    // Table cells can't be text-selected on macOS, so both tables carry a
    // selection and a right-click Copy instead. See Copyable.swift.
    @State private var designSel = Set<CrossPurchaseDesign.ID>()
    @State private var pairSel = Set<CrossPurchasePair.ID>()

    private var data: CrossPurchaseResponse? {
        market == appState.selectedMarket ? response : nil
    }

    private var currency: String? { appState.currentMarket?.currency }
    private var designRows: [CrossPurchaseDesign] { data?.designs ?? [] }
    private var pairRows: [CrossPurchasePair] { data?.pairs ?? [] }

    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "Cross-purchase", subtitle: subtitle, help: .crossPurchase)
            // Band and tables are UNCONDITIONAL siblings. A `@ViewBuilder` if/else
            // here — even one guarding the non-greedy band — turns the whole detail
            // into empty placeholder rows on macOS 26 (see the type doc comment). So
            // the band shows "—" while data is absent instead of being toggled away.
            headerBand
            Divider()
            SectionHeader(title: "Designs that sell other designs",
                          subtitle: "ranked by the sales their ads sent elsewhere",
                          count: designRows.count)
                .padding(.horizontal, Layout.Spacing.sm)
                .padding(.top, Layout.Spacing.xs)
            designsTable
            Divider()
            SectionHeader(title: "Clicked this, bought that",
                          subtitle: "biggest measured cross-sells",
                          count: pairRows.count)
                .padding(.horizontal, Layout.Spacing.sm)
                .padding(.top, Layout.Spacing.xs)
            pairsTable
        }
        .background(Theme.Colors.canvas)
        // Loading / error / "no data" are drawn as an overlay LAYER, never as a
        // VStack sibling of the tables — a conditional sibling blanks the detail.
        .overlay { stateOverlay }
        .task(id: appState.viewKey) { await load() }
    }

    private var subtitle: String {
        guard let asOf = data?.asOf else { return "\(appState.selectedMarket)" }
        return "\(appState.selectedMarket) · measured · through \(Format.euDate(asOf))"
    }

    // MARK: - Loading / error / "no data", drawn OVER the tables

    @ViewBuilder
    private var stateOverlay: some View {
        if let error {
            overlayMessage(title: "Cross-purchase unavailable", detail: error)
        } else if data == nil && isLoading {
            ProgressView("Loading cross-purchase…")
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Theme.Colors.canvas)
        } else if let d = data, !d.supported {
            overlayMessage(
                title: "No cross-purchase data yet",
                detail: d.note
                    ?? "The nightly pull banks this once the purchased-product report lands.")
        }
        // data present & supported → nothing drawn, the tables show through.
    }

    private func overlayMessage(title: String, detail: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.callout.weight(.semibold))
            Text(detail)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(Layout.Spacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .mdCard()
        .padding(Layout.Spacing.lg)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background(Theme.Colors.canvas)
    }

    // MARK: - Header band (stat cards)

    /// Always present, never toggled by a conditional (see the type doc comment).
    /// While data is absent the cards read "—", the way BidReport / Profit do it.
    private var headerBand: some View {
        let t = data?.totals
        return HStack(alignment: .top, spacing: Layout.Spacing.sm) {
            StatCard(title: "Halo sales",
                     value: t.map { Format.money($0.otherAsinSales, currency: currency) } ?? "—",
                     tint: (t?.otherAsinSales ?? 0) > 0 ? Theme.Colors.positive : .primary,
                     symbol: "arrow.triangle.branch",
                     subtitle: "ads sold a different design")
                .frame(maxHeight: .infinity, alignment: .topLeading)
                .mdCard()
            StatCard(title: "Cross-sell pairs",
                     value: "\(pairRows.count)",
                     symbol: "arrow.left.arrow.right",
                     subtitle: "clicked this, bought that")
                .frame(maxHeight: .infinity, alignment: .topLeading)
                .mdCard()
            StatCard(title: "Designs earning halo",
                     value: "\(designRows.filter { $0.otherSales > 0 }.count)",
                     symbol: "tshirt",
                     subtitle: "ads that sold something else")
                .frame(maxHeight: .infinity, alignment: .topLeading)
                .mdCard()
            StatCard(title: "Units",
                     value: Format.count(designRows.reduce(0) { $0 + $1.otherUnits }),
                     symbol: "shippingbox",
                     subtitle: "not-advertised units sold")
                .frame(maxHeight: .infinity, alignment: .topLeading)
                .mdCard()
        }
        .fixedSize(horizontal: false, vertical: true)
        .padding(.horizontal, Layout.Spacing.lg)
        .padding(.vertical, Layout.Spacing.md)
    }

    // MARK: - Tables

    /// Content-sized (as tall as its rows, capped) so it sits at the top and the
    /// pairs table below it takes the rest.
    private var designsTable: some View {
        Table(designRows, selection: $designSel) {
            TableColumn("Design") {
                AsinLink(asin: $0.advertisedAsin, text: $0.adGroup ?? $0.advertisedAsin, font: .body)
            }
            TableColumn("ASIN") { AsinLink(asin: $0.advertisedAsin) }
            TableColumn("Same design") {
                Text(Format.money($0.ownSales, currency: currency)).monospacedDigit()
            }
            TableColumn("Other designs") {
                Text(Format.money($0.otherSales, currency: currency))
                    .monospacedDigit()
                    .foregroundStyle($0.otherSales > 0 ? Theme.Colors.positive : .primary)
            }
            TableColumn("Halo share") {
                Text(Format.percent($0.otherPct)).monospacedDigit()
            }
            TableColumn("Designs sold") { Text("\($0.distinctOthers)").monospacedDigit() }
        }
        .copyableRows(designRows, primaryLabel: "ASIN",
                      primary: { $0.advertisedAsin ?? $0.adGroup ?? "—" },
                      row: { "\($0.advertisedAsin ?? "")\t\($0.adGroup ?? "")\t\($0.ownSales)\t\($0.otherSales)\t\($0.distinctOthers)" })
        .contentSizedTable(rows: designRows.count)
    }

    /// Greedy: fills the space below the designs table, with a floor so it can't be
    /// squeezed to nothing. This is what removes the dead space at the bottom.
    private var pairsTable: some View {
        Table(pairRows, selection: $pairSel) {
            TableColumn("Ad shown for") { AsinLink(asin: $0.advertisedAsin) }
            TableColumn("Design") { Text($0.adGroup ?? "—") }
            TableColumn("Bought instead") { AsinLink(asin: $0.purchasedAsin) }
            TableColumn("Sales") {
                Text(Format.money($0.sales, currency: currency)).monospacedDigit()
            }
            TableColumn("Units") { Text("\($0.units)").monospacedDigit() }
        }
        .copyableRows(pairRows, primaryLabel: "ASIN",
                      primary: { $0.purchasedAsin ?? "—" },
                      row: { "\($0.advertisedAsin ?? "")\t\($0.adGroup ?? "")\t\($0.purchasedAsin ?? "")\t\($0.sales)\t\($0.units)" })
        .frame(minHeight: 160)
    }

    private func load() async {
        isLoading = true
        error = nil
        defer { isLoading = false }
        let target = appState.selectedMarket
        do {
            let bridge = try appState.makeBridge()
            let result = try await bridge.call(
                CrossPurchaseResponse.self, ["crosspurchase"], market: target)
            guard !Task.isCancelled else { return }
            response = result
            market = target
        } catch {
            self.error = error.localizedDescription
            market = target
        }
    }
}
