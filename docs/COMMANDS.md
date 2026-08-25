# Command reference

Everything you can run, and whether it can change your Amazon account.

---

## How to read this page

Every command is one of three kinds:

| Kind | Meaning |
|---|---|
| 🟢 **Read** | Local database only. No network, no writes. Safe any time. |
| 🔵 **Live read** | Calls Amazon to read. Slower, needs `requests`. Changes nothing. |
| 🔴 **Write** | **Can change your Amazon account.** Gated by the kill switch, the freshness gate, the economics gate and the bid ceiling. Logged to the audit trail. |

Everything runs from the repository folder.

---

## The two ways in

### The phase scripts

```bash
ADS_MARKET=US python3 engine/phase3_bids.py            # preview — writes nothing
ADS_MARKET=US python3 engine/phase3_bids.py --apply    # actually applies
```

**Every phase previews by default.** `--apply` is always opt-in. `--auto` additionally
skips the interactive confirmation, which is what the nightly job uses.

### `appctl.py` — the JSON API

```bash
ADS_MARKET=DE python3 engine/appctl.py metrics
```

Prints exactly one JSON object: `{"ok": true, "data": ...}` or `{"ok": false, "error": "..."}`.

`ADS_MARKET` picks the marketplace and defaults to `US`. A few commands look at every
market at once and should be run **without** it: `health`, `overview`.

Pipe through `python3 -m json.tool` (or `jq`) to read the output comfortably:

```bash
ADS_MARKET=US python3 engine/appctl.py metrics | python3 -m json.tool
```

---

## Getting started

| Command | | What it does |
|---|---|---|
| `python3 engine/get_token.py` | 🔵 | Walks you through Amazon login and prints your refresh token. |
| `python3 engine/list_profiles.py` | 🔵 | Lists the advertising profile ids your account can see. |
| `python3 engine/inspect_accounts.py` | 🔵 | Lists the advertiser accounts you have access to. |
| `python3 engine/appctl.py markets` | 🟢 | Which markets are configured and which have data. |
| `python3 engine/appctl.py health` | 🟢 | **Run this first when anything looks wrong.** Per-market data freshness, stale tables, last run status. Run without `ADS_MARKET`. |

---

## Safety controls

| Command | | What it does |
|---|---|---|
| `touch KILL` | 🟢 | **Freeze every write.** |
| `rm KILL` | 🟢 | Unfreeze. |
| `appctl.py kill` | 🟢 | Report the kill switch state. |
| `appctl.py kill --on` / `--off` | 🔴 | Set it from the command line. |
| `appctl.py approval-mode` | 🟢 | Is approval mode on? |
| `appctl.py approval-mode --on` / `--off` | 🔴 | Nightly run proposes instead of applying. |
| `appctl.py maxbid` | 🟢 | Show the per-market bid and budget ceilings. |
| `appctl.py maxbid --set --target 0.50 --keyword 0.50 --budget 20` | 🔴 | Set them. **Do this before your first live run.** |
| `appctl.py maxbid --clear` | 🔴 | Remove them. |
| `appctl.py econ-gate` | 🟢 | Is the economics gate open? A closed gate blocks every economics-driven write. |
| `appctl.py portfolio-cap` | 🟢 | The account-wide spend cap. |
| `appctl.py change-cap` | 🟢 | How many changes one automatic rules run may apply here. Default **500 per market**. |
| `appctl.py change-cap --set 800` | 🔴 | Raise it. `--set 0` means no cap; `--clear` restores 500. |

---

## Looking at your account

### Money

| Command | | What it does |
|---|---|---|
| `appctl.py metrics` | 🟢 | The headline numbers. Use `trailing30` as your stable ACOS. |
| `appctl.py periods` | 🟢 | This month, last month, year to date, last year, all time. Spend and sales are exact; **profit is modelled** and the reply says so. |
| `appctl.py monthly` | 🟢 | Calendar months from banked per-day history. |
| `appctl.py daily [--days 30]` | 🟢 | Per-day account totals. |
| `appctl.py report --start 2026-01-01 --end 2026-03-31` | 🟢 | Any custom timeframe. |
| `appctl.py profit` | 🟢 | Royalty-aware true margin per design. Not ACOS — actual money. |
| `appctl.py overview` | 🟢 | Every market in one rollup. Run without `ADS_MARKET`. |
| `appctl.py synccal` | 🟢 | Heat grid of which days are banked. |

