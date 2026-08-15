import XCTest
import AppKit
@testable import Merch_Ads

@MainActor
final class RuleSyntaxTests: XCTestCase {
    private func colorAt(_ storage: NSTextStorage, of needle: String, in source: String) -> NSColor? {
        let range = (source as NSString).range(of: needle)
        guard range.location != NSNotFound else { return nil }
        return storage.attribute(.foregroundColor, at: range.location, effectiveRange: nil) as? NSColor
    }

    func testCategoriesColored() {
        let src = "FOR EACH target:\n  IF target.profit < break_even:\n    target.pause()\n    target.note(\"hi\") # tail\n"
        let storage = NSTextStorage(string: src)
        RuleSyntax.highlight(storage)

        XCTAssertEqual(colorAt(storage, of: "FOR", in: src), .systemPink)      // keyword
        XCTAssertEqual(colorAt(storage, of: "profit", in: src), .systemGreen)  // economics field (the moat)
        XCTAssertEqual(colorAt(storage, of: "break_even", in: src), .systemGreen)
        XCTAssertEqual(colorAt(storage, of: "target", in: src), .systemTeal)   // entity
        XCTAssertEqual(colorAt(storage, of: "pause", in: src), .systemOrange)  // action verb
    }

    func testStringAndCommentAndNumber() {
        let src = "IF x > 45%:\n  y.note(\"why\") # note\n"
        let storage = NSTextStorage(string: src)
        RuleSyntax.highlight(storage)
        XCTAssertEqual(colorAt(storage, of: "45%", in: src), .systemPurple)         // number/percent
        XCTAssertEqual(colorAt(storage, of: "\"why\"", in: src), .systemRed)         // string
        XCTAssertEqual(colorAt(storage, of: "# note", in: src), .secondaryLabelColor) // comment
    }

    func testPlainIdentifierUsesDefaultTextColor() {
        let src = "LET foo = 1\n"
        let storage = NSTextStorage(string: src)
        RuleSyntax.highlight(storage)
        XCTAssertEqual(colorAt(storage, of: "foo", in: src), .textColor)   // no category → default
    }
}
