import SwiftUI

enum Screen: String, CaseIterable, Identifiable, Hashable, Codable, Sendable {
    case dashboard, allMarkets, health
    case campaigns, targets, liveStatus, killList, kdpBooks, productRoyalty
    case bidReport, profit, crossPurchase, accumulatedAsins, accumulatedKeywords, demandFeed, seasonal, halo, reports
    case watchlist, rules, strategyBuilder
    case actions, approvals, harvest, dataImport, audit, errors
    case settings

    var id: String { rawValue }

    var title: String {
        switch self {
        case .dashboard: "Dashboard"
        case .allMarkets: "All Markets"
        case .health: "System Health"
        case .campaigns: "Campaigns"
        case .targets: "Targets"
        case .liveStatus: "Live Status"
        case .killList: "Kill List"
        case .kdpBooks: "KDP Books"
        case .productRoyalty: "Product Royalty"
        case .bidReport: "Bid Report"
        case .profit: "Profit"
        case .accumulatedAsins: "Accumulated ASINs"
        case .accumulatedKeywords: "Accumulated Keywords"
        case .watchlist: "Watchlist"
        case .rules: "Rules"
        case .strategyBuilder: "Strategy Builder"
        case .demandFeed: "Demand Feed"
        case .seasonal: "Seasonal"
        case .crossPurchase: "Cross-purchase"
        case .halo: "Organic Halo"
        case .reports: "Reports"
        case .actions: "Actions"
        case .approvals: "Approval Queue"
        case .harvest: "Harvest"
        case .dataImport: "Import"
        case .audit: "Audit Trail"
        case .errors: "Errors"
        case .settings: "Settings"
        }
    }

    var icon: String {
        switch self {
        case .dashboard: "chart.bar.xaxis"
        case .allMarkets: "globe"
        case .health: "heart.text.square"
        case .campaigns: "megaphone"
        case .targets: "scope"
        case .liveStatus: "dot.radiowaves.left.and.right"
        case .killList: "xmark.bin"
        case .kdpBooks: "books.vertical"
        case .productRoyalty: "banknote"
        case .bidReport: "chart.line.uptrend.xyaxis"
        case .profit: "dollarsign.circle"
        case .accumulatedAsins: "square.stack.3d.up"
        case .accumulatedKeywords: "text.append"
        case .watchlist: "pin"
        case .rules: "curlybraces"
        case .strategyBuilder: "wand.and.stars"
        case .demandFeed: "sparkles"
        case .seasonal: "calendar.badge.clock"
        case .crossPurchase: "arrow.triangle.branch"
        case .halo: "waveform.path.ecg"
        case .reports: "doc.text.magnifyingglass"
        case .actions: "bolt"
        case .approvals: "checklist"
        case .harvest: "leaf"
        case .dataImport: "tray.and.arrow.down"
        case .audit: "clock.arrow.circlepath"
        case .errors: "exclamationmark.triangle"
        case .settings: "gearshape"
        }
    }