### Product economics

Every bid, pause and negative is judged against a design's own break-even ACOS, so the
royalty behind it decides what the automation does. These are local config only — no
Amazon call — and the break-even is always **computed** from royalty ÷ price, never typed.

| Command | | What it does |
|---|---|---|
| `appctl.py royalties` | 🟢 | Every royalty the engine prices with, and where each number came from: `built-in`, `derived` from your export, or `operator` for one you entered. |
| `appctl.py royalty-set --type mug --price 18.99 --royalty 3.00` | 🟢 | Enter the real royalty for a product type. **Works in every market** — an operator number always beats a derived median. |
| `appctl.py royalty-set --price 25.99 --royalty 10.07` | 🟢 | Set one rung of the US tee price ladder. US only; every other market prices one royalty per product type. |
| `appctl.py royalty-clear [--type X \| --price P]` | 🟢 | Drop an override and fall back to the shipped or derived figure. |

A value that cannot be a real royalty — zero, negative, or at or above the price — is
refused and nothing is written. The Mac app's **Product Royalty** tab does the same thing
with a form, which is the easier way in.

### Structure

| Command | | What it does |
|---|---|---|
| `appctl.py campaigns [--type standard\|lottery\|scavenger\|harvested] [--state ENABLED\|PAUSED]` | 🟢 | Your campaigns. |
| `appctl.py adgroups --campaign <id>` | 🟢 | Ad groups in one campaign. |
| `appctl.py targets --adgroup <id>` | 🟢 | Targets in one ad group. |
| `appctl.py targets --adgroup <id> --live` | 🔵 | Same, plus each target's live bid and state from Amazon. |
| `appctl.py alltargets [--limit N]` | 🟢 | Every target in the account, flat. `bid_inherited: true` means no own bid — the ad-group default rules the auction. |
| `appctl.py searchterms --adgroup <id>` | 🟢 | What people actually searched, spend-sorted. |
| `appctl.py asin B0XXXXXXX` | 🟢 | Everywhere one design appears. |
| `appctl.py negatives --adgroup <id>` | 🟢 | Negative keywords on one ad group. |
| `appctl.py accumulated-asins [--limit 0]` | 🟢 | One row per ASIN, summed across every campaign. `--limit 0` returns everything. |
| `appctl.py accumulated-keywords [--limit 0]` | 🟢 | Same, per keyword text. |
| `appctl.py history --campaign\|--adgroup\|--target <id>` | 🟢 | Banked series for one entity. **Read the `basis` field** — `daily` is real days, `trailing30_snapshot` is overlapping totals. |
| `appctl.py campaigndaily --campaigns a,b,c` | 🟢 | Per-day series over specific campaigns. |

### Finding problems

| Command | | What it does |
|---|---|---|
| `appctl.py killlist` | 🟢 | Designs below the CVR floor **and** over their own break-even ACOS. Excludes designs in a price transition or in a multi-ASIN cohort, and says so. |
| `appctl.py stale` | 🟢 | Enabled designs with impressions, no clicks, no sales. |
| `appctl.py alerts` | 🟢 | Spend spikes, budget maxed, kill candidates, stale data. |
| `appctl.py bidreport [--days 7]` | 🟢 | What moved, and why. |
| `appctl.py harvest` | 🟢 | Search terms that converted and deserve their own keyword. |
| `appctl.py crosspurchase` | 🟢 | What ad-attributed buyers also bought. |
| `appctl.py halo [--min-spend 1] [--limit 300]` | 🟢 | Estimated organic lift from advertising, per design, across every campaign type. **US only. Upper bound, correlational, not causal.** |

