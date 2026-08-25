import AppKit
import SwiftUI

/// Links into the Amazon storefront.
///
/// The same design is a different listing in every marketplace, so the domain
/// follows the market the row belongs to — a DE row opens on amazon.de. KDP US
/// is the US store, like every other US profile.
enum AmazonLink {
    static func host(for market: String) -> String {
        switch market.uppercased() {
        case "UK": "www.amazon.co.uk"
        case "DE": "www.amazon.de"
        case "FR": "www.amazon.fr"
        case "ES": "www.amazon.es"
        case "IT": "www.amazon.it"
        default: "www.amazon.com"       // US, USKDP, and anything added later
        }
    }

    /// True only for a real ASIN: ten characters, letters and digits.
    ///
    /// Several screens fall back to an ad-group id when a row carries no ASIN
    /// (`design.asin ?? design.adGroupId`). Amazon entity ids are much longer
    /// numeric strings, so this test keeps them out of a product link that
    /// would land on a 404.
    static func isASIN(_ text: String) -> Bool {
        text.count == 10 && text.allSatisfy { $0.isASCII && ($0.isLetter || $0.isNumber) }
    }

    static func product(_ asin: String, market: String) -> URL? {
        guard isASIN(asin) else { return nil }
        return URL(string: "https://\(host(for: market))/dp/\(asin)")
    }
}

/// One ASIN, rendered as a link to its Amazon product page.
///
/// Only the glyphs are clickable, so clicking anywhere else in the cell still
/// selects the row — a table's first column is where people click to select,
/// and an accidental browser launch there would be worse than no link at all.
/// The link underlines on hover and the pointer turns into a hand, which is how
/// the affordance is discoverable without colouring every ASIN column indigo.
///
/// A value that is not a real ASIN renders as plain text (see
/// `AmazonLink.isASIN`), so a fallback id never becomes a dead link.
struct AsinLink: View {
    let asin: String?
    /// Market override for a row that does not belong to the selected market.
    var market: String? = nil
    /// Text shown in place of the bare ASIN (Halo's "Title — ASIN" label).
    var text: String? = nil
    /// Extra fact for the tooltip, where the ASIN column carries one (the kill
    /// list has no Type column, so the product type lives here).
    var hint: String? = nil
    /// Shown when the row carries no ASIN at all.
    var placeholder: String = "—"
    /// Indigo identity styling, for a table's primary column (`entityLink()`).
    var prominent: Bool = false
    /// The ASIN's own font. Codes read as monospaced everywhere in this app.
    var font: Font = .body.monospaced()

    @Environment(AppState.self) private var appState: AppState?
    @Environment(\.openURL) private var openURL
    @State private var hovering = false

    private var code: String { market ?? appState?.selectedMarket ?? "US" }

    var body: some View {
        let raw = (asin ?? "").trimmingCharacters(in: .whitespaces)
        if raw.isEmpty {
            label(placeholder, underline: false).foregroundStyle(.quaternary)
        } else if let url = AmazonLink.product(raw, market: code) {
            Button { openURL(url) } label: {
                label(raw, underline: hovering)
            }
            .buttonStyle(.plain)
            .onHover { inside in
                hovering = inside
                if inside { NSCursor.pointingHand.push() } else { NSCursor.pop() }
            }
            // A table cell can be scrolled out from under the pointer while it
            // is hovered, and the pushed cursor would outlive the view.
            .onDisappear {
                if hovering { NSCursor.pop(); hovering = false }
            }
            .help(tooltip(raw))
            .accessibilityLabel("\(text ?? raw), open on Amazon")
        } else {
            label(raw, underline: false)
        }
    }

    private func tooltip(_ raw: String) -> String {
        let open = "Open \(raw) on \(AmazonLink.host(for: code))"
        guard let hint, !hint.isEmpty else { return open }
        return "\(hint) · \(open)"
    }

    @ViewBuilder
    private func label(_ raw: String, underline: Bool) -> some View {
        let content = Text(text ?? raw).underline(underline)
        Group {
            if prominent { content.entityLink() } else { content }
        }
        .font(font)
    }
}