    /// Hover tooltip: what this screen is FOR, in one sentence.
    var blurb: String {
        switch self {
        case .dashboard:
            "This month, year to date, and month-by-month performance for the selected market."
        case .allMarkets:
            "Every market in this account family side by side — trailing 30 days and year to date, grouped by currency."
        case .health:
            "Is the nightly job OK? Data freshness per market, plus the KILL and approval-gate state."
        case .campaigns:
            "Browse campaigns → ad groups → targets and search terms. Pause, edit bids, negate terms, see bid history."
        case .targets:
            "Every keyword and product target across all campaigns in one sortable, filterable table — impressions, CTR, CPC, ACOS and more."
        case .liveStatus:
            "Look up an ASIN and check its REAL state on Amazon right now (the tables elsewhere show last night's snapshot)."
        case .killList:
            "Designs to stop: 'Bleeding' converts too poorly to ever profit; 'Stale' gets seen but never clicked. One-click pause."
        case .kdpBooks:
            "Each KDP book's royalty per ASIN — the book economics that drive its break-even, profit, and kill-list rules. A book with no entry fails closed."
        case .productRoyalty:
            "What every Merch product earns you. Edit the royalties the whole app prices with — break-even, kill list, profit and the bid rules all read these numbers."
        case .bidReport:
            "Every bid change the engine made — what moved up, what moved down, and the rule that moved it."
        case .profit:
            "True margin per design and product type: royalty earned minus ad spend (ACOS can't tell you this)."
        case .accumulatedAsins:
            "Every advertised ASIN summed across all its campaigns — spot designs quietly bleeding budget across many small campaigns."
        case .accumulatedKeywords:
            "Every keyword summed across all campaigns it runs in — a term can look fine in one campaign but be a disaster across ten."
        case .watchlist:
            "A private pinboard: pin campaigns, ad groups, targets, or ASINs and watch them as one combined trend — great for babysitting a launch."
        case .rules:
            "Write your own automation in a plain-language, economics-aware language (bid/pause/negate on break-even, royalty, profit). Preview before it ever writes."
        case .demandFeed:
            "Proven customer searches to design NEW work for, and recent top earners to make variations of."
        case .seasonal:
            "Tag seasonal designs (Juneteenth, Christmas…) so the nightly job pauses them out of season and re-enables them ahead of time."
        case .dataImport:
            "Bring any file in from one place. New Designs routes a catalogue export into Lottery and Scavenger campaigns; Sales banks the Merch sales report; Ads banks the console monthly-history export. Each tab shows its own last-recorded date and how to get the file."
        case .crossPurchase:
            "Measured halo: a shopper clicked one design's ad and bought a different design. Amazon attributes it; the campaign and targeting reports credit it nowhere."
        case .halo:
            "Does advertising a design move its ORGANIC royalty? Compares each advertised design's royalty rate after ads started against its own pre-ad baseline. An upper bound — correlational, not causal."
        case .reports:
            "An account rollup for any date range — spend, sales, orders, and the derived ad metrics (ACOS, ROAS, CTR, CPC, CVR, CPO) with a per-day CSV export."
        case .strategyBuilder:
            "Guided two-step flow: promote converting search terms into exact-match keywords, and negate the wasteful ones — in one place."
        case .actions:
            "The KILL freeze, the approval gate, reset inflated bids, and manual run triggers."
        case .approvals:
            "Review the negatives and pauses the automation wants to apply — approve or reject each one."
        case .harvest:
            "Converting search terms to promote into exact-match keywords, and wasteful harvested keywords to prune."
        case .audit:
            "Every write ever made to Amazon, newest first — with Undo where the action is reversible."
        case .errors:
            "Everything wrong right now, in one place: failed appctl calls, a closed economics gate, stale data, KILL state, and engine alerts."
        case .settings:
            "Where the engine lives, the python runtime, action confirmation, and per-market bid ceilings."
        }
    }

    /// Screens deliberately kept OUT of the sidebar and the command palette.
    /// Their views and routing still exist, so this is fully reversible — remove
    /// a case here and it reappears. Watchlist and Strategy Builder were retired
    /// from navigation on operator request (2026-08-13): the nightly automation
    /// already promotes and negates, and the watchlist pins went unused.
    var isHidden: Bool { self == .watchlist || self == .strategyBuilder }

    /// Screens that only make sense for the POD/Merch business: building tee
    /// campaigns from a catalogue export, the Merch sales report, organic
    /// halo, cross-purchase, and seasonal designs. The engine already returns
    /// `supported:false` for the organic ones outside US-Merch, and the rest is
    /// tee-campaign machinery with no KDP equivalent.
    ///
    /// When a KDP advertiser account is selected these are dropped from the
    /// sidebar and the command palette. KDP reuses every shared screen — the
    /// dashboard, the campaign/target browsers, kill list, profit, rules,
    /// reports — because their economics are kind-aware and work for books. Its
    /// book royalties live on the KDP-only KDP Books screen (see `isKDPOnly`).
    var isMerchOnly: Bool {
        switch self {
        case .crossPurchase, .demandFeed, .seasonal, .halo, .dataImport, .harvest,
             .productRoyalty:
            true
        default:
            false
        }
    }

    /// The inverse of `isMerchOnly`: screens that only make sense for a KDP
    /// advertiser account. KDP Books holds each book's royalty (the KDP analog
    /// of the Merch catalogue export). A Merch account gets its economics from
    /// the export instead, so this screen is dropped from a Merch sidebar and
    /// command palette.
    var isKDPOnly: Bool { self == .kdpBooks }

    /// Whether this screen belongs in the navigation for a market of this kind.
    func isAvailable(forKDP isKDP: Bool) -> Bool {
        isKDP ? !isMerchOnly : !isKDPOnly
    }

