import SwiftUI

/// Product Royalty — what every Merch product earns you, and the one place to
/// change it.
///
/// These numbers are the money the rest of the app reasons with. Break-even
/// ACOS, the kill list, true profit and every bid or pause rule read them. They
/// used to live only in the engine's Python, so changing a royalty meant editing
/// code; this screen is what makes the app stand on its own.
///
/// Two kinds of row, one table:
///  * **Tee price ladder** — US standard tees earn a different royalty at each
///    list price, and it is a confirmed table rather than a formula.
///  * **Product types** — every other product carries one royalty and one price.
///
/// Every market is editable. Untouched rows come from the engine's built-in US
/// tables, or from what `derive_econ.py` worked out from the product export in
/// the other markets; your saved number always wins over either. Amazon fixes a
/// maximum price per product per market, so a figure read off the Merch
/// dashboard is definitive, while a derived median only reflects whatever mix
/// of listings happens to exist.
///
/// Break-even is never typed. It is royalty ÷ price, computed on save. A typed
/// percentage is the easiest number in this whole system to get wrong.
struct ProductRoyaltyView: View {
    @Environment(AppState.self) private var appState

    enum Tab: String, CaseIterable, Identifiable {
        case types, tees
        var id: String { rawValue }
        var label: String { self == .types ? "Product types" : "Tee price ladder" }
    }

    @State private var payload: RoyaltyResponse?
    @State private var isLoading = false
    @State private var loadError: String?
    @State private var tab: Tab = .types
    @State private var filterText = ""
    @State private var selection = Set<RoyaltyRow.ID>()

    @State private var editPrice = ""
    @State private var editRoyalty = ""
    @State private var editNote = ""
    @State private var saving = false
    @State private var actionError: String?

    @State private var showingAdd = false
    @State private var newName = ""
    @State private var newPrice = ""
    @State private var newRoyalty = ""

    @State private var colPrefs: TableColumnCustomization<RoyaltyRow> =
        ColumnPrefs.load(TableID.productRoyalty)
    @State private var sort = SortPrefs.load(
        TableID.productRoyalty, fields: sortFields,
        fallback: [KeyPathComparator(\RoyaltyRow.adGroupsSort, order: .reverse)])

    private static let sortFields: [String: KeyPathComparator<RoyaltyRow>] = [
        "item": .init(\.nameSort), "price": .init(\.priceSort),
        "royalty": .init(\.royaltySort), "breakeven": .init(\.breakEvenSort),
        "usedby": .init(\.adGroupsSort), "source": .init(\.source),
    ]

    private var currency: String { payload?.currency ?? "USD" }
    /// The engine still reports this per market. It is true everywhere today;
    /// the flag stays so a market that ever becomes read-only says so itself
    /// rather than silently accepting edits that go nowhere.
    private var editable: Bool {
        Self.canEdit(payloadMarket: payload?.market,
                     selectedMarket: appState.selectedMarket,
                     engineEditable: payload?.editable ?? false)
    }
    /// The price ladder only means anything for US tees, which earn a different
    /// royalty at each rung. Elsewhere there is one table.
    private var showsTabs: Bool { !(payload?.teePrices.isEmpty ?? true) }

    private var allRows: [RoyaltyRow] {
        guard let payload else { return [] }
        return tab == .tees && showsTabs
            ? payload.teePrices.map(RoyaltyRow.init(tee:))
            : payload.productTypes.map(RoyaltyRow.init(type:))
    }

    private var rows: [RoyaltyRow] {
        allRows
            .filter { filterText.isEmpty || $0.name.localizedStandardContains(filterText) }
            .sorted(using: sort)
    }

    private var selected: RoyaltyRow? {
        guard let id = selection.first else { return nil }
        return allRows.first { $0.id == id }
    }

    private var overrideCount: Int { payload?.overrides ?? 0 }

