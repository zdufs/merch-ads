import Foundation

// Contract test: pipes real appctl JSON through the app's Codable models
// (compiled together with MerchAds/Models.swift by verify_models.sh).
// Usage: verify <endpoint> < json    — exits non-zero on decode failure.

let endpoint = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "?"
let data = FileHandle.standardInput.readDataToEndOfFile()
let decoder = JSONDecoder()
decoder.keyDecodingStrategy = .convertFromSnakeCase

func check<T: Decodable>(_ type: T.Type, detail: (T) -> String = { _ in "" }) {
    do {
        let envelope = try decoder.decode(Envelope<T>.self, from: data)
        guard envelope.ok else { print("\(endpoint): ENGINE ERROR \(envelope.error ?? "?")"); exit(2) }
        guard let payload = envelope.data else { print("\(endpoint): NO DATA"); exit(2) }
        print("\(endpoint): OK \(detail(payload))")
    } catch {
        print("\(endpoint): DECODE FAIL \(error)")
        exit(1)
    }
}

switch endpoint {
case "markets":
    check(MarketsResponse.self) { "count=\($0.markets.count) current=\($0.current)" }
case "metrics":
    check(MetricsResponse.self) { m in
        let t = m.trailing30.map { "spend=\($0.spend) acos=\(String(describing: $0.acos))" } ?? "empty"
        let month = m.month.map { "month=\($0.month)" } ?? "no-month"
        return "\(t) \(month) ytd=\(m.ytd?.spend ?? -1)"
    }
case "monthly":
    check(MonthlyResponse.self) { "months=\($0.months.count) ytd_spend=\($0.ytd?.spend ?? -1) first=\($0.coverage?.firstDay ?? "?")" }
case "campaigns":
    check(CampaignsResponse.self) { "count=\($0.count) decoded=\($0.campaigns.count)" }
case "adgroups":
    check(AdGroupsResponse.self) { "ad_groups=\($0.adGroups.count)" }
case "targets":
    check(TargetsResponse.self) { "targets=\($0.targets.count) live=\(String(describing: $0.live))" }
case "searchterms":
    check(SearchTermsResponse.self) { "terms=\($0.searchTerms.count)" }
case "asin":
    check(AsinResponse.self) { "asin=\($0.asin) ad_groups=\($0.adGroups.count)" }
case "bidhistory":
    check(BidHistoryResponse.self) { "changes=\($0.changes.count)" }
case "killlist":
    check(KillListResponse.self) { "count=\($0.count)" }
case "health":
    check(HealthResponse.self) { "markets=\($0.markets.count) kill=\($0.killActive) gate=\(String(describing: $0.approvalRequired))" }
case "overview":
    check(OverviewResponse.self) { "markets=\($0.markets.count)" }
case "digest":
    check(DigestResponse.self) { "actions=\($0.actions.count)" }
case "bidreport":
    check(BidReportResponse.self) { "count=\($0.count) ups=\($0.ups) downs=\($0.downs)" }
case "harvest":
    check(HarvestResponse.self) { "count=\($0.count) pending=\($0.pending)" }
case "harvest-prune":
    check(HarvestPruneResponse.self) { "count=\($0.count) wasted=\($0.wasted)" }
case "stale":
    check(StaleResponse.self) { "count=\($0.count)" }
case "alerts":
    check(AlertsResponse.self) { "alerts=\($0.alerts.count)" }
case "profit":
    check(ProfitResponse.self) { "designs=\($0.designCount ?? 0) profit=\($0.totalProfit ?? 0)" }
case "demandfeed":
    check(DemandFeedResponse.self) { "seeds=\($0.keywordSeeds.count) proven=\($0.provenSellers.count)" }
case "import-preview":
    check(ImportPreviewResponse.self) { "new=\($0.new) routes=\($0.routes.count)" }
case "kill":
    check(KillResponse.self) { "active=\($0.killActive)" }
case "audit":
    check(AuditResponse.self) { "rows=\($0.count)" }
case "livestate":
    check(LiveStateResponse.self) { "groups=\($0.groups.count)" }
default:
    print("unknown endpoint \(endpoint)")
    exit(3)
}