    static func restored(from rawValue: String) -> Screen? {
        if rawValue == "playbook" { return .health }
        if rawValue == "intake" { return .dataImport }   // New Designs folded into Import
        return Screen(rawValue: rawValue)
    }
}

struct ContentView: View {
    @Environment(AppState.self) private var appState
    @AppStorage(AppSettings.appearanceKey) private var appearanceRaw = AppAppearance.system.rawValue
    @State private var selection: Screen? = .dashboard
    // Pin the sidebar open. Without an explicit binding the split view can drop
    // into detail-only / overlay mode (where selecting a row auto-hides the
    // sidebar) and there is no menu or shortcut to bring it back — the app then
    // reads as "the sidebar buttons do nothing." Defaulting to .all each launch
    // also discards any persisted collapsed state.
    @State private var columnVisibility: NavigationSplitViewVisibility = .all

    var body: some View {
        @Bindable var appState = appState
        NavigationSplitView(columnVisibility: $columnVisibility) {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    sidebarGroup("Overview", [.dashboard, .allMarkets])
                    sidebarGroup("Manage", [.campaigns, .targets, .kdpBooks, .productRoyalty, .rules,
                                            .liveStatus, .killList, .harvest, .approvals, .dataImport])
                    sidebarGroup("Insights", [.profit, .crossPurchase, .accumulatedAsins,
                                              .accumulatedKeywords, .bidReport, .reports,
                                              .demandFeed, .seasonal, .halo])
                    sidebarGroup("System", [.errors, .actions, .audit, .health, .settings])
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 12)
            }
            .scrollContentBackground(.hidden)
            .background(Theme.Colors.surface)
            .overlay(alignment: .trailing) {
                Rectangle().fill(Theme.Colors.separator).frame(width: 1)
            }
            .navigationSplitViewColumnWidth(min: 210, ideal: 230, max: 300)
            .safeAreaInset(edge: .bottom, spacing: 0) {
                VStack(spacing: 0) {
                    AppearanceToggle()
                        .frame(maxWidth: .infinity)   // centre the pill in the sidebar
                        .padding(.top, 8)
                        .padding(.bottom, 6)
                    MarketFooter()
                }
                // The sidebar's bottom inset region hugs the separator on the
                // trailing edge but insets the leading edge, so an untouched
                // centre lands slightly right. Match the leading inset on the
                // trailing side so the region is symmetric — the pill and the
                // market card then both sit in the true middle.
                .padding(.trailing, 12)
            }
        } detail: {
            VStack(spacing: 0) {
                if let message = appState.lastError {
                    ErrorBanner(message: message) { appState.lastError = nil }
                }
                detailView
            }
            .scrollEdgeEffectStyle(.soft, for: .top)
        }
        .navigationSplitViewStyle(.balanced)   // sidebar sits beside the detail, never as a dismiss-on-tap overlay
        .frame(minWidth: 900, minHeight: 560)
        .tint(Theme.Colors.accent)              // MerchDash indigo — buttons, links, selection
        .preferredColorScheme(AppAppearance.stored(appearanceRaw).colorScheme)  // System / Light / Dark (Settings)
        // Every label, number and note in the app is selectable and copyable.
        // This is an environment value, so one call here reaches every screen —
        // the alternative was remembering `.textSelection` on each new Text, and
        // that was already losing. Two places opt back OUT (the command palette
        // and the rule list): there a drag has to pick a row, not a word.
        // `Table` cells ignore this entirely on macOS, so tables carry their own
        // right-click Copy — see Copyable.swift.
        .textSelection(.enabled)
        .navigationTitle("Merch Ads")
        // The version sits beside the title as a SUBTITLE, not inside the title
        // string. MenuBarController.bringMainWindowFront() finds the window by
        // `window.title == "Merch Ads"`, so folding the version into the title
        // would quietly break the menu bar's "Open Merch Ads".
        .navigationSubtitle(AppVersion.displayName)
        .sheet(isPresented: $appState.showingCommandPalette) {
            CommandPaletteView()
        }
        .onChange(of: appState.requestedRoute) {
            guard let route = appState.requestedRoute else { return }
            // Switch the screen first. The path/focus push must happen on the NEXT
            // runloop tick: a NavigationStack created in the SAME transaction as a
            // pre-populated `path` can't resolve its destinations yet and writes the
            // path back to empty, dropping the deep link. Deferring lets the target
            // screen mount with an empty path, then receive the route as a genuine
            // programmatic push (and lets .onAppear focus consumers see the change).
            selection = route.screen
            Task { @MainActor in appState.consumeRequestedRoute(route) }
        }
        // Switching to a KDP account can hide the screen you were on (a Merch-only
        // one). Fall back to the Dashboard so the detail never shows a screen the
        // sidebar no longer offers.
        .onChange(of: appState.selectedMarket) {
            let isKDP = appState.currentMarket?.isKDP == true
            if let current = selection, !current.isAvailable(forKDP: isKDP) {
                selection = .dashboard
            }
        }
        .toolbar {
            if PythonBridge.isRehearsal {
                ToolbarItem(placement: .status) {
                    StatusBadge(text: "REHEARSAL", symbol: "shield.checkered",
                                tint: Theme.Colors.caution)
                        .help("Mutating appctl commands are blocked; coordinator actions are recorded only")
                }
            }
            ToolbarItem(placement: .primaryAction) {
                // A pull-down Menu, not a `.menu` Picker. The pop-up Picker
                // positions the SELECTED row over the button, so for a market low
                // in the list (FR, ES, IT, KDP) the menu opened scrolled and
                // clipped, with an up-chevron and a blue highlight. A Menu always
                // drops the full list below the button, the current market marked
                // with a checkmark.
                Menu {
                    ForEach(appState.markets) { market in
                        Button {
                            appState.selectedMarket = market.code
                        } label: {
                            if market.code == appState.selectedMarket {
                                Label("\(market.displayLabel) \(market.currencySymbol)",
                                      systemImage: "checkmark")
                            } else {
                                Text("\(market.displayLabel) \(market.currencySymbol)")
                            }
                        }
                    }
                } label: {
                    Text("\(appState.currentMarket?.displayLabel ?? appState.selectedMarket) \(appState.currentMarket?.currencySymbol ?? "")")
                }
                .help("Active market — every screen and action targets this market")
                .accessibilityLabel("Market")
            }
            ToolbarItem {
                Button {
                    Task { await appState.refresh() }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
                .buttonStyle(.bordered)
                .disabled(appState.isLoading)
                .help("Reload markets and engine health (⌘R)")
            }
        }
    }

    @ViewBuilder
    private var detailView: some View {
        let screen = selection ?? .dashboard
        if screen == .settings {
            ScreenDetail(screen: screen)
        } else {
            ScreenDetail(screen: screen)
                .id(appState.actionContextGeneration)
        }
    }
}

