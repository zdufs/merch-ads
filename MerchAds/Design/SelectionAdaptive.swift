import SwiftUI

// A selected row in a focused Table is filled with the solid accent colour.
// Plain text on that row is turned white for us. Text that paints its own
// colour is not — it keeps the colour it was given and goes unreadable, which
// is what made selected campaign names and type badges disappear into the blue.
//
// SwiftUI reports the situation through `backgroundProminence`. Every cell that
// carries its own colour reads it and steps aside on a selected row: the colour
// is dropped, and the shape of the cell (weight, symbol, capsule) carries the
// meaning instead. Row selection is not the place to read a value off its
// colour; it is the place to read the label.

extension BackgroundProminence {
    /// True while this view is drawn on a selected row of a focused table.
    /// Views read `\.backgroundProminence` directly, rather than a derived
    /// environment key, so SwiftUI tracks the dependency reliably.
    var isSelectedRow: Bool { self == .increased }
}

extension View {
    /// Applies `color` normally, and the row's own foreground colour (white on
    /// the selection fill) once the row is selected.
    func rowAdaptiveForeground(_ color: Color, selected: Bool) -> some View {
        foregroundStyle(selected ? AnyShapeStyle(.primary) : AnyShapeStyle(color))
    }
}
