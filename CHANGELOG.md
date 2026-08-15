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
- **`seasonal.example.json`** — a starting point for the operator's season config. It
  carries the eleven season windows and their keyword lists, with no ASINs.
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

- Operator data is no longer tracked: `seasonal.json` (a design catalogue) is gitignored
  and shipped as an example instead.
- Internal working notes that were specific to one operation, and a third party's private
  community notes, were dropped from the published tree.

---

## [0.2.4] — 2026-08-15

### Added

- **The release checks are a push gate now.** Every fresh snapshot carries a pre-push
  hook that re-runs all the leak checks against the exact tree being pushed. A tree
  that fails cannot be pushed. The checks exist because they caught real leaks that
  careful review missed; a check you have to remember is not a check.
- **Sales import shows its ledger** — every banked file, with the period it covers and
  the rows it added. The engine always sent it; the screen used to drop it.
- **Profit shows its coverage.** A Coverage card states what share of spend can be
  assigned to a single design, and a line under the tables states how much multi-ASIN
  cohort spend is excluded from the profit figures.
- **Organic Halo shows Net halo** (the lift minus the ad spend) and loads every design
  instead of stopping quietly at 300.

### Changed

- **Every screen's instruction text was audited against the engine** — five parallel
  audits, 48 findings, all fixed. Help entries, tooltips and dialogs no longer describe
  retired UI, wrong data sources, or elements that are not on the screen.
- Kill List's Stale view says "showing top N" when the engine caps the list.
- Build all markets skips KDP profiles — the engine refuses tee builds there, so the
  loop only manufactured a failure row.
- Snapshot commits use the GitHub noreply address instead of a personal one.

### Fixed

- **Approval-queue and nightly phase-2 negatives are undoable now.** Those two paths
  dropped the created keyword id, so the same negative was one Undo from gone when added
  by right-click or a rule, and permanent when added from the queue. Both paths log the
  id now, and two new tests pin them (suite: 438).
- **Settings' "appctl.py Found" badge** checked the pre-`engine/` location and said
  "Not found" forever, while the app worked. It uses the bridge's resolver now.
- **The Errors tab's copy-fix commands** were missing the `engine/` prefix and failed
  when pasted. Same for the export-adoption failure hint.
- **`--verify-only` no longer rewrites the tree it judges.** It used to run the
  private-file removal and the owner substitution against the tree it was only supposed
  to verify.
- The Ads history import no longer replaces every engine refusal with the sales-report
  hint. Instructive refusals — like the year-ambiguity message that says how to re-export
  — pass through to the user.

---

## [0.2.3] — 2026-08-15

### Changed

- **The Python engine lives in `engine/`.** The repository root held 78 loose files, 55
  of them modules. It is 14 now: README, LICENSE, the contributor files, and the config
  a fresh clone needs.
- **`engine/paths.py` is the single definition of where things are**, exporting
  `ENGINE_DIR`, `REPO_ROOT` and `POD_ROOT`. Twenty-four modules each derived the
  repository from their own `__file__`, so every data path — `.env`, the databases,
  `KILL`, `outputs/`, `seasonal.json` — carried its own copy of the answer. There are
  genuinely two roots: your data sits in the repository, and the Merch catalogue exports
  and dated `SALES_REPORT` sit in the folder above it.
- **The Mac app's sources are grouped by role**, and `Views/` is split one folder per
  sidebar section, so the folder tree and the app's navigation agree.
- **Documented commands now read `python3 engine/<script>.py`.** The Swift bridge
  resolves `engine/appctl.py` and still accepts the flat layout, so an existing engine
  folder setting keeps working with no user action.

### Fixed

- **`products._newest_export` silently closed the economics gate.** It imports `os` under
  an alias, so a sweep for the plain `os.` prefix did not see it; it kept walking up from
  its own file, landed on the repository instead of the folder above, found no catalogue
  export, and reported "no adopted export". A closed gate blocks every economics-driven
  write. Found by running `econ-gate` on both layouts and comparing, not by reading the
  diff.

### Notes on verification

