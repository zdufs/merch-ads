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

/// The same three ceilings as they arrive inside a `health` market row, as
/// numbers rather than the display strings `maxbid` returns.
///
/// nil on a surface means NO CEILING for that market — not "unknown". The engine
/// sends every surface explicitly for exactly that reason: a dropped key and an
/// unset ceiling would decode to the same nil, and the whole point of putting
/// this on System Health is that a market with no cap looks different from one
/// that has one.
struct BidCeilingRow: Codable, Hashable {
    let target: Double?
    let keyword: Double?
    let budget: Double?
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
    let cvr: Double?
}

struct WatchlistSummary: Codable, Hashable {
    let impressions: Int
    let clicks: Int
    let spend: Double
    let orders: Int
    let sales: Double
    let acos: Double?
    let cvr: Double?
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
    let cvr: Double?
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
    let cvr: Double?
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
    let cvr: Double?
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
    /// True when the banked history starts later than January, so this is a
    /// PART-year figure sitting under a full-year label. On 2026-08-22 that was
    /// six of the seven markets — the EU ones only began advertising in June
    /// and KDP in August — while US covered the whole year. Printing them the
    /// same way invites a comparison that is not there.
    let partial: Bool?
    /// The first month the figure actually covers, e.g. "2026-06".
    let firstMonth: String?
    let supplemented: Bool?
    let basis: String?

    /// "since Jun 2026" when the year is short, nil when it is whole.
    var partialLabel: String? {
        guard partial == true else { return nil }
        guard let firstMonth else { return "part-year" }
        return "since \(Format.monthName(firstMonth))"
    }
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
    let stderr: String?

    var failureTail: String {
        let source = (stderr?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false)
            ? stderr! : text
        return source.split(separator: "\n", omittingEmptySubsequences: true)
            .suffix(3).joined(separator: " · ")
    }
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
    /// The TRUE number of targets in the snapshot, not the number of rows below.
    ///
    /// The engine computed this after the cap, so it was always the cap itself
    /// and the tab said "top 2000 by spend" whether one target sat beyond the
    /// cap or fifty thousand did. `returned` is what this reply carries.
    let count: Int
    var returned: Int? = nil
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
    let cvr: Double?
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

    /// The targeting snapshot this verdict was read from, or nil when none is
    /// banked. A market with no data answered exactly like a healthy market
    /// with nothing worth killing: `count: 0` and an empty list. The two
    /// differed only in the `skipped` counters, which are non-zero today by
    /// luck. Nil here means nothing was evaluated at all.
    let asOf: String?

    /// How many ad groups the thresholds were actually applied to. `count: 0`
    /// beside `evaluated: 67` is a real all-clear; beside `evaluated: 0` it is
    /// not a verdict.
    let evaluated: Int?

    /// Set when there was nothing to evaluate. Rendered instead of the
    /// all-clear sentence.
    let note: String?
    // Optional: an older engine build omits it, so a cached/old reply still decodes.
    let spared: [KillSpared]?
    let skipped: KillSkipped?

    /// Set when the kill list could not run AT ALL, because this market's
    /// database predates the economics tables.
    ///
    /// The engine answers that case with `count: 0`, `designs: []` and this
    /// sentence. Without decoding it the screen is byte-identical to a healthy
    /// market where nothing is worth killing — a clean, empty, reassuring list.
    /// It is the same fault `KillSkipped` was written for, one level up: there
    /// the engine could not judge SOME designs, here it could not judge any.
    ///
    /// A fresh install is exactly when this fires, so it is the first thing a
    /// new operator would have been told wrongly.
    let econ: String?

    /// True when no verdict was possible for this market.
    var economicsUnavailable: Bool { (econ?.isEmpty == false) }
}

/// Designs the kill list could not JUDGE, counted rather than dropped.
///
/// A kill verdict needs the design's OWN break-even. A design whose price is in
/// a 30-day transition, whose price is unknown, or that shares an ad group with
/// other designs (a cohort has no per-design economics) is therefore excluded
/// before any threshold is applied.
///
/// This matters because the screen states a NEGATIVE. "No design is below the
/// CVR floor while over break-even" is a claim about every design; what the
/// engine actually established is that none of the designs it could judge
/// qualified. On 2026-08-22 that was 0 flagged and 49 never looked at. The two
/// read identically and only one of them is true.
///
/// `crossSell` is a deliberate spare with its own banner (`spared`), so it is
/// counted here but deliberately left out of `unjudged`.
struct KillSkipped: Codable, Hashable {
    let transition: Int?
    let unknownPrice: Int?
    let cohort: Int?
    let crossSell: Int?

    /// Designs excluded for want of economics.
    var unjudged: Int { (transition ?? 0) + (unknownPrice ?? 0) + (cohort ?? 0) }

    /// Plain-language reasons, biggest first, for the banner.
    var reasons: [String] {
        var out: [(Int, String)] = []
        if let n = transition, n > 0 { out.append((n, "\(n) in a 30-day price transition")) }
        if let n = unknownPrice, n > 0 { out.append((n, "\(n) with no known list price")) }
        if let n = cohort, n > 0 { out.append((n, "\(n) sharing an ad group with other designs")) }
        return out.sorted { $0.0 > $1.0 }.map(\.1)
    }
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
    /// How many targets were SENT. `paused` counts only what Amazon confirmed,
    /// so a wholly refused batch and a plan with nothing in it both answer 0 —
    /// and the screen printed that in the success colour (found 2026-08-24).
    let requested: Int?
    /// Rows Amazon NAMED as rejected.
    let failed: Int?
    /// Rows Amazon neither accepted nor named: a transport failure, or a
    /// multi-status with more errors than it could index. Not the same claim
    /// as `failed` — nobody knows whether these landed.
    let unconfirmed: Int?

    /// One sentence, or nil when every requested pause was confirmed.
    var shortfallNote: String? {
        guard let requested, requested > paused else { return nil }
        var parts: [String] = []
        if let failed, failed > 0 { parts.append("Amazon refused \(failed)") }
        if let unconfirmed, unconfirmed > 0 {
            parts.append("\(unconfirmed) unconfirmed")
        }
        if parts.isEmpty { parts.append("\(requested - paused) did not go through") }
        return "Paused \(paused) of \(requested) — " + parts.joined(separator: ", ")
             + ". See the Audit trail."
    }
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

    /// The royalty the RANKING used, whatever window it came from.
    ///
    /// This is the field that was missing, and the cost was total. Snap for MOD
    /// does not export `salesLast30` / `royaltyLast30` — that has been true
    /// since it replaced MerchFlow on 2026-08-15 — so `demand_feed` falls back
    /// to the design's lifetime royalty, ranks on it, and writes 0 into
    /// `royalty_last30` because it is honestly not a 30-day figure. The app
    /// read only that zero. Measured on 2026-08-23: 60 of 60 proven sellers
    /// drew 0.00 royalty and 0 sales, including a design that had actually
    /// earned four figures across several thousand units. The order was right
    /// and every number
    /// beside it was a zero.
    let royalty: Double?

    /// "last30" or "lifetime" — which window `royalty` covers. The engine has
    /// always sent it; nothing decoded it, so the screen could not have said.
    let royaltyBasis: String?

    let royaltyTotal: Double?
    let salesTotal: Int?

    var id: String { asin }

    /// True when the figures describe the design's whole life, not 30 days.
    var isLifetimeBasis: Bool { royaltyBasis == "lifetime" }

    /// What to draw in the royalty column: the ranking figure, falling back to
    /// the 30-day number for an older engine that sent no `royalty`.
    var royaltyShown: Double { royalty ?? royaltyLast30 }

    /// What to draw in the sales column, matching `royaltyShown`'s window.
    var salesShown: Int {
        isLifetimeBasis ? (salesTotal ?? salesLast30) : salesLast30
    }

