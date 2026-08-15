import Foundation

// Codable mirrors of the appctl.py JSON contract (docs/claude-code-handoff.md).
// Decoded with .convertFromSnakeCase, so `has_data` → `hasData` etc.
// `acos`/`cvr` are fractions (0.1816 = 18.16%); money is in the market currency.

// MARK: - Envelope

/// Every appctl command prints exactly one of these on stdout.
struct Envelope<T: Decodable>: Decodable {
    let ok: Bool
    let data: T?
    let error: String?
}

// MARK: - markets

struct Market: Codable, Identifiable, Hashable {
    let code: String
    let currency: String?
    let region: String?
    let isDefault: Bool
    let hasData: Bool
    var kind: String? = nil        // "merch" | "kdp"
    var label: String? = nil       // friendly switcher name (e.g. "KDP US")

    var id: String { code }
    var isKDP: Bool { kind == "kdp" }
    var displayLabel: String { label ?? code }

    var currencySymbol: String {
        switch currency {
        case "USD": "$"
        case "GBP": "£"
        case "EUR": "€"
        default: currency ?? ""
        }
    }

    /// Static list used before the first `markets` call returns (matches markets.py).
    static let fallback: [Market] = [
        Market(code: "US", currency: "USD", region: "NA", isDefault: true, hasData: true),
        Market(code: "UK", currency: "GBP", region: "EU", isDefault: false, hasData: true),
        Market(code: "DE", currency: "EUR", region: "EU", isDefault: false, hasData: true),
        Market(code: "FR", currency: "EUR", region: "EU", isDefault: false, hasData: true),
        Market(code: "ES", currency: "EUR", region: "EU", isDefault: false, hasData: true),
        Market(code: "IT", currency: "EUR", region: "EU", isDefault: false, hasData: true),
    ]
}

struct MarketsResponse: Codable {
    let markets: [Market]
    let current: String
}

// MARK: - max-bid ceiling (per-market)

/// `appctl maxbid` — dollar strings or nil when a surface has no ceiling.
struct MaxBidResponse: Codable {
    let market: String
    let target: String?
    let keyword: String?
    let budget: String?    // daily campaign-budget ceiling (same clamp family)
}

// MARK: - rules DSL (economics-aware automation language)

struct RuleSeason: Codable, Hashable {
    let start: String?
    let end: String?
}

struct RuleSummary: Codable, Identifiable, Hashable {
    let name: String
    let enabled: Bool
    let mode: String            // "review" | "auto"
    let season: RuleSeason?
    let updated: String?
    var id: String { name }
}

struct RuleListResponse: Codable {
    let rules: [RuleSummary]
}

struct Rule: Codable {
    let name: String
    let text: String
    let enabled: Bool
    let mode: String
    let season: RuleSeason?
    let updated: String?
}

struct RuleValidationError: Codable, Identifiable, Hashable {
    let line: Int
    let col: Int
    let message: String
    var id: String { "\(line):\(col):\(message)" }
}

struct RuleValidateResponse: Codable {
    let ok: Bool
    let errors: [RuleValidationError]
}

/// A proposed change from rules-preview (never executed in preview).
struct RuleChange: Codable, Identifiable, Hashable {
    let entityKind: String
    let entityId: String?
    let label: String
    let action: String
    let argsText: String?
    let note: String?
    let econDriven: Bool?
    let trace: [ConditionTrace]?
    var id: String { "\(entityKind):\(entityId ?? label):\(action)" }
}

struct RulePreviewResponse: Codable {
    let ok: Bool
    let market: String?
    let evaluated: Int?
    let matched: Int?
    let changes: [RuleChange]?
    let truncated: Bool?
    let errors: [RuleValidationError]?
}

/// A review-mode rule change waiting in the Approval queue.
struct RulePendingChange: Codable, Identifiable, Hashable {
    let id: String
    let rule: String
    let entityKind: String
    let label: String
    let action: String
    let argsText: String?
    let note: String?
    let econDriven: Bool?
    let trace: [ConditionTrace]?
    /// Set when another rule wants the same entity. Nothing checked for this
    /// before: both writes went through and whichever ran last silently won.
    var conflict: RuleConflict? = nil
}

/// One side of a cross-rule clash, as the engine's conflict guard sees it.
struct RuleConflict: Codable, Hashable {
    /// The other rules competing for this entity.
    let with: [String]
    /// What they compete for: "bid", "state", "budget", "negatives".
    let surface: String
    /// The rule that wins, which is the first one in rule order.
    let winner: String
    /// Whether THIS change is the one that survives.
    let kept: Bool
}

struct RulePendingResponse: Codable {
    let market: String
    let changes: [RulePendingChange]
    /// How many ENTITIES more than one rule wants (not how many rows).
    var conflicts: Int? = nil
}

// MARK: - sync calendar (GitHub-style heat-grid, 4 modes)

struct SyncDay: Codable, Hashable {
    let date: String
    let stored: Bool
    let spend: Double
    var sales: Double = 0
    let orders: Int
    let adjusted: Int
    var impressions: Int? = nil
    var clicks: Int? = nil
    var units: Int? = nil
}

struct SyncCalTotals: Codable, Hashable {
    let days: Int
    let adjusted: Int
    let spend: Double
    let orders: Int
}

struct ReportBounds: Codable, Hashable {
    let min: String?
    let max: String?
}

struct ReportTotals: Codable, Hashable {
    let spend: Double
    let sales: Double
    let orders: Int
    let impressions: Int
    let clicks: Int
    let units: Int
    let acos: Double?
    let roas: Double?
    let ctr: Double?
    let cpc: Double?
    let cvr: Double?
    let cpo: Double?
}

struct ReportResponse: Codable {
    let market: String
    let start: String?
    let end: String?
    let available: ReportBounds
    let dayCount: Int
    let totals: ReportTotals
    let days: [SyncDay]
}

/// Per-day metric series summed over selected campaigns (appctl campaigndaily) —
/// reuses SyncDay so the metric chart renders it directly.
struct CampaignDailyResponse: Codable {
    let market: String
    let campaignIds: [String]
    let days: [SyncDay]
    let count: Int
    let note: String?
}

struct SyncCalResponse: Codable {
    let market: String
    let days: [SyncDay]
    let count: Int
    let totals: SyncCalTotals
}

// MARK: - watchlist (private per-market pinboard)

