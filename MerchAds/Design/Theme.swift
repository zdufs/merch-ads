import SwiftUI

enum Theme {
    // MerchDash theme — brand values pinned per appearance. Each token carries a
    // light hex and a dark hex; `Color(light:dark:)` resolves the right one for
    // the current appearance (Aqua vs Dark Aqua), including a `.preferredColorScheme`
    // override. The app follows the operator's Appearance setting (System / Light /
    // Dark) — see AppAppearance; it no longer forces `.light`.
    //
    // The dark set keeps the MerchDash identity: a cool near-black canvas, a
    // one-step-lighter card surface set apart by its hairline border, and the
    // semantic + series colors lifted one weight (600 → 400/500) so they read on
    // dark instead of muddying.
    enum Colors {
        static let canvas = Color(light: 0xF6F7F9, dark: 0x0D0F14)          // page background
        static let surface = Color(light: 0xFFFFFF, dark: 0x171A21)         // cards / tables
        static let elevatedSurface = Color(light: 0xFFFFFF, dark: 0x1F232C) // popovers / menus
        static let separator = Color(light: 0xE5E7EB, dark: 0x2A2F3A)       // hairline borders
        static let muted = Color(light: 0x6B7280, dark: 0x8B93A3)           // uppercase labels

        static let accent = Color(light: 0x4F46E5, dark: 0x818CF8)          // indigo — primary/links/tint
        static let accentSoft = Color(light: 0xEEF2FF, dark: 0x252A46)      // active nav pill bg
        static let textPrimary = Color(light: 0x111827, dark: 0xF2F4F7)     // titles/values
        static let textSecondary = Color(light: 0x374151, dark: 0xC5CBD6)   // nav/body

        // Light greens/ambers darkened one Tailwind step (600→700) so they clear
        // 4.5:1 body contrast on white — the 600s only made AA-large, which failed
        // in small table cells and delta chips. Dark set already passed, unchanged.
        static let positive = Color(light: 0x15803D, dark: 0x22C55E)        // green
        static let caution = Color(light: 0xB45309, dark: 0xF59E0B)         // amber
        static let critical = Color(light: 0xDC2626, dark: 0xF87171)        // red
        static let information = Color(light: 0x2563EB, dark: 0x60A5FA)     // blue
        static let neutralAccent = Color(light: 0x0891B2, dark: 0x22D3EE)   // cyan

        static let campaignLottery = Color(light: 0x7C3AED, dark: 0xA78BFA)   // violet
        static let campaignScavenger = Color(light: 0xD97706, dark: 0xFBBF24) // amber
        static let campaignStandard = Color(light: 0x6B7280, dark: 0x9CA3AF)  // gray
        static let campaignTamas = Color(light: 0x2563EB, dark: 0x60A5FA)     // blue
        static let campaignHarvested = Color(light: 0x16A34A, dark: 0x34D399) // green

        static let chartSales = Color(light: 0x16A34A, dark: 0x34D399)       // green line
        static let chartSpend = Color(light: 0x4F46E5, dark: 0x818CF8)       // indigo line
        static let chartProfit = Color(light: 0x16A34A, dark: 0x34D399)
        static let chartLoss = Color(light: 0xDC2626, dark: 0xF87171)
        static let chartGrid = Color(light: 0xE5E7EB, dark: 0x2A2F3A)

        /// Heat-grid cell with no value — also the "Not synced" legend swatch.
        static let gridEmpty = Color(light: 0xEBEDF0, dark: 0x20242D)
        /// Track behind small inline segmented controls (mode tabs).
        static let controlTrack = Color(light: 0xF3F4F6, dark: 0x20242D)

        static func campaignType(_ type: String) -> Color {
            switch type.lowercased() {
            case "lottery": campaignLottery
            case "scavenger": campaignScavenger
            case "tamas": campaignTamas
            case "harvested": campaignHarvested
            default: campaignStandard
            }
        }

        static func entityState(_ state: String?) -> Color {
            switch state?.uppercased() {
            case "ENABLED", "ACTIVE": positive
            case "PAUSED": caution
            case "ARCHIVED", "DISABLED": muted
            default: muted
            }
        }
    }

    enum ChartPalette {
        static let categorical = [
            Colors.chartSales, Colors.chartSpend, Colors.campaignLottery,
            Colors.campaignScavenger, Colors.information, Colors.neutralAccent,
        ]

        // One color per trend metric, pinned per appearance. Each carries a
        // light hex and a dark hex (lifted one weight so the line reads on a dark
        // card) — deliberately not adaptive system colors, which drift with the
        // appearance and read washed out on the white MerchDash cards.
        static let impressions = Color(light: 0xA78BFA, dark: 0xC4B5FD)  // violet (faint backdrop bars)
        static let clicks = Colors.information         // blue
        static let spend = Colors.accent               // indigo
        static let orders = Color(light: 0xEA580C, dark: 0xFB923C)       // orange
        static let units = Color(light: 0x9333EA, dark: 0xC084FC)        // purple
        static let sales = Colors.positive             // green
        static let acos = Colors.critical              // red
        static let roas = Color(light: 0x0D9488, dark: 0x2DD4BF)         // teal
        static let ctr = Color(light: 0x0891B2, dark: 0x22D3EE)          // cyan
        static let cvr = Color(light: 0x65A30D, dark: 0xA3E635)          // lime (replaces .mint)
        static let cpc = Color(light: 0xDB2777, dark: 0xF472B6)          // pink
        static let cpo = Color(light: 0x854D0E, dark: 0xEAB308)          // yellow (lifted; 800 is invisible on dark)
    }
}

enum AcosTier: Equatable {
    case unavailable
    case comfort
    case elevated
    case high
    case profitable
    case unprofitable

    static func select(acos: Double?, breakEven: Double? = nil,
                       royaltyROI: Double? = nil) -> AcosTier {
        guard let acos else { return .unavailable }
        if let breakEven {
            return acos <= breakEven ? .profitable : .unprofitable
        }
        if let royaltyROI {
            return royaltyROI >= 1 ? .profitable : .unprofitable
        }
        if acos > 0.30 { return .high }
        if acos > 0.22 { return .elevated }
        return .comfort
    }

    var color: Color {
        switch self {
        case .unavailable: Theme.Colors.muted
        case .comfort: .primary
        case .elevated: Theme.Colors.neutralAccent
        case .high: Theme.Colors.information
        case .profitable: Theme.Colors.positive
        case .unprofitable: Theme.Colors.critical
        }
    }

    var help: String {
        switch self {
        case .unavailable: "No sales attributed yet"
        case .comfort: "Within the neutral ACOS comfort band"
        case .elevated: "Above the neutral 22% comfort marker"
        case .high: "Above the neutral 30% reference marker"
        case .profitable: "At or within the attached profitability threshold"
        case .unprofitable: "Above the attached profitability threshold"
        }
    }
}
