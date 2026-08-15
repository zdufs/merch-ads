# Safety

**This software changes a live Amazon Advertising account. That spends real money.**

Read this page before you turn on any automation. It is short on purpose.

---

## The one thing to remember

```bash
touch KILL
```

Create a file named `KILL` in the repository folder and **every write stops**. Bids,
pauses, negative keywords, budgets, new campaigns — all of it refuses.

```bash
rm KILL
```

Removes the freeze.

Try it today, while nothing is at stake:

```bash
touch KILL
python3 engine/appctl.py kill      # -> {"ok": true, "data": {"kill_active": true}}
rm KILL
```

The Mac app has the same switch as a toggle. It reads and writes the same file.

**Reads keep working while the kill switch is on.** You can still look at everything —
dashboards, reports, previews. Only writes are blocked.

---

## The five gates

Every write has to pass all five. Any one of them says no, the write does not happen.

### 1. Kill switch

Checked at the top of every apply path — every phase script, every rules run, and every
write command in `appctl.py`. A frozen run exits with a clear message and code `3`.

### 2. Preview by default

No phase writes unless you pass `--apply`. Run any of these bare and you get a report of
what *would* change:

```bash
ADS_MARKET=US python3 engine/phase2_apply.py     # negatives + pauses
ADS_MARKET=US python3 engine/phase3_bids.py      # bid changes
ADS_MARKET=US python3 engine/appctl.py rules-preview --rule "My rule"
```

The nightly job passes `--apply --auto`. That is the moment automation becomes real, and
it is why approval mode exists.

### 3. Data freshness

Performance data can go stale — Amazon's report jobs fail independently of each other, so
one table can freeze while its neighbours stay current.

**Acting on stale data is worse than doing nothing**, because the numbers still look
plausible. So a write whose evidence is more than **3 days old is refused**
(`db.SNAPSHOT_STALE_AFTER_DAYS`). The gate is checked against the specific table the
decision was measured over, not against the freshest table in the database.

Rules with a rolling window (`IN LAST N DAYS`) are stricter still: if the window has a
hole in it, the rule refuses rather than acting on a partial sum.

Check freshness any time:

```bash
python3 engine/appctl.py health
```

`latest_data` is the **worst** of the three performance tables. `stale_tables` names
anything past the threshold.

### 4. Economics

Bids, pauses and negatives are judged against each design's **own break-even ACOS**,
derived from its real list price and the royalty table.

If those economics are unavailable or the price export is stale, the write is **refused,
not guessed**:

```bash
python3 engine/appctl.py econ-gate      # -> {"ok": false, "reasons": [...]} when closed
```

A closed gate blocks every economics-driven write: negatives, pauses, promotions, bid
resets and the nightly auto-apply. This is deliberate. A wrong royalty figure produces
confidently wrong money decisions.

The same principle covers KDP: a book with no royalty entered reports economics as
unavailable rather than inventing one.

### 5. Ceilings and caps

- **Bid ceiling.** Set a per-market maximum bid and daily budget. Every write is clamped
  to it and the clamp is logged.

  ```bash
  python3 engine/appctl.py maxbid --set --target 0.50 --keyword 0.50 --budget 20
  python3 engine/appctl.py maxbid                 # show current ceilings
  python3 engine/appctl.py maxbid --clear
  ```

  Set this before your first live run. It is the cheapest insurance here.

- **Change cap.** One rules run applies at most 50,000 changes. A runaway rule stops
  rather than working through your whole account.

- **Conflict guard.** If two different rules propose a change to the same entity, only
  the first in rule order wins. The rest are skipped and reported, so a later rule cannot
  silently overwrite an earlier one.

---

## Approval mode — use it first

```bash
python3 engine/appctl.py approval-mode --on
```

The nightly run now **collects** proposed changes instead of applying them. You review
them in the app's **Approval Queue** and approve or discard each one.

Run this way for your first few weeks. It shows you exactly what the automation wants to
do to your account, with the reasoning attached, before it can do it.

```bash
python3 engine/appctl.py approval-mode --off     # when you trust it
python3 engine/appctl.py approval-mode           # check current state
```

**A caveat worth knowing:** approval mode gates the built-in phase-2 step. Rules you have
written and set to `AUTO` still apply themselves. Set a rule to `REVIEW` mode if you want
it queued for approval too.

---

## Undo

Every write is logged to the `writes_log` table with its previous value.

```bash
python3 engine/appctl.py audit --limit 50        # newest first
python3 engine/appctl.py undo --row 12345        # reverse one write
```

The app's **Audit Trail** screen shows the same list with an Undo button on each
reversible row.

**Reversible:** pause and enable (ad group, campaign, target, keyword), bid changes,
budget changes, and negative keywords added since reversible negatives shipped.

**Not reversible:**

- **Archiving a campaign.** Amazon has no un-archive. The campaign leaves the console
  permanently. `archive-campaign` refuses without `--confirm`, and it is deliberately
  excluded from the undo list so the app never offers an Undo it cannot honour.
  **Pause instead. Pausing is the reversible option.**
- **Old negative keywords** created before the id was logged. There is nothing to delete,
  so the row honestly reports itself as not undoable.
- **Newly created campaigns and ad groups.** Pause them; do not expect an undo.

---

## Recommended first month

1. **Week 0.** Pull data only. Compare `appctl.py metrics` against the Amazon console
   until you trust the numbers.
2. **Set the ceilings.** `maxbid --set` before anything writes.
3. **One market.** Leave the other profile ids blank in `.env`.
4. **Approval mode on.** Review every proposal for a couple of weeks. If you disagree
   with the built-in logic, write your own [rules](rules-dsl.md) instead.
5. **Read the audit trail** after every run for the first week. `appctl.py audit`.
6. **Then**, and only then, consider turning approval mode off — and only for the
   specific behaviours you have watched and agreed with.

---

## Things that will surprise you

**Amazon's data lags.** The freshest day or two of sales is under-attributed. It reads as
a collapse in performance that is not real. The engine ends rolling windows two days back
(`db.DAILY_ATTRIBUTION_LAG_DAYS`) for exactly this reason, and labels the current day's
figures as *settling*. Do not panic at yesterday's ACOS.

**EU markets lag more than the US.** Three days behind is a normal pre-pull morning in
Europe, not a fault.

**Performance snapshots are cumulative, not daily.** `campaign_perf`, `targeting_perf` and
`search_term_perf` each hold a rolling trailing-30 total per pull date. Consecutive rows
overlap by 29 days. Never sum them and never date one from another's newest date — the
tables are filled by separate Amazon report jobs and drift apart. `daily_totals`,
`campaign_daily` and `target_daily` are the true per-day tables.

**Amazon only keeps about 95 days of reporting.** Anything older is gone for good unless
you banked it. That is why the nightly job stores per-day totals locally, and why the
console history import exists.

**Never edit the Python files while the nightly job is running.** It launches a fresh
`python3` per script per market against a live account. Check first:

```bash
ps ax | grep run_scheduled
```

---

## If something goes wrong right now

```bash
touch KILL                          # 1. freeze everything
python3 engine/appctl.py audit --limit 50  # 2. see what it did
python3 engine/appctl.py undo --row <id>   # 3. reverse what you want back
```

Then read [TROUBLESHOOTING.md](TROUBLESHOOTING.md), and open an issue if it looks like a
bug in the software rather than a configuration problem.

---

## No warranty

This is source-available software provided as-is under the [Elastic License 2.0](../LICENSE).
There is no warranty and no liability. Your Amazon account, your money, your
responsibility. Not affiliated with Amazon.
