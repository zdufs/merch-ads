import XCTest
@testable import Merch_Ads

final class ImportFileKindTests: XCTestCase {
    func testCatalogExportByPrefix() {
        XCTAssertEqual(ImportFileKind.classify(filename: "export_products_2026-08-04T16_30_41.366Z.csv"), .catalogExport)
    }
    func testCatalogExportIsCaseInsensitive() {
        XCTAssertEqual(ImportFileKind.classify(filename: "EXPORT_PRODUCTS_x.csv"), .catalogExport)
    }
    func testSalesReportIsDataCSV() {
        XCTAssertEqual(ImportFileKind.classify(filename: "SALES_REPORT-8_1_26-8_12_26.csv"), .dataCSV)
    }
    func testConsoleHistoryIsDataCSV() {
        XCTAssertEqual(ImportFileKind.classify(filename: "Sponsored Products Search term report.csv"), .dataCSV)
    }
}