    /// Column heading suffix, so the number is never read against the wrong
    /// window. Nil when the basis is unknown (an older engine).
    var basisLabel: String? {
        switch royaltyBasis {
        case "lifetime": return "all time"
        case "last30": return "30 days"
        default: return nil
        }
    }
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
    /// Days INSIDE this window with no banked history. `partial` used to mean
    /// only "the history starts later than asked for", so a window whose start
    /// was covered but which was missing days in the middle came out marked
    /// exact — 1 to 22 August with the 10th absent dropped that day's spend and
    /// sales from the total and said nothing.
    let daysMissing: Int?
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
    /// How many calendar months of this window came from the imported Ads
    /// console export rather than from banked daily history.
    let monthsImported: Int?
    /// Where this row's figures came from, in the engine's words — "banked
    /// daily + imported monthly", or the imported export alone.
    let source: String?
    /// The engine's own sentence saying the profit figure covers LESS of this
    /// window than spend, sales and ACOS do. Present only when it does.
    let profitNote: String?

    var id: String { key }

    /// True when profit covers a shorter window than the rest of the row.
    ///
    /// A period can be extended backwards with months imported from the Ads
    /// console. Spend, sales and ACOS then cover the whole window. Profit
    /// cannot: royalty is per design, and the imported months carry no
    /// per-design economics, so the engine models profit over the daily-banked
    /// portion alone. It says so in `profitNote`, and the engine deliberately
    /// leaves `partial` false on these rows, because the window is NOT partial
    /// for the three figures that do cover it.
    ///
    /// Both halves of that were dropped. The Dashboard's Year to date row read
    /// ad spend for 2026-01→2026-08-21 beside an estimated profit covering
    /// 2026-04-01 onward — the 143 banked days. Three months of spend had no
    /// profit beside them and nothing said so. The reply's All time row is
    /// worse still: five years of spend against that same profit figure,
    /// because both cover the same banked days. It is saved only by
    /// `hiddenFromDashboard`, which is not a guard — it is a layout choice that
    /// could be reversed by anyone at any time.
    var profitWindowIsShorter: Bool { profitNote != nil }

    /// What the profit card covers, said on the card itself.
    ///
    /// Not a tooltip: a caveat only a mouse can find is one a reader can miss,
    /// and `StatCard` combines its children for VoiceOver, so a subtitle is
    /// spoken while a `.help` string is not.
    var profitSubtitle: String {
        guard profitWindowIsShorter else { return "modeled royalty" }
        guard let days = daysBanked else { return "not estimated for imported months" }
        return days == 1 ? "modeled · 1 banked day only"
                         : "modeled · \(days) banked days only"
    }

    /// What the spend, sales and ACOS cards cover.
    ///
    /// Those three DO include the imported months, so the banked-day count on
    /// its own understates them — "143 days" sat under five years of spend.
    var spanSubtitle: String? {
        switch (daysBanked, monthsImported) {
        case let (days?, months?):
            return "\(days) \(days == 1 ? "day" : "days") + "
                 + "\(months) \(months == 1 ? "month" : "months") imported"
        case let (nil, months?):
            return "\(months) \(months == 1 ? "month" : "months") imported"
        case let (days?, nil):
            return days == 1 ? "1 day" : "\(days) days"
        default:
            return nil
        }
    }
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
    /// Share of ALL spend assignable to a single design (fraction). The rest is
    /// multi-ASIN cohort spend, reported in the fields below and excluded from profit.
    let coveragePct: Double?
    let modeledRoyaltyN: Int?
    let unattributedCohortSpend: Double?
    let unattributedCohortOrders: Int?
    let unattributedCohortSales: Double?
    let unattributedCohortGroups: Int?
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
    /// What the year-to-date figure does NOT cover.
    ///
    /// `appctl._ytd_totals` has computed both of these all along and `overview`
    /// dropped them, so this table printed UK, DE, FR, ES and IT — all of which
    /// only began advertising 2026-06-24 — under a plain "YTD" heading, and
    /// USKDP's single month of August the same way. The wire keys are
    /// `ytd_partial` and `ytd_first_month`, matching ytd_spend / ytd_sales /
    /// ytd_supplemented / ytd_basis in the same row.
    let ytdPartial: Bool?
    let ytdFirstMonth: String?
    /// True when imported console months extend the year back past the banked
    /// daily history. Only US and UK can be supplemented — one console export
    /// covers every marketplace and carries no country, so DE/FR/ES/IT share a
    /// single merged EUR series that cannot be split per market.
    let ytdSupplemented: Bool?
    let ytdBasis: String?

    var id: String { market }

    /// Prefer the overview's own truth fields. Older engines omit them, so use
    /// the market's banked daily coverage rather than pretending the year began
    /// in January.
    func ytdStartLabel(fallbackFirstDay: String?) -> String? {
        let currentYear = asOf.map { String($0.prefix(4)) }
        let fallbackMonth = fallbackFirstDay.flatMap { day -> String? in
            guard day.count >= 7 else { return nil }
            let month = String(day.prefix(7))
            guard let currentYear else { return month }
            return month.hasPrefix(currentYear) ? month : currentYear + "-01"
        }
        let month = ytdFirstMonth ?? fallbackMonth
            ?? (ytdPartial == false ? currentYear.map { $0 + "-01" } : nil)
        guard let month else { return nil }
        let inferredPartial = ytdPartial ?? !month.hasSuffix("-01")
        return (inferredPartial ? "Partial · " : "") + "since \(Format.monthName(month))"
    }
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
    /// Writes that LANDED, per action. Rejected attempts used to be counted here
    /// too, so a run where Amazon refused every bid change still announced them
    /// as work done.
    let actions: [String: Int]
    /// Writes Amazon rejected, per action. A run that failed silently reads
    /// exactly like a quiet one, so this has to reach the notification.
    let failed: [String: Int]?
    let failedTotal: Int?
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

    /// What the phase actually landed, printed on its last line and read back
    /// by `appctl promote`.
    ///
    /// The exit code could never answer this. Amazon refuses individual writes
    /// inside a batch and still answers 200 for the batch, and both phase4
    /// scripts exited 0 whatever it said — so a promotion where every source
    /// negative was refused arrived here as "keywords exit 0". Each refused
    /// term is still serving in the ad group it was meant to leave, competing
    /// with the replacement that just went live.
    ///
    /// `reported == false` means the phase printed no counts: unverified, not
    /// clean.
    var reported: Bool? = nil
    var requested: Int? = nil
    var created: Int? = nil
    var negativesRequested: Int? = nil
    var negativesLanded: Int? = nil
    var negativesRefused: Int? = nil
    /// phase4b's third state: a 2xx whose body the engine cannot read. Counted
    /// and named, deliberately not called a failure.
    var negativesUnconfirmed: Int? = nil
    var promoted: Int? = nil
    /// Set when Amazon refused every campaign, so nothing could be promoted.
    var aborted: String? = nil
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

/// How much true per-day ACCOUNT history one market has banked (daily_totals).
/// Filled by daily_metrics.py, a DIFFERENT nightly step from the perf pull, so
/// it goes stale on its own. It is what the dashboard's day grid, trend, month
/// and year-to-date rows read. `stale` is the engine's own verdict — the same
/// limit at which its rolling-window rules refuse to write.
struct DayHistoryCoverage: Codable, Hashable {
    let days: Int
    let first: String?
    let last: String?
    let behindDays: Int?
    let stale: Bool
    let reason: String?
}

struct MarketHealth: Codable, Identifiable, Hashable {
    let market: String
    let configured: Bool
    let hasData: Bool
    let latestData: String?       // WORST of the three perf tables (they drift independently)
    let lastPull: String?
    let lastWrite: String?
    let campaigns: Int?
    let campaignsEnabled: Int?    // the ENABLED subset — what a direct read counts
    let lastNote: PullNote?       // newest non-empty pull_log note (errors show here)
    let reportsPending: Int?      // report_jobs not yet downloaded (stalled reports)
    let staleTables: [String]?    // perf tables past the write-freeze threshold (>3d)