A path refactor fails quietly — a wrong path reads an empty database rather than raising.
So this one was checked by comparison, not inspection: 19 read endpoints run against the
real databases on both layouts and diffed byte-for-byte, every resolved path compared
before and after, and a real nightly phase previewed through the new layout.

---

## [0.2.2] — 2026-08-15

### Changed

- **Phases 2 and 3 skip campaigns on STATE, not on name.** They skipped a retired
  strategy's campaigns under a comment reading "campaigns that run on their OWN
  optimizers" — but that optimizer was deleted in 0.2.1, and every one of those
  campaigns was archived. The name test had become a stand-in for a state test. Both
  phases now skip scavenger, which genuinely has its own optimizer, plus anything not
  ENABLED. **This is a behaviour change:** Amazon keeps reporting trailing-30 rows for
  paused and archived campaigns, so they surfaced in the perf tables regardless of
  state. A PAUSED standard campaign was not skipped before and now is.
- **One campaign classifier.** `campaign_kinds.py` is the only place that decides what
  kind a campaign name describes. `appctl` had one and the halo estimator grew a second
  in 0.2.1; both call the shared one now, and a test fails if either regrows its own.

### Removed

- The retired strategy's name, everywhere it shipped: its module, its per-market
  capability flag, that flag's copy in the `markets` reply, its Discord digest bucket
  and stats field, and the docstrings in `traz`, `scavenger` and `scavenger_optimize`
  that defined those modules by contrast with it.
- `docs/superpowers/` no longer ships. Those are dated internal planning documents
  recording a working process and the branch names of the day — editing them to match
  today's naming would falsify the record, and a stranger reading the repo does not
  need them.

### Fixed

- **A launch list of 27 real ASINs was committed** when the ignore rule naming the
  retired strategy was removed along with everything else. The release verifier caught
  it before publication. The rule is generic now (`*_launches.csv`, with an exception
  for `*_launches.example.csv`).

### Added

- The release verifier fails if the retired strategy's name, or a real ASIN, appears
  anywhere in the published tree.

---

## [0.2.1] — 2026-08-15

### Removed

- **A retired manual campaign strategy** — one broad keyword and one ASIN per campaign,
  fixed bids, judged on royalty minus ad spend rather than ACOS. Its optimizer, launcher
  and candidate finder are deleted, along with its nightly step and its candidates
  endpoint.

### Changed

- **The organic-halo estimate now covers every campaign type.** It was scoped to the
  retired strategy above, keyed on campaigns that held exactly one ASIN — a shape a
  1,000-ASIN lottery campaign cannot have, so it could describe 27 designs and no others. The unit is now the DESIGN, with
  ad facts summed across every ad group advertising it. On the account it was built for
  that took it from 27 designs to 782, spanning lottery, scavenger, standard and
  harvested. New `campaign_types` field, and a Campaigns column in the app.
- **Halo ad facts come from `target_daily`**, which holds true per-day rows. The old
  version read the newest `campaign_perf` snapshot as "total spend" — but those rows are
  cumulative trailing-30 totals, so it was reporting a trailing-30 figure as a lifetime
  one.
- **`halo.analyze()` takes an optional `conn`.** Without it the rules DSL, evaluating
  against a temporary database, silently read halo from the real market database. That
  also made the DSL test file 7.6s; it is 0.1s now.
- `appctl halo` gained `--min-spend` and `--limit`.

### Fixed

- **The retired strategy's optimizer called Amazon every night for nothing.** It selected
  campaigns by name with no state filter, so archived ones counted as live, the "nothing
  to do" early return never fired, and it fetched keywords for campaigns that could not
  serve. Moot now that the module is deleted, but the same shape was checked for across
  the other optimizers.

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
- **Campaign strategies** — lottery, scavenger, and harvest promotion.
- **Organic halo** (US only) — estimates the organic lift a design got while advertised,
  presented explicitly as an upper bound.
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

[0.2.3]: https://github.com/zdufs/merch-ads/releases/tag/v0.2.3
[0.2.2]: https://github.com/zdufs/merch-ads/releases/tag/v0.2.2
[0.2.1]: https://github.com/zdufs/merch-ads/releases/tag/v0.2.1
[0.2.0]: https://github.com/zdufs/merch-ads/releases/tag/v0.2.0
