# Per-Target Daily Banking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bank true per-day, per-target performance so the rules language can offer rolling windows like `IN LAST 7 DAYS`.

**Architecture:** A new `target_daily` table is filled from an `spTargeting` report requested with `timeUnit=DAILY` — Amazon's own per-day numbers, nothing inferred. A one-time backfill seeds 92 days. The nightly top-up joins `phase0_pull.py`'s existing parallel report batch rather than running as a new serial step. The rules DSL gains rolling windows over that table, guarded by a fail-closed gate that refuses to act when the window has holes.

**Tech Stack:** Python 3 standard library, `sqlite3`, `requests` (via `ads_client`), `unittest`. SwiftUI for the final display task.

**Spec:** `docs/superpowers/specs/2026-08-06-per-target-daily-banking-design.md`

## Global Constraints

- Run every command from the Ads folder. Tests run as `python3 -m unittest tests.<module> -v`.
- Never read or print `.env`.
- Never edit engine `.py` files while `run_scheduled.sh` is running. Check with `ps ax | grep run_scheduled` first.
- Multi-market: anything market-scoped loops `US UK DE FR ES IT` via the `ADS_MARKET` env var. Never assume US-only.
- Never date one perf table from another. Resolve a date from the table you are about to read. `tests/snapshot_lint_tests.py` enforces this — if it fails, fix the module, never widen the allowlist.
- Commit on a branch, never straight to `main`. The branch is `feat/per-target-daily-banking`, already created.
- Use `git commit -F -` with a heredoc for any message containing parentheses or apostrophes.
- Plain language in commit messages and comments: short sentences, one idea each.
- Money values are in the market currency. `acos` and `cvr` are fractions (0.1816 = 18.16%).
- `DAILY_ATTRIBUTION_LAG_DAYS = 2` and `MAX_DAILY_WINDOW_DAYS = 92` are defined once, in `db.py`, and imported everywhere else. Never re-declare them.

---

### Task 1: The `target_daily` table and its storer

**Files:**
- Modify: `db.py` — add to the `SCHEMA` string near `campaign_daily` (around line 203), add two module constants near `SNAPSHOT_STALE_AFTER_DAYS` (line 457), add `store_target_daily` near `store_campaign_daily` (line 934)
- Test: `tests/target_daily_tests.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `db.DAILY_ATTRIBUTION_LAG_DAYS` → `int`, value `2`
  - `db.MAX_DAILY_WINDOW_DAYS` → `int`, value `92`
  - `db.store_target_daily(conn, rows, end_date=None) -> int`. `rows` is an iterable of raw Amazon report dicts. Returns the number of rows written. `end_date` is accepted and ignored, so the signature matches the other entries in `phase0_pull.STORERS`.

- [ ] **Step 1: Write the failing test**

Create `tests/target_daily_tests.py`:

```python
#!/usr/bin/env python3
"""Per-target daily banking: storer, window maths, and the fail-closed gate.

Run from the Ads folder:  python3 -m unittest tests.target_daily_tests -v
"""

import datetime
import os
import sqlite3
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["ADS_MARKET"] = "US"

import db  # noqa: E402


def memory_conn():
    """An in-memory DB carrying only the table under test."""
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE target_daily (
        date TEXT, campaign_id TEXT, ad_group_id TEXT,
        target_id TEXT, targeting TEXT, match_type TEXT,
        impressions INTEGER, clicks INTEGER, cost REAL,
        orders INTEGER, sales REAL, acos REAL, pulled_at TEXT,
        PRIMARY KEY (date, campaign_id, ad_group_id, targeting, match_type))""")
    return conn


def report_row(date="2026-08-02", targeting="50s shirt", cost=1.5, sales=0.0,
               clicks=2, impressions=30, purchases=0, campaign=900000000000001,
               ad_group=900000000000002, keyword=900000000000003, match="EXACT"):
    """One row shaped like Amazon's DAILY spTargeting output."""
    return {"date": date, "campaignId": campaign, "adGroupId": ad_group,
            "keywordId": keyword, "targeting": targeting, "matchType": match,
            "impressions": impressions, "clicks": clicks, "cost": cost,
            "purchases30d": purchases, "sales30d": sales}


class Storer(unittest.TestCase):

    def test_row_is_dated_from_its_own_date_field(self):
        """The DAILY report carries one row per day. The row's own `date` is
        the truth — never the report's end date, which would collapse every
        day of the window onto one date."""
        conn = memory_conn()
        db.store_target_daily(conn, [report_row(date="2026-08-02"),
                                     report_row(date="2026-08-03")],
                              end_date="2026-08-04")
        dates = [r[0] for r in conn.execute(
            "SELECT date FROM target_daily ORDER BY date")]
        self.assertEqual(dates, ["2026-08-02", "2026-08-03"])

    def test_acos_is_derived_and_none_without_sales(self):
        conn = memory_conn()
        db.store_target_daily(conn, [
            report_row(targeting="sells", cost=2.0, sales=10.0),
            report_row(targeting="dead", cost=2.0, sales=0.0)])
        got = dict(conn.execute("SELECT targeting, acos FROM target_daily"))
        self.assertEqual(got["sells"], 0.2)
        self.assertIsNone(got["dead"])

    def test_rebanking_replaces_rather_than_duplicates(self):
        """The Monday true-up re-reads days already banked. Attribution has
        grown since, so the new figures must win and the row count must not."""
        conn = memory_conn()
        db.store_target_daily(conn, [report_row(sales=0.0)])
        db.store_target_daily(conn, [report_row(sales=19.99)])
        rows = conn.execute("SELECT COUNT(*), SUM(sales) FROM target_daily").fetchone()
        self.assertEqual(rows[0], 1)
        self.assertEqual(rows[1], 19.99)

    def test_rows_without_a_date_are_skipped(self):
        """A row with no date cannot be banked as a day. Dropping it is
        correct; banking it under a guessed date is not."""
        conn = memory_conn()
        written = db.store_target_daily(conn, [report_row(), {"campaignId": 1}])
        self.assertEqual(written, 1)


class Constants(unittest.TestCase):

    def test_lag_and_cap_are_defined_once(self):
        self.assertEqual(db.DAILY_ATTRIBUTION_LAG_DAYS, 2)
        self.assertEqual(db.MAX_DAILY_WINDOW_DAYS, 92)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.target_daily_tests -v`

Expected: FAIL with `AttributeError: module 'db' has no attribute 'store_target_daily'`.

- [ ] **Step 3: Add the schema**

In `db.py`, in the `SCHEMA` string, immediately after the `campaign_daily` index line (`CREATE INDEX IF NOT EXISTS idx_campaign_daily_campaign ...`), add:

```sql
-- True per-day PER-TARGET performance, from an spTargeting report requested
-- with timeUnit=DAILY. This is the bottom rung of the daily ladder that
-- daily_totals and campaign_daily already cover at coarser grain.
-- NOT a snapshot table: targeting_perf holds overlapping trailing-30 windows
-- keyed by pull date, and differencing those gives the day that entered the
-- window minus the day that left it. These rows are single days.
-- A day's spend is final at once; its sales keep growing for up to 30 days as
-- Amazon attributes purchases back to the click that earned them. So recent
-- days under-report sales, and readers lag the window by
-- DAILY_ATTRIBUTION_LAG_DAYS.
-- The key matches targeting_perf rather than target_id: auto and
-- product-targeting clauses may not carry a keywordId. target_id gets an index.
CREATE TABLE IF NOT EXISTS target_daily (
    date TEXT, campaign_id TEXT, ad_group_id TEXT,
    targeting TEXT, match_type TEXT, target_id TEXT,
    impressions INTEGER, clicks INTEGER, cost REAL,
    orders INTEGER, sales REAL, acos REAL, pulled_at TEXT,
    PRIMARY KEY (date, campaign_id, ad_group_id, targeting, match_type)
);
CREATE INDEX IF NOT EXISTS idx_target_daily_target ON target_daily(target_id, date);
CREATE INDEX IF NOT EXISTS idx_target_daily_adgroup ON target_daily(ad_group_id, date);
```

- [ ] **Step 4: Add the constants**

In `db.py`, directly below `SNAPSHOT_STALE_AFTER_DAYS = 3` (line 457):

```python
# Amazon attributes a sale back to the click that earned it for up to 30 days,
# so a recent day's sales are incomplete while its spend is already final. A
# rule reading a recent window would see full spend against partial sales and
# pause too eagerly. Rolling windows therefore end this many days ago.
DAILY_ATTRIBUTION_LAG_DAYS = 2