    /// The newest snapshot date of EACH perf table, keyed by table name.
    ///
    /// `latestData` above is the WORST of the three, which is the right number
    /// to gate on and the wrong number to diagnose from. The three tables are
    /// filled by three INDEPENDENT Amazon report jobs, so they fail
    /// independently and drift apart — that is the whole reason for the
    /// standing rule against dating one perf table from another's MAX(date),
    /// a mistake that recurred three times and once froze US bids, pauses and
    /// harvest for four nights while `campaign_perf` stayed green throughout.
    ///
    /// Without this the screen shows one date and no way to tell which job
    /// stopped. `staleTables` only names them once they are past the 4-day
    /// freeze threshold; a table two days behind is invisible until then, and
    /// two days behind is exactly when it is worth noticing.
    /// The VALUE is optional because a table that exists and holds no snapshot
    /// reports a null date, not a missing key. Typing this as `[String: String]`
    /// made a present-with-null map throw `valueNotFound`, and because `health`
    /// answers for every market in ONE reply, a single market whose report job
    /// had never succeeded blanked System Health for all seven. That is the
    /// state of a market on the day it is added. Found by review, 2026-08-23.
    var tables: [String: String?]? = nil

    /// Only the tables that have a date — what every date comparison wants.
    var datedTables: [String: String] { (tables ?? [:]).compactMapValues { $0 } }

    /// Tables that exist but have never been filled. Not "stale" and not
    /// "lagging": no report job has ever landed one, which is a different
    /// sentence and the one the operator needs on a market's first day.
    var undatedTables: [String] {
        (tables ?? [:]).filter { $0.value == nil }.keys.sorted()
    }

    /// Tables whose newest snapshot is older than the best of them, with how
    /// far behind each is. Empty when all three landed on the same day.
    ///
    /// Deliberately not a red flag: a one-day spread is an ordinary morning
    /// mid-pull. It is information, and `staleTables` remains the alarm.
    var laggingTables: [(name: String, date: String, daysBehind: Int)] {
        let dated = datedTables
        guard dated.count > 1 else { return [] }
        let newest = dated.values.max() ?? ""
        return dated
            .filter { $0.value < newest }
            .map { (name: $0.key, date: $0.value,
                    daysBehind: MarketHealth.dayGap(from: $0.value, to: newest)) }
            .sorted { $0.name < $1.name }
    }

    /// Whole days between two ISO dates, 0 when either cannot be read.
    static func dayGap(from: String, to: String) -> Int {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.timeZone = TimeZone(identifier: "UTC")
        guard let a = f.date(from: from), let b = f.date(from: to) else { return 0 }
        return max(0, Int(b.timeIntervalSince(a) / 86_400))
    }
    let targetDaily: TargetDailyCoverage?  // nil = no per-day history banked yet
    let dailyTotals: DayHistoryCoverage?   // nil = no banked days at all
    let bidCeiling: BidCeilingRow?         // nil = engine too old to report it
    let error: String?

    var id: String { market }

    /// True when this market writes bids with no cap on either bid surface.
    ///
    /// The daily-budget surface is deliberately not part of this: bids are
    /// written by the nightly rules every night, budgets almost never are. A
    /// market flagged for a blank budget cap would cry wolf on all seven rows.
    var bidsAreUncapped: Bool {
        guard let c = bidCeiling else { return false }   // unknown ≠ uncapped
        return c.target == nil && c.keyword == nil
    }

    /// ONE rule for "the nightly pull is behind", because two screens read it.
    /// The nightly re-pulls every market about every 24h (US ~10:15 through
    /// IT ~14:41), so 30h clears the normal stagger and still catches a night
    /// that never ran. Errors owned this rule alone once: it raised six
    /// pull-behind errors while System Health showed every market "Clear",
    /// because System Health only looked at the perf tables' own age.
    static let pullBehindAfterHours: Double = 30

    func pullIsBehind(now: Date = Date()) -> Bool {
        guard let hours = pullHoursAgo(now: now) else { return false }
        return Double(hours) > MarketHealth.pullBehindAfterHours
    }

    /// Whole hours since the last pull; nil when there is no usable timestamp.
    func pullHoursAgo(now: Date = Date()) -> Int? {
        guard let pulledAt = Format.dateTime(lastPull) else { return nil }
        return max(0, Int(now.timeIntervalSince(pulledAt) / 3_600))
    }
}

/// One phase's wall time from the nightly run.
struct RunStepTiming: Codable, Hashable, Identifiable {
    let market: String
    let step: String
    let seconds: Int

    var id: String { "\(market)|\(step)" }

    /// "41m", "2h 43m", "18s" — a duration read at a glance rather than parsed.
    var readable: String {
        if seconds >= 3_600 {
            let h = seconds / 3_600, m = (seconds % 3_600) / 60
            return m == 0 ? "\(h)h" : "\(h)h \(m)m"
        }
        if seconds >= 60 { return "\(seconds / 60)m" }
        return "\(seconds)s"
    }
}

/// One failed phase from the nightly run (run_scheduled.sh step tracker).
struct RunStepFailure: Codable, Hashable {
    let market: String
    let step: String
    let exit: Int
}

/// One market that applied nothing, and why. Not a failure — nothing crashed.
struct RunMarketGate: Codable, Hashable {
    let market: String
    let reason: String
}

/// outputs/last_run_status.json — the nightly's machine-readable outcome.
/// With Discord digests off this is how a crashed phase reaches the operator.
struct LastRunStatus: Codable, Hashable {
    let started: String?
    let finished: String?
    let ok: Bool
    let failures: [RunStepFailure]

    /// Which markets the run actually covered, from the step tracker.
    ///
    /// The nightly discovers its market list at start-up. When that discovery
    /// returns a short list the loop still finishes every step it ran, so
    /// `ok` stays true and `failures` stays empty — and the banner said "all
    /// steps OK" for five nights while only US was being advertised. The list
    /// is the only evidence of that, so the app decodes it and compares.
    let markets: [String]?

    /// Every step's wall time, slowest first.
    ///
    /// The nightly takes hours — 2h43m on 2026-08-23 — and the only two numbers
    /// recorded were the moment it started and the moment it finished. Nothing
    /// said which phase owned the time, so a phase that doubled read as a
    /// busier night, and no optimisation could be checked afterwards.
    var steps: [RunStepTiming]? = nil

    /// The sum of every step, which is less than finish − start: the gaps are
    /// what the script does between phases.
    var totalStepSeconds: Int? = nil

    /// Markets that ran their reads and applied NOTHING, with the reason.
    ///
    /// A closed economics gate skips every auto-apply stage for that market —
    /// negatives, pauses, both harvest promoters, bids, both builders, seasonal
    /// and the DSL rules. Nothing crashes, so `ok` stays true and `failures`
    /// stays empty, and the only place that said a whole market did no
    /// automation all night was a line in a 4 MB log file.
    var gated: [RunMarketGate]? = nil

    /// The one line worth putting on a banner: what owned the night.
    var slowestStep: RunStepTiming? { steps?.first }

    /// Total run wall time, when both ends are readable.
    var wallSeconds: Int? {
        guard let a = Format.dateTime(started), let b = Format.dateTime(finished)
        else { return nil }
        return max(0, Int(b.timeIntervalSince(a)))
    }

    /// Configured markets the run did NOT cover, in the health table's order.
    ///
    /// Empty when the run covered everything, when the engine is too old to
    /// report a market list (nil), or when the run somehow covered MORE than is
    /// configured — none of those is evidence of a silent skip, and a false
    /// amber banner every morning would train the operator to ignore it.
    func skippedMarkets(configured: [String]) -> [String] {
        guard let ran = markets, !ran.isEmpty else { return [] }
        let covered = Set(ran)
        return configured.filter { !covered.contains($0) }
    }
}

// MARK: - Marketing Stream: today

/// appctl `stream-today` — the first endpoint in the app that can answer
/// "what has today cost me so far".
///
/// Everything else on the Dashboard reads BANKED REPORT data, which is a day
/// behind by design. This reads Marketing Stream, which is about an hour behind
/// the hour it describes. The two must never be added together or compared as
/// like for like, which is why this has its own section with its own label.
///
/// `supported` is false, with a `note`, when Stream is not set up or nothing has
/// been banked for the market. That is deliberately different from a day of
/// zeroes: "no data" and "no spend" are different answers and only one of them
/// means stop worrying.
struct StreamTodayResponse: Codable, Hashable {
    let market: String
    let supported: Bool
    let note: String?
    let currency: String?
    let day: String?
    let isToday: Bool?
    /// The marketplace's UTC offset, taken off the message itself. The day
    /// boundary is Amazon's, not the Mac's.
    let accountOffset: String?
    let asOf: String?
    let hoursDelivered: Int?
    let latestHour: String?
    let coverage: StreamCoverage?
    let totals: StreamTodayTotals?
    let hours: [StreamTodayHour]?
    let placements: [StreamPlacement]?
    let campaigns: [StreamTodayCampaign]?
    /// The TRUE number of campaigns that served, which `campaigns` may cap.
    /// A capped list reads exactly like a complete one, so the count is what
    /// makes the difference visible.
    let campaignCount: Int?
    let campaignsTruncated: Bool?
    /// Advertisers whose market could not be resolved.
    ///
    /// A day's rows are scoped to the advertisers KNOWN to belong to this
    /// market, so an unresolved advertiser's spend and impressions are left out
    /// of every total, hour, placement and campaign on this panel. Nothing else
    /// in the reply changes shape when that happens: the totals still add up,
    /// the hours still add up, and the day simply reads low. Merch US and KDP
    /// US both advertise on the same marketplace, so this is not hypothetical.
    let unresolvedAdvertisers: [StreamAdvertiser]?

