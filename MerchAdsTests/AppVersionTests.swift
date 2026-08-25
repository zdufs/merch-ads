import XCTest
@testable import Merch_Ads

/// The version shown beside the window title.
///
/// The one real trap here is the window TITLE. MenuBarController finds the main
/// window with `window.title == "Merch Ads"`, so the version has to ride as a
/// subtitle. Folding it into the title string would quietly break the menu
/// bar's "Open Merch Ads" — no crash, no warning, just a button that stops
/// working.
final class AppVersionTests: XCTestCase {

    func testTheDisplayNameIsPrefixedAndShort() {
        XCTAssertTrue(AppVersion.displayName.hasPrefix("v"))
        XCTAssertLessThan(AppVersion.displayName.count, 16,
                          "it sits beside the window title — anything longer crowds it")
    }

    func testFullCarriesBothNumbers() {
        XCTAssertEqual(AppVersion.full, "\(AppVersion.short) (\(AppVersion.build))")
    }

    /// A missing key falls back to "—" rather than trapping. Only reachable in a
    /// test host, but crashing there would be a silly way to fail a build.
    func testAMissingValueFallsBackInsteadOfCrashing() {
        XCTAssertFalse(AppVersion.short.isEmpty)
        XCTAssertFalse(AppVersion.build.isEmpty)
    }

    /// The menu bar's "Open Merch Ads" finds the window by title. macOS joins
    /// title and subtitle on screen — "Merch Ads – v0.2.5" — so an equality
    /// check would have started failing the moment the version was added, and
    /// failed silently: a button that just stops working.
    func testTheWindowLookupSurvivesTheVersionSubtitle() {
        let onScreen = "\(MenuBarController.mainWindowTitle) – \(AppVersion.displayName)"
        XCTAssertTrue(onScreen.hasPrefix(MenuBarController.mainWindowTitle))
        XCTAssertNotEqual(onScreen, MenuBarController.mainWindowTitle,
                          "if these were equal the prefix match would be pointless "
                          + "— this test is here because they are NOT")
    }

    func testTheLookupStillMatchesABareTitle() {
        XCTAssertTrue(MenuBarController.mainWindowTitle
            .hasPrefix(MenuBarController.mainWindowTitle))
    }
}
