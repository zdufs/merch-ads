import XCTest
import SwiftUI
@testable import Merch_Ads

/// The one place the whole app's "first click sorts high → low" behaviour lives.
/// Table interaction can't be unit-tested, but the binding wrapper it flows
/// through can: we stand in for SwiftUI's Table by writing the sortOrder value a
/// header click would produce, then check what the wrapper stored.
final class TableSortDirectionTests: XCTestCase {

    /// A binding backed by a local value, wrapped the way each Table wires it.
    /// Test-only and single-threaded, so the Sendable capture is safe.
    private final class Store: @unchecked Sendable {
        var value: [KeyPathComparator<Campaign>]
        init(_ v: [KeyPathComparator<Campaign>]) { value = v }
        var wrapped: Binding<[KeyPathComparator<Campaign>]> {
            Binding(get: { self.value }, set: { self.value = $0 }).descendingFirst()
        }
    }

    // Table always reports a fresh header click as .forward; the wrapper decides
    // whether to keep it (text) or flip it (numeric).
    private func click(_ store: Store, _ order: [KeyPathComparator<Campaign>]) {
        store.wrapped.wrappedValue = order
    }

    func testNumericColumnFirstClickFlipsToDescending() {
        let store = Store([KeyPathComparator(\Campaign.sales, order: .reverse)])
        click(store, [KeyPathComparator(\Campaign.spend, order: .forward)])
        XCTAssertEqual(store.value.first?.order, .reverse, "a fresh numeric column should start high → low")
        XCTAssertEqual(store.value.first.map { $0.keyPath as AnyKeyPath }, \Campaign.spend as AnyKeyPath)
    }

    func testTextColumnFirstClickStaysAscending() {
        let store = Store([KeyPathComparator(\Campaign.spend, order: .reverse)])
        click(store, [KeyPathComparator(\Campaign.nameValue, order: .forward)])
        XCTAssertEqual(store.value.first?.order, .forward, "a fresh text column should start A → Z")
    }

    func testSameNumericColumnSecondClickIsAscending() {
        // First click left us at spend.reverse; clicking again, Table toggles to forward.
        let store = Store([KeyPathComparator(\Campaign.spend, order: .reverse)])
        click(store, [KeyPathComparator(\Campaign.spend, order: .forward)])
        XCTAssertEqual(store.value.first?.order, .forward, "re-clicking the sorted column must give low → high")
    }

    func testSameNumericColumnThirdClickBackToDescending() {
        let store = Store([KeyPathComparator(\Campaign.spend, order: .forward)])
        click(store, [KeyPathComparator(\Campaign.spend, order: .reverse)])
        XCTAssertEqual(store.value.first?.order, .reverse)
    }

    func testFirstEverClickFromEmptyFlipsNumeric() {
        let store = Store([])
        click(store, [KeyPathComparator(\Campaign.spend, order: .forward)])
        XCTAssertEqual(store.value.first?.order, .reverse)
    }

    func testTextColumnDetection() {
        XCTAssertTrue(TableSortDirection.isTextColumn(\Campaign.nameValue))
        XCTAssertFalse(TableSortDirection.isTextColumn(\Campaign.spend))
        XCTAssertFalse(TableSortDirection.isTextColumn(\Campaign.orders))
    }
}
