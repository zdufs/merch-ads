import SwiftUI

extension Color {
    /// Fixed sRGB color from a 0xRRGGBB literal — used by the MerchDash light
    /// theme, which pins exact brand values rather than adaptive system colors.
    init(hex: UInt, alpha: Double = 1) {
        self.init(.sRGB,
                  red: Double((hex >> 16) & 0xFF) / 255,
                  green: Double((hex >> 8) & 0xFF) / 255,
                  blue: Double(hex & 0xFF) / 255,
                  opacity: alpha)
    }

    /// Appearance-adaptive color: `light` in Aqua, `dark` in Dark Aqua. Both are
    /// 0xRRGGBB literals, so the theme still pins exact brand values per mode —
    /// it just resolves the right one for the current appearance (including a
    /// `.preferredColorScheme` override) instead of forcing one palette.
    init(light: UInt, dark: UInt, alpha: Double = 1) {
        let dynamic = NSColor(name: nil) { appearance in
            let isDark = appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua
            let hex = isDark ? dark : light
            return NSColor(srgbRed: Double((hex >> 16) & 0xFF) / 255,
                           green: Double((hex >> 8) & 0xFF) / 255,
                           blue: Double(hex & 0xFF) / 255,
                           alpha: alpha)
        }
        self.init(nsColor: dynamic)
    }
}
