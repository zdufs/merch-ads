# MerchAds — Amazon Ads automation + macOS app

Two layers in one folder: a **working Python ads-automation engine** (Amazon Ads API for merch/POD, 6 markets: US UK DE FR ES IT, per-market SQLite `ads_data[_<CODE>].sqlite`, nightly `run_scheduled.sh` via launchd `io.github.zdufs.merchads` at 10:00) and **MerchAds**, the native SwiftUI Mac app on top of it.

**Ground truth lives in the docs — read before building:**
@docs/claude-code-handoff.md

Also: `docs/marketing-stream.md` (Amazon Marketing Stream — the hourly push channel; LIVE since 2026-08-21: both US subscriptions ACTIVE, queues in us-east-1, hourly drain installed from the app bundle), `docs/swiftui-app-plan.md` (product spec), `docs/multi-market.md`, `docs/bidding-rules.md` (incl. the lottery-campaign routing policy: standard tees fill existing lottery campaigns to 1000 ASINs before new ones are created — dictated by the operator 2026-07-03 and committed to engine + handoff), `docs/rules-dsl.md` (rules authoring guide).

**`docs/review-2026-08-04.md` — the full engine + app review, and what was done about it.** Read it before proposing "improvements": it already says which features earn their place, which are built but idle, what was fixed across twelve blocks on 2026-08-05, and what was deliberately closed without code (Liquid Glass, the removed AI copilot; forced light was later reversed — the app is appearance-aware with a System/Light/Dark setting since 2026-08-14, see DESIGN.md). It also records the standing verdicts on the disk I/O errors and the UK/DE report staleness, so neither gets re-diagnosed from scratch.

## Operator conventions (standing)
- **Live-account writes are operator-run.** Any command that mutates the production Amazon Ads account (`setbid`, `pause`, `pause-campaign`, `negatives-apply`, `run --phase promote`, campaign creation) — even when the milestone was "authorized generally" — is pre-staged by Claude as the exact `ADS_MARKET=<code> python3 engine/appctl.py …` line and executed by the operator via `!`. The auto-mode classifier blocks them anyway; this is a deliberate human gate, not friction to engineer away.
- Reads/analysis are unrestricted: `appctl.py` read endpoints, read-only SQLite (`?mode=ro`), report backfills.
- **Never date one perf table from another (standing rule, codified 2026-08-04 after the second occurrence).** `campaign_perf`, `targeting_perf` and `search_term_perf` are each filled by their own Amazon report job. Those jobs fail independently, so the tables drift apart. Taking `MAX(date)` from one and filtering another with it matches ZERO rows, and the caller then reports "no changes" instead of "no data". That silently froze US bids, pauses, harvest and the dashboard's Estimated profit for four nights in Aug 2026. Always resolve the date from the table you are about to read: `db.latest_snapshot(conn, "<table>")`, or `db.snapshot_gate(conn, "<table>")` when a write depends on it (fails closed past `db.SNAPSHOT_STALE_AFTER_DAYS`, like the econ gate). `tests/snapshot_lint_tests.py` enforces this across the whole engine — if it fails, fix the module, don't widen the allowlist.
- **The shipped economics ARE the operator's numbers (promoted 2026-08-21).**
  Every royalty in `products.PRODUCT_ECON`, `US_TEE_ROYALTY_CENTS` and the new
  per-market `MARKET_PRODUCT_ECON` was read off the Merch dashboard by the
  operator. Nothing is extrapolated and nothing is an export median any more, so
  the app shows "built-in" everywhere. `PRODUCT_PRICE` /
  `MARKET_PRODUCT_ECON[m][t][2]` hold the REAL list price each royalty came
  from — never divide royalty by break-even to get a price, it lands cents off.
  `products.list_price_for()` is the one place that answers "what price is this".
  Amazon fixes a maximum price per product per market, so these are caps.
  `tests/shipped_economics_tests.py` checks the arithmetic of every row.
- **Royalties are edited in the app, not in `products.py`.** The Product Royalty
  tab writes `royalty_overrides.json` (gitignored) and `engine/royalty_config.py`
  merges it over the built-in US tables. So do NOT hand-edit
  `US_TEE_ROYALTY_CENTS` or `PRODUCT_ECON` when the operator gives you a new
  royalty — that is exactly the dependence on Claude Code this tab removed.
  Stage `ADS_MARKET=US python3 engine/appctl.py royalty-set …` instead, or just
  point at the tab. The built-in tables stay the shipped floor and keep their
  self-assert. EVERY market is editable and overrides are stored per market —
  an untouched non-US row still comes from `derive_econ.py`'s export median, and
  an operator number beats it, because Amazon fixes a maximum price per product
  per market. Only US has the tee price ladder.

- **Marketing Stream: resolve a message's market through its CAMPAIGN ids, never
  through `marketplace_id`.** Merch US and KDP US both advertise on
  `ATVPDKIKX0DER` — verified 2026-08-21 against `/v2/profiles`, which reports that
  marketplace for "Sponsored ads - KDP" while live Merch US Stream rows carry it
  under a different entity. Using the marketplace would merge two separate
  advertisers into one number. `engine/stream_map.py` is the one place that knows
  this; it caches the answer per `advertiser_id` and REPORTS an advertiser that
  nothing claims, or that two markets claim, rather than guessing.
