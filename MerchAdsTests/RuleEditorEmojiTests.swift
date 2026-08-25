import XCTest
import AppKit
@testable import Merch_Ads

/// Pasting an emoji into the rule editor crashed the app.
///
/// The highlighter walked the text by UTF-16 code unit and built each scalar
/// with `UnicodeScalar(c)!`. Every non-BMP character — every emoji — is stored
/// as a surrogate PAIR, and neither half is a valid scalar on its own, so
/// `UnicodeScalar(_: UInt16)` returns nil and the force unwrap trapped. Three
/// sites did it: the top of the loop, the number scan and the identifier scan.
///
/// A surrogate starts no token, so skipping it is also the right highlighting
/// answer — the rule around the emoji must still be coloured.
@MainActor
final class RuleEditorEmojiTests: XCTestCase {

    private func highlighted(_ text: String) -> NSTextStorage {
        let storage = NSTextStorage(string: text)
        RuleSyntax.highlight(storage)
        return storage
    }

    // Mutation tested on 2026-08-24 by restoring the force unwrap. The four
    // tests below CRASH the runner without the fix. These first two do NOT, and
    // that is worth saying rather than leaving them looking equally strong: the
    // comment and string branches jump straight to end-of-line and end-of-quote
    // without reading scalars, so an emoji inside one never reaches an unwrap.
    // They are kept because they pin that skipping behaviour — if either branch
    // is ever rewritten to walk scalars, they start doing real work.

    func testAnEmojiInACommentIsSkippedWithTheRestOfTheComment() {
        let storage = highlighted("# a note 🎯 about bids\nFOR EACH target:\n")
        XCTAssertEqual(storage.string.count, "# a note 🎯 about bids\nFOR EACH target:\n".count)
    }

    func testAnEmojiInAStringLiteralIsSkippedWithTheRestOfTheString() {
        // Invented words on purpose. What is under test is the emoji inside a
        // string literal, so the words around it only have to be words.
        _ = highlighted("IF target.targeting == \"florbin quazzle 💪\":\n")
    }

    // Everything below reaches a real unwrap and traps without the fix.

    func testAnEmojiPressedAgainstANumberDoesNotTrap() {
        // The number scan has its own unwrap, and a surrogate right after a
        // digit is what reaches it.
        _ = highlighted("IF target.clicks >= 12🎯:\n")
    }

    func testAnEmojiPressedAgainstAnIdentifierDoesNotTrap() {
        // As does the identifier scan.
        _ = highlighted("IF target🎯.clicks >= 12:\n")
    }

    func testAWholeRuleOfEmoji() {
        _ = highlighted("🎯🚀💪🏳️‍🌈👨‍👩‍👧‍👦\n")
    }

    func testTheRuleAroundAnEmojiIsStillHighlighted() {
        // A surrogate starts no token, so skipping it must not swallow the
        // keyword after it.
        let storage = highlighted("🎯 FOR EACH target:\n")
        let range = (storage.string as NSString).range(of: "FOR")
        XCTAssertNotEqual(NSNotFound, range.location)
        let colour = storage.attribute(.foregroundColor, at: range.location,
                                       effectiveRange: nil) as? NSColor
        XCTAssertEqual(NSColor.systemPink, colour,
                       "the keyword after an emoji lost its highlighting")
    }

    func testPlainTextStillHighlightsAsBefore() {
        let storage = highlighted("FOR EACH target:\n")
        let colour = storage.attribute(.foregroundColor, at: 0,
                                       effectiveRange: nil) as? NSColor
        XCTAssertEqual(NSColor.systemPink, colour)
    }
}
