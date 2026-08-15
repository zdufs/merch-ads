import SwiftUI

enum PrimaryRow {
    static func latest<ID: Hashable>(old: Set<ID>, new: Set<ID>, current: ID?) -> ID? {
        if let added = new.subtracting(old).first { return added }
        if let current, new.contains(current) { return current }
        return new.first
    }
}

struct CampaignInspectorView: View {
    @Environment(AppState.self) private var appState
    let campaign: Campaign
    let currency: String?
    let toggleState: () -> Void
    let editBudget: () -> Void
    @State private var adGroups: [AdGroup] = []
    @State private var loadError: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Layout.Spacing.md) {
                SectionHeader(title: campaign.nameValue,
                              subtitle: "campaign inspector")
                HStack {
                    StatusBadge.campaignType(campaign.type)
                    StatusBadge.entityState(campaign.state)
                    Spacer()
                }
                inspectorCard {
                    LabeledContent("Daily budget") {
                        MoneyText(value: campaign.budget, currency: currency)
                    }
                    LabeledContent("Ad groups") { CountText(value: adGroups.count) }
                    LabeledContent("Enabled") {
                        CountText(value: adGroups.filter { $0.state == "ENABLED" }.count)
                    }
                    LabeledContent("Spend") { MoneyText(value: campaign.spend, currency: currency) }
                    LabeledContent("Sales") { MoneyText(value: campaign.sales, currency: currency) }
                }
                HStack {
                    Button(campaign.state == "ENABLED" ? "Pause" : "Enable", action: toggleState)
                    Button("Edit Budget…", action: editBudget)
                }
                .buttonStyle(.bordered)
                if let loadError {
                    Text(loadError).font(.caption).foregroundStyle(Theme.Colors.critical)
                }
            }
            .padding(Layout.Spacing.sm)
        }
        .task(id: "\(campaign.id)|\(appState.viewKey)") { await load() }
    }

    private func load() async {
        let market = appState.selectedMarket
        let campaignID = campaign.campaignId
        do {
            let bridge = try appState.makeBridge()
            let response = try await bridge.call(
                AdGroupsResponse.self, ["adgroups", "--campaign", campaignID],
                market: market)
            // Only the response for the row and market still on screen may land.
            guard !Task.isCancelled, market == appState.selectedMarket,
                  campaignID == campaign.campaignId else { return }
            adGroups = response.adGroups
            loadError = nil
        } catch {
            guard !Task.isCancelled, market == appState.selectedMarket,
                  campaignID == campaign.campaignId else { return }
            loadError = error.localizedDescription
        }
    }

    private func inspectorCard<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        InspectorCard(content: content)
    }
}

/// Pins the label to the left edge and the value to the right, instead of
/// letting the pair sit together in the middle of an otherwise empty panel.
/// The values then line up in a column that can be read straight down.
struct InspectorRowStyle: LabeledContentStyle {
    func makeBody(configuration: Configuration) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: Layout.Spacing.sm) {
            configuration.label
                .foregroundStyle(.secondary)
            Spacer(minLength: Layout.Spacing.xs)
            configuration.content
        }
    }
}

/// The inspector's white panel. It stretches to the full inspector width rather
/// than hugging its longest line, so the panel keeps its shape as the sidebar is
/// resized and every `LabeledContent` inside lines its values up on the right.
struct InspectorCard<Content: View>: View {
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.xs) { content }
            .labeledContentStyle(InspectorRowStyle())
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(Layout.Spacing.sm)
            .background(Theme.Colors.surface, in: .rect(cornerRadius: Layout.Radius.medium))
    }
}

/// Shown instead of the single-campaign inspector once more than one row is
/// selected. Picking the newest row and hiding the rest left no way to see what
/// the chart above was actually totalling, so this lists every selected campaign
/// on its own line under the combined totals.
struct CampaignMultiInspectorView: View {
    let campaigns: [Campaign]
    let currency: String?
    let pauseAll: ([Campaign]) -> Void
    let enableAll: ([Campaign]) -> Void

