import SwiftUI

/// "Today so far" — the Dashboard's only LIVE section.
///
/// Everything else on this screen reads banked report data, which is a day
/// behind because that is how Amazon's reports work. This reads Marketing
/// Stream, which is about an hour behind the hour it describes. The two bases
/// must never be read as like for like, so this section carries its own header
/// saying where the numbers come from and how fresh they are.
///
/// Three refusals are built in, and each one exists because the alternative
/// would be a confident wrong number:
///
/// 1. **No ACOS, no sales, no conversion rate** while the sp-conversion dataset
///    is empty. sp-traffic carries spend and clicks only. A zero for sales
///    would read as "spent money, sold nothing", which is a different and much
///    worse claim than "we cannot see sales yet".
/// 2. **A day with missing hours says so, loudly.** Stream never resends, so a
///    gap is permanent, and the totals under it are an undercount.
/// 3. **Not set up is not the same as no spend.** When Stream is unconfigured
///    the section explains itself instead of drawing a row of zeroes.
struct StreamTodayView: View {
    let response: StreamTodayResponse?
    let fallbackCurrency: String?

    private var currency: String? { response?.currency ?? fallbackCurrency }

    var body: some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.sm) {
            header
            if let response, response.supported {
                totalsRow(response)
                backlogNote(response)
                coverageNote(response)
                unresolvedNote(response)
                unkeyedNote(response)
                conversionsNote(response)
                if let placements = response.placements, !placements.isEmpty {
                    placementSection(placements)
                }
                if let hours = response.hours, !hours.isEmpty {
                    hourStrip(hours, coverage: response.coverage)
                }
            } else if let response {
                unavailable(response.note)
            }
        }
        .padding(Layout.Spacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        // Same card treatment as every other panel on this screen, so the live
        // section reads as one of the Dashboard's cards rather than a callout.
        .mdCard()
    }

    // MARK: - header

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: Layout.Spacing.sm) {
            Label("Today so far", systemImage: "antenna.radiowaves.left.and.right")
                .font(.headline)
                .foregroundStyle(Theme.Colors.textPrimary)
            Text(subtitle)
                .font(.caption)
                .foregroundStyle(Theme.Colors.muted)
            Spacer(minLength: 0)
        }
        .help(helpText)
    }

    /// The long answer, for whoever hovers. Says where the numbers come from,
    /// whose clock the hours are on, and why this panel must never be added to
    /// the rest of the Dashboard.
    private var helpText: String {
        var text = "Amazon Marketing Stream pushes hourly rows about an hour after "
            + "the hour they describe. Every other number on this screen comes from "
            + "the nightly report, which is a day behind. Do not add them together.\n\n"
        text += "Every hour here is the marketplace's own clock"
        if let offset = response?.accountOffset { text += " (UTC \(offset))" }
        text += ", not yours, because the marketplace is what decides when the "
            + "advertising day starts and ends."
        return text
    }

    /// Says the basis and the day, and — when the day is not today — says that
    /// too, so a back-dated look is never mistaken for live.
    private var subtitle: String {
        guard let response, response.supported else { return "Marketing Stream" }
        var parts = ["Marketing Stream"]
        if response.isToday == false, let day = response.day {
            parts.append("showing \(Format.euDate(day))")
        }
        if let latest = response.latestHour, let hour = hourLabel(latest) {
            var text = "through \(hour) Amazon time"
            if let mine = localHourLabel(latest) { text += " (\(mine) yours)" }
            parts.append(text)
        }
        return parts.joined(separator: " · ")
    }

    /// "08:00" out of "2026-08-21T08:00:00-07:00". Marketplace-local already, so
    /// there is no conversion here to get wrong.
    private func hourLabel(_ window: String) -> String? {
        guard window.count >= 16 else { return nil }
        let start = window.index(window.startIndex, offsetBy: 11)
        let end = window.index(window.startIndex, offsetBy: 16)
        return String(window[start..<end])
    }

    /// The SAME instant on the reader's own clock.
    ///
    /// Every hour in this panel is Amazon's, because the marketplace decides
    /// when a day starts and ends. That is correct and it is also confusing:
    /// an operator in Europe reads "through 09:00" at six in the evening and
    /// cannot tell whose morning it is. Printing the raw offset ("account time
    /// -07:00") is precise and answers nothing. So the header says both hours
    /// and names which is which.
    private func localHourLabel(_ window: String) -> String? {
        let parser = ISO8601DateFormatter()
        parser.formatOptions = [.withInternetDateTime]
        guard let instant = parser.date(from: window) else { return nil }
        let out = DateFormatter()
        out.locale = Locale(identifier: "en_US_POSIX")
        out.dateFormat = "HH:mm"
        return out.string(from: instant)
    }

    // MARK: - totals

    @ViewBuilder
    private func totalsRow(_ response: StreamTodayResponse) -> some View {
        let totals = response.totals
        let conversions = response.conversions
        HStack(alignment: .top, spacing: Layout.Spacing.xl) {
            metric("Spend", Format.money(totals?.cost, currency: currency), emphasised: true)
            // Sales appear only once the conversion dataset has delivered
            // something. No sales row at all is the honest state before that;
            // a zero would be a claim we cannot support.
            if let conversions, conversions.available {
                metric("Sales", Format.money(conversions.sales, currency: currency),
                       emphasised: true)
                metric("Orders", Format.count(conversions.orders))
            }
            metric("Impressions", Format.count(totals?.impressions))
            metric("Clicks", Format.count(totals?.clicks))
            metric("CTR", Format.percent(totals?.ctr, digits: 2))
            metric("CPC", Format.money(totals?.cpc, currency: currency))
            Spacer(minLength: 0)
        }
    }

    private func metric(_ label: String, _ value: String, emphasised: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label.uppercased())
                .font(.caption2)
                .foregroundStyle(Theme.Colors.muted)
            Text(value)
                .font(emphasised ? .title3.monospacedDigit() : .body.monospacedDigit())
                .fontWeight(emphasised ? .semibold : .regular)
                .foregroundStyle(Theme.Colors.textPrimary)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(label) \(value)")
    }

    // MARK: - the two honesty notes

    /// The drain is behind, so part of today is still sitting in the queue.
    ///
    /// Every other caveat on this panel is about hours that were BANKED.
    /// Messages still in SQS were never banked at all, and they belong to
    /// hours that already read as delivered — so the panel said the day was
    /// complete while 958 messages queued up and the backlog grew hourly
    /// (2026-08-24). System Health said exactly this, two clicks away; the
    /// screen showing the totals has to say it too. Same wording, because it
    /// is the same fault.
    @ViewBuilder
    private func backlogNote(_ response: StreamTodayResponse) -> some View {
        let queues = response.coverage?.backlogPending ?? []
        // Only when the hours themselves are whole. When they are not, the
        // engine's note already names the queue, and two lines saying one
        // thing read as two separate faults.
        if !queues.isEmpty, response.coverage?.hoursAreIncomplete == false {
            Label("The hourly drain did not empty \(queues.joined(separator: " and ")). "
                  + "Messages are arriving faster than it reads them, so these totals "
                  + "are an undercount and the backlog is growing.",
                  systemImage: "exclamationmark.triangle.fill")
                .font(.caption)
                .foregroundStyle(Theme.Colors.caution)
                .padding(Layout.Spacing.xs)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.Colors.caution.opacity(0.1),
                            in: RoundedRectangle(cornerRadius: Layout.Radius.small, style: .continuous))
        }
    }

    /// A permanent hole in the day. Amber, not red: the number is not wrong, it
    /// is incomplete, and the nightly report will still cover the day properly.
    ///
    /// A backlog with whole hours is drawn on its own line above, so this one
    /// stays quiet in that case rather than repeating it.
    @ViewBuilder
    private func coverageNote(_ response: StreamTodayResponse) -> some View {
        if let coverage = response.coverage, !coverage.complete, let note = coverage.note,
           coverage.hoursAreIncomplete {
            Label(note, systemImage: "exclamationmark.triangle.fill")
                .font(.caption)
                .foregroundStyle(Theme.Colors.caution)
                .padding(Layout.Spacing.xs)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.Colors.caution.opacity(0.1),
                            in: RoundedRectangle(cornerRadius: Layout.Radius.small, style: .continuous))
        }
    }

    /// An advertiser whose market is unknown is left OUT of this whole panel.
    ///
    /// The rows behind every figure here are scoped to the advertisers known to
    /// belong to this market. So an unresolved one does not appear as a gap or
    /// an error — its spend and impressions are simply absent, the remaining
    /// numbers still add up, and the day reads quiet. That is the one Stream
    /// failure with no other symptom, which is why it is said out loud rather
    /// than left in the reply for nobody to read.
    @ViewBuilder
    private func unresolvedNote(_ response: StreamTodayResponse) -> some View {
        let unresolved = response.unresolvedAdvertisers ?? []
        if !unresolved.isEmpty {
            Label("\(unresolved.count) advertiser\(unresolved.count == 1 ? "" : "s") could not be matched to a market, so anything they spent is missing from these totals.",
                  systemImage: "questionmark.circle.fill")
                .font(.caption)
                .foregroundStyle(Theme.Colors.caution)
                .padding(Layout.Spacing.xs)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.Colors.caution.opacity(0.1),
                            in: RoundedRectangle(cornerRadius: Layout.Radius.small, style: .continuous))
                .help(unresolved.compactMap { $0.reason ?? $0.advertiserId }.joined(separator: "\n"))
        }
    }

    /// A day that may be counted HIGH, which is the opposite direction from
    /// every other caveat on this panel.
    ///
    /// sp-traffic rows are deltas, so dedupe keys on `idempotency_id` alone and
    /// a row without one is kept rather than collapsed — collapsing on shape
    /// would throw away most of an hour of real traffic. The price is that a
    /// redelivered row is counted twice. It has been zero on every day since
    /// the subscription opened; it is drawn anyway, because the day it is not
    /// is the day this panel is wrong, and `stream-verify` only judges days
    /// that have already settled.
    @ViewBuilder
    private func unkeyedNote(_ response: StreamTodayResponse) -> some View {
        if let warning = response.unkeyedWarning {
            Label(warning, systemImage: "plus.forwardslash.minus")
                .font(.caption)
                .foregroundStyle(Theme.Colors.caution)
                .padding(Layout.Spacing.xs)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.Colors.caution.opacity(0.1),
                            in: RoundedRectangle(cornerRadius: Layout.Radius.small, style: .continuous))
        }
    }

    /// Says what the sales figure is, or why there is none.
    ///
    /// Both states need a sentence. Before conversions arrive, the absence of a
    /// sales number needs explaining. After they arrive, the number needs a
    /// health warning: it is attributed to the CLICK hour, it lands hours or
    /// days late, and Amazon restates it — so it only ever grows, and it must
    /// not be divided into the spend beside it.
    @ViewBuilder
    private func conversionsNote(_ response: StreamTodayResponse) -> some View {
        if let conversions = response.conversions {
            VStack(alignment: .leading, spacing: 2) {
                if let note = conversions.note {
                    Label(note, systemImage: "info.circle")
                        .font(.caption)
                        .foregroundStyle(Theme.Colors.muted)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                if let withheld = conversions.acosWithheld {
                    Label(withheld, systemImage: "percent")
                        .font(.caption)
                        .foregroundStyle(Theme.Colors.muted)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .help("sp-conversion is a second subscription with its own queue. "
                  + "It reports on the 30-day attribution window, the same one the "
                  + "nightly report uses, so the two figures are comparable.")
        }
    }

    // MARK: - placement

    /// Where the ads were actually shown. This dimension exists NOWHERE else in
    /// the engine — the report pipeline never carried it — so it is the single
    /// most useful thing on the panel.
    ///
    /// The bar is IMPRESSION share, not cost share, because for most of a day
    /// most placements have spent nothing and a cost bar would be blank.
    /// How wide the bars are allowed to get. A share bar is read against its own
    /// label and number, not against the window.
    private static let barWidth: CGFloat = 360

    private func placementSection(_ placements: [StreamPlacement]) -> some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.xs) {
            Text("WHERE THE ADS SHOWED")
                .font(.caption2)
                .foregroundStyle(Theme.Colors.muted)
                .padding(.top, Layout.Spacing.xs)
            ForEach(placements) { placement in
                HStack(spacing: Layout.Spacing.sm) {
                    Text(placement.placement)
                        .font(.caption)
                        .foregroundStyle(Theme.Colors.textSecondary)
                        .frame(width: 170, alignment: .leading)
                        .lineLimit(1)
                    GeometryReader { geo in
                        ZStack(alignment: .leading) {
                            RoundedRectangle(cornerRadius: 3, style: .continuous)
                                .fill(Theme.Colors.controlTrack)
                            RoundedRectangle(cornerRadius: 3, style: .continuous)
                                .fill(Theme.Colors.accent)
                                .frame(width: max(geo.size.width * placement.impressionShare, 2))
                        }
                    }
                    .frame(height: 8)
                    // Bounded on purpose. Left to fill the window the bar ran a
                    // metre away from its own percentage and spend, and the two
                    // small placements read as empty rows.
                    .frame(maxWidth: Self.barWidth)
                    Text(Format.percent(placement.impressionShare, digits: 1))
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(Theme.Colors.muted)
                        .frame(width: 52, alignment: .trailing)
                    Text(Format.money(placement.cost, currency: currency))
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(Theme.Colors.textPrimary)
                        .frame(width: 66, alignment: .trailing)
                    Spacer(minLength: 0)
                }
                .accessibilityElement(children: .combine)
                .accessibilityLabel("\(placement.placement), "
                                    + "\(Format.percent(placement.impressionShare, digits: 1)) of impressions, "
                                    + "\(Format.money(placement.cost, currency: currency)) spent")
            }
        }
    }

    // MARK: - hours

    /// One slot per hour of the day so far, so a MISSING hour is visible as a
    /// hole rather than by its absence.
    ///
    /// The first version drew only the hours that arrived. Two bars sat side by
    /// side labelled 07 and 08 and the seven hours Amazon never sent simply were
    /// not there — the strip looked like a quiet morning instead of a gap. A
    /// missing hour now gets its own hollow marker, which is distinct from both
    /// a real bar and a zero-spend bar.
    private func hourStrip(_ hours: [StreamTodayHour], coverage: StreamCoverage?) -> some View {
        let byHour = Dictionary(uniqueKeysWithValues: hours.compactMap { hour in
            hour.hour.map { ($0, hour) }
        })
        let last = byHour.keys.max() ?? 0
        let peak = max(hours.map(\.cost).max() ?? 0, 0.01)
        let partial = Set(coverage?.partialHours ?? [])
        let missing = Set(coverage?.missingHours ?? [])
        return VStack(alignment: .leading, spacing: Layout.Spacing.xs) {
            HStack(spacing: Layout.Spacing.xs) {
                Text("SPEND BY HOUR · AMAZON TIME")
                    .font(.caption2)
                    .foregroundStyle(Theme.Colors.muted)
                if let coverage, !coverage.complete, let caption = stripCaption(coverage) {
                    Text("· " + caption)
                        .font(.caption2)
                        .foregroundStyle(Theme.Colors.caution)
                }
            }
            .padding(.top, Layout.Spacing.xs)

            HStack(alignment: .bottom, spacing: 3) {
                ForEach(0...last, id: \.self) { index in
                    hourSlot(index, hour: byHour[index], peak: peak,
                             isPartial: partial.contains(index),
                             isMissing: missing.contains(index))
                }
                Spacer(minLength: 0)
            }
            .frame(height: 52, alignment: .bottom)
        }
    }

    /// Names the two shortfalls separately, because they are not the same
    /// problem: a missing hour is gone for good, a partial one merely started
    /// before we were listening and will not recur tomorrow.
    private func stripCaption(_ coverage: StreamCoverage) -> String? {
        var parts: [String] = []
        let missing = coverage.missingHours.count
        let partial = coverage.partialHours?.count ?? 0
        if missing > 0 { parts.append("\(missing) never delivered") }
        if partial > 0 { parts.append("\(partial) started before Stream was on") }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    @ViewBuilder
    private func hourSlot(_ index: Int, hour: StreamTodayHour?, peak: Double,
                          isPartial: Bool, isMissing: Bool) -> some View {
        let label = String(format: "%02d", index)
        VStack(spacing: 3) {
            if let hour {
                // A partial hour gets a real bar for what it holds, drawn in
                // amber with a dashed cap, so it never passes for a whole one.
                RoundedRectangle(cornerRadius: 2, style: .continuous)
                    .fill(isPartial ? Theme.Colors.caution.opacity(0.45)
                                    : Theme.Colors.chartSpend)
                    .frame(height: max(CGFloat(hour.cost / peak) * 34, 2))
            } else {
                // Hollow, amber, and only as tall as a hint: not a bar, not a
                // zero — an hour we do not have and never will.
                RoundedRectangle(cornerRadius: 2, style: .continuous)
                    .strokeBorder((isPartial || isMissing
                                   ? Theme.Colors.caution : Theme.Colors.muted).opacity(0.55),
                                  style: StrokeStyle(lineWidth: 1, dash: [2, 2]))
                    .frame(height: 34)
            }
            Text(label)
                .font(.system(size: 9).monospacedDigit())
                .foregroundStyle(isMissing || isPartial
                                 ? Theme.Colors.caution : Theme.Colors.muted)
        }
        .frame(width: 22)
        .help(Self.hourHelp(label, hour: hour, isPartial: isPartial,
                           isMissing: isMissing, currency: currency))
    }

    nonisolated static func hourHelp(_ label: String, hour: StreamTodayHour?,
                                    isPartial: Bool, isMissing: Bool,
                                    currency: String?) -> String {
        guard let hour else {
            if isPartial {
                return "\(label):00 — before Stream was switched on. Stream was not listening yet, so no data was lost."
            }
            if isMissing {
                return "\(label):00 — never delivered. Stream does not resend this hour."
            }
            return "\(label):00 — no Stream activity was recorded."
        }
        let figures = "\(Format.money(hour.cost, currency: currency)), "
            + "\(Format.count(hour.clicks)) clicks"
        if isPartial {
            return "\(label):00 — \(figures). INCOMPLETE: this hour began before "
                + "Stream was switched on, so it holds only what Amazon's catch-up "
                + "included. The real figure is higher."
        }
        return "\(label):00 — \(figures)"
    }

    // MARK: - not set up

    private func unavailable(_ note: String?) -> some View {
        Label(note ?? "Marketing Stream is not set up for this market.",
              systemImage: "antenna.radiowaves.left.and.right.slash")
            .font(.caption)
            .foregroundStyle(Theme.Colors.muted)
            .frame(maxWidth: .infinity, alignment: .leading)
            .help("Set it up with: appctl stream-setup, then stream-subscribe. "
                  + "See docs/marketing-stream.md")
    }
}
