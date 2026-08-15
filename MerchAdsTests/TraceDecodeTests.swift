import XCTest
@testable import Merch_Ads

final class TraceDecodeTests: XCTestCase {
    private func decoder() -> JSONDecoder {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }

    func testDecodeConditionTrace() throws {
        let json = """
        {"condition":"cvr < floor","actual":0.06,"threshold":0.08,"pass":true}
        """.data(using: .utf8)!
        let c = try decoder().decode(ConditionTrace.self, from: json)
        XCTAssertEqual(c.condition, "cvr < floor")
        XCTAssertEqual(c.actual, 0.06)
        XCTAssertEqual(c.threshold, 0.08)
        XCTAssertTrue(c.pass)
    }

    func testKillDesignCarriesOptionalTrace() throws {
        let json = """
        {"asin":"B0AAA","ad_group_id":"g1","type":"standard_tee","state":"ENABLED",
         "clicks":30,"orders":0,"cvr":0.0,"spend":5.0,"sales":0.0,"acos":null,"break_even":0.41,
         "trace":[{"condition":"cvr < floor","actual":0.0,"threshold":0.08,"pass":true}]}
        """.data(using: .utf8)!
        let d = try decoder().decode(KillDesign.self, from: json)
        XCTAssertEqual(d.trace?.count, 1)
        XCTAssertEqual(d.trace?.first?.pass, true)
    }

    func testTraceOptionalWhenAbsent() throws {
        let json = """
        {"asin":"B0AAA","ad_group_id":"g1","type":null,"state":null,
         "clicks":30,"orders":0,"cvr":0.0,"spend":5.0,"sales":0.0,"acos":null,"break_even":null}
        """.data(using: .utf8)!
        let d = try decoder().decode(KillDesign.self, from: json)
        XCTAssertNil(d.trace)
    }
}

final class AccumulatedDecodeTests: XCTestCase {
    private func decoder() -> JSONDecoder {
        let d = JSONDecoder(); d.keyDecodingStrategy = .convertFromSnakeCase; return d
    }

    func testAsinsResponse() throws {
        let json = """
        {"market":"US","as_of":"2026-07-31","count":1,
         "rows":[{"asin":"B0AAA","product_type":"standard_tee","campaigns":4,"ad_groups":4,
                  "impressions":100,"clicks":10,"spend":5.0,"orders":1,"sales":20.0,
                  "acos":0.25,"cvr":0.1}]}
        """.data(using: .utf8)!
        let r = try decoder().decode(AccumulatedAsinsResponse.self, from: json)
        XCTAssertEqual(r.rows.first?.campaigns, 4)
        XCTAssertEqual(r.rows.first?.acos, 0.25)
    }

    func testKeywordsAndBreakdownWithNullAcos() throws {
        let kw = """
        {"market":"US","as_of":"2026-07-31","count":1,
         "rows":[{"targeting":"close-match","match_type":"TARGETING_EXPRESSION_PREDEFINED",
                  "campaigns":35,"ad_groups":100,"impressions":9,"clicks":0,"spend":2.0,
                  "orders":0,"sales":0.0,"acos":null,"cvr":0.0}]}
        """.data(using: .utf8)!
        let r = try decoder().decode(AccumulatedKeywordsResponse.self, from: kw)
        XCTAssertNil(r.rows.first?.acos)
        XCTAssertEqual(r.rows.first?.campaigns, 35)

        let bd = """
        {"market":"US","asin":"B0AAA","as_of":"2026-07-31",
         "breakdown":[{"campaign_id":"c1","campaign":"Lotto 2","ad_group_id":"g1",
                       "ad_group":"G1","impressions":10,"clicks":1,"spend":0.5,"orders":0,
                       "sales":0.0,"acos":null,"cvr":0.0}]}
        """.data(using: .utf8)!
        let b = try decoder().decode(AccumulatedBreakdownResponse.self, from: bd)
        XCTAssertEqual(b.breakdown.first?.campaign, "Lotto 2")
        XCTAssertNil(b.breakdown.first?.matchType)
    }
}
