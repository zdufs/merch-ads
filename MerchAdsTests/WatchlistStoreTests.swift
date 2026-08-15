import XCTest
@testable import Merch_Ads

final class WatchlistStoreTests: XCTestCase {
    override func setUp() {
        UserDefaults.standard.removeObject(forKey: "watchlist.v1.US")
        UserDefaults.standard.removeObject(forKey: "watchlist.v1.DE")
    }

    private func pin(_ id: String, market: String = "US") -> WatchlistPin {
        WatchlistPin(kind: .campaign, market: market, campaignID: id,
                     adGroupID: nil, targetID: nil, asin: nil, label: "C-\(id)")
    }

    func testPerMarketIsolationAndDedup() {
        WatchlistStore.add(pin("c1"), market: "US")
        WatchlistStore.add(pin("c1"), market: "US")            // dedup by id
        XCTAssertEqual(WatchlistStore.pins(market: "US").count, 1)
        XCTAssertEqual(WatchlistStore.pins(market: "DE").count, 0)
        WatchlistStore.remove(pin("c1"), market: "US")
        XCTAssertEqual(WatchlistStore.pins(market: "US").count, 0)
    }

    func testMarketsDoNotLeak() {
        WatchlistStore.add(pin("c1", market: "US"), market: "US")
        WatchlistStore.add(pin("c9", market: "DE"), market: "DE")
        XCTAssertEqual(WatchlistStore.pins(market: "US").map(\.campaignID), ["c1"])
        XCTAssertEqual(WatchlistStore.pins(market: "DE").map(\.campaignID), ["c9"])
    }

    func testCapacity() {
        for i in 0..<(WatchlistStore.capacity + 5) {
            WatchlistStore.add(pin("c\(i)"), market: "US")
        }
        XCTAssertEqual(WatchlistStore.pins(market: "US").count, WatchlistStore.capacity)
    }
}
