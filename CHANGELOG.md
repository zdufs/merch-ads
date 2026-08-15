# Changelog

Notable changes to this project. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [0.2.0] — 2026-08-15 — first public release

The first version published outside the operation it was built for. The software itself is
not new; this release is about making it usable by someone other than its author.

### Added

- **`README.md`, `docs/SETUP.md`, `docs/SAFETY.md`, `docs/COMMANDS.md`,
  `docs/ARCHITECTURE.md`, `docs/TROUBLESHOOTING.md`** — documentation written for a new
  user rather than for the author.
- **`.env.example`** — every credential the engine reads, with comments and nothing unused.
- **`requirements.txt`** — one dependency, `requests`.
- **`scripts/install_launchd.sh`** — installs, reschedules or removes the nightly job, with
  the repository path resolved at install time.
- **`seasonal.example.json` and `tamas_launches.example.csv`** — starting points for the
  two operator data files. `seasonal.example.json` carries the eleven season windows and
  their keyword lists, with no ASINs.
- **CI** (`.github/workflows/tests.yml`) — the 419-test suite on macOS, plus an
  informational Linux run.
- **Contributor files** — `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, issue
  and pull request templates.
- **`LICENSE`** — Elastic License 2.0. Use it on your own account, including commercially;
  do not offer it to others as a hosted service.

### Changed

- **`run_scheduled.sh` resolves its own directory** instead of a hardcoded home path, so
  the nightly job works wherever the repository is cloned.
- **`io.github.zdufs.merchads.plist` became `io.github.zdufs.merchads.plist.template`**, with the path
  filled in at install time by `scripts/install_launchd.sh`.
- **`seasonal_pause.load_config()` seeds `seasonal.json` from the shipped example** when
  it is missing. A fresh clone starts with every design untagged, and seasonal pausing is
  a no-op until you tag something — rather than crashing on a missing file.
- **`killswitch.py` prints the actual `KILL` file path** in its instructions instead of one
  particular machine's path.

### Removed

- Operator data is no longer tracked: `seasonal.json` (a design catalogue) and
  `tamas_launches.csv` (a launch list) are gitignored and shipped as examples instead.
- Internal working notes that were specific to one operation, and a third party's private
  community notes, were dropped from the published tree.

---

## Before 0.2.0

The engine and app were developed privately between June and August 2026. The public
history starts fresh, so this is a summary of what already existed at first release rather
than a commit-by-commit record.

### The engine

- **Amazon Ads API client** with token refresh, transient-failure retries, asynchronous
  report polling, per-batch response checking and write ceilings.
- **Six Merch marketplaces** (US, UK, DE, FR, ES, IT), one SQLite database each, all in
  WAL mode.
- **Royalty-aware economics.** Price-aware US tee economics landed 2026-07-12: each
  design's break-even ACOS derives from its own current list price, with 30-day price
  transition windows and a freshness gate that fails closed.
- **The nightly phases** — pull, map, dry run, harvest, negatives, pauses, bids, campaign
  building, seasonal scheduling.
- **Campaign strategies** — lottery, scavenger, TAMAS, and harvest promotion.
- **TRAZ and organic halo** (US only) — estimates the organic lift a design got while
  advertised, presented explicitly as an upper bound.
- **Rules DSL** (2026-08-01) — an operator-authored automation language with economics as
  first-class fields, preview before apply, and an approval queue.
- **Rolling `IN LAST N DAYS` windows** (2026-08-06), made possible by `target_daily`, the
  first true per-day per-entity table.
- **Cross-rule conflict guard** (2026-08-07) — two rules can no longer silently overwrite
  each other on the same entity.
- **KDP books** (2026-08-02) as a separate advertiser profile, with Amazon's published
  royalty formula and fail-closed economics.
- **Nightly `campaign_daily` banking** (2026-08-09), so campaign rolling-window rules stay
  fresh all week instead of only after Monday.
- **Daily gap-fill** (2026-08-14) — a market whose daily report timed out is filled in on
  the next run rather than leaving a permanent hole.

### The Mac app

- **27 screens** over the engine: dashboard, campaigns, targets, profit, kill list,
  approval queue, audit trail, rules editor, reports, cross-purchase, seasonal, halo,
  imports, system health and more.
- **A worker pool** keeping one `appctl.py serve` process per market, so reads cost about
  5 ms instead of about 50 ms.
- **Action coordinator** with a rehearsal mode, so every mutating call funnels through one
  place.
- **Command palette, inspectors and saved views.**
- **Appearance-aware** with a System / Light / Dark setting (2026-08-14).

### Invariant guards

Several tests exist specifically to stop a class of bug from returning:

- `snapshot_lint_tests.py` — nobody may date one performance table from another. That bug
  silently froze bids, pauses, harvesting and the profit figure for four nights before the
  guard existed.
- `ytd_definition_tests.py` — year-to-date is computed in exactly one place. It was once
  computed three ways, and two of them disagreed by more than double.
- `batch_truth_tests.py` — a failed Amazon batch is never reported as applied.
- `rules_conflicts_tests.py` — the conflict guard stays honest.

---

[0.2.0]: https://github.com/zdufs/merch-ads/releases/tag/v0.2.0