- **Only `stream-verify` can prove Stream is not dropping data.** Every other
  check proves the pipeline reads faithfully what ARRIVED — dedupe, coverage,
  arithmetic, queue depth. None of them can see Amazon sending less than it
  should, and that failure stays internally consistent all the way to the
  screen: totals, placements and hours all add up, and the number is quietly
  low. `stream-verify` measures one SETTLED day twice, from Stream and from
  `campaign_daily`, and compares per campaign; the `stream_undercount` alert
  runs it without being asked. It REFUSES days Stream could not have seen
  whole — never "fix" that by widening what it will judge, because an alarm
  that fires on every subscription's first day gets muted.
- **Mutation-test the Stream guards, do not just add tests.** Two of the tests
  written for this pipeline passed for the wrong reason and one constant had
  never been exercised at all; both were found by deliberately breaking the
  code and checking that something failed, not by reading it. The harness
  pattern is in the 2026-08-21 session: copy the module, patch one line, run
  `tests.stream_map_tests tests.stream_tests tests.stream_verify_tests`,
  restore. A mutation nothing catches is either a hole or an equivalent
  change — decide which before moving on.
  **Clear `__pycache__` between mutations.** CPython decides a `.pyc` is current
  from the source's mtime and size, and both have one-second resolution. A
  mutate-test-restore loop rewrites one file several times inside a second, so
  the interpreter can keep running the previous version. That reads as a
  mutation nothing caught, or as a fix that did not take. It cost half an hour
  on 2026-08-21 on a test that failed under `discover` and passed under a direct
  module run, with correct source on disk both times. Move them aside before
  believing any result: `find . -name __pycache__ -not -path './.git/*'`.
- **A Stream drain that "ran" is not a drain that finished.** Stream sends
  roughly ONE MESSAGE PER IMPRESSION, so a US day is on the order of ten
  thousand messages and SQS hands them over about ten at a time. The first
  hourly job budgeted 60 seconds, which reads fewer messages than a single hour
  delivers, so the queue grew all day and the Dashboard's live totals were an
  undercount that worsened hourly — while every run logged a healthy count of
  messages banked. The budget is 300s per queue now, `drain_queue` returns
  `exhausted`, and a queue that was still full is reported through
  `stream_store.health()` as `drain_backlog` and drawn amber on System Health.
  Never judge the drain by its message count alone. Guarded by
  `tests/stream_tests.py::DrainExhaustion`.
