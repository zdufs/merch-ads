import Foundation

/// What kind of file the user dropped: a product-grid export that feeds the
/// New Designs build, or a data CSV that gets banked (Merch sales report, Ads
/// console monthly history).
///
/// Two browser extensions export the product grid, under two different names:
///   * Snap for MOD — `snap-grid-export-*.csv`, what we export today.
///   * MerchFlow    — `export_products_*.csv`, also the economics catalog.
///
/// This is the single source of truth for that decision — both import sub-tabs
/// use it to cross-route a file dropped on the wrong one.
///
/// The filename decides it whenever it can. A file whose name says nothing gets
/// its CSV header read instead, so a renamed export is still routed to New
/// Designs rather than failing in the sales importer.
enum ImportFileKind: Equatable {
    case catalogExport   // product-grid export → New Designs build workflow
    case dataCSV         // Merch sales report or console monthly history → banked

    /// Filename prefixes the supported product-grid exports ship with.
    private static let exportPrefixes = ["export_products", "snap-grid-export", "snap-grid"]

    static func classify(filename: String) -> ImportFileKind {
        let name = filename.lowercased()
        return exportPrefixes.contains(where: name.hasPrefix) ? .catalogExport : .dataCSV
    }

    /// Same decision, plus a header read when the name is not one we know.
    static func classify(url: URL) -> ImportFileKind {
        if classify(filename: url.lastPathComponent) == .catalogExport { return .catalogExport }
        return headerLooksLikeExport(url) ? .catalogExport : .dataCSV
    }

    /// True when the first CSV line carries the columns of either product-grid
    /// export. Only the first chunk is read, so a multi-GB catalog costs
    /// nothing here.
    static func headerLooksLikeExport(_ url: URL) -> Bool {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        guard let handle = try? FileHandle(forReadingFrom: url) else { return false }
        defer { try? handle.close() }
        guard let chunk = try? handle.read(upToCount: 64 * 1024), !chunk.isEmpty else {
            return false
        }
        let text = String(decoding: chunk, as: UTF8.self)
        let header = (text.split(whereSeparator: \.isNewline).first ?? "").lowercased()
        guard !header.isEmpty else { return false }
        let merchFlow = header.contains("producttype") && header.contains("marketplace")
        // "Product Type" alone would also match the dated Merch sales report,
        // which carries Product Type and ASIN columns. Require a column only the
        // Snap grid has, or the sales report would be routed to the builder.
        let snap = header.contains("product type")
            && (header.contains("marketplace") || header.contains("ad-safe asin")
                || header.contains("design id"))
        return merchFlow || snap
    }
}
