# MerchAds Rules DSL — authoring guide

A small, readable automation language over the engine. Write rules that pause/bid/
negate on **economics** (break-even, royalty, profit) — the thing ACOS-only tools
can't do. Every rule previews read-only before it ever writes.

Author rules in the app (**Manage → Rules**) or as text files in `rule_defs/*.rule`.
Preview with `⌘↩`; enable + set to Auto to run in the nightly job, or "Run & apply
now" on demand.

## Windows

Two kinds of history back a rule:

- The **cumulative trailing-~30-day snapshot** — `CURRENT` (default) and `LIFETIME`.
- **True per-day tables** (`target_daily`, `campaign_daily`) — the rolling and
  baseline windows below. These exist for `target`, `keyword`, `adGroup` and
  `campaign` only; `searchTerm` and `product` have no per-day source, so any
  window on them is a validation error.

**FOR EACH windows** (the range every metric in the loop is summed over):

- **`CURRENT`** (default) — the latest snapshot (~last 30 settled days).
- **`LIFETIME`** — stored lifetime units (`lifetime_sales`) only.
- **`IN LAST n DAYS`** — a rolling window ending two days back (the freshest two
  days are still settling). `n` ≤ 92 (Amazon's retention). Added 2026-08-06.

**Inline baseline / trend windows** (added 2026-08-09) — restate ONE metric over a
different range, so a rule can compare recent to baseline in a single pass:

- `<metric> IN FROM a DAYS AGO TO b DAYS AGO` — an offset baseline (unlagged).
- `<metric> IN n DAYS AGO` — one exact day.
- `<metric> IN YESTERDAY` — the latest settled day (two days back).
- `<metric> IN LAST n DAYS` — a rolling sub-window.

A metric with no data in that window reads `NONE`, so a missing baseline fails
closed (the rule skips) rather than firing on a partial or empty history.

```
FOR EACH keyword IN LAST 7 DAYS:
  LET baseline = keyword.acos IN FROM 8 DAYS AGO TO 60 DAYS AGO
  IF keyword.clicks >= 10 AND keyword.acos > baseline * 1.5:
    keyword.setBid(MAX($0.05, keyword.bid * 0.85))
    keyword.note("recent {acos:percent} over 1.5x its 8-60d baseline")
```

## Shape

```
FOR EACH <entity> [AS name] [IN CURRENT|LIFETIME|LAST n DAYS]:
  # comment
  LET x = <expression> [IN <window>]
  IF <condition>:
    <entity>.<action>(<args>)
    <entity>.note("... {field} ...")
```

Indentation is two spaces (tabs rejected). Keywords/fields are case-insensitive.

**Entities:** `keyword` / `target` (things you bid on), `searchTerm` (shopper queries),
`campaign`, `adGroup`, `product` (an ASIN across campaigns), and the cross-campaign
rollups `accumulated_asin` / `accumulated_keyword` (one row per ASIN / per keyword
text, summed over every campaign it runs in; extra fields `campaigns` and
`ad_groups` count how far it spread). Accumulated entities read only `CURRENT` and
take only the everywhere verbs below.

## Fields

**Metrics** (over the window): `impressions clicks spend sales orders units acos roas
ctr cvr cpc`. A ratio with a zero denominator (e.g. `acos` with 0 sales) is `NONE` and
never matches a numeric comparison — so "high acos" rules skip zero-sale rows.

**Economics (the edge)** — resolved from the same economics the phases use, and
**fail-closed**: when economics can't be resolved for a design, every field below is
`NONE`, so the rule skips it (never guesses margin):
- `break_even` — the design's own break-even ACOS.
- `royalty` — per-unit royalty · `profit` = royalty×orders − spend · `royalty_roi`.
- `halo_est` `net_halo` `organic_per_day` — US organic-lift estimate (NONE elsewhere).
- `in_transition` (30-day price window) · `is_cohort` (multi-ASIN group) ·
  `econ_available` · `product_type` · `lifetime_sales`.
- `owned_cross_sell` — the royalty this design's ad drove on your OTHER designs
  (measured from Amazon's purchased-product report; only counts ASINs in your
  catalogue). Unlike the fields above it is MEASURED, so a design with no owned
  cross-sell reads `0.0`, not `NONE` — a guard like `owned_cross_sell < spend`
  still fires. Works in every market. Example: spare a bleeder with
  `... AND adGroup.owned_cross_sell < adGroup.spend`.

**Settings/identity:** `bid bid_inherited state name match_type keyword_text search_term
asin ad_type budget days_since_bid_change`.

**Cooldowns.** `days_since_bid_change` (targets/keywords) and
`days_since_budget_change` (campaigns) count the days since the ENGINE last made
that change, read from `writes_log`. An entity we have never touched reads
`99999` (`entities.NEVER_CHANGED_DAYS`), not `NONE` — it has waited forever, so
`days_since_bid_change > 7` passes. This is the opposite of the economics
fields, and deliberately so: a missing royalty is unknown, while a missing
bid-change row is knowledge. It used to return `NONE`, which made every cooldown
false and quietly limited those rules to the handful of already-touched targets
(55 of 43,370 in US on 2026-08-06) — two of the three shipped rules matched
nothing and read as broken. A bid changed by hand in the Amazon console is
invisible here, because it was never ours to wait on.

## Operators & functions

`AND OR NOT` · `= != < <= > >=` · `IN / NOT IN [..]` · `CONTAINS / STARTS WITH / ENDS
WITH` (case-insensitive) · arithmetic `+ - * / %` · `$0.85` money · `45%` = 0.45.
Functions: `MIN MAX CLAMP ROUND FLOOR CEIL ABS IF(cond,then,else) LOWER UPPER LENGTH`.

## Actions

`pause() enable()` (target/adGroup/campaign) · `setBid(x)` · `setBudget(x)` ·
`addNegative(text[, "exact"|"phrase"])` · `note("…")`. Every bid written is clamped by
the per-market **max-bid ceiling** (Settings); writes are logged to the Audit Trail and
honor the KILL freeze and the economics gate. (`createKeyword` / `setBiddingStrategy`
aren't executable yet.)

**Everywhere verbs** (accumulated entities only) fan one change out to every
instance of the rollup: `pauseEverywhere()` (asin → its ad groups, keyword → its
target clauses), `negateEverywhere(["exact"|"phrase"])` (keyword → a negative in
every ad group it ran in), `setBidEverywhere(x)` (keyword → every target clause).
An ASIN can only be paused. Already-paused / same-bid instances are skipped, each
write is logged and undoable, and the fan-out is counted per instance. Example —
pause a design bleeding across many small campaigns:

```
FOR EACH accumulated_asin:
  IF accumulated_asin.spend > $25 AND accumulated_asin.orders = 0:
    accumulated_asin.pauseEverywhere()
    accumulated_asin.note("{spend:money} across {campaigns} campaigns, no orders")
```

`note()` placeholders pull from the row: `{clicks}`, `{acos:percent}`, `{spend:money}`.

## Cookbook — economics-first starting points

Thresholds are starting points; **always Preview first**, start in Review mode.

**Pause money-losers (true profit, not just ACOS):**
```
FOR EACH target:
  IF target.clicks >= 15 AND target.profit < 0:
    target.pause()
    target.note("{clicks} clicks, {spend:money} spent, profit negative")
```

**Bid down over-break-even converters (floor + cool-down):**
```
FOR EACH keyword:
  IF keyword.orders >= 1 AND keyword.acos > break_even AND days_since_bid_change > 7:
    keyword.setBid(MAX($0.05, keyword.bid * 0.85))
    keyword.note("ACOS {acos:percent} over break-even {break_even:percent}")
```

**Bid up profitable winners (ceiling):**
```
FOR EACH keyword:
  IF keyword.orders >= 3 AND royalty_roi > 1.5 AND days_since_bid_change > 7:
    keyword.setBid(MIN($1.20, keyword.bid * 1.10))
    keyword.note("royalty ROI {royalty_roi} — room to scale")
```

**Negate wasteful search terms:**
```
FOR EACH searchTerm:
  IF searchTerm.clicks >= 12 AND searchTerm.orders = 0:
    searchTerm.addNegative(searchTerm.search_term, "exact")
    searchTerm.note("{clicks} clicks, 0 sales")
```

**Spare proven winners, cut the rest (cohort/lifetime aware):**
```
FOR EACH adGroup:
  IF adGroup.spend > $8 AND adGroup.orders = 0 AND lifetime_sales < 10 AND is_cohort = FALSE:
    adGroup.pause()
    adGroup.note("no sales, not a proven design")
```

## Running

- **Preview** (app, `⌘↩`): read-only proposed changes + per-condition traces.
- **Nightly**: a rule set **Enabled + Auto** runs each night per market (in-season only),
  gated by KILL + the economics gate + the change cap.
- **On demand**: "Run & apply now" (app) or `ADS_MARKET=<m> python3 appctl.py rules-run
  --apply --rule "<name>"` (operator-run).
- **Review mode**: preview it, run it manually, and enable Auto once its judgment earns it.
