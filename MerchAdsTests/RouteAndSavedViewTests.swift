import XCTest
@testable import Merch_Ads

final class RouteAndSavedViewTests: XCTestCase {
    func testTypedRoutesRoundTripWithMarketAndFullParentIdentity() throws {
        let routes: [Route] = [
            .screen(.dashboard),
            .campaign(market: "US", campaignID: "campaign/one"),
            .adGroup(market: "DE", campaignID: "campaign-2", adGroupID: "group/9"),
            .target(market: "FR", campaignID: "c3", adGroupID: "g4", targetID: "t/5"),
            .asin(market: "UK", asin: "B0TESTCCCC"),
        ]
        for route in routes {
            XCTAssertEqual(Route(path: route.path), route)
        }

        guard case .adGroup(let market, let campaignID, let adGroupID) =
                Route(path: "ad-group/IT/campaign-42/group-7") else {
            return XCTFail("Ad-group route did not parse")
        }
        XCTAssertEqual(market, "IT")
        XCTAssertEqual(campaignID, "campaign-42")
        XCTAssertEqual(adGroupID, "group-7")
    }

    func testRouteParserRejectsMissingParentOrMarket() {
        XCTAssertNil(Route(path: "ad-group/US/group-only"))
        XCTAssertNil(Route(path: "campaign"))
        XCTAssertNil(Route(path: "target/US/campaign/group"))
        XCTAssertNil(Route(path: "unknown/US/value"))
    }

    func testSavedViewSchemaV1EncodesAndDecodes() throws {
        let source = SavedView(
            tableID: TableID.campaigns, name: "Paused Lottery",
            filters: ["type": "lottery", "state": "PAUSED", "search": "summer"],
            sortDescriptors: [SavedSortDescriptor(field: "spend", ascending: false)],
            columnCustomization: Data([0x01, 0x02, 0xFE]))

        let data = try JSONEncoder().encode(source)
        let decoded = try JSONDecoder().decode(SavedView.self, from: data)
        XCTAssertEqual(decoded, source)
        XCTAssertEqual(decoded.version, 1)
        XCTAssertTrue(decoded.isValid(for: TableID.campaigns))
        XCTAssertFalse(decoded.isValid(for: TableID.audit))
    }

    func testLegacyKeyMigrationPreservesOldAndWritesIdenticalCanonicalValues() throws {
        let suite = "RouteAndSavedViewTests.\(UUID().uuidString)"
        guard let defaults = UserDefaults(suiteName: suite) else {
            return XCTFail("Could not create isolated defaults")
        }
        defer { defaults.removePersistentDomain(forName: suite) }

        let columnData = Data([0xCA, 0xFE, 0xBA, 0xBE])
        defaults.set(columnData, forKey: "columns.allMarkets")
        defaults.set("spend|desc", forKey: "sort.allmarkets")
        defaults.set(Data([0x10, 0x20]), forKey: "columns.playbook")
        defaults.set("price|asc", forKey: "sort.playbook.pricing")

        LegacyPreferenceMigration.migrate(defaults: defaults)

        XCTAssertEqual(defaults.data(forKey: "columns.allMarkets"), columnData)
        XCTAssertEqual(defaults.string(forKey: "sort.allmarkets"), "spend|desc")
        XCTAssertEqual(defaults.data(forKey: "columns.all-markets"), columnData)
        XCTAssertEqual(defaults.string(forKey: "sort.all-markets"), "spend|desc")
        XCTAssertEqual(defaults.data(forKey: "columns.playbook.pricing"), Data([0x10, 0x20]))
        XCTAssertEqual(defaults.string(forKey: "sort.playbook.pricing"), "price|asc")

        defaults.set(Data([0x99]), forKey: "columns.allMarkets")
        LegacyPreferenceMigration.migrate(defaults: defaults)
        XCTAssertEqual(defaults.data(forKey: "columns.all-markets"), columnData,
                       "one-shot migration must not overwrite canonical values")
    }

    func testIntakeRawValueRestoresToImport() {
        XCTAssertEqual(Screen.restored(from: "intake"), .dataImport)
    }
    func testPlaybookRawValueStillRestoresToHealth() {
        XCTAssertEqual(Screen.restored(from: "playbook"), .health)
    }
    func testUnknownRawValueRestoresToNil() {
        XCTAssertNil(Screen.restored(from: "no-such-screen"))
    }
}