    private var totalSpend: Double { campaigns.reduce(0) { $0 + $1.spend } }
    private var totalSales: Double { campaigns.reduce(0) { $0 + $1.sales } }
    private var totalOrders: Int { campaigns.reduce(0) { $0 + $1.orders } }
    private var totalBudget: Double { campaigns.reduce(0) { $0 + ($1.budget ?? 0) } }
    /// Spend-weighted, not a mean of the per-campaign figures — averaging ACOS
    /// across campaigns of different sizes would misreport the combined result.
    private var blendedAcos: Double? { totalSales > 0 ? totalSpend / totalSales : nil }

    private var enabled: [Campaign] { campaigns.filter { $0.state == "ENABLED" } }
    private var paused: [Campaign] { campaigns.filter { $0.state == "PAUSED" } }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Layout.Spacing.md) {
                SectionHeader(title: "\(campaigns.count) campaigns",
                              subtitle: "campaign inspector")
                InspectorCard {
                    LabeledContent("Daily budget") {
                        MoneyText(value: totalBudget, currency: currency)
                    }
                    LabeledContent("Spend") { MoneyText(value: totalSpend, currency: currency) }
                    LabeledContent("Sales") { MoneyText(value: totalSales, currency: currency) }
                    LabeledContent("Orders") { CountText(value: totalOrders) }
                    LabeledContent("ACOS") {
                        PercentText(value: blendedAcos, label: "Blended ACOS")
                    }
                }
                .help("Combined trailing-30 totals for the selected campaigns — the same rows the chart above is charting")

                SectionHeader(title: "Selected", subtitle: "trailing 30d spend and ACOS")
                InspectorCard {
                    ForEach(Array(campaigns.enumerated()), id: \.element.id) { index, campaign in
                        if index > 0 { Divider() }
                        selectedRow(campaign)
                    }
                }

                HStack {
                    if !enabled.isEmpty {
                        Button("Pause \(enabled.count)") { pauseAll(enabled) }
                    }
                    if !paused.isEmpty {
                        Button("Enable \(paused.count)") { enableAll(paused) }
                    }
                }
                .buttonStyle(.bordered)
            }
            .padding(Layout.Spacing.sm)
        }
    }

    /// Two lines: the name against its spend, then what kind of campaign it is
    /// against its ACOS. Pairing each number with its own row keeps the two
    /// figures from reading as one run of digits.
    private func selectedRow(_ campaign: Campaign) -> some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.xxs) {
            HStack(alignment: .firstTextBaseline, spacing: Layout.Spacing.sm) {
                Text(campaign.nameValue)
                    .fontWeight(.medium)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Spacer(minLength: Layout.Spacing.xs)
                MoneyText(value: campaign.spend, currency: currency)
            }
            HStack(spacing: Layout.Spacing.xs) {
                StatusBadge.campaignType(campaign.type)
                StatusBadge.entityState(campaign.state)
                Spacer(minLength: Layout.Spacing.xs)
                PercentText(value: campaign.acos, label: "ACOS")
            }
        }
        .padding(.vertical, Layout.Spacing.xxs)
        .help("\(campaign.nameValue) — spend \(Format.money(campaign.spend, currency: currency)), sales \(Format.money(campaign.sales, currency: currency))")
    }
}