struct WatchlistRow: Codable, Identifiable, Hashable {
    let kind: String
    let id: String?
    let label: String
    let resolved: Bool
    let impressions: Int
    let clicks: Int
    let spend: Double
    let orders: Int
    let sales: Double
    let acos: Double?
    let cvr: Double
}

struct WatchlistSummary: Codable, Hashable {
    let impressions: Int
    let clicks: Int
    let spend: Double
    let orders: Int
    let sales: Double
    let acos: Double?
    let cvr: Double
}

struct WatchlistResponse: Codable {
    let market: String
    let asOf: String?
    let rows: [WatchlistRow]
    let summary: WatchlistSummary
}

// MARK: - accumulated reports (cross-campaign rollups)

struct AccumulatedAsinRow: Codable, Identifiable, Hashable {
    let asin: String
    let productType: String?
    let campaigns: Int
    let adGroups: Int
    let impressions: Int
    let clicks: Int
    let spend: Double
    let orders: Int
    let sales: Double
    let acos: Double?
    let cvr: Double
    var id: String { asin }
}

struct AccumulatedAsinsResponse: Codable {
    let market: String
    let asOf: String?
    let count: Int              // the true total
    let returned: Int?          // what this response actually carries
    let truncated: Bool?        // true when the engine capped the rows
    let rows: [AccumulatedAsinRow]
}

struct AccumulatedKeywordRow: Codable, Identifiable, Hashable {
    let targeting: String
    let matchType: String?
    let campaigns: Int
    let adGroups: Int
    let impressions: Int
    let clicks: Int
    let spend: Double
    let orders: Int
    let sales: Double
    let acos: Double?
    let cvr: Double
    var id: String { "\(targeting)|\(matchType ?? "")" }
}

struct AccumulatedKeywordsResponse: Codable {
    let market: String
    let asOf: String?
    let count: Int              // the true total
    let returned: Int?          // what this response actually carries
    let truncated: Bool?        // true when the engine capped the rows
    let rows: [AccumulatedKeywordRow]
}

/// One per-campaign row when an accumulated row is expanded. `matchType` present
/// only for keyword expansions; `adGroup`/`campaign` are display names.
struct AccumulatedBreakdownRow: Codable, Identifiable, Hashable {
    let campaignId: String
    let campaign: String?
    let state: String?          // campaign state: ENABLED | PAUSED | ARCHIVED
    let adGroupId: String
    let adGroup: String?
    let matchType: String?
    let impressions: Int
    let clicks: Int
    let spend: Double
    let orders: Int
    let sales: Double
    let acos: Double?
    let cvr: Double
    var id: String { "\(campaignId)|\(adGroupId)|\(matchType ?? "")" }
}

struct AccumulatedBreakdownResponse: Codable {
    let market: String
    let asOf: String?
    let asin: String?
    let targeting: String?
    let breakdown: [AccumulatedBreakdownRow]
}

// MARK: - debug traces (per-entity condition evaluation on previews)

/// One condition a preview rule evaluated: additive on killlist / negatives /
/// resetbids rows. `actual`/`threshold` are raw (fractions for acos/cvr, dollars
/// for bids/spend); the app formats them. nil `actual` renders as "—".
struct ConditionTrace: Codable, Identifiable, Hashable, Sendable {
    let condition: String
    let actual: Double?
    let threshold: Double?
    let pass: Bool

    var id: String { condition }
}

// MARK: - monthly / YTD (from banked daily_totals history)

struct MonthRow: Codable, Identifiable, Hashable {
    let month: String             // "2026-04"
    let spend: Double
    let sales: Double
    let orders: Int
    let acos: Double?
    let daysBanked: Int

    var id: String { month }
}

struct YearToDate: Codable, Hashable {
    let year: String
    let spend: Double
    let sales: Double
    let orders: Int
    let acos: Double?
}

struct DailyCoverage: Codable, Hashable {
    let firstDay: String?
    let lastDay: String?
}

struct MonthlyResponse: Codable {
    let market: String
    let currency: String?
    let months: [MonthRow]
    let ytd: YearToDate?
    let coverage: DailyCoverage?
    let note: String?
}

struct BackfillResponse: Codable {
    let market: String
    let code: Int
    let text: String
}

// MARK: - metrics

struct MetricsResponse: Codable {
    let market: String
    let currency: String?
    let empty: Bool?              // {"market": .., "empty": true} when no data yet
    let trailing30: TrailingMetrics?
    let daily: PeriodMetrics?
    let mtd: PeriodMetrics?
    let movers: [Mover]?
    let month: MonthRow?          // current calendar month (banked days so far)
    let ytd: YearToDate?
    let coverage: DailyCoverage?
    // note: the endpoint also returns `trend` (rolling trailing-30 snapshots);
    // the app charts true per-day history from `daily` instead.
}

/// The headline numbers: latest rolling ~30-day snapshot (stable, trustworthy).
struct TrailingMetrics: Codable {
    let spend: Double
    let sales: Double
    let orders: Int
    let clicks: Int
    let acos: Double?
    let cvr: Double?
    let asOf: String
}

/// True single-day / month-to-date totals. `settling == true` means the freshest
/// day is still under-attributed — show muted / labelled.
struct PeriodMetrics: Codable {
    let window: String
    let spend: Double
    let sales: Double
    let orders: Int
    let acos: Double?
    let settling: Bool?
}

struct Mover: Codable, Identifiable {
    let campaign: String
    let delta: Double

    var id: String { campaign }
}

// MARK: - campaigns

struct Campaign: Codable, Identifiable, Hashable {
    let campaignId: String
    let name: String?
    let type: String
    let state: String?
    let targeting: String?        // AUTO / MANUAL (from the campaign record)
    let budget: Double?
    let bidding: String?
    let spend: Double
    let sales: Double
    let orders: Int
    let clicks: Int
    let impressions: Int?
    let acos: Double?
    let cvr: Double?

    var id: String { campaignId }

    /// Trailing-30 average daily spend as a share of the daily budget —
    /// ≥0.9 matches the engine's budget_max alert ("likely capped").
    var budgetUse: Double? {
        guard let budget, budget > 0 else { return nil }
        return (spend / 30.0) / budget
    }

    /// Click-through rate = clicks / impressions (nil when un-served).
    var ctr: Double? {
        guard let impressions, impressions > 0 else { return nil }
        return Double(clicks) / Double(impressions)
    }

    /// Cost per click = spend / clicks (nil when no clicks).
    var cpc: Double? {
        guard clicks > 0 else { return nil }
        return spend / Double(clicks)
    }
}

