import Foundation

// Non-optional Comparable sort proxies for Table columns.
//
// SwiftUI's `KeyPathComparator` needs a non-optional Comparable value, and Bool
// isn't Comparable at all. So every column we want click-to-sort on maps to one
// of these proxies. A `String` proxy sorts A → Z first; a numeric proxy sorts
// high → low first (see `TableSortDirection`). Nil folds to a sentinel that sorts
// to the bottom under the default (descending) direction: -1 for values that are
// never negative, and -greatestFiniteMagnitude for values that legitimately can be.
//
// Campaign / AdGroup already have their proxies in CampaignBrowserView.swift, and
// AllTargetRow defines its own inline in Models.swift; this file adds the rest.

extension Campaign {
    var impressionsValue: Int { impressions ?? -1 }
    var budgetUseValue: Double { budgetUse ?? -1 }
}

extension AdGroup {
    var impressionsValue: Int { impressions ?? -1 }
}

extension AccumulatedAsinRow {
    var productTypeValue: String { productType ?? "" }
    var acosValue: Double { acos ?? -1 }
}

extension AccumulatedKeywordRow {
    var matchValue: String { matchType ?? "" }
    var acosValue: Double { acos ?? -1 }
}

extension AccumulatedBreakdownRow {
    var campaignValue: String { campaign ?? campaignId }
    var adGroupValue: String { adGroup ?? adGroupId }
    var stateValue: String { state ?? "" }
    var acosValue: Double { acos ?? -1 }
}

extension OverviewMarket {
    var acosValue: Double { acos ?? -1 }
    var cvrValue: Double { cvr ?? -1 }
    var ytdSpendValue: Double { ytdSpend ?? -1 }
    var ytdSalesValue: Double { ytdSales ?? -1 }
    var asOfValue: String { asOf ?? "" }
}

extension BidReportChange {
    var asinValue: String { asin ?? "" }
    var targetingValue: String { targeting ?? "" }
    var newValue: Double { new ?? -1 }
    var reasonValue: String { reason ?? "" }
}

extension HaloDesign {
    var adStartValue: String { adStart ?? "" }
    var trazWindowValue: Double { trazWindow ?? -.greatestFiniteMagnitude }
}

extension AsinAdGroup {
    var campaignValue: String { campaign ?? "" }
    var adGroupValue: String { adGroup ?? "" }
    var stateCachedValue: String { stateCached ?? "" }
    var bidValue: Double { bid ?? -1 }
    var acosValue: Double { acos ?? -1 }
    var cvrValue: Double { cvr ?? -1 }
}

extension TargetRow {
    var targetingValue: String { targeting ?? "" }
    var matchValue: String { matchType ?? "" }
    var currentBidValue: Double { currentBid ?? -1 }
    var ctrValue: Double { ctr ?? -1 }
    var cpcValue: Double { cpc ?? -1 }
    var acosValue: Double { acos ?? -1 }
    var cvrValue: Double { cvr ?? -1 }
}

extension SearchTermRow {
    var targetingValue: String { targeting ?? "" }
    var ctrValue: Double { ctr ?? -1 }
    var cpcValue: Double { cpc ?? -1 }
    var acosValue: Double { acos ?? -1 }
    var cvrValue: Double { cvr ?? -1 }
}

extension KillDesign {
    var asinValue: String { asin ?? "" }
    var stateValue: String { state ?? "" }
    var acosValue: Double { acos ?? -1 }
    var breakEvenValue: Double { breakEven ?? -1 }
    /// `cvr` became optional when the engine stopped reporting a measured 0%
    /// for a row nobody had clicked. -1 keeps the unknown rows at the bottom of
    /// an ascending sort, which is where "we cannot say" belongs on a screen
    /// about the worst converters. The kill list's own query demands 15 clicks,
    /// so in practice nothing here is ever unknown — the optional is what stops
    /// the whole reply failing to decode if that ever changes.
    var cvrValue: Double { cvr ?? -1 }
}

extension StaleDesign {
    var asinValue: String { asin ?? "" }
    var typeValue: String { type ?? "" }
    var nameValue: String { name ?? "" }
}

extension PruneKeyword {
    var asinValue: String { asin ?? "" }
    var cvrValue: Double { cvr ?? -1 }
    var acosValue: Double { acos ?? -1 }
}

extension HarvestWinner {
    var typeValue: String { type ?? "" }
    var acosValue: Double { acos ?? -1 }
    var cpcValue: Double { cpc ?? -1 }
    var lastSeenValue: String { lastSeen ?? "" }
    var promotedValue: Int { promoted ? 1 : 0 }
}

extension ProfitDesign {
    var asinValue: String { asin ?? "" }
    var typeValue: String { type ?? "" }
    var royaltyRoiValue: Double { royaltyRoi ?? -.greatestFiniteMagnitude }
}

extension ProposedPause {
    var asinValue: String { asin ?? "" }
    var nameValue: String { name ?? "" }
}

extension AuditWrite {
    var detailValue: String { detail ?? "" }
    var resultValue: String { result ?? "" }
}

extension SeasonTag {
    var activeValue: Int { active == true ? 1 : 0 }
    var productTypeValue: String { productType ?? "" }
}

extension SeasonInfo {
    var activeValue: Int { active ? 1 : 0 }
}

extension DemandSeed {
    var nicheValue: String { niche ?? "" }
    var productTypeValue: String { productType ?? "" }
    var acosValue: Double { acos ?? -1 }
    var cvrValue: Double { cvr ?? -1 }
}

extension ProvenSeller {
    var titleValue: String { title ?? "" }
    var productTypeValue: String { productType ?? "" }
}
