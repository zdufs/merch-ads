# KENP wiring — handoff for a fresh session

**START HERE:** You are picking up a parked task. Read this whole doc, then do
steps 1–6 in order. It is self-contained. It was written 2026-08-11, right after
the KDP ad campaigns went live, by the session that planned this. Deadline: land
before **~2026-08-25** (day 14 of the no-touch window).

Background context lives in the auto-memory `kdp-ads-automation` and in
`docs/claude-code-handoff.md`. You do not need them to execute this, but they
explain the wider KDP ads project.

## What this is and why

KDP book ads earn from KENP — Kindle Edition Normalized Pages read through Kindle
Unlimited — as well as from outright sales. The engine cannot see KENP today, so
the KDP automation rules judge on ad-attributed orders only. That means the pause
and negate rules would pause a keyword that has zero ad-orders but is quietly
driving page reads. This task wires KENP into the engine so those rules can gate
on it.

Confirmed on 2026-08-11 by a live read-only report probe: the Amazon Sponsored
Products v3 report **accepts** the KENP columns for the KDP profile. So this is a
wiring task, not a research question. The engine just never requested those
columns, because its report config was built for Merch tees, which have no KENP.

The 5 `kind:"kdp"` rules stay **DISABLED + REVIEW** through this whole task.
Enabling them is a separate, later step once KENP values are confirmed to be
populating (~day 14). Do not enable anything here.

## Blast radius — read before you touch the schema

The KENP columns go on `targeting_perf`, `search_term_perf`, and `campaign_perf`.
**All six Merch markets (US UK DE FR ES IT) bank into these same tables every
night at 10:00** via `run_scheduled.sh` (launchd `io.github.zdufs.merchads`). A bad
migration breaks Merch banking, not just KDP. Three things make it safe if you
follow them:

- The new columns are added **additively and nullable** (`ALTER TABLE ADD
  COLUMN`). Existing rows and existing INSERTs are untouched.
- `db._f(row, *keys)` returns **0** when a report key is absent (db.py:838). So a
  Merch row, whose report never requested KENP, just stores `kenp = 0`. No
  breakage.
- Only the **KDP** report pull requests the KENP columns (kind-aware, step 2).
  Merch pulls stay exactly as they are.

**Never edit an engine `.py` file while `run_scheduled.sh` is running.** Check
`ps ax | grep run_scheduled` first, and do this work outside the 10:00 window.

## Step 1 — schema (db.py)

Perf table definitions: `campaign_perf` (db.py:56), `targeting_perf` (db.py:62),
`search_term_perf` (db.py:69). Add two columns to each `CREATE TABLE` so fresh
DBs get them: `kenp_read REAL, kenp_royalties REAL`.

Then add an idempotent migration for existing DBs in `_migrate(conn)` (db.py:637),
following the `lifetime_sales` pattern already there:

```python
for t in ("campaign_perf", "targeting_perf", "search_term_perf"):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
    if "kenp_read" not in cols:
        conn.execute(f"ALTER TABLE {t} ADD COLUMN kenp_read REAL")
        conn.execute(f"ALTER TABLE {t} ADD COLUMN kenp_royalties REAL")
conn.commit()
```

`_migrate` runs on every `db.connect` (db.py:255 and :349), so every market DB
migrates the next time it is opened. No manual per-market step.

## Step 2 — banking (db.py store_* + phase0_pull.py)

Report column names (API-confirmed): `kindleEditionNormalizedPagesRead14d` and
`kindleEditionNormalizedPagesRoyalties14d`.

In `store_campaign_perf` (db.py:846), `store_targeting_perf` (db.py:862), and
`store_search_term_perf` (db.py:907): add to each row tuple

```python
_f(r, "kindleEditionNormalizedPagesRead14d"),
_f(r, "kindleEditionNormalizedPagesRoyalties14d"),
```

and add `kenp_read,kenp_royalties` to that function's INSERT column list plus two
more `?` placeholders. This is safe for Merch because `_f` returns 0 when the
keys are absent — you do NOT need to branch these functions by kind.

