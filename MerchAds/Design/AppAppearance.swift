import SwiftUI

/// The app's appearance preference, backed by `AppSettings.appearanceKey`.
///
/// The app used to force `.light` everywhere because the palette was fixed
/// light-hex. The palette is appearance-aware now (see Theme), so the app can
/// follow the system, and the operator can still pin Light or Dark by hand.
enum AppAppearance: String, CaseIterable, Identifiable {
    case system
    case light
    case dark

    var id: String { rawValue }

    var label: String {
        switch self {
        case .system: "System"
        case .light: "Light"
        case .dark: "Dark"
        }
    }

    var symbol: String {
        switch self {
        case .system: "circle.lefthalf.filled"
        case .light: "sun.max"
        case .dark: "moon"
        }
    }

    /// `nil` follows the system appearance; the other two pin it. Feeds
    /// `.preferredColorScheme` at each scene root.
    var colorScheme: ColorScheme? {
        switch self {
        case .system: nil
        case .light: .light
        case .dark: .dark
        }
    }

    /// Decode the stored raw string, falling back to `.system` for an empty or
    /// unknown value — so a missing default reads as "follow the system".
    static func stored(_ raw: String) -> AppAppearance {
        AppAppearance(rawValue: raw) ?? .system
    }
}