/// The detail pane as its OWN struct: ContentView.body reads appState.isLoading
/// for the toolbar spinner, and while the switch lived inline every isLoading
/// toggle re-evaluated the whole detail subtree. As a separate view it only
/// re-evaluates when the selected screen changes.
private struct ScreenDetail: View {
    let screen: Screen

    var body: some View {
        switch screen {
        case .dashboard:
            DashboardView()
        case .allMarkets:
            AllMarketsView()
        case .campaigns:
            CampaignBrowserView()
        case .targets:
            TargetsView()
        case .liveStatus:
            LiveStatusView()
        case .killList:
            KillListView()
        case .kdpBooks:
            KdpBooksView()
        case .productRoyalty:
            ProductRoyaltyView()
        case .bidReport:
            BidReportView()
        case .profit:
            ProfitView()
        case .accumulatedAsins:
            AccumulatedAsinsView()
        case .accumulatedKeywords:
            AccumulatedKeywordsView()
        case .watchlist:
            WatchlistView()
        case .rules:
            RulesView()
        case .demandFeed:
            DemandFeedView()
        case .seasonal:
            SeasonalView()
        case .crossPurchase:
            CrossPurchaseView()
        case .dataImport:
            ImportHubView()
        case .halo:
            HaloView()
        case .reports:
            ReportsView()
        case .strategyBuilder:
            StrategyBuilderView()
        case .actions:
            ActionsView()
        case .approvals:
            ApprovalsView()
        case .harvest:
            HarvestView()
        case .audit:
            AuditView()
        case .health:
            HealthView()
        case .errors:
            ErrorsView()
        case .settings:
            SettingsView(embedded: true)
        }
    }
}