    /// One sentence, built from the engine's counted `basis`.
    private var basisCaption: String {
        guard let basis = payload?.basis else {
            return "Anything you save wins over the number the engine ships with."
        }
        return "Rows come from \(basis). Anything you save wins over them."
    }
    private var advertisedTypes: Int {
        (payload?.productTypes ?? []).filter { ($0.adGroups ?? 0) > 0 }.count
    }

    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "Product Royalty",
                       subtitle: "\(appState.currentMarket?.displayLabel ?? appState.selectedMarket) · what each product earns",
                       help: .productRoyalty)
            statusBand
            errorBand
            filterBar
            SectionHeader(title: showsTabs ? tab.label : "Product types",
                          subtitle: payload?.basis ?? "royalties the engine prices with",
                          count: rows.count)
                .padding(.horizontal, Layout.Spacing.sm)
            Divider()
            // The table stays UNCONDITIONAL in the body and the loading / error /
            // empty states ride as an overlay. A greedy Table toggled by an
            // if/else blanks the whole detail on macOS 26.
            royaltyTable
                .overlay { tableOverlay }
            editBar
        }
        .background(Theme.Colors.canvas)
        .task(id: appState.viewKey) { await load() }
        .onChange(of: selection) { fillEditor() }
        .onChange(of: tab) { selection = []; fillEditor() }
        .onChange(of: colPrefs) { ColumnPrefs.save(TableID.productRoyalty, colPrefs) }
        .onChange(of: sort) { SortPrefs.save(TableID.productRoyalty, sort, fields: Self.sortFields) }
    }

    // MARK: - Bands

    private var statusBand: some View {
        HStack(spacing: Layout.Spacing.sm) {
            StatCard(title: "Priced products",
                     value: Format.count((payload?.productTypes.count ?? 0)),
                     symbol: "shippingbox",
                     subtitle: "types the engine prices")
                .mdCard()
            StatCard(title: "Advertised now", value: Format.count(advertisedTypes),
                     tint: advertisedTypes == 0 ? Theme.Colors.muted : Theme.Colors.positive,
                     symbol: "megaphone",
                     subtitle: "types with ad groups running")
                .mdCard()
            StatCard(title: "Your edits",
                     value: overrideCount == 0 ? "—" : Format.count(overrideCount),
                     tint: overrideCount == 0 ? Theme.Colors.muted : Theme.Colors.accent,
                     symbol: "pencil",
                     subtitle: "royalties you set yourself")
                .mdCard()
        }
        .fixedSize(horizontal: false, vertical: true)
        .padding(.horizontal, Layout.Spacing.lg)
        .padding(.vertical, Layout.Spacing.sm)
    }

    /// A saved royalty the engine cannot read closes the econ gate, which stops
    /// every money decision. That must be loud, never a quiet empty table.
    @ViewBuilder
    private var errorBand: some View {
        if let errors = payload?.errors, !errors.isEmpty {
            VStack(alignment: .leading, spacing: 2) {
                Label("A saved royalty cannot be read — economics are frozen until it is fixed",
                      systemImage: "exclamationmark.triangle.fill")
                    .font(.callout.weight(.semibold))
                    .foregroundStyle(Theme.Colors.critical)
                ForEach(errors, id: \.self) { Text($0).font(.caption).foregroundStyle(.secondary) }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(Layout.Spacing.sm)
            .background(Theme.Colors.critical.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
            .padding(.horizontal, Layout.Spacing.lg)
            .padding(.bottom, Layout.Spacing.xs)
        }
    }

    private var filterBar: some View {
        FilterBar {
            if showsTabs {
                Picker("View", selection: $tab) {
                    ForEach(Tab.allCases) { Text($0.label).tag($0) }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .frame(width: 260)
            } else {
                // Say what the engine COUNTED, never a per-market guess. The
                // hard-coded version of this line told DE its numbers came
                // from the product export while 13 of its 14 rows were the
                // shipped table — which sends the operator off to re-export a
                // catalogue that cannot change them (found 2026-08-24).
                Text(basisCaption)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        } trailing: {
            if editable {
                Button {
                    newName = ""; newPrice = ""; newRoyalty = ""
                    showingAdd = true
                } label: {
                    Label(tab == .tees ? "Add price" : "Add type", systemImage: "plus")
                }
                .help(tab == .tees
                      ? "Add a list price the ladder does not cover yet."
                      : "Add a product type the engine has not met yet.")
                .popover(isPresented: $showingAdd, arrowEdge: .bottom) { addPopover }
            }
            if !allRows.isEmpty {
                TextField("Filter", text: $filterText)
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 180)
            }
        }
    }

    // MARK: - Table

    private var royaltyTable: some View {
        Table(rows, selection: $selection, sortOrder: $sort.descendingFirst(),
              columnCustomization: $colPrefs) {
            TableColumn("Item", value: \.nameSort) { row in
                HStack(spacing: Layout.Spacing.xs) {
                    Text(row.name).lineLimit(1).truncationMode(.tail)
                    ForEach(row.flags, id: \.text) { flag in
                        StatusBadge(text: flag.text, symbol: flag.symbol, tint: flag.tint)
                            .fixedSize()
                            .help(flag.help)
                    }
                    Spacer(minLength: 0)
                }
            }
            .width(min: 180, ideal: 280)
            .customizationID("item")
            TableColumn("List price", value: \.priceSort) { row in
                MoneyText(value: row.price, currency: currency)
                    .foregroundStyle(row.priceIsImplied ? .secondary : .primary)
                    .help(row.priceIsImplied
                          ? "Worked back from the break-even, so it can land a few cents off. Save a price here and it becomes exact."
                          : "The list price this royalty was worked out from.")
            }
            .width(min: 80, ideal: 110)
            .customizationID("price")
            TableColumn("Royalty", value: \.royaltySort) { row in
                MoneyText(value: row.royalty, currency: currency)
            }
            .width(min: 80, ideal: 110)
            .customizationID("royalty")
            TableColumn("Break-even ACOS", value: \.breakEvenSort) { row in
                Text(Format.percent(row.breakEven))
                    .font(Typography.tableNumeral)
                    .help("Spend more than this share of the sale and the order loses money.")
            }
            .width(min: 96, ideal: 130)
            .customizationID("breakeven")
            TableColumn("Advertised by", value: \.adGroupsSort) { row in
                if let n = row.adGroups, n > 0 {
                    Text(Format.count(n) + " ad groups")
                        .font(Typography.tableNumeral)
                        .foregroundStyle(.secondary)
                } else {
                    Text("—").foregroundStyle(.tertiary)
                }
            }
            .width(min: 96, ideal: 128)
            .customizationID("usedby")
            TableColumn("Number from", value: \.source) { row in
                StatusBadge(text: row.sourceLabel, symbol: row.sourceSymbol,
                            tint: row.sourceTint)
                    .fixedSize()
                    .help(row.sourceHelp)
            }
            .width(min: 92, ideal: 116)
            .customizationID("source")
        }
        .background(Theme.Colors.surface)
        .contextMenu(forSelectionType: RoyaltyRow.ID.self) { ids in
            if editable, let row = allRows.first(where: { ids.contains($0.id) }),
               row.source == "operator" {
                Button("Reset to built-in") { Task { await reset(row) } }
            }
        }
    }

    @ViewBuilder
    private var tableOverlay: some View {
        if isLoading && payload == nil {
            ProgressView("Loading royalties…")
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Theme.Colors.surface)
        } else if let loadError {
            ContentUnavailableView {
                Label("Royalties unavailable", systemImage: "banknote")
            } description: {
                Text(loadError)
            } actions: {
                Button("Retry") { Task { await load() } }
            }
            .background(Theme.Colors.surface)
        } else if allRows.isEmpty && payload != nil {
            ContentUnavailableView {
                Label("No royalties yet", systemImage: "banknote")
            } description: {
                Text("Nothing is priced yet. Add a product type to give the engine something to work with.")
            }
            .background(Theme.Colors.surface)
        } else if rows.isEmpty {
            ContentUnavailableView.search(text: filterText)
                .background(Theme.Colors.surface)
        }
    }

    // MARK: - Editing

    /// Always present, so the numbers you can change never move around on you.
    private var editBar: some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.xs) {
            Divider()
            if let row = selected {
                HStack(spacing: Layout.Spacing.sm) {
                    Text(row.name).font(.callout.weight(.semibold)).frame(minWidth: 140, alignment: .leading)
                    // A tee row IS its price — that is the key the ladder is
                    // keyed on. Letting the price be edited here would quietly
                    // ADD a rung instead of moving this one, so it is locked and
                    // "Add price" is the way to create a new one.
                    LabeledField(label: "List price", text: $editPrice,
                                 disabled: !editable || row.kind == .tee)
                    LabeledField(label: "Royalty", text: $editRoyalty, disabled: !editable)
                    VStack(alignment: .leading, spacing: 0) {
                        Text("BREAK-EVEN").font(Typography.cardLabel).foregroundStyle(Theme.Colors.muted)
                        Text(previewBreakEven)
                            .font(Typography.tableNumeral)
                            .foregroundStyle(previewBreakEven == "—" ? .secondary : .primary)
                    }
                    .frame(width: 84, alignment: .leading)
                    Button("Save") { Task { await save(row) } }
                        .keyboardShortcut(.defaultAction)
                        .disabled(saving || !editable || !canSave)
                    if row.source == "operator" {
                        Button("Reset to built-in") { Task { await reset(row) } }
                            .disabled(saving || !editable)
                            .help("Drop your edit and use the number the engine ships with.")
                    }
                    if saving { ProgressView().controlSize(.small) }
                    Spacer(minLength: 0)
                }
            } else {
                Text("Pick a row to change what it earns. Break-even is worked out from the royalty and the price — you never type a percentage.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            ActionErrorBar(message: $actionError)
        }
        .textFieldStyle(.roundedBorder)
        .padding(.horizontal, Layout.Spacing.lg)
        .padding(.vertical, Layout.Spacing.sm)
        .background(Theme.Colors.surface)
    }

    private var addPopover: some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.sm) {
            Text(tab == .tees ? "Add a tee list price" : "Add a product type")
                .font(.headline)
            if tab == .types {
                LabeledField(label: "Type name", text: $newName, width: 200)
                Text("Use the engine's own spelling, e.g. beach_towel.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            HStack(spacing: Layout.Spacing.sm) {
                LabeledField(label: "List price", text: $newPrice)
                LabeledField(label: "Royalty", text: $newRoyalty)
            }
            HStack {
                Spacer()
                Button("Cancel") { showingAdd = false }
                    .keyboardShortcut(.cancelAction)
                Button("Add") { Task { await add() } }
                    .keyboardShortcut(.defaultAction)
                    .disabled(saving || !canAdd)
            }
        }
        .padding()
        .frame(width: 280)
    }

    /// A German or French keyboard types "21,99". `Double("21,99")` is nil, so
    /// without this the Save button just stays dead and never says why.
    /// The engine is fed the normalised form, never the typed one.
    /// `nonisolated` for the same reason as DashboardView.windowLabel:
    /// string in, number out, nothing on screen.
    nonisolated static func decimal(_ text: String) -> Double? {
        let cleaned = text
            .trimmingCharacters(in: .whitespaces)
            .replacingOccurrences(of: ",", with: ".")
        return cleaned.isEmpty ? nil : Double(cleaned)
    }

    nonisolated static func canEdit(payloadMarket: String?, selectedMarket: String,
                                    engineEditable: Bool) -> Bool {
        engineEditable && payloadMarket == selectedMarket
    }

    private static func plain(_ text: String) -> String {
        decimal(text).map { String(format: "%.2f", $0) } ?? text
    }

    private var canAdd: Bool {
        guard let price = Self.decimal(newPrice), let royalty = Self.decimal(newRoyalty),
              price > 0, royalty > 0, royalty < price else { return false }
        return tab == .tees || !newName.trimmingCharacters(in: .whitespaces).isEmpty
    }

    private var canSave: Bool {
        guard let price = Self.decimal(editPrice), let royalty = Self.decimal(editRoyalty)
        else { return false }
        return price > 0 && royalty > 0 && royalty < price
    }

    private var previewBreakEven: String {
        guard let price = Self.decimal(editPrice), let royalty = Self.decimal(editRoyalty),
              price > 0, royalty > 0, royalty < price else { return "—" }
        return Format.percent(royalty / price)
    }

    private func fillEditor() {
        actionError = nil
        guard let row = selected else { editPrice = ""; editRoyalty = ""; return }
        editPrice = row.price.map { String(format: "%.2f", $0) } ?? ""
        editRoyalty = row.royalty.map { String(format: "%.2f", $0) } ?? ""
        editNote = row.note ?? ""
    }

    // MARK: - Bridge
    //
    // Royalties are local config plus the market DB — no Amazon call, nothing
    // that touches the live account. Same direct-call precedent as KDP Books.

    private func load() async {
        let market = appState.selectedMarket
        payload = nil
        selection = []
        showingAdd = false
        fillEditor()
        isLoading = true
        defer { if !Task.isCancelled { isLoading = false } }
        loadError = nil
        do {
            let bridge = try appState.makeBridge()
            // Royalties are per market — Amazon fixes a different maximum price
            // in each one. Without this the screen silently reads US everywhere.
            let r = try await bridge.call(RoyaltyResponse.self, ["royalties"],
                                          market: appState.selectedMarket)
            guard !Task.isCancelled, market == appState.selectedMarket,
                  r.market == market else { return }
            payload = r
            if r.teePrices.isEmpty { tab = .types }
            fillEditor()
        } catch {
            guard !Task.isCancelled, market == appState.selectedMarket else { return }
            loadError = error.localizedDescription
        }
    }

    private func save(_ row: RoyaltyRow) async {
        let price = Self.plain(editPrice), royalty = Self.plain(editRoyalty)
        await write(args: row.kind == .tee
                    ? ["royalty-set", "--price", price, "--royalty", royalty]
                    : ["royalty-set", "--type", row.key, "--price", price, "--royalty", royalty])
    }

    private func add() async {
        let price = Self.plain(newPrice), royalty = Self.plain(newRoyalty)
        let args: [String] = tab == .tees
            ? ["royalty-set", "--price", price, "--royalty", royalty]
            : ["royalty-set", "--type", newName.trimmingCharacters(in: .whitespaces),
               "--price", price, "--royalty", royalty]
        await write(args: args)
        if actionError == nil { showingAdd = false }
    }

    private func reset(_ row: RoyaltyRow) async {
        await write(args: row.kind == .tee
                    ? ["royalty-clear", "--price", String(format: "%.2f", row.price ?? 0)]
                    : ["royalty-clear", "--type", row.key])
    }

    private func write(args: [String]) async {
        guard editable, let market = payload?.market,
              market == appState.selectedMarket else {
            actionError = "Royalties are not loaded for the selected market."
            return
        }
        saving = true
        defer { saving = false }
        actionError = nil
        do {
            let bridge = try appState.makeBridge()
            _ = try await bridge.call(RoyaltySaveResponse.self, args,
                                      market: appState.selectedMarket, preferWorker: false)
            await load()
        } catch {
            actionError = error.localizedDescription
        }
    }
}

