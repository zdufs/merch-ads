import SwiftUI

enum TableID {
    static let campaigns = "campaigns"
    static let adGroups = "ad-groups"
    static let targets = "targets"
    static let allTargets = "all-targets"
    static let searchTerms = "search-terms"
    static let killBleeding = "kill-list.bleeding"
    static let killStale = "kill-list.stale"
    static let harvest = "harvest"
    static let harvestPrune = "harvest.prune"
    static let approvalNegatives = "approvals.negatives"
    static let approvalPauses = "approvals.pauses"
    static let bidReport = "bid-report"
    static let demandSeeds = "demand.seeds"
    static let demandSellers = "demand.sellers"
    static let seasonalTags = "seasonal.tags"
    static let seasonalSeasons = "seasonal.seasons"
    static let allMarkets = "all-markets"
    static let liveStatus = "live-status"
    static let audit = "audit"
    static let profitTypes = "profit.types"
    static let profitDesigns = "profit.designs"
    static let health = "health"
    static let halo = "halo"
    static let accumulatedAsins = "accumulated.asins"
    static let accumulatedKeywords = "accumulated.keywords"
    static let watchlist = "watchlist"
    static let kdpBooks = "kdp-books"
    static let productRoyalty = "product-royalty"

    /// Every legacy spelling found in the pre-Phase-3 TablePrefs sweep.
    static let legacyAliases: [String: String] = [
        "adgroups": adGroups,
        "searchterms": searchTerms,
        "killList": killBleeding,
        "staleList": killStale,
        "harvestPrune": harvestPrune,
        "bidReport": bidReport,
        "demandfeed.seeds": demandSeeds,
        "demand.proven": demandSellers,
        "demandfeed.sellers": demandSellers,
        "allMarkets": allMarkets,
        "allmarkets": allMarkets,
        "liveStatus": liveStatus,
    ]
}

enum LegacyPreferenceMigration {
    static let markerKey = "tablePrefs.canonicalIDs.v1"

    static func migrate(defaults: UserDefaults = .standard) {
        guard !defaults.bool(forKey: markerKey) else { return }
        for prefix in ["columns", "sort"] {
            for (oldID, canonicalID) in TableID.legacyAliases where oldID != canonicalID {
                let oldKey = "\(prefix).\(oldID)"
                let canonicalKey = "\(prefix).\(canonicalID)"
                guard defaults.object(forKey: canonicalKey) == nil,
                      let value = defaults.object(forKey: oldKey) else { continue }
                defaults.set(value, forKey: canonicalKey)
            }
        }
        defaults.set(true, forKey: markerKey)
    }
}

enum ColumnPrefs {
    static func load<Row>(_ key: String, defaults: UserDefaults = .standard) -> TableColumnCustomization<Row> {
        guard let data = defaults.data(forKey: "columns.\(key)"),
              let value = decode(data, as: Row.self) else {
            return TableColumnCustomization<Row>()
        }
        return value
    }

    static func save<Row>(_ key: String, _ value: TableColumnCustomization<Row>,
                          defaults: UserDefaults = .standard) {
        if let data = encode(value) {
            defaults.set(data, forKey: "columns.\(key)")
        }
    }

    static func encode<Row>(_ value: TableColumnCustomization<Row>) -> Data? {
        try? JSONEncoder().encode(value)
    }

    static func decode<Row>(_ data: Data, as _: Row.Type) -> TableColumnCustomization<Row>? {
        try? JSONDecoder().decode(TableColumnCustomization<Row>.self, from: data)
    }
}

struct SavedSortDescriptor: Codable, Equatable, Hashable, Sendable {
    let field: String
    let ascending: Bool
}

enum SortPrefs {
    static func load<Row>(_ key: String, fields: [String: KeyPathComparator<Row>],
                          fallback: [KeyPathComparator<Row>],
                          defaults: UserDefaults = .standard) -> [KeyPathComparator<Row>] {
        guard let raw = defaults.string(forKey: "sort.\(key)"),
              let descriptor = decode(raw) else { return fallback }
        return comparators([descriptor], fields: fields, fallback: fallback)
    }

