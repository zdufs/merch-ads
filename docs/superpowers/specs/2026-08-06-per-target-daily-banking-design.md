# Per-target daily banking — design

Date: 2026-08-06
Status: approved; probe confirmed the approach (see "Probe results")

## Why

`docs/review-2026-08-04.md` ranks this item 6 of 6 in "What to build next" and
calls it "the one real moat extension". It is the only entry on that list still
open. Items 1 through 5 all shipped.

The engine has daily history at two grains and not at the third:

| Grain | Table | Filled by | Status |
|---|---|---|---|
| Account, per day | `daily_totals` | `daily_metrics.py`, `backfill_daily.py` | exists |
| Campaign, per day | `campaign_daily` | `backfill_daily.py` | exists |
| Target, per day | — | — | missing |

Because the bottom rung is missing, the rules language cannot offer rolling
windows. `rules/parser.py:100` rejects `IN LAST N DAYS` at parse time, and the
error says why: the data is a trailing-30 snapshot, not per-day history.

The operator's stated goal for the app is that he writes the rules and the app
applies them automatically. Rolling windows are the missing piece of that: today
a rule can only ask how a target did over the last month. It cannot ask how the
target did last week, so a design that turned bad recently stays hidden behind
three good weeks.

## What the review got wrong

The review proposed getting there by diffing consecutive `targeting_perf`
snapshots. That does not work.

`phase0_pull.py:27-28` requests `spTargeting` as a SUMMARY report over a rolling
31-day window (`START = today-31`, `END = today-1`). Each stored row is a
trailing-31-day aggregate as of the pull date. So the difference between two
consecutive snapshots is *the day that entered the window minus the day that
left it*. That is not yesterday's performance.

The result would look plausible and be wrong. It would also break outright
whenever a night is missed, which the UK and DE report-generation failures show
does happen.

`docs/review-2026-08-04.md` should be corrected so nobody picks the diff idea up
again later.

## Approach

Ask Amazon for the per-day numbers directly, with a DAILY `spTargeting` report.

`backfill_daily.py` already proves this shape against `spCampaigns`: chunk the
window into pieces of 31 days or fewer, request the chunks in parallel, resume
through `report_jobs`, bank the rows. Pointing the same shape at `spTargeting`
gives true per-day per-target figures with nothing inferred.

It also backfills. Amazon's reporting retention reaches about 92 days, so the
history is available now instead of accumulating over the next three months.
Anything older than that is gone for good.

Two alternatives were considered and rejected:

- **Snapshot differencing.** Wrong, for the reason above.
- **Switching the existing nightly pull from SUMMARY to DAILY.** One report
  instead of two, which is genuinely cheaper. But it redefines what
  `targeting_perf` means, and that table feeds phase 2, phase 3, the DSL, the
  kill list, `db.snapshot_gate` and the lint tests. This is the same area where
  dating one perf table from another silently froze US bids for four nights,
  twice. Adding a table beside it is safer than rewriting the load-bearing one.

## Scope, as agreed with the operator

- Backfill the full 92 days, all six markets.
- Consumers are the rules DSL and the app's display. No new hardcoded phase
  logic.
- No pruning. Nothing in this engine prunes any table today, and this data is
  unrecoverable once Amazon drops it.

## Data layer

### Table

```sql
CREATE TABLE IF NOT EXISTS target_daily (
    date TEXT, campaign_id TEXT, ad_group_id TEXT,
    targeting TEXT, match_type TEXT, target_id TEXT,
    impressions INTEGER, clicks INTEGER, cost REAL,
    orders INTEGER, sales REAL, acos REAL, pulled_at TEXT,
    PRIMARY KEY (date, campaign_id, ad_group_id, targeting, match_type)
);
CREATE INDEX IF NOT EXISTS idx_target_daily_target
    ON target_daily(target_id, date);
CREATE INDEX IF NOT EXISTS idx_target_daily_adgroup
    ON target_daily(ad_group_id, date);
```

Same columns as `targeting_perf` plus `pulled_at`, so a reader of one can read
the other without translating.

The primary key copies `targeting_perf`'s rather than keying on `target_id`.
The probe found `keywordId` on all 20,277 rows it read, so `target_id` looks
reliable. The composite key is kept anyway: the probe covered three days of one
market, auto and product-targeting clauses may still behave differently, and
matching `targeting_perf` costs nothing. `target_id` gets an index instead,
which is what the reads actually need.