extension ContentView {
    @ViewBuilder
    fileprivate func sidebarGroup(_ title: String, _ screens: [Screen]) -> some View {
        // Drop the screens that don't apply to the selected account kind, and
        // skip the group entirely (header included) if nothing is left.
        let isKDP = appState.currentMarket?.isKDP == true
        let visible = screens.filter { $0.isAvailable(forKDP: isKDP) }
        if !visible.isEmpty {
            VStack(alignment: .leading, spacing: 2) {
                Text(title.uppercased())
                    .font(Typography.cardLabel)
                    .tracking(0.5)
                    .foregroundStyle(Theme.Colors.muted)
                    .padding(.horizontal, 12)
                    .padding(.bottom, 2)
                ForEach(visible) { sidebarNavItem($0) }
            }
        }
    }

    private func sidebarNavItem(_ screen: Screen) -> some View {
        let active = (selection ?? .dashboard) == screen
        return Button {
            selection = screen
        } label: {
            HStack(spacing: 9) {
                // Decorative: it repeats the title, so it stays out of the
                // accessibility element — the same rule StatCard follows.
                Image(systemName: screen.icon)
                    .font(Typography.cardBody)
                    .frame(width: 17, alignment: .center)
                    .accessibilityHidden(true)
                Text(screen.title)
                Spacer(minLength: 0)
            }
            .font(.system(.body, weight: active ? .semibold : .medium))
            .foregroundStyle(active ? Theme.Colors.accent : Theme.Colors.textSecondary)
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(active ? Theme.Colors.accentSoft : Color.clear,
                        in: RoundedRectangle(cornerRadius: 8, style: .continuous))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help(screen.blurb)
        // The row already names itself: SwiftUI derives a Button's
        // accessibility name from the Text inside its label, so VoiceOver reads
        // "Dashboard", "Campaigns" and so on with or without these two lines.
        // Verified by removing them, rebuilding and reading the real
        // accessibility API — all 25 rows stayed named.
        //
        // They are kept for two reasons. The label is explicit, so the name
        // survives someone rearranging the HStack or wrapping the Text. And the
        // HINT is not otherwise available: `.help` above publishes AXHelp,
        // which a pointer surfaces as a tooltip and a screen reader does not
        // read as guidance.
        //
        // A note on how this was checked, because it was nearly recorded as a
        // defect that never existed: System Events reports `AXDescription` as
        // missing for these buttons even when it is set. Reading the
        // accessibility API directly shows the real value. Measure SwiftUI
        // accessibility with the API, never through System Events.
        .accessibilityLabel(Text(screen.title))
        .accessibilityHint(Text(screen.blurb))
        .accessibilityAddTraits(active ? [.isButton, .isSelected] : .isButton)
    }
}

/// Sidebar light/dark toggle: a sliding pill with a moon (dark) and a sun
/// (light), knob over the active side. Binary by design — it pins Light or Dark.
/// System stays available in Settings. Writes the same `AppSettings.appearanceKey`,
/// so it and the Settings picker stay in sync. When the stored value is System it
/// reflects the effective appearance and a tap pins the opposite.
private struct AppearanceToggle: View {
    @AppStorage(AppSettings.appearanceKey) private var appearanceRaw = AppAppearance.system.rawValue
    @Environment(\.colorScheme) private var systemScheme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var isDark: Bool {
        switch AppAppearance.stored(appearanceRaw) {
        case .dark: true
        case .light: false
        case .system: systemScheme == .dark
        }
    }

    private let width: CGFloat = 84
    private let height: CGFloat = 34
    private var knob: CGFloat { height - 8 }              // 26 — leaves a 4pt ring of pill around it
    private let iconSize: CGFloat = 16
    private let inset: CGFloat = 5                        // clear gap between knob and pill edge
    // The two rest points for the knob — the idle icons sit on the SAME points,
    // so everything lines up on one horizontal centre line.
    private var leftCenter: CGFloat { inset + knob / 2 }        // 18
    private var rightCenter: CGFloat { width - inset - knob / 2 } // 66