---

## Changing things

**Every command below can spend your money.**

### One thing at a time

| Command | | What it does |
|---|---|---|
| `appctl.py pause --adgroup <id>` | 🔴 | Pause one ad group. Reversible. |
| `appctl.py enable --adgroup <id>` | 🔴 | Enable it again. |
| `appctl.py pause-campaign --campaign <id>` | 🔴 | Pause a campaign. Reversible. |
| `appctl.py enable-campaign --campaign <id>` | 🔴 | Enable it. |
| `appctl.py pause-target --target <id>` | 🔴 | Pause one targeting clause. |
| `appctl.py enable-target --target <id>` | 🔴 | Enable it. |
| `appctl.py setbid --target <id> --bid 0.35 [--prev 0.28]` | 🔴 | Change one bid. Pass `--prev` so undo works. Clamped by the ceiling. |
| `appctl.py setbudget --campaign <id> --budget 15` | 🔴 | Change a daily budget. Clamped by the ceiling. |
| `appctl.py negate --campaign X --adgroup Y --term "..."` | 🔴 | Add one negative-exact keyword. |
| `appctl.py archive-campaign --campaign <id> --confirm` | 🔴 | **PERMANENT. Amazon has no un-archive.** The campaign leaves the console for good and cannot be undone. **Pause instead.** |
| `appctl.py everywhere-preview` | 🟢 | Resolve an "act everywhere" selection (one ASIN or keyword, every place it appears) to the exact instances it would touch. Writes nothing. JSON on stdin. |
| `appctl.py everywhere-apply` | 🔴 | Apply that selection everywhere: pause, set a bid, or add negatives. The plan is re-resolved against fresh state first, so stale ids are never sent. |
| `appctl.py harvest-suggest --term "..."` | 🟢 | Designs across the whole catalogue whose titles match a winning search term — including ones never advertised. |
| `appctl.py harvest-promote-group [--apply]` | 🔴 | Promote one winning term onto a chosen family of designs. Dry run without `--apply`. |

### The phases

| Command | | What it does |
|---|---|---|
| `phase0_pull.py` | 🔵 | Download reports into SQLite. Read-only towards Amazon; the slow step. |
| `map_products.py` | 🟢 | Resolve ASINs to product types and prices. |
| `phase1_dryrun.py` | 🟢 | What would change. |
| `phase2_apply.py [--apply]` | 🔴 | Reactive negatives and pauses. |
| `phase3_bids.py [--apply]` | 🔴 | Bid changes against each design's economics. |
| `phase4_harvest_create.py [--apply]` | 🔴 | Promote keyword winners. |
| `phase4b_harvest_asins.py [--apply]` | 🔴 | Promote ASIN winners. |
| `harvest_prune.py [--apply]` | 🔴 | Pause wasteful harvested keywords. |
| `lottery_build.py [--apply]` | 🔴 | Build and fill lottery campaigns. |
| `scavenger_build.py [--apply]` | 🔴 | Add new ASINs to typed cohort campaigns. |
| `scavenger_optimize.py [--apply]` | 🔴 | Prune and retire. |
| `seasonal_pause.py [--apply]` | 🔴 | Pause and re-enable seasonal designs. No-op until designs are tagged. |
| `reset_inflated_bids.py [--apply]` | 🔴 | Pull runaway bids back down. |
| `appctl.py run [--phase pull\|phase2\|phase3\|harvest\|promote]` | 🔴 | Trigger a phase or a full market run. |
| `appctl.py run-status` | 🟢 | Is a run in progress, and did the last one succeed? |

### The approval queue

| Command | | What it does |
|---|---|---|
| `appctl.py negatives-preview` | 🟢 | Proposed negatives and pauses. |
| `appctl.py negatives-apply` | 🔴 | Apply an approved subset. Takes JSON on stdin. |
| `appctl.py harvest-prune` | 🟢 | Proposed keyword pauses. |
| `appctl.py harvest-prune-apply` | 🔴 | Apply an approved subset. |
| `appctl.py promote` | 🔴 | Promote harvest winners. JSON on stdin scopes it; empty stdin means all pending. |
| `appctl.py resetbids [--apply]` | 🔴 | Preview or apply a bid reset. |