    /// sp-traffic rows that carried no `idempotency_id`.
    ///
    /// Those rows are DELTAS, so many legitimately share one hour, ad, keyword
    /// and placement — dedupe therefore keys on the id alone, and a row without
    /// one is KEPT rather than collapsed, because collapsing on shape would
    /// throw away most of an hour of real traffic. The cost of keeping it is
    /// that a repeated delivery is counted twice, so a non-zero value here
    /// means this day may be counted HIGH.
    ///
    /// It has been 0 on every day since the subscription opened. It is
    /// rendered anyway, because the day it is not is the day the panel is
    /// wrong and nothing else on the screen would say so: `stream-verify`
    /// only judges SETTLED days, and this panel's whole job is the day in
    /// progress.
    var unkeyedMessages: Int? = nil

    /// Nil when the day is clean; a sentence when it may be overcounted.
    var unkeyedWarning: String? {
        guard let n = unkeyedMessages, n > 0 else { return nil }
        return "\(n) message\(n == 1 ? "" : "s") arrived without an id, so this "
             + "day may be counted high. Tomorrow's check compares it against "
             + "the banked report."
    }

    let conversions: StreamConversions?
}

/// One Stream advertiser and how its market was decided.
///
/// `market` is nil when nothing claimed it, or when two markets did; `reason`
/// carries the engine's explanation. The engine never guesses here, because a
/// wrong guess merges two separate advertisers into one number.
struct StreamAdvertiser: Codable, Hashable, Identifiable {
    let advertiserId: String
    let market: String?
    let matched: Int?
    let sampled: Int?
    let learnedAt: String?
    let reason: String?

    var id: String { advertiserId }
}

/// Which of the day's hours actually arrived.
///
/// Stream never resends, so a missing hour stays missing. A total summed over a
/// day with holes is an UNDERCOUNT, and nothing else on screen would say so.
struct StreamCoverage: Codable, Hashable {
    let deliveredHours: Int
    let expectedHours: Int
    let missingHours: [Int]
    /// Hours that ARRIVED but cannot be whole, because they had already begun
    /// before Stream was switched on. Amazon sends a short catch-up when a
    /// subscription is created and promises nothing about how far it reaches.
    /// Drawing these as ordinary bars is what made the first day read as a
    /// collapse in spend rather than as a pipe that had just been turned on.
    let partialHours: [Int]?
    /// Queues whose last drain could not empty them, as "realm/dataset".
    ///
    /// Every other count here is about hours that were BANKED. Messages still
    /// sitting in SQS were never banked, and they belong to hours that already
    /// read as delivered — so the day could say complete while part of its
    /// traffic was queued at Amazon and the backlog was growing. That is what
    /// the Dashboard showed on 2026-08-24 with 958 messages undrained, while
    /// System Health said exactly this two clicks away.
    let backlogPending: [String]?
    /// True only when the hours are whole AND the pipeline is caught up.
    let complete: Bool
    let note: String?

    /// True when the HOURS are what is wrong — a hole, an hour that began
    /// before Stream was switched on, or nothing delivered at all.
    ///
    /// A drain backlog is not an hours problem: those messages arrived at
    /// Amazon and were never read, and they belong to hours that already
    /// count as delivered. The two get their own lines on the panel, so this
    /// says which one the engine's `note` is about.
    var hoursAreIncomplete: Bool {
        deliveredHours == 0 || !missingHours.isEmpty || !(partialHours ?? []).isEmpty
    }
}

/// Traffic only. There is no `sales`, `orders` or `acos` here on purpose —
/// sp-traffic does not carry them, and a zero would invent a return on spend.
struct StreamTodayTotals: Codable, Hashable {
    let impressions: Int
    let clicks: Int
    let cost: Double
    let ctr: Double?
    let cpc: Double?
}

struct StreamTodayHour: Codable, Hashable, Identifiable {
    let hour: Int?
    let window: String
    let impressions: Int
    let clicks: Int
    let cost: Double

    var id: String { window }
    var label: String { hour.map { String(format: "%02d", $0) } ?? "—" }
}

/// Where the ad was shown. Nothing else in the engine has this — it arrives
/// only on Stream, and it is how "we almost never reach Top of Search" becomes
/// visible at all.
struct StreamPlacement: Codable, Hashable, Identifiable {
    let placement: String
    let impressions: Int
    let clicks: Int
    let cost: Double
    /// Share of the day's COST. Degenerate early in a day, when most placements
    /// have spent nothing at all.
    let share: Double
    /// Share of the day's IMPRESSIONS — the one that is always meaningful.
    let impressionShare: Double
    let ctr: Double?

    var id: String { placement }
}

struct StreamTodayCampaign: Codable, Hashable, Identifiable {
    let campaignId: String
    let campaign: String
    let impressions: Int
    let clicks: Int
    let cost: Double

    var id: String { campaignId }
}

/// Sales and orders live in the sp-conversion dataset, a separate subscription.
/// Until ANY of it arrives the today panel reports spend and traffic only, and
/// refuses to show sales, ACOS or conversion rate — a zero there would read as
/// "spent money, sold nothing" rather than "cannot see sales yet".
///
/// Once it does arrive, two things stay true and both are said on screen.
/// A conversion is dated to the hour of the CLICK, not the purchase, so this is
/// "sales attributed to today's ad interactions" and it only ever grows.
/// And ACOS is still withheld for a day in progress: the spend for an hour is
/// final about an hour later and its sales are not, so the ratio of the two is
/// always alarming and always wrong.
struct StreamConversions: Codable, Hashable {
    let available: Bool
    /// Conversion messages banked in total, across every day.
    let messages: Int
    /// How many of them belong to the day being shown.
    let rows: Int?
    /// Which attribution window the figures use. "30d" — the same one
    /// `phase0_pull` and `daily_metrics` read, so the two never disagree.
    let attribution: String?
    let sales: Double?
    let orders: Int?
    let units: Int?
    let note: String?
    /// Why there is no ACOS here. Present exactly when sales are.
    let acosWithheld: String?
}

/// One Marketing Stream dataset, as System Health sees it.
///
/// `state` is deliberately three-valued, because "no messages" has two very
/// different meanings. `waiting` = subscribed, nothing has ever arrived, which
/// is what sp-conversion looked like for its whole first day and is not a
/// fault. `quiet` = messages arrived before but not lately. `flowing` = fresh.
struct StreamDatasetHealth: Codable, Identifiable, Hashable {
    let dataset: String
    let realm: String?
    let messages: Int
    let firstWindow: String?
    let lastWindow: String?
    let lastReceived: String?
    let ageMinutes: Int?
    let state: String

    var id: String { "\(realm ?? "unknown")|\(dataset)" }