struct AdGroupInspectorView: View {
    @Environment(AppState.self) private var appState
    let campaign: Campaign
    let adGroup: AdGroup
    let currency: String?
    let toggleState: () -> Void
    @State private var history: HistoryResponse?
    @State private var targets: [TargetRow] = []
    @State private var loadError: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Layout.Spacing.md) {
                SectionHeader(title: adGroup.nameValue, subtitle: campaign.nameValue)
                HStack {
                    StatusBadge.entityState(adGroup.state)
                    if let type = adGroup.type { StatusBadge.campaignType(type) }
                    Spacer()
                }
                InspectorCard {
                    LabeledContent("ASIN", value: adGroup.asin ?? "—")
                    LabeledContent("Targets") { CountText(value: targets.count) }
                    LabeledContent("With sales") {
                        CountText(value: targets.filter { $0.orders > 0 }.count)
                    }
                    LabeledContent("Spend") { MoneyText(value: adGroup.spend, currency: currency) }
                    LabeledContent("Sales") { MoneyText(value: adGroup.sales, currency: currency) }
                }
                Button(adGroup.state == "ENABLED" ? "Pause Ad Group" : "Enable Ad Group",
                       action: toggleState)
                    .buttonStyle(.bordered)
                if let history, history.points.count > 1 {
                    SectionHeader(title: "Performance history",
                                  subtitle: history.isDaily
                                      ? "banked per-day totals" : "banked trailing-30 snapshots")
                    HistoryChart(points: history.points, currency: currency)
                    HistorySeriesCaption(history: history)
                } else {
                    Text("Not enough banked performance history yet.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                if let loadError {
                    Text(loadError).font(.caption).foregroundStyle(Theme.Colors.critical)
                }
            }
            .padding(Layout.Spacing.sm)
        }
        .task(id: "\(adGroup.id)|\(appState.viewKey)") { await load() }
    }

    private func load() async {
        let market = appState.selectedMarket
        let adGroupID = adGroup.adGroupId
        do {
            let bridge = try appState.makeBridge()
            async let historyCall = bridge.call(
                HistoryResponse.self, ["history", "--adgroup", adGroupID],
                market: market)
            async let targetsCall = bridge.call(
                TargetsResponse.self, ["targets", "--adgroup", adGroupID],
                market: market)
            let loadedHistory = try? await historyCall
            let loadedTargets = try await targetsCall.targets
            // Only the response for the row and market still on screen may land.
            guard !Task.isCancelled, market == appState.selectedMarket,
                  adGroupID == adGroup.adGroupId else { return }
            history = loadedHistory
            targets = loadedTargets
            loadError = nil
        } catch {
            guard !Task.isCancelled, market == appState.selectedMarket,
                  adGroupID == adGroup.adGroupId else { return }
            loadError = error.localizedDescription
        }
    }
}

struct KillRowInspectorView: View {
    let design: KillDesign
    let currency: String?
    let pause: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.md) {
            SectionHeader(title: design.asin ?? design.adGroupId,
                          subtitle: "kill-list inspector")
            HStack {
                StatusBadge.entityState(design.state)
                if let type = design.type { StatusBadge.campaignType(type) }
                Spacer()
            }
            InspectorCard {
                LabeledContent("Spend") { MoneyText(value: design.spend, currency: currency) }
                LabeledContent("Sales") { MoneyText(value: design.sales, currency: currency) }
                LabeledContent("Clicks") { CountText(value: design.clicks) }
                LabeledContent("Orders") { CountText(value: design.orders) }
                LabeledContent("CVR") {
                    PercentText(value: design.cvr, label: "CVR", color: Theme.Colors.critical)
                }
                LabeledContent("ACOS") {
                    PercentText(value: design.acos, breakEven: design.breakEven, label: "ACOS")
                }
                LabeledContent("Break-even ACOS") {
                    PercentText(value: design.breakEven, label: "Break-even", color: .secondary)
                }
            }
            if let trace = design.trace, !trace.isEmpty {
                DebugTraceBlock(trace: trace, currency: currency)
            } else {
                Text("This row is actionable because conversion is below the market CVR floor and ACOS is over this design's break-even threshold.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            if design.state == "ENABLED" {
                Button("Pause Ad Group", role: .destructive, action: pause)
                    .buttonStyle(.borderedProminent)
            }
            Spacer()
        }
        .padding(Layout.Spacing.sm)
    }
}
