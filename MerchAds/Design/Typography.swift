import SwiftUI

enum Typography {
    static let heroNumber = Font.system(.largeTitle, design: .rounded, weight: .semibold)
    static let metricNumber = Font.system(.title2, design: .rounded, weight: .semibold)
    static let statNumber = Font.system(.title3, design: .rounded, weight: .semibold)
    static let sectionTitle = Font.headline
    /// The qualifier beside a section title ("161 winning terms selected…").
    /// Deliberately a step up from .caption and paired with a real gray rather
    /// than .tertiary, which was too faint to read on a white card.
    static let sectionSubtitle = Font.subheadline
    static let sectionCount = Font.subheadline.monospacedDigit()
    static let tableNumeral = Font.body.monospacedDigit()
    static let compactNumeral = Font.callout.monospacedDigit()

    // MerchDash component roles. Every one is built on a relative text style so
    // the components track the user's text-size setting instead of freezing at a
    // point size. The comment after each is its size at the default setting —
    // that is the size the hardcoded .system(size:) fonts used to bake in.

    /// The big in-content page heading (PageHeader) — was a frozen 28pt.
    static let pageHeading = Font.system(.largeTitle, weight: .bold)    // ~26pt
    /// The gray line under it — was a frozen 12.5pt.
    static let pageSubtitle = Font.system(.callout)                     // ~12pt
    /// Screen / empty-state heading.
    static let pageTitle = Font.system(.title2, weight: .bold)          // ~17pt
    /// Heading inside a card.
    static let cardTitle = Font.system(.title3, weight: .bold)          // ~15pt
    /// The uppercase micro-label above a stat value.
    static let cardLabel = Font.system(.subheadline, weight: .semibold) // ~11pt
    /// The stat value itself.
    static let cardValue = Font.system(.title, weight: .bold)           // ~22pt
    /// Body copy inside a card (subtitles, descriptions).
    static let cardBody = Font.system(.body)                            // ~13pt
    /// Supporting caption inside a card (deltas, sub-values).
    static let cardCaption = Font.system(.callout)                      // ~12pt
    static let cardCaptionEmphasis = Font.system(.callout, weight: .medium)
    /// Toggle chip / mode tab label; `chipLabelActive` is its selected weight.
    static let chipLabel = Font.system(.callout)                        // ~12pt
    static let chipLabelActive = Font.system(.callout, weight: .semibold)
    /// Smallest supporting text: legends, scope labels, tooltips.
    static let microLabel = Font.system(.subheadline)                   // ~11pt
    /// Smaller still: the heat-grid's month and weekday gutters.
    static let gridLabel = Font.system(.footnote)                       // ~10pt
    /// The glyph in a centered empty state.
    static let emptyStateGlyph = Font.system(.title, weight: .semibold) // ~22pt
}

extension View {
    func tableNumeralStyle() -> some View {
        font(Typography.tableNumeral)
    }
}
