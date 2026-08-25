import XCTest
@testable import Merch_Ads

/// The databases live in the repo root; `appctl.py` lives in `engine/` under it.
/// When the engine moved into that subfolder the engine-root setting was
/// repointed at `…/Ads/engine`, and every direct database read followed it into
/// a folder with no databases. "DB direct" read "—" for every market and the
/// sidebar footer said "no local data".
///
/// So the data root is resolved by looking for a database, not by assuming a
/// layout. These tests pin both layouts and the empty case.
final class DataRootTests: XCTestCase {

    private var root: URL!

    override func setUpWithError() throws {
        root = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("dataroot-\(UUID().uuidString)")
        try FileManager.default.createDirectory(
            at: root.appendingPathComponent("engine"), withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: root)
    }

    private func plantDatabase() throws {
        try Data().write(to: root.appendingPathComponent("ads_data.sqlite"))
    }

    func testTheRepoRootIsUsedWhenItIsConfigured() throws {
        try plantDatabase()
        XCTAssertEqual(AppSettings.dataRoot(under: root).path, root.path)
    }

    func testTheEngineFolderResolvesUpToTheRepoRoot() throws {
        try plantDatabase()
        let engine = root.appendingPathComponent("engine")
        XCTAssertEqual(AppSettings.dataRoot(under: engine).path, root.path)
    }

    func testAFreshInstallWithNoDatabaseStillLeavesTheEngineFolder() {
        let engine = root.appendingPathComponent("engine")
        XCTAssertEqual(AppSettings.dataRoot(under: engine).path, root.path)
    }

    func testAnUnrelatedFolderIsLeftAlone() {
        XCTAssertEqual(AppSettings.dataRoot(under: root).path, root.path)
    }

    func testTheMarketFilenamesAreUnchanged() throws {
        try plantDatabase()
        let engine = root.appendingPathComponent("engine")
        XCTAssertEqual(AppSettings.dataRoot(under: engine)
                        .appendingPathComponent("ads_data_DE.sqlite").lastPathComponent,
                       "ads_data_DE.sqlite")
    }
}