/// A caption over a field. The royalty and the price are two small numbers that
/// must not be mixed up, so each one is labelled where it is typed.
private struct LabeledField: View {
    let label: String
    @Binding var text: String
    var width: CGFloat = 88
    var disabled: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(label.uppercased())
                .font(Typography.cardLabel)
                .foregroundStyle(Theme.Colors.muted)
            TextField("", text: $text)
                .frame(width: width)
                .multilineTextAlignment(.trailing)
                .disabled(disabled)
                .help(disabled
                      ? "A tee's price is which rung of the ladder this is. To price a tee Amazon does not list yet, use “Add price”."
                      : "")
        }
    }
}

/// Both kinds of royalty row in one shape, so the screen has ONE table. A Table
/// swapped in and out by a condition blanks the whole detail on macOS 26.
struct RoyaltyRow: Identifiable, Hashable {
    enum Kind: Hashable { case tee, type }

    struct Flag: Hashable {
        let text: String
        let symbol: String
        let tint: Color
        let help: String
    }

    let kind: Kind
    let id: String
    let key: String
    let name: String
    let price: Double?
    let royalty: Double?
    let breakEven: Double?
    let adGroups: Int?
    let source: String
    let note: String?
    let flags: [Flag]
    /// True when the price shown was worked BACK from the break-even rather than
    /// stored. Nothing does that any more — the engine keeps the real list price
    /// alongside every royalty since 2026-08-21 — so this is false everywhere.
    /// It stays because a future derived row could reintroduce a divided price,
    /// and dimming it is how the screen would admit that.
    let priceIsImplied: Bool