In `phase0_pull.py`, the `REPORTS` config holds the `columns` lists (around
lines 40–70). Request the two KENP columns **only for KDP** — make the columns
kind-aware, e.g. append the KENP pair when `markets.is_kdp()`. Do not add them to
Merch pulls (apparel profiles have no KENP; keep Merch requests unchanged so you
cannot regress them).

Scope note: the **daily** targeting report (phase0_pull.py:66, which banks
`target_daily`) does NOT get KENP in this pass. That means rolling
`kenp IN LAST N DAYS` windows are a later extension and would need a `kenp` column
on `target_daily` too. CURRENT-window KENP is enough for R2/R3/R6 as they are
written (they use CURRENT metrics, not rolling).

## Step 3 — DSL field (rules/entities.py + rules/runner.py)

`_base_metrics` (entities.py:152) builds the per-row metric dict. Thread KENP
through it:

- Add `kenp_read` / `kenp_royalties` to each perf SELECT that feeds a window:
  the aggregate queries at entities.py:187, :197, :205, :247 use `SUM(...)`; the
  CURRENT per-target query at entities.py:254 reads the raw columns.
- Pass them into `_base_metrics` and add `"kenp": kenp_read` and
  `"kenp_royalties": kenp_royalties` to the dict it returns.
- Add `"kenp"` and `"kenp_royalties"` to `SNAPSHOT_METRICS` (runner.py:27) so a
  LIFETIME window nulls them like the other snapshot metrics, and to
  `KNOWN_FIELDS` (runner.py:45) so the validator accepts a rule that references
  them.

KENP is a CURRENT/aggregate metric here; in a rolling window it reads 0 until
`target_daily` carries it (future work, per the scope note above).

## Step 4 — rule wiring (the 5 kind:"kdp" rules)

The rules live in `rule_defs/` (gitignored user data). Edit their text and
re-save with `appctl rules-save` under `ADS_MARKET=USKDP` (rules-save validates
the DSL and keeps `kind=kdp`). Keep every rule **disabled + review**:

- "Pause dead keywords" and "Pause dead targets" (R2): add `AND <entity>.kenp = 0`
  so a target is paused only when it has zero page reads as well as zero orders.
- "Negate wasteful terms (KDP)" (R6): add `AND searchTerm.kenp = 0`.
- "Bid down over break-even" (R3): optional richer version — judge return on
  `spend` vs `sales + kenp_royalties` rather than ad-sales alone. At minimum
  leave it as-is; the KENP gate matters most for the pause and negate rules.

Enabling the rules is NOT part of this task. That is the day-14 step, after you
confirm KENP values are actually populating.

## Step 5 — validate

- `python3 -m unittest tests.snapshot_lint_tests` must still pass. Its rule:
  never filter one perf table by another table's date. Adding columns does not
  change that — keep reading each table by its OWN snapshot date
  (`db.latest_snapshot` / `_latest(conn, "<table>")`). Do not introduce any
  cross-table date filtering.
- Full suite: `python3 -m unittest discover -s tests -p "*_tests.py"`.
- Confirm KENP actually lands. A KDP pull is a live API read, so stage it for
  the operator to run: `ADS_MARKET=USKDP python3 engine/appctl.py run --phase pull`. Then check
  `targeting_perf` has non-null `kenp_read` for the live KDP campaigns. Note the
  campaigns launched 2026-08-11, so KENP is near-zero the first days and accrues
  over the window — an early pull showing 0 is expected, not a failure.
- `ADS_MARKET=USKDP python3 engine/appctl.py rules-preview` (rule text on stdin) should
  show `kenp` on the rows.

## Step 6 — tests to add

- `store_targeting_perf` maps the two KENP report keys into the columns.
- `_migrate` adds the columns idempotently (run it twice, no error).
- `entities` loads `kenp` for a target from a synthetic `targeting_perf` row.
- `runner` accepts `kenp` in `KNOWN_FIELDS` (a rule referencing `kenp` validates).

## Done when

KENP banks for USKDP, the DSL `kenp` field resolves, and R2/R3/R6 carry the KENP
gate — all with the rules still disabled. Then hand to the operator for the day-14
enable decision (~2026-08-25). Live writes and the enable decision stay his.