    var isWaiting: Bool { state == "waiting" }
}

struct StreamRealmDrainHealth: Codable, Hashable {
    let lastDrain: String?
    let ageMinutes: Int?
    let stale: Bool
}

/// Marketing Stream health, read from LOCAL state only — no AWS call is made to
/// draw this. `stream-status` is the command that talks to AWS.
///
/// The alarm that matters is `drainStale`, not an empty dataset: Stream sends
/// nothing for an hour in which nothing happened, but it also never resends, so
/// a drain that stopped is losing data the moment SQS retention expires.
struct StreamHealth: Codable, Hashable {
    let configured: Bool
    let queuesConfigured: Int?
    let database: Bool?
    let datasets: [StreamDatasetHealth]?
    let lastDrain: String?
    let drainAgeMinutes: Int?
    let drainStale: Bool?
    let drainByRealm: [String: StreamRealmDrainHealth]?
    let drainStaleRealms: [String]?
    /// Datasets whose LAST drain ran out of time with the queue still full.
    /// A recent drain and a big message count both read green in that state,
    /// so this is the only field that says the backlog is growing.
    let drainBacklog: [String]?
    /// The database failed its own `quick_check`. Hours already banked may be
    /// unreadable and Stream never resends, so this outranks every other line
    /// on the card. nil means the check could not run — which is "unknown",
    /// not "fine".
    let corrupt: Bool?
    let corruptDetail: String?
    let error: String?
}

struct HealthResponse: Codable {
    let killActive: Bool
    let approvalRequired: Bool?
    let lastRun: LastRunStatus?
    /// nil when the engine predates Marketing Stream, or when no queue is set up.
    let stream: StreamHealth?
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
    let currency: String?
    let catalog: CatalogCoverage?
    let econCoverage: EconCoverage?
}

/// How many advertised ad groups the economics gate can actually judge.
///
/// This is the number to act on, and it is NOT the catalogue's price coverage.
/// Only US standard tees resolve their break-even from the design's own list
/// price; every other product type, and every other market, is priced from the
/// type table and needs no per-design price at all. On 2026-08-22 the catalogue
/// had no list price for 19,185 advertised designs — and 18,001 of those were
/// hats, which were never affected. The figure worth showing was 182.
///
/// `transition` is a deliberate 30-day leniency after a price change, not a
/// gap, so it is reported and deliberately left out of `actionable`.
struct EconCoverage: Codable, Hashable {
    let total: Int?
    let ok: Int?
    let transition: Int?
    let unknownPrice: Int?
    let unmapped: Int?
    let cohort: Int?
    /// unknownPrice + unmapped, counted in AD GROUPS — what a fresh catalogue
    /// export would fix. One product can be advertised by several ad groups, so
    /// this is the larger of the two numbers: 200 ad groups over 177 products on
    /// 2026-08-22. Calling it "designs" overstated it by 23.
    let actionable: Int?
    /// The same set counted in PRODUCTS (distinct ASINs).
    let actionableAsins: Int?
    /// Of those products, the ones whose listing still exists — the ONLY number
    /// a fresh catalogue export can move.
    ///
    /// A MerchFlow "all products" export carries REMOVED listings, so a design
    /// with no price is often one that can never be priced again: it timed out,
    /// was deleted, or was locked. On 2026-08-22, 165 of 174 were in that state,
    /// and the warning was telling the operator to go and re-export for designs
    /// that are not for sale.
    let actionableLive: Int?
    let actionableRemoved: Int?
    /// Of the removed ones, those whose ad is still ENABLED — the only subset
    /// there is anything to do about. Suggesting a pause for an ad that is
    /// already paused reads exactly like a task that still needs doing.
    let actionableRemovedEnabled: Int?
    /// e.g. ["timed_out": 93, "deleted_content_creator": 41] — why they went.
    let removedStatuses: [String: Int]?
    /// Ad groups deliberately left out of every count above, because neither
    /// can serve: ARCHIVED is terminal (Amazon has no un-archive), and a stale
    /// row is one Amazon's live product-ad list no longer returns. Reported so
    /// the exclusion is visible rather than silent.
    let excludedArchived: Int?
    let excludedStaleRows: Int?
    /// What those ad groups spent over the trailing 30 days, in the market's
    /// currency. A count alone reads as bookkeeping. This is the number that
    /// says whether to care: nothing pauses or flags them, because no rule can
    /// decide, so no rule acts.
    let actionableSpend: Double?
}

/// How much of the ADVERTISED catalogue the price map covers.
///
/// The catalogue is a merge of Snap export chunks, each capped at 100k rows, so
/// a partial catalogue is an expected working state and not an error.
///
/// Read this as INVENTORY, not as impact. A missing list price only disables
/// economics for a US standard tee, because that is the one product priced per
/// design; hats, hoodies and every non-US market are priced from the type
/// table and are unaffected. On 2026-08-22 this said 65,151 of 84,328 priced,
/// which sounds like a quarter of the account was unmanaged — the number the
/// gate actually could not judge was 182. `EconCoverage` is that number, and it
/// is the one to raise an issue on.
struct CatalogCoverage: Codable, Hashable {
    let designsMapped: Int?
    let designsWanted: Int?
    let pricesOlderThanGate: Int?
    let newest: String?
    let oldestPriceDate: String?
    let files: [String]?

    /// Advertised designs the merged catalogue does not price.
    var unpriced: Int { max(0, (designsWanted ?? 0) - (designsMapped ?? 0)) }

    /// 0…1, or nil when the engine did not report both halves.
    var coverage: Double? {
        guard let wanted = designsWanted, wanted > 0, let mapped = designsMapped
        else { return nil }
        return Double(mapped) / Double(wanted)
    }
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
    /// True only when EVERY bid in the plan went through.
    let applied: Bool?
    let items: [ResetBidsItem]?

    /// How many bids actually moved, counted per target. A reset plan is many
    /// bids in one call, so `applied` alone cannot distinguish "one target was
    /// refused" from "nothing happened".
    var appliedCount: Int? = nil
    var rejectedCount: Int? = nil

    /// The reduction over the targets that actually moved. `totalReduction`
    /// describes the whole PLAN, so on a partial rejection the receipt claimed
    /// a saving that never happened — "Reset 3 bids (reduction 0.30)" printed
    /// immediately above "Amazon refused 1 of 3". Found by review, 2026-08-23.
    var appliedReduction: Double? = nil

    /// False when Amazon's reply could not be mapped onto the bids we sent — a
    /// transport failure, or an error it did not index. Nothing may be claimed
    /// about what it refused in that case.
    var outcomeConfirmed: Bool? = nil

    /// What the receipt should headline: what MOVED, falling back to the plan
    /// for an engine older than 2026-08-23 that does not send the applied
    /// figures (and for the preview, where nothing has moved yet).
    var shownCount: Int { appliedCount ?? count }
    var shownReduction: Double { appliedReduction ?? totalReduction }

    /// Nil when everything went through; a sentence when some of it did not.
    /// It names only what was REFUSED, because the headline beside it now
    /// states what was applied — saying both was how the two disagreed.
    var partialFailureNote: String? {
        if outcomeConfirmed == false {
            return "Amazon's reply could not be matched to the bids that were "
                 + "sent, so none are counted as applied. Check the account "
                 + "before running this again — some may have gone through."
        }
        guard let rejected = rejectedCount, rejected > 0 else { return nil }
        return "Amazon refused \(rejected) of \(count) — see the Audit trail for which."
    }
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
    /// The headline date: the OLDER of the two tables below, because the plan
    /// is no fresher than the oldest evidence behind it.
    let asOf: String?

    /// The snapshot the NEGATIVES were resolved from (`search_term_perf`), and
    /// the one the PAUSES were resolved from (`targeting_perf`).
    ///
    /// Two independent Amazon report jobs fill those tables, so they drift
    /// apart — 12 days of drift are on record in the US database. Both are sent
    /// back with the approved plan so the engine can check each half against
    /// its own table. Checking one half against the other's date refused every
    /// apply in one direction of the drift and waved the pauses through in the
    /// other. Optional: an older engine build omits them.
    let asOfSearchTerms: String?
    let asOfTargeting: String?

    let negatives: [ProposedNegative]
    let pauses: [ProposedPause]
}

struct NegativesApplyResponse: Codable {
    let market: String
    let negativesApplied: Int
    let pausesApplied: Int

