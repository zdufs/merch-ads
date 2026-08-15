import SwiftUI
import Charts

protocol ChartTooltipDatum {
    var tooltipTitle: String { get }
    var tooltipDetail: String { get }
}

struct ChartTooltip: View {
    let datum: any ChartTooltipDatum

    var body: some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.xxs) {
            Text(datum.tooltipTitle).font(.caption.weight(.semibold))
            Text(datum.tooltipDetail).font(.caption2).foregroundStyle(.secondary)
        }
        .padding(Layout.Spacing.xs)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: Layout.Radius.small))
    }
}

extension View {
    func merchAdsChartStyle(height: CGFloat = Layout.ChartHeight.standard) -> some View {
        self
            .chartLegend(position: .top, alignment: .trailing, spacing: Layout.Spacing.xs)
            .chartYAxis {
                AxisMarks(position: .leading) {
                    AxisGridLine(stroke: StrokeStyle(lineWidth: 0.5))
                        .foregroundStyle(Theme.Colors.chartGrid)
                    // Concrete Color, not .secondary: the hierarchical style picked
                    // up a series tint in dark mode (operator: "just use white").
                    AxisValueLabel().foregroundStyle(Color.primary)
                }
            }
            .chartPlotStyle { plot in
                plot.background(Theme.Colors.surface.opacity(0.35))
            }
            .frame(minHeight: height, maxHeight: .infinity)
    }
}
