import SwiftUI

/// One row of the dashboard's period stack: four cards for a single window.
///
/// Spend, sales and ACOS are exact and every period reads the same banked daily
/// history, so rows can be compared directly. Profit is MODELED — royalty is per
/// design and no per-design daily table exists — and each card says so.
///
/// A period the data cannot cover renders its reason instead of zeroes. Amazon's
/// reporting retention only reaches ~95 days back, so the earliest windows do not
/// exist and never will; showing 0,00 there would read as "you earned nothing".
///
/// The four cards do not always cover the SAME window. A period extended
/// backwards with months imported from the Ads console has complete spend, sales
/// and ACOS, and profit over the daily-banked portion alone — royalty is per
/// design and imported months carry no per-design economics. Each card therefore
/// states its own span, and the profit card says so in the caution colour.
/// Without that, Year to date read a whole year of spend beside a profit figure
/// that covered only its last 143 days.
struct PeriodBandView: View {
    let period: PeriodRow
    let currency: String?

    var body: some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.xs) {
            header
            if period.available {
                cards
            } else {
                unavailable
            }
        }
    }

    private var header: some View {
        HStack(spacing: Layout.Spacing.xs) {
            Text(period.label.uppercased())
                .font(.caption.weight(.semibold))
                .tracking(0.55)
                .foregroundStyle(Theme.Colors.muted)
            if let window = windowLabel {
                Text(window)
                    .font(.caption)
                    .foregroundStyle(Theme.Colors.muted)
            }
            if period.partial == true {
                // A hole in the middle is a different fault from a window that
                // simply starts late, and it is the worse one: the total looks
                // whole and is quietly short by those days.
                Label((period.daysMissing ?? 0) > 0
                      ? "\(period.daysMissing ?? 0) day(s) missing" : "partial",
                      systemImage: "exclamationmark.triangle.fill")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Theme.Colors.caution)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 2)
                    .background(Theme.Colors.caution.opacity(0.12),
                                in: Capsule(style: .continuous))
                    .help(period.partialReason ?? "This window starts later than it should.")
            }
            Spacer(minLength: 0)
        }
    }

    private var cards: some View {
        HStack(alignment: .top, spacing: Layout.Spacing.sm) {
            StatCard(title: "Estimated profit",
                     value: period.profit.map { Format.money($0, currency: currency) } ?? "—",
                     tint: profitTint,
                     symbol: "dollarsign.circle.fill",
                     subtitle: period.profitSubtitle,
                     subtitleTint: period.profitWindowIsShorter ? Theme.Colors.caution : nil)
                .frame(maxHeight: .infinity, alignment: .topLeading)
                .mdCard()
                .help(period.profitNote ?? profitHelp)

            StatCard(title: "Ad spend",
                     value: period.spend.map { Format.money($0, currency: currency) } ?? "—",
                     symbol: "creditcard.fill",
                     subtitle: period.spanSubtitle)
                .frame(maxHeight: .infinity, alignment: .topLeading)
                .mdCard()
                .help(period.source ?? "Banked daily history.")

            StatCard(title: "Attributed sales",
                     value: period.sales.map { Format.money($0, currency: currency) } ?? "—",
                     symbol: "chart.line.uptrend.xyaxis",
                     subtitle: period.spanSubtitle)
                .frame(maxHeight: .infinity, alignment: .topLeading)
                .mdCard()
                .help(period.source ?? "Banked daily history.")

            StatCard(title: "ACOS",
                     value: Format.percent(period.acos),
                     tint: AcosTier.select(acos: period.acos).color,
                     symbol: "percent",
                     subtitle: period.spanSubtitle)
                .frame(maxHeight: .infinity, alignment: .topLeading)
                .mdCard()
                .help(period.source ?? "Banked daily history.")
        }
        .fixedSize(horizontal: false, vertical: true)
    }

    private var unavailable: some View {
        HStack(spacing: Layout.Spacing.xs) {
            Image(systemName: "clock.badge.xmark")
                .foregroundStyle(Theme.Colors.muted)
            Text(period.reason ?? "No data for this period.")
                .font(.callout)
                .foregroundStyle(Theme.Colors.muted)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .padding(.vertical, 14)
        .padding(.horizontal, 18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .mdCard()
    }

    private var profitTint: Color {
        guard let profit = period.profit else { return .primary }
        return profit >= 0 ? Theme.Colors.positive : Theme.Colors.critical
    }

    /// Shown when the engine sent no sentence of its own — profit is always
    /// modeled, even on a row whose window it covers whole.
    private var profitHelp: String {
        "Royalty is per design and no per-design daily table exists, so profit "
        + "is modeled from each period's product-type mix."
    }

    private var windowLabel: String? {
        period.window.flatMap { DashboardView.windowLabel($0) }
    }
}
