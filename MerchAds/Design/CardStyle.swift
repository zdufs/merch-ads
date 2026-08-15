import SwiftUI

enum MDCard {
    /// The MerchDash card corner radius. Anything that rides the card's edge —
    /// a selection ring, a hit-test shape — must use this same value or its
    /// corners visibly diverge from the card.
    static let radius: CGFloat = 10
}

extension Text {
    /// MerchDash-style entity link: indigo, medium weight. For the primary
    /// name/ASIN column in tables (campaigns, ad groups, ASINs, keywords).
    /// Indigo on indigo is unreadable, so the link turns plain on a selected
    /// row and keeps only its heavier weight. See SelectionAdaptive.swift.
    func entityLink() -> some View {
        EntityLinkText(text: self.fontWeight(.medium))
    }
}

/// The weight is baked into the `Text` rather than applied as a view modifier
/// so that call sites which follow with their own `.font(…)` — the monospaced
/// ASIN columns — keep the medium weight.
private struct EntityLinkText: View {
    @Environment(\.backgroundProminence) private var prominence
    let text: Text

    var body: some View {
        text.rowAdaptiveForeground(Theme.Colors.accent,
                                   selected: prominence.isSelectedRow)
    }
}

extension View {
    /// The MerchDash card: flat white, ~10px radius, 1px hairline border, soft
    /// low shadow. Drop-in replacement for the old Liquid-Glass card treatment.
    func mdCard(radius: CGFloat = MDCard.radius) -> some View {
        self
            .background(Theme.Colors.surface)
            .clipShape(RoundedRectangle(cornerRadius: radius, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: radius)
                    .strokeBorder(Theme.Colors.separator, lineWidth: 1)
            }
            .shadow(color: Color(hex: 0x101828, alpha: 0.05), radius: 1, x: 0, y: 1)
    }
}