---

## Your own rules

The DSL lets you write automation in near-English instead of editing the engine. Full
guide: **[rules-dsl.md](rules-dsl.md)**.

| Command | | What it does |
|---|---|---|
| `appctl.py rules-list` | 🟢 | Your rules and their modes. |
| `appctl.py rules-get --rule "Name"` | 🟢 | One rule's text. |
| `appctl.py rules-validate --rule "Name"` | 🟢 | Syntax **and** semantics. Unknown fields are rejected here, not discovered as a silent no-op weeks later. |
| `appctl.py rules-preview --rule "Name"` | 🟢 | What it would change, with a per-condition trace. **Never executes.** |
| `appctl.py rules-save` | 🟢 | Save a rule. JSON on stdin. Rejects anything unparseable. |
| `appctl.py rules-delete --rule "Name"` | 🟢 | Delete it. |
| `appctl.py rules-run --rule "Name" [--apply]` | 🔴 | Preview, or apply for real. |
| `appctl.py rules-nightly` | 🔴 | What the nightly job runs: AUTO rules apply, REVIEW rules queue. |
| `appctl.py rules-collect` | 🟢 | Re-evaluate REVIEW rules into the pending queue. |
| `appctl.py rules-pending` | 🟢 | What is waiting for approval, including conflicts. |
| `appctl.py rules-approve` | 🔴 | Execute an approved subset. JSON on stdin. |
| `appctl.py rules-discard` | 🟢 | Drop pending proposals. |

Rules live in `rule_defs/`, which is gitignored — they are your data, not code.

---

## Seasonal scheduling

Tag a design with a season and its ads pause outside that season's window, then come back
before the sales period starts.

| Command | | What it does |
|---|---|---|
| `appctl.py seasons` | 🟢 | Configured seasons and what is tagged. |
| `appctl.py season-define --name halloween --label "Halloween" --resume 08-15 --pause 11-05` | 🟢 | Add or update a window (MM-DD). |
| `appctl.py season-tag --asin B0XXXXXXX --season halloween` | 🟢 | Tag one design. `--clear` untags. |
| `appctl.py season-suggest [--apply]` | 🟢 | Guess seasonal designs from their titles. |
| `appctl.py season-tag-csv --csv list.csv --season halloween [--apply]` | 🟢 | Tag a curated list. |
| `appctl.py seasonal-preview` | 🟢 | What would pause and enable right now. |
| `appctl.py seasonal-apply` | 🔴 | Do it. Re-enabling only touches ad groups it paused itself — it never resurrects something you paused for performance. |

Your tags live in `seasonal.json`, which is gitignored. The repo ships
`seasonal.example.json` with the season windows but no ASINs, and it is copied into place
automatically on first use.

---

## Importing data

| Command | | What it does |
|---|---|---|
| `appctl.py sales-report` | 🟢 | Which Merch SALES_REPORT the engine is reading. |
| `appctl.py sales-report --import <csv>` | 🟢 | Import one. **The only source of organic royalty** — the Ads API reports ad-attributed sales only. |
| `appctl.py sales-history` | 🟢 | What organic history is banked, and where the gaps are. |
| `appctl.py history-import <csv>` | 🟢 | Bank a monthly account-history CSV from the Ads console. Reaches past Amazon's ~95-day API window. Once banked this is the only copy. |
| `appctl.py import-preview <csv> [--days 14]` | 🟢 | New designs from a catalogue export, routed to their campaign type. |
| `appctl.py import-apply <csv>` | 🔴 | Build campaigns for an approved subset. JSON on stdin. |
| `appctl.py adopt-export <csv>` | 🟢 | Adopt a newer catalogue export as the economics source. |
| `appctl.py catalog-cache` | 🟢 | Whether the banked copy of the merged catalogue matches the export files on disk. |
| `appctl.py catalog-cache --rebuild` | 🟢 | Rebuild it. Your catalogue is several export files merged at read time, and that merge is slow enough to matter — a US read drops from ~19s to ~2s. It is a **pure speed-up**: the cache carries a signature over the export files, and any mismatch makes every reader fall back to the CSVs. A stale or missing cache costs seconds, never an answer. Built by the nightly; you rarely run this by hand. |
| `appctl.py export-date` | 🟢 | The newest design-upload date **inside** the current catalogue export, read from the rows rather than the filename. Cached, because the scan takes ~18s over 2M rows. |
| `appctl.py backfill-daily [--days 92]` | 🔵 | Rebuild per-day history from Amazon. |