struct CampaignsResponse: Codable {
    let market: String
    let count: Int
    let campaigns: [Campaign]
}

// MARK: - adgroups

struct AdGroup: Codable, Identifiable, Hashable {
    let adGroupId: String
    let name: String?
    let state: String?
    let defaultBid: Double?
    let asin: String?
    let type: String?
    let lifetimeSales: Double?
    let spend: Double
    let sales: Double
    let orders: Int
    let clicks: Int
    let impressions: Int?
    let acos: Double?
    let cvr: Double?

    var id: String { adGroupId }

    var ctr: Double? {
        guard let impressions, impressions > 0 else { return nil }
        return Double(clicks) / Double(impressions)
    }
    var cpc: Double? {
        guard clicks > 0 else { return nil }
        return spend / Double(clicks)
    }
}

struct AdGroupsResponse: Codable {
    let market: String
    let campaignId: String
    let adGroups: [AdGroup]
}

// MARK: - targets

struct TargetRow: Codable, Identifiable, Hashable {
    let targetId: String?
    let targeting: String?
    let matchType: String?
    let impressions: Int
    let clicks: Int
    let spend: Double
    let sales: Double
    let orders: Int
    let acos: Double?
    let cvr: Double?
    let lastBid: Double?          // parsed from the most recent writes_log bid_change
    let bidChanges: Int
    let liveBid: Double?          // present only after a --live fetch
    let liveState: String?

    // Fallback id folds the metrics in — duplicate targeting rows without a
    // target_id would otherwise collide.
    var id: String {
        targetId ?? "\(targeting ?? "?")|\(matchType ?? "?")|\(impressions)|\(clicks)|\(spend)"
    }

    /// Best known current bid: Amazon live if fetched, else last logged change.
    var currentBid: Double? { liveBid ?? lastBid }

    var ctr: Double? {
        guard impressions > 0 else { return nil }
        return Double(clicks) / Double(impressions)
    }
    var cpc: Double? {
        guard clicks > 0 else { return nil }
        return spend / Double(clicks)
    }
}

struct TargetsResponse: Codable {
    let market: String
    let adGroupId: String
    let asOf: String?
    let live: Bool?
    let targets: [TargetRow]
}

// MARK: - alltargets (account-wide Targets tab)

struct AllTargetRow: Codable, Identifiable, Hashable {
    let targetId: String?
    let targeting: String?
    let matchType: String?
    let campaignId: String?
    let campaign: String?
    let adGroupId: String?
    let adGroup: String?
    let asin: String?
    let bid: Double?           // the entity's OWN bid from the pull's targets mirror
    let bidInherited: Bool?    // true = no own bid, the ad-group default rules the auction
    let impressions: Int
    let clicks: Int
    let spend: Double
    let sales: Double
    let orders: Int
    let acos: Double?
    let cvr: Double?

    var id: String {
        targetId ?? "\(targeting ?? "?")|\(matchType ?? "?")|\(adGroupId ?? "?")|\(impressions)|\(spend)"
    }

    var ctr: Double? {
        guard impressions > 0 else { return nil }
        return Double(clicks) / Double(impressions)
    }
    var cpc: Double? {
        guard clicks > 0 else { return nil }
        return spend / Double(clicks)
    }
    var cpo: Double? {
        guard orders > 0 else { return nil }
        return spend / Double(orders)
    }
    var roas: Double? {
        guard spend > 0 else { return nil }
        return sales / spend
    }

    // Non-optional sort proxies (Table's KeyPathComparator needs Comparable).
    var targetingValue: String { targeting ?? "" }
    var matchValue: String { matchType ?? "" }
    var campaignValue: String { campaign ?? "" }
    var asinValue: String { asin ?? "" }
    var acosValue: Double { acos ?? -1 }
    var cvrValue: Double { cvr ?? -1 }
    var bidValue: Double { bid ?? -1 }
    var ctrValue: Double { ctr ?? -1 }
    var cpcValue: Double { cpc ?? -1 }
}

struct AllTargetsResponse: Codable {
    let market: String
    let asOf: String?
    let count: Int
    let truncated: Bool
    let targets: [AllTargetRow]
}

// MARK: - searchterms

struct SearchTermRow: Codable, Identifiable, Hashable {
    let searchTerm: String
    let targeting: String?
    let matchType: String?
    let impressions: Int
    let clicks: Int
    let spend: Double
    let sales: Double
    let orders: Int
    let acos: Double?
    let cvr: Double?

    var id: String { "\(searchTerm)|\(targeting ?? "")|\(matchType ?? "")" }

    var ctr: Double? {
        guard impressions > 0 else { return nil }
        return Double(clicks) / Double(impressions)
    }
    var cpc: Double? {
        guard clicks > 0 else { return nil }
        return spend / Double(clicks)
    }
}

struct SearchTermsResponse: Codable {
    let market: String
    let adGroupId: String
    let asOf: String?
    let searchTerms: [SearchTermRow]
}

// MARK: - asin

struct AsinAdGroup: Codable, Identifiable, Hashable {
    let adGroupId: String
    let adGroup: String?
    let stateCached: String?      // from the last pull — use `status` for live state
    let bid: Double?
    let campaignId: String
    let campaign: String?
    let type: String?
    let spend: Double
    let sales: Double
    let orders: Int
    let clicks: Int
    let acos: Double?
    let cvr: Double?

    var id: String { adGroupId }
}

struct AsinResponse: Codable {
    let market: String
    let asin: String
    let productType: String?
    let lifetimeSales: Double?
    let note: String?
    let adGroups: [AsinAdGroup]
}

// MARK: - bidhistory

struct BidChange: Codable, Identifiable {
    let at: String
    let old: Double?
    let new: Double?
    let reason: String?

    // `at` alone can collide (two logged changes in the same second).
    var id: String { "\(at)|\(old ?? -1)|\(new ?? -1)" }
}

struct BidHistoryResponse: Codable {
    let targetId: String
    let changes: [BidChange]
}

// MARK: - history (dated perf series from banked snapshots)

struct HistoryPoint: Codable, Identifiable, Hashable {
    let date: String
    let impressions: Int
    let clicks: Int
    let spend: Double
    let sales: Double
    let orders: Int
    let acos: Double?
    let cvr: Double?

    var id: String { date }
}

