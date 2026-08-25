import SwiftUI
import Charts

// Campaign browser: campaigns → ad groups → targets & search terms, with the
// per-target bid-change timeline (the daily-vs-weekly bid story) one click away.

// Sort proxies: Table's KeyPathComparator needs non-optional Comparable values.
extension Campaign {
    var nameValue: String { name ?? "" }
    var stateValue: String { state ?? "" }
    var budgetValue: Double { budget ?? 0 }
    var acosValue: Double { acos ?? -1 }
    var cvrValue: Double { cvr ?? -1 }
    var ctrValue: Double { ctr ?? -1 }
    var cpcValue: Double { cpc ?? -1 }
}

extension AdGroup {
    var nameValue: String { name ?? "" }
    var stateValue: String { state ?? "" }
    var asinValue: String { asin ?? "" }
    var bidValue: Double { defaultBid ?? 0 }
    var acosValue: Double { acos ?? -1 }
    var cvrValue: Double { cvr ?? -1 }
    var lifetimeValue: Double { lifetimeSales ?? 0 }
    var ctrValue: Double { ctr ?? -1 }
    var cpcValue: Double { cpc ?? -1 }
}

/// Route for the ad-group drill level (campaign context travels along).
struct AdGroupRoute: Hashable {
    let campaign: Campaign
    let adGroup: AdGroup
}

/// A state change waiting for the "always confirm" dialog.
struct PendingStateChange: Identifiable {
    let intent: ActionIntent
    let entityId: String
    let name: String
    let state: String

    var id: String { "\(entityId)|\(state)" }
}

/// A BULK state change (n entities) — bulk always confirms, per the app's rules.
struct PendingBulkChange: Identifiable {
    let intents: [ActionIntent]
    let state: String

    var id: String { "\(intents.map(\.id.uuidString).joined(separator: "|"))|\(state)" }
}

struct CampaignBrowserView: View {
    @Environment(AppState.self) private var appState

    var body: some View {
        @Bindable var appState = appState
        NavigationStack(path: $appState.campaignPath) {
            CampaignListView()
                .navigationDestination(for: Route.self) { route in
                    switch route {
                    case .campaign(let market, let campaignID):
                        CampaignRouteDestination(market: market, campaignID: campaignID)
                    case .adGroup(let market, let campaignID, let adGroupID):
                        AdGroupRouteDestination(market: market, campaignID: campaignID,
                                                adGroupID: adGroupID)
                    default:
                        ContentUnavailableView("Route unavailable",
                                               systemImage: "point.topleft.down.to.point.bottomright.curvepath")
                    }
                }
        }
        .onChange(of: appState.selectedMarket) { _, market in
            if appState.campaignPath.contains(where: { $0.market != nil && $0.market != market }) {
                appState.campaignPath.removeAll()
            }
        }
    }
}

private struct CampaignRouteDestination: View {
    @Environment(AppState.self) private var appState
    let market: String
    let campaignID: String
    @State private var campaign: Campaign?
    @State private var isLoading = true
    @State private var loadError: String?

    var body: some View {
        Group {
            if let campaign {
                AdGroupsView(campaign: campaign)
            } else if isLoading {
                ProgressView("Re-resolving campaign…")
            } else {
                ContentUnavailableView {
                    Label("Campaign no longer available", systemImage: "megaphone")
                } description: {
                    Text(loadError ?? "The campaign was deleted or renamed since the palette result was shown.")
                }
            }
        }
        .task(id: "\(market)|\(campaignID)|\(appState.dataStamp)") { await resolve() }
    }

    private func resolve() async {
        isLoading = true
        defer { isLoading = false }
        do {
            let response = try await appState.makeBridge().call(
                CampaignsResponse.self, ["campaigns"], market: market)
            guard !Task.isCancelled else { return }
            campaign = response.campaigns.first { $0.campaignId == campaignID }
        } catch {
            loadError = error.localizedDescription
        }
    }
}

private struct AdGroupRouteDestination: View {
    @Environment(AppState.self) private var appState
    let market: String
    let campaignID: String
    let adGroupID: String
    @State private var resolved: AdGroupRoute?
    @State private var isLoading = true
    @State private var loadError: String?

    var body: some View {
        Group {
            if let resolved {
                AdGroupDetailView(route: resolved)
            } else if isLoading {
                ProgressView("Re-resolving ad group…")
            } else {
                ContentUnavailableView {
                    Label("Ad group no longer available", systemImage: "rectangle.3.group")
                } description: {
                    Text(loadError ?? "The ad group was deleted or renamed since the palette result was shown.")
                }
            }
        }
        .task(id: "\(market)|\(campaignID)|\(adGroupID)|\(appState.dataStamp)") { await resolve() }
    }

    private func resolve() async {
        isLoading = true
        defer { isLoading = false }
        do {
            let bridge = try appState.makeBridge()
            async let campaignsCall = bridge.call(CampaignsResponse.self, ["campaigns"], market: market)
            async let adGroupsCall = bridge.call(
                AdGroupsResponse.self, ["adgroups", "--campaign", campaignID], market: market)
            let (campaigns, adGroups) = try await (campaignsCall, adGroupsCall)
            guard !Task.isCancelled else { return }
            if let campaign = campaigns.campaigns.first(where: { $0.campaignId == campaignID }),
               let adGroup = adGroups.adGroups.first(where: { $0.adGroupId == adGroupID }) {
                resolved = AdGroupRoute(campaign: campaign, adGroup: adGroup)
            }
        } catch {
            loadError = error.localizedDescription
        }
    }
}



#Preview {
    CampaignBrowserView()
        .environment(AppState())
}
