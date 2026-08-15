import Foundation

/// Per-market persistence for the entity/data filters — Campaigns Type & State,
/// Targets Match, Audit action. Their meaning is market-specific ("enabled
/// standard" is a sensible view for US, "all" for a fresh KDP market), so each
/// market keeps its own choice. `@AppStorage` can't key on a runtime value, so
/// the views load through here when the market changes and save on edit.
/// Structural switchers that mean the same thing in every market (Promote/Prune,
/// sections, window sizes) use plain `@AppStorage` instead.
enum FilterPrefs {
    private static func key(_ base: String, _ market: String) -> String {
        "filter.\(base).\(market)"
    }

    static func load(_ base: String, market: String, default fallback: String) -> String {
        UserDefaults.standard.string(forKey: key(base, market)) ?? fallback
    }

    static func save(_ base: String, market: String, _ value: String) {
        UserDefaults.standard.set(value, forKey: key(base, market))
    }
}