struct HistoryResponse: Codable {
    let market: String
    let entity: String            // "campaign" / "ad_group" / "target"
    let id: String
    let note: String?
    /// Which kind of series the engine returned. `daily` is true per-day totals
    /// from target_daily. `trailing30_snapshot` is one trailing-30 aggregate per
    /// pull date, so consecutive points overlap by 29 days. The two look
    /// identical on a chart, so the caption has to say which one is on screen.
    let basis: String?
    /// How many distinct dates the returned points cover. A market mid-backfill
    /// returns a short series honestly, and six points should say so.
    let daysBanked: Int?
    let first: String?            // first date covered, nil when there are no points
    let last: String?             // last date covered
    let points: [HistoryPoint]

    /// True when every point is one real day. Anything else is a rolling
    /// trailing-30 window, so treat an unknown basis as the old shape.
    var isDaily: Bool { basis == "daily" }
}

// MARK: - negatives inventory (already-applied negative keywords)

struct AppliedNegative: Codable, Identifiable, Hashable {
    let term: String
    let at: String
    let result: String?

    var id: String { term }
}

struct NegativesListResponse: Codable {
    let market: String
    let adGroupId: String
    let count: Int
    let negatives: [AppliedNegative]
}

// MARK: - daily (true per-day account totals)

struct DailyDay: Codable, Identifiable, Hashable {
    let date: String
    let spend: Double
    let sales: Double
    let orders: Int
    let acos: Double?

    var id: String { date }
}

struct DailyResponse: Codable {
    let market: String
    let currency: String?
    let days: [DailyDay]
}

// MARK: - killlist

struct KillDesign: Codable, Identifiable, Hashable {
    let asin: String?
    let adGroupId: String
    let type: String?
    let state: String?
    let clicks: Int
    let orders: Int
    let cvr: Double
    let spend: Double
    let sales: Double
    let acos: Double?
    let breakEven: Double?
    let trace: [ConditionTrace]?

    var id: String { adGroupId }
}

/// One design bought off another design's ad — the measured cross-sell that
/// spares a bleeder from the kill list.
struct KillSparedOther: Codable, Hashable {
    let asin: String
    let units: Int
    let royalty: Double
}

/// A design that met the kill rule (CVR under floor, ACOS over break-even) but
/// was SPARED because its ad drives enough owned cross-sell royalty to cover its
/// own spend. Pausing it would kill the catalogue sales its ad creates.
struct KillSpared: Codable, Identifiable, Hashable {
    let asin: String?
    let adGroupId: String
    let type: String?
    let state: String?
    let spend: Double
    let crossSellRoyalty: Double
    let ownedUnits: Int
    let others: [KillSparedOther]

    var id: String { adGroupId }
}

struct KillListResponse: Codable {
    let market: String
    let cvrFloor: Double
    let count: Int
    let designs: [KillDesign]
    // Optional: an older engine build omits it, so a cached/old reply still decodes.
    let spared: [KillSpared]?
}

// MARK: - bidreport

struct BidReportChange: Codable, Identifiable, Hashable {
    let at: String
    let targetId: String
    let old: Double?
    let new: Double?
    let delta: Double?
    let reason: String?
    let adGroupId: String?
    let targeting: String?
    let asin: String?

    var id: String { "\(at)|\(targetId)" }

    /// Sort proxy — a missing delta sorts as no change.
    var deltaValue: Double { delta ?? 0 }
}

struct BidReportResponse: Codable {
    let market: String
    let days: Int
    let ups: Int
    let downs: Int
    let netDelta: Double
    let count: Int
    let changes: [BidReportChange]
}

// MARK: - harvest

struct HarvestWinner: Codable, Identifiable, Hashable {
    let searchTerm: String
    let sourceAdGroupId: String?
    let kind: String?
    let type: String?
    let sourceCampaignId: String?
    let clicks: Int
    let orders: Int
    let sales: Double
    let acos: Double?
    let cpc: Double?
    let firstSeen: String?
    let lastSeen: String?
    let promoted: Bool
    let needsDesign: Bool?          // optional — old/cached replies omit it

    var id: String { "\(searchTerm)|\(sourceAdGroupId ?? "")" }
}

struct HarvestResponse: Codable {
    let market: String
    let count: Int
    let pending: Int
    let winners: [HarvestWinner]
}

// MARK: - harvest suggest / promote group (cohort winners with no design yet)

struct SuggestedDesign: Codable, Identifiable, Hashable {
    let asin: String
    let title: String?
    let productType: String?
    let matchedWords: [String]?
    let score: Int
    let lifetimeSales: Int?

    var id: String { asin }
}

struct HarvestSuggestResponse: Codable {
    let term: String
    let count: Int
    let suggestions: [SuggestedDesign]
}

struct PromoteGroupResult: Codable {
    let applied: Bool
    let result: PromoteGroupOutcome?
}

struct PromoteGroupOutcome: Codable {
    let promoted: Bool?
    let keywordsCreated: Int?
    let groupsWithKeyword: Int?
}

// MARK: - harvest prune (wasteful exact keywords)

struct PruneKeyword: Codable, Identifiable, Hashable {
    let keywordId: String
    let kind: String?              // "keyword" or "target" (ASIN product target)
    let keyword: String
    let asin: String?
    let type: String?
    let clicks: Int
    let orders: Int
    let spend: Double
    let sales: Double
    let acos: Double?
    let cvr: Double?
    let breakEven: Double?
    let reason: String

    var id: String { keywordId }
}

struct HarvestPruneResponse: Codable {
    let market: String
    let asOf: String?
    let count: Int
    let wasted: Double
    let keywords: [PruneKeyword]
}

struct HarvestPruneApplyResponse: Codable {
    let market: String
    let paused: Int
    let note: String?
}

// MARK: - stale

struct StaleDesign: Codable, Identifiable, Hashable {
    let adGroupId: String
    let name: String?
    let asin: String?
    let type: String?
    let impressions: Int
    let clicks: Int
    let spend: Double

    var id: String { adGroupId }
}

struct StaleResponse: Codable {
    let market: String
    let asOf: String?
    let minImpressions: Int
    let count: Int
    let designs: [StaleDesign]
}

// MARK: - demand feed

struct DemandSeed: Codable, Identifiable, Hashable {
    let term: String
    let niche: String?
    let productType: String?
    let orders: Int
    let sales: Double
    let acos: Double?
    let cvr: Double?

    var id: String { term }
}

struct ProvenSeller: Codable, Identifiable, Hashable {
    let asin: String
    let title: String?
    let productType: String?
    let brand: String?
    let royaltyLast30: Double
    let salesLast30: Int
    let action: String?