    /// Items Amazon refused, counted PER ITEM.
    ///
    /// The Approval Queue sends every approved negative in one call and every
    /// approved pause in another, and Amazon rejects individual items inside a
    /// 207 routinely — a duplicate negative above all. Until 2026-08-23 one
    /// such rejection made the whole call report zero applied, so the operator
    /// was told nothing happened while the rest were live on the account.
    /// The counts are honest now, and these say what did not make it.
    var negativesRejected: Int? = nil
    var pausesRejected: Int? = nil

    /// False when Amazon's reply could not be mapped onto the pauses we sent.
    /// `pausesRejected` is nil then, because nobody knows.
    var pausesConfirmed: Bool? = nil

    /// Whether the engine could check the plan against the CURRENT snapshot.
    ///
    /// The approval queue is a screen that can be left open across a nightly
    /// pull, so the engine compares the snapshot the plan was approved against
    /// with the newest one and refuses if they differ. That check needs `as_of`
    /// in the payload, which this app sends — so this is false only for a
    /// client older than the field, and then the write went out on evidence
    /// nobody re-checked. Which is worth a sentence.
    var asOfChecked: Bool? = nil

    /// Nil when everything went through; a sentence when some of it did not.
    var partialFailureNote: String? {
        if asOfChecked == false {
            return "This was applied WITHOUT checking that the evidence is still "
                 + "current — the plan carried no snapshot date. If it had been "
                 + "on screen a while, check the Audit trail against today's "
                 + "numbers."
        }
        if pausesConfirmed == false {
            return "Amazon's reply could not be matched to the pauses that were "
                 + "sent, so none are counted as applied. Check the account "
                 + "before running this again — some may have gone through."
        }
        let n = negativesRejected ?? 0
        let p = pausesRejected ?? 0
        guard n > 0 || p > 0 else { return nil }
        var parts: [String] = []
        if n > 0 { parts.append("\(n) negative\(n == 1 ? "" : "s")") }
        if p > 0 { parts.append("\(p) pause\(p == 1 ? "" : "s")") }
        return "Amazon refused " + parts.joined(separator: " and ")
             + ". The rest went through — see the Audit trail for which."
    }
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

    /// The snapshot the plan was resolved against. Worth showing: the counts
    /// come from the accumulated rollup, not from a live read.
    let asOf: String?

    /// Every instance the selection resolves to, each saying whether it would
    /// be acted on. The engine deliberately KEEPS the ones it will skip rather
    /// than dropping them, so that a selection of 40 landing on 12 can explain
    /// itself — and the app decoded none of it, which threw that away.
    let instances: [EverywhereInstance]?

    /// Instances skipped because there is no target id to write to. These are
    /// NOT no-ops: nothing about them is already in the requested state, the
    /// app simply cannot address them. Counting them as "already at that
    /// state" is how a keyword the operator asked for goes quietly missing.
    ///
    /// This reads the engine's own `skip_reason`. It used to infer the answer
    /// from whether `target_id` came back — and the engine's `_everywhere_slim`
    /// never sent `target_id` at all, so EVERY skip landed here and
    /// `skippedAlreadyInState` was always zero. An ASIN pause acts on ad groups
    /// and has no target id by design, so the inference could not have worked
    /// even once it was sent. Found by review, 2026-08-23.
    var skippedUnaddressable: Int { countSkips(reason: "unaddressable") }

    /// Instances skipped because they are already paused or archived — the
    /// genuine no-ops.
    var skippedAlreadyInState: Int { countSkips(reason: "already_paused") }

    /// Skipped because the app has no mirrored state for the row, so it cannot
    /// say the write would be a no-op. Reported apart from the two above rather
    /// than folded into either: claiming "already paused" about a row nobody
    /// has looked at would be a guess.
    var skippedStateUnknown: Int { countSkips(reason: "state_unknown") }

    private func countSkips(reason: String) -> Int {
        (instances ?? []).filter { $0.skip == true && $0.skipReason == reason }.count
    }
}

/// One resolved instance of an "act everywhere" selection.
struct EverywhereInstance: Codable, Identifiable, Hashable {
    let key: String?
    let campaign: String?
    let campaignId: String?
    let adGroup: String?
    let adGroupId: String?
    let targetId: String?
    let asin: String?
    let state: String?
    let spend: Double?
    /// What this instance would actually get: `pause_ad_group`, `pause_target`,
    /// `set_bid` or `add_negative`. One selection can resolve to more than one
    /// of these, so the row rather than the request is what knows.
    let op: String?
    /// True when this instance would NOT be written. Several different reasons
    /// share this flag — `skipReason` is which one.
    let skip: Bool?
    /// Why it would not be written: `unaddressable`, `already_paused` or
    /// `state_unknown`. Nil on a row that would be written, and nil from an
    /// engine older than 2026-08-23, which is why every reader falls back to
    /// the plain `skippedNoop` total rather than to a wrong breakdown.
    let skipReason: String?

    var id: String { (targetId ?? adGroupId ?? "") + "|" + (key ?? "") }
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
    let conflictsSkipped: Int?
    /// Approved changes whose RULE has since been edited, disabled or deleted.
    /// Applying one writes a value no rule currently proposes — queue
    /// setBid(0.50), edit the rule to 0.20, approve, and 0.50 went out. They are
    /// skipped and stay in the queue; re-collect to refresh them.
    let staleSkipped: Int?
    let results: [RulesApproveResult]?
    let note: String?
    let message: String?
    /// How many rows the rule matched. Only `rules-run` sends it; it is what
    /// separates "no row qualified" from "every proposed write was refused".
    let matched: Int?

    private enum CodingKeys: String, CodingKey {
        case applied, count, blocked, conflictsSkipped, staleSkipped, results, note, message
        case matched
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        if let value = try? values.decodeIfPresent(Bool.self, forKey: .applied) {
            applied = value
        } else if let value = try? values.decodeIfPresent(Int.self, forKey: .applied) {
            applied = value != 0
        } else {
            applied = nil
        }
        count = try values.decodeIfPresent(Int.self, forKey: .count)
        blocked = try values.decodeIfPresent(String.self, forKey: .blocked)
        conflictsSkipped = try values.decodeIfPresent(Int.self, forKey: .conflictsSkipped)
        staleSkipped = try values.decodeIfPresent(Int.self, forKey: .staleSkipped)
        results = try values.decodeIfPresent([RulesApproveResult].self, forKey: .results)
        note = try values.decodeIfPresent(String.self, forKey: .note)
        message = try values.decodeIfPresent(String.self, forKey: .message)
        matched = try values.decodeIfPresent(Int.self, forKey: .matched)
    }

    var notAppliedResults: [RulesApproveResult] {
        (results ?? []).filter { !$0.isApplied }
    }

    /// What a `rules-run --apply` actually did, in one sentence.
    ///
    /// "Applied 0 change(s)" is true of a rule that matched nothing AND of a
    /// run whose every write was blocked by the economics gate, refused on
    /// stale evidence or rejected by Amazon. Those need opposite reactions
    /// from the operator, and `count` alone cannot tell them apart — the
    /// executor reports a status per change, so read it (found 2026-08-24).
    var runSummary: String {
        if let blocked {
            let why = message ?? note
            return "Blocked (\(blocked)) — nothing was applied."
                 + (why.map { " \($0)" } ?? "")
        }
        let applied = count ?? 0
        let rows = results ?? []
        if rows.isEmpty {
            if matched == 0 { return "No rows matched — nothing to apply." }
            return "Applied \(applied) change(s)."
        }
        if notAppliedResults.isEmpty { return "Applied \(applied) change(s)." }
        return "Applied \(applied) of \(rows.count) change(s). "
             + (resultFailureNote ?? "")
    }

    var resultFailureNote: String? {
        guard !notAppliedResults.isEmpty else { return note ?? message }
        let details = notAppliedResults.map(\.failureDetail).joined(separator: " · ")
        return "\(notAppliedResults.count) rule write(s) were not applied: \(details)"
    }
}

struct RulesApproveResult: Codable, Hashable, Identifiable {
    let entityKind: String?
    let entityId: String?
    let label: String?
    let action: String?
    let note: String?
    let status: String
    let reason: String?
    let reasons: [String]?
    let message: String?
    let http: [Int?]?