    init(tee: RoyaltyTeePrice) {
        var flags: [Flag] = []
        if tee.extrapolated {
            flags.append(Flag(text: "guessed", symbol: "questionmark.circle",
                              tint: Theme.Colors.caution,
                              help: "This royalty was worked out from the confirmed range, not read off your dashboard. Confirm it and the flag clears."))
        }
        if tee.growthPriced {
            flags.append(Flag(text: "rank push", symbol: "arrow.up.forward",
                              tint: Theme.Colors.information,
                              help: "Priced below the growth floor on purpose. It earns this royalty, but rules act on the floor's economics so a price cut is not undone by an automatic pause."))
        }
        self.kind = .tee
        self.id = "tee-\(tee.priceCents)"
        self.key = String(tee.priceCents)
        self.name = Format.money(tee.price, currency: "USD") + " tee"
        self.price = tee.price
        self.royalty = tee.royalty
        self.breakEven = tee.breakEven
        self.adGroups = nil
        self.source = tee.source
        self.note = tee.note
        self.flags = flags
        self.priceIsImplied = false        // a rung IS its price
    }

    init(type: RoyaltyProductType) {
        var flags: [Flag] = []
        if type.model == "A" {
            flags.append(Flag(text: "growth", symbol: "chart.line.uptrend.xyaxis",
                              tint: Theme.Colors.accent,
                              help: "Optimised for conversion toward a growth ceiling rather than straight to break-even."))
        }
        if type.productType == "standard_tshirt" {
            flags.append(Flag(text: "per design", symbol: "list.number",
                              tint: Theme.Colors.neutralAccent,
                              help: "Each US tee is priced from its OWN list price — see the Tee price ladder tab. This row is the floor: it is what a tee cohort uses, and what stands in when a design's price is unknown."))
        }
        self.kind = .type
        self.id = "type-\(type.productType)"
        self.key = type.productType
        self.name = type.label
        self.price = type.price
        self.royalty = type.royalty
        self.breakEven = type.breakEven
        self.adGroups = type.adGroups
        self.source = type.source
        self.note = type.note
        self.flags = flags
        self.priceIsImplied = false     // every price is now a real one
    }

