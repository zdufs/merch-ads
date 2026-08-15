import Foundation

/// App-wide, market-qualified navigation. Entity routes carry every parent ID
/// needed to re-resolve safely from the mirror at the destination.
enum Route: Hashable, Codable, Sendable {
    case screen(Screen)
    case campaign(market: String, campaignID: String)
    case adGroup(market: String, campaignID: String, adGroupID: String)
    case target(market: String, campaignID: String, adGroupID: String, targetID: String)
    case asin(market: String, asin: String)

    var market: String? {
        switch self {
        case .screen: nil
        case .campaign(let market, _), .asin(let market, _): market
        case .adGroup(let market, _, _): market
        case .target(let market, _, _, _): market
        }
    }

    var screen: Screen {
        switch self {
        case .screen(let screen): screen
        case .campaign, .adGroup, .target: .campaigns
        case .asin: .liveStatus
        }
    }

    /// Stable path form used by tests and future app-level deep links.
    var path: String {
        let pieces: [String]
        switch self {
        case .screen(let screen): pieces = ["screen", screen.rawValue]
        case .campaign(let market, let campaignID):
            pieces = ["campaign", market, campaignID]
        case .adGroup(let market, let campaignID, let adGroupID):
            pieces = ["ad-group", market, campaignID, adGroupID]
        case .target(let market, let campaignID, let adGroupID, let targetID):
            pieces = ["target", market, campaignID, adGroupID, targetID]
        case .asin(let market, let asin): pieces = ["asin", market, asin]
        }
        return pieces.map(Self.escape).joined(separator: "/")
    }

    init?(path: String) {
        let pieces = path.split(separator: "/", omittingEmptySubsequences: true)
            .map(String.init).compactMap(Self.unescape)
        guard !pieces.isEmpty else { return nil }
        switch (pieces[0], pieces.count) {
        case ("screen", 2):
            guard let screen = Screen.restored(from: pieces[1]) else { return nil }
            self = .screen(screen)
        case ("campaign", 3):
            self = .campaign(market: pieces[1], campaignID: pieces[2])
        case ("ad-group", 4):
            self = .adGroup(market: pieces[1], campaignID: pieces[2], adGroupID: pieces[3])
        case ("target", 5):
            self = .target(market: pieces[1], campaignID: pieces[2],
                           adGroupID: pieces[3], targetID: pieces[4])
        case ("asin", 3):
            self = .asin(market: pieces[1], asin: pieces[2])
        default: return nil
        }
    }

    private static func escape(_ value: String) -> String {
        value.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed.subtracting(CharacterSet(charactersIn: "/"))) ?? value
    }

    private static func unescape(_ value: String) -> String? {
        value.removingPercentEncoding
    }
}
