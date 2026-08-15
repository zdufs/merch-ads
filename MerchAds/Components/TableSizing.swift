import SwiftUI

/// Layout helpers shared by every screen that shows a short list.
///
/// Both exist because SwiftUI's defaults read badly in a tall operator window:
/// a `Table` stretches to fill whatever it is given, so a one-row list paints a
/// page of empty filler stripes, and a `ContentUnavailableView` centres itself,
/// so an empty screen puts its message halfway down the window with nothing
/// around it.
extension View {

    /// Size a `Table` to the rows it actually has, up to `cap`.
    ///
    /// The height has to cover the header, every row, and the horizontal
    /// scroller — wide tables always show one when the system is set to keep
    /// scroll bars visible, and leaving it out clips the last row behind the
    /// scroller. Past `cap` the table scrolls normally.
    ///
    /// This sets a *definite* height, so it is equally safe inside a ScrollView
    /// (where a `maxHeight` would collapse the table to nothing). Callers in a
    /// fill container add their own `.frame(maxHeight: .infinity, alignment: .top)`.
    func contentSizedTable(rows: Int, cap: CGFloat = 520) -> some View {
        modifier(ContentSizedTable(rows: rows, cap: cap))
    }

    /// Put an empty/error state just below the controls instead of floating it
    /// in the middle of the window.
    ///
    /// `ContentUnavailableView` is greedy, so it has to be given a bounded box
    /// before it can be pinned anywhere — and the box has to be a `maxHeight`,
    /// not `fixedSize`, whose ideal height is tall enough to push the rest of
    /// the page off-screen.
    func topAlignedEmptyState(topPadding: CGFloat = 24) -> some View {
        frame(maxWidth: .infinity, maxHeight: 260)
            .padding(.top, topPadding)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
    }
}

private struct ContentSizedTable: ViewModifier {
    let rows: Int
    let cap: CGFloat

    // Scaled so the fit still holds at larger accessibility text sizes, where a
    // hardcoded 28pt row silently starts clipping.
    @ScaledMetric(relativeTo: .body) private var rowHeight: CGFloat = 30
    @ScaledMetric(relativeTo: .body) private var headerHeight: CGFloat = 32
    @ScaledMetric(relativeTo: .body) private var scrollerAllowance: CGFloat = 20

    func body(content: Content) -> some View {
        let needed = CGFloat(max(rows, 1)) * rowHeight + headerHeight + scrollerAllowance
        content.frame(height: min(cap, needed))
    }
}
