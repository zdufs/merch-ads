import Foundation

/// What build is actually running.
///
/// The app is installed by `scripts/package_app.sh --install` and relaunched
/// constantly, so "is the window in front of me the build I just made?" is a
/// question that gets asked a lot. Showing it beats reading Info.plist.
///
/// Read from the bundle rather than hardcoded: the numbers live in the Xcode
/// project (MARKETING_VERSION, CURRENT_PROJECT_VERSION) and a copy here would
/// drift from them the first time one changed.
enum AppVersion {

    /// "0.2.5" — the marketing version, or "—" if the bundle has none (only
    /// reachable in a test host, but a crash there would be a silly way to fail).
    static var short: String {
        bundleString("CFBundleShortVersionString") ?? "—"
    }

    /// "2" — the build number.
    static var build: String {
        bundleString("CFBundleVersion") ?? "—"
    }

    /// "v0.2.5" for the window subtitle: short enough to sit beside the title
    /// without crowding it.
    static var displayName: String { "v\(short)" }

    /// "0.2.5 (2)" — both numbers, for anywhere the exact build matters.
    static var full: String { "\(short) (\(build))" }

    private static func bundleString(_ key: String) -> String? {
        guard let value = Bundle.main.object(forInfoDictionaryKey: key) as? String,
              !value.trimmingCharacters(in: .whitespaces).isEmpty else { return nil }
        return value
    }
}