---

## Books (KDP)

| Command | | What it does |
|---|---|---|
| `appctl.py kdp-book` | 🟢 | List your books and their economics. |
| `appctl.py kdp-book --asin B0X --list-price 12.99 --royalty 4.55` | 🟢 | Enter a book. Take the royalty from your KDP dashboard — it is the most accurate source. |
| `appctl.py kdp-book --asin B0X --clear` | 🟢 | Remove an entry. |
| `appctl.py kdp-titles --refresh` | 🔵 | Fetch book titles from Amazon. The only title source for a book with no campaign. |

A book with no entry fails closed: economics report as unavailable rather than guessed.

---

## Audit and undo

| Command | | What it does |
|---|---|---|
| `appctl.py audit [--limit 100]` | 🟢 | Every write, newest first, with its previous value. |
| `appctl.py undo --row <rowid>` | 🔴 | Reverse one write. |
| `appctl.py digest --since 2026-08-01T00:00:00` | 🟢 | Counts per action since a timestamp. |
| `appctl.py bidhistory --target <id>` | 🟢 | Every bid change for one target. |

Not undoable: archived campaigns (permanent at Amazon), negatives created before the id
was logged, and newly created campaigns.

---

## Live lookups

| Command | | What it does |
|---|---|---|
| `appctl.py status B0XXXXXXX` | 🔵 | Live state from Amazon for one or more ASINs. |
| `appctl.py livestate B0XXXXXXX` | 🔵 | Same, structured, and heals the local mirror. |

---

## Utilities

| Command | | What it does |
|---|---|---|
| `appctl.py serve` | 🟢 | Long-running line protocol. The app uses this for fast reads. |
| `appctl.py watchlist` | 🟢 | Current metrics for the campaigns, ad groups, targets and ASINs you pinned in the app. JSON on stdin. |
| `appctl.py demandfeed [--refresh]` | 🔵 | Keyword seeds and proven sellers, for choosing what to make next. `--refresh` rebuilds it. |
| `appctl.py prune-snapshots` | 🟢 | Count performance-snapshot rows older than the retention window (400 days). Preview only. |
| `appctl.py prune-snapshots --apply` | 🟢 | Delete them. Local database only — no Amazon call. Runs itself on Mondays. Reports `0` until the account is over a year old. |
| `python3 engine/dashboard.py` | 🟢 | Write `outputs/dashboard.html`. |
| `python3 engine/weekly_report.py` | 🟢 | Weekly summary. |
| `python3 engine/notify_discord.py` | 🟢 | Post a digest to your webhook. Skipped while a `NO_DISCORD` file exists. |
| `python3 engine/catchup.py [--markets UK DE …]` | 🔵 | **After a missed night.** Asks every market for its reports, then collects in rounds until nothing is pending. Amazon builds reports slower than any one poll window, so asking and collecting are always two passes — the nightly hides that by collecting the next night, and a catch-up has no next night. Use this instead of re-running the pull by hand. |
| `bash scripts/install_launchd.sh [--hour N] [--uninstall]` | 🟢 | Install or remove the nightly job. Defaults to **01:00 Merch time (Seattle)**, converted to your local clock — that is the clock the engine anchors every market's "yesterday" to. `--hour` overrides with your own local hour. See [SETUP.md](SETUP.md#why-0100-seattle-and-why-that-is-right-for-every-marketplace). macOS only; on Windows see [WINDOWS.md](WINDOWS.md). |
| `bash scripts/package_app.sh [--install]` | 🟢 | Build the Mac app. macOS only — to build your own screen instead, see [BUILD-A-UI.md](BUILD-A-UI.md). |
| `python3 -m unittest discover -s tests -p '*_tests.py' -t .` | 🟢 | Run the whole test suite. |
| `python3 tests/run_all.py` | 🟢 | The same suite with a watchdog: a hang prints every stack and exits, instead of waiting forever. |