    var sourceLabel: String {
        switch source {
        case "operator": "yours"
        case "derived": "your export"
        default: "built-in"
        }
    }

    var sourceSymbol: String {
        switch source {
        case "operator": "pencil"
        case "derived": "function"
        default: "shippingbox"
        }
    }

    var sourceTint: Color {
        switch source {
        case "operator": Theme.Colors.accent
        case "derived": Theme.Colors.neutralAccent
        default: Theme.Colors.muted
        }
    }

    var sourceHelp: String {
        switch source {
        case "operator": "You set this number. “Reset to built-in” puts the shipped one back."
        case "derived": "Worked out from your product export — the median royalty per unit for this type in this market."
        default: "The number the engine ships with, confirmed off the Merch dashboard."
        }
    }

    // Non-optional sort keys — `Double?` is not Comparable, so a missing figure
    // sorts below every real one rather than failing to sort.
    /// A rung sorts by its price, a type by its name — one comparator, because
    /// prices sorted as text put $9.99 after $24.99.
    var nameSort: String {
        kind == .tee ? String(format: "%09.2f", price ?? 0) : name
    }

    var priceSort: Double { price ?? -1 }
    var royaltySort: Double { royalty ?? -1 }
    var breakEvenSort: Double { breakEven ?? -1 }
    var adGroupsSort: Int { adGroups ?? -1 }
}

#Preview {
    ProductRoyaltyView()
        .environment(AppState())
}