### Backfiller

New `backfill_target_daily.py`, modelled on `backfill_daily.py`:

- Window defaults to 92 days, capped there (retention is about 95).
- Chunked into pieces of 31 days or fewer, because Amazon caps DAILY reports.
- Chunks requested in parallel, then polled together.
- Resumable through `report_jobs`, keyed per chunk.
- Banks with `INSERT OR REPLACE`, so re-running is idempotent.
- Read-only against Amazon. It requests a report; it changes nothing.
- Polls for 40 minutes before giving up, matching `backfill_daily.py`'s
  `MAX_WAIT`. The probe's 3-day report took 24 minutes to generate, so a
  generous ceiling is not optional here. Because the chunk's report id is saved
  in `report_jobs`, a run that times out resumes rather than restarting.

### Attribution and the 2-day lag

Amazon's `sales30d` attributes a sale back to the click that earned it, for up
to 30 days after that click. A day's spend is final immediately. Its sales are
not. A day only really settles after about 30 days.

This has a live-money consequence. A rule reading a recent window sees complete
spend against incomplete sales, so every recent window looks worse than it was.
Rules that pause or cut bids would fire too eagerly, on the real account.

Three options were weighed:

1. Lag the window by 2 days. Cheap, matches how the dashboard already mutes its
   `settling` figure, and fails in the safe direction.
2. Age-weight the sales by expected remaining attribution. More accurate, more
   machinery to get wrong, and it reports numbers Amazon did not say.
3. Take the days as they come and document the edge.

**Decision: option 1.** `IN LAST 7 DAYS` means the 7 days ending 2 days ago. The
lag is a single named constant, `db.DAILY_ATTRIBUTION_LAG_DAYS = 2`, so it can
be changed in one place.

This does not fully solve the problem. A 2-day-old day is still only partly
attributed. It removes the worst of the distortion, and the weekly true-up below
fixes the rest over time.

### Nightly wiring

Report generation is slow. The probe's 3-day report took 24 minutes to
generate. That rules out a separate serial script per market: six markets at
25 minutes each would add about 2.5 hours to the nightly, and `phase0_pull.py`
polls for only 25 minutes before giving up. The review already records that
failure — UK and DE went stale because report generation outran the poll window
and the job was abandoned.

So the nightly pull is **not** a new step. It becomes a fifth report inside
`phase0_pull.py`'s existing batch. That script already requests four reports at
once and polls them together, because they generate in parallel server-side.
Adding a fifth costs almost no extra wall-clock.

- `REPORTS` gains a `targeting_daily` entry: `spTargeting`, grouped by
  `targeting`, with `date` added to the columns and `time_unit="DAILY"`.
- Its window is the last 7 days, not the 31 the other reports use. Each report
  is created with its own start and end, so a per-report window is a small
  change. Seven days rather than one means a night the job dies heals itself on
  the next run, instead of leaving a permanent hole in the window the rules
  read.
- It banks through a new `db.store_target_daily`, alongside the existing
  storers.

Two cadences remain outside phase 0:

- The one-time 92-day backfill, run once per market to seed the history.
- Mondays, a 35-day true-up for maturing attribution, run beside the
  `backfill_daily.py` re-run that already happens on Mondays. Thirty-five days
  covers the full 30-day attribution window with a few days to spare. The full
  92 is not needed weekly, because days older than about 35 no longer move.

The Monday true-up is not optional. Without it the recent days stay permanently
under-attributed.

## Rules language

### Grammar

The parse error at `rules/parser.py:100` is replaced by real support:

```
FOR EACH target IN LAST 7 DAYS:
    IF clicks > 15 AND orders = 0:
        setBid(bid * 0.8)
        note("dead week — cutting back")
```

`IN CURRENT` and `IN LIFETIME` keep working exactly as they do now. No existing
rule changes meaning.

### Which entities support it

- `target` and `adgroup` read `target_daily`. An ad group is its targets summed.
- `campaign` reads `campaign_daily`, which already holds about 92 days. Campaign
  rolling windows therefore cost almost nothing to add.
- `searchterm` and `product` have no per-day source. A rolling window on those
  is a validation error at save time, with a message naming the entities that do
  have daily history. This follows the semantic-validation work from block 2 of
  the review, where unknown fields stopped being discovered as a nightly
  "unsupported".