- **An hour that ARRIVED is not an hour that is WHOLE, and an hour we were not
  LISTENING for was never lost.** Amazon sends a short catch-up when a
  subscription is created and promises nothing about how far back it reaches,
  so every hour that began before the first message ever arrived holds a
  fragment — or nothing at all. `stream_map.listening_since()` is
  `MIN(received_at)`, and `coverage.partial_hours` names EVERY hour that began
  before it, whether or not the catch-up carried anything for it. Only an hour
  we were actually listening for can be `missing_hours`, because that is the
  set the app prints as "never arrived" — a sentence that has to keep meaning
  data was lost and cannot be recovered. Both halves came from the same live
  day: collapsing partial into delivered made day one read as a 90% collapse in
  spend (Amazon said 7,40 US$, the panel said 2,49 US$ and blamed "5 hours
  never delivered"), and then calling the empty pre-subscription hours missing
  accused Amazon of dropping three hours it had never been asked to send.
  Guarded by `tests/stream_map_tests.py::Coverage`.
- **The two Stream datasets dedupe differently, and the split is load-bearing.**
  `sp-traffic` rows are DELTAS (impressions 1 or 2, corrections -1), so many
  share one hour/ad/keyword/placement on purpose: key them on
  `idempotency_id` ONLY, keep an id-less message rather than collapsing it, and
  report the count as `unkeyed_messages`. `sp-conversion` rows are RESTATED
  SNAPSHOTS carrying a 1d/7d/14d/30d ladder, resent as attribution grows: key
  those on the row's natural grain, newest wins, because summing restatements
  invents sales and inflated sales flatter ACOS into a bid-up. One rule for
  both silently threw away most of an hour of traffic. Guarded by
  `tests/stream_map_tests.py::Deduplication` and `::Conversions`.
- **Negative Stream numbers are corrections, not bugs.** `impressions: -1` is
  Amazon backing out traffic it reported earlier. Summing everything is correct
  and needs no special case. On the FIRST day only, corrections arrive for hours
  whose originals predate the subscription, so an early hour can show a negative
  count — real, small, and self-correcting.
- **A Stream day is not a settled day.** Stream never resends, so an hour that was
  not delivered is gone for good and that day's total is an undercount.
  `stream-today` reports `coverage.missing_hours` and the app draws them as gaps.
  The nightly report stays the source of truth for a finished day. Never add a
  Stream number to a banked report number — different bases, about a day apart.
  And never show sales, ACOS or CVR from Stream while `sp-conversion` is empty:
  sp-traffic does not carry them, and a zero reads as "sold nothing" rather than
  "cannot see sales yet". Once it does deliver, read the **30-day** columns
  (`stream_map.ATTRIBUTION`) because that is what `phase0_pull` and
  `daily_metrics` read — and remember a conversion is dated to the CLICK hour,
  not the purchase, so a message arriving tonight with a six-day-old window
  belongs to that day. Still no ACOS for a day in progress: the spend is complete
  and the sales are not.
- **After a missed night, run the catch-up — not six pulls by hand.**
  `python3 engine/catchup.py [--markets UK DE …]` asks every market for its
  reports first, then collects in rounds until nothing is pending. Amazon builds
  reports slower than any single poll window, so asking and collecting are always
  two passes. The nightly hides that by collecting the next night; a catch-up has
  no next night, and doing it by hand cost three rounds of babysitting on
  2026-08-20. `phase0_pull.py` and `daily_metrics.py` take `--max-wait SECS`
  (0 = ask and exit) and the pull takes `--reports-only` for the cheap rounds.
- **Diagnostics go to STDERR, in every module appctl calls IN-PROCESS.**
  `appctl` promises exactly one JSON object on stdout and the app decodes it
  with Codable. A `print()` anywhere in that call tree lands in the pipe ahead
  of the envelope. The `serve` worker sinks stray stdout, so a leak there is
  INVISIBLE — the same command run one-shot is not, which is how
  `harvest_prune`'s econ-gate notice survived until the 2026-08-21 audit. The
  app coped only because its decoder rescans lines; `jq`, a script, or any
  future caller would not. `tests/serve_protocol_tests.py::OneShotStdoutContract`
  runs every economics-driven command against an EMPTY data folder — which
  closes the gate and takes the branch that prints — and fails on anything that
  is not one clean envelope. If it fails, move the print, don't shorten the list.
  **`tests/stdout_contract_lint_tests.py` is the half that check cannot do.**
  It reads the CALL GRAPH and fails on any `print()` without `file=`. That is
  how the WRITE paths are covered — `harvest_prune._pause_batch` is reached from
  `harvest-prune-apply`, which needs a live Amazon client and an approved plan,
  so no runtime test will ever see it. It was missed in 0.4.2 for exactly that
  reason, two lines below the call that got fixed. The rule has no exceptions on
  purpose: a print that is safe only because its caller passes `verbose=False`
  is lucky, not safe.
  **The walk starts at appctl and leaves its module (widened 0.4.5).** The first
  version began at the modules appctl NAMES and stayed inside each one. Three
  kinds of leak fitted through that, and the 2026-08-21 audit found one of each:
  appctl's OWN handlers were never read (`cmd_kdp_titles`); a call that left its
  module was dropped (`sales-report` reaches `db.bulk_write` through
  `sales_import.bank`, and `stream-drain` reaches `stream_sqs.delete_batch`);
  and a call on an OBJECT was dropped. The last is the one that mattered:
  `AdsClient` is built inside about fourteen appctl handlers, so EVERY live
  write ran through `_send_retry`, which printed its 429 and 5xx backoff notices
  to stdout. Amazon throttles routinely — that is why the retry exists — so
  `appctl setbid` answered with two lines of plain text and then the envelope.
  So: every appctl function is a root, a `module.function()` call is followed
  into that module, and a method call is followed when the local variable was
  built from an engine class in the same function. `engine/rules/` is read too.
  A print is allowed only with `file=`, or when its single argument is
  `json.dumps(...)` — that shape IS the envelope, which is how `_import_failed`
  answers a startup that died before there was a dispatcher. Never re-narrow the
  walk, and never add an allowlist: five checks guard the machinery itself, two
  of them because a lint that reads an empty graph passes forever and says
  nothing while it does.

- **A missing operator file is a state to REPORT, not an exception to leak.**
  `seasonal.json`, `rule_defs/`, `kdp_books.json` and the Snap exports are all
  gitignored operator data, and the app ships standalone, so its data folder may
  never have held any of them. `load_config` fell through to reading a file that
  was not there and `FileNotFoundError` travelled out of the bridge intact: the
  Seasonal screen showed an absolute filesystem path where it should have said
  nothing is tagged yet. Fail closed with a sentence, or return the empty shape
  every reader already handles — never let a traceback reach the envelope. The
  same test above asserts no reply carries `Errno` or `Traceback`.

- **The Swift tests are gated by `package_app.sh`, not by CI.** The app target
  and the test target compile separately, so the app can build, sign, install
  and run perfectly while the test target does not compile at all. CI runs the
  Python suite alone — deliberately: the app targets macOS 26 with Swift 6 and
  GitHub's runners lag that — so on 2026-08-21 a new field on `HealthResponse`
  broke six fixtures and 162 Swift tests silently stopped running. Packaging is
  the one step the standing rule guarantees on every surviving Swift change, so
  `bash scripts/package_app.sh --install` now runs `xcodebuild test` FIRST and
  refuses to build if it fails. `SKIP_SWIFT_TESTS=1` bypasses it for an
  engine-only emergency rebuild. Adding a field to a shared response type is a
  Swift change: run the packaging step, don't assume a clean app build means
  anything about the tests.

- **Automatic runs are capped by COUNT, and past the cap they apply nothing
  (added 2026-08-22).** Every other guard here judges one change: the KILL file,
  the econ gate, the snapshot gate, the conflict guard, the bid ceiling, the
  no-op check. None of them counts, so a rule whose condition is one character
  too loose — `>= 1` where `>= 15` was meant — matches tens of thousands of rows
  and all of them wave it through. Six rules run on AUTO nightly across seven
  markets with nobody looking. `db.AUTO_CHANGE_CAP_DEFAULT` is 500, per market,
  per run, editable with `appctl change-cap --set N` (0 = off). It REFUSES the
  whole run rather than truncating: the old code applied `changes[:cap]` and set
  `truncated: true`, which is half an account acted on plus a flag that reached
  no screen. 500 is measured — the busiest rule-eligible day in any market's
  `writes_log` is US 2026-06-29 at 255, the EU markets peak at 26, and a normal
  night is 4 to 49 writes. `rules-approve` is exempt because those ids were
  picked by hand; `tests/rules_volume_cap_tests.py` fails if any other caller
  passes an explicit cap, and if the cap ever fails OPEN on a corrupt value or a
  database with no `engine_meta`.
- **That 500 was measured over CHANGES, and applying it to BUILDS broke a
  legitimate build on night one (corrected 2026-08-24).** The cap moved down
  into `ads_client._budget` so it would cover the eight scripts that run
  `--apply --auto` nightly, not just the DSL. Two of those eight are the
  campaign builders, and they do not compare a metric to a threshold — they
  enumerate the catalogue and populate campaigns. Measured across every
  market's `writes_log`, an ordinary build night is 1,500 to 3,900 entities and
  the busiest day ever recorded is 27,319, against a change-surface peak of
  518. On 2026-08-24 the cap stopped US `scavenger_build` at 475 of about 700
  product ads; the nightly recorded `scavenger_build (exit 1)`, the account was
  left half-built, and the stop's own message says the collected batch results
  are lost so `writes_log` may be incomplete. So there are TWO budgets now.
  A write counts against `db.AUTO_BUILD_CAP_DEFAULT` (50,000, `appctl
  change-cap --set-build N`) only when BOTH halves agree: the endpoint is in
  `ads_client._BUILD_ENDPOINTS` — creating ad groups, product ads or keywords,
  plus `PUT /sp/targets`, which is `lottery_build.set_clause_bids` configuring
  the four clauses Amazon auto-generates under each NEW ad group, four writes
  per ad group, so 125 new ASINs is 500 on its own — and the process called
  `client.declare_campaign_builder()`. The endpoint alone is NOT enough:
  `phase4_harvest_create` and `phase4b_harvest_asins` create the same three
  things and they ARE threshold-driven, so a split by endpoint would have
  quietly handed the harvest promoters a 50,000-write budget — the opposite of
  what the guard was for. Everything else, INCLUDING creating a campaign, keeps
  the 500: a campaign is the unit of daily spend at $5/day and no legitimate
  night creates more than about fifty. 50,000 is read off the builders' own
  structural caps — `scavenger_build` cannot exceed 6 series x
  `scavenger.MAX_CAMPAIGNS` x (`MAX_ASINS` + `MAX_KEYWORDS`) = 43,272 — so it
  never fires on a build those caps allow, and it still fires long before a
  runaway reaches a catalogue of 725,970 listings. Both halves fail SAFE: an
  endpoint nobody lists and a script that never declares itself get the
  stricter cap and stop loudly. Guarded by
  `tests/auto_write_budget_tests.py`, mutation-checked.
  **The cap counts ids inside a FILTER too.** v3 deletes take
  `{"campaignIdFilter": {"include": [ … ]}}` rather than entity objects, and
  `_entity_count` read only top-level lists, so a batch of a hundred archives
  counted as ONE — a guard a hundred times weaker than it read, on the action
  Amazon cannot undo. And a cap stored as a fraction used to round DOWN into 0,
  which means no cap at all; only a typed 0 may switch a guard off.
- **A build that dies part-way leaves the PREVIOUS run's report on disk.**
  `scavenger_build` wrote `outputs/scav_build_<MARKET>.json` only after the
  whole loop finished, so both of 2026-08-24's scavenger failures — US on the
  write cap, DE when the app bundle was replaced under the running nightly and
  every TLS call died on a missing `certifi/cacert.pem` — left yesterday
  morning's successful report in place. Same keys, same shape, `added` counts
  from a run that completed; only `as_of` said otherwise and nothing reads it.
  The report is now written on the way out of a stopped build too, carrying
  `stopped` with what raised, and `ScavengerCoverage.warning` says so.
- **An alert kind the app has never heard of arrives wearing the app's own
  name.** `AppState.title(for:)` falls back to "Merch Ads" and
  `IssueDerivation.alertRoute` falls back to the Dashboard. Both are the right
  net for an engine that is newer than the app, and both are SILENT, so a kind
  nobody wired up looks exactly like one that was. `aws_plan_expiry` shipped in
  the engine on 2026-08-21 and neither Swift file learned it; the 2026-08-22
  review found it by listing both sides and diffing them by hand.
  `tests/app_alert_contract_tests.py` does that diff now, reading `appctl`'s
  `_*_alerts` builders as the source of truth — so adding a kind to the engine
  and nothing else FAILS. The Swift `AlertRoutingTests` still pins WHERE each
  kind lands, because that is a judgement; this only asks whether the app was
  told at all.
- **The engine reports what it could NOT do, and the app has to say so.** The
  same 2026-08-22 review found four of those fields decoded into nothing:
  `killlist.skipped` (49 US designs excluded before any threshold ran, under a
  screen reading "No design in US is below the CVR floor"), `ytd.partial` (six
  of seven markets are part-year), and
  `stream-today.unresolved_advertisers` (an unresolved advertiser is dropped
  from every total on the panel). Each one made a screen read complete when it
  was not. When adding a field that says what the data does NOT cover, wire the
  Swift side in the same change — a truth field nobody renders is the same as
  not having it, and it is worse than not having it, because the reply looks
  careful.
- **A period's four cards do not all cover the same window (found 2026-08-22).**
  `appctl periods` can extend a window backwards with months imported from the
  Ads console. Spend, sales and ACOS then cover the whole window; PROFIT cannot,
  because royalty is per design and imported months carry no per-design
  economics, so it is modelled over the daily-banked portion alone. The engine
  says exactly that in `profit_note`, and it deliberately leaves `partial` FALSE
  on those rows — correctly, since the window is not partial for the three
  figures that do cover it. `PeriodRow` decoded neither `profit_note` nor
  `months_imported`, so the Dashboard's Year to date row showed a whole year of
  ad spend beside a profit figure covering only its last 143 days, unmarked. The
  All time row pairs five years of spend with that identical profit figure and is
  off-screen only because `hiddenFromDashboard` drops it — a layout choice, not
  a guard. Each card now states its own span and the profit card says so in the
  caution colour. `tests/periods_contract_tests.py` fails when `cmd_periods`
  emits a key no period struct in `Models.swift` names, which is the SECOND time
  this class was found by hand; `MerchAdsTests/PeriodProfitWindowTests` pins what
  each card says.

- **Catalogue price coverage is INVENTORY. The economics gate is IMPACT. Never
  warn on the first (learned the hard way, 2026-08-22).** `econ-gate.catalog`
  counts advertised designs with no list price, and the first version of the
  app's warning fired on that: 19,177 designs, "so bids, pauses and negatives
  skip them entirely". That sentence was false. Only a **US standard tee**
  resolves its break-even from the design's own list price; every other product
  type, and every other market, is priced from the type table and needs no list
  price at all (`products.design_be_for`). 18,001 of those 19,177 were trucker
  and baseball hats, which were never affected. The gate could not judge **182**.
  An alarm wrong by two orders of magnitude gets muted, and then the real one is
  missed too. `appctl econ-gate` now carries `econ_coverage`, which asks the real
  gate per ad group and reports its own skip reasons (~0.5s over 85k); the app
  warns on `actionable` = `unknown_price + unmapped`. `transition` is a
  deliberate 30-day leniency after a price change and is deliberately excluded.
  **Count ad groups and products separately, and say which.** `actionable` is
  AD GROUPS and `actionable_asins` is PRODUCTS; one product can be advertised by
  several ad groups, so the first is always larger — 200 against 177 on
  2026-08-22, shipped once as "200 designs". And report `actionable_spend`
  beside them: a bare count reads as bookkeeping and got waved off as
  background noise in the same session, when the honest figure was 6.9% of US
  trailing-30 spend running with no break-even, including eleven ad groups that
  reach the kill list's own bar and cannot be judged.
  Guarded by `tests/econ_coverage_tests.py` and
  `MerchAdsTests/EngineTruthFieldsTests.testCatalogueCoverageAloneNeverRaisesAnIssue`.
- **A MerchFlow "all products" export carries REMOVED listings, and "published"
  is not the same question as "for sale" (2026-08-22).** `map_products` priced
  only `status == "published"`, so every other state got no list price — and no
  list price means no break-even, which means every economics rule SKIPS the
  design. Not paused, not flagged, not counted: exempt. That is the one outcome
  worse than pricing it wrong. The operator confirmed a timed-out listing stays
  for sale, and the export's own 30-day sales agree: `timed_out` 569 listings /
  20 units, `locked` 114 listings / **348 units** — harder per listing than
  anything else in the catalogue — `propagated` 147 / 1, against 317k `deleted_*`
  listings selling 6 units between them and `publishing` + `review` selling none.
  `products.PURCHASABLE_STATUSES` is the one place that knows this.
  **The bar for PRICING a design you already advertise is not the bar for
  CHOOSING a new one.** `lottery_build`, `scavenger_build` and `import-preview`
  still require `published`, because starting a campaign on a locked design is a
  different decision. `tests/purchasable_status_tests.py` pins both halves and
  fails if either drifts. Widening the pricing filter took US `unknown_price`
  from 182 to 63 and the spend running with no break-even from 66.14 to 18.75.
- **Report what a fresh export can actually FIX, not what has no price.**
  `econ-gate.econ_coverage` splits `actionable_live` from `actionable_removed`,
  because 58 of 72 unpriced products are deleted listings that no future export
  will ever price. The app headlines the live count and withholds the re-map
  command entirely when nothing is fixable — otherwise the "fix" is an errand
  that cannot succeed. map_products records the status of every advertised
  listing it skips, into `catalog_coverage.json` as `not_live`; that map is bulk
  and is stripped out of the reply in `cmd_econ_gate`, which keeps only counts.
- **A US tee price a few cents off the ladder is priced AS the nearest rung
  (2026-08-22).** Every rung of `US_TEE_ROYALTY_CENTS` is a .99 price a dollar
  apart, and anything else used to resolve to NO economics — which exempts the
  design from every bid, pause and negative rule. Measured across the US
  account: 21,267 advertised tees sat exactly on a rung, ONE was off, by a
  single cent ($20.00), and none were outside the ladder's range. So off-ladder
  prices are formatting artifacts, not pricing decisions.
  `products.US_TEE_PRICE_SNAP_CENTS` is **5**, deliberately tiny: rungs are 100
  cents apart so ±5 can never reach two of them, and a genuinely unusual price
  ($20.49) still resolves to nothing and is still skipped. Snapping chooses
  which royalty row to READ; it never changes what the design is said to cost —
  `price_cents` and `true_break_even` stay on the real price, `priced_as_cents`
  and `price_snapped` say what happened. `growth_priced` follows the SNAPPED
  rung, because $19.95 is a $19.99 tee with a rounding artifact, not a
  deliberate rank-push below the growth floor.
- **Only count ad groups that could still ACT.** A warning naming something the
  operator cannot change gets ignored, and then the real one is ignored too.
  `_econ_coverage` excludes ARCHIVED ad groups (terminal — Amazon has no
  un-archive) and STALE rows (`ad_group_product` keeps its row after Amazon's
  live product-ad list stops returning the ad group, so nothing refreshes it and
  the blank price is the row's age, not a hole in the catalogue). Both are
  COUNTED and reported as `excluded_archived` / `excluded_stale_rows`, never
  silent. The same rule governs advice: `actionable_removed_enabled` exists so
  the app does not tell the operator to pause an ad that is already paused.
  Together these took one warning from 14 products to the 2 that could actually
  spend money. Guarded by `tests/econ_coverage_tests.py`, which builds a
  synthetic database rather than observing the live one — `_econ_coverage`
  takes an optional `conn` for exactly that reason.
- **`stream_data.sqlite` can corrupt, and the WAL is part of the database
  (2026-08-22).** It corrupted between two hourly drains — the 03:57 drain
  banked 640 messages and reported success, and by 04:23 the file was malformed.
  Recovered with `sqlite3 .recover`, ZERO rows lost: 7,502 messages, identical
  counts, nothing orphaned. **Cause unknown.** Ruled out: the Mac app (never
  opens that file), overlapping drains (hourly, 600s ceiling), the 03:57 drain
  itself, Google Drive / iCloud (not syncing that path), Time Machine (atomic
  APFS snapshots), and disk space (75 GB free).
  `stream_store._integrity()` runs `PRAGMA quick_check` in `health()` and
  `_stream_corrupt_alerts` raises `stream_db_corrupt` from the DEFAULT market
  only — one file serves every realm, and the incident reached the operator as
  seven copies of "the undercount check could not run", none of which named the
  fault. System Health draws it in red above every other line, because a stale
  drain, a healthy message count and a fresh timestamp all read GREEN while the
  file underneath is unreadable.
  **`quick_check`, not `integrity_check`**: measured on that exact file, 1ms
  against 9ms, and it catches this class.
  **A copy taken without its `-wal` reads as perfectly healthy.** The corruption
  lived in the WAL, so `backups/stream_data.sqlite.corrupt-…` on its own
  answered a confident `ok` — which nearly produced the conclusion that
  `quick_check` could not detect this at all. To check a backup, put the
  sidecar beside it under the matching name. Guarded by
  `tests/stream_integrity_tests.py`, which corrupts a real database by
  overwriting a page rather than mocking the pragma.
- **The catalogue cache is a PURE OPTIMISATION, and it stays one (2026-08-22).**
  The product grid is several CSV chunks merged at read time, and the nightly
  performed that merge about twenty times — measured at 20s over 2,007,127
  listings, so roughly seven minutes a night re-parsing 1.1 GB that had not
  changed. `engine/catalog_cache.py` banks it into `catalog_cache.sqlite` in the
  DATA folder. A US read went from 19.2s to 1.75s.
  **The safety is structural, not careful coding.** The table carries a signature
  over the export files: name, mtime AND SIZE. `read()` returns None whenever
  that does not match the folder, and `catalog_rows` falls through to the CSVs. So a cache
  that is stale, missing or corrupt costs SECONDS and can never cost an answer.
  Never add a code path that trusts the table without that check, and never let a
  read build it lazily — a read must not block for half a minute.
  **Size is in the signature, and mtime alone is not enough.** Both mtime and the
  timestamp inside an export's filename have one-second resolution, so a chunk
  being COPIED into the folder while the build reads it banks a TRUNCATED
  catalogue — and if the copy finishes inside that same second, the mtime never
  moves and the signature still matches. That is the one failure the fallback
  cannot rescue, because nothing would ask for it. `catalog_cache.signature()` is
  deliberately its own function: `export_reader.catalog_signature` stays name +
  mtime because `products.export_signature()` banks it in `engine_meta` for the
  economics gate, and changing that format would mismatch every market at once.
  **A folder with no export is never banked.** Zero listings buys no speed, and a
  cache reading `matches: true` with `rows: 0` states that the account has no
  products. It would also replace a good cache because the exports were moved
  aside for a moment. `build()` returns 0 and leaves the table alone.
  An explicit `files=` list ALWAYS reads those files: it is a scoped request, and
  answering it from a cache of the whole catalogue hands back listings the caller
  deliberately excluded. This is the same failure as the Drinkware cohort, one
  layer down: the danger of a cache here is not that it is wrong, it is that it
  is CONFIDENTLY YESTERDAY, and every screen above it reads healthy.
  Built by `run_scheduled.sh` before the market loop and by `adopt-export`.
  Verified once against the live catalogue: 725,970 US rows, zero field
  differences, identical order. Guarded by `tests/catalog_cache_tests.py`.
  **`ORDER BY rowid` in `_stream` is load-bearing and looks redundant.** Removing
  it breaks nothing TODAY, because the read selects all twenty columns so no
  index covers it and SQLite scans in rowid order anyway. That is an accident of
  the column list. Given a query the UNIQUE (marketplace, asin) index can cover,
  SQLite returns ASIN order instead — measured: insert ZZZ, AAA, MMM and it hands
  back AAA, MMM, ZZZ. Callers take the FIRST row they see for an ASIN, so a
  silent re-sort changes which chunk's price wins. The test fixture is
  deliberately shuffled so the day that changes, it fails.
  **A field the cache does not store reads back as None, not as an error.** That
  is how a price stops reaching an economics rule with nothing raised.
  `catalog_cache.FIELDS` is checked against the callers by reading their SYNTAX
  TREE — a text search over those files also reports the Amazon API payloads and
  sales-report rows they walk, and the only way to quieten that is an allowlist
  that grows until it excuses the real thing.
  **A field named by a VARIABLE fails that lint, because it cannot be judged.**
  The first version simply skipped `p.get(field)`, which is the one shape it
  should have refused: a read nobody checks is worse than one nobody wrote,
  because the file then looks covered. `traz.load_asin_royalty` took the field
  as an argument, so changing its default to `royaltyLast12Months` — a real
  MerchFlow column the cache does not bank — passed the whole suite, while a
  literal `p.get("bsr")` two lines away failed it. Nothing ever called it with
  anything but the default, so nothing was wrong; the hole was in the guard, at
  exactly the spot the docs called closed. Write catalogue field names out as
  literals. If a caller must choose one, the choice belongs in
  `catalog_cache.FIELDS`, where the lint can see it (found 2026-08-22).
  **A cached row carries 20 keys where a MerchFlow row carries 134.** The cache
  normalises every row to the shape `export_reader._snap_row()` produces, so the
  115 MerchFlow-only columns — `bsr`, `designSalesLast30`, the bullets — are NOT
  banked. No caller reads one today, proved by hashing every consumer's output
  with the cache on and forced off. A future caller that did would get a value
  when the cache is cold and None when it is warm, which is the hardest kind of
  bug to be handed. That is what the syntax-tree lint is for, and it was proved
  by planting `p.get("bsr")` in `traz.py` and watching the test name it.
  **Nothing underscored may join `FIELDS` except `_source` and `_as_of`.**
  `_STORED` drops underscored fields and `_stream` rebuilds exactly those two by
  hand, so a third would be absent from a cached row entirely — no column, no
  error, not even a None. The module refuses to import instead.

- **`_source` and `_as_of` are four filenames repeated two million times.** Stored
  inline they cost 103 MB, more than any real per-listing field. They live in
  `catalog_file` and each row keeps an integer. Same rows out; the expansion is
  one dict lookup in Python, never a two-million-row join.

- **A side export SUPPLEMENTS the catalogue. It never REPLACES it (2026-08-22).**
  The US Drinkware cohort read its ASINs from a dedicated ad-safe file instead of
  the main export. `scavenger_build.find_source("tumbler")` picked the only
  matching file in the POD folder. That file was dated 24 June. It held none of
  the August designs, so once the intake scope filtered it, the whole Drinkware
  series left the plan. The builder ran four cohorts where five were asked for.
  Nothing raised and nothing warned. 723 new drinkware designs — 2,852 ad ASINs —
  got no ads, and the Import screen still read "Complete · Drinkware 723",
  because the screen was printing the REQUEST. The five EU markets have no such
  side file, so all five built correctly the same afternoon. Both sources are
  merged and sorted together now. A stale file can cost coverage it can no longer
  supply. It must never cost coverage the fresh export has.
- **A build now says what it could NOT do, and the screen has to show it.**
  `scavenger_build` writes `outputs/scav_build_<MARKET>.json` on every run,
  preview included. `appctl import-apply` deletes any older copy FIRST and reads
  it back into `coverage` — a report that is missing is reported as unverified,
  never as clean. It names four silent failures: a series that matched nothing,
  the tail `shard()` dropped past `MAX_ASINS x MAX_CAMPAIGNS`, any campaign
  that took new ads while PAUSED, and product ads Amazon REFUSED. The PAUSED one
  is not hypothetical: the same run
  put 446 new US hat ads into `SCAVENGER - Hats 1`, paused since June. Amazon
  accepted every one of them, so every count in the reply looked healthy, and not
  one of those ads can serve. `build_one` reuses a campaign by NAME whatever its
  state, and that stays true — the fix is that the operator is told. Guarded by
  `tests/scavenger_coverage_tests.py` and
  `MerchAdsTests/EngineTruthFieldsTests`.
- **A refused product ad is not a no-op, and `added: 0` cannot tell them apart
  (found 2026-08-25).** Both builders read only the `success` block of a create
  response and dropped the HTTP status and the error block on the floor.
  `chunked_create`'s caller then printed `product ads added: 0/40` beside a
  comment asserting the benign reading — "Amazon accepts nothing when every ASIN
  is already advertised here" — and that was wrong every night it was not zero.
  About 873 ASINs a night were submitted and turned down across six markets,
  unchanged since 2026-06-25, and the loop can never close on its own:
  `new_asins` is the difference against Amazon's live product-ad list, a refused
  ad never joins that list, so the same ASINs go back forever. The confirmed
  half is the retail-ASIN fallback for hardgoods — 474 US listings advertised
  nowhere. A third of it is apparel and is still UNEXPLAINED.
  Both builders now print the refused count, the HTTP status and the first
  reasons out of the error block to STDERR (`ads_client.report_refused`), which
  run_scheduled.sh captures into `outputs/scheduled_runs.log` — grep `REFUSED`.
  The count reaches the Import screen through the coverage report's `refused`.
  Nothing about WHICH ASINs get submitted was changed: the reasons come first,
  because a third of the residue has no explanation yet and guessing would be
  the third fix aimed at the wrong thing. `docs/rejected-product-ads-2026-08-25.md`
  holds the diagnosis and what to grep after a run.
- **Never edit engine `.py` files while `run_scheduled.sh` is running.** The loop starts a fresh `python3` per script per market and runs `phase2_apply.py --apply --auto` / `phase3_bids.py --apply --auto` against LIVE accounts. Wait for the run to finish (check `ps ax | grep run_scheduled`), then apply.
- Multi-market operations loop all six markets via `ADS_MARKET` (`for M in US UK DE FR ES IT`); never assume US-only.
- **Daily Discord digests are OFF (turned off by the operator 2026-08-04).** The `NO_DISCORD` marker file in this folder makes `run_scheduled.sh` skip every `notify_discord.py` call. Delete the file to turn the alerts back on. The macOS notification at the end of the run is unaffected.
- **STANDING RULE (codified 2026-07-22): commit always, and relaunch the app on every commit.** Any change that survives — engine (`appctl.py`/Python) or Swift — is committed autonomously the same turn without asking (on a branch, never straight to `main`; use `git commit -F -` for messages with quotes/parens/apostrophes). After committing, always `bash scripts/package_app.sh --install` and then relaunch: `pkill -x "Merch Ads"; open "/Applications/Merch Ads.app"`.
  **This includes ENGINE-only changes (corrected 2026-08-22).** The rule used to
  say a plain relaunch was enough for Python, because the app shelled out to the
  checkout. That stopped being true on 2026-08-21, when the app became
  standalone: it carries its own copy of the modules at
  `Contents/Resources/engine` and runs THOSE. An engine fix does not reach the
  running app until the bundle is rebuilt. Nothing said so — the tests passed,
  the freshness hook only hashed Swift, and this rule promised a relaunch was
  enough, so a fix could sit in the repo, green, while the app went on running
  the old code. `.claude/hooks/app_src_hash.sh` now hashes everything the bundle
  ships (`MerchAds/`, `MerchAds.xcodeproj/project.pbxproj` and the shared
  schemes, `engine/**.py`, `run_scheduled.sh`, `run_stream_drain.sh`,
  `requirements.txt`), so the Stop hook blocks on an unpackaged engine change
  instead of anyone having to remember. Proved by editing one engine file: the
  old hash did not move, the new one did.
  **It sorts under `LC_ALL=C`, and the two build inputs joined the set on
  2026-08-24.** `sort` collates by the caller's locale, so the same untouched
  tree hashed differently under C and under en_US.UTF-8 — and the stamp carries
  whichever locale the install ran under, so a byte-identical fresh install
  could read STALE for good. `project.pbxproj` decides which sources compile
  and with which settings; `requirements.txt` is installed into the bundled
  interpreter. Changing either alone left the digest identical, and the hook
  then called an out-of-date `/Applications` copy fresh. Guarded by
  `tests/packaging_nightly_guard_tests.py`.

## Build & run the app
- **The app is standalone since 2026-08-21. Code ships inside the bundle; data stays outside.**
  `Contents/Resources/engine` holds the Python modules, `Contents/Resources/python`
  holds a relocatable CPython 3.12 with `requests`, and
  `Contents/Resources/run_scheduled.sh` holds the nightly. So no system python3,
  no `pip install`, and no repo checkout is needed to run it — proved with
  `env -i`, an empty environment, reading real market data.
  **What stays outside is DATA**: `ads_data*.sqlite`, `.env`, `outputs/`, `KILL`,
  `seasonal.json`, `rule_defs/` — at the folder in Settings (default
  `~/Biznis/ClaudeCode/POD/Ads`). Deleting or replacing the app cannot touch a row.
  The bridge passes that folder as **`MERCHADS_DATA_DIR`** (and its parent as
  `MERCHADS_POD_DIR`, where the Snap exports and SALES_REPORT files sit).
  `engine/paths.py` is the ONE place that reads them, and it **fails closed**: a
  variable that names a folder which is not there stops the process. Unset, both
  fall back to the old `__file__` derivation, so a checkout, the test suite and
  the nightly are byte-identical to before. Never re-derive a data path from
  `__file__` in a new module — inside the bundle that resolves to
  `Contents/Resources`, which is real, readable and holds no databases, and the
  reply is a cheerful `{"ok": true, "empty": true}` for every market.
  To move the nightly onto the app too: `bash scripts/install_launchd.sh --app`.
- Fast iteration/verification: `xcodebuild -project MerchAds.xcodeproj -scheme MerchAds -configuration Debug -derivedDataPath /tmp/merchads-derived build`, then launch the exact built binary from that DerivedData path (same stale-copy gotcha class as other local apps: `open` by bundle id may grab an old build). **A DerivedData build ships no engine and no Python**, so it falls back to the Settings folder and the login-shell python3 — that is deliberate, and it is why the fallbacks must keep working.
- **STANDING RULE (codified 2026-07-19): the `/Applications` copy must always be the latest.** Any time you update or fix the app — every fix, feature, or change that survives — the turn is NOT done until you have run `bash scripts/package_app.sh --install` (builds Release + installs to `/Applications/Merch Ads.app`) and **relaunched from `/Applications`** (`pkill -x "Merch Ads"` then `open "/Applications/Merch Ads.app"`). Never leave `/Applications` stale, and never leave the temp `/tmp/merchads-derived` build as the running instance at end of turn. A Stop hook (`.claude/hooks/check_app_fresh.sh`) blocks turn-end if source changed since the last install — that's the backstop, not the primary; do it without being reminded.
- UI work: load `macos-design-guidelines` + `swiftui-pro` skills; judge on the REAL running app via screenshot and self-critique against HIG before presenting (the dashboard has been rejected once for looking "ugly and not really logical" — spatial logic and hierarchy matter here as much as polish).
- Golden rules from the handoff bear repeating: never read or print `.env`; Swift never writes the DBs or calls Amazon — everything mutating goes through `appctl.py`'s safety rails.