    var id: String { asin }
}

struct DemandFeedResponse: Codable {
    let schema: String?
    let generated: String?
    let market: String
    let notes: String?
    let keywordSeeds: [DemandSeed]
    let provenSellers: [ProvenSeller]
}

// MARK: - organic halo (US-only: does advertising move organic royalty?)

/// One advertised design's ad-serving-windowed organic-halo estimate.
struct HaloDesign: Codable, Identifiable, Hashable {
    let asin: String
    let name: String?
    let title: String?
    /// Which campaign kinds advertise this design ("lottery, scavenger"). The
    /// estimate spans every campaign type now, so the reader needs to see how a
    /// design is being bought before judging its halo. Optional: older engine
    /// builds do not send it.
    let campaignTypes: String?
    let adStart: String?
    let adSpend: Double
    let adClicks: Int
    let totalRoyalty: Double
    let netUnits: Int
    let preDays: Int
    let postDays: Int
    let preRoyalty: Double
    let postRoyalty: Double
    let baseRate: Double
    let postRate: Double
    let haloEst: Double
    let netHalo: Double
    let trazWindow: Double?
    let flags: String?

    var id: String { asin }
    /// Sortable, never-nil proxy for the Campaigns column.
    var campaignTypesValue: String { campaignTypes ?? "" }
    /// Readable label: "Title — ASIN".
    ///
    /// `title` is the design's full product title, resolved backend-side from its
    /// descriptive ad-group name. A design with no descriptive ad group has no
    /// title, and falls back to whatever label the backend sent — still tagged
    /// with the ASIN so the design stays identifiable.
    var label: String {
        if let title, !title.isEmpty { return "\(title) — \(asin)" }
        let fallback = (name ?? asin)
            .replacingOccurrences(of: " - \(asin)", with: "")
        return fallback == asin ? asin : "\(fallback) — \(asin)"
    }
}

struct HaloResponse: Codable {
    let market: String
    let supported: Bool
    let reason: String?
    let reportStart: String?
    let reportEnd: String?
    let note: String?
    /// True total before the response cap; `designs` may carry fewer.
    let count: Int?
    let returned: Int?
    let truncated: Bool?
    let minSpend: Double?
    let designs: [HaloDesign]
}

// MARK: - sales report (the only source of ORGANIC royalty)

/// The dated Merch sales report the engine reads for organic royalty. The Ads
/// API reports ad-attributed sales only, so the organic-halo estimate and the
/// royalty-vs-spend analysis both depend on this file being present and current.
struct SalesReport: Codable, Hashable {
    let filename: String
    let folder: String
    let start: String?
    let end: String?
    let rows: Int
    let usRows: Int
    let asins: Int
    let ageDays: Int?
    let stale: Bool
}

/// The file an import just wrote — not necessarily the one the engine reads,
/// since importing an older report leaves the newest one in charge.
struct ImportedSalesReport: Codable, Hashable {
    let filename: String
    let start: String
    let end: String
    let rows: Int
}

/// What the import actually added to the banked history. `newRows` of 0 means
/// the report was already covered — a caution, not a success.
struct BankedSalesImport: Codable, Hashable {
    let filename: String?
    let rowsInFile: Int?
    let rowsBanked: Int?
    let newRows: Int?
    let totalRows: Int?
    let periodStart: String?
    let periodEnd: String?
    let asins: Int?
    let skipped: Int?
    let error: String?
}

struct SalesReportResponse: Codable {
    let imported: Bool
    let copied: Bool?
    let file: ImportedSalesReport?
    let isNewest: Bool?
    let banked: BankedSalesImport?
    let report: SalesReport?
    let folder: String?
    let note: String?
}

// MARK: - profit

struct ProfitTypeRow: Codable, Identifiable, Hashable {
    let type: String
    let designs: Int
    let orders: Int
    let spend: Double
    let sales: Double
    let royaltyEst: Double
    let profit: Double
    let profitable: Int

    var id: String { type }
}

struct ProfitDesign: Codable, Identifiable, Hashable {
    let adGroupId: String
    let asin: String?
    let type: String?
    let orders: Int
    let clicks: Int
    let spend: Double
    let sales: Double
    let royaltyPerUnit: Double?
    let royaltyEst: Double
    let profit: Double
    let royaltyRoi: Double?

    var id: String { adGroupId }
}

/// Current-month profit. Spend and orders are exact (Amazon's MTD report);
/// `royaltyEst` is MODELED — there is no per-design daily data, so the engine
/// applies each product type's trailing-30 royalty per order. `modeled` is
/// always true and the UI must say so rather than showing this as a hard figure.
struct ProfitMTD: Codable {
    let month: String
    let window: String
    let modeled: Bool
    let spend: Double
    let orders: Double
    let royaltyEst: Double
    let profit: Double
    let uncoveredSpend: Double?
    let royaltyPerOrder: Double?
    let basis: String?
    let note: String?
}

// MARK: - sales-history (banked organic royalty)

/// A hole in the banked organic history. Royalty summed across a half-covered
/// window reads as a slump rather than as missing data, so gaps are surfaced.
struct SalesGap: Codable, Identifiable, Hashable {
    let start: String
    let end: String
    var id: String { "\(start)|\(end)" }
}

struct SalesCoverage: Codable {
    let days: Int
    let firstDay: String?
    let lastDay: String?
    let rows: Int?
    let asins: Int?
    let gaps: [SalesGap]?
}

struct SalesImportLogEntry: Codable, Identifiable {
    let kind: String?
    let filename: String
    let importedAt: String?
    let periodStart: String?
    let periodEnd: String?
    let rowsInFile: Int?
    let rowsBanked: Int?
    let note: String?
    var id: String { filename }
}

/// What organic history the engine actually knows — the accumulated union of
/// every imported report, not whichever file happens to be newest.
struct SalesHistoryResponse: Codable {
    let banked: Bool
    let coverage: SalesCoverage?
    let imports: [SalesImportLogEntry]?
    let note: String?
}

// MARK: - crosspurchase (measured halo from spPurchasedProduct)

/// One advertised design and what its ads actually sold. `otherSales` is the
/// part that went to a DIFFERENT ASIN — halo Amazon attributes but the campaign
/// and targeting reports credit nowhere.
struct CrossPurchaseDesign: Codable, Identifiable {
    let advertisedAsin: String?
    let adGroupId: String?
    let adGroup: String?
    let ownSales: Double
    let otherSales: Double
    let otherUnits: Int
    let distinctOthers: Int
    let otherPct: Double?