    static func save<Row>(_ key: String, _ order: [KeyPathComparator<Row>],
                          fields: [String: KeyPathComparator<Row>],
                          defaults: UserDefaults = .standard) {
        guard let descriptor = descriptors(order, fields: fields).first else { return }
        defaults.set(encode(descriptor), forKey: "sort.\(key)")
    }

    static func descriptors<Row>(_ order: [KeyPathComparator<Row>],
                                 fields: [String: KeyPathComparator<Row>]) -> [SavedSortDescriptor] {
        order.compactMap { comparator in
            guard let name = fields.first(where: { $0.value.keyPath == comparator.keyPath })?.key else { return nil }
            return SavedSortDescriptor(field: name, ascending: comparator.order != .reverse)
        }
    }

    static func comparators<Row>(_ descriptors: [SavedSortDescriptor],
                                 fields: [String: KeyPathComparator<Row>],
                                 fallback: [KeyPathComparator<Row>]) -> [KeyPathComparator<Row>] {
        let values = descriptors.compactMap { descriptor -> KeyPathComparator<Row>? in
            guard var comparator = fields[descriptor.field] else { return nil }
            comparator.order = descriptor.ascending ? .forward : .reverse
            return comparator
        }
        return values.isEmpty ? fallback : values
    }

    private static func decode(_ raw: String) -> SavedSortDescriptor? {
        let parts = raw.split(separator: "|")
        guard parts.count == 2 else { return nil }
        return SavedSortDescriptor(field: String(parts[0]), ascending: parts[1] != "desc")
    }

    private static func encode(_ descriptor: SavedSortDescriptor) -> String {
        "\(descriptor.field)|\(descriptor.ascending ? "asc" : "desc")"
    }
}

struct SavedView: Codable, Equatable, Identifiable, Sendable {
    static let currentVersion = 1

    let version: Int
    let tableID: String
    let name: String
    let filters: [String: String]
    let sortDescriptors: [SavedSortDescriptor]
    let columnCustomization: Data?

    var id: String { "\(tableID)|\(name.lowercased())" }

    init(version: Int = currentVersion, tableID: String, name: String,
         filters: [String: String], sortDescriptors: [SavedSortDescriptor],
         columnCustomization: Data?) {
        self.version = version
        self.tableID = tableID
        self.name = name.trimmingCharacters(in: .whitespacesAndNewlines)
        self.filters = filters
        self.sortDescriptors = sortDescriptors
        self.columnCustomization = columnCustomization
    }

    func isValid(for expectedTableID: String) -> Bool {
        version == Self.currentVersion && tableID == expectedTableID && !name.isEmpty
    }
}

enum SavedViewStore {
    static func load(tableID: String, defaults: UserDefaults = .standard) -> [SavedView] {
        guard let data = defaults.data(forKey: key(tableID)),
              let decoded = try? JSONDecoder().decode([SavedView].self, from: data) else { return [] }
        return decoded.filter { $0.isValid(for: tableID) }.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
    }

    static func save(_ view: SavedView, defaults: UserDefaults = .standard) {
        guard view.isValid(for: view.tableID) else { return }
        var views = load(tableID: view.tableID, defaults: defaults)
        views.removeAll { $0.name.caseInsensitiveCompare(view.name) == .orderedSame }
        views.append(view)
        persist(views, tableID: view.tableID, defaults: defaults)
    }

    static func delete(_ view: SavedView, defaults: UserDefaults = .standard) {
        let remaining = load(tableID: view.tableID, defaults: defaults).filter { $0.id != view.id }
        persist(remaining, tableID: view.tableID, defaults: defaults)
    }

    private static func persist(_ views: [SavedView], tableID: String, defaults: UserDefaults) {
        if let data = try? JSONEncoder().encode(views) {
            defaults.set(data, forKey: key(tableID))
        }
    }

    private static func key(_ tableID: String) -> String { "savedViews.v1.\(tableID)" }
}
