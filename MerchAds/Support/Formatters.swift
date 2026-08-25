import Foundation

// Display formatting for engine numbers: acos/cvr arrive as fractions
// (0.1816 = 18.16%), money in the market currency.

enum Format {
    // NumberFormatter construction is expensive and these run per table cell —
    // cache one per (currency, fraction-digits). Both the cache and the cached
    // NumberFormatter itself are unsafe to touch from two threads at once, so
    // the lock is held across the whole lookup-and-format. A lock rather than
    // @MainActor: callers are SwiftUI views today, but nothing stops a future
    // exporter or report builder from formatting off the main thread, and this
    // way it stays correct without touching a single call site.
    private static let moneyLock = NSLock()
    private nonisolated(unsafe) static var moneyFormatters: [String: NumberFormatter] = [:]

    static func money(_ value: Double?, currency: String?) -> String {
        guard let value else { return "—" }
        let digits = value >= 1000 ? 0 : 2
        let key = "\(currency ?? "USD")|\(digits)"
        moneyLock.lock()
        defer { moneyLock.unlock() }
        let formatter: NumberFormatter
        if let cached = moneyFormatters[key] {
            formatter = cached
        } else {
            formatter = NumberFormatter()
            formatter.numberStyle = .currency
            formatter.currencyCode = currency ?? "USD"
            formatter.maximumFractionDigits = digits
            moneyFormatters[key] = formatter
        }
        return formatter.string(from: NSNumber(value: value)) ?? String(format: "%.2f", value)
    }

    static func percent(_ fraction: Double?, digits: Int = 1) -> String {
        guard let fraction else { return "—" }
        // Locale-aware, like Format.money two functions up — a German-locale
        // card used to render "1.234,00 €" next to a hardcoded "18.2%".
        return fraction.formatted(.percent.precision(.fractionLength(digits)))
    }

    static func count(_ value: Int?) -> String {
        guard let value else { return "—" }
        return value.formatted(.number.grouping(.automatic))
    }

    /// "2026-04" → "Apr 2026" (calendar-month labels from the daily history).
    static func monthName(_ yearMonth: String) -> String {
        let parts = yearMonth.split(separator: "-")
        guard parts.count == 2, let month = Int(parts[1]),
              (1...12).contains(month) else { return yearMonth }
        let names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        return "\(names[month - 1]) \(parts[0])"
    }

    // Fixed-format engine dates need the POSIX locale — a user calendar/locale
    // override would otherwise make parsing fail silently (staleness banners
    // would just never show).
    private static let engineDateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = .current
        return formatter
    }()

    /// Engine dates are "yyyy-MM-dd" strings (market-local pull dates).
    static func date(_ string: String?) -> Date? {
        guard let string else { return nil }
        return engineDateFormatter.date(from: string)
    }

    // Engine timestamps ("pulled_at" in pull_log) are "yyyy-MM-dd'T'HH:mm:ss",
    // written in local time with no zone suffix — parse them as .current.
    private static let engineTimestampFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = .current
        return formatter
    }()

    /// Engine timestamps like "2026-07-30T10:15:43" (e.g. health's last_pull).
    static func dateTime(_ string: String?) -> Date? {
        guard let string else { return nil }
        return engineTimestampFormatter.date(from: string)
    }

    /// Today/current dates in the engine's "yyyy-MM" / "yyyy-MM-dd" forms.
    static func yearMonth(of date: Date = Date()) -> String {
        String(engineDateFormatter.string(from: date).prefix(7))
    }

    static func dayString(of date: Date = Date()) -> String {
        engineDateFormatter.string(from: date)
    }

    /// A Date in the engine's timestamp form, "yyyy-MM-dd'T'HH:mm:ss" — the
    /// inverse of `dateTime(_:)`, for arguments the engine parses back.
    static func engineTimestamp(of date: Date = Date()) -> String {
        engineTimestampFormatter.string(from: date)
    }

    // EU display formatting (dd.MM.yyyy) — the engine stores ISO yyyy-MM-dd, but
    // every user-facing date is shown European-style.
    private static let euDateFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "dd.MM.yyyy"
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = .current
        return f
    }()
    private static let euShortFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "dd.MM."
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = .current
        return f
    }()

    /// Engine "yyyy-MM-dd" (or a Date) → EU "dd.MM.yyyy". Unparseable input is
    /// returned unchanged so nothing renders as a crash-y blank.
    static func euDate(_ string: String?) -> String {
        guard let string else { return "—" }
        guard let d = date(string) else { return string }
        return euDateFormatter.string(from: d)
    }
    static func euDate(_ date: Date) -> String { euDateFormatter.string(from: date) }

    /// Short EU date for axes/compact labels: "dd.MM." (e.g. "03.07.").
    static func euDateShort(_ date: Date) -> String { euShortFormatter.string(from: date) }

    private static let euDateTimeFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "dd.MM.yyyy HH:mm"
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = .current
        return f
    }()

    /// Engine timestamp "yyyy-MM-dd'T'HH:mm:ss" → EU "dd.MM.yyyy HH:mm". Accepts a
    /// bare "yyyy-MM-dd" too (falls back to euDate); unparseable input is returned raw.
    static func euDateTime(_ string: String?) -> String {
        guard let string else { return "—" }
        if let dt = dateTime(string) { return euDateTimeFormatter.string(from: dt) }
        if date(string) != nil { return euDate(string) }
        return string
    }
}