    var id: String { advertisedAsin ?? adGroupId ?? UUID().uuidString }
}

/// A measured "clicked this, bought that" pair. Self-purchases are excluded by
/// the engine, so every row here is genuine cross-selling.
struct CrossPurchasePair: Codable, Identifiable {
    let advertisedAsin: String?
    let purchasedAsin: String?
    let adGroup: String?
    let sales: Double
    let units: Int
    let purchases: Int

    var id: String { "\(advertisedAsin ?? "?")|\(purchasedAsin ?? "?")" }
}

struct CrossPurchaseTotals: Codable {
    let adSales: Double
    let ownAsinSales: Double
    let otherAsinSales: Double
    let otherPct: Double?
}

struct CrossPurchaseResponse: Codable {
    let market: String
    let supported: Bool
    let asOf: String?
    let totals: CrossPurchaseTotals?
    let designs: [CrossPurchaseDesign]?
    let pairs: [CrossPurchasePair]?
    let note: String?
}

// MARK: - periods (the dashboard's period stack)

/// One period in the stack. Spend/sales/orders/ACOS are exact and every period
/// reads the same banked daily history, so the rows are directly comparable.
/// `profit` is MODELED — royalty is per design and no per-design daily data
/// exists — and gets rougher the further back the window sits.
///
/// `available == false` means the data cannot cover this period at all (Amazon's
/// retention window). That is rendered as an explicit empty state with `reason`,
/// never as zeroes. `partial` means the window starts later than it should.
struct PeriodRow: Codable, Identifiable {
    let key: String
    let label: String
    let available: Bool
    let window: String?
    let requestedWindow: String?
    let partial: Bool?
    let partialReason: String?
    let reason: String?
    let daysBanked: Int?
    let spend: Double?
    let sales: Double?
    let orders: Int?
    let acos: Double?
    let profit: Double?
    let royaltyEst: Double?
    let royaltyPerOrder: Double?
    let coveredSpend: Double?
    let uncoveredSpend: Double?
    let basis: String?
    let modeled: Bool?

    var id: String { key }
}

extension PeriodRow {
    /// Periods the engine computes but the dashboard does not show.
    ///
    /// Removed 2026-08-06 at the operator's request. Both rows lean on the imported
    /// console history, and their profit figures are the weakest in the stack:
    /// "Previous year" is supplement-only (no banked days, so no profit basis at
    /// all), and "All time" reaches back to 2021 while modelling profit from
    /// today's royalty rates. The history itself is untouched — it stays in
    /// `ads_history_monthly`, which is the ONLY copy of anything past Amazon's
    /// ~95-day retention, and Reports still reads it.
    static let hiddenFromDashboard: Set<String> = ["previous_year", "all_time"]

    /// The rows under the status band, in the engine's own order.
    /// `current_month` is pinned above the fold, so it is dropped here to avoid
    /// rendering it twice.
    static func dashboardStack(from periods: [PeriodRow]) -> [PeriodRow] {
        periods.filter { $0.key != "current_month" && !hiddenFromDashboard.contains($0.key) }
    }
}

struct PeriodsCoverage: Codable {
    let firstDay: String?
    let lastDay: String?
}

struct PeriodsResponse: Codable {
    let market: String
    let currency: String?
    let empty: Bool?
    let coverage: PeriodsCoverage?
    let periods: [PeriodRow]
    let retentionNote: String?
    let note: String?
}

struct ProfitResponse: Codable {
    let market: String
    let asOf: String?
    let empty: Bool?
    let totalSpend: Double?
    let totalRoyaltyEst: Double?
    let totalProfit: Double?
    let mtd: ProfitMTD?
    let designCount: Int?
    let types: [ProfitTypeRow]?
    let designs: [ProfitDesign]?
    let note: String?
}

// MARK: - alerts

struct EngineAlert: Codable, Identifiable, Hashable {
    let kind: String
    let key: String
    let message: String
    // Structured entity for deep-linking "Review →" to the exact thing (added
    // 2026-07-19). budget_max carries campaignId; kill_candidate carries the
    // design's campaignId/adGroupId/asin; spend_spike is market-wide (no entity).
    var market: String? = nil
    var campaignId: String? = nil
    var adGroupId: String? = nil
    var asin: String? = nil

    var id: String { key }
}

struct AlertsResponse: Codable {
    let market: String
    let count: Int
    let alerts: [EngineAlert]
}

// MARK: - overview (all markets)

struct OverviewMarket: Codable, Identifiable, Hashable {
    let market: String
    let currency: String?
    let asOf: String?
    let spend: Double
    let sales: Double
    let orders: Int
    let clicks: Int
    let acos: Double?
    let cvr: Double?
    let dailySpend: Double?
    let dailySales: Double?
    let ytdSpend: Double?
    let ytdSales: Double?
    /// True when imported console months extend the year back past the banked
    /// daily history. Only US and UK can be supplemented — one console export
    /// covers every marketplace and carries no country, so DE/FR/ES/IT share a
    /// single merged EUR series that cannot be split per market.
    let ytdSupplemented: Bool?
    let ytdBasis: String?

    var id: String { market }
}

struct OverviewResponse: Codable {
    let markets: [OverviewMarket]
}

// MARK: - approval mode / digest / negate / livestate / promote

struct ApprovalModeResponse: Codable {
    let approvalRequired: Bool
}

struct DigestResponse: Codable {
    let market: String
    let since: String
    let latestWrite: String?
    let actions: [String: Int]
}

struct NegateResponse: Codable {
    let market: String
    let term: String
    let adGroupId: String
    let applied: Bool
}

struct LiveStateGroup: Codable, Identifiable, Hashable {
    let adGroupId: String
    let adGroup: String?
    let campaignId: String
    let campaign: String?
    let adGroupLive: String?
    let campaignLive: String?
    let bidLive: Double?

    var id: String { adGroupId }
}

struct LiveStateResponse: Codable {
    let market: String
    let asin: String
    let groups: [LiveStateGroup]
}

struct PhaseResult: Codable {
    let code: Int
    let text: String
}

struct PromoteResponse: Codable {
    let market: String
    let scoped: Int?
    let keywords: PhaseResult?
    let asins: PhaseResult?
}

// MARK: - health

