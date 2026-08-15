import SwiftUI

/// First-click sort direction for every data table in the app.
///
/// SwiftUI's `Table` sorts a freshly-clicked column ascending. That is backwards
/// for the numbers we care about: the first click on Spend should show the
/// biggest spender, not the smallest. So we flip it. The rule the operator asked
/// for:
///   • Numbers, money, %, counts and dates → first click sorts high → low.
///   • Text (name, ASIN, keyword) → first click sorts A → Z (nothing is "high").
///   • Clicking the SAME column again toggles the other way, as usual.
///
/// Apply it to the `Table(sortOrder:)` binding only — `Table(... sortOrder: $order.descendingFirst())`.
/// Leave the plain `$order` on anything that restores an exact saved direction
/// (SavedViewPicker), so a saved view is honoured verbatim.
enum TableSortDirection {
    /// A column whose values read as text, where A → Z is the natural first
    /// direction. Anything we can't identify as text is treated as a magnitude
    /// (high → low first) — the safe default for this app's mostly-numeric tables.
    static func isTextColumn(_ keyPath: AnyKeyPath) -> Bool {
        let value = type(of: keyPath).valueType
        return value == String.self || value == String?.self
            || value == Substring.self || value == Substring?.self
    }
}

extension Binding {
    /// Wrap a `Table` sortOrder binding so the first click on a column sorts in
    /// the most useful direction (see `TableSortDirection`). Re-clicking the same
    /// column toggles normally from whatever the first click set.
    func descendingFirst<Row>() -> Binding<[KeyPathComparator<Row>]>
    where Value == [KeyPathComparator<Row>] {
        Binding<[KeyPathComparator<Row>]>(
            get: { wrappedValue },
            set: { proposed in
                var next = proposed
                if let primary = next.first,
                   primary.order == .forward,                          // a fresh forward click (re-clicks landing on .reverse are left alone)
                   wrappedValue.first?.keyPath != primary.keyPath,      // the sort column changed — not a same-column toggle
                   !TableSortDirection.isTextColumn(primary.keyPath) {  // text keeps A → Z; magnitudes flip to high → low
                    next[0].order = .reverse
                }
                wrappedValue = next
            })
    }
}
