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

    /// The engine shipped inside the app, at `Contents/Resources/engine`.
    ///
    /// nil when the app was built without it — the plain `xcodebuild` loop and
    /// the stress harness both produce that, and both must keep working. The
    /// bundled copy is preferred when it exists, because then the Python the app
    /// runs is always the Python the app was tested against: a repo checkout
    /// that has moved on (or been deleted) can no longer change what the
    /// installed app does.
    static var bundledEngineRoot: URL? {
        guard let resources = Bundle.main.resourceURL else { return nil }
        let engine = resources.appendingPathComponent("engine")
        return FileManager.default.fileExists(atPath: engine.appendingPathComponent("appctl.py").path)
            ? engine : nil
    }

    /// The interpreter shipped inside the app, at `Contents/Resources/python`.
    ///
    /// It carries its own OpenSSL and SQLite and has `requests` installed, so
    /// nothing about the operator's machine — no Homebrew, no pip, no Command
    /// Line Tools — decides whether the app can talk to Amazon or read a
    /// database. nil when the app was built without it.
    static var bundledPython: URL? {
        guard let resources = Bundle.main.resourceURL else { return nil }
        let python = resources.appendingPathComponent("python/bin/python3")
        return FileManager.default.isExecutableFile(atPath: python.path) ? python : nil
    }

    /// Empty string means "auto-resolve via the login shell".
    static var pythonOverride: String? {
        let stored = UserDefaults.standard.string(forKey: pythonPathKey) ?? ""
        return stored.isEmpty ? nil : stored
    }

    /// Where the `.sqlite` files actually live.
    ///
    /// The databases sit in the REPO ROOT; `appctl.py` sits in `engine/` under
    /// it. The engine moved into that subfolder on 2026-08-15 and this setting
    /// was repointed at `…/Ads/engine` so the bridge could still find appctl —
    /// which silently sent every direct database read into a folder with no
    /// databases in it. "DB direct" showed "—" for every market and the sidebar
    /// footer said "no local data" for five days.
    ///
    /// So resolve it by looking, not by assuming: whichever of the configured
    /// folder or its parent actually holds `ads_data.sqlite` wins. Both layouts
    /// work, and moving the engine again cannot break it.
    static func dataRoot(under root: URL, fileManager: FileManager = .default) -> URL {
        func holdsDatabases(_ folder: URL) -> Bool {
            fileManager.fileExists(atPath: folder.appendingPathComponent("ads_data.sqlite").path)
        }
        if holdsDatabases(root) { return root }
        let parent = root.deletingLastPathComponent()
        if holdsDatabases(parent) { return parent }
        // Nothing banked yet (a fresh install before the first pull). The repo
        // root is still the right guess whenever the engine folder was given.
        return root.lastPathComponent == "engine" ? parent : root
    }

    static var dataRoot: URL { dataRoot(under: engineRoot) }

    static var actionExecutionContextID: String {
        actionExecutionContextID(engineRoot: engineRoot,
                                 pythonOverride: pythonOverride)
    }

    static func actionExecutionContextID(engineRoot: URL,
                                         pythonOverride: String?) -> String {
        let engine = engineRoot.standardizedFileURL.path
        let data = dataRoot(under: engineRoot).standardizedFileURL.path
        return [engine, data, pythonOverride ?? "<auto>"].joined(separator: "|")
    }

    /// US uses the original ads_data.sqlite; other markets are suffixed.
    static func databaseURL(market: String) -> URL {
        let file = market == "US" ? "ads_data.sqlite" : "ads_data_\(market).sqlite"
        return dataRoot.appendingPathComponent(file)
    }
}