struct PullNote: Codable, Hashable {
    let at: String
    let kind: String?
    let note: String
}

/// How much true per-day, per-target history one market has banked.
/// Rolling-window rules refuse to write when their window has holes, so this is
/// where the operator finds out why a rule went quiet.
struct TargetDailyCoverage: Codable, Hashable {
    let days: Int
    let first: String?
    let last: String?
}

struct MarketHealth: Codable, Identifiable, Hashable {
    let market: String
    let configured: Bool
    let hasData: Bool
    let latestData: String?       // WORST of the three perf tables (they drift independently)
    let lastPull: String?
    let lastWrite: String?
    let campaigns: Int?
    let lastNote: PullNote?       // newest non-empty pull_log note (errors show here)
    let reportsPending: Int?      // report_jobs not yet downloaded (stalled reports)
    let staleTables: [String]?    // perf tables past the write-freeze threshold (>3d)
    let targetDaily: TargetDailyCoverage?  // nil = no per-day history banked yet
    let error: String?

    var id: String { market }
}

/// One failed phase from the nightly run (run_scheduled.sh step tracker).
struct RunStepFailure: Codable, Hashable {
    let market: String
    let step: String
    let exit: Int
}

/// outputs/last_run_status.json — the nightly's machine-readable outcome.
/// With Discord digests off this is how a crashed phase reaches the operator.
struct LastRunStatus: Codable, Hashable {
    let started: String?
    let finished: String?
    let ok: Bool
    let failures: [RunStepFailure]
}

struct HealthResponse: Codable {
    let killActive: Bool
    let approvalRequired: Bool?
    let lastRun: LastRunStatus?
    let markets: [MarketHealth]
}

/// appctl `run-status` — a nightly run happening RIGHT NOW, parsed live from
/// scheduled_runs.log (the machine-readable status file lands only when a run
/// finishes). `active:false` when no run is in progress.
struct RunStatusResponse: Codable {
    let active: Bool
    let label: String?
    let started: String?
    let elapsedSeconds: Int?
    let markets: [String]?
    let reached: [String]?
    let currentMarket: String?
    let failures: [RunStepFailure]?
    let lastActivity: String?
}

/// The US economics freshness gate (`econ-gate`). Closed (`ok == false`) means
/// every economics-driven write refuses; `reasons` explains why.
struct EconGateResponse: Codable, Hashable {
    let ok: Bool
    let reasons: [String]
    let market: String?
    let modelVersion: String?
}

// MARK: - actions

struct KillResponse: Codable {
    let killActive: Bool
}

struct StateChangeResponse: Codable {
    let market: String
    let adGroupId: String?
    let campaignId: String?
    let prevState: String?
    let newState: String?
    let applied: Bool
    let http: [Int]?
}

struct SetBidResponse: Codable {
    let market: String
    let targetId: String
    let prevBid: String?
    let newBid: Double?
    let applied: Bool
}

struct SetBudgetResponse: Codable {
    let market: String
    let campaignId: String
    let prevBudget: Double?
    let newBudget: Double?
    let applied: Bool
}

struct ResetBidsItem: Codable, Identifiable, Equatable, Sendable {
    let targetId: String
    let original: Double
    let current: Double
    let new: Double
    let trace: [ConditionTrace]?

    var id: String { targetId }
}

struct ResetBidsResponse: Codable, Equatable, Sendable {
    let market: String
    let count: Int
    let totalReduction: Double
    let preview: Bool?
    let applied: Bool?
    let items: [ResetBidsItem]?
}

struct ProposedNegative: Codable, Identifiable, Hashable {
    let searchTerm: String
    let campaignId: String
    let adGroupId: String
    let spend: Double
    let reason: String
    let trace: [ConditionTrace]?

    var id: String { "\(adGroupId)|\(searchTerm)" }
}

struct ProposedPause: Codable, Identifiable, Hashable {
    let adGroupId: String
    let campaignId: String
    let spend: Double
    let reason: String
    let name: String?
    let asin: String?
    let trace: [ConditionTrace]?

    var id: String { adGroupId }
}

struct NegativesPreviewResponse: Codable {
    let market: String
    let asOf: String?
    let negatives: [ProposedNegative]
    let pauses: [ProposedPause]
}

struct NegativesApplyResponse: Codable {
    let market: String
    let negativesApplied: Int
    let pausesApplied: Int
}

/// `everywhere-preview` — what an accumulated "act everywhere" selection resolves
/// to, so the confirm sheet can say "pause this ASIN in 8 ad groups across 6
/// campaigns" before anything is applied.
struct EverywherePreviewResponse: Codable {
    let kind: String
    let action: String
    let count: Int
    let applicable: Int
    let skippedNoop: Int
    let campaigns: Int
}

/// `everywhere-apply` ack — how many instances were paused/negated, skipped as
/// no-ops, or failed.
struct EverywhereApplyResponse: Codable {
    let kind: String
    let action: String
    let applied: Int
    let skippedNoop: Int
    let failed: Int
    let count: Int
}

/// `rules-approve` ack: the approved subset routed through the rules executor.
/// `blocked` is set when a gate (KILL) refused the whole batch.
struct RulesApproveResponse: Codable {
    let applied: Bool?
    let count: Int?
    let blocked: String?
    /// Approved changes the conflict guard held back because another rule was
    /// already writing to that entity. They stay in the queue, so approving one
    /// on its own afterwards still works.
    var conflictsSkipped: Int? = nil
}

/// `history-import` — console monthly history banked into ads_history_monthly
/// (the only source past the API's ~95-day retention; feeds `periods`).
struct HistoryBankMeta: Codable, Hashable {
    let filename: String?
    let newRows: Int?
    let totalRows: Int?
    let rowsInFile: Int?
}

/// One (market, currency) split of the banked console history. "EU" is DE, FR,
/// ES and IT merged — the console export carries no country, only currency,
/// and EUR covers all four.
struct HistoryCoverageMarket: Codable, Hashable, Identifiable {
    let market: String
    let currency: String
    let months: Int
    let spend: Double
    let sales: Double
    let purchases: Int?

    var id: String { "\(market)|\(currency)" }
}

/// What the console monthly-history importer has banked so far, account-wide.
struct HistoryCoverage: Codable, Hashable {
    let months: Int
    let firstMonth: String?
    let lastMonth: String?
    let byMarket: [HistoryCoverageMarket]?
}

struct HistoryImportResponse: Codable {
    let imported: Bool
    let file: HistoryBankMeta?
    let coverage: HistoryCoverage?
}

