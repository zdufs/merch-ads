import SwiftUI
import AppKit

// Copy-to-clipboard for tables. SwiftUI `Table` cells are not selectable on
// macOS, so instead of cell text-selection we give every table a right-click
// "Copy": one line copies each row's headline value (a search term, ASIN,
// campaign name…), the other copies the whole row as tab-separated text you can
// paste straight into a spreadsheet. Works on the right-clicked row even when
// it isn't part of the current selection.

enum Clipboard {
    static func copy(_ text: String) {
        let pb = NSPasteboard.general
        pb.clearContents()
        pb.setString(text, forType: .string)
    }
}

/// The Copy buttons themselves, so they can also be dropped into a table that
/// already has its own context menu (e.g. the campaign browser's actions).
@ViewBuilder
func copyMenuItems<Row: Identifiable>(
    _ picked: [Row],
    primaryLabel: String,
    primary: (Row) -> String,
    row: ((Row) -> String)? = nil
) -> some View {
    if !picked.isEmpty {
        let primaryText = picked.map(primary).joined(separator: "\n")
        Button(picked.count > 1 ? "Copy \(picked.count) \(primaryLabel)s" : "Copy \(primaryLabel)") {
            Clipboard.copy(primaryText)
        }
        if let row {
            let rowText = picked.map(row).joined(separator: "\n")
            Button(picked.count > 1 ? "Copy \(picked.count) Rows" : "Copy Row") {
                Clipboard.copy(rowText)
            }
        }
    }
}

extension View {
    /// Attach to a `Table` that has a `selection:` binding of `Row.ID`. Adds a
    /// right-click Copy menu. `primaryLabel` names the headline field
    /// ("Search Term", "ASIN", "Campaign"); `primary` extracts it; `row`
    /// (optional) renders the full tab-separated line for spreadsheet paste.
    func copyableRows<Row: Identifiable>(
        _ rows: [Row],
        primaryLabel: String,
        primary: @escaping (Row) -> String,
        row: ((Row) -> String)? = nil
    ) -> some View {
        contextMenu(forSelectionType: Row.ID.self) { ids in
            copyMenuItems(rows.filter { ids.contains($0.id) },
                          primaryLabel: primaryLabel, primary: primary, row: row)
        }
    }
}
