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
ADS_MARKET=US python3 phase3_bids.py            # preview — writes nothing
ADS_MARKET=US python3 phase3_bids.py --apply    # actually applies
```

**Every phase previews by default.** `--apply` is always opt-in. `--auto` additionally
skips the interactive confirmation, which is what the nightly job uses.

### `appctl.py` — the JSON API

```bash
ADS_MARKET=DE python3 appctl.py metrics
```

Prints exactly one JSON object: `{"ok": true, "data": ...}` or `{"ok": false, "error": "..."}`.

`ADS_MARKET` picks the marketplace and defaults to `US`. A few commands look at every
market at once and should be run **without** it: `health`, `overview`.

Pipe through `python3 -m json.tool` (or `jq`) to read the output comfortably:

```bash
ADS_MARKET=US python3 appctl.py metrics | python3 -m json.tool
```

---

## Getting started

| Command | | What it does |
|---|---|---|
| `python3 get_token.py` | 🔵 | Walks you through Amazon login and prints your refresh token. |
| `python3 list_profiles.py` | 🔵 | Lists the advertising profile ids your account can see. |
| `python3 inspect_accounts.py` | 🔵 | Lists the advertiser accounts you have access to. |
| `python3 appctl.py markets` | 🟢 | Which markets are configured and which have data. |
| `python3 appctl.py health` | 🟢 | **Run this first when anything looks wrong.** Per-market data freshness, stale tables, last run status. Run without `ADS_MARKET`. |

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

### Structure

| Command | | What it does |
|---|---|---|
| `appctl.py campaigns [--type standard\|lottery\|scavenger\|tamas\|harvested] [--state ENABLED\|PAUSED]` | 🟢 | Your campaigns. |
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
| `appctl.py nudges` | 🟢 | Playbook reminders, including price targets. Reminders only — there is no pricing API. |
| `appctl.py bidreport [--days 7]` | 🟢 | What moved, and why. |
| `appctl.py harvest` | 🟢 | Search terms that converted and deserve their own keyword. |
| `appctl.py crosspurchase` | 🟢 | What ad-attributed buyers also bought. |
| `appctl.py halo` | 🟢 | Estimated organic lift from advertising. **US only. Upper bound, correlational, not causal.** |
| `appctl.py tamas-candidates` | 🟢 | Proven organic sellers worth a focused campaign. **US only.** |

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
| `python3 dashboard.py` | 🟢 | Write `outputs/dashboard.html`. |
| `python3 weekly_report.py` | 🟢 | Weekly summary. |
| `python3 notify_discord.py` | 🟢 | Post a digest to your webhook. Skipped while a `NO_DISCORD` file exists. |
| `bash scripts/install_launchd.sh [--hour N] [--uninstall]` | 🟢 | Install or remove the nightly job. |
| `bash scripts/package_app.sh [--install]` | 🟢 | Build the Mac app. |
| `python3 -m unittest discover -s tests -p '*_tests.py' -t .` | 🟢 | Run all 419 tests. |

---

## Running every market

```bash
for M in US UK DE FR ES IT; do
  ADS_MARKET=$M python3 appctl.py metrics
done
```

The nightly job does this automatically for every market with a profile id in `.env`.