    var id: String { "\(entityKind ?? "entity")|\(entityId ?? label ?? "unknown")|\(status)" }
    var isApplied: Bool { status == "applied" }
    var failureDetail: String {
        var details: [String] = [label ?? entityId ?? "Unknown row", status.replacingOccurrences(of: "_", with: " ")]
        if let action, !action.isEmpty { details.append(action) }
        if let note, !note.isEmpty { details.append(note) }
        if let reason, !reason.isEmpty { details.append(reason) }
        if let reasons, !reasons.isEmpty { details.append(reasons.joined(separator: ", ")) }
        if let message, !message.isEmpty { details.append(message) }
        if let codes = http?.compactMap({ $0 }), !codes.isEmpty {
            details.append("HTTP " + codes.map(String.init).joined(separator: ", "))
        }
        return details.joined(separator: " — ")
    }
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
    /// This bucket's OWN range. The account-wide one covers three currency
    /// series of different lengths, so it can say nothing about any of them.
    let firstMonth: String?
    let lastMonth: String?

    var id: String { "\(market)|\(currency)" }

    /// Months inside this bucket's own range that were never banked.
    /// Nil when the range is unknown (an older engine, or nothing banked).
    var monthGaps: Int? {
        guard months > 0, let first = firstMonth, let last = lastMonth,
              let a = Self.monthIndex(first), let b = Self.monthIndex(last),
              b >= a else { return nil }
        return max(0, (b - a + 1) - months)
    }

    static func monthIndex(_ yearMonth: String) -> Int? {
        let parts = yearMonth.split(separator: "-")
        guard parts.count == 2, let year = Int(parts[0]), let month = Int(parts[1])
        else { return nil }
        return year * 12 + (month - 1)
    }
}

/// What the console monthly-history importer has banked so far, account-wide.
///
/// `months` is the UNION of every currency series, so it belongs to no single
/// market. The Import screen printed it as this market's own and told DE it
/// had "60 months banked, continuous" while DE's table held nothing at all
/// (found 2026-08-24). Read `bucket(market:currency:)` instead.
struct HistoryCoverage: Codable, Hashable {
    let months: Int
    let firstMonth: String?
    let lastMonth: String?
    let byMarket: [HistoryCoverageMarket]?

    /// The banked series this market's history actually lives in, or nil when
    /// none does.
    ///
    /// The console export carries a Budget currency and nothing finer — its
    /// Country dimension comes back empty — so DE, FR, ES and IT share ONE
    /// euro series, banked as "EU". Match the market code first, then fall
    /// back to its currency.
    func bucket(market: String, currency: String?) -> HistoryCoverageMarket? {
        let rows = byMarket ?? []
        if let exact = rows.first(where: { $0.market == market }) { return exact }
        guard let currency, !currency.isEmpty else { return nil }
        return rows.first { $0.currency.caseInsensitiveCompare(currency) == .orderedSame }
    }

    /// True when the market reads its history out of a series it shares with
    /// other markets — every euro market does.
    func bucketIsShared(market: String, currency: String?) -> Bool {
        guard let found = bucket(market: market, currency: currency) else { return false }
        return found.market != market
    }
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

/// One US tee list price and what it earns. The price ladder is a table, not a
/// formula: Amazon's royalty does not scale linearly with price, so every
/// supported price carries its own confirmed number.
struct RoyaltyTeePrice: Codable, Identifiable, Hashable {
    let priceCents: Int
    let price: Double
    let royaltyCents: Int
    let royalty: Double
    let breakEven: Double
    let source: String            // "built-in" | "operator"
    let extrapolated: Bool        // we guessed this royalty from the confirmed range
    let growthPriced: Bool        // below the growth floor — acted on at floor economics
    let note: String?
    let updatedAt: String?

    var id: Int { priceCents }
}

/// One product type's economics. `price` is what the break-even implies, so
/// royalty and price together are the whole story: break-even is their ratio.
struct RoyaltyProductType: Codable, Identifiable, Hashable {
    let productType: String
    let label: String
    let royalty: Double?
    let price: Double?
    let breakEven: Double?
    let model: String?            // "A" (tee, CVR-first) or "B" (bid to break-even)
    let negThreshold: Double?
    let pauseThreshold: Double?
    let adGroups: Int?            // how many ad groups advertise this type here
    let source: String            // "built-in" | "operator" | "derived"
    let listings: Int?            // non-US only: how many listings the median came from
    let note: String?
    let updatedAt: String?

    var id: String { productType }
}

struct RoyaltyResponse: Codable {
    let market: String
    let currency: String?
    /// US royalties are hand-confirmed and editable. Every other market DERIVES
    /// them from the product export, so there is nothing to type in.
    let editable: Bool
    let basis: String
    let growthFloor: Double?
    let modelVersion: String?
    /// Problems in royalty_overrides.json. Non-empty closes the econ gate, so
    /// this must be shown, not swallowed.
    let errors: [String]
    let teePrices: [RoyaltyTeePrice]
    let productTypes: [RoyaltyProductType]
    let overrides: Int
}

struct RoyaltySaveResponse: Codable {
    let saved: Bool?
    let cleared: Bool?
    let productType: String?
    let priceCents: Int?
    let royaltyCents: Int?
    let royalty: Double?
    let price: Double?
    let breakEven: Double?
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

/// How many writes there REALLY are, counted in SQL over the whole log.
///
/// The screen derived these from the page it had loaded, so every card was
/// capped by the fetch limit: US read "500 writes this week" against a true
/// 10,635, on an account that logged 9,663 writes in one day that same week.
/// Catching a runaway rule is the entire point of this screen.
struct AuditTotals: Codable, Hashable {
    let today: Int
    let week: Int
    let noOpsToday: Int
    let undoable: Int
    let windowDays: Int?
}

struct AuditResponse: Codable {
    let market: String
    let count: Int
    let writes: [AuditWrite]
    /// nil on an older engine — the view falls back to counting the page and
    /// says so rather than printing the page size as a total.
    let totals: AuditTotals?
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
    let note: String?
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
    /// Every design routed here — the number the header prints.
    let count: Int
    /// How many of them this reply carries. The engine used to send the first
    /// 2000 with nothing saying so, and this screen builds what it was SENT:
    /// a route of 5,000 headlined 5,000, listed 2,000 and gave the rest no
    /// ads. Nil on an older engine, which is why `truncation` treats a missing
    /// value as "all of them" only when the list length agrees with `count`.
    let returned: Int?
    let truncated: Bool?
    let designs: [IntakeDesign]

    var id: String { route }

    /// How many designs this route is about to build with, and how many it
    /// cannot. Zero missing is the normal case and says nothing on screen.
    var missingFromPlan: Int { max(0, count - designs.count) }

    var isTruncated: Bool { truncated ?? (missingFromPlan > 0) }
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
    /// Absolute path the file now lives at. The app used to rebuild this from
    /// its engine-folder setting, which points at Ads/engine, so it guessed
    /// Ads/ and every follow-up call looked one folder too deep.
    let path: String?
    let movedToPod: Bool
    let removed: [String]
    let freedMb: Int
    /// "ok", or the engine's reason the US product map did not rebuild. The
    /// envelope is a SUCCESS either way — the file did move — so without this
    /// field a failed re-map read as a clean import while the economics stayed
    /// on the old prices and the gate was marked STALE.
    let usRemap: String?
    /// What the re-map actually said when it failed. Without it the operator
    /// gets "FAILED" and nothing to act on.
    let usRemapError: String?

    var remapFailed: Bool { (usRemap ?? "ok") != "ok" }
}

struct AdoptExportResponse: Codable {
    let export: AdoptedExport?
}

/// How many designs went into each cohort — the breakdown the result row shows.
struct IntakeCohortCount: Codable, Equatable, Sendable, Identifiable {
    let series: String
    let count: Int
    var id: String { series }
}

struct ImportApplyResponse: Codable, Equatable, Sendable {
    let market: String
    let designs: Int?
    let built: Int?
    let note: String?
    /// The REQUEST, per cohort — how many new designs were handed to the builders.
    let cohorts: [IntakeCohortCount]?
    let lottery: BuilderResult?
    let scavenger: BuilderResult?
    /// What the scavenger builder could NOT cover. `cohorts` above is the ask;
    /// on 2026-08-22 the screen printed it as the answer and read "Complete ·
    /// Drinkware 723" over zero drinkware ads.
    let coverage: ScavengerCoverage?
    let export: AdoptedExport?

