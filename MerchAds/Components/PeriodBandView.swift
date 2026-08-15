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
                Label("partial", systemImage: "exclamationmark.triangle.fill")
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
                     subtitle: "modeled royalty")
                .frame(maxHeight: .infinity, alignment: .topLeading)
                .mdCard()

            StatCard(title: "Ad spend",
                     value: period.spend.map { Format.money($0, currency: currency) } ?? "—",
                     symbol: "creditcard.fill",
                     subtitle: daysLabel)
                .frame(maxHeight: .infinity, alignment: .topLeading)
                .mdCard()

            StatCard(title: "Attributed sales",
                     value: period.sales.map { Format.money($0, currency: currency) } ?? "—",
                     symbol: "chart.line.uptrend.xyaxis",
                     subtitle: daysLabel)
                .frame(maxHeight: .infinity, alignment: .topLeading)
                .mdCard()

            StatCard(title: "ACOS",
                     value: Format.percent(period.acos),
                     tint: AcosTier.select(acos: period.acos).color,
                     symbol: "percent",
                     subtitle: daysLabel)
                .frame(maxHeight: .infinity, alignment: .topLeading)
                .mdCard()
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

    private var daysLabel: String? {
        guard let days = period.daysBanked else { return nil }
        return days == 1 ? "1 day" : "\(days) days"
    }

    private var windowLabel: String? {
        period.window.flatMap { DashboardView.windowLabel($0) }
    }
}
