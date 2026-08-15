import XCTest
@testable import Merch_Ads

/// The library is import-only, so a bad template is not a crash — it is an
/// operator importing a rule that quietly does nothing, or one that fires on
/// the whole account. Both happened. These tests hold the line.
final class RuleTemplateTests: XCTestCase {

    func testNamesAreUniqueAndDescriptive() {
        let names = RuleTemplates.all.map(\.name)
        XCTAssertEqual(Set(names).count, names.count, "two templates share a name")
        for t in RuleTemplates.all {
            XCTAssertFalse(t.blurb.trimmingCharacters(in: .whitespaces).isEmpty,
                           "\(t.name) has no blurb")
            XCTAssertFalse(t.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                           "\(t.name) has no rule text")
        }
    }

    /// No two templates may make the same move, the same direction, behind the
    /// same gate. That is the duplication the library was consolidated to remove
    /// — and it is not cosmetic: nothing checks for conflicts between rules, so
    /// two enabled rules that both setBid on one target both write, and the last
    /// one wins.
    ///
    /// Direction and gate are what make a pair legitimate. Bidding a keyword
    /// down over break-even and bidding it up on royalty ROI are opposite moves.
    /// Pausing on negative profit and pausing on zero orders answer the same
    /// question for two different situations: the first goes silent whenever
    /// royalty can't be resolved, and the second is what covers that gap.
    func testNoTwoTemplatesMakeTheSameMove() {
        var seen: [String: String] = [:]
        for t in RuleTemplates.all {
            let gate = t.text.contains("econ_available") ? "economics" : "plain"
            for key in moveKeys(in: t.text) {
                let full = "\(key) [\(gate)]"
                if let other = seen[full] {
                    XCTFail("“\(t.name)” and “\(other)” both do \(full) — "
                            + "merge them or scope one of them")
                }
                seen[full] = t.name
            }
        }
    }

    /// Every bid or budget write must be bounded. An unbounded `setBid` is a
    /// blank cheque against the max-bid ceiling.
    func testEveryBidAndBudgetWriteIsClamped() {
        for t in RuleTemplates.all {
            let writes = t.text.contains("setBid(") || t.text.contains("setBudget(")
            guard writes else { continue }
            let clamped = t.text.contains("MIN(") || t.text.contains("MAX(")
                || t.text.contains("CLAMP(")
            XCTAssertTrue(clamped, "\(t.name) moves a bid or budget with no floor or ceiling")
        }
    }

    /// A rule that moves a bid on a repeating nightly schedule needs a cooldown,
    /// or it walks the same bid every single night.
    func testRepeatableBidMovesWaitBetweenChanges() {
        for t in RuleTemplates.all where t.text.contains("setBid(") {
            XCTAssertTrue(t.text.contains("days_since_bid_change"),
                          "\(t.name) moves a bid with no cooldown")
        }
        for t in RuleTemplates.all where t.text.contains("setBudget(") {
            XCTAssertTrue(t.text.contains("days_since_budget_change"),
                          "\(t.name) moves a budget with no cooldown")
        }
    }

    /// Templates that read economics must say so in the name, because they go
    /// silent whenever royalty can't be resolved (price transition, cohort) and
    /// "it does nothing" then looks like a bug rather than a fail-closed.
    func testEconomicsTemplatesAdvertiseThatTheyNeedEconomics() {
        for t in RuleTemplates.all where t.text.contains("econ_available") {
            XCTAssertTrue(t.name.lowercased().contains("economics"),
                          "\(t.name) is economics-gated but its name doesn't say so")
        }
    }

    /// Templates carry an advertiser family, and the Library scopes to it — the
    /// same separation the store enforces for saved rules. Every template today
    /// is a Merch tee rule, so KDP must surface none of them; picking the KDP
    /// profile in the Library used to still show the whole Merch catalogue.
    func testTemplatesAreScopedByAdvertiserFamily() {
        XCTAssertEqual(RuleTemplates.all(for: "merch").count, RuleTemplates.all.count,
                       "a template dropped out of the Merch library")
        XCTAssertTrue(RuleTemplates.all(for: "kdp").isEmpty,
                      "a Merch template is leaking into the KDP library")
        for t in RuleTemplates.all {
            XCTAssertEqual(t.kind, "merch", "\(t.name) has an unexpected advertiser family")
        }
    }

    /// "entity.verb up/down" for the mutating verbs only — `note` annotates and
    /// never writes. Direction comes from the arithmetic in the same statement:
    /// `* 0.85` is down, `* 1.10` and `+ $0.01` are up.
    private func moveKeys(in text: String) -> Set<String> {
        let verbs = ["pause", "enable", "setBid", "setBudget", "addNegative"]
        var keys = Set<String>()
        for line in text.split(separator: "\n") {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            for verb in verbs {
                guard let dot = trimmed.range(of: ".\(verb)(") else { continue }
                let entity = String(trimmed[trimmed.startIndex..<dot.lowerBound])
                // Skip argument references like `searchTerm.search_term`.
                guard !entity.contains("(") else { continue }
                keys.insert("\(entity).\(verb)\(direction(of: trimmed))")
            }
        }
        return keys
    }

    private func direction(of statement: String) -> String {
        guard statement.contains("setBid(") || statement.contains("setBudget(") else { return "" }
        if statement.contains("* 0.") { return " down" }
        if statement.contains("* 1.") || statement.contains("+ $") { return " up" }
        return " set"
    }
}
