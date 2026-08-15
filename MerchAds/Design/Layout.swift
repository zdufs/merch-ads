import CoreGraphics

enum Layout {
    enum Spacing {
        static let xxs: CGFloat = 4
        static let xs: CGFloat = 8
        static let sm: CGFloat = 12
        static let md: CGFloat = 16
        static let lg: CGFloat = 24
        static let xl: CGFloat = 32
    }

    enum Radius {
        static let small: CGFloat = 8
        static let medium: CGFloat = 12
        static let large: CGFloat = 16
        static let hero: CGFloat = 24
    }

    enum ChartHeight {
        static let compact: CGFloat = 150
        static let standard: CGFloat = 190
        static let hero: CGFloat = 240
    }
}
