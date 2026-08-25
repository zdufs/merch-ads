import Foundation

/// Long-form, per-screen documentation — the text behind the "?" in every page
/// header.
///
/// `Screen.blurb` is the one-sentence hover tooltip: what this screen is FOR.
/// This is the other half — what the numbers mean, how to actually work the
/// screen, and the things that mislead people when nobody says them out loud.
/// The caveats are not decoration: half of them (cumulative snapshots, modeled
/// profit, permanent negatives, upper-bound halo) are places where reading the
/// screen naively leads to a wrong money decision.
struct ScreenHelp: Identifiable, Sendable, Equatable {
    /// Two or three plain sentences: what this screen is.
    let summary: String
    /// Where the numbers come from, so a figure can be traced when it looks wrong.
    let source: String
    /// How to work the screen, in the order you would actually do it.
    let steps: [String]
    /// What misleads people if it is not said out loud.
    let notes: [String]

    var id: String { summary }
}

extension Screen {
    /// The full help for this screen. Exhaustive by construction — a new
    /// `Screen` case will not compile until it has been documented here.
    var help: ScreenHelp {
        switch self {

        case .dashboard:
            ScreenHelp(
                summary: """
                The money view for one market. It answers three questions: what did we \
                spend, what came back, and how does this month compare with last month \
                and with the year so far.
                """,
                source: """
                Banked per-day account totals, plus the console months you imported for \
                the earlier part of the year. Every period band on this screen reads that \
                same history, so the rows can be compared directly.
                """,
                steps: [
                    "Pick the market in the toolbar. Every figure here belongs to that market alone.",
                    "Read the top band first — that is the current month.",
                    "The bands under it are last month and the year so far.",
                    "Scroll to the day grid to see which days are banked. A pale square is a day with no data.",
                    "Hover a bar in the monthly chart for that month's exact spend and sales.",
                ],
                notes: [
                    "Spend, sales, orders and ACOS are exact. Profit is modeled — treat it as a good estimate, not a filed number.",
                    "The most recent day or two is still being attributed by Amazon. Those figures will grow after the fact.",
                    "Amazon keeps about 95 days of reporting. Anything older than the first banked day cannot be recovered, so year to date stays partial until a full year is banked.",
                    "A band that says unavailable is being honest. It means the data cannot cover that window — not that the figure was zero.",
                ])

        case .allMarkets:
            ScreenHelp(
                summary: """
                Every market in this account family side by side. Use it to see where \
                the money goes across the whole account, rather than inside one market.
                """,
                source: """
                Each market's own database, rolled up over the trailing 30 days and the \
                year so far. Markets are grouped by currency, because pounds and euros \
                must never be added together.
                """,
                steps: [
                    "Scan the currency groups. Each group totals only the markets that share that currency.",
                    "Hover a bar for the exact spend and attributed sales.",
                    "Double-click a market to switch the whole app over to it.",
                ],
                notes: [
                    "Only US and UK can have their year extended with imported console history. One console export covers every marketplace and carries no country, so DE, FR, ES and IT share a single merged euro series.",
                    "The EU markets only began advertising on 24 June 2026. A shorter history there is expected, not a fault.",
                ])

        case .health:
            ScreenHelp(
                summary: """
                Whether the machine behind the app is still running. Come here when a \
                number looks frozen, a rule went quiet, or the app seems a day behind.
                """,
                source: """
                Opens every market's database directly, plus the status file the nightly \
                job writes when it finishes.
                """,
                steps: [
                    "Read the last nightly run line first. If it failed, it names the market and the step that broke.",
                    "Check the Data through column per market — it is the worst of the three performance tables. The Issues column names any table that is stuck.",
                    "Read the per-day history column when a rule with a rolling window stopped acting — a window with holes makes the rule refuse to write.",
                    "If KILL or the approval gate is on, this screen says so.",
                ],
                notes: [
                    "Each performance table is filled by its own Amazon report job. They fail independently and drift apart, which is exactly why all three are shown.",
                    "More than three days behind freezes writes on purpose. The engine would rather do nothing than act on stale evidence.",
                    "EU markets sit about two days behind by design. Two or three days there is a normal morning.",
                    "A market with fewer banked days than US is not an error. The EU markets started later.",
                ])

        case .campaigns:
            ScreenHelp(
                summary: """
                The full browse path: campaigns, then their ad groups, then the targets \
                and search terms inside them. Most hands-on work happens here.
                """,
                source: "Last night's snapshot from the local database, over the trailing 30 days.",
                steps: [
                    "Filter or search to find a campaign.",
                    "Click a campaign to chart it. Use Show Ad Groups to drill in.",
                    "From an ad group, open Targets & Search Terms to see what people actually typed.",
                    "Right-click any row for its actions: pause, enable, edit budget, edit bid.",
                    "Select several rows to act on them together. Bulk actions always ask first.",
                ],
                notes: [
                    "Archiving is permanent. Amazon has no un-archive, so the campaign leaves the console for good. Pausing is the reversible choice.",
                    "Every action goes through the engine's safety rails and lands in the Audit Trail.",
                    "The state shown is last night's. Use Live Status to see what Amazon says right now.",
                ])

        case .targets:
            ScreenHelp(
                summary: """
                Every keyword and product target in the account in one sortable table. \
                Use it to find waste that hides inside individual campaigns.
                """,
                source: """
                The nightly targets mirror for bids, and the trailing-30 targeting \
                report for performance.
                """,
                steps: [
                    "Sort by spend to put the money first.",
                    "Narrow with the campaign scope. The trend chart above follows your scope.",
                    "Right-click a row for Edit Bid, Pause, or Enable.",
                    "Select many rows to set a bid or pause them in one pass.",
                ],
                notes: [
                    "The bid column is the target's OWN bid. A small 'ag' marker means it has none of its own — the number shown is the ad group's default, which rules the auction.",
                    "Bid writes are clamped by the ceiling in Settings, and the ceiling always wins.",
                ])

        case .liveStatus:
            ScreenHelp(
                summary: """
                Ask Amazon directly. Every other screen shows last night's snapshot; this \
                one makes a live call right now.
                """,
                source: "Look Up reads last night's local snapshot. Refresh from Amazon makes the live API call.",
                steps: [
                    "Type or paste an ASIN and press Look Up.",
                    "Tick All markets to look the design up in every market at once.",
                    "Pause or enable ad groups straight from the result.",
                ],
                notes: [
                    "This is the tool for the question \"did that pause actually go through?\"",
                    "It also heals the local mirror when the app's cached state has drifted from Amazon.",
                ])

        case .killList:
            ScreenHelp(
                summary: """
                The designs worth stopping. Bleeding converts too poorly to ever pay for \
                itself. Stale gets seen plenty and never clicked.
                """,
                source: """
                Trailing-30 performance measured against each design's OWN break-even \
                ACOS, computed from its list price and royalty.
                """,
                steps: [
                    "Read the two lists. Each row carries CVR, ACOS and that design's own break-even.",
                    "Pause a single row, or select several and pause them together.",
                ],
                notes: [
                    "A design has to fail on both counts to be listed: conversion under the floor AND ACOS above its own break-even.",
                    "Designs in a 30-day price transition, with unsupported prices, or sharing an ASIN cohort are excluded on purpose and counted as skipped.",
                    "A bleeder whose ad drives enough royalty on your OTHER designs to cover its own spend is spared, not killed — it shows in the green band up top, and the nightly auto-pause holds it back too. See Cross-purchase for the measured sales behind it.",
                    "An empty list is a normal result, not a broken screen. After a price change the US list stays quiet until the new price has 30 days behind it.",
                ])

        case .bidReport:
            ScreenHelp(
                summary: """
                Every bid the engine moved, with the reason it moved. Read it weekly to \
                check that the automation is behaving.
                """,
                source: "The write log, filtered to bid changes inside the window you pick.",
                steps: [
                    "Set the window at the top. Seven days is the usual read.",
                    "Check the up count, the down count and the net change first.",
                    "Scan the rows for any reason you did not expect.",
                ],
                notes: [
                    "This is history, not a plan. Nothing on this screen writes anything.",
                ])

        case .profit:
            ScreenHelp(
                summary: """
                The screen ACOS cannot give you. It sets each design's royalty against \
                its ad spend, per design and per product type.
                """,
                source: """
                Trailing-30 orders combined with per-ASIN royalty. Where recent sales exist \
                it uses the royalty from that period; otherwise it falls back to the current \
                modeled royalty and discloses that.
                """,
                steps: [
                    "Read the product-type table first. It is short and it shows where money is actually made.",
                    "Sort the design table by profit to see the worst and the best.",
                    "Use royalty ROI as the bid signal: above 1.5 means room to bid up, below 1 means ads cost more than the royalty they earn.",
                    "Export to CSV if you want to work on it outside the app.",
                ],
                notes: [
                    "Only ad-attributed sales are counted. Organic sales are not here — see Organic Halo and Cross-purchase for that side of the picture.",
                    "Spend on multi-ASIN cohorts cannot be assigned to one design, so it is left out of these figures rather than presented as profit. The excluded amount is stated under the totals.",
                    "Coverage on the top band is the share of spend that CAN be assigned to a single design.",
                ])

        case .accumulatedAsins:
            ScreenHelp(
                summary: """
                One row per ASIN, summed over every campaign it appears in. It catches \
                designs that look harmless everywhere and lose money in total.
                """,
                source: "Trailing-30 figures, rolled up across campaigns.",
                steps: [
                    "Sort by spend, then read the ACOS column down.",
                    "Select a row to see the per-campaign breakdown behind the total.",
                    "Right-click a bad ASIN to pause it everywhere it appears.",
                ],
                notes: [
                    "Only the trailing 30 days is available here. That is the window Amazon reports cumulatively, and no other window exists for it.",
                    "The count at the top is the true total, and every row is loaded.",
                ])

        case .accumulatedKeywords:
            ScreenHelp(
                summary: """
                The same idea as Accumulated ASINs, but for keyword text. A term can look \
                acceptable in one campaign and be a disaster across ten.
                """,
                source: "Trailing-30 figures, summed over every campaign the term runs in.",
                steps: [
                    "Sort by spend and read down.",
                    "Select a row to see which campaigns are spending on that term.",
                    "Right-click a bad term to negate it everywhere, exact or phrase.",
                ],
                notes: [
                    "Only the trailing 30 days is available here — the same cumulative window Amazon reports.",
                    "The count at the top is the true total, and every row is loaded.",
                ])

        case .watchlist:
            ScreenHelp(
                summary: """
                A private pinboard. Pin campaigns, ad groups, targets or ASINs and watch \
                them together as one combined trend.
                """,
                source: """
                Current figures for whatever you pinned. The pins themselves live on this \
                Mac only.
                """,
                steps: [
                    "This screen is retired from the sidebar. Pins made earlier still show; tables no longer offer a pin item.",
                    "Come back here to see them side by side with a combined summary.",
                    "Remove a pin when you stop caring about it.",
                ],
                notes: [
                    "This is the tool for babysitting a launch for a week without hunting through tables.",
                    "Pins never leave your machine and are never sent to Amazon.",
                ])

        case .rules:
            ScreenHelp(
                summary: """
                Write your own automation in plain language. Rules can raise and lower \
                bids, pause things, and add negatives — using real economics like \
                break-even, royalty and profit, not just ACOS.
                """,
                source: "The same data and the same economics the built-in nightly phases use.",
                steps: [
                    "Start from a template, or create a new rule.",
                    "Press Validate. It checks the syntax, the field names and the action verbs.",
                    "Press Preview (⌘↩). Preview is read-only and never touches Amazon.",
                    "Read the trace on each proposed change to see which condition fired and on what number.",
                    "Save it, then pick its mode: Auto applies on the nightly run, Review queues its changes for you in the Approval Queue.",
                ],
                notes: [
                    "A rule whose economics are unavailable refuses to act. It fails closed on purpose.",
                    "Rules refuse to write on stale data, exactly like the built-in phases do.",
                    "A rolling window ends two days before today, because the freshest days are still being attributed and would read as a collapse in sales.",
                    "Run & apply now writes to the live account. Every write is capped by the max-bid ceiling and logged to the Audit Trail.",
                ])

        case .strategyBuilder:
            ScreenHelp(
                summary: """
                A guided two-step pass over your search terms: promote the ones that \
                convert, negate the ones that only spend.
                """,
                source: "Search-term performance from the latest snapshot.",
                steps: [
                    "Work the promote list first. Tick the terms worth their own exact-match keyword.",
                    "Then work the negate list. Tick the terms that spend without converting.",
                    "Apply each side. Both write to Amazon and both are logged.",
                ],
                notes: [
                    "Negatives can be undone from the Audit Trail.",
                    "Promoting creates keywords on Amazon. Check the Audit Trail afterwards to confirm.",
                ])

        case .demandFeed:
            ScreenHelp(
                summary: """
                Design input, not ad management. It shows what customers actually searched \
                for, and which recent designs earned the most.
                """,
                source: """
                The demand feed the engine builds from converting search terms and recent \
                top earners.
                """,
                steps: [
                    "Read the keyword seeds for phrases worth designing for.",
                    "Read the proven sellers for designs worth making variations of.",
                    "Press Refresh to rebuild it from the latest data.",
                ],
                notes: [
                    "Nothing on this screen writes to Amazon.",
                ])

        case .seasonal:
            ScreenHelp(
                summary: """
                Tag designs that only sell at a certain time of year. The nightly job then \
                pauses them out of season and turns them back on ahead of the season.
                """,
                source: "A local config of season windows, plus the ASIN tags you set here.",
                steps: [
                    "Use Scan Titles to find seasonal designs automatically by keyword.",
                    "Review the suggestions and tag the ones that are genuinely seasonal.",
                    "Import a CSV to tag a curated list the title scan would miss.",
                    "Check the preview — it shows exactly what would pause and what would re-enable right now.",
                    "Press Apply Now if you do not want to wait for tonight's run.",
                ],
                notes: [
                    "Resume dates lead the selling season by roughly two months, so ads have time to ramp before people buy.",
                    "Re-enabling only touches ad groups this feature paused. It never resurrects something you paused for poor performance.",
                ])

        case .halo:
            ScreenHelp(
                summary: """
                Does advertising a design lift its ORGANIC sales? This compares each \
                advertised design's royalty per day after ads started against its own \
                pre-ad baseline.
                """,
                source: """
                The dated Merch sales report — the only source of organic royalty. The Ads \
                API reports ad-attributed sales only.
                """,
                steps: [
                    "Import a recent Merch sales report first, on the Import screen.",
                    "Read Halo est. as the lift, and Net halo as that lift minus the ad spend.",
                    "Check the flags column before believing any row.",
                ],
                notes: [
                    "The estimate is an UPPER BOUND. It is correlational, not causal — something other than your ads may have moved those sales.",
                    "The flags matter: never served, no ad traffic, and peak before ad all mean the row cannot support a conclusion.",
                    "US only. The other markets have no equivalent report.",
                ])

        case .crossPurchase:
            ScreenHelp(
                summary: """
                Measured halo. A shopper clicked one design's ad and bought a DIFFERENT \
                design. Amazon attributes it, but the campaign and targeting reports credit \
                it nowhere.
                """,
                source: "Amazon's purchased-product report, split into own-SKU and other-SKU sales.",
                steps: [
                    "Read the totals: how much attributed money came from a design other than the one advertised.",
                    "Look down the design rows to find the ones that sell the rest of the catalogue.",
                    "The pairs table shows which design led to which.",
                ],
                notes: [
                    "This is Amazon's own attribution, so it is measured rather than estimated. But it only counts sales that followed an ad click.",
                    "Nothing appears until the nightly job has banked the first purchased-product snapshot.",
                    "A design can read as a money-loser on Profit while quietly selling other designs here.",
                ])

        case .reports:
            ScreenHelp(
                summary: """
                An account rollup for any date range you choose, with the derived ad \
                metrics and a per-day CSV export.
                """,
                source: "The banked per-day account totals.",
                steps: [
                    "Pick a start and an end date. The line under the picker says which days are actually banked.",
                    "Press Generate.",
                    "Read the totals, then export the per-day rows if you need them elsewhere.",
                ],
                notes: [
                    "You cannot report on days that were never banked. The available range is stated rather than guessed.",
                ])

        case .actions:
            ScreenHelp(
                summary: """
                The engine's control panel: the emergency freeze, the approval gate, and \
                the manual run triggers.
                """,
                source: "Engine state files and the nightly run scripts.",
                steps: [
                    "KILL freezes every write to Amazon — from every screen and from the nightly job. Reach for it first when something looks wrong.",
                    "The approval gate makes the nightly job COLLECT negatives, pauses and harvest prunes instead of applying them. They then wait in the Approval Queue.",
                    "Reset bids previews the inflated bids first, and only applies them once you accept.",
                    "Backfill daily history pulls the per-day series Amazon still retains.",
                    "Run triggers one phase, or a full market run, by hand.",
                ],
                notes: [
                    "KILL is the safety switch. Nothing writes while it is on.",
                    "A manual run writes to the LIVE account exactly like the nightly one does.",
                    "The approval gate does not stop everything: nightly bid changes and rules set to Auto still apply themselves. Set a rule to Review to queue it too.",
                ])

        case .approvals:
            ScreenHelp(
                summary: """
                The gate. With approval mode on, the negatives and pauses the automation \
                wants to apply wait here for your decision.
                """,
                source: "The pending plan collected by the nightly run, or a fresh evaluation.",
                steps: [
                    "Choose the Phase-2 list or the Rules list with the picker at the top.",
                    "Tick what you agree with. Select All and Deselect All are there for speed.",
                    "Press Apply to send ONLY the approved subset to Amazon.",
                    "On the Rules list, discard the rest so the queue does not carry stale suggestions. The Phase-2 list recomputes fresh each load.",
                ],
                notes: [
                    "Nothing in this queue has been applied yet. That is the whole point of the screen.",
                    "Added negatives can be undone from the Audit Trail, like pauses and bid changes.",
                ])

        case .harvest:
            ScreenHelp(
                summary: """
                Two halves of one job. Promote the search terms that are converting into \
                their own exact-match keywords, and pause the harvested keywords that \
                turned out wasteful.
                """,
                source: """
                The search-term winners the engine collected, plus the prune plan it builds \
                from harvested-exact performance.
                """,
                steps: [
                    "Read the winners list. Terms marked pending have not been promoted yet.",
                    "Tick the ones worth their own keyword and promote them.",
                    "Work the prune list separately: tick the wasteful keywords and pause them.",
                ],
                notes: [
                    "Promoting creates campaigns and keywords on the live account.",
                    "Both sides are blocked while KILL is on.",
                ])

        case .dataImport:
            ScreenHelp(
                summary: """
                One Import tab for every file you drop, split into three sub-tabs. \
                New Designs builds campaigns from a catalogue export; Sales banks the \
                Merch sales report; Ads banks the console monthly-history export.
                """,
                source: """
                The products export (snap-grid-export-*.csv from the Snap for MOD \
                extension; a MerchFlow export_products_*.csv is still read), the dated \
                Merch sales report, and the monthly history export from the ads console. Each \
                sub-tab recognizes its own file. A catalogue export dropped on a data \
                tab switches to New Designs; a data file on the wrong data tab is \
                pointed at the right one.
                """,
                steps: [
                    "New Designs: drop the products export (snap-grid-export-*.csv), set the recency window, tick what to build, and press Build. There is a build-everywhere option for all markets.",
                    "Sales: drop the Merch sales report. Read the coverage section for banked days and gaps; the imports list shows every file banked.",
                    "Ads: drop the console monthly-history export. The banked summary counts months across the whole account.",
                    "Each sub-tab shows its own \"Last recorded\" date and a collapsed \"How to get this file\" guide.",
                ],
                notes: [
                    "Building in New Designs writes to the live account. Banking in Sales or Ads does not.",
                    "The Merch sales report is the only source of organic royalty. The Ads API reports ad-attributed sales only.",
                    "The console monthly export is the only way past Amazon's ~95-day retention. Once banked, it is the only copy.",
                    "Every import ADDS to the history instead of replacing it.",
                ])

        case .audit:
            ScreenHelp(
                summary: """
                Every write this app or the engine ever made to Amazon, newest first.
                """,
                source: "The engine's write log.",
                steps: [
                    "Scan the top of the list after any action to confirm it landed.",
                    "Filter to narrow it down to one kind of action.",
                    "Press Undo where it is offered.",
                ],
                notes: [
                    "Undo reverses ONE logged write: a pause becomes an enable, a bid returns to its previous value.",
                    "Created keywords and archived campaigns cannot be undone. A negative can be, when its row carries the created id; older or bulk-applied negatives offer no Undo. Where undo is impossible, the button is simply not offered rather than failing halfway.",
                ])

        case .errors:
            ScreenHelp(
                summary: """
                Everything wrong right now, in one place: failed engine calls, a closed \
                economics gate, stale data, KILL state, and the engine's own alerts.
                """,
                source: "The app's own failed calls, plus the engine's alert list.",
                steps: [
                    "Read the list top down. Each issue says what is wrong and where to go.",
                    "Open Details for the full message.",
                    "Re-pull is offered when the fix is simply fresher data.",
                    "Dismiss an issue you have handled, or clear the resolved ones in one go.",
                ],
                notes: [
                    "An empty screen here is the good outcome.",
                    "Stale data fires at four days behind — the same threshold at which the engine freezes writes. There is one threshold everywhere.",
                ])

        case .productRoyalty:
            ScreenHelp(
                summary: """
                What every Merch product earns you, and what you can edit. These \
                royalties are the money the whole app reasons with: break-even ACOS, \
                the kill list, true profit, and every bid or pause rule read them.
                """,
                source: """
                United States numbers ship with the engine and are the ones you \
                confirm off your Merch dashboard; your edits are saved on this Mac \
                (royalty_overrides.json) and merged on top. Every other market \
                DERIVES its royalties from the product export, so those are shown \
                read-only.
                """,
                steps: [
                    "Pick a row and change its royalty and list price. Break-even is worked out for you — never typed.",
                    "“Reset to built-in” drops your edit and puts the shipped number back.",
                    "Add a price the ladder does not have yet, or a product type the engine has not met.",
                ],
                notes: [
                    "Break-even ACOS is royalty divided by price. Above it, an order loses money.",
                    "A royalty that cannot be real is refused — nothing is saved when the numbers do not add up.",
                    "If a saved royalty ever becomes unreadable, economics fail closed: the engine refuses money decisions rather than guessing.",
                    "Tees priced under the growth floor are ACTED ON at floor economics on purpose — a rank push would otherwise pause the very campaigns it is meant to feed.",
                ])

        case .kdpBooks:
            ScreenHelp(
                summary: """
                Each KDP book's royalty, one row per ASIN. This is the book economics \
                the rest of the app leans on: break-even, true profit, and the kill \
                list all read the royalty you enter here.
                """,
                source: """
                A local config file on this Mac (kdp_books.json) holds the numbers you \
                type in. Titles are fetched from Amazon on demand, and the Advertising \
                badge comes from the nightly pull.
                """,
                steps: [
                    "Add a book at the bottom: paste the ASIN, its list price, and the royalty.",
                    "Enter the royalty straight off your KDP dashboard — that is the most accurate figure.",
                    "Break-even is computed for you from the royalty and the list price.",
                    "Remove a book to clear its entry. Its economics then read as unavailable again.",
                ],
                notes: [
                    "This screen only appears for a KDP advertiser account. Merch designs get their economics from the catalogue export instead.",
                    "A book with no royalty entered fails closed — its economics read as unavailable, never guessed, until you fill it in.",
                    "The print-cost compute path (format, pages, ink) lives in the CLI: appctl kdp-book. This screen takes the dashboard-royalty path.",
                ])

        case .settings:
            ScreenHelp(
                summary: """
                The app's configuration in one place: where the engine lives, which \
                python it runs, how actions confirm, and the per-market bid ceilings.
                """,
                source: """
                Local settings stored on this Mac, plus the engine's own max-bid ceiling.
                """,
                steps: [
                    "Engine folder points at the repo folder: the databases sit there, and appctl.py sits in its engine/ subfolder. The badge says whether it was found.",
                    "Leave the python path empty to resolve python3 from your login shell — normally the same Homebrew python3 the nightly job runs.",
                    "Max bid ceiling caps every bid and daily budget written for the selected market — manual, bulk, and nightly alike.",
                ],
                notes: [
                    "The app never reads the engine's .env. Secrets stay there.",
                    "The ceiling always wins: a bid above it is written at the ceiling and shown as adjusted in the Audit Trail.",
                ])
        }
    }
}
