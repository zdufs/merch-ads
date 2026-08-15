import Foundation

/// Holds the built accumulated reports so they survive leaving the screen.
///
/// `ContentView` renders the detail pane with a `switch`, so navigating away
/// destroys the screen and every `@State` it owns. Both accumulated screens
/// gate their (heavy) report behind a "Generate report" button, which meant
/// switching tabs and coming back threw the report away and asked the operator
/// to build it again.
///
/// Entries are keyed by `AppState.viewKey` — market plus data stamp — so a
/// market switch looks up a different report, and a nightly run that lands new
/// data invalidates the old one instead of showing it as current.
@MainActor
@Observable
final class AccumulatedStore {
    static let shared = AccumulatedStore()

    private var asins: [String: AccumulatedAsinsResponse] = [:]
    private var keywords: [String: AccumulatedKeywordsResponse] = [:]

    private init() {}

    func asins(for key: String) -> AccumulatedAsinsResponse? { asins[key] }
    func keywords(for key: String) -> AccumulatedKeywordsResponse? { keywords[key] }

    func store(_ response: AccumulatedAsinsResponse, for key: String) {
        asins[key] = response
    }

    func store(_ response: AccumulatedKeywordsResponse, for key: String) {
        keywords[key] = response
    }

    /// Drop a cached report so the next visit rebuilds it (the Refresh path).
    func invalidate(key: String) {
        asins[key] = nil
        keywords[key] = nil
    }
}
