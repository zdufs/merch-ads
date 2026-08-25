import SwiftUI
import AppKit

/// A syntax-highlighting source editor for the rules DSL. Wraps NSTextView
/// (SwiftUI's TextEditor can't do stable per-token highlight-on-edit) and colors
/// tokens by category — economics fields get a distinct accent since they're the
/// whole point. Coloring only sets foreground attributes on the text storage, so
/// the insertion point never jumps. Theme-aware via semantic NSColors.
struct RuleSourceEditor: NSViewRepresentable {
    @Binding var text: String

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSTextView.scrollableTextView()
        guard let tv = scroll.documentView as? NSTextView else { return scroll }
        tv.delegate = context.coordinator
        tv.isRichText = false
        tv.isAutomaticQuoteSubstitutionEnabled = false
        tv.isAutomaticDashSubstitutionEnabled = false
        tv.isAutomaticSpellingCorrectionEnabled = false
        tv.isAutomaticTextReplacementEnabled = false
        tv.isContinuousSpellCheckingEnabled = false
        tv.isGrammarCheckingEnabled = false
        tv.usesFindBar = true
        tv.allowsUndo = true
        tv.font = RuleSyntax.font
        tv.textContainerInset = NSSize(width: 8, height: 8)
        tv.string = text
        RuleSyntax.highlight(tv.textStorage)
        return scroll
    }

    func updateNSView(_ scroll: NSScrollView, context: Context) {
        guard let tv = scroll.documentView as? NSTextView else { return }
        // Never rewrite the buffer mid-composition: replacing the string while
        // marked text is active drops the in-flight IME/dead-key input.
        guard !tv.hasMarkedText() else { return }
        if tv.string != text {                    // external change (e.g. load rule)
            let sel = tv.selectedRanges
            tv.string = text
            // Selecting into the *old* length raises NSRangeException when the new
            // rule is shorter — switching from a long rule to a short one with the
            // caret at the end used to crash.
            let limit = (text as NSString).length
            tv.selectedRanges = sel.compactMap { value in
                let r = value.rangeValue
                guard r.location <= limit else { return nil }
                return NSValue(range: NSRange(location: r.location,
                                              length: min(r.length, limit - r.location)))
            }.ifEmpty(NSValue(range: NSRange(location: limit, length: 0)))
            RuleSyntax.highlight(tv.textStorage)
        }
    }

    final class Coordinator: NSObject, NSTextViewDelegate {
        private let parent: RuleSourceEditor
        init(_ parent: RuleSourceEditor) { self.parent = parent }

        func textDidChange(_ notification: Notification) {
            guard let tv = notification.object as? NSTextView else { return }
            parent.text = tv.string
            guard !tv.hasMarkedText() else { return }   // don't re-attribute mid-composition
            RuleSyntax.highlight(tv.textStorage)        // recolor; leaves selection intact
        }
    }
}

private extension Array where Element == NSValue {
    /// A text view with no selection ranges misbehaves, so fall back to one.
    func ifEmpty(_ fallback: NSValue) -> [NSValue] { isEmpty ? [fallback] : self }
}

/// Tokenizer + palette for the rules DSL. One linear scan; category colors are
/// semantic NSColors so they adapt to light/dark automatically.
enum RuleSyntax {
    // NSFont isn't Sendable; every consumer is view code, so pin it there.
    @MainActor static let font = NSFont.monospacedSystemFont(ofSize: NSFont.systemFontSize, weight: .regular)

    private static let keywords: Set<String> = [
        "FOR", "EACH", "AS", "IN", "IF", "WHEN", "AND", "OR", "NOT", "LET",
        "CURRENT", "LIFETIME", "TRUE", "FALSE", "NONE",
        "CONTAINS", "STARTS", "ENDS", "WITH", "IS",
    ]
    private static let econFields: Set<String> = [
        "break_even", "royalty", "profit", "royalty_roi", "halo_est", "net_halo",
        "organic_per_day", "in_transition", "is_cohort", "econ_available",
    ]
    private static let entities: Set<String> = [
        "keyword", "target", "searchterm", "campaign", "adgroup", "product", "asin",
    ]
    private static let actions: Set<String> = [
        "pause", "enable", "setbid", "setbudget", "addnegative", "createkeyword",
        "note", "setstate", "setbiddingstrategy",
    ]
    private static let functions: Set<String> = [
        "min", "max", "clamp", "round", "floor", "ceil", "abs", "if", "lower",
        "upper", "length",
    ]

    private static func color(_ ns: NSColor) -> [NSAttributedString.Key: Any] {
        [.foregroundColor: ns]
    }

    // NSTextStorage is UI state — this always runs on the main actor.
    @MainActor
    static func highlight(_ storage: NSTextStorage?) {
        guard let storage else { return }
        let text = storage.string
        let ns = text as NSString
        let full = NSRange(location: 0, length: ns.length)
        storage.beginEditing()
        storage.setAttributes([.foregroundColor: NSColor.textColor, .font: font], range: full)

        var i = 0
        let n = ns.length
        while i < n {
            let c = ns.character(at: i)
            // A non-BMP character — every emoji — is stored as a surrogate
            // PAIR, and neither half is a valid scalar on its own. These three
            // conversions were force unwrapped, so pasting an emoji into a rule
            // trapped and killed the app. A surrogate starts no token, so
            // skipping it is also the right highlighting answer.
            guard let scalar = UnicodeScalar(c) else { i += 1; continue }

            if scalar == "#" {                                   // comment to EOL
                var j = i
                while j < n && ns.character(at: j) != 10 { j += 1 }
                storage.addAttributes(color(.secondaryLabelColor), range: NSRange(location: i, length: j - i))
                i = j
                continue
            }
            if scalar == "\"" {                                  // string literal
                var j = i + 1
                while j < n && ns.character(at: j) != 34 { j += 1 }
                let end = min(j + 1, n)
                storage.addAttributes(color(.systemRed), range: NSRange(location: i, length: end - i))
                i = end
                continue
            }
            if scalar == "$" || CharacterSet.decimalDigits.contains(scalar) {  // number/money/percent
                var j = i + 1
                while j < n {
                    guard let s = UnicodeScalar(ns.character(at: j)) else { break }
                    if CharacterSet.decimalDigits.contains(s) || s == "." || s == "%" { j += 1 } else { break }
                }
                storage.addAttributes(color(.systemPurple), range: NSRange(location: i, length: j - i))
                i = j
                continue
            }
            if scalar.properties.isAlphabetic || scalar == "_" {  // identifier / keyword
                var j = i
                while j < n {
                    guard let s = UnicodeScalar(ns.character(at: j)) else { break }
                    if s.properties.isAlphabetic || s == "_" || CharacterSet.decimalDigits.contains(s) { j += 1 } else { break }
                }
                let word = ns.substring(with: NSRange(location: i, length: j - i))
                let lower = word.lowercased()
                let attr: NSColor?
                if keywords.contains(word.uppercased()) { attr = .systemPink }
                else if econFields.contains(lower) { attr = .systemGreen }
                else if entities.contains(lower) { attr = .systemTeal }
                else if actions.contains(lower) { attr = .systemOrange }
                else if functions.contains(lower) { attr = .systemIndigo }
                else { attr = nil }
                if let attr { storage.addAttributes(color(attr), range: NSRange(location: i, length: j - i)) }
                i = j
                continue
            }
            i += 1
        }
        storage.endEditing()
    }
}