/// `kdp-book` — per-book economics config (local kdp_books.json; no Amazon).
/// A book with no entry fails closed: economics unavailable, never guessed.
struct KdpBook: Codable, Identifiable, Hashable {
    let asin: String
    let title: String?             // full Amazon title (cached), or the ad-group name; nil if never fetched
    let advertised: Bool?          // has an ENABLED ad group in an ENABLED campaign (serving now)
    let listPrice: Double?
    let royalty: Double?           // as entered off the KDP dashboard
    let royaltyResolved: Double?   // what the engine will actually use
    let breakEven: Double?
    let known: Bool?

    var id: String { asin }
}

struct KdpBooksResponse: Codable {
    let books: [KdpBook]
    let count: Int
}

struct KdpTitlesResponse: Codable {
    let refreshed: Bool?
    let fetched: Int?
    let total: Int?
    let cached: Int?
    let missing: [String]?
}

struct KdpBookSaveResponse: Codable {
    let asin: String
    let saved: Bool?
    let cleared: Bool?
    let known: Bool?
}

struct AuditWrite: Codable, Identifiable, Hashable {
    let rowId: Int
    let at: String
    let action: String
    let entityType: String?
    let entityId: String
    let detail: String?
    let prevState: String?
    let result: String?
    let undoable: Bool
    let entityName: String?       // resolved ad-group/campaign name (ASIN fallback)

    var id: Int { rowId }

    /// Amazon bulk writes come back 200/207 — treat both (and the engine's own
    /// "submitted") as applied; the raw payload can still carry per-item errors,
    /// so surface it in a tooltip rather than as a scary red dict.
    var succeeded: Bool {
        guard let result else { return false }
        if result.hasPrefix("submitted") { return true }
        return result.contains("'http': 200") || result.contains("'http': 207")
    }

    /// Writes that did nothing (e.g. "0 ASINs" builder runs) — visual noise.
    var isNoOp: Bool {
        guard let detail else { return false }
        return detail.hasPrefix("0 ASINs") || detail.hasPrefix("0 designs")
    }
}

struct AuditResponse: Codable {
    let market: String
    let count: Int
    let writes: [AuditWrite]
    let hasMore: Bool?     // a full page — older history exists below it
}

struct UndoResponse: Codable {
    let market: String
    let undidRow: Int
    let entityId: String
    let newState: String?
    let restoredBid: Double?
    let applied: Bool
}

// MARK: - intake

/// `export-date` — New Designs' "Last recorded" date: the newest design-upload
/// date INSIDE the current catalogue export, read from the data (the `createdDate`
/// column), not the filename. The export is ~2M rows and takes ~18s to scan the
/// first time; the engine caches the result by the file's signature, so `cached`
/// is false only on that first read.
struct ExportDateResponse: Codable {
    let available: Bool
    let lastRecorded: String?
    let source: String?
    let rows: Int?
    let cached: Bool?
}

struct IntakeDesign: Codable, Identifiable, Hashable, Sendable {
    let asin: String
    let adAsins: [String]
    let type: String
    let series: String
    let title: String?
    let lifetimeSales: Int
    let created: String?
    let lottery: Bool

    var id: String { asin }
}

struct IntakeRoute: Codable, Identifiable, Equatable, Sendable {
    let route: String
    let count: Int
    let designs: [IntakeDesign]

    var id: String { route }
}

struct ImportPreviewResponse: Codable, Equatable, Sendable {
    let market: String
    let csv: String
    let days: Int
    let designsInMarket: Int
    let alreadyAdvertised: Int
    let new: Int
    let usLotteryNote: String?
    let routes: [IntakeRoute]
    let skippedTypes: [String: Int]
}

struct BuilderResult: Codable, Equatable, Sendable {
    let scopedTo: Int
    let code: Int
    let text: String
    let stderr: String?
}

struct AdoptedExport: Codable, Equatable, Sendable {
    let adopted: String
    let movedToPod: Bool
    let removed: [String]
    let freedMb: Int
}

struct AdoptExportResponse: Codable {
    let export: AdoptedExport?
}

struct ImportApplyResponse: Codable, Equatable, Sendable {
    let market: String
    let designs: Int?
    let built: Int?
    let note: String?
    let lottery: BuilderResult?
    let scavenger: BuilderResult?
    let export: AdoptedExport?
}

// MARK: - live / actions

struct RunResponse: Codable {
    let ran: String
    let text: String
    let code: Int
}

// MARK: - seasonal scheduler

struct SeasonsResponse: Codable {
    let market: String
    let today: String
    let seasons: [SeasonInfo]
    let tags: [SeasonTag]
}

struct SeasonInfo: Codable, Identifiable, Hashable {
    let key: String
    let label: String
    let resume: String
    let pause: String
    let active: Bool
    let nextTransition: String?
    let taggedCount: Int
    var id: String { key }
}

struct SeasonTag: Codable, Identifiable, Hashable {
    let asin: String
    let season: String
    let label: String
    let active: Bool?
    let productType: String?
    let adGroups: Int
    let enabled: Int
    let paused: Int
    var id: String { asin }
}

struct SeasonalPreviewResponse: Codable {
    let market: String
    let pause: [SeasonalAction]
    let enable: [SeasonalAction]
}

struct SeasonalAction: Codable, Identifiable, Hashable {
    let adGroupId: String
    let asin: String
    let season: String
    let label: String
    let name: String?
    var id: String { adGroupId }
}

struct SeasonTagResponse: Codable {
    let asin: String
    let season: String?
}

struct SeasonalApplyResponse: Codable {
    let market: String
    let paused: Int
    let enabled: Int
}

struct SeasonSuggestResponse: Codable {
    let market: String
    let suggestions: [SeasonSuggestion]
}

struct SeasonSuggestion: Codable, Identifiable, Hashable {
    let asin: String
    let name: String
    let season: String
    let label: String
    let keyword: String
    let currentSeason: String?
    let alreadyTagged: Bool
    var id: String { asin }
}

struct SeasonSuggestApplyResponse: Codable {
    let market: String
    let count: Int
}

struct SeasonCsvPreview: Codable {
    let season: String
    let label: String
    let csv: String
    let found: Int
    let already: Int
    let new: Int
    let sample: [String]
}

struct SeasonCsvApplyResponse: Codable {
    let season: String
    let label: String
    let csv: String
    let found: Int
    let already: Int
    let tagged: Int
}