    /// Why adopting the export FAILED, when it did.
    ///
    /// Adoption is what makes the new file the engine's economics source. When
    /// it raises, the engine sends this instead of `export` — and the envelope
    /// is still a success, because the campaigns really were built. `export`
    /// being nil already means "not adopted", but nil is also what an older
    /// engine and a `--no-adopt` run look like, so the screen read the failure
    /// as a normal quiet import.
    ///
    /// The cost is delayed and looks unrelated: the economics keep answering
    /// from the PREVIOUS export, which ages toward the 21-day freshness gate.
    /// When it crosses, every economics-driven write refuses, with nothing on
    /// screen connecting that to an import days earlier that said it worked.
    /// Same shape as `AdoptedExport.usRemap`, one step further out.
    let exportError: String?

    enum BuilderOutcome: Equatable, Sendable {
        case success
        case partialFailure
        case failure
    }

    var builderFailures: [String] {
        var failures: [String] = []
        if let code = lottery?.code, code != 0 { failures.append("Lottery exited \(code)") }
        if let code = scavenger?.code, code != 0 { failures.append("Scavenger exited \(code)") }
        return failures
    }

    var builderOutcome: BuilderOutcome {
        let builders = [lottery, scavenger].compactMap { $0 }
        let failures = builders.filter { $0.code != 0 }.count
        if failures == 0 { return .success }
        return failures == builders.count ? .failure : .partialFailure
    }

    var builderFailureSummary: String? {
        builderFailures.isEmpty ? nil : builderFailures.joined(separator: " · ")
    }
}

/// The scavenger builder's own account of what it missed.
struct ScavengerCoverage: Codable, Equatable, Sendable {
    /// false when the builder wrote no report at all — unverified, not clean.
    let available: Bool?
    let note: String?
    /// ASINs the build was scoped to (nil for an unscoped nightly build).
    let scoped: Int?
    let planned: Int?
    /// Scoped ASINs no cohort claimed. These were asked for and not built.
    let unplanned: Int?
    /// Campaigns that took new ads while PAUSED — created, and unable to serve.
    let pausedCampaigns: [String]?
    /// Product ads Amazon REFUSED. Submitted, turned down, and re-submitted the
    /// next night because a refused ad never joins Amazon's live product-ad
    /// list, which is what the builder computes "new" against. About 873 a
    /// night across six markets went unnamed from 2026-06-25 to 2026-08-25:
    /// `added` was the only figure recorded, and a night that added nothing
    /// read exactly like a night with nothing to add.
    let refused: Int?
    /// Hardgood designs a cohort WANTED and cannot advertise at all: no ad-safe
    /// ASIN in the export.
    ///
    /// A hat, mug, tumbler or water bottle is only advertisable through the
    /// export's ad-safe ASIN; its retail ASIN returns AD_INELIGIBLE every time.
    /// The builder used to submit the retail ASIN anyway, so these arrived as
    /// `refused` and read like a transient Amazon problem. They are not:
    /// several hundred US designs are advertised NOWHERE, and only a fresh
    /// export with that column populated recovers them. How many is an account
    /// measurement, so this field reports it rather than a comment claiming it.
    /// Skipping them is right, and reporting the skip
    /// is what stops the fix from hiding the loss it fixed.
    let noAdSafe: Int?
    /// The same count broken out by series. Kept beside the total because a
    /// series skipped down to nothing never reaches `series` at all.
    let noAdSafeSeries: [String: Int]?
    /// Set when the build died part-way: the write cap refusing a batch, or the
    /// bundle being replaced under a running nightly. The rows below then cover
    /// only the campaigns that finished, so the report is not this run's whole
    /// account of itself.
    let stopped: String?
    let series: [ScavengerCoverageSeries]?

    /// Nil when there is nothing to warn about. A sentence otherwise.
    var warning: String? {
        if available == false { return note ?? "the build wrote no coverage report" }
        var parts: [String] = []
        if let stopped, !stopped.isEmpty {
            parts.append("the build STOPPED part-way and is incomplete: \(stopped)")
        }
        if let unplanned, unplanned > 0 {
            let of = scoped.map { " of \(Format.count($0))" } ?? ""
            parts.append("\(Format.count(unplanned))\(of) requested ASINs matched no cohort and were NOT built")
        }
        if let pausedCampaigns, !pausedCampaigns.isEmpty {
            parts.append("ads were added to PAUSED campaigns and cannot serve: "
                         + pausedCampaigns.joined(separator: ", "))
        }
        if let refused, refused > 0 {
            parts.append("Amazon REFUSED \(Format.count(refused)) product ads "
                         + "(reasons in the run log)")
        }
        if let noAdSafe, noAdSafe > 0 {
            let where_ = (noAdSafeSeries ?? [:]).isEmpty ? "" :
                " (" + (noAdSafeSeries ?? [:]).sorted { $0.key < $1.key }
                    .map { "\($0.key) \(Format.count($0.value))" }
                    .joined(separator: ", ") + ")"
            parts.append("\(Format.count(noAdSafe)) hardgood designs have NO ad-safe ASIN"
                         + where_ + " and cannot be advertised — export that column to recover them")
        }
        for row in series ?? [] where (row.overCap ?? 0) > 0 {
            parts.append("\(row.series): \(Format.count(row.overCap ?? 0)) past the campaign cap")
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
}

struct ScavengerCoverageSeries: Codable, Equatable, Sendable, Identifiable {
    let series: String
    /// ASINs this series matched in the export.
    let matched: Int?
    /// ASINs that survived the shard cap and entered the plan.
    let planned: Int?
    /// Product ads Amazon actually accepted.
    let added: Int?
    /// Product ads Amazon refused in this series: submitted minus accepted.
    let refused: Int?
    /// Hardgoods in this series with no ad-safe ASIN, skipped before the plan.
    let noAdSafe: Int?
    /// matched − planned: the tail shard() dropped.
    let overCap: Int?
    let pausedCampaigns: [String]?
    var id: String { series }
}

// MARK: - live / actions

struct RunResponse: Codable {
    let ran: String
    let text: String
    let code: Int

    /// What a FULL nightly wrote about itself.
    ///
    /// `code` could not answer it. run_scheduled.sh sends everything it prints
    /// into outputs/scheduled_runs.log and its last statement is an echo, so the
    /// app got an empty output pane and exit 0 however many steps had failed.
    /// The failed steps and the gated markets are here. Nil for a single phase,
    /// and nil for a full run that wrote no status file — see `note`.
    var lastRun: LastRunStatus? = nil

    /// Set when a full run left no status of its own: unverified, never clean.
    var note: String? = nil
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

struct SeasonalApplyResponse: Decodable {
    let market: String
    let paused: Int
    let enabled: Int
    let errors: [SeasonalApplyError]

    var partialFailureNote: String? {
        guard !errors.isEmpty else { return nil }
        return "Seasonal partial failure: " + errors.map(\.summary).joined(separator: " · ")
    }
}

struct SeasonalApplyError: Decodable, Hashable {
    let phase: String
    let http: [Int?]
    let rejected: [String]

    private struct DynamicKey: CodingKey {
        let stringValue: String
        let intValue: Int? = nil
        init?(stringValue: String) { self.stringValue = stringValue }
        init?(intValue: Int) { return nil }
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: DynamicKey.self)
        rejected = try values.decodeIfPresent([String].self, forKey: DynamicKey(stringValue: "rejected")!) ?? []
        let phaseKey = values.allKeys.first { $0.stringValue != "rejected" }
        phase = phaseKey?.stringValue ?? "seasonal action"
        http = phaseKey.flatMap { try? values.decode([Int?].self, forKey: $0) } ?? []
    }

    var summary: String {
        var parts = [phase.replacingOccurrences(of: "_", with: " ")]
        let codes = http.compactMap { $0 }
        if !codes.isEmpty { parts.append("HTTP " + codes.map(String.init).joined(separator: ", ")) }
        if !rejected.isEmpty { parts.append("rejected " + rejected.joined(separator: ", ")) }
        return parts.joined(separator: " — ")
    }
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