### What resolves inside a rolling window

Metrics (`impressions`, `clicks`, `spend`, `cost`, `sales`, `orders`, `acos`,
`cvr`, `ctr`, `cpc`, `roas`, `aov`) become that window's true sums, lagged 2
days.

Everything else is unchanged. `bid` and `state` still come from the `targets`
mirror. Economics fields still resolve through the phase resolvers, so
`IF acos > break_even` means what it looks like, measured over 7 days instead of
30.

Implementation: `rules/entities.load(conn, kind)` gains a window argument. For a
rolling window the source query swaps to `target_daily` over a date range and
groups; the mirror lookup, ad-group defaults and economics attachment are
untouched.

### The window gate

New `db.daily_window_gate()`, beside the existing `db.snapshot_gate()`. Before
any rolling-window rule writes anything, it verifies the window is actually
present. For a window of N days it checks that every one of the N days in
`[today - lag - N + 1, today - lag]` carries at least one banked row, and that
the newest banked day overall is no older than
`DAILY_ATTRIBUTION_LAG_DAYS + db.SNAPSHOT_STALE_AFTER_DAYS`, which is 5 days.
Reusing `SNAPSHOT_STALE_AFTER_DAYS` keeps one staleness threshold across the
engine, which is the same reasoning the `data_stale` alert already follows.

A short or gappy window blocks the change and reports it, the same way
`blocked_stale_data` does today in `rules/executor.py`.

This matters because of the failure mode. If a Monday report job dies and the
week holds 6 days instead of 7, a naive read sums 6 days and calls it a week.
Every target then looks about 14% cheaper and 14% worse-selling than it really
was, and the rules act on that. The gate turns a wrong answer into a refusal.
Same fail-closed shape as the econ gate and the KDP book entries.

`rules/executor.py`'s `_SOURCE_TABLE` map gains the rolling case, so a change
carries which window it came from and the executor picks the matching gate.

### Cap

`IN LAST N DAYS` with N above 92 is a validation error. Amazon cannot supply it,
so the language should not pretend it can.

## Display

No new endpoint.

`appctl history --target|--adgroup|--campaign` already returns a dated series,
and today it honestly labels its points as trailing-30 snapshots per pull date
(`appctl.py:556`). It gains a `basis` field: `"daily"` when `target_daily`
carries at least one row for the requested entity, `"trailing30_snapshot"`
when it falls back. The app reads `basis` and labels the chart accordingly, so
a chart never quietly changes meaning.

`basis` says WHICH series a chart is drawing; it does not say how much of it
there is. A thinly-banked target — two days of `target_daily`, say — still
returns `basis: "daily"` honestly, which is correct (the two series can't be
concatenated, and days are the more truthful answer), but a chart drawing two
points needs to know its own span rather than imply months of history. So the
response also carries `days_banked` (the number of distinct dates the
returned points cover), and `first`/`last` (the first and last dates covered,
or `null` when there are no points) — populated the same way for both bases,
so the app never has to branch on which one it got.

The Targets screen then shows a true per-day series with bid changes overlaid.
`bidhistory` already supplies those.

`health` gains per-market `target_daily` coverage: banked days, first day, last
day. The gate can block a rule for insufficient history, so the operator needs
somewhere to see why.

## Testing

- `tests/target_daily_tests.py` — chunk arithmetic at the 31-day cap, idempotent
  re-banking, the 2-day lag maths, and the window gate refusing a short window,
  a gapped window, and a stale one.
- Rules tests — `IN LAST 7 DAYS` parses; N over 92 is rejected; `searchterm` and
  `product` are rejected with a message naming what does work; the evaluator
  sums the right days; the executor blocks when the gate is shut.
- `tests/snapshot_lint_tests.py` needs no change. It guards the three cumulative
  snapshot tables against being dated from each other. `target_daily` is read by
  date range, like `campaign_daily` and `daily_totals`, neither of which is in
  `PERF_TABLES`. The defect class does not apply.
- Every existing suite stays green. `targeting_perf` and its consumers are
  untouched.

## Rollout order

1. Schema plus `backfill_target_daily.py`, with tests.
2. Seed the history: 92 days, six markets. This is a report read, which project
   conventions place under unrestricted reads, so Claude runs it. Nothing here
   writes to the Amazon account.