---

## Hourly data (Amazon Marketing Stream)

Amazon pushes hourly Sponsored Products figures into an SQS queue you own, instead of
making you request a report and wait. Setup, including the AWS console steps:
**[marketing-stream.md](marketing-stream.md)**.

| Command | | What it does |
|---|---|---|
| `appctl.py stream-status` | 🔵 | Subscriptions, queue depth, and what has been banked. |
| `appctl.py stream-setup [--queue-url U]` | 🟢 | The queue names to create, and the access policy to paste. |
| `appctl.py stream-fields` | 🟢 | Which fields the banked payloads actually carry. |
| `appctl.py stream-today [--day D]` | 🔵 | This market's day so far: spend, clicks, placement split, spend by hour. |
| `appctl.py stream-advertisers [--refresh]` | 🟢 | Which advertising account each message belongs to, and how that was decided. |
| `appctl.py stream-subscribe --dataset sp-traffic\|sp-conversion` | 🔴 | Start the hourly push for one dataset. |
| `appctl.py stream-unsubscribe --subscription ID` | 🔴 | Archive a subscription. Amazon has no delete. |
| `appctl.py stream-verify [--day D]` | 🟢 | Compare one SETTLED day's Stream totals against the report, per campaign. Refuses a day Stream could not have seen whole, or one the report has not banked. Default = newest whole day. |
| `appctl.py stream-drain [--seconds N] [--realm NA\|EU]` | 🟢 | Empty the queues into `stream_data.sqlite`. Reads AWS, writes locally. Budget is per queue and defaults to 300s; it exits early when the queue is empty, and warns when it is not. |
| `python3 engine/stream_drain.py --status` | 🟢 | Same summary, without calling Amazon. |
| `bash scripts/install_stream_drain.sh [--app] [--uninstall]` | 🟢 | Install or remove the hourly pickup job. |

Five things worth knowing before you start:

- **Each dataset is published from a different Amazon AWS account.** One queue policy
  reused for both datasets drops every message of the second, with no error anywhere and
  the subscription still reading `ACTIVE`. Let `stream-setup` generate the policy.
- **A new subscription is asleep until the SNS handshake is answered.** It parks a
  confirmation message in your queue and sends nothing until a drain answers it. Run
  `stream-drain` once after subscribing.
- **Stream is not a substitute for the nightly pull.** A subscription starts the clock
  and sends little about the past. History and the Monday true-up stay with reports.
- **A Stream conversion is dated to the CLICK, not the purchase.** A message arriving
  tonight with a window six days old is normal — somebody clicked then and bought now.
  It belongs to that day, not today. `stream-today` keys on the ad day for exactly this
  reason, which is also what makes it comparable to `campaign_daily`. And because
  conversions land late and Amazon restates them, a day's sales figure only ever grows.
  That is why no ACOS is shown for a day in progress.
- **A day of Stream can have holes, and they are permanent.** Amazon sends nothing for
  an hour in which nothing happened, which is fine — but a delivery that never arrives
  is never resent either. `stream-today` reports which hours are missing and says the
  totals are an undercount. Never read a Stream day as a settled day; the nightly report
  is the source of truth for that.

---

## Running every market

```bash
for M in US UK DE FR ES IT; do
  ADS_MARKET=$M python3 engine/appctl.py metrics
done
```

The nightly job does this automatically for every market with a profile id in `.env`.
