import Foundation

/// A ready-to-import rule template for the local Rules Library (MerchDash's
/// "Community library", import-only). Economics-first starters; all validated.
///
/// **One job per template.** The library used to carry 13 and four clusters of
/// them did the same work: two search-term negators, two bid-down rules, three
/// campaign budget raisers, three target pausers. That is not variety — nothing
/// checks for conflicts between rules, so two enabled rules that both `setBid`
/// on the same target BOTH write and the last one wins. Where two templates
/// overlapped, the economics-aware one survived: the app's whole edge is that it
/// knows a design's own break-even, and a fixed "ACOS > 40%" throws that away.
///
/// Variations belong in the blurb (see the budget scaler's brand filter), not in
/// a second card that looks identical in the grid.
struct RuleTemplate: Identifiable, Hashable {
    let name: String
    let blurb: String
    let text: String
    /// The advertiser family this template belongs to — "merch" | "kdp". Merch
    /// tees and KDP books keep separate rule sets (the store rejects a name that
    /// crosses families), and the Library shows only the selected profile's
    /// templates. Every starter today is a tee rule, so this defaults to merch.
    var kind: String = "merch"
    var id: String { name }
}

enum RuleTemplates {
    static let all: [RuleTemplate] = [

        // MARK: - Stop the losses

        RuleTemplate(
            name: "Pause dead targets (no economics needed)",
            blurb: "The blunt companion to the rule above: pauses any ENABLED target with ≥15 clicks and 0 orders. Use this one where royalty is unavailable — cohorts, hardgoods, designs mid-price-change.",
            text: """
            FOR EACH target:
              IF target.clicks >= 15 AND target.orders = 0 AND target.state = "ENABLED":
                target.pause()
                target.note("{clicks} clicks, 0 orders — dead weight")
            """),

        RuleTemplate(
            name: "Spare proven winners, cut the rest",
            blurb: "Pauses a whole ad group spending over $8 with 0 sales — unless it's a proven design (lifetime ≥10) or a multi-ASIN cohort where the spend can't be pinned to one design.",
            text: """
            FOR EACH adGroup:
              IF adGroup.spend > $8 AND adGroup.orders = 0 AND lifetime_sales < 10 AND is_cohort = FALSE:
                adGroup.pause()
                adGroup.note("no sales, not a proven design")
            """),

        RuleTemplate(
            name: "Negate wasteful search terms",
            blurb: "Adds an exact negative for any search term with ≥8 clicks, 0 orders and over $2 spent. The spend gate matters: clicks alone negates cheap terms that never cost you anything. Negatives are permanent.",
            text: """
            FOR EACH searchTerm:
              IF searchTerm.clicks >= 8 AND searchTerm.orders = 0 AND searchTerm.spend > $2:
                searchTerm.addNegative(searchTerm.search_term, "exact")
                searchTerm.note("{clicks} clicks, {spend:money} wasted")
            """),

        // MARK: - Move the bids

        RuleTemplate(
            name: "Bid down over break-even (needs economics)",
            blurb: "Cuts a converting keyword's bid 15% when its ACOS is over that design's OWN break-even — not a fixed percentage. Floor $0.05, and it waits a week between moves on the same keyword.",
            text: """
            FOR EACH keyword:
              IF econ_available AND keyword.orders >= 1 AND keyword.acos > break_even AND keyword.days_since_bid_change > 7:
                keyword.setBid(MAX($0.05, keyword.bid * 0.85))
                keyword.note("ACOS {acos:percent} over break-even {break_even:percent}")
            """),

        RuleTemplate(
            name: "Nudge up starved keywords",
            blurb: "Bumps the bid a penny on ENABLED keywords serving 200–1,000 impressions with under 3 clicks — they aren't losing money, they're under-served. Capped at $0.40, one move a week. The 200 floor is doing real work: without it this matches almost the whole catalogue, because most keywords have barely served at all and a penny won't change that.",
            text: """
            FOR EACH keyword:
              IF keyword.impressions >= 200 AND keyword.impressions < 1000 AND keyword.clicks < 3 AND keyword.state = "ENABLED" AND keyword.days_since_bid_change > 7:
                keyword.setBid(MIN($0.40, keyword.bid + $0.01))
                keyword.note("starved: {impressions} impressions — nudging bid up")
            """),

        // MARK: - Move the budgets

        RuleTemplate(
            name: "Scale campaign budgets on ROAS",
            blurb: "Raises a campaign's daily budget 20% when ROAS is over 4 and the budget hasn't moved in a week. Clamped $1–$100. To scope it to one brand, add: AND campaign.name CONTAINS \"Retro Name Vault\"",
            text: """
            FOR EACH campaign:
              IF campaign.roas > 4 AND campaign.days_since_budget_change > 7:
                campaign.setBudget(CLAMP(campaign.budget * 1.2, $1, $100))
                campaign.note("ROAS {roas} — scaling budget")
            """),
    ]

    /// The starter templates for one advertiser family — "merch" | "kdp". The
    /// Library shows only these, so picking the KDP profile never surfaces the
    /// Merch tee rules. There are no KDP starters yet, so KDP returns an empty
    /// list (the Library shows its own empty state).
    static func all(for kind: String) -> [RuleTemplate] {
        all.filter { $0.kind == kind }
    }
}
