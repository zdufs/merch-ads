import Foundation

// UserDefaults-backed settings, shared between SettingsView (@AppStorage) and
// the non-view code (bridge construction, DB paths).

enum AppSettings {
    static let engineRootKey = "engineRootPath"
    static let pythonPathKey = "pythonPath"
    static let alwaysConfirmKey = "alwaysConfirm"
    static let selectedMarketKey = "selectedMarket"
    static let fastBridgeKey = "fastBridge"          // persistent serve worker (default on)
    static let showMenuBarKey = "showMenuBarExtra"
    static let appearanceKey = "appAppearance"        // System / Light / Dark (default System)

    static var isUnitTesting: Bool {
        ProcessInfo.processInfo.environment["MERCHADS_UNIT_TESTS"] == "YES"
    }

    static var defaultEngineRoot: String {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Biznis/ClaudeCode/POD/Ads").path
    }

    static var engineRoot: URL {
        let stored = UserDefaults.standard.string(forKey: engineRootKey) ?? ""
        return URL(fileURLWithPath: stored.isEmpty ? defaultEngineRoot : stored)
    }

    /// Empty string means "auto-resolve via the login shell".
    static var pythonOverride: String? {
        let stored = UserDefaults.standard.string(forKey: pythonPathKey) ?? ""
        return stored.isEmpty ? nil : stored
    }

    /// US uses the original ads_data.sqlite; other markets are suffixed.
    static func databaseURL(market: String) -> URL {
        let file = market == "US" ? "ads_data.sqlite" : "ads_data_\(market).sqlite"
        return engineRoot.appendingPathComponent(file)
    }
}
