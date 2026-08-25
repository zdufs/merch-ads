# CLOSED: the trailing-30 window is 30 days everywhere, and always was

Measured 2026-08-24, settled 2026-08-25 by the instrumented run. No code change
followed, and the doc's own criteria are why. Kept because the wrong answer was
convincing and someone will measure this again.

## The answer

**The window is 30 days. It does not track snapshot age, it does not differ by
market, and Amazon returns exactly what it is asked for.**

The 2026-08-25 run logged 65 `[window]` lines across seven markets. Every
REQUESTED line asks for the same thing:

    [window] DE campaigns REQUESTED start=2026-07-26 end=2026-08-24 unit=SUMMARY days=30 report=f4a6739d-…
    [window] US campaigns REQUESTED start=2026-07-26 end=2026-08-24 unit=SUMMARY days=30 report=8d8e431d-…

and every RETURNED line — Amazon's own echo, same report id — comes back with
the identical start, end and day count:

    [window] DE campaigns RETURNED start=2026-07-26 end=2026-08-24 days=30 stored_as=2026-08-24 report=f4a6739d-… rows=56 cost=129.73
    [window] US campaigns RETURNED start=2026-07-26 end=2026-08-24 days=30 stored_as=2026-08-24 report=8d8e431d-… rows=87 cost=931.36

So criterion 1 (is the REQUEST 30 everywhere?) is yes, and criterion 2 (does
Amazon widen it?) is no. Both of the fixes this note held in reserve were
conditional on one of those being false. Neither is. **Nothing needed changing.**

## What the 31 days actually was

A hole in `campaign_daily`, on the measuring side — not in the window being
measured.

The original test summed `campaign_daily` over the N days ending at the
snapshot's date. That only works if `campaign_daily` HAS all N of those days. In
a market whose daily banking is one day behind the snapshot, the last day of the
window is missing, so a 30-day sum contains 29 days of data and reads low.
Reaching back a 31st day adds a day at the front to replace the one absent at
the back, and the total lands on the snapshot. It looks like a 31-day window. It
is a 30-day window with one day moved.

Recomputed from the 2026-08-25 run:

| market | snapshot | campaign_daily days present | 29d | 30d | 31d | 32d |
|---|---|---|---|---|---|---|
| US | 2026-08-24 | 30/30 | -4.1% | **+0.0%** | +3.3% | +6.5% |
| FR | 2026-08-23 | 30/30 | -2.7% | **+0.0%** | +2.4% | +4.7% |
| ES | 2026-08-23 | 30/30 | -3.1% | **+0.0%** | +2.7% | +4.8% |
| IT | 2026-08-23 | 30/30 | -2.9% | **+0.0%** | +2.8% | +4.5% |
| UK | 2026-08-24 | 29/30 | -5.6% | -3.5% | **+0.0%** | +1.1% |
| DE | 2026-08-24 | 29/30 | -7.6% | -4.3% | -2.4% | **-0.1%** |

Every market with a complete `campaign_daily` reconciles at exactly 30 days.
Every market missing a day does not. The correlation is total, and it runs the
opposite way to the age theory: FR, ES and IT hold the OLDER snapshot here
(08-23) and are the ones that land on 30, while UK holds the newer one and does
not. DE is two days out because its Monday true-up was killed, which this note
already recorded.

That also explains the observation that made the age theory look right. A market
whose pull is a day behind is a market whose daily banking is a day behind — the
same nightly does both — so snapshot age and the missing day move together. IT
flipped into the 30-day column "once its pull succeeded" because that pull banked
the day `campaign_daily` was missing.

## What this means for the engine

Nothing is wrong and nothing was renamed. Every threshold in the engine is
applied to a genuine 30-day window, in every market. The 2026-08-24 worry — that
the narrowing from 31 to 30 days never reached EU, so EU thresholds were being
met on 3% more evidence than they said — was unfounded.

One thing to carry forward: **reconciling a snapshot against `campaign_daily`
requires checking that the window's days are all present first.** Counting
distinct dates in the range is the check. Without it the comparison silently
answers a question about banking coverage and reads as an answer about window
width.

---

*Everything below is the note as written on 2026-08-24, kept for the reasoning.*

## Correction, same day, after the run finished

The first version of this note called it EU versus US. That was wrong, and IT
disproved it within the hour. IT's pull completed, its snapshot moved from
2026-08-22 to 2026-08-23, and it then reconciled over THIRTY days like US and
UK — without anything else changing.

The split is by SNAPSHOT AGE, not by region:

| newest snapshot | markets              | reconciles at |
|-----------------|----------------------|---------------|
| 2026-08-23      | US, UK, IT, USKDP    | 30 days       |
| 2026-08-22      | DE, FR, ES           | 31 days       |

So the question is not "why is EU different". It is "why does a snapshot whose
END is one day older span one day MORE". Every market lands in the 30-day
column once its pull succeeds, which is why this was invisible until three
markets happened to be a day behind at the same time.

## The fact (as first measured, EU/US framing now superseded)

Sum `campaign_daily` over the N days ending at a market's newest
`campaign_perf` snapshot, and compare to that snapshot's own total spend:

| market | snapshot   | 29d   | 30d   | 31d   | 32d   |
|--------|------------|-------|-------|-------|-------|
| US     | 2026-08-23 | -3.2% | **0.0%** | +3.3% | +6.6% |
| UK     | 2026-08-23 | -3.5% | **0.0%** | +1.1% | +4.3% |
| ES     | 2026-08-22 | -4.9% | -2.2% | **0.0%** | +2.9% |
| FR     | 2026-08-22 | -4.6% | -2.2% | **0.0%** | +4.8% |
| IT     | 2026-08-22 | -4.5% | -1.7% | **-0.0%** | +3.3% |

The shortfall is exactly one day, not noise. ES's 30-day shortfall is 5.49,
and ES spent 5.49 on 2026-07-23 — the day just outside the 30-day window, to
the cent. IT is 4.13 against 4.06, within attribution drift.

It is not a missing campaign: ES has 57 campaigns in both tables, none only in
one, and every campaign is short by roughly the same proportion.

DE is excluded from the table above: its Monday true-up was killed, so its
`campaign_daily` still carries stale attribution and it cannot be read this way
until it is re-run.

## Why it might matter

`phase0_pull.py` line 29 says every threshold in the engine — spend floors,
click minimums, ACOS against break-even — is applied to this window under the
name "trailing 30". The same comment records that the window WAS 31 days and
was narrowed, "so each was being met on about 3% more evidence than it said".
If the EU windows are still 31 days, that correction did not reach them.

The size is about 3%. Small, and it moves thresholds in the permissive
direction.

## What was checked and did not explain it

- `END = today-1` and `START = today-30` is 30 days inclusive, and there is no
  per-market adjustment anywhere in `phase0_pull.py` or `markets.py`.
- The stored date is `END` (`STORERS[key](conn, rows, END)`, line 264), so an
  08-22 snapshot was written on 08-23, whose window is 07-24..08-22 — thirty
  days. The data needs 07-23 as well.
- ES's pull SUCCEEDED today (`Done. Stored`, no "NOT REQUESTED") and still left
  `MAX(date)` at 08-22. That part is consistent with the documented EU
  reporting lag: Amazon returns nothing yet for 08-23, zero rows are stored,
  and the step reports success.

## What would settle it

Log the exact `startDate`/`endDate` sent per market on the next pull and read
back the row count per date. If Amazon is returning a wider window than asked
for in EU, that is Amazon's boundary and the fix is to date the snapshot from
the rows rather than from `END`. If the request itself is wider, it is ours.

Do NOT "fix" this by widening or narrowing the comparison until the cause is
known. The number on the screen is not currently wrong; only the name of the
window may be.

## The instrumentation is in place (2026-08-24)

The run log already carried one window line per market:

    [DE] profile 900000000067890 | window 2026-07-25 → 2026-08-23

That is `phase0_pull.START` and `END`, printed once at line 337. It was NOT
enough to answer this, in three ways. It speaks only for the three trailing-30
snapshots — `targeting_daily` asks for its own shorter window and was covered
by a line that named a different one. A RESUMED report was requested on some
earlier day whose start is not stored anywhere, so the line described a request
that was never made this run. And nothing recorded what Amazon actually built.

`phase0_pull.window_note()` now prints one line per report, to STDERR, which
`run_scheduled.sh` sends to the same log:

    [window] DE campaigns REQUESTED start=… end=… days=30 unit=SUMMARY report=…
    [window] DE campaigns RETURNED  start=… end=… days=… stored_as=… rows=… cost=…
    [window] DE campaigns RESUMED   end=… report=…
    [window] DE campaigns RECOVERED start=… end=… days=… stored_as=… rows=… cost=…

`RETURNED` is Amazon's own echo, read from the report metadata that
`get_report` used to throw away. It costs no extra call — it is the same GET
the poll loop already makes. `cost` is the returned rows' own spend, which is
the number the table above compares against `campaign_daily`. `row_days` /
`row_first` / `row_last` appear only for the DAILY report, because a SUMMARY
report carries no date column and must not claim days it cannot see.

### What to read tomorrow

After the 10:00 run:

    grep '^\[window\]' outputs/scheduled_runs.log | grep -v RESUMED

Read it per market, three ways:

1. **REQUESTED `days=`** — is it 30 everywhere? If any market asks for 31, the
   request is ours and `phase0_pull`'s constants are where to look. Nothing in
   the code is per-market today, so this would be a surprise.
2. **RETURNED `days=` against the REQUESTED `days=` for the same report id.**
   Different means Amazon widened the window. That is Amazon's boundary, and
   the fix is to date the snapshot from the rows rather than from `END`.
3. **RETURNED `cost=` against `campaign_daily`** summed over the returned
   window. This is the table at the top of this note, computed from the run
   itself rather than reconstructed afterwards.

DE, FR and ES are the markets that were in the 31-day column, and the split was
by SNAPSHOT AGE rather than by region, so read whichever markets are a day
behind on the morning you look — not whichever ones are in Europe.

The warning above still stands. This change only measures. Nothing about the
window, the storage date or any comparison was adjusted.