3. Nightly wiring: the fifth report inside `phase0_pull.py`, plus the Monday
   true-up in `run_scheduled.sh`. Engine files are edited only when no run is in
   flight.
4. Rules DSL support plus the window gate.
5. `history` `basis` field, `health` coverage, and the app chart. Rebuild and
   reinstall to `/Applications`, then relaunch.

## Cost

Measured after the seed ran, not estimated.

| Market | Days | Rows | First day |
|---|---|---|---|
| US | 92 | 727,777 | 2026-05-06 |
| ES | 43 | 129,117 | 2026-06-24 |
| IT | 43 | 99,101 | 2026-06-24 |
| FR | 43 | 54,418 | 2026-06-24 |
| DE | 43 | 37,968 | 2026-06-24 |
| UK | 43 | 24,252 | 2026-06-24 |

About 1.07 million rows across all six markets. US runs about 7,900 rows a day,
so ongoing growth there is roughly 2.9 million rows a year. The EU markets
together add far less than US alone.

The five EU markets all start on 2026-06-24 rather than the full 92 days back.
That is not a failed backfill. `campaign_daily`, which `backfill_daily.py`
filled independently months earlier, also starts on 2026-06-24 in those markets
and holds nothing before it. Two separately filled tables agree that EU ad
activity began that day.

For comparison, `targeting_perf` already holds 1.27 million rows of thirty
overlapping snapshots in US alone. The new table is not a step change in size.

Generation was the real cost, not storage. The three US chunks took about 22
minutes to generate, all polled in parallel.

## Probe results

A read-only probe requested a 3-day DAILY `spTargeting` report against the live
US account and counted the rows. It wrote nothing, to Amazon or to the database.

1. **`spTargeting` accepts `timeUnit=DAILY`.** The report reached `COMPLETED`
   and returned rows carrying a real `date`. The approach is viable.
2. **`keywordId` appeared on all 20,277 rows.** `target_id` is reliably
   populated. The composite primary key is kept regardless, for the reasons in
   the schema section.
3. **Generation took 24 minutes for a 3-day window.** This was not something the
   probe set out to measure, and it is the finding that shaped the nightly
   wiring above. It is why the nightly pull joins `phase0_pull.py`'s parallel
   report batch instead of running as its own serial step.

A sample row, for reference:

```json
{"date": "2026-08-02", "keywordId": 900000000000003, "targeting": "50s shirt",
 "matchType": "EXACT", "campaignId": 900000000000001, "adGroupId": 900000000000002,
 "impressions": 3, "clicks": 0, "cost": 0, "purchases30d": 0, "sales30d": 0}
```

## Known gaps after this work shipped

Recorded here because the review scratch directory does not survive, and these
are the things a future reader would otherwise rediscover the hard way.

**The gate can refuse but cannot repair.** If the nightly `targeting_daily`
report fails for several days running, rolling-window rules go quiet rather than
wrong. That is the safe direction, but quiet is easy to miss. `health` reports
per-market coverage and System Health shows it, yet nothing polls it on a
schedule. If rolling rules become load-bearing, that check should become an
alert.

**Monday's run got longer.** The 35-day true-up is a serial per-market script
with a 40-minute ceiling, so Monday's 10:00 run can finish in the afternoon.
The design rejected that shape for the nightly and then adopted it for Mondays
without costing it. Folding the true-up into phase 0's parallel batch, the way
the nightly pull already is, would remove the problem.

**A genuinely dead market-day is indistinguishable from a lost report.** A day
on which no target anywhere took an impression looks exactly like a day whose
report never landed, so the gate blocks every window containing it. Failing
closed is right, but the operator needs to know this is why.

**Preview does not re-run semantic validation.** `rules-validate` rejects a
rolling window on an entity with no per-day source, but `preview` — which the
nightly, collect and run paths all use — parses without that check. The
executor and the entity loader now both fail closed independently, so a
hand-edited rule file is caught. It would still be better caught at the front.

**Timing is resolved twice.** Preview resolves "today" when it loads entities
and the executor resolves it again when it gates. A run crossing local midnight
would gate a window one day off from the one it measured. Threading a single
`today` through would settle it.

**No rolling rule is live yet.** The path is covered end to end by tests,
including one that fails loudly if a blocked change ever reaches Amazon, but it
has not yet run against the account with a real rule behind it.
