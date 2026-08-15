import Foundation

/// What kind of file the user dropped, decided from the filename alone.
/// The catalogue export always arrives named `export_products_*.csv` from
/// Merch on Demand, and the rest of the app has always relied on that prefix.
/// This is the single source of truth for that decision — both import
/// sub-tabs use it to cross-route a file dropped on the wrong one.
enum ImportFileKind: Equatable {
    case catalogExport   // export_products_*.csv → New Designs build workflow
    case dataCSV         // Merch sales report or console monthly history → banked

    static func classify(filename: String) -> ImportFileKind {
        filename.lowercased().hasPrefix("export_products") ? .catalogExport : .dataCSV
    }
}
