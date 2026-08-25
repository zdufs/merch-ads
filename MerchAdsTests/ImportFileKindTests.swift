import XCTest
@testable import Merch_Ads

final class ImportFileKindTests: XCTestCase {
    func testCatalogExportByPrefix() {
        XCTAssertEqual(ImportFileKind.classify(filename: "export_products_2026-08-04T16_30_41.366Z.csv"), .catalogExport)
    }
    func testCatalogExportIsCaseInsensitive() {
        XCTAssertEqual(ImportFileKind.classify(filename: "EXPORT_PRODUCTS_x.csv"), .catalogExport)
    }
    func testSnapGridExportIsACatalogExport() {
        XCTAssertEqual(ImportFileKind.classify(filename: "snap-grid-export-2026-08-15_23-26-07.csv"), .catalogExport)
    }
    func testSalesReportIsDataCSV() {
        XCTAssertEqual(ImportFileKind.classify(filename: "SALES_REPORT-8_1_26-8_12_26.csv"), .dataCSV)
    }
    func testConsoleHistoryIsDataCSV() {
        XCTAssertEqual(ImportFileKind.classify(filename: "Sponsored Products Search term report.csv"), .dataCSV)
    }

    // MARK: - header fallback (a renamed export must still reach New Designs)

    private func tempCSV(_ name: String, _ contents: String) throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
            .appendingPathComponent(name)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try contents.write(to: url, atomically: true, encoding: .utf8)
        return url
    }

    func testRenamedSnapExportIsFoundByItsHeader() throws {
        let url = try tempCSV("new stuff.csv",
                              "Marketplace,Price,ASIN,Ad-safe ASIN,Status,Product Type\nUS,$21.99,B0X,,Live,Tumbler\n")
        XCTAssertEqual(ImportFileKind.classify(url: url), .catalogExport)
    }

    func testRenamedMerchFlowExportIsFoundByItsHeader() throws {
        let url = try tempCSV("catalog.csv",
                              "listingId,status,asin,marketplace,productType\nL1,published,B0X,us,standard_tshirt\n")
        XCTAssertEqual(ImportFileKind.classify(url: url), .catalogExport)
    }

    /// The dated Merch sales report carries "Product Type" and "ASIN" columns of
    /// its own, so the header check must not claim it for the campaign builder.
    func testRenamedSalesReportStaysADataCSV() throws {
        let url = try tempCSV("royalties.csv",
                              "\"Mkt\",\"Date\",\"ASIN\",\"Title\",\"Category 1\",\"Category 2\",\"Category 3\",\"Product Type\",\"Purchased\",\"Cancelled\",\"Returned\",\"Revenue\",\"Royalties\",\"Currency\"\n\".it\",\"4/20/26\",\"B0X\",\"T\",\"Women\",\"M\",\"Black\",\"Standard t-shirt\",1,0,0,19.49,3.7,\"EUR\"\n")
        XCTAssertEqual(ImportFileKind.classify(url: url), .dataCSV)
    }

    func testMissingFileStaysADataCSV() {
        let url = URL(fileURLWithPath: "/tmp/does-not-exist-\(UUID().uuidString).csv")
        XCTAssertEqual(ImportFileKind.classify(url: url), .dataCSV)
    }
}