# Amazon's reporting retention starts about 95 days back and rolls forward.
# Asking for more is a promise the data cannot keep.
MAX_DAILY_WINDOW_DAYS = 92
```

- [ ] **Step 5: Add the storer**

In `db.py`, directly after `store_campaign_daily` (ends line 952):

```python
def store_target_daily(conn, rows, end_date=None):
    """Bulk-upsert true per-day per-target rows from a DAILY spTargeting report.

    `end_date` is accepted and ignored so this matches the signature every other
    entry in phase0_pull.STORERS has. It must be ignored: the report returns one
    row per target PER DAY, so each row's own `date` is the truth. Using the
    report's end date would collapse the whole window onto a single day.

    Re-running is idempotent by design. The Monday true-up re-reads days that
    are already banked, because their sales have grown since.
    """
    out = []
    for r in rows:
        date = r.get("date")
        if not date:
            continue        # cannot be banked as a day; a guessed date is worse
        cost = _f(r, "cost", "spend")
        sales = _f(r, "sales30d", "sales", "sales14d", "sales7d")
        out.append((date, r.get("campaignId"), r.get("adGroupId"),
                    r.get("targeting"), r.get("matchType"),
                    r.get("keywordId") or r.get("targetId"),
                    _f(r, "impressions"), _f(r, "clicks"), cost,
                    _f(r, "purchases30d", "purchases", "orders"), sales,
                    round(cost / sales, 4) if sales else None, _now()))
    return bulk_write(
        conn,
        """INSERT OR REPLACE INTO target_daily
           (date,campaign_id,ad_group_id,targeting,match_type,target_id,
            impressions,clicks,cost,orders,sales,acos,pulled_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", out, "target_daily")
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m unittest tests.target_daily_tests -v`

Expected: PASS, 5 tests.

- [ ] **Step 7: Run the existing suites to confirm nothing regressed**

Run: `python3 -m unittest tests.snapshot_lint_tests tests.ytd_definition_tests tests.history_import_tests -v`

Expected: PASS. `snapshot_lint_tests` must stay green without modification — `target_daily` is read by date range, like `campaign_daily` and `daily_totals`, so it is deliberately not in `PERF_TABLES`.

- [ ] **Step 8: Commit**

```bash
git add db.py tests/target_daily_tests.py
git commit -F - <<'MSG'
Add the target_daily table and its storer

This is the bottom rung of the daily ladder. daily_totals covers the account
and campaign_daily covers campaigns. Nothing covered targets, which is why the
rules language cannot offer rolling windows.

The storer dates each row from the row's own date field rather than the
report's end date. The DAILY report returns one row per target per day, so
using the end date would collapse the whole window onto a single day.

Re-banking replaces rather than duplicates, because the Monday true-up re-reads
days that are already banked to pick up sales Amazon has attributed since.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

### Task 2: The fail-closed window gate

**Files:**
- Modify: `db.py` — add after `snapshot_gate` (ends around line 496)
- Test: `tests/target_daily_tests.py` (extend)

**Interfaces:**
- Consumes: `db.DAILY_ATTRIBUTION_LAG_DAYS`, `db.MAX_DAILY_WINDOW_DAYS`, `db.SNAPSHOT_STALE_AFTER_DAYS` from Task 1.
- Produces:
  - `db.daily_window(days, lag=DAILY_ATTRIBUTION_LAG_DAYS, today=None) -> (start_iso, end_iso)`. Both ends inclusive. For `days=7`, `lag=2`, `today=2026-08-06`: `("2026-07-29", "2026-08-04")`.
  - `db.daily_window_gate(conn, table, days, lag=DAILY_ATTRIBUTION_LAG_DAYS, today=None) -> dict` with keys `ok`, `reason`, `start`, `end`, `days_requested`, `days_banked`, `missing`. `missing` is a sorted list of the absent dates, capped at five entries.

- [ ] **Step 1: Write the failing test**

Append to `tests/target_daily_tests.py`:

```python
TODAY = datetime.date(2026, 8, 6)


def banked(conn, dates, targeting="50s shirt"):
    """Put one row on each named day."""
    for d in dates:
        db.store_target_daily(conn, [report_row(date=d, targeting=targeting)])


class Window(unittest.TestCase):

    def test_window_is_lagged_two_days_and_inclusive(self):
        start, end = db.daily_window(7, today=TODAY)
        self.assertEqual(end, "2026-08-04")      # two days before today
        self.assertEqual(start, "2026-07-29")    # seven days inclusive

    def test_one_day_window_is_a_single_date(self):
        start, end = db.daily_window(1, today=TODAY)
        self.assertEqual((start, end), ("2026-08-04", "2026-08-04"))

    def test_lag_is_overridable(self):
        start, end = db.daily_window(7, lag=0, today=TODAY)
        self.assertEqual((start, end), ("2026-07-31", "2026-08-06"))


class Gate(unittest.TestCase):

    def test_complete_window_passes(self):
        conn = memory_conn()
        banked(conn, ["2026-07-29", "2026-07-30", "2026-07-31", "2026-08-01",
                      "2026-08-02", "2026-08-03", "2026-08-04"])
        gate = db.daily_window_gate(conn, "target_daily", 7, today=TODAY)
        self.assertTrue(gate["ok"], gate["reason"])
        self.assertEqual(gate["days_banked"], 7)

    def test_empty_table_fails_closed(self):
        gate = db.daily_window_gate(memory_conn(), "target_daily", 7, today=TODAY)
        self.assertFalse(gate["ok"])
        self.assertIn("no days banked", gate["reason"])

    def test_a_hole_in_the_middle_fails_closed(self):
        """The failure this gate exists for. Six days summed and called a week
        makes every target look about 14% cheaper and 14% worse-selling than it
        was, and the rules would act on that."""
        conn = memory_conn()
        banked(conn, ["2026-07-29", "2026-07-30", "2026-07-31",
                      "2026-08-02", "2026-08-03", "2026-08-04"])   # 08-01 missing
        gate = db.daily_window_gate(conn, "target_daily", 7, today=TODAY)
        self.assertFalse(gate["ok"])
        self.assertEqual(gate["days_banked"], 6)
        self.assertIn("2026-08-01", gate["missing"])

    def test_stale_newest_day_fails_closed(self):
        """Days present but the newest is old: the report job has been failing.
        The threshold is the shared SNAPSHOT_STALE_AFTER_DAYS plus the lag, so
        the engine keeps one staleness number rather than inventing a second."""
        conn = memory_conn()
        banked(conn, ["2026-07-20", "2026-07-21", "2026-07-22"])
        gate = db.daily_window_gate(conn, "target_daily", 3, today=TODAY)
        self.assertFalse(gate["ok"])
        self.assertIn("stale", gate["reason"])

    def test_window_beyond_retention_fails_closed(self):
        conn = memory_conn()
        banked(conn, ["2026-08-04"])
        gate = db.daily_window_gate(conn, "target_daily", 200, today=TODAY)
        self.assertFalse(gate["ok"])
        self.assertIn("92", gate["reason"])

    def test_gate_reads_the_table_it_is_given(self):
        """campaign rolling windows read campaign_daily, not target_daily."""
        conn = memory_conn()
        conn.execute("CREATE TABLE campaign_daily (date TEXT, campaign_id TEXT, "
                     "cost REAL, PRIMARY KEY (date, campaign_id))")
        conn.executemany("INSERT INTO campaign_daily VALUES (?,?,?)",
                         [("2026-08-03", "c1", 1.0), ("2026-08-04", "c1", 1.0)])
        gate = db.daily_window_gate(conn, "campaign_daily", 2, today=TODAY)
        self.assertTrue(gate["ok"], gate["reason"])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.target_daily_tests -v`

Expected: FAIL with `AttributeError: module 'db' has no attribute 'daily_window'`.

- [ ] **Step 3: Write the implementation**

In `db.py`, directly after `snapshot_gate`:

```python
# ---- rolling daily windows ---------------------------------------------------
# target_daily and campaign_daily hold TRUE single days, so a rolling window is
# a date range rather than one snapshot date. Two things can go wrong, and both
# have to fail closed.
#
# The window can have holes. If a report job dies and a week holds six days, a
# naive SUM calls six days a week: every entity looks about 14% cheaper and 14%
# worse-selling than it was, and the rules act on that. A wrong answer is worse
# than a refusal.
#
# The table can be stale. That is the same condition snapshot_gate already
# guards, so it reuses the same threshold rather than inventing a second one.


def daily_window(days, lag=DAILY_ATTRIBUTION_LAG_DAYS, today=None):
    """The inclusive (start, end) ISO dates of a rolling window of `days`,
    ending `lag` days before today. Both ends are inclusive, so a 7-day window
    spans 7 dates."""
    today = today or datetime.date.today()
    end = today - datetime.timedelta(days=lag)
    start = end - datetime.timedelta(days=days - 1)
    return start.isoformat(), end.isoformat()


def daily_window_gate(conn, table, days, lag=DAILY_ATTRIBUTION_LAG_DAYS, today=None):
    """Fail-closed completeness check for one rolling window, mirroring
    snapshot_gate's shape. `ok` is False when the window reaches past Amazon's
    retention, when any day inside it has no rows, or when the newest banked
    day is stale."""
    result = {"table": table, "days_requested": days, "days_banked": 0,
              "missing": [], "start": None, "end": None, "ok": False, "reason": ""}
    if days < 1 or days > MAX_DAILY_WINDOW_DAYS:
        result["reason"] = (f"a {days}-day window is outside what Amazon keeps "
                            f"(1 to {MAX_DAILY_WINDOW_DAYS} days)")
        return result

    start, end = daily_window(days, lag=lag, today=today)
    result["start"], result["end"] = start, end

    present = {r[0] for r in conn.execute(
        f"SELECT DISTINCT date FROM {table} WHERE date BETWEEN ? AND ?", (start, end))}
    result["days_banked"] = len(present)

    newest = conn.execute(f"SELECT MAX(date) FROM {table}").fetchone()[0]
    if not newest:
        result["reason"] = f"{table} has no days banked"
        return result

    age = snapshot_age_days(newest, today=today)
    limit = lag + SNAPSHOT_STALE_AFTER_DAYS
    if age is None:
        result["reason"] = f"{table} newest day '{newest}' is not a valid date"
        return result
    if age > limit:
        result["reason"] = (f"{table} newest day is {newest} ({age}d old, limit "
                            f"{limit}d) — the report job has been failing")
        return result

    if len(present) < days:
        wanted = datetime.date.fromisoformat(start)
        last = datetime.date.fromisoformat(end)
        missing = []
        while wanted <= last:
            if wanted.isoformat() not in present:
                missing.append(wanted.isoformat())
            wanted += datetime.timedelta(days=1)
        result["missing"] = missing[:5]
        result["reason"] = (f"{table} covers {len(present)} of {days} days in "
                            f"{start}..{end} — summing a short window would "
                            f"understate every entity")
        return result

    result["ok"] = True
    return result
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.target_daily_tests -v`

Expected: PASS, 14 tests.

- [ ] **Step 5: Commit**

```bash
git add db.py tests/target_daily_tests.py
git commit -F - <<'MSG'
Gate rolling windows on the days actually being there

A rolling window is a date range, not a snapshot date, so it can have holes.
If a report job dies and a week holds six days, a plain SUM calls six days a
week. Every entity then looks about 14 percent cheaper and 14 percent
worse-selling than it really was, and the rules act on that.

The gate refuses instead. It also refuses when the newest banked day is stale,
reusing SNAPSHOT_STALE_AFTER_DAYS so the engine keeps one staleness number
rather than growing a second one.

Windows are lagged two days, because a recent day has final spend and
incomplete sales.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

### Task 3: The backfill script

**Files:**
- Create: `backfill_target_daily.py`
- Test: `tests/target_daily_tests.py` (extend)

**Interfaces:**
- Consumes: `db.store_target_daily`, `db.MAX_DAILY_WINDOW_DAYS` from Task 1.
- Produces:
  - `backfill_target_daily.chunk_window(start, end, max_days=31) -> [(start_iso, end_iso)]`. Pure, so it is testable without touching Amazon.
  - `backfill_target_daily.COLUMNS -> list[str]`, the report columns. Task 4 imports this so the nightly and the backfill request identical shapes.

- [ ] **Step 1: Write the failing test**

Append to `tests/target_daily_tests.py`:

```python
import backfill_target_daily  # noqa: E402


class Chunking(unittest.TestCase):
    """Amazon caps DAILY reports at 31 days, so the window is split. The
    chunks must tile the range exactly: no gap leaves a hole the gate will
    later refuse to act on, and no overlap wastes a slow report."""

    def test_short_window_is_one_chunk(self):
        self.assertEqual(
            backfill_target_daily.chunk_window("2026-08-01", "2026-08-07"),
            [("2026-08-01", "2026-08-07")])

    def test_exactly_31_days_is_one_chunk(self):
        chunks = backfill_target_daily.chunk_window("2026-07-01", "2026-07-31")
        self.assertEqual(len(chunks), 1)

    def test_32_days_splits(self):
        chunks = backfill_target_daily.chunk_window("2026-07-01", "2026-08-01")
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0], ("2026-07-01", "2026-07-31"))
        self.assertEqual(chunks[1], ("2026-08-01", "2026-08-01"))

    def test_chunks_tile_the_range_without_gap_or_overlap(self):
        chunks = backfill_target_daily.chunk_window("2026-05-07", "2026-08-06")
        self.assertEqual(chunks[0][0], "2026-05-07")
        self.assertEqual(chunks[-1][1], "2026-08-06")
        for (_, prev_end), (next_start, _) in zip(chunks, chunks[1:]):
            gap = (datetime.date.fromisoformat(next_start)
                   - datetime.date.fromisoformat(prev_end)).days
            self.assertEqual(gap, 1)

    def test_columns_include_date(self):
        """Without `date` explicitly requested, a DAILY report returns rows
        the storer cannot place on a day."""
        self.assertIn("date", backfill_target_daily.COLUMNS)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.target_daily_tests -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'backfill_target_daily'`.

- [ ] **Step 3: Write the script**

Create `backfill_target_daily.py`:

```python
#!/usr/bin/env python3
"""
Backfill target_daily from DAILY spTargeting reports, as far back as the
Reporting API allows (about 92 days). Gives the rules language real per-day
per-target history instead of the overlapping trailing-30 snapshots in
targeting_perf.

Also worth re-running weekly: sales use the 30-day attribution window, so a
day's sales keep growing for a month. A refresh trues-up the recent weeks.

Read-only against Amazon — it requests a report. Writes only target_daily.

Reports are SLOW. A three-day report measured 24 minutes to generate on the US
account, so MAX_WAIT is generous and every chunk's report id is saved in
report_jobs. A run that times out resumes instead of starting over.

Run:  python3 backfill_target_daily.py               # last 92 days, active market
      ADS_MARKET=DE python3 backfill_target_daily.py --days 35
"""

import datetime
import sys
import time
import traceback

import db
from ads_client import AdsClient

POLL_SECS = 30
MAX_WAIT = 2400        # 40 min, matching backfill_daily.py
CHUNK_DAYS = 31        # Amazon's cap on a DAILY report window

# `date` must be requested explicitly or a DAILY report comes back without it.
COLUMNS = ["date", "campaignId", "adGroupId", "keywordId", "targeting",
           "matchType", "impressions", "clicks", "cost", "purchases30d", "sales30d"]


def chunk_window(start, end, max_days=CHUNK_DAYS):
    """Split an inclusive ISO date range into consecutive chunks of at most
    `max_days`. The chunks tile the range exactly: a gap would leave a hole the
    window gate later refuses to act on, and an overlap would waste a slow
    report."""
    cursor = datetime.date.fromisoformat(start)
    last = datetime.date.fromisoformat(end)
    chunks = []
    while cursor <= last:
        chunk_end = min(cursor + datetime.timedelta(days=max_days - 1), last)
        chunks.append((cursor.isoformat(), chunk_end.isoformat()))
        cursor = chunk_end + datetime.timedelta(days=1)
    return chunks


def main():
    args = sys.argv[1:]
    days = db.MAX_DAILY_WINDOW_DAYS
    if "--days" in args:
        try:
            days = min(db.MAX_DAILY_WINDOW_DAYS, int(args[args.index("--days") + 1]))
        except (IndexError, ValueError):
            pass

    today = datetime.date.today()
    window_end = today - datetime.timedelta(days=1)
    window_start = today - datetime.timedelta(days=days)
    chunks = chunk_window(window_start.isoformat(), window_end.isoformat())

    client = AdsClient()
    conn = db.connect()
    print(f"Backfill target_daily [{client.market}]: {window_start} → {window_end} "
          f"({len(chunks)} DAILY reports, ≤{CHUNK_DAYS} days each)")

    active = {}
    for start, end in chunks:
        key = f"target_daily_{start}"
        job = db.get_report_job(conn, key)
        if job and job[4] == end and job[5] == 0 and job[2] not in ("FAILED", "CANCELLED"):
            active[(start, end)] = job[1]
            print(f"  {start}→{end}: resuming {job[1]}")
            continue
        try:
            rid = client.create_report("spTargeting", COLUMNS, ["targeting"],
                                       start, end, time_unit="DAILY")
        except Exception as e:
            print(f"  {start}→{end}: CREATE FAILED: {e}")
            failed.append((start, end))
            continue
        db.save_report_job(conn, key, rid, end)
        active[(start, end)] = rid
        print(f"  {start}→{end}: requested ({rid})")

    pending = dict(active)
    waited = 0
    banked = 0
    failed = []
    while pending and waited <= MAX_WAIT:
        for span in list(pending):
            start, end = span
            key = f"target_daily_{start}"
            try:
                status, url = client.get_report(pending[span])
            except Exception as e:
                print(f"  {start}→{end}: status check error: {e}")
                continue
            db.set_report_status(conn, key, status)
            if status == "COMPLETED":
                # Isolate per chunk. One failed store must not abandon the
                # other chunks still generating.
                try:
                    rows = client.download_gzip_json(url)
                    n = db.store_target_daily(conn, rows)
                except Exception as e:
                    print(f"  {start}→{end}: STORE FAILED — {type(e).__name__}: {e}")
                    failed.append(span)
                    del pending[span]
                    continue
                db.set_report_status(conn, key, "COMPLETED", downloaded=1)
                db.log_pull(conn, f"target_daily:{start}", n)
                banked += n
                print(f"  {start}→{end}: COMPLETED — stored {n} rows")
                del pending[span]
            elif status in ("FAILED", "CANCELLED"):
                print(f"  {start}→{end}: {status}")
                del pending[span]
        if pending:
            print(f"  …still generating: {len(pending)} chunk(s)  ({waited}s elapsed)")
            time.sleep(POLL_SECS)
            waited += POLL_SECS

    covered = conn.execute(
        "SELECT MIN(date), MAX(date), COUNT(DISTINCT date), COUNT(*) FROM target_daily"
    ).fetchone()
    print(f"\nBanked {banked} rows this run. target_daily now covers "
          f"{covered[0]} → {covered[1]} ({covered[2]} days, {covered[3]} rows).")

    if failed:
        print(f"\n⚠️ Create failed: {len(failed)} chunk(s).")
    if pending:
        print(f"\n⚠️ Still generating after {MAX_WAIT // 60} min: {len(pending)} chunk(s).")
        print("   Run this script again in a few minutes — it resumes those reports.")
    if failed or pending:
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.target_daily_tests -v`

Expected: PASS, 19 tests.

- [ ] **Step 5: Confirm the lint stays green**

Run: `python3 -m unittest tests.snapshot_lint_tests -v`

Expected: PASS. The new module reads `target_daily` by range and never filters a perf table by another table's date.

- [ ] **Step 6: Commit**

```bash
git add backfill_target_daily.py tests/target_daily_tests.py
git commit -F - <<'MSG'
Add the target_daily backfiller

Modelled on backfill_daily.py, which already does this for campaigns. Amazon
caps a DAILY report at 31 days, so the window is split into chunks that tile
the range exactly. A gap would leave a hole the window gate later refuses to
act on.

Reports are slow. A three-day report took 24 minutes on the US account, so the
poll ceiling is 40 minutes and every chunk's report id is saved. A run that
times out resumes rather than starting over.

One failed chunk store does not abandon the chunks still generating. That is
the same isolation phase0_pull learned the hard way when a failed targeting
write took the search-term report down with it.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

### Task 4: Nightly wiring

**Files:**
- Modify: `phase0_pull.py` — `REPORTS` (line 31), `STORERS` (line 55), `ensure_report_jobs` (around line 153)
- Modify: `run_scheduled.sh` — line 115, the Monday block
- Test: `tests/target_daily_tests.py` (extend)

**Interfaces:**
- Consumes: `db.store_target_daily` (Task 1), `backfill_target_daily.COLUMNS` (Task 3).
- Produces: a `"targeting_daily"` key in `phase0_pull.REPORTS` and `phase0_pull.STORERS`, carrying its own `start`/`end`.

**Why this shape:** a separate nightly script would add roughly 25 minutes per market to a run that already polls for only 25 before deferring. `phase0_pull.py` requests four reports at once and polls them together because they generate in parallel server-side. A fifth costs almost no extra wall-clock.

- [ ] **Step 1: Write the failing test**

Append to `tests/target_daily_tests.py`:

```python
import phase0_pull  # noqa: E402


class NightlyReport(unittest.TestCase):

    def test_daily_targeting_is_in_the_parallel_batch(self):
        """Not a separate serial step. Six markets at 25 minutes each would
        add about 2.5 hours to a nightly that gives up polling after 25."""
        self.assertIn("targeting_daily", phase0_pull.REPORTS)
        self.assertIn("targeting_daily", phase0_pull.STORERS)

    def test_it_is_the_only_daily_report(self):
        cfg = phase0_pull.REPORTS["targeting_daily"]
        self.assertEqual(cfg["time_unit"], "DAILY")
        self.assertIn("date", cfg["columns"])
        for key, other in phase0_pull.REPORTS.items():
            if key != "targeting_daily":
                self.assertEqual(other.get("time_unit", "SUMMARY"), "SUMMARY")

    def test_its_window_is_seven_days_not_thirty_one(self):
        """Seven rather than one, so a night the job dies heals itself on the
        next run instead of leaving a permanent hole in the window."""
        cfg = phase0_pull.REPORTS["targeting_daily"]
        span = (datetime.date.fromisoformat(cfg["end"])
                - datetime.date.fromisoformat(cfg["start"])).days
        self.assertEqual(span, 6)
        self.assertEqual(cfg["end"], phase0_pull.END)

    def test_it_stores_into_target_daily_not_targeting_perf(self):
        self.assertIs(phase0_pull.STORERS["targeting_daily"], db.store_target_daily)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.target_daily_tests -v`

Expected: FAIL with `KeyError: 'targeting_daily'` or an assertion error on `assertIn`.

- [ ] **Step 3: Confirm no nightly run is in flight**

Run: `ps ax | grep run_scheduled`

Expected: no matching process. If one is running, wait for it. `run_scheduled.sh` starts a fresh `python3` per script per market against LIVE accounts, so editing engine files mid-run is not safe.

- [ ] **Step 4: Add the report definition**

In `phase0_pull.py`, add below the existing `START`/`END` lines (line 28):

```python
# The DAILY targeting report banks TRUE per-day rows into target_daily, which
# is what the rules language needs for rolling windows. Seven days rather than
# one, so a night this job dies heals itself on the next run instead of leaving
# a permanent hole in the window.
DAILY_START = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
```

Then add to the `REPORTS` dict, after the `"purchased"` entry:

```python
    # True per-day per-target rows. Every other report here is a SUMMARY over
    # the trailing 31 days; this one asks for the days themselves. It rides in
    # the same batch because reports generate in parallel server-side, so a
    # fifth costs almost no extra wall-clock — and a separate serial step would
    # add about 25 minutes per market to a run that polls for only 25.
    "targeting_daily": dict(
        report_type_id="spTargeting", group_by=["targeting"],
        time_unit="DAILY", start=DAILY_START, end=END,
        columns=["date", "campaignId", "adGroupId", "keywordId", "targeting",
                 "matchType", "impressions", "clicks", "cost",
                 "purchases30d", "sales30d"]),
```

And to `STORERS`:

```python
    "targeting_daily": db.store_target_daily,
```

- [ ] **Step 5: Let each report carry its own window**

In `phase0_pull.py`, in `ensure_report_jobs`, replace the `create_report` call and the window comparison. The current code is:

```python
        job = db.get_report_job(conn, key)
        if job and job[4] == END and job[5] == 0 and job[2] not in ("FAILED", "CANCELLED"):
            active[key] = job[1]
            print(f"  {key}: resuming saved report {job[1]}")
            continue
        try:
            rid = client.create_report(cfg["report_type_id"], cfg["columns"],
                                       cfg["group_by"], START, END)
            db.save_report_job(conn, key, rid, END)
```

Replace with:

```python
        # Most reports share the 31-day SUMMARY window. targeting_daily asks for
        # a shorter one, so each config may override start/end.
        r_start = cfg.get("start", START)
        r_end = cfg.get("end", END)
        job = db.get_report_job(conn, key)
        if job and job[4] == r_end and job[5] == 0 and job[2] not in ("FAILED", "CANCELLED"):
            active[key] = job[1]
            print(f"  {key}: resuming saved report {job[1]}")
            continue
        try:
            rid = client.create_report(cfg["report_type_id"], cfg["columns"],
                                       cfg["group_by"], r_start, r_end,
                                       time_unit=cfg.get("time_unit", "SUMMARY"))
            db.save_report_job(conn, key, rid, r_end)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m unittest tests.target_daily_tests -v`

Expected: PASS, 23 tests.

- [ ] **Step 7: Add the Monday true-up**

In `run_scheduled.sh`, directly below line 115 (the existing `backfill_daily` line):

```bash
    [ "$(date +%u)" = "1" ] && step backfill_target_daily "$PY" backfill_target_daily.py --days 35   # Mondays: true-up per-target daily history (30d attribution)
```

Thirty-five days covers the full 30-day attribution window with a few days to spare. The full 92 is not needed weekly, because days older than about 35 no longer move.

- [ ] **Step 8: Check the shell script still parses**

Run: `bash -n run_scheduled.sh`

Expected: no output, exit 0.

- [ ] **Step 9: Commit**

```bash
git add phase0_pull.py run_scheduled.sh tests/target_daily_tests.py
git commit -F - <<'MSG'
Bank per-target daily rows as a fifth report in phase 0

A separate nightly script would have added about 25 minutes per market to a run
that already gives up polling after 25. The review records that exact failure:
UK and DE went stale because report generation outran the poll window.

phase0_pull already requests four reports at once and polls them together,
because they generate in parallel server-side. A fifth costs almost no extra
wall-clock, and it inherits the resume and leftover-recovery paths for free.

Each report config can now carry its own window. The daily one asks for seven
days rather than one, so a night it dies heals itself on the next run instead
of leaving a permanent hole in the window the rules read.

Mondays additionally true up 35 days, which covers the attribution window with
room to spare.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

### Task 5: Seed the history

**Files:** none. This task produces data, not code.

**Interfaces:**
- Consumes: `backfill_target_daily.py` from Task 3.
- Produces: 92 days of `target_daily` in all six market databases. Tasks 7 onward can be verified against real data rather than fixtures alone.

This is a report read. Project conventions place report backfills under unrestricted reads, so it does not need operator hands. Nothing here writes to the Amazon account.

- [ ] **Step 1: Confirm no nightly run is in flight**

Run: `ps ax | grep run_scheduled`

Expected: no matching process.

- [ ] **Step 2: Seed US first and check the shape before spending five more markets**

Run: `ADS_MARKET=US python3 backfill_target_daily.py`

Expected: three or four chunk requests, then `COMPLETED` lines. This is slow — budget up to 40 minutes. If it reports chunks still generating, run it again; it resumes.

- [ ] **Step 3: Verify what landed**

Run:

```bash
python3 - <<'PY'
import sqlite3
c = sqlite3.connect("file:ads_data.sqlite?mode=ro", uri=True)
print(c.execute("SELECT MIN(date), MAX(date), COUNT(DISTINCT date), COUNT(*) "
                "FROM target_daily").fetchone())
print(c.execute("SELECT date, COUNT(*) FROM target_daily "
                "GROUP BY date ORDER BY date DESC LIMIT 5").fetchall())
PY
```

Expected: about 90 distinct dates and roughly 6,000 to 7,500 rows per day. A day far below that range means a chunk failed — re-run before continuing.

- [ ] **Step 4: Seed the other five markets**

Run:

```bash
for M in UK DE FR ES IT; do
  echo "=== $M ==="
  ADS_MARKET=$M python3 backfill_target_daily.py
done
```

Expected: each completes. EU markets are much smaller than US.

- [ ] **Step 5: Record the real figures in the spec**

Replace the estimates in the "Cost" section of `docs/superpowers/specs/2026-08-06-per-target-daily-banking-design.md` with the measured row counts and the resulting database sizes, per market.

- [ ] **Step 6: Commit the measurement**

```bash
git add docs/superpowers/specs/2026-08-06-per-target-daily-banking-design.md
git commit -F - <<'MSG'
Record what the backfill actually cost

Replaces the estimated row counts with the measured ones, per market.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

### Task 6: Rolling windows in the grammar

**Files:**
- Modify: `rules/lexer.py:10-14` (`KEYWORDS`)
- Modify: `rules/ast_nodes.py:17-22` (`ForEach`)
- Modify: `rules/parser.py:92-106` (window parsing)
- Modify: `rules/runner.py:63-73` (`_semantic_errors`)
- Test: `tests/rules_rolling_tests.py` (create)

**Interfaces:**
- Consumes: `db.MAX_DAILY_WINDOW_DAYS` from Task 1.
- Produces:
  - `ForEach.window` is now one of `"CURRENT"`, `"LIFETIME"`, `"ROLLING"`.
  - `ForEach.window_days` is `None` unless `window == "ROLLING"`, where it is an `int`.
  - `rules.runner.ROLLING_ENTITIES -> set[str]`, the entity kinds with a per-day source: `{"target", "keyword", "adgroup", "campaign"}`.

- [ ] **Step 1: Write the failing test**

Create `tests/rules_rolling_tests.py`:

```python
#!/usr/bin/env python3
"""Rolling windows in the rules DSL.

Before per-target daily banking existed, `IN LAST N DAYS` was a parse error:
the only per-entity data was an overlapping trailing-30 snapshot. target_daily
and campaign_daily changed that for four entity kinds. The other two still have
no per-day source, and asking must fail at save time rather than as a nightly
"unsupported" weeks later.

Run from the Ads folder:  python3 -m unittest tests.rules_rolling_tests -v
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["ADS_MARKET"] = "US"

from rules.parser import parse, ParseError  # noqa: E402
from rules import runner  # noqa: E402

ROLLING = """FOR EACH target IN LAST 7 DAYS:
  IF clicks > 15 AND orders = 0:
    setBid(bid * 0.8)
"""


class Grammar(unittest.TestCase):

    def test_rolling_window_parses(self):
        prog = parse(ROLLING)
        fe = prog.rules[0]
        self.assertEqual(fe.window, "ROLLING")
        self.assertEqual(fe.window_days, 7)

    def test_current_still_defaults(self):
        prog = parse("FOR EACH target:\n  IF clicks > 1:\n    pause()\n")
        self.assertEqual(prog.rules[0].window, "CURRENT")
        self.assertIsNone(prog.rules[0].window_days)

    def test_lifetime_is_untouched(self):
        prog = parse("FOR EACH target IN LIFETIME:\n  IF lifetime_sales > 5:\n    pause()\n")
        self.assertEqual(prog.rules[0].window, "LIFETIME")
        self.assertIsNone(prog.rules[0].window_days)

    def test_singular_day_parses(self):
        prog = parse("FOR EACH target IN LAST 1 DAY:\n  IF clicks > 1:\n    pause()\n")
        self.assertEqual(prog.rules[0].window_days, 1)

    def test_a_bare_number_is_still_an_error(self):
        with self.assertRaises(ParseError):
            parse("FOR EACH target IN 7:\n  IF clicks > 1:\n    pause()\n")


class Semantics(unittest.TestCase):

    def test_valid_rolling_rule_passes(self):
        self.assertTrue(runner.validate(ROLLING)["ok"])

    def test_window_beyond_retention_is_rejected_at_save_time(self):
        result = runner.validate(
            "FOR EACH target IN LAST 200 DAYS:\n  IF clicks > 1:\n    pause()\n")
        self.assertFalse(result["ok"])
        self.assertIn("92", result["errors"][0]["message"])

    def test_zero_days_is_rejected(self):
        result = runner.validate(
            "FOR EACH target IN LAST 0 DAYS:\n  IF clicks > 1:\n    pause()\n")
        self.assertFalse(result["ok"])

    def test_fractional_days_are_rejected(self):
        result = runner.validate(
            "FOR EACH target IN LAST 7.5 DAYS:\n  IF clicks > 1:\n    pause()\n")
        self.assertFalse(result["ok"])

    def test_searchterm_rolling_is_rejected_and_says_what_works(self):
        result = runner.validate(
            "FOR EACH searchTerm IN LAST 7 DAYS:\n  IF clicks > 1:\n    pause()\n")
        self.assertFalse(result["ok"])
        message = result["errors"][0]["message"]
        self.assertIn("searchterm", message.lower())
        self.assertIn("campaign", message.lower())

    def test_product_rolling_is_rejected(self):
        result = runner.validate(
            "FOR EACH product IN LAST 7 DAYS:\n  IF clicks > 1:\n    pause()\n")
        self.assertFalse(result["ok"])

    def test_searchterm_current_still_works(self):
        self.assertTrue(runner.validate(
            "FOR EACH searchTerm:\n  IF clicks > 1:\n    addNegative()\n")["ok"])

    def test_campaign_and_adgroup_rolling_are_allowed(self):
        for entity in ("campaign", "adGroup"):
            src = f"FOR EACH {entity} IN LAST 14 DAYS:\n  IF clicks > 1:\n    pause()\n"
            self.assertTrue(runner.validate(src)["ok"], entity)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.rules_rolling_tests -v`

Expected: FAIL. `parse(ROLLING)` raises `ParseError` with the "windows are CURRENT or LIFETIME only" message.

- [ ] **Step 3: Add the keywords**

In `rules/lexer.py`, change `KEYWORDS` (line 10) to include `LAST`, `DAYS` and `DAY`:

```python
KEYWORDS = {
    "FOR", "EACH", "AS", "IN", "IF", "WHEN", "AND", "OR", "NOT", "LET",
    "CURRENT", "LIFETIME", "LAST", "DAYS", "DAY", "TRUE", "FALSE", "NONE",
    "CONTAINS", "STARTS", "ENDS", "WITH", "IS",
}
```

- [ ] **Step 4: Add the AST field**

In `rules/ast_nodes.py`, change the `ForEach` dataclass:

```python
@dataclass
class ForEach:
    entity: str                     # keyword | target | searchTerm | campaign | adGroup | product
    alias: Optional[str]            # the loop-var name (defaults to entity)
    window: str                     # "CURRENT" | "LIFETIME" | "ROLLING"
    body: List[Any]                 # Let | If | Action | Note
    line: int = 0
    window_days: Optional[int] = None   # set only when window == "ROLLING"
```

`window_days` goes last with a default, so every existing positional construction keeps working.

- [ ] **Step 5: Parse the rolling window**

In `rules/parser.py`, replace the window block (lines 92-106):

```python
    window = "CURRENT"
    window_days = None
    if c.at("KEYWORD", "IN"):
        c.eat()
        wt = c.cur
        if c.at("KEYWORD", "CURRENT") or c.at("KEYWORD", "LIFETIME"):
            window = c.eat().value
        elif c.at("KEYWORD", "LAST"):
            # Rolling windows read the true per-day tables (target_daily,
            # campaign_daily). Before those existed this was a parse error,
            # because the only per-entity data was an overlapping trailing-30
            # snapshot. The day count is validated in runner._semantic_errors,
            # which is where the operator sees it while typing.
            c.eat()
            nt = c.cur
            if not c.at("NUMBER"):
                raise ParseError("IN LAST needs a number of days, as in "
                                 "'IN LAST 7 DAYS'", nt.line)
            raw_days = c.eat().value
            # NUMBER tokens are always float. A whole day count becomes a
            # real int here, to match the ForEach.window_days contract. A
            # fractional count (7.5) stays a float on purpose — that is what
            # lets runner._rolling_errors still see the fraction and reject
            # it, with a message the operator sees while typing.
            window_days = int(raw_days) if raw_days == int(raw_days) else raw_days
            if not c.at("KEYWORD", "DAYS") and not c.at("KEYWORD", "DAY"):
                raise ParseError("IN LAST <n> must be followed by DAYS", c.cur.line)
            c.eat()
            window = "ROLLING"
        else:
            raise ParseError(
                "windows are CURRENT, LIFETIME, or LAST <n> DAYS", wt.line)
    c.eat("COLON")
    c.eat("NEWLINE")
    body = _block(c)
    return A.ForEach(entity=entity.lower(), alias=alias or entity, window=window,
                     body=body, line=kw.line, window_days=window_days)
```

- [ ] **Step 6: Validate the window semantically**

In `rules/runner.py`, add below `KNOWN_FIELDS` (after line 50):

```python
# Entity kinds with a TRUE per-day source. targets and ad groups read
# target_daily; campaigns read campaign_daily. Search terms and products have
# no per-day table, so a rolling window on them is rejected while the operator
# is typing rather than discovered as a nightly "unsupported".
ROLLING_ENTITIES = {"target", "keyword", "adgroup", "campaign"}
```

Then in `_semantic_errors`, add the window check at the top of the loop:

```python
def _semantic_errors(prog):
    """Unknown verbs and unknown field names — everything the parser is too
    permissive to catch but the evaluator/executor would choke on. Also the
    rolling-window rules: which entities have per-day data, and how far back
    Amazon actually keeps it."""
    errors = []
    for fe in prog.rules:
        if fe.window == "ROLLING":
            errors.extend(_rolling_errors(fe))
        allowed = set(KNOWN_FIELDS)
        allowed.add(fe.alias.lower())
        allowed.add(fe.entity.lower())
        _collect_let_names(fe.body, allowed)
        _check_stmts(fe.body, allowed, errors)
    return errors


def _rolling_errors(fe):
    """Everything wrong with `IN LAST n DAYS`, reported at save time."""
    import db
    out = []
    days = fe.window_days
    if days is None or float(days) != int(days):
        out.append({"line": fe.line, "col": 0,
                    "message": "IN LAST needs a whole number of days"})
        return out
    days = int(days)
    if days < 1 or days > db.MAX_DAILY_WINDOW_DAYS:
        out.append({"line": fe.line, "col": 0,
                    "message": (f"a {days}-day window is outside what Amazon keeps "
                                f"— reporting retention is about "
                                f"{db.MAX_DAILY_WINDOW_DAYS} days")})
    if fe.entity not in ROLLING_ENTITIES:
        out.append({"line": fe.line, "col": 0,
                    "message": (f"{fe.entity} has no per-day history, so it cannot "
                                f"use IN LAST n DAYS — rolling windows work on "
                                f"target, keyword, adGroup and campaign")})
    return out
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python3 -m unittest tests.rules_rolling_tests -v`

Expected: PASS, 13 tests.

- [ ] **Step 8: Run the existing rules suite**

Run: `python3 -m unittest discover -s tests -p "rules*tests.py" -v`

Expected: PASS. No existing rule changes meaning — `CURRENT` is still the default and `LIFETIME` is untouched.

- [ ] **Step 9: Commit**

```bash
git add rules/lexer.py rules/ast_nodes.py rules/parser.py rules/runner.py tests/rules_rolling_tests.py
git commit -F - <<'MSG'
Let rules say IN LAST 7 DAYS

This was a parse error because the only per-entity data was an overlapping
trailing-30 snapshot. target_daily and campaign_daily changed that.

Rolling windows work on targets, keywords, ad groups and campaigns. Search
terms and products still have no per-day source, so asking for one is rejected
while the rule is being typed rather than discovered as a nightly
"unsupported". Windows longer than Amazon's retention are rejected the same
way.

CURRENT is still the default and LIFETIME is untouched, so no existing rule
changes meaning.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

### Task 7: Load entities over a rolling window

**Files:**
- Modify: `rules/entities.py` — `load` (line 84), `_load_targets` (line 118), `_load_campaigns` (line 171), `_load_ad_groups` (line 190)
- Test: `tests/rules_rolling_tests.py` (extend)

**Interfaces:**
- Consumes: `db.daily_window` (Task 2), `ROLLING_ENTITIES` (Task 6).
- Produces: `entities.load(conn, kind, window="CURRENT", window_days=None, today=None) -> list[EntityRow]`. The `today` parameter exists so tests can pin the date. Every field on the returned rows keeps the name and meaning it has today — only the metric values change.

- [ ] **Step 1: Write the failing test**

Append to `tests/rules_rolling_tests.py`:

```python
import datetime  # noqa: E402
import sqlite3  # noqa: E402

from rules import entities  # noqa: E402

TODAY = datetime.date(2026, 8, 6)


def rolling_conn():
    """A DB with seven complete days in target_daily, plus the structure
    tables the loaders join against."""
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE target_daily (
        date TEXT, campaign_id TEXT, ad_group_id TEXT, target_id TEXT,
        targeting TEXT, match_type TEXT, impressions INTEGER, clicks INTEGER,
        cost REAL, orders INTEGER, sales REAL, acos REAL, pulled_at TEXT,
        PRIMARY KEY (date, campaign_id, ad_group_id, targeting, match_type))""")
    conn.execute("CREATE TABLE ad_groups (ad_group_id TEXT, name TEXT, state TEXT, "
                 "default_bid REAL, campaign_id TEXT)")
    conn.execute("INSERT INTO ad_groups VALUES ('ag1','Tee AG','ENABLED',0.75,'c1')")
    conn.execute("CREATE TABLE ad_group_product (ad_group_id TEXT, asin TEXT, "
                 "product_type TEXT)")
    conn.execute("INSERT INTO ad_group_product VALUES ('ag1','B0TEST','tee')")
    conn.execute("CREATE TABLE targets (target_id TEXT, bid REAL, state TEXT)")
    conn.execute("INSERT INTO targets VALUES ('t1', 0.90, 'ENABLED')")
    conn.execute("CREATE TABLE writes_log (applied_at TEXT, action TEXT, "
                 "entity_id TEXT)")
    # Seven days, two clicks and one dollar each.
    for offset in range(7):
        day = (datetime.date(2026, 8, 4) - datetime.timedelta(days=offset)).isoformat()
        conn.execute("INSERT INTO target_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (day, "c1", "ag1", "t1", "50s shirt", "EXACT",
                      100, 2, 1.0, 0, 0.0, None, "now"))
    return conn


class RollingLoad(unittest.TestCase):

    def test_target_metrics_are_the_window_sum(self):
        rows = entities.load(rolling_conn(), "target", window="ROLLING",
                             window_days=7, today=TODAY)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].field("clicks"), 14)
        self.assertEqual(rows[0].field("spend"), 7.0)

    def test_window_is_lagged_so_the_newest_days_are_excluded(self):
        """Only 7 days ending 2026-08-04 count. A row dated 2026-08-05 sits
        inside the lag and must not be summed."""
        conn = rolling_conn()
        conn.execute("INSERT INTO target_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("2026-08-05", "c1", "ag1", "t1", "50s shirt", "EXACT",
                      100, 99, 99.0, 0, 0.0, None, "now"))
        rows = entities.load(conn, "target", window="ROLLING",
                             window_days=7, today=TODAY)
        self.assertEqual(rows[0].field("clicks"), 14)

    def test_shorter_window_sums_fewer_days(self):
        rows = entities.load(rolling_conn(), "target", window="ROLLING",
                             window_days=3, today=TODAY)
        self.assertEqual(rows[0].field("clicks"), 6)

    def test_bid_and_state_still_come_from_the_mirror(self):
        """Only the metrics change in a rolling window. The bid is a setting,
        not a measurement, so it reads the same mirror as always."""
        rows = entities.load(rolling_conn(), "target", window="ROLLING",
                             window_days=7, today=TODAY)
        self.assertEqual(rows[0].field("bid"), 0.90)
        self.assertFalse(rows[0].field("bid_inherited"))

    def test_identity_fields_survive(self):
        rows = entities.load(rolling_conn(), "target", window="ROLLING",
                             window_days=7, today=TODAY)
        self.assertEqual(rows[0].field("asin"), "B0TEST")
        self.assertEqual(rows[0].field("product_type"), "tee")
        self.assertEqual(rows[0].field("match_type"), "EXACT")

    def test_ad_groups_sum_their_targets(self):
        rows = entities.load(rolling_conn(), "adgroup", window="ROLLING",
                             window_days=7, today=TODAY)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].field("clicks"), 14)
        self.assertEqual(rows[0].field("default_bid"), 0.75)

    def test_campaigns_read_campaign_daily_not_target_daily(self):
        conn = rolling_conn()
        conn.execute("CREATE TABLE campaign_daily (date TEXT, campaign_id TEXT, "
                     "campaign_name TEXT, cost REAL, sales REAL, orders INTEGER, "
                     "impressions INTEGER, clicks INTEGER, units INTEGER, "
                     "pulled_at TEXT, PRIMARY KEY (date, campaign_id))")
        conn.execute("CREATE TABLE campaigns (campaign_id TEXT, name TEXT, state TEXT, "
                     "daily_budget REAL, bidding_strategy TEXT)")
        conn.execute("INSERT INTO campaigns VALUES ('c1','Lotto 1','ENABLED',10.0,'auto')")
        for offset in range(7):
            day = (datetime.date(2026, 8, 4) - datetime.timedelta(days=offset)).isoformat()
            conn.execute("INSERT INTO campaign_daily VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (day, "c1", "Lotto 1", 5.0, 0.0, 0, 500, 10, 0, "now"))
        rows = entities.load(conn, "campaign", window="ROLLING",
                             window_days=7, today=TODAY)
        self.assertEqual(rows[0].field("clicks"), 70)
        self.assertEqual(rows[0].field("budget"), 10.0)

    def test_current_window_is_unchanged(self):
        """The default path must not have moved. targeting_perf is still the
        source when no rolling window is asked for."""
        conn = rolling_conn()
        conn.execute("""CREATE TABLE targeting_perf (
            date TEXT, campaign_id TEXT, ad_group_id TEXT, targeting TEXT,
            match_type TEXT, target_id TEXT, impressions INTEGER, clicks INTEGER,
            cost REAL, orders INTEGER, sales REAL, acos REAL)""")
        conn.execute("INSERT INTO targeting_perf VALUES "
                     "('2026-08-05','c1','ag1','50s shirt','EXACT','t1',"
                     "3000,55,30.0,1,19.99,1.5)")
        rows = entities.load(conn, "target")
        self.assertEqual(rows[0].field("clicks"), 55)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.rules_rolling_tests -v`

Expected: FAIL with `TypeError: load() got an unexpected keyword argument 'window'`.

- [ ] **Step 3: Widen the loader entry point**

In `rules/entities.py`, replace `load` (line 84):

```python
def load(conn, kind, window="CURRENT", window_days=None, today=None):
    """Load one entity kind into EntityRow objects.

    CURRENT and LIFETIME read the latest cumulative snapshot. ROLLING reads the
    true per-day tables over a lagged date range: targets and ad groups from
    target_daily, campaigns from campaign_daily. `today` exists so tests can pin
    the date.
    """
    kind = kind.lower()
    rolling = None
    if window == "ROLLING":
        import db
        rolling = db.daily_window(int(window_days), today=today)
    if kind in ("target", "keyword"):
        return _load_targets(conn, kind, rolling=rolling)
    if kind == "searchterm":
        return _load_search_terms(conn)
    if kind == "campaign":
        return _load_campaigns(conn, rolling=rolling)
    if kind == "adgroup":
        return _load_ad_groups(conn, rolling=rolling)
    if kind in ("product", "asin"):
        return _load_products(conn)
    raise FieldError(f"unknown entity {kind!r}")
```

`searchterm` and `product` take no `rolling` argument. `runner._rolling_errors` already refuses those at save time, so a rolling window can never reach them.

- [ ] **Step 4: Give the three loaders a rolling source**

In `rules/entities.py`, change the signature and the query of `_load_targets` (line 118). Everything after the `for` loop stays exactly as it is:

```python
def _load_targets(conn, kind, rolling=None):
    defaults = dict(conn.execute("SELECT ad_group_id, default_bid FROM ad_groups"))
    prod = {r[0]: (r[1], r[2]) for r in
            conn.execute("SELECT ad_group_id, asin, product_type FROM ad_group_product")}
    ag_state = dict(conn.execute("SELECT ad_group_id, state FROM ad_groups"))
    mirror = _target_mirror(conn)
    if rolling:
        # True per-day rows summed over the window. Grouped on the same key
        # target_daily is stored under — which does not include target_id.
        # If a keyword is deleted and recreated mid-window under the same
        # targeting text and match type, Amazon gives it a new target_id, and
        # a bare `target_id` column would let SQLite pick an arbitrary row's
        # value for the group. MAX() makes that pick deterministic, and
        # SQLite's MAX skips NULLs, so a real id wins over a NULL when a
        # group mixes them. Do not simplify this back to a bare column.
        sql = """SELECT MAX(target_id), campaign_id, ad_group_id, targeting, match_type,
                        SUM(impressions), SUM(clicks), SUM(cost),
                        SUM(orders), SUM(sales)
                   FROM target_daily WHERE date BETWEEN ? AND ?
                  GROUP BY campaign_id, ad_group_id, targeting, match_type"""
        params = rolling
    else:
        sql = """SELECT target_id, campaign_id, ad_group_id, targeting, match_type,
                        impressions, clicks, cost, orders, sales
                   FROM targeting_perf WHERE date=?"""
        params = (_latest(conn, "targeting_perf"),)
    rows = []
    for (tid, cid, agid, targeting, mt, imps, clicks, cost, orders, sales) in \
            conn.execute(sql, params):
```

Change `_load_campaigns` (line 171) the same way:

```python
def _load_campaigns(conn, rolling=None):
    meta = {r[0]: r for r in conn.execute(
        "SELECT campaign_id, name, state, daily_budget, bidding_strategy FROM campaigns")}
    if rolling:
        # campaign_daily has held true per-day rows since backfill_daily.py, so
        # campaign rolling windows cost nothing extra.
        sql = """SELECT campaign_id, SUM(impressions), SUM(clicks), SUM(cost),
                        SUM(orders), SUM(sales)
                   FROM campaign_daily WHERE date BETWEEN ? AND ?
                  GROUP BY campaign_id"""
        params = rolling
    else:
        sql = """SELECT campaign_id, SUM(impressions), SUM(clicks), SUM(cost),
                        SUM(orders), SUM(sales)
                   FROM campaign_perf WHERE date=? GROUP BY campaign_id"""
        params = (_latest(conn, "campaign_perf"),)
    rows = []
    for (cid, imps, clicks, cost, orders, sales) in conn.execute(sql, params):
```

And `_load_ad_groups` (line 190):

```python
def _load_ad_groups(conn, rolling=None):
    meta = {r[0]: r for r in conn.execute(
        "SELECT ad_group_id, name, state, default_bid, campaign_id FROM ad_groups")}
    prod = {r[0]: (r[1], r[2]) for r in
            conn.execute("SELECT ad_group_id, asin, product_type FROM ad_group_product")}
    if rolling:
        # An ad group is its targets summed.
        sql = """SELECT ad_group_id, SUM(impressions), SUM(clicks), SUM(cost),
                        SUM(orders), SUM(sales)
                   FROM target_daily WHERE date BETWEEN ? AND ?
                  GROUP BY ad_group_id"""
        params = rolling
    else:
        sql = """SELECT ad_group_id, SUM(impressions), SUM(clicks), SUM(cost),
                        SUM(orders), SUM(sales)
                   FROM targeting_perf WHERE date=? GROUP BY ad_group_id"""
        params = (_latest(conn, "targeting_perf"),)
    rows = []
    for (agid, imps, clicks, cost, orders, sales) in conn.execute(sql, params):
```

Delete the now-unused `latest = _latest(...)` line from the top of each of the three functions.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m unittest tests.rules_rolling_tests -v`

Expected: PASS, 21 tests.

- [ ] **Step 6: Confirm the lint stays green**

Run: `python3 -m unittest tests.snapshot_lint_tests -v`

Expected: PASS. `rules/entities.py` is already in `DYNAMIC_TABLE_MODULES` because it routes every read through its own per-table helper, and the rolling branch keeps that discipline: `target_daily` is filtered by a range derived from the calendar, never by another table's `MAX(date)`.

- [ ] **Step 7: Commit**

```bash
git add rules/entities.py tests/rules_rolling_tests.py
git commit -F - <<'MSG'
Sum the true per-day tables for a rolling window

Targets and ad groups read target_daily; campaigns read campaign_daily, which
has held real days since backfill_daily. An ad group is its targets summed.

Only the metrics change. Bid and state are settings rather than measurements,
so they still read the targets mirror, and the identity fields keep the names
and meanings they already had.

The window is lagged two days, so a row inside the lag is excluded even when it
is already banked.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

### Task 8: Carry the window through preview and apply

**Files:**
- Modify: `rules/runner.py` — the `entities.load` call (line 148) and the change dict (line 224)
- Modify: `rules/executor.py` — `_SOURCE_TABLE` (line 29) and `snap_gate` (line 54)
- Test: `tests/rules_rolling_tests.py` (extend)

**Interfaces:**
- Consumes: `db.daily_window_gate` (Task 2), `entities.load(..., window=..., window_days=...)` (Task 7).
- Produces: every change dict gains `"window"` (`str`) and `"window_days"` (`int` or `None`). `rules.executor._ROLLING_SOURCE -> dict[str, str]` maps an entity kind to its per-day table.

- [ ] **Step 1: Write the failing test**

Append to `tests/rules_rolling_tests.py`:

```python
from rules import executor  # noqa: E402


class ChangeCarriesWindow(unittest.TestCase):
    """`preview` resolves economics for every row. On a minimal test DB that
    degrades cleanly rather than raising: products.design_be_for catches the
    missing tables and returns None, and econ_fields._break_even reports
    'unmapped'. So these tests need no economics fixtures."""

    def test_preview_change_records_its_window(self):
        """The executor gates on the window the change was measured over, so
        the change has to carry it."""
        conn = rolling_conn()
        src = ("FOR EACH target IN LAST 7 DAYS:\n"
               "  IF clicks > 10:\n"
               "    setBid(0.50)\n")
        result = runner.preview(conn, src)
        self.assertTrue(result["ok"], result.get("errors"))
        self.assertEqual(len(result["changes"]), 1)
        self.assertEqual(result["changes"][0]["window"], "ROLLING")
        self.assertEqual(result["changes"][0]["window_days"], 7)

    def test_current_change_records_current(self):
        conn = rolling_conn()
        conn.execute("""CREATE TABLE targeting_perf (
            date TEXT, campaign_id TEXT, ad_group_id TEXT, targeting TEXT,
            match_type TEXT, target_id TEXT, impressions INTEGER, clicks INTEGER,
            cost REAL, orders INTEGER, sales REAL, acos REAL)""")
        conn.execute("INSERT INTO targeting_perf VALUES "
                     "('2026-08-05','c1','ag1','50s shirt','EXACT','t1',"
                     "3000,55,30.0,1,19.99,1.5)")
        result = runner.preview(
            conn, "FOR EACH target:\n  IF clicks > 10:\n    setBid(0.50)\n")
        self.assertEqual(result["changes"][0]["window"], "CURRENT")
        self.assertIsNone(result["changes"][0]["window_days"])


class RollingSourceTable(unittest.TestCase):

    def test_rolling_targets_gate_on_target_daily(self):
        self.assertEqual(executor._ROLLING_SOURCE["target"], "target_daily")
        self.assertEqual(executor._ROLLING_SOURCE["adgroup"], "target_daily")

    def test_rolling_campaigns_gate_on_campaign_daily(self):
        self.assertEqual(executor._ROLLING_SOURCE["campaign"], "campaign_daily")

    def test_current_changes_still_gate_on_the_snapshot_tables(self):
        self.assertEqual(executor._SOURCE_TABLE["target"], "targeting_perf")
        self.assertEqual(executor._SOURCE_TABLE["campaign"], "campaign_perf")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.rules_rolling_tests -v`

Expected: FAIL with `KeyError: 'window'` on the change dict.

- [ ] **Step 3: Pass the window into the loader**

In `rules/runner.py`, in `preview`, replace the load call (line 148):

```python
        try:
            rows = entities.load(conn, fe.entity, window=fe.window,
                                 window_days=fe.window_days)
```

- [ ] **Step 4: Record the window on every change**

In `rules/runner.py`, in `_run_body`, add two keys to the change dict (after `"args_text"`, around line 227):

```python
                    "window": fe.window, "window_days": fe.window_days,
```

- [ ] **Step 5: Gate rolling changes on the right table**

In `rules/executor.py`, add below `_SOURCE_TABLE` (after line 35):

```python
# A rolling-window change was measured over the true per-day tables, so its
# gate has to check those rather than the trailing-30 snapshots. The question
# is different too: not "is the newest snapshot fresh" but "are all the days in
# this window actually there". A week that holds six days makes every entity
# look about 14% cheaper and 14% worse-selling than it was.
_ROLLING_SOURCE = {
    "target": "target_daily", "keyword": "target_daily",
    "adgroup": "target_daily", "campaign": "campaign_daily",
}
```

Then replace `snap_gate` inside `execute` (line 54):

```python
    def snap_gate(ch):
        kind = ch["entity_kind"]
        if ch.get("window") == "ROLLING":
            days = int(ch.get("window_days") or 0)
            table = _ROLLING_SOURCE.get(kind, "target_daily")
            cache_key = (table, days)
            if cache_key not in snap_gates:
                snap_gates[cache_key] = db.daily_window_gate(conn, table, days,
                                                            today=today)
            return snap_gates[cache_key]
        table = _SOURCE_TABLE.get(kind, "targeting_perf")
        if table not in snap_gates:
            snap_gates[table] = db.snapshot_gate(conn, table, today=today)
        return snap_gates[table]
```

And update the call site in the loop (line 57 in the original):

```python
        snap = snap_gate(ch)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m unittest tests.rules_rolling_tests tests.target_daily_tests -v`

Expected: PASS, 26 tests in `rules_rolling_tests`.

- [ ] **Step 7: Run every rules suite**

Run: `python3 -m unittest discover -s tests -p "*tests.py" -v`

Expected: PASS across the board.

- [ ] **Step 8: Verify against the real US database**

Run:

```bash
cat > /tmp/rolling_check.rule <<'RULE'
FOR EACH target IN LAST 7 DAYS:
  IF clicks > 15 AND orders = 0:
    setBid(bid * 0.8)
    note("dead week")
RULE
ADS_MARKET=US python3 appctl.py rules-preview --rule /tmp/rolling_check.rule 2>/dev/null \
  || printf '{"name":"rolling-check","text":%s}' "$(python3 -c 'import json,sys;print(json.dumps(open("/tmp/rolling_check.rule").read()))')" \
     | ADS_MARKET=US python3 appctl.py rules-preview
```

Expected: `ok: true`, with `evaluated` in the tens of thousands. Preview is read-only — it proposes changes and executes nothing.

- [ ] **Step 9: Commit**

```bash
git add rules/runner.py rules/executor.py tests/rules_rolling_tests.py
git commit -F - <<'MSG'
Gate a rolling change on the table it was measured over

Every change now records the window it came from, so the executor can pick the
matching gate. A trailing-30 change still checks snapshot freshness. A rolling
change checks that the days in its window are actually there.

Those are different questions. A stale snapshot means the report job is
failing. A window with a hole in it still looks fresh, and summing it would
understate every entity by however much the missing day was worth.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

### Task 9: Serve the daily series and the coverage

**Files:**
- Modify: `appctl.py` — `cmd_history` (line 528), `cmd_health` (line 2330)
- Test: `tests/target_daily_tests.py` (extend)

**Interfaces:**
- Consumes: `target_daily` from Task 1.
- Produces:
  - `appctl history` gains `basis`, one of `"daily"` or `"trailing30_snapshot"`.
  - `appctl history` also gains `days_banked` (the number of distinct dates
    the returned points cover), and `first`/`last` (the first and last dates
    covered, or `null` when there are no points). These say how much of the
    series there is, not which series it is — `basis` can read `"daily"` on
    just a couple of banked days, and the chart needs its real span, not an
    implied full history. Populated the same way for both bases.
  - `appctl health` gains `target_daily` per market: `{days, first, last}`, or `null` when the table is empty or absent.

- [ ] **Step 1: Write the failing test**

Append to `tests/target_daily_tests.py`:

```python
class HistoryBasis(unittest.TestCase):
    """The chart must say which kind of number it is drawing. A trailing-30
    snapshot series and a true per-day series look identical and mean very
    different things."""

    def test_basis_names_the_source(self):
        import appctl
        self.assertTrue(hasattr(appctl, "_history_basis"))
        conn = memory_conn()
        self.assertEqual(appctl._history_basis(conn, "t1"), "trailing30_snapshot")
        banked(conn, ["2026-08-03", "2026-08-04"])
        conn.execute("UPDATE target_daily SET target_id='t1'")
        self.assertEqual(appctl._history_basis(conn, "t1"), "daily")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.target_daily_tests -v`

Expected: FAIL with `AssertionError: False is not true` on `hasattr`.

- [ ] **Step 3: Serve the daily series from `history`**

In `appctl.py`, replace `cmd_history` (line 528):

```python
def _history_basis(conn, entity_id, column="target_id"):
    """'daily' when target_daily carries rows for this entity, else
    'trailing30_snapshot'. The two series look identical on a chart and mean
    very different things, so the app has to be told which it is drawing."""
    try:
        row = conn.execute(
            f"SELECT 1 FROM target_daily WHERE {column}=? LIMIT 1",
            (str(entity_id),)).fetchone()
    except sqlite3.OperationalError:
        return "trailing30_snapshot"        # table absent on an old DB
    return "daily" if row else "trailing30_snapshot"


def cmd_history(args):
    """Dated performance series for ONE campaign / ad group / target.

    Prefers TRUE per-day rows (target_daily for targets and ad groups,
    campaign_daily for campaigns). Falls back to the banked nightly snapshots,
    where each point is that day's trailing-30 aggregate — drift over time
    rather than per-day numbers. `basis` says which, because the two look the
    same on a chart.
    """
    mkt = markets.current()
    conn = db.connect(ro=True)
    cur = conn.cursor()
    if args.campaign:
        entity, eid = "campaign", str(args.campaign)
        basis = "trailing30_snapshot"
        try:
            if cur.execute("SELECT 1 FROM campaign_daily WHERE campaign_id=? LIMIT 1",
                           (eid,)).fetchone():
                basis = "daily"
        except sqlite3.OperationalError:
            pass
        sql = ("""SELECT date, SUM(impressions), SUM(clicks), SUM(cost),
                         SUM(orders), SUM(sales)
                    FROM campaign_daily WHERE campaign_id=? GROUP BY date ORDER BY date"""
               if basis == "daily" else
               """SELECT date, SUM(impressions), SUM(clicks), SUM(cost),
                         SUM(orders), SUM(sales)
                    FROM campaign_perf WHERE campaign_id=? GROUP BY date ORDER BY date""")
    elif args.adgroup:
        entity, eid = "ad_group", str(args.adgroup)
        basis = _history_basis(cur, eid, column="ad_group_id")
        sql = ("""SELECT date, SUM(impressions), SUM(clicks), SUM(cost),
                         SUM(orders), SUM(sales)
                    FROM target_daily WHERE ad_group_id=? GROUP BY date ORDER BY date"""
               if basis == "daily" else
               """SELECT date, SUM(impressions), SUM(clicks), SUM(cost),
                         SUM(orders), SUM(sales)
                    FROM targeting_perf WHERE ad_group_id=? GROUP BY date ORDER BY date""")
    elif args.target:
        entity, eid = "target", str(args.target)
        basis = _history_basis(cur, eid)
        sql = ("""SELECT date, SUM(impressions), SUM(clicks), SUM(cost),
                         SUM(orders), SUM(sales)
                    FROM target_daily WHERE target_id=? GROUP BY date ORDER BY date"""
               if basis == "daily" else
               """SELECT date, SUM(impressions), SUM(clicks), SUM(cost),
                         SUM(orders), SUM(sales)
                    FROM targeting_perf WHERE target_id=? GROUP BY date ORDER BY date""")
    else:
        err("pass one of --campaign / --adgroup / --target")
    points = []
    for d, imps, clicks, cost, orders, sales in cur.execute(sql, (eid,)):
        imps, clicks, cost, orders, sales = [x or 0 for x in (imps, clicks, cost, orders, sales)]
        points.append({"date": d, "impressions": imps, "clicks": clicks,
                       "spend": round(cost, 2), "sales": round(sales, 2), "orders": orders,
                       "acos": _acos(cost, sales), "cvr": _cvr(orders, clicks)})
    note = ("true per-day totals" if basis == "daily" else
            "points are trailing-30 snapshots per pull date, not per-day totals")
    out({"market": mkt, "entity": entity, "id": eid, "basis": basis,
         "note": note, "points": points})
```

- [ ] **Step 4: Report coverage in `health`**

In `appctl.py`, in `cmd_health`, add inside the `try` block after the `entry["campaigns"]` line (line 2351):

```python
                # Rolling-window rules refuse to write when their window has
                # holes, so the operator needs somewhere to see the coverage.
                try:
                    cov = c.execute("SELECT COUNT(DISTINCT date), MIN(date), MAX(date) "
                                    "FROM target_daily").fetchone()
                    entry["target_daily"] = ({"days": cov[0], "first": cov[1],
                                              "last": cov[2]} if cov and cov[0] else None)
                except sqlite3.OperationalError:
                    entry["target_daily"] = None
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m unittest tests.target_daily_tests -v`

Expected: PASS, 24 tests.

- [ ] **Step 6: Verify against the real database**

Run:

```bash
ADS_MARKET=US python3 appctl.py health | python3 -m json.tool | grep -A 4 target_daily
TID=$(python3 -c "import sqlite3;print(sqlite3.connect('file:ads_data.sqlite?mode=ro',uri=True).execute('SELECT target_id FROM target_daily WHERE target_id IS NOT NULL LIMIT 1').fetchone()[0])")
ADS_MARKET=US python3 appctl.py history --target "$TID" | python3 -m json.tool | head -20
```

Expected: `health` shows about 92 days per market. `history` returns `"basis": "daily"` and one point per day.

- [ ] **Step 7: Commit**

```bash
git add appctl.py tests/target_daily_tests.py
git commit -F - <<'MSG'
Serve true per-day history, and say when it is not

The history endpoint now prefers the real per-day tables and falls back to the
trailing-30 snapshots when a target has no daily rows yet. It reports which one
it used.

That field is the point. A snapshot series and a per-day series look identical
on a chart and mean very different things, so the app should never have to
guess.

Health reports per-market daily coverage. A rolling-window rule refuses to
write when its window has holes, and the operator needs somewhere to see why.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

**Amended after review.** Two findings came back on the first pass, both
traced to this brief rather than the implementation:

1. The absent-`target_daily` paths (`_history_basis` on a table-less DB, the
   campaign branch's inline `campaign_daily` check, and `cmd_health`'s
   coverage block) had no test forcing the table to not exist —
   `memory_conn()` always creates it, so only "empty" and "has rows" were
   covered. Added `tests/target_daily_tests.py::AbsentDailyTables`, using a
   bare `sqlite3.connect(":memory:")` with no `target_daily` table at all.
   Extracted the `cmd_health` coverage block into `_target_daily_coverage(conn)`
   so it has something to call directly — same query, same return shape, just
   named and testable.
2. `_history_basis` reports `"daily"` on a single banked row, which is
   correct — the two series can't be concatenated — but this document's
   original wording ("`target_daily` covers the requested range") did not
   match that, and the shipped `history` response gave no way to tell a
   thinly-banked daily series from a fully-banked one. Fixed the wording
   above, and added `days_banked`/`first`/`last` to the `history` response
   (see Interfaces) so a two-day daily series is never charted as if it were
   the full 92. See `tests/target_daily_tests.py::HistorySpan`.

Both fixes are additive to the response shapes; no existing field changed
name or meaning. Full evidence in `task-9-report.md`'s fix-report section.

---

### Task 10: Show it in the app

**Files:**
- Modify: `MerchAds/Models.swift` — the history response model
- Modify: the Targets drill-down chart view and the System Health view (locate with `grep -rn "history" MerchAds/Views/`)
- Build: `scripts/package_app.sh --install`

**Interfaces:**
- Consumes: `basis` on the `history` response and `target_daily` on the `health` response, both from Task 9.
- Produces: no new engine interface.

**Before starting:** load the `macos-design-guidelines` and `swiftui-pro` skills, per the project's UI convention. Judge the result on the real running app via screenshot and self-critique against the HIG before presenting.

- [ ] **Step 1: Find the current shapes**

Run:

```bash
grep -rn "HistoryResponse\|struct HistoryPoint\|\"history\"" MerchAds/Models.swift MerchAds/Views/ | head -20
grep -rn "target_daily\|stale_tables\|latestData" MerchAds/Views/ | head -20
```

Read what you find before editing. The chart view and the System Health view are the two places that change.

- [ ] **Step 2: Add `basis` to the history model**

In `MerchAds/Models.swift`, add to the history response struct:

```swift
    /// Which kind of series the engine returned. `daily` is true per-day
    /// totals from target_daily. `trailing30_snapshot` is one trailing-30
    /// aggregate per pull date — drift over time, not per-day numbers. The two
    /// look identical on a chart, so the caption has to say which it is.
    let basis: String?
```

Add the matching `CodingKeys` entry if the struct declares them explicitly.

- [ ] **Step 3: Caption the chart honestly**

Add this to the chart view, directly below the `Chart { … }` block. Replace `Typography.caption` with whatever the caption token in `Typography.swift` is actually called — check it, don't guess:

```swift
/// A trailing-30 series and a per-day series look identical on a chart. The
/// caption is the only thing telling them apart, so it is not decoration.
private var basisCaption: String {
    history?.basis == "daily"
        ? "True per-day totals."
        : "Each point is that day's trailing-30 total, not a single day."
}

Text(basisCaption)
    .font(Typography.caption)
    .foregroundStyle(.secondary)
    .accessibilityLabel(basisCaption)
```

- [ ] **Step 4: Show coverage in System Health**

Add `targetDaily` to the per-market health model in `MerchAds/Models.swift`:

```swift
struct TargetDailyCoverage: Codable, Hashable {
    let days: Int
    let first: String?
    let last: String?
}
```

Add `let targetDaily: TargetDailyCoverage?` to the market health struct, mapping `target_daily` in its `CodingKeys`. Then render a row beside the existing per-table freshness rows:

```swift
/// Rolling-window rules refuse to write when their window has holes, so this
/// row is where the operator finds out why a rule went quiet.
if let coverage = market.targetDaily {
    LabeledContent("Per-target daily") {
        Text("\(coverage.days) days · \(coverage.first ?? "—") → \(coverage.last ?? "—")")
            .monospacedDigit()
    }
} else {
    LabeledContent("Per-target daily") {
        Text("not banked yet").foregroundStyle(.secondary)
    }
}
```

- [ ] **Step 5: Build and check for compiler errors**

Run:

```bash
xcodebuild -project MerchAds.xcodeproj -scheme MerchAds -configuration Debug \
  -derivedDataPath /tmp/merchads-derived build 2>&1 | tail -20
```

Expected: `BUILD SUCCEEDED`. Fix compiler errors and repeat until clean.

- [ ] **Step 6: Install the release build and relaunch from /Applications**

Run:

```bash
bash scripts/package_app.sh --install
pkill -x "Merch Ads"; open "/Applications/Merch Ads.app"
```

This is the standing rule: `/Applications` must never be left stale, and the temp DerivedData build must never be the running instance at the end of a turn.

- [ ] **Step 7: Screenshot and self-critique**

Open a target's drill-down chart and the System Health screen in the running app. Screenshot both. Check the caption reads correctly, the coverage row is legible, and the spacing matches the surrounding cards. Fix anything that looks wrong before presenting.

- [ ] **Step 8: Commit**

```bash
git add MerchAds/
git commit -F - <<'MSG'
Draw the per-day series, and caption which series it is

The chart now reads the basis field the history endpoint returns. A true
per-day series and a trailing-30 snapshot series look the same on a chart, so
the caption says which one is on screen.

System Health shows per-market daily coverage, because a rolling-window rule
refuses to write when its window has holes and the operator needs to see why.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

## Final verification

- [ ] **Run every test**

Run: `python3 -m unittest discover -s tests -p "*tests.py" -v`

Expected: PASS, including `snapshot_lint_tests` and `ytd_definition_tests` unmodified.

- [ ] **Confirm no existing rule changed meaning**

Run: `ADS_MARKET=US python3 appctl.py rules-list` then `rules-preview --rule <name>` for each existing rule. Compare `matched` against what it was before this branch. A rule with no `IN LAST` should match exactly what it matched before.

- [ ] **Confirm the nightly is whole**

Run: `bash -n run_scheduled.sh` and `ADS_MARKET=US python3 -c "import phase0_pull; print(list(phase0_pull.REPORTS))"`

Expected: five reports listed, with `targeting_daily` among them.

- [ ] **Update the review record**

Add a section to `docs/review-2026-08-04.md` recording that item 6 shipped, and correct the "snapshot diffs" claim in section 5 — the snapshots are rolling trailing-30 windows, so differencing them was never going to work. Leaving the wrong idea in the record invites someone to pick it up again.

- [ ] **Update the handoff contract**

Add `target_daily` to the table list and the `basis` field to the `history` entry in `docs/claude-code-handoff.md`. Note rolling-window support under the Rules DSL section, including which entities have it and that the window is lagged two days.
