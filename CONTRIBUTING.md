# Contributing

Thanks for looking. This started as one seller's private tool, so contributions are
genuinely welcome — you will see things I could not, because you run a different account.

---

## Before you write code

**Open an issue first** for anything larger than a bug fix. It saves you from building
something that does not fit, and it saves me from saying no to finished work.

Good first contributions:

- A bug you hit and can describe precisely.
- A gap in the documentation. You just walked the setup path with fresh eyes; I cannot.
- A market, product type or royalty case the economics get wrong.
- A rule in the DSL you wanted to write and could not.

---

## Ground rules

These are not style preferences. Each one exists because breaking it cost real money.

### 1. Never date one performance table from another

`campaign_perf`, `targeting_perf` and `search_term_perf` are each filled by their own
Amazon report job. Those jobs fail independently, so the tables drift apart.

Taking `MAX(date)` from one and filtering another with it matches **zero rows**, and the
caller then reports "no changes" instead of "no data". That silently froze bids, pauses,
harvesting and the profit figure for four nights.

Always resolve the date from the table you are about to read:

```python
date = db.latest_snapshot(conn, "targeting_perf")     # for reads
gate = db.snapshot_gate(conn, "targeting_perf")       # when a write depends on it
```

`tests/snapshot_lint_tests.py` enforces this across the whole engine. **If it fails, fix
your module. Do not widen the allowlist.**

### 2. Never sum snapshot rows

Those three tables hold **cumulative trailing-30 totals**, one row per pull date.
Consecutive rows overlap by 29 days. Summing them adds the same sales five times.

For true per-day numbers use `daily_totals`, `campaign_daily` or `target_daily`.

### 3. Fail closed

If economics are unavailable, if data is stale, if a rolling window has a hole — **refuse
to write**. Never substitute a default and carry on.

A missed optimisation costs a little. A bid computed from a stale or misread number costs
real money and is hard to notice.

### 4. Every write goes through the rails

A new write path must:

- call `killswitch.check()` before it touches Amazon,
- pass the relevant `db.snapshot_gate(...)`,
- go through `ads_client` so the bid and budget ceilings clamp it,
- log to `writes_log` with the previous value, so undo works,
- mirror the new state locally.

### 5. Swift never writes

The Mac app must not open a database for writing and must not call Amazon. Everything
mutating goes through `appctl.py`. This is what keeps one copy of every safety rail.

### 6. Preview before apply

Every new command that changes something needs a preview mode, and preview must be the
default. `--apply` is always opt-in.

---

## Development setup

```bash
git clone https://github.com/zdufs/merch-ads.git
cd merch-ads
python3 -m pip install -r requirements.txt
```

You can run the entire test suite with no credentials and no network:

```bash
python3 -m unittest discover -s tests -p '*_tests.py' -t .
```

Expect `Ran 419 tests ... OK`. Loud `BULK WRITE FAILED` lines in the output are
simulated failures inside passing tests — read the summary line.

Run one file while you work:

```bash
python3 -m unittest tests.rules_parser_tests -v
```

For the Mac app:

```bash
xcodebuild -project MerchAds.xcodeproj -scheme MerchAds \
  -configuration Debug -derivedDataPath /tmp/merchads-derived build
```

---

## Testing

**A behaviour change needs a test.** Put it in `tests/`, named `<area>_tests.py`.

Tests must not need credentials, network access or your real databases. Use a temporary
SQLite file. Look at `tests/snapshot_tests.py` for the pattern.

Some existing tests are **invariant guards** rather than feature tests, and they are the
most valuable ones here:

| Test | Guards |
|---|---|
| `snapshot_lint_tests.py` | Nobody dates one perf table from another. |
| `ytd_definition_tests.py` | Year-to-date is computed in exactly one place. |
| `batch_truth_tests.py` | A failed Amazon batch is never counted as applied. |
| `rules_conflicts_tests.py` | Two rules cannot silently overwrite each other. |

If you fix a bug that could come back, consider adding a guard like these instead of only
a regression test.

---

## Style

- **Match the surrounding code.** Naming, comment density, and structure.
- **Comments explain why, not what.** The most useful comments in this codebase record a
  decision or a past failure. Keep writing those.
- **Plain language in user-facing text.** Short sentences, one idea per sentence. That
  applies to commit messages, error strings and docs, not just to prose.
- Python: standard library first, `requests` is the only dependency. Keep it that way
  unless there is a very good reason.
- Swift 6, SwiftUI, system fonts and semantic colours only. See `DESIGN.md`.

---

## Commits and pull requests

- One logical change per commit. Write the message so it explains *why*.
- Branch off `main`. Never commit straight to `main`.
- Say in the PR description what you tested and what you did **not** test. If you could
  not test a market or a product type, say so — that is useful, not embarrassing.
- Confirm the test suite passes.

---

## What will probably be declined

- Anything that removes or weakens a safety gate.
- Anything that adds a runtime dependency without a strong reason.
- A rewrite of engine logic in Swift.
- A hosted or multi-tenant version. That is outside the [license](LICENSE) as well as
  outside the intent.
- Features that only make sense for one account's particular workflow. The Rules DSL is
  the extension point for those — use it rather than hard-coding.

---

## Security

Do **not** open a public issue for a security problem. See [SECURITY.md](SECURITY.md).

Before attaching any output to an issue or a PR, strip your profile ids, your ASINs and
your revenue figures. `appctl.py health` output in particular carries profile ids.

---

## Conduct

Be decent. See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
