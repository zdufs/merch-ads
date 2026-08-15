import Foundation

enum PinKind: String, Codable, Hashable, Sendable {
    case campaign, adGroup, target, asin
}

/// A pinned entity on the per-market watchlist. Carries enough parent IDs to
/// re-resolve against the engine (modeled on Route's entity cases). Purely a
/// view — pinning never touches Amazon.
struct WatchlistPin: Codable, Identifiable, Hashable, Sendable {
    let kind: PinKind
    let market: String
    let campaignID: String?
    let adGroupID: String?
    let targetID: String?
    let asin: String?
    let label: String

    /// Stable identity for dedup: kind + the entity's own id.
    var id: String {
        switch kind {
        case .campaign: "campaign:\(campaignID ?? "")"
        case .adGroup:  "adGroup:\(adGroupID ?? "")"
        case .target:   "target:\(targetID ?? "")"
        case .asin:     "asin:\(asin ?? "")"
        }
    }

    /// The argv fragment the `watchlist` endpoint expects for this pin.
    var engineDict: [String: String] {
        switch kind {
        case .campaign: ["kind": "campaign", "campaign_id": campaignID ?? ""]
        case .adGroup:  ["kind": "adgroup", "ad_group_id": adGroupID ?? ""]
        case .target:   ["kind": "target", "target_id": targetID ?? ""]
        case .asin:     ["kind": "asin", "asin": asin ?? ""]
        }
    }
}

/// Per-market pin storage in UserDefaults (`watchlist.v1.<market>`). Private to
/// the user; no engine involvement beyond resolving pins for display.
enum WatchlistStore {
    static let capacity = 1000

    private static func key(_ market: String) -> String { "watchlist.v1.\(market)" }

    static func pins(market: String) -> [WatchlistPin] {
        guard let data = UserDefaults.standard.data(forKey: key(market)),
              let pins = try? JSONDecoder().decode([WatchlistPin].self, from: data)
        else { return [] }
        return pins
    }

    static func isPinned(_ pin: WatchlistPin, market: String) -> Bool {
        pins(market: market).contains { $0.id == pin.id }
    }

    static func add(_ pin: WatchlistPin, market: String) {
        var current = pins(market: market)
        guard !current.contains(where: { $0.id == pin.id }) else { return }
        guard current.count < capacity else { return }
        current.append(pin)
        persist(current, market: market)
    }

    static func remove(_ pin: WatchlistPin, market: String) {
        persist(pins(market: market).filter { $0.id != pin.id }, market: market)
    }

    static func toggle(_ pin: WatchlistPin, market: String) {
        if isPinned(pin, market: market) { remove(pin, market: market) }
        else { add(pin, market: market) }
    }

    private static func persist(_ pins: [WatchlistPin], market: String) {
        if let data = try? JSONEncoder().encode(pins) {
            UserDefaults.standard.set(data, forKey: key(market))
        }
    }
}