    var body: some View {
        Button {
            appearanceRaw = (isDark ? AppAppearance.light : AppAppearance.dark).rawValue
        } label: {
            ZStack {
                Capsule()
                    .fill(Theme.Colors.accentSoft)
                    .overlay { Capsule().strokeBorder(Theme.Colors.separator, lineWidth: 1) }
                // Idle icons, dimmed, pinned dead-centre on each rest point. The
                // active side is covered by the knob, so it is hidden.
                Image(systemName: "moon.fill")
                    .font(.system(size: iconSize, weight: .semibold))
                    .foregroundStyle(Theme.Colors.muted)
                    .opacity(isDark ? 0 : 1)
                    .position(x: leftCenter, y: height / 2)
                Image(systemName: "sun.max.fill")
                    .font(.system(size: iconSize, weight: .semibold))
                    .foregroundStyle(Theme.Colors.muted)
                    .opacity(isDark ? 1 : 0)
                    .position(x: rightCenter, y: height / 2)
                // Knob: white circle carrying the active mode's icon, centred on
                // its rest point — moon (left) when dark, sun (right) when light.
                ZStack {
                    Circle()
                        .fill(.white)
                        .shadow(color: .black.opacity(0.22), radius: 2, y: 1)
                    Image(systemName: isDark ? "moon.fill" : "sun.max.fill")
                        .font(.system(size: iconSize, weight: .semibold))
                        .foregroundStyle(Color(hex: 0x1F2430))
                }
                .frame(width: knob, height: knob)
                .position(x: isDark ? leftCenter : rightCenter, y: height / 2)
            }
            .frame(width: width, height: height)
        }
        .buttonStyle(.plain)
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.18), value: isDark)
        .help(isDark ? "Dark appearance — click for Light" : "Light appearance — click for Dark")
        .accessibilityLabel("Appearance")
        .accessibilityValue(isDark ? "Dark" : "Light")
        .accessibilityHint("Switches between Light and Dark")
    }
}

/// Sidebar footer: the selected market's freshness, read directly from its
/// SQLite file (independent check on the read-only DB layer), plus KILL state.
private struct MarketFooter: View {
    @Environment(AppState.self) private var appState
    @State private var latestDate: String?
    @State private var campaignCount: Int64?

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Divider()
            HStack(spacing: 6) {
                Image(systemName: "cylinder.split.1x2")
                    .foregroundStyle(.secondary)
                Text(appState.selectedMarket)
                    .fontWeight(.semibold)
                if let campaignCount {
                    Text("· \(campaignCount) active")
                        .foregroundStyle(.secondary)
                        .help("Campaigns currently serving (ENABLED) in this market — paused and archived campaigns are not counted")
                }
            }
            .font(.caption)
            Text(latestDate.map { "data through \(Format.euDate($0))" } ?? "no local data")
                .font(.caption2)
                .foregroundStyle(.secondary)
                .help("Read straight from this market's local SQLite file — updates after each nightly pull")
            if appState.killActive {
                Label("KILL active — writes frozen", systemImage: "exclamationmark.octagon.fill")
                    .font(.caption2)
                    .foregroundStyle(Theme.Colors.critical)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .mdCard()
        .task(id: appState.viewKey) { await load() }
        .onChange(of: appState.health?.killActive) { Task { await load() } }
    }

    private func load() async {
        let market = appState.selectedMarket
        let snapshot = await Task.detached(priority: .utility) {
            AppState.directSnapshot(market: market)
        }.value
        latestDate = snapshot.latestDate
        campaignCount = snapshot.campaignCount
    }
}

struct ErrorBanner: View {
    let message: String
    let dismiss: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(Theme.Colors.caution)
            Text(message)
                .lineLimit(2)
                .textSelection(.enabled)
            Spacer()
            Button("Dismiss", action: dismiss)
                .buttonStyle(.borderless)
        }
        .font(.callout)
        .padding(10)
        .background(Theme.Colors.caution.opacity(0.12))
        .overlay(alignment: .bottom) { Divider() }
    }
}

/// Inline banner for a FAILED ACTION (pause, bid, negate, undo, …): the data
/// on screen is still valid, so this renders above it instead of replacing it
/// the way `loadError` empty-states do.
struct ActionErrorBar: View {
    @Binding var message: String?

    var body: some View {
        if let text = message {
            HStack(spacing: 8) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .foregroundStyle(Theme.Colors.critical)
                Text(text)
                    .lineLimit(2)
                    .textSelection(.enabled)
                Spacer()
                Button("Dismiss") { message = nil }
                    .buttonStyle(.borderless)
            }
            .font(.callout)
            .padding(8)
            .background(Theme.Colors.critical.opacity(0.1))
            .overlay(alignment: .bottom) { Divider() }
        }
    }
}

#Preview {
    ContentView()
        .environment(AppState())
}
