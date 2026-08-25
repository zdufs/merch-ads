# Changelog

Notable changes to this project. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [0.4.17] — 2026-08-25 — the refusals had a cause, and the window never had one

The last release made Amazon's refusals speak. This one reads what they said.
Every refused product ad in the instrumented run came back with the same
eligibility error, and that turned out to be two different stories wearing one
error code.

### Fixed — hardgoods submitted every night that could never be advertised

A hardgood cannot be advertised through its retail listing. It needs the ad-safe
identifier the product export carries beside it. The cohort builder has said so
in a comment since it was written, and the code did the opposite: with no ad-safe
identifier it fell back to the retail one and submitted that.

Amazon refused every one of them. A refused ad never joins the live product-ad
list, and that list is what the builder computes "new" against, so the same
designs went back the next night, and the night after that, for about two months.

The run log settles it to the unit. Each batch the builder sent was refused
exactly as many times as it held designs with no ad-safe identifier. One batch
read its identifiers from a dedicated ad-safe file, and that one was refused
nothing at all.

The builder skips those designs now, and the skip is COUNTED — per cohort series
and as a total, because a series skipped down to nothing never reaches the series
list at all. That count reaches the Import screen, which already draws a series in
amber when something was missed. Skipping in silence would have moved the problem
one layer on rather than fixing it: these designs are coverage the account has
genuinely lost, and only a fresh export with the ad-safe column filled in brings
them back.

Only designs a cohort would actually have advertised are counted. The catalogue
holds a great many unsold products with no ad-safe identifier, and not one of them
is lost coverage. That is the lesson the economics gate taught: an alarm wrong by
two orders of magnitude gets muted, and then the real one is missed too.

One constant in the engine knows which product types need an ad-safe identifier.
It is a type question and not a series one, so a cohort that grows a new hardgood
has to declare it where the reason is written down.

The apparel half of the same refusal is NOT fixed. It carries the same error code
and still has no explanation, and nothing about what apparel submits was touched.

### Fixed — the right reason attached to no design

Amazon names a refused entry by its POSITION in the batch, not by its identifier.
Every reason in the run carried an index and none carried the field the old code
read, so every line printed as position zero — the correct reason, attached to
nothing.

The refusal log now takes the batch it submitted and resolves the index back to
the design. That is what makes the apparel half answerable from a run log at all,
and it changes nothing about what gets submitted. The old test fixture asserted
the shape the diagnosis had assumed, which is exactly why nothing caught this
before real payloads arrived.

### Closed — the trailing-30 window is thirty days in every market

The previous release wrote this down as an open question rather than guessing at
it: a snapshot one day stale appeared to reconcile over thirty-one days of banked
rows where a current one reconciled over thirty. It is closed now, and no code
change follows, because the worry was unfounded.

An instrumented run logged what each market asked Amazon for and what Amazon
echoed back. Every request asks for thirty days. Every reply carries the same
start, the same end and the same day count as its own request. Both of the fixes
that note held in reserve depended on one of those being false. Neither is.

The extra day was a hole in the measuring, not in the window being measured. The
test summed banked daily rows over the days ending at the snapshot, which only
works when every one of those days is banked. A market whose daily banking is a
day behind its snapshot is missing the last day of the window, so a thirty-day sum
holds twenty-nine days of data and reads low. Reaching back one more day adds a day
at the front to replace the one absent at the back. It looks like a wider window.
It is the same window with one day moved.

Recomputed from the instrumented run, the correlation is total. Every market with
complete daily banking reconciles at exactly thirty days. Every market missing a
day does not. It also runs the opposite way to the snapshot-age theory that made
the original reading look right.

So every threshold in the engine is applied to a genuine thirty-day window,
everywhere. The note is kept rather than deleted, because the wrong answer was
convincing and someone will measure this again. It carries the lesson it cost:
check that a window's days are all present before reconciling a snapshot against
them. Without that, the comparison quietly answers a question about banking
coverage and reads as an answer about window width.

### Housekeeping

Ten new tests cover the skip, the counting and the index-to-design mapping. Every
one of them fails against the code before it. The Swift side is wired in the same
change, because a truth field nobody renders is worse than not having it.

1391 Python tests and 302 Swift tests pass.

---

## [0.4.16] — 2026-08-25 — the app nobody had read, and the guards that did not guard

The last release read the engine. This one starts where that stopped: the
SwiftUI layer on top of it, the seam between the two, the shell scripts that run
unattended at night — and then a second reader over the fixes themselves.

Four passes over the app found 57 defects. A fifth tried to refute every one and
confirmed 51. A later sweep ran against the RUNNING system instead of the source:
one auditor walked every screen of the installed app, one fed the engine empty,
corrupt and missing data folders, one re-read the fix merges. That found 49 more.
45 are fixed here; 4 were deliberate behaviour and are recorded rather than
changed.

Then every guard in this repository was tested by breaking it deliberately.
Seventy-six mutations: forty-seven were caught and twenty-nine were not.

### Fixed — tonight's nightly would have crashed

The previous release renamed a variable in phase 2's pause path, phase 2's
rollback and phase 3's bids, and left the old name in each closing print. Python
evaluates the condition of a conditional expression before it picks a branch, so
all three raised every time — after the writes had gone to Amazon, after the
audit log, after the local mirror. The run would have reported the step failed
with the account already changed.

Nothing in the test suite could see it, because a name that is read and never
bound is a runtime error, not a syntax one. A lint for exactly that shape is new
here, and finding these three is what it was written for.

### Fixed — writes Amazon refused, reported as writes that worked

Most of a batch of campaign archives came back rejected with a 400 and the app
told the operator each one had worked. The engine reports a rejection INSIDE a
successful envelope — the call was fine, the write was not — and the app decoded
a struct that read none of it.

That shape turned up ten more times: a promotion that Amazon refused counted
itself a success, and nine other paths recorded a write that never happened. A
local mirror that disagrees with the account is worse than one a day behind,
because the next preview proposes from it.

### Fixed — guards that counted the wrong thing, or nothing at all

The volume cap is the only guard here that asks HOW MANY rather than whether one
change is safe. It read a key the act-everywhere plan does not return, so a
fan-out over hundreds of instances counted as a single change. Delete batches
counted as one apiece as well.

The bid ceiling failed OPEN again, in a second place: a ceiling that could not be
READ returned the same empty value that means "deliberately uncapped", and the
comment above it described that exact collapse as already fixed.

Six of seven markets ran their automatic rules with no economics gate at all.
Five modules ignored the catalogue folder the app passes them, so the economics
gate could certify a catalogue nobody had mapped. Five markets asked Amazon for
English keywords.

And the cap that guards the rules broke the builders, which is the opposite
mistake: a builder writes thousands of items on a normal night by design. They
carry their own cap now, read off the structural ceiling each builder already
has.

### Fixed — tests that decided their own outcome

Three fixes from the last release were pinned only by tests that read the SOURCE
FILE, and all three kept passing while the behaviour was broken. One asserted
that a string appears somewhere in the file; it still did, in an unrelated query
a hundred lines away.

The snapshot lint compared two sets per module, so a module that read one perf
table's date honestly was exempt for that table everywhere else in the same file.
Sixteen modules sat in that blind spot. The rule is asked per QUERY now.

The Swift suite had never once decoded a fixture through the app's own decoder:
every test built a private one, so all of them passed with the bridge's key
conversion switched off. Breaking it now fails fifty-one tests.

Fourteen constants decide what gets paused, negated and bid, and not one of them
had a test. Which of them have a runtime backstop is written down now, because it
differs.

### Fixed — the app invalidated its own code signature

Installing copied the bundle without preserving timestamps, so every engine
module looked newer than the bytecode shipped beside it, and CPython rewrote that
bytecode inside the sealed bundle. From the first command the app ran, the
signature no longer verified. The bridge and both shipped shell scripts now tell
the interpreter never to write bytecode, and the installer preserves timestamps
so the shipped copy stays valid.

### Fixed — packaging killed the nightly, and nothing said so

The nightly runs FROM the bundle, so installing during a run kills whatever step
is in flight. It happened: three steps of one market died on the signal, the run
carried on to the next market, and the only trace was three failed steps in a
log nobody reads. That market's Dashboard then showed sales about 15% low for
days, because its banked daily rows kept the attribution each night saw while the
snapshot beside them matured normally.

Packaging refuses to install while the nightly is running now, and says how to
wait and how to force. A plain build is never blocked. The guard had a bug of its
own — under `pipefail` it exited silently when the run finished mid-check — and a
guard that refuses without saying why is worse than none.

### Fixed — screens that read complete when they were not

Fifteen of them. An account-wide ACOS that added dollars, pounds and euros
together. Six part-years labelled as a full year to date. Four screens that
printed the page they had loaded as if it were the whole account, where the
Targets tab now says how many rows it is not showing. A plan previewed in one
market and applied in whichever market the picker had moved to. The live Today
panel can say its own number is an undercount when the drain is behind.

Reads got quieter in the right way too. A read no longer CREATES the database it
was asked about. Four of them answered with an error number and a filesystem path
where a sentence belonged. A rule with no data to read reported a healthy
zero-match run rather than saying it could judge nothing.

### Added — Amazon's refusals get names

A few hundred product ads have been refused every night since June and the code
threw the reasons away. Every refused create batch now prints one line naming the
design, the error type and the status Amazon returned. The build's coverage
report counts refusals per series, and the Import screen draws them in amber
beside the gaps it already named. What gets submitted is deliberately unchanged.

### Not a bug — the console and the engine count different campaigns

The gap between Amazon's console panel and this engine on US purchases was the
method, not a defect. The panel filters to enabled campaigns; the engine sums
every campaign that spent. Archived campaigns held the difference, and counting
enabled-only reproduces the console's number to the unit. Spend matches Amazon's
own report to the cent in five markets and to a rounding error in the sixth. The
attribution theory from the day before is refuted in writing.

One open question is written down rather than guessed at: a snapshot one day
stale reconciles over thirty-one days of banked rows where a current one
reconciles over thirty. No screen is wrong today; the name of the window may be.

### Housekeeping

The release checks learned about this release's own tree, and the test fixtures
stopped carrying the account's own figures — a week of write counts, a keyword
the account bids on, and three real entity ids, none of which any test asserted
on. Two dated internal analyses stay private, because in both the figures ARE the
argument and a scrubbed copy would reason from numbers it no longer shows.

1376 Python tests and 301 Swift tests pass.

---

## [0.4.15] — 2026-08-24 — the code nobody had read

Every audit before this one was scoped to a diff. That is the right way to
review a change, and it means the code that did NOT change was never read: about
twenty thousand lines across seventy files, including a dozen that write to the
live account every night with nobody watching.

Three passes of an outside reader over exactly that surface. Seventy-four
findings; the third pass tried to refute them all and confirmed sixty-two.
Every one was reproduced against real data before it was touched, and four were
checked and deliberately left alone.

### Fixed — a keyword rule was acting on every automatic target

The rules language offers `keyword` and `target` as two entity kinds. The loader
took the kind and used it only as a LABEL on the row it built; it never reached
the query. So the two returned the identical set — 51,631 rows in US, of which
49,901 are automatic targeting expressions and 1,711 are keywords.

A rule written for keywords was therefore reaching about thirty times what it
named, and nothing failed, because the executor routes each write to the right
Amazon endpoint on its own copy of the same distinction. It also defeated the
cross-rule conflict guard, which keys on (kind, id): one clause proposed by a
keyword rule and by a target rule looked like two different entities, so both
wrote and whichever ran last won without a word.

The split is Amazon's own: BROAD, EXACT and PHRASE are keywords, everything else
is a product or automatic target. A clause with no match type counts as a
target, so a keyword rule can never reach one we cannot identify.

No live automatic rule changes what it does. Every rule in every market was
previewed before and after and the matched counts are identical — the narrowing
removed only rows that matched nothing.

Thirty-three tests failed on this change and all thirty-three were asserting the
bug: their fixtures hold EXACT rows and loaded them as targets.

### Fixed — phase 2 was proposing pauses that could not do anything

Its candidate query filtered campaign state and never ad-group state. On the day
this was found that was all 23 of its 23 proposals across five markets: every
one pointed at an ad group that was already paused.

Worse, each was logged with a previous state of ENABLED, hardcoded. That is the
value Undo restores, so undoing one would have ENABLED an ad group the operator
had paused on purpose.

### Fixed — "nobody knows" was counting as "Amazon accepted it"

The write endpoints answer 207 MULTI-STATUS, which exists precisely because some
items in a batch may have failed. An unreadable body reports no item information
— correctly — but the success test read `not failed_items`, and `not None` is
True. So a 207 nobody could parse passed every test for total success.

The id-level check had the mirror image of the same hole: it returns the ids
Amazon NAMED as rejected, so a 500 and an unreadable 207 both produce an empty
set, and every caller wrote "everything not named is accepted".

That reached three places at once. The audit log recorded `submitted` for writes
that never happened, the local ad-group and target mirrors moved to a state
Amazon might not hold, and Undo then offered to restore a previous state that
was never true. Local state that disagrees with the account is worse than local
state that is a day behind, because the next preview proposes from it.

### Fixed — guards that failed open

A snapshot dated in the FUTURE gave a negative age, and the freshness gate only
rejected an age above the limit. One corrupt row became the newest and the table
read fresh forever.

Four jobs made destructive decisions from evidence they never checked:
harvest_prune, harvest, the campaign half of scavenger_optimize and phase 3's
campaign list. scavenger_optimize even carried a comment promising both halves
failed closed. Only one did, and the other retires campaigns by pausing them,
unattended, every night.

Any error reading a market's bid ceiling was cached as "no ceiling" for the rest
of the process, so one transient failure let every bid and budget through
unclamped. And campaign and ad-group CREATION never applied the ceiling at all —
it bit only edits, so a builder could open a campaign at any budget it liked.

### Added — the volume cap now guards the eight scripts it did not

The 500-change cap is the only guard in this engine that asks how MANY changes
there are rather than whether one is safe. It guarded the rules engine alone,
while eight other scripts ran unattended every night counting nothing.

There is now a backstop at the single funnel every write already passes through,
so a script added later is covered without anyone remembering to cover it. It
counts only mutating requests, applies to automatic runs only — an operator's
bulk repair is deliberate and supervised — and past the cap the run stops and
exits non-zero.

### Fixed — things that failed and reported success

A rule that errored on EVERY row returned ok:true with matched:0, which reads as
"nothing qualified" rather than "nothing could be judged". The nightly summary
counted only applied results and dropped every refusal, so forty changes all
blocked for stale evidence came out as matched:40 applied:0 with no reason
anywhere. Amazon marking a report FAILED was dropped and counted nowhere, so the
pull and the daily metrics printed Done and exited zero over a stale table or a
missing day. The catch-up discarded its children's exit codes and decided it had
finished from pending reports alone — a pull that died on startup asked for
nothing, so its market had nothing pending, so the script reported everything
was collected.

### Fixed — economics that were confident about numbers they had invented

An unresolved product type fell through to a default whose 18% belongs to no
real product. A cohort ad group outside US was handed a real break-even and
judged as one design. The derivation stored a break-even of 0.18 for any type it
could not price. The economics gate never checked whether the nightly derivation
had run at all. Cross-sell protection valued a design at a display fallback, and
that map SPARES ad groups from being paused, so an invented royalty kept a
loss-making design running.

The trailing-30 window was 31 days: the start was today minus 31 with the end at
yesterday, which spans both ends inclusive. Every spend floor, click minimum and
ACOS test is applied to that window under the name "trailing 30".

### Fixed — TRAZ was reading a column the exporter does not write

Snap for MOD exports no trailing-30 royalty. TRAZ read it anyway, so every
Snap-sourced design showed a royalty of zero and a TRAZ of minus its entire ad
spend. It reads the dated sales report now: 170 US designs get their real 30-day
royalty and 161 show a positive result where every one of them was negative.

### Fixed — Marketing Stream could go quiet without saying so

One queue serves a whole realm and the realms are independent, but coverage
grouped on dataset alone and the last-drain time was an account-wide maximum. A
dead EU queue read exactly like a live one. The advertiser sample was not per
advertiser, so a quiet advertiser was never resolved and never reported as
unresolved either. Conversion availability was account-wide, so one market
delivering conversions made every other market on that queue report sales of
zero — which reads as "sold nothing" rather than "cannot see sales yet".

A notification carrying several records was banked whole, and every downstream
query reads a field path that is null for an array. The message counted towards
Stream health while its records contributed nothing to any figure.

### Fixed — builders that reported the plan and never read the answer

The lottery builder discarded the results of three live writes, so its printed
counts were the size of the plan. Both harvest builders turned a failed
inventory listing into an empty inventory and carried on, which meant submitting
duplicates for every ASIN already there. Harvest promotion negated the source
term whether or not the replacement had been created — so a term that was
EARNING stopped serving anywhere, and was marked promoted so it never came back.

### Fixed — the rules machinery around the rules

An inline rolling window inside a current-snapshot rule was never checked for
holes: six of seven days banked summed to six and the write went out as if it
had seven. Two rule names differing only by punctuation stored to the same file,
so saving the second silently overwrote the first and destroyed its backup at
the same time. An approved change outlived the rule that proposed it — queue a
bid, edit the rule, approve, and the old value went out.

### Fixed — a create retried after an ambiguous failure

A 429 means Amazon refused the request, so retrying is safe and is why the retry
loop exists. A 500 may mean the request was processed before the answer came
back, and a create carries no idempotency key, so the retry can open a second
campaign with its own budget. Creates are no longer retried on a 5xx. Updates
still are: setting a bid twice is the same as setting it once.

### Fixed — screens that read complete when they were not

A period marked itself partial only when its history started later than asked
for, so a window missing days in the MIDDLE came out marked exact. The dashboard
and the demand feed had their queries fixed to read each table's own date and
their labels left reading another's. The Organic Halo cache keyed on the newest
sales report while halo reads the union of all of them, so importing an older
report changed the answer and not the key. Halo also treated the first day of
banked ad history as the day a design started advertising — 416 of 874 US
designs were measured against a baseline that may have had ads running through
it, and are now flagged.

### Not changed, deliberately

Four findings were checked and left alone. The intake's skipped-type count comes
from the same call and window as the preview beside it. Error replies carry no
absolute path or traceback, and the one path the app decodes is deliberately
shown. The catalogue price-age figure is reported rather than gating, because
the catalogue is a dozen chunks and gating on chunk age would freeze every
economics write permanently. And the report-day boundary stays on one clock,
because requesting a day that is not finished banks a partial number, which is
worse than a lag the gap-fill already heals.

1041 Python tests and 231 Swift tests pass.

---

## [0.4.14] — 2026-08-23 — an outside reader, and what eight of my own passes had missed

The 0.4.13 audit ran eight passes and each found what the last had missed. This
release is what a DIFFERENT reader found in the result: two independent reviews
by Codex (gpt-5.6-sol), pointed at the same 44-file diff, one with no context
and one told this project's invariants.

Fifteen defects, every one reproduced by hand before it was touched. Eleven were
in code written during that audit — including two fixes that opened a new hole
while closing the one they were aimed at. That is the argument for a reader who
did not write the thing.

### Fixed — a rejected write could be recorded as accepted

`_applied_subset` decides which items of a batch Amazon took. It subtracted the
ids Amazon NAMED as rejected — and Amazon does not always name them. `failed_items`
counts the error entries, `failed_ids` carries only those whose index mapped back
onto an id we sent, and an entry without a usable index leaves the two disagreeing.

The function ignored `failed_items` entirely, so a refused ad group was counted as
applied: the Audit Trail said `submitted` and the local mirror was set to PAUSED
for an ad group Amazon had left ENABLED. It kept spending while the screen said it
had stopped — the exact desync this family of helpers exists to prevent, reached
from the other side. `ads_client.items_ok` had always read `failed_items` and
answered honestly about the same response; two helpers over one reply, disagreeing
about whether a write had happened.

A batch that reports failures it cannot name now counts nothing at all, the same
rule already stated for a transport failure. The new tests drive the REAL response
parser instead of hand-written batch dicts — which is how this survived: every
existing test wrote `failed_ids` in by hand, so no fixture could produce the case.

### Fixed — three keywords the account bids on were live on GitHub

The keyword check compares string literals in the tree against `targeting_perf`.
Two holes in it, both mine:

It matched **lowercase literals only**, so a title-cased niche in a docstring was
invisible — and one was: a real, currently-bid-on term in `engine/harvest_suggest.py`,
published through a release the gate called clean. It was scrubbed from a Swift file
during the 0.4.13 audit and missed in the Python one.

It also pre-filtered the tree with a regex of common apparel and stop words.
Measured against the live account, that regex could not see **46.6%** of the
keywords it was meant to protect.

Two more narrowings turned up while proving the fix by planting leaks. It read
only QUOTED text, so a keyword in a comment or in prose was invisible — five
more, all live. And its word runs were non-overlapping, so a keyword with
ordinary words on both sides of it inside one sentence was never compared —
three more, in test fixtures. Nine live keywords in total.

The check now takes every 3-to-6 word window anywhere in a file, plus 2-word
windows where a run of words begins, case-insensitively. The word regex is gone,
replaced by three STRUCTURAL exclusions read out of the snapshot's own source so
they cannot go stale: Amazon's product wording from `products.py` and
`preempt.py`, and the declared trademark blocklist in `demand_feed.py` — a list
of what the operator refuses to sell, which is the opposite of strategy.

The 2-word limit is the one deliberate line, and it is measured. Broad match
bids on whatever Amazon decides to match, which here includes ordinary English
word pairs; comparing every 2-word window returns five of those and no extra
leak, while requiring three words except at a run start returns zero false
positives and still catches every real niche. Neither frequency in the tree nor
`match_type` separates the two. The whole snapshot costs about a third of a
second.

Six leak classes are now each proved by planting the exact leak and watching the
build refuse: a keyword in a comment, mid-sentence, and in a bullet; a
capitalised login; money with a currency code; and a refresh token.

None of those exclusions is written down in the script, because the script ships
inside the tree it greps and a phrase named in an allowlist would fail the release
on its own source. Four releases have now learned that lesson.

### Fixed — the release gate reported CLEAN when it had not run

The keyword scanner's exit status was discarded with `|| true`, and an empty
result printed the same green line as a clean tree. A missing interpreter, an
unreadable database or any bug inside it therefore passed the security gate
precisely when it had failed. It fails closed now and prints what went wrong.

### Fixed — the gate could not see a refresh token

It scanned for Amazon identifiers of the `amzn1.` family. A LOGIN WITH REFRESH
TOKEN — the one secret here that alone is enough to write to the live account —
begins `Atzr` and a pipe, as `.env.example` has always shown. A real one pasted
into a tracked `.py` or `.md` file passed every check.

Two smaller holes beside it: the operator-name check was case-sensitive, so a
capitalised copy of the same login walked past; and a money figure with a
currency CODE welded to it (`1,234 EUR` in shape) was discarded for having no
money word
nearby, when an attached currency makes it money by construction.

### Fixed — one market with an empty table blanked System Health for all seven

`health` answers for every market in one reply. A perf table that exists and holds
nothing reports a null date, and `tables` was typed as a dictionary of plain
strings, so that reply failed to decode ENTIRELY — every market gone, not just the
one. It is the state of a market on the day it is added, and of any market whose
report job has never once succeeded. The field now carries the null, and "never
filled" is shown as its own sentence rather than folded into "stale".

### Fixed — "act everywhere" explained every skip with the wrong sentence

The app worked out WHY an instance was skipped from whether a `target_id` came
back. The engine never sent one: `_everywhere_slim` stripped `target_id`,
`campaign_id`, `state` and `asin` before the reply left. So every skip was reported
as "the app cannot address this" and the genuine no-ops were counted as zero, on
every preview since the field was added. An ASIN pause acts on ad groups, which
carry no target id by design, so the inference could not have been right even once
the field was sent.

The engine says the reason now — `already_paused`, `unaddressable`, or
`state_unknown` for a row whose state was never mirrored and which therefore
cannot be called a no-op without guessing.

The Swift test covering this hand-wrote `target_id` into its fixture and passed
against a JSON shape production has never produced. A new contract test reads BOTH
sides — the Swift struct and the engine function — and fails on a field that
exists on only one of them. It immediately found a third: `op` was sent and never
decoded.

### Fixed — unknown could satisfy a negated condition

0.4.13 made the rule engine's ratios answer NONE instead of 0.0 for a zero
denominator. That closed the hole on the `<` side and opened one on the other:
`_eq(None, 0)` is false, so `not _eq(...)` made `IF adGroup.cvr != 0` TRUE for
every ad group nobody had ever clicked — in front of `pause()`. `NOT IN` and
`NOT CONTAINS` had the same shape, and `NOT (...)` around any fail-closed False
inverted it into a match one level up.

Comparisons are three-valued now. UNKNOWN travels through AND, OR and NOT the way
it does in SQL and is collapsed to "does not match" when the rule decides. Writing
the `NONE` literal yourself is still a real question with a real answer, so
`IF target.bid != NONE` still means "is a bid set".

Latent, not live: no shipped rule uses any of those operators, and all 56 rule
previews across all seven markets returned identical counts before and after.

### Fixed — the same ratio bug, in the half that was missed

`rules/entities.py` was fixed in 0.4.13 and `appctl.py` was not. So the write side
answered NONE for an unclicked row while every READ endpoint — campaigns, ad
groups, targets, accumulated, watchlist, reports — went on answering 0.0 for the
same row, and the CSV exports wrote 0%. Two modules, one question, two answers.
Every `cvr` field in the app is optional now, which is what let this land without
repeating the health decode crash.

### Fixed — reset-bids contradicted itself

`total_reduction` describes the PLAN. On a partial rejection the receipt printed
it as the headline and then said "Amazon refused 1 of 3" underneath, claiming a
saving that never happened. The headline is now what moved.

### Fixed — a detector that guards against silence could go silent

`_aws_plan_expiry_alerts` answered a malformed date with an empty list, which is
what the feed carries when there is nothing wrong. It guards the AWS account
holding the Marketing Stream queues, which fails by going quiet — the queues
lapse, Stream stops arriving, and Amazon carries on reporting the subscription
ACTIVE. A test asserted this behaviour and called it "ignored rather than fatal";
not fatal was right, silent was not.

The lint written in 0.4.13 to catch exactly this could not see it, because it
recognised only a bare `except Exception` by name and this was a tuple. Widened
to any handler that RETURNS an empty collection — while still allowing a narrow,
named `continue` that skips one item in a loop and lets the rest run, which
`_staleness_alerts` does deliberately for a table missing from an older database.

### Fixed — a Demand Feed export asserted a window its numbers did not have

The CSV headers said 30 days over figures that are all-time whenever the catalogue
export carried no 30-day column. The columns are named for what they hold now, with
a `basis` column saying which window each row is on. A sort saved before 0.4.13
also kept resolving to the old zero-valued field, so the new fallback never ran.

### Fixed — a second review, and eighteen more

The same two-model review was run again over this release's own diff, the second
pass told what the first had found and asked for what it missed. Eighteen more
defects, all reproduced by hand. Most were mine, and several were the fixes above
breaking something or landing half-done.

**`--verify-only` could erase every database.** The release gate ends by deleting
every `*.sqlite`, every WAL sidecar and the whole `outputs/` folder from `--out`,
and it ran in verify mode too. Pointed at the operator's own checkout that is
seven market databases, the Stream database, the catalogue cache and every output
file — deleted while the script prints "ok". The same file states the rule 600
lines higher, for the other destructive step: *"Verification must not touch the
tree it is judging."* Two guards now: `--out` is refused on this repository or on
any folder holding a `.env`, and the cleanup removes only what the run itself
created, so verifying twice leaves the tree byte-identical.

**UNKNOWN could still reach an action.** Three-valued comparisons were not
enough, because a missing value could be turned into a real one before the
comparison ever happened — `x + ""` produced the four characters `None` through an
f-string, `LOWER` and `CONCAT` did the same via `str()`, and `IF(...)` picked its
else-branch. `IF IF(target.cvr != 0, FALSE, TRUE): target.pause()` therefore
proposed a live pause on an unmeasured row, with an empty trace behind it. Every
function is strict now.

**And the same fix had broken two things that worked.** `x IN [NONE]` stopped
matching, because the literal was nested in a list where the syntactic check
could not see it; and `LET missing = NONE` lost the marker entirely, so a rule
that matched before went quiet with no explanation. Both are fixed by carrying
the authored `NONE` as a value rather than inferring it from the syntax — which
also settles `NOT (bid < NONE)`, where treating a relational operator as a null
test inverted a refusal into a match.

**"We cannot tell" was reported as "Amazon refused everything."** An empty
accepted set meant both, and the callers subtracted it from what they asked for
and said "Amazon refused all 40" — a claim nothing in the reply supports, which
invites re-running writes that may already have landed. The outcome is now
explicitly unconfirmed, and the app says so instead.

**A clause the mirror has not seen is no longer read as its ad group's state.**
It reported ENABLED, so the pause went out as a no-op and the audit row recorded
a previous state of ENABLED — meaning Undo would enable a clause the operator had
paused beforehand. Measured across all seven markets: zero clauses are missing
today, so this costs nothing and closes the hole for the day it does not.

**Six more gate holes.** The placeholder rule treated any four repeated
characters as fake, so a real refresh token containing a repeat would have been
published; it is eight now, against shipped placeholders forty characters long.
The money check discarded a whole line when it also held the documented
placeholder, and its strip was written with `\b`, which BSD sed silently
ignores — so it never ran at all. European decimals are covered on both sides. A
file the keyword scanner could not decode was skipped in silence. `_declared()`
read only plain assignments, so a type annotation would have emptied the
exclusion list. And `engine/` was not in the required-file list, so a tree with no
product in it could be certified.

**Two truth fields still reached no screen**, which is the mistake this release
already fixed once: the third skip bucket and the never-filled report tables both
computed and both invisible. Both are drawn now. Plus a kill-list sort that never
persisted and a Copy that emitted `Optional(...)`.

**Four tests passed for the wrong reason** and are rewritten to assert the thing
that actually changed rather than a decision that was already true.

### Notes

- 1003 Python tests, 231 Swift tests.
- Every fix mutation-tested: the fix reverted, the suite re-run, and the failure
  confirmed to name it. Three separate mutations on the rules evaluator alone.
- The release gate is at 16 checks. It is a ratchet, not a proof — it catches the
  leak classes somebody has already thought of, and this release added two more
  after an outside reader thought of them first.
- The gate failed the release on its own source twice more while this was
  written, once on a worked example in a comment and once on the changelog entry
  describing it. That file greps the tree it ships in; six releases have now
  learned it. Describe the shape, never write the example.

---

## [0.4.13] — 2026-08-23 — eight audit passes, and each one found what the last had missed

A full audit of the app and of the public repository, run four times over.
Every pass found something the pass before it did not, which is the case for
doing more than one — and the guard written after pass one missed the worst
fault of pass two, which is the case for testing the guards by breaking
them.

### Fixed — a partly-accepted batch is no longer reported as a total failure

`_http_ok` asks whether EVERY item in a batch went through. That is the right
question for a single-entity write, and almost every command is one. The
Approval Queue is not: it sends every approved negative in one call and every
approved pause in another.

Amazon rejects individual items inside a 207 routinely, a duplicate negative
above all. One such rejection used to mean the reply said nothing was applied
while the rest were live on the account, every row in the Audit Trail said
`failed`, and — for pauses — the local mirror was skipped entirely, leaving ad
groups Amazon had paused reading ENABLED. That last one is the exact desync
`_http_ok` was added to prevent, arrived at from the other side.

`_applied_subset` answers per item now, using the rejected ids the client
already parses. A batch that never returned 2xx still counts nothing, because a
transport failure has no body and its ids are absent from that list — which
would otherwise read as a clean run.

### Fixed — the Demand Feed was drawing every royalty as zero

Snap for MOD exports no `salesLast30` or `royaltyLast30`, and has not since it
replaced MerchFlow on 2026-08-15. `demand_feed` therefore ranks proven sellers
on LIFETIME royalty, says which window it used in `royalty_basis`, puts the
real figure in `royalty`, and honestly writes 0 into `royalty_last30`.

`ProvenSeller` decoded neither of the first two. It read the zero. Every proven
seller on the screen drew 0.00 royalty and 0 sales under a column headed
"Royalty 30d", and the default sort was on that same zero. It looked like a
working screen because the ranking underneath happened to be right.

The columns now show the figure the ranking used and name the window it covers.

### Fixed — three engine truths the app decoded into nothing

- `killlist.econ` says the market has no economics tables, so nothing could be
  judged at all. Undecoded, that reply is byte-identical to a healthy market
  with nothing worth killing — and a fresh install is what produces it.
- `import-apply.export_error` says the export was not adopted. The campaigns
  were still built, so the envelope is a success. The cost lands days later,
  when the economics cross the freshness gate still reading the old prices.
- `everywhere-preview.instances` is the list the engine deliberately keeps so a
  selection of 40 landing on 12 can explain itself. Every skip was described as
  "already at that state" — true for a paused ad group, false for a keyword
  with no target id, which the app cannot write to at all.

### Removed

- `bidreport.held`, which was hardcoded 0 and read as "changes held back" — a
  real thing the conflict guard and the volume cap both do, and one that
  function cannot see, because `writes_log` records what was written.

### Fixed — the two detectors guarding lost data could go quiet themselves

`_seasonal_tags_alerts` and `_rules_lost_alerts` both answered their own
exception with `return []`. An empty list is what the alerts feed carries when
everything is fine, so any bug inside either detector would take it off duty
for good with nothing anywhere saying so.

Their own docstrings say why they exist. The seasonal tag map was deleted on
2026-08-15 and the scheduler ran as a silent no-op for six days; an empty
`rule_defs/` reads exactly like a fresh install, so the nightly evaluates
nothing and reports success. Each detector could fail in precisely the shape it
was built to catch.

The project had reasoned this through once already, at `stream_check_failed`:
"the check that watches for a SILENT failure must not fail silently itself." It
was never carried across. Six of the eight alert builders were already right.

Both raise the new `guard_check_failed` now, keyed on the exception type so a
persistent fault alerts once rather than on every poll.

Two more silent handlers, both on paths where the silence changed an outcome:
`catchup.pending_reports` answered 0 — meaning "nothing pending" — on any
error, ending a catch-up at once and reporting a clean finish over reports it
never collected. `harvest_promote_group` swallowed the read that says which ad
groups already exist, which is not a missing optimisation but a different
decision: the builder stops reusing and starts creating, on a live account,
from a read that failed.

### Fixed — seven scripts answered a new install with a stack trace

Run against a fresh checkout with no `.env` and no databases — which is exactly
a new install — seven engine scripts printed a Python traceback. Four did it
for `--help`, the worst case available: somebody asking how a tool works,
answered with a stack trace, because the argument was being read as a filename.

`inspect_accounts.py` is a DIAGNOSTIC, so it is what a stuck reader reaches
for, and it crashed on not having set up credentials yet. `get_token.py` raised
`EOFError` when it was not attached to a terminal, and it is the first script
in the setup guide.

CLAUDE.md already stated the rule for the JSON bridge — a missing operator file
is a state to REPORT, not an exception to leak. The command-line half had never
been held to it.

### Security — two identifiers were public, in shapes no check could see

- **Account totals.** `docs/review-2026-08-04.md` carried the real all-time
  spend and profit, written as Amazon writes them, with the currency symbol
  AFTER the number. One check wanted the symbol before it, another wanted a
  decimal point where this had a comma, and a third was looking at paths. The
  figures are gone; the sentences still work, because what they were making a
  point about was the ratio between two cards.
- **An ads-account id**, hardcoded in `engine/inspect_accounts.py`. Not a
  credential, but it named the account permanently — and it was a bug on its
  own terms, since the script's job is to answer "is MY account reachable" and
  it asked that about somebody else's for every reader but one. It reads
  `AMZN_ADS_ACCOUNT_ID` now, and lists everything when that is unset.
- **The operator's macOS login**, in a Claude Code project path where the
  slashes are dashes, so the home-path check walked straight past it.

### Fixed — a preview accepted five kinds of rule the save would refuse

`rules-validate` and `rules-save` run the parser AND the semantic checks.
`rules-preview` ran only the parser, so five classes of rule that can never be
saved previewed anyway — and two of them previewed as a confident number:
`target.explode()` and `target.createKeyword(...)` each reported "72 changes"
for a verb that cannot execute.

The quiet one is worse, because it does not look like an error at all. A
misspelt field previewed as "matched 0", which is exactly what a correct rule
matching nothing looks like. The operator reads "no rows meet my condition"
when the truth is the field name is wrong.

Also accepted: a window past Amazon's ~92-day retention, and a rolling window
on an entity with no per-day history. Preview runs the same checks now, and all
thirteen saved rules preview unchanged.

### Fixed — the one place the engine failed OPEN

Every ratio in the DSL now answers `NONE` when its denominator is zero, and
`NONE` matches no numeric comparison, so a rule skips the row instead of acting
on a number nobody measured. `acos`, `cpc` and `roas` already worked that way.

`cvr` and `ctr` answered `0`, which is not "unknown" — it is the worst possible
score, on exactly the two metrics an author writes with `<` because low is bad.
So `IF adGroup.cvr < 8%` was TRUE for every ad group that had never been
clicked: **37,330** of them on the live US account, against a rule whose action
is `pause()`. This engine fails closed on unknown data everywhere else —
economics, snapshot dates, rolling windows, batch writes — and here it failed
open, in the direction of switching ads off. The rules guide already described
the corrected behaviour, which is what its author believed it did.

**It changes nothing today, and that was measured rather than assumed.** All 53
rule previews across all seven markets return identical counts before and
after: the only shipped rule that reads `cvr` demands `clicks >= 15` first, so
it never met the zero-click case. What the fix does change is the trap: a rule
written without a clicks floor drops from 38,501 matches to 1,171 — the ones
that were actually clicked and convert badly. A measured zero still counts:
forty clicks and no orders is a real 0%, and still matches.

Verified correct in the same audit: percent literals, money literals, AND/OR
precedence, three-valued NULL logic, rolling-window arithmetic and the
attribution lag, no-op filtering, and the volume cap falling back to the
shipped default rather than to zero.

### Added — two truth fields that had been decided against, and were wrong to be

Both were judged acceptable earlier in this audit and re-examined. Neither
survived the second look.

- **`health.tables`** — the newest snapshot date of each perf table. The screen
  showed `latest_data`, the WORST of the three, which is the right number to
  gate writes on and useless for working out what broke. The three tables are
  filled by three independent Amazon report jobs; that is the entire reason for
  the standing rule against dating one from another, a mistake that recurred
  three times and once froze US bids, pauses and harvest for four nights while
  `campaign_perf` stayed green throughout. `stale_tables` only names a table
  once it is four days behind — and two days behind is exactly when it is worth
  seeing. System Health now names any table that trails the freshest.

- **`stream-today.unkeyed_messages`** — the one caveat on that panel pointing
  the other way. Every other Stream warning says the day may read LOW; this says
  it may read HIGH, because a traffic row with no id is kept rather than
  collapsed and a redelivery counts twice. It has been 0 every day since the
  subscription opened, which was the argument for leaving it — and the day it is
  not zero is the day the panel is wrong, with `stream-verify` unable to help
  because it only judges days that have already settled.

### Security — a keyword the account bids on, in a preview fixture

A leak class no check could see, because it is ordinary English. Not an ASIN,
not money, not an identifier, not a path — two everyday words in a SwiftUI
preview, which the account bid on in sixty-odd targeting rows across three
markets. It is also the most commercially useful thing an ads repository can
give away: an ASIN says which product, a keyword says how the money is made.

The new check asks the market databases rather than a pattern, and reads
`targeting_perf` only — what the operator CHOSE to bid on — never
`search_term_perf`, which is what shoppers typed. That distinction is the whole
check: the trademark blocklists in `demand_feed.py` and the Harvest screen are
full of famous names that appear in search terms with zero targeting rows
behind them, and reading that table would fail the release on a list of things
the operator is carefully NOT selling.

Amazon's product-type labels are read out of `products.py` rather than listed,
and the seasonal example file is skipped because nobody invented Christmas.
With no databases to hand the check reports that it could not run rather than
passing.

It caught itself first, on the comment explaining it — the fourth check here to
learn that this script greps the tree it is part of.

### Added — the nightly now says where its hours go

The run takes 2h43m and recorded exactly two numbers: the moment it started
and the moment it finished. Nothing said which phase owned the time, so a
phase that doubled read as a busier night, and no optimisation could be
checked afterwards — the catalogue cache is claimed to save about seven
minutes and that claim could not be tested from anything on disk.

`step()` already wraps every phase to catch failures, so it now also takes the
wall time: two `date +%s` calls, no behaviour change, nothing that can fail a
run. `outputs/last_run_status.json` carries `steps` sorted slowest first plus
`total_step_seconds`, and System Health's banner says how long the run took
and which phase owned it, with the top twelve in the tooltip.

The sum of the steps is deliberately NOT presented as the run length. The gap
between them is what the script does between phases, and a test pins that the
two are allowed to differ.

Every read command the app makes was timed in the same pass and none needs
work: 0.14–0.88s each, including interpreter start-up, with `halo` at 0.67s on
its cache and `alltargets` at 0.94s.

### Verified — the app names every control it owns

An earlier draft of these notes claimed the opposite: that all 25 sidebar rows
exposed no accessibility name and VoiceOver read the app's primary navigation
as "button" 25 times over. **That was wrong, and it was published.** The
correction is here rather than quietly deleted, because the reason it happened
is worth more than the claim was.

The measurement was taken through AppleScript's System Events, which reports
`AXDescription` as missing for a SwiftUI Button even when it is set. Reading
the accessibility API directly shows the real value. Three "fixes" were then
tried against that broken instrument and each appeared to change nothing —
which is exactly what a fix looks like when the thing you are fixing is not
broken.

Read with the real API: **37 of the app's 37 controls carry a name.** All 25
sidebar rows announce "Dashboard", "Campaigns", "Kill List" and so on, and they
did so before anything was changed — SwiftUI derives a Button's name from the
Text inside its label. The only seven unnamed controls in the window belong to
the system: the scrollbar arrows and the close, minimise and full-screen
buttons, each already carrying the subrole a screen reader names it by.

What survives from the episode is small and real: the sidebar rows now carry an
explicit label, so the name survives someone rearranging the row, and a hint,
which `.help` could not provide — a tooltip is a pointer affordance and a
screen reader does not read it as guidance.

The lesson is recorded at the call site: measure SwiftUI accessibility with the
accessibility API, never through System Events.

### Verified — the other twenty-four screens

Every screen was driven through the accessibility API, clicked and read. All
load and render; none crashed or showed an error state. Two that first read as
empty were the measurement, not the app: Campaigns and Organic Halo keep their
content in a Table, which a static-text scrape cannot see, and Halo needs
longer than two seconds to load. Screenshots confirmed both — 374 campaigns,
and 874 designs measured with 682 clean reads.

### Added — guards, because each of these was found by eye

- `tests/rules_preview_validate_tests.py` asserts that validate and preview
  reach the same verdict for any rule, and pins which ratios answer zero.
- `tests/detector_failure_tests.py` reads every `_*_alerts` builder and fails
  on any that answers its own exception with a bare empty list.
- `tests/script_usage_tests.py` runs every engine script twice in a temporary
  directory holding the code and nothing else, and fails on any traceback.
- `tests/app_contract_tests.py` diffs every field the engine sends against
  every property the app declares. The class it guards had been found by hand
  five times. Its first version read `appctl` alone and missed the Demand Feed
  — so it reads the payload MODULES too, proved by restoring the bug.
- Three new release checks: a money figure with a trailing or absent currency
  symbol, a real `amzn1.` identifier, and the operator's login in any spelling.
  Each proved by planting the exact leak, and each proved NOT to fire on the
  legitimate counts it sits beside — an alarm that noisy gets muted, and then
  the real one is missed too.

949 Python tests, 223 Swift.

---

## [0.4.12] — 2026-08-23 — the nightly runs at 01:00 Merch time, and now says so

Yesterday's release explained the nightly hour against the wrong clock. It told the
reader to schedule from their own marketplace's midnight, with a table per marketplace.
That is not what the engine does, and `daily_metrics.py` had said so in a comment the
whole time: **daily report days are anchored to Seattle / Pacific for EVERY market**, so
Amazon's ad calendar means the same date wherever the job runs from.

The operator's 10:00 was never a local habit either. It is **01:00 Seattle**, which is
what 10:00 happens to be in Central Europe. The number had been published for a week
with its meaning stripped off.

### Changed

- **`install_launchd.sh` computes the hour instead of hardcoding it.** The default is
  01:00 Merch time, converted to the installing machine's clock through the system
  timezone database, so DST is handled on both sides and half-hour zones work.
  The docs also now say WHY that hour: the run asks Amazon for *yesterday*, and
  `daily_metrics.py` resolves which date that is on the Seattle clock. At 01:00 there,
  yesterday is the day that ended an hour ago — finished and ready to report. At 23:00
  there the date has not rolled over, so "yesterday" still means a day already banked,
  and the job would re-ask for it nightly while never collecting the fresh one. It prints
  what it chose: `01:00 Seattle is 10:00 here — scheduling for that`. `--hour` and
  `--minute` still override. On this operator's machine it computes 10:00 — identical to
  the job already installed, so re-running changes nothing.
- Every document now explains the schedule against Seattle rather than the reader's own
  marketplace: `docs/SETUP.md` (rewritten, with a conversion table), the README,
  `docs/COMMANDS.md`, `docs/WINDOWS.md`, and the launchd template's comment.
- The docs state the one honest limit: the conversion is fixed at install time, and the
  US and Europe change their clocks on different dates, so for a couple of weeks each
  spring and autumn the job runs an hour off. That is well inside the slack the engine
  already carries.

### Fixed

- `install_launchd.sh` refuses a `date +%z` that is not a numeric offset rather than
  parsing it into a plausible wrong hour. A wrong schedule is silent: the job just runs
  at a time nobody chose.

---

## [0.4.11] — 2026-08-23 — one release, and it publishes itself

The repository showed **v0.2.5** as its latest release while the code was 0.4.10, because
nothing in the publish flow ever touched GitHub Releases. Worse, the six old tags were
still downloadable, and two of them carried the retired strategy's name — removed from
the working tree weeks earlier, and frozen public in a tag no force-push can reach.

That is the shape of the problem: the release checks are a ratchet, so each new leak class
becomes the next check, and everything already tagged keeps whatever the next check would
have caught. Audited before deleting anything: no ASINs, no revenue figures and no
personal identity in any of the six.

### Added

- **`scripts/publish_release.sh`** — publishes exactly ONE release and deletes any older
  one, so a single published tree is a structural property rather than something to
  remember. The notes are the version's own `CHANGELOG.md` section, so a release note
  cannot say something the changelog does not. `--dry-run` shows it without changing
  anything.
- `make_public_snapshot.sh` adds the `origin` remote when `--owner` is given. The snapshot
  repository is recreated by every build, so its remote went with it, and it had been
  re-added by hand three times.

### Changed

- The snapshot's closing instructions name the whole publish — push, then release — rather
  than a repository-creation command that only applies once.
- The README's documentation table was missing `WINDOWS.md`, `BUILD-A-UI.md` and
  `CHANGELOG.md`. With no release history published, the changelog is the version record,
  and nothing in the README linked it.
- The README claimed 683 Python tests. It is 897. A count maintained by hand in two places
  drifts, so the README no longer carries the authoritative one.

---

## [0.4.10] — 2026-08-23 — "daily at 10:00" answered the wrong question

The nightly's hour was written as a bare `10:00` in six user-facing places and explained
in none of them. A new user has no way to tell whether that number is a requirement, a
recommendation, or one person's habit. It is the third.

### Changed

- **The hour is now explained against the MARKETPLACE's clock, not the reader's.** Amazon
  closes a sales day at midnight in the marketplace's own timezone and only then starts
  building the report for it, so that midnight — not yours — is what the schedule has to
  clear. [docs/SETUP.md](docs/SETUP.md) gains a timezone table and two worked examples;
  the README, `docs/COMMANDS.md`, `docs/WINDOWS.md`, `scripts/install_launchd.sh --help`
  and the launchd template all point at it instead of repeating a number.
- The docs also now say the reassuring half, which was missing entirely: **this is hard
  to get badly wrong.** Amazon re-attributes for days so the freshest day or two is always
  incomplete, `daily_metrics.py` fills in any settled day it finds missing, and the rules
  refuse to act on stale evidence rather than acting on half of it. What actually matters
  is that the computer is awake.
- `install_launchd.sh --help` printed its option list split in half around a comment
  block. The usage lines are together again.

---

## [0.4.9] — 2026-08-23 — it did not run on Windows, and nothing said so

Someone asked whether a new user on Windows could set this up. They could not, and no
document mentioned Windows at all. Two habits in the engine were quietly wrong there,
and both matter on Linux too.

### Fixed

- **Forty-two text files were opened without naming an encoding.** Python then uses the
  platform's preferred encoding — UTF-8 here, cp1252 on a default Windows install. Every
  product title with an accent and every European price with a currency symbol would
  decode to the wrong characters, or raise on the way back out. The catalogue and
  sales-report readers had always named theirs; nothing else had, and the gap was
  invisible because both sides agree on macOS.
- **Four database connections built their `file:` URI by formatting the path into a
  string.** A URI carries neither a backslash nor a bare drive letter, so on Windows
  SQLite is handed a name that is not the database. A POSIX path holding `?` or `#` fails
  the same way, since everything after it parses as the query. `db.file_uri()` builds it
  properly and fixed that second case as a side effect.

### Added

- **[docs/WINDOWS.md](docs/WINDOWS.md)** — setup through WSL, what runs there and what
  does not, and the nightly scheduled with Task Scheduler rather than cron. Cron cannot
  run inside a Linux that Windows has shut down, and Windows shuts it down when the last
  terminal closes. The page says which of its steps have not been run on a Windows
  machine, because untested setup instructions cost a new user an evening.
- **[docs/BUILD-A-UI.md](docs/BUILD-A-UI.md)** — the app is SwiftUI and will never run on
  Windows, but the app is not the product. The engine answers 108 commands with one JSON
  object each, enforced by `serve_protocol_tests` and `stdout_contract_lint_tests`, and
  the shipped app uses nothing else. So this is the contract, the four rules a front end
  must obey so it cannot bypass the safety rails, and a prompt to hand an AI coding agent.
- **`tests/portability_tests.py`** — five tests reading the syntax tree, not grepping.
  Both guards were proved by breaking the code and watching them name the exact line.
- `catalog-cache` and `catchup` reached no user-facing page. Both are in
  [docs/COMMANDS.md](docs/COMMANDS.md) now, and the release check that requires
  documentation to exist now covers the two new pages.

### Changed

- The README states the platform truth in the requirements table: the engine runs on
  macOS, Linux and Windows through WSL; the app is macOS only.

---

## [0.4.8] — 2026-08-22 — the five things the audit had only looked at

The previous two passes fixed what they found and listed five things they had
not. This closes all five. Each was CHECKED before it was touched, and two of
the five turned out not to be what the audit said they were.

### Fixed

- **`ads_client.load_env` reports a missing `.env` instead of raising.** One
  bare `FileNotFoundError` surfaced two different ways, and both broke the
  standing rule that no traceback may reach the envelope. In-process,
  `stream-status`, `stream-setup`, `stream-drain` and `seasonal-apply` answered
  `{"ok": false, "error": "[Errno 2] No such file or directory: '…/.env'"}`.
  Wrapped in a script, `status` and `backfill-daily` captured the whole Python
  stack into the reply's `stderr` field with `code: 1` the only sign of trouble.
  A string `SystemExit` fixes both at the source: the dispatcher turns it into a
  sentence, and a wrapped script prints that sentence with no stack. The file's
  CONTENTS are still never read on this path.
- **The Stream undercount check could fail silently.** `_stream_undercount_alerts`
  wrapped `stream_verify.verify()` in a bare `except: return []`, so a renamed
  column or a schema change would have switched off the ONLY check that can see
  Stream dropping data — and the alerts feed would have stayed clean, which is
  what it looks like when everything is fine. It now raises the
  `stream_check_failed` alert, keyed on the exception TYPE so a persistent fault
  alerts once. A market with no Stream data does NOT come through here: verify
  returns `comparable:false` with a reason, confirmed live against UK, DE and
  USKDP before the change.

### Added

- **`prune-snapshots [--days N] [--apply]`, and a Monday step in the nightly.**
  The three perf tables gained a row per entity per pull and nothing had ever
  deleted one — US `targeting_perf` was 2.0M rows over 45 snapshot dates, about
  52,000 a night, and the seven databases came to 2.0 GB. The window is 400
  days, chosen against the measured depth: the deepest table spans 67 days, so
  this deletes NOTHING today and caps the future instead. `date < cutoff`, so a
  row exactly at the edge is kept, and each table is counted on its own dates
  because the three are filled by independent report jobs. Every one of them
  indexes `date` first, so the delete is a seek. Deleting does not shrink the
  file — SQLite reuses the pages, which is what bounds growth — and no VACUUM
  runs against a database the app holds open. Eight tests, five mutations.
- **`tests/envelope_contract_tests.py` — all 105 commands, not eight.**
  `OneShotStdoutContract` covered the economics-driven ones; this sweeps every
  command in the dispatcher against an EMPTY data folder and asserts one JSON
  object with no `Traceback` and no `[Errno`. That empty folder is also what
  makes it safe: `ENV_PATH` follows `MERCHADS_DATA_DIR`, so with no credentials
  on disk nothing can reach Amazon — checked before the test was written. It
  found the six leaks above. 2.1 seconds for 104 commands, on a thread pool.
- **`aws_plan_expiry`** — the AWS account holding the Stream queues is on the
  free plan and closes 2027-02-21. The bill is about nothing either way, so this
  is paperwork, and it is the dangerous kind of deadline because of HOW it
  fails: the queues go, Stream stops arriving, and Amazon carries on reporting
  the subscription ACTIVE, so the day just reads quieter. Silent until 60 days
  out, and it keeps speaking after the date rather than going quiet at the worst
  possible moment. Nine tests, four mutations.

### Not a defect after all

- **The four "dead" engine modules are hand-run operator tools.**
  `enrich_types`, `export_paused_asins`, `paused_audit` and `scav_cleanup` are
  not imported by anything, named in no shell script, and absent from appctl and
  launchd — which is what a CLI utility looks like. `export_paused_asins` builds
  a manual price-change worklist and was edited on 2026-08-20, two days before
  being flagged. The detection heuristic was wrong, not the code; nothing was
  removed. Recorded here so the next sweep does not flag them again.
- **The 5h45m nightly is Amazon, not the engine.** The log shows
  `…generating: mtd (720s)` — twelve minutes waiting on one report to build.

### Known, still open

- **`stream-verify` has not judged a day yet, and cannot until 2026-08-23.**
  Hours 0–9 of 2026-08-21 began before the subscription existed, so that day can
  never be whole; 2026-08-22 is the first that can, and it needs the nightly to
  bank it. Nothing to do — the alert runs the check by itself, and it can now no
  longer fail silently.

Tests: 821 Python, 162 Swift.

---

## [0.4.7] — 2026-08-22 — the one guard that counts

Six rules apply to a live account every night across seven markets with nobody
looking. Every guard they pass through judges ONE change: the KILL file, the
economics gate, the snapshot freshness gate, the cross-rule conflict guard, the
per-market bid ceiling, the no-op check. Not one of them counts.

So a condition one character too loose — `>= 1` where `>= 15` was meant — matches
tens of thousands of targets, and every gate above waves it through. The data is
fresh, the economics are available, no two rules disagree, and a pause is not a
bid so no ceiling touches it.

### Added

- **A per-market VOLUME cap on one automatic run.** `db.AUTO_CHANGE_CAP_DEFAULT`
  is 500, read by `executor.execute`, editable with `appctl change-cap --set N`
  (`--set 0` turns it off, `--clear` restores the default). `rules-approve` is
  exempt: those ids were picked by hand in the queue, so the human gate has
  already happened.
- **500 is measured, not guessed.** Counting only the actions a rule can emit,
  the busiest day in any market's `writes_log` is US 2026-06-29 at 255 — and
  that includes the hardcoded phases. Every EU market peaks at 26, and a normal
  night across the whole account is 4 to 49 writes. So the cap is about twice
  the busiest day ever and a hundred times an ordinary one.
- **`tests/rules_volume_cap_tests.py`** — 14 tests. Seven mutations were checked
  to fail: the gate deleted, an off-by-one that would block a run exactly at the
  cap, a corrupt value failing open, a database with no `engine_meta` failing
  open, the approve exemption deleted, and the exemption copied onto the nightly
  where it must never be. An eighth — restoring the `changes[:cap]` slice — was
  checked and found EQUIVALENT rather than missed: the refusal above it already
  guarantees nothing is left to truncate.

### Changed

- **Past the cap a run applies NOTHING.** It used to apply `changes[:cap]` and
  set `truncated: true` — half an account acted on, no refusal, and a flag that
  reached no screen. A partial apply leaves the account in a state no rule
  described and no operator chose. The refusal names the count, the cap, and the
  three ways forward: fix the rule, run it in REVIEW mode and approve from the
  queue, or raise the cap.
- **The reply carries `cap` instead of `truncated`.** `tests/rules_executor_tests.py`'s
  old `test_change_cap_truncates` now asserts the refusal, and says why the
  behaviour it used to protect was the bug.
- **A cap that cannot be READ falls back to the shipped default, never to 0.**
  A corrupt value or a connection without `engine_meta` would otherwise switch
  the guard off on exactly the databases nobody had looked at closely. Both were
  found by mutation, and the second was a real hole in the first draft.

### Fixed, found by asking what a capped night would actually look like

- **A refused run would have reported itself as a large success.**
  `rules-nightly` builds its own summary instead of spreading the executor's
  reply, and it read a BLOCKED result like a normal one. `count` on a refusal is
  what was PROPOSED, so it became `total_applied`: 700 pauses announced, none
  made. And `results` is empty on a refusal, so `zip(kept, results)` ran zero
  times and the reason reached no rule row. The nightly would have printed a
  confident number with no explanation anywhere in it — and the KILL freeze lost
  its reason the same way, which predates this release.
  `_nightly_apply_summary` now gives a refusal its own branch, reports zero
  applied, and puts the reason, the cap and the proposed count at the top of the
  reply as well as on every auto rule's row. Three mutations checked to fail.
- **A test that hung for sixty seconds, three times in one session.**
  `nightly_market_discovery_tests` runs the nightly's inline snippets, one of
  which starts `json.load(sys.stdin)`. `capture_output=True` redirects stdout
  and stderr and leaves stdin INHERITED, so from a terminal the child waited on
  the tty until the timeout and the suite went from 13s to 73s before failing.
  From a pipe already at EOF it passed instantly, which is exactly why it read
  as a flake and got re-run on its own three times. `stdin=subprocess.DEVNULL`
  fixes it; the file's header blamed SQLite locks, which is the right worry for
  the other snippets and was the wrong diagnosis for this one.

### Note

The new `change-cap` command was caught by yesterday's own documentation lint
before this shipped — added to the dispatcher, absent from the handoff, and the
suite failed naming it. That is the guard from 0.4.5 doing its job on its author.

Tests: 799 Python, 162 Swift.

---

## [0.4.6] — 2026-08-22 — an engine fix could be green in the repo and absent from the app

A second review pass. The rules were calm — 51,687 entities evaluated across the
six auto rules and two changes proposed, both correct. No crashes, no alerts, no
stale markets, the econ gate open everywhere, and Stream's coverage arithmetic
checked out hour by hour.

The find is about shipping rather than about the engine. The app became
standalone on 2026-08-21 and carries its own copy of the modules at
`Contents/Resources/engine`. It runs those, not the checkout. Nothing said so.
The freshness hash covered only `MerchAds/`, and the standing rule still
promised that a relaunch was enough for Python. So an engine-only fix could pass
its tests, satisfy the Stop hook, and never reach the running app.

Yesterday's stdout fix escaped this only because it happened to touch two Swift
files, which forced a rebuild.

### Fixed

- **`.claude/hooks/app_src_hash.sh` hashes everything the bundle ships**:
  `MerchAds/`, `engine/**.py` including `rules/`, `run_scheduled.sh` and
  `run_stream_drain.sh`. `scripts/package_app.sh` stamps the same hash, from the
  repo root rather than the `MerchAds` folder. Proved by editing one engine
  file: the old hash did not move, the new one did, and the Stop hook now blocks.
- **The standing rule in CLAUDE.md said the opposite.** It now says to package on
  every surviving change, engine included, and records why the old wording was
  true until the day the app started carrying its own engine.
- **A proposed bid is rounded where it is built, not on the way out.**
  `setBid target.bid * 1.10` lands on something like 0.187 and the executor
  rounded it to 0.19 as it wrote. The preview did not, so the Approval Queue
  offered a number the account was never going to receive: seen live on
  2026-08-22, the queue said 0.187 and the write would have been 0.19. Preview,
  no-op check, queue and write now speak about one number. Nothing downstream
  changed — `_is_noop` and the executor already rounded to two places.
- **Two sentences in the docs were simply wrong.** The handoff said an edit in a
  non-US market is "refused with the reason", seventeen lines below its own
  sentence saying every market is editable. Only the TEE LADDER form is refused
  outside US; `--type X --price P --royalty R` works everywhere and is how the EU
  markets get an operator number over a derived median. `cmd_royalties`' own
  docstring still described non-US as read-only while the code returns
  `editable: True` unconditionally. A reader who believed either would hand-edit
  `products.py`, which the Product Royalty tab exists to stop.

### Added

- **`tests/rules_noop_tests.py::ProposedMoneyIsTheMoneyThatGetsWritten`** — five
  tests, the load-bearing one running a real preview end to end and asserting no
  proposed bid carries a third decimal. Four mutations were checked to fail:
  the rounding removed, rounding to three places, `setBudget` forgotten, and a
  non-numeric argument dropped instead of kept. The end-to-end test skipped on
  its first draft because the fixture proposed nothing; a test that skips is not
  a test, so the rule was rewritten in real DSL syntax and the skip replaced
  with an assertion that something was proposed at all.

### Known, not fixed — one decision for the operator

- **Nothing limits how MANY changes an automatic rule may apply in one night.**
  `executor.execute` takes `cap=50000` and `rules-nightly` uses the default. A
  normal night is 4 to 49 writes across the whole account; the busiest rules day
  on record is 533. Every other guard is about VALUE or SAFETY — the KILL file,
  the econ gate, the snapshot gate, the conflict guard, the bid ceiling, the
  no-op check — and all of them would wave through a rule whose condition was
  one character too loose. Six rules are on AUTO and apply without anyone
  looking. Worse, at the cap the executor applies the first N and flags
  `truncated`, so a runaway would half-apply rather than stop. A fail-closed
  cap — refuse the whole run and report when the count is absurd — would match
  how every other gate in this engine behaves, but it changes what happens to a
  live account, so it is the operator's call rather than an audit's.
- **The perf snapshot tables have no retention.** US `targeting_perf` holds 45
  snapshot dates and 2.0M rows, growing about 52,000 rows a night; the seven
  databases total 2.0 GB against 75 GB free. Years away from mattering, but it
  is unbounded and nothing prunes it.
- **`stream-verify` still cannot judge a day.** Hours 4, 5 and 6 of 2026-08-21
  never arrived, and they are correctly counted PARTIAL rather than missing —
  they began before the subscription existed. The first whole day is 2026-08-22,
  so the first real comparison is possible once tonight's day is banked.

Tests: 781 Python, 162 Swift.

---

## [0.4.5] — 2026-08-21 — the guard had three holes, and something was in each

An audit of the whole app. The engine, the nightly, the six markets and the
Swift app were all healthy: 766 Python tests and 162 Swift tests green, every
market fresh, no alerts anywhere, the nightly finished clean.

The stdout contract was not. The lint written in 0.4.3 reads the call graph, but
it started at the modules appctl NAMES and never left them. Three kinds of leak
fitted through the gaps, and all three were sitting there.

The worst is on every live write. `AdsClient` is built inside about fourteen
appctl handlers, and `_send_retry` printed its 429 and 5xx backoff notices to
stdout. Amazon throttles routinely — that is why the retry exists — so
`appctl setbid` answered with two lines of plain text and then the envelope.

### Fixed

- **`ads_client` sends its retry notices to stderr.** Eight prints across
  `_send_retry`, `access_token`, `get_keyword_recommendations` and
  `product_metadata`. Proved before and after by forcing a 429 against a stub
  and capturing both streams: two lines on stdout before, none after, and the
  human still reads them on stderr.
- **`appctl.cmd_kdp_titles`** printed a per-market failure line onto the
  envelope's own stream. The lint never read appctl's own handlers.
- **`db.bulk_write` and `db.store_targets`** print the disk-I/O diagnostics that
  this project already fought once. `appctl sales-report --import` reaches
  `bulk_write` through `sales_import.bank`, so a failed import answered the
  Import screen with `!! BULK WRITE FAILED` and then the envelope.
- **`stream_sqs.delete_batch`**, reached from `stream-drain` through
  `stream_drain.drain_queue`. The drain runs hourly and from the app.

### Changed

- **The lint starts at appctl, crosses module boundaries, and follows objects.**
  Three rules, one per blind spot: every function in `appctl.py` is a root; a
  `module.function()` call is followed into that module; and a method call is
  followed when the local variable was built from an engine class in the same
  function. `engine/rules/` is read as well — it is clean today, and a print
  added there would have been invisible before.
- **A print is a leak unless it carries `file=` or its only argument is
  `json.dumps(...)`.** That second shape IS the envelope, which is how
  `_import_failed` answers a startup that died before there was a dispatcher.
  Recognising the shape keeps that honest without an allowlist a later edit
  could quietly grow.
- **Two pure parsers are `nonisolated`.** `DashboardView.windowLabel` and
  `ProductRoyaltyView.decimal` take a string and return a value. They were
  main-actor only because they live inside a `View`, which cost 16 Swift 6
  warnings in the test target. `Format` is nonisolated and guards its shared
  formatters with a lock, so nothing was relying on the isolation.

### Added

- **The seven missing commands are written down**, in `docs/claude-code-handoff.md`:
  `everywhere-preview`, `everywhere-apply`, `export-date`,
  `harvest-promote-group`, `harvest-suggest`, `portfolio-cap` and `run-status`.
  Each gets its reply shape and the screen that calls it. All seven were built,
  tested and shipped without ever reaching the document CLAUDE.md calls ground
  truth. A missing command is worse than an undocumented function: the next
  reader does not find it, builds a second one beside it, and nothing fails.
- **`tests/command_docs_lint_tests.py`** so it cannot drift again. It reads the
  dispatcher, reads the two documents, and names anything in the first and not
  the second. Matching is backtick-strict: a command counts when it opens a code
  span or follows `appctl` inside one. A loose substring search would call `run`
  documented because the word appears in a hundred sentences — the same failure
  the stdout lint already had once. Four mutations were checked to fail,
  including both halves of the parse quietly finding nothing.
- **Five checks on the stdout lint's own machinery.** One per blind spot, plus
  two that fail if the parse ever finds nothing — a lint that reads an empty
  graph passes forever and says nothing while it does.
- All five were confirmed by breaking the walker on purpose: dropping
  cross-module calls, dropping bare local calls, dropping object resolution,
  returning an empty graph, and no longer treating appctl's functions as roots.
  The first version of the planted-leak test passed with bare-following deleted,
  because every appctl function is a root and the bare hop was inside appctl.
  The hop moved out to a second module and the mutation started failing.

### Known, not fixed

- **`stream-verify` has never judged a real day.** The subscription began
  2026-08-21, so the first day Stream saw whole is 2026-08-22. Until then the
  one check that can prove Stream is not dropping data correctly refuses, and
  the `stream_undercount` alert cannot fire. Worth reading the day it can.
- **Eight of 105 commands are checked for the stdout contract at runtime.** The
  lint now covers all 105 statically, which is the half that reaches the write
  paths. The runtime half still only runs the economics-driven commands.

Tests: 776 Python, 162 Swift.

---

## [0.4.4] — 2026-08-21 — a list that showed twelve and counted fifty-one

`stream-today` returned the twelve biggest-SPENDING campaigns of the day, with
no count beside them and no flag saying the list was cut. On 2026-08-21 that
was 12 of 51 campaigns holding 2,478 of the day's 4,465 impressions: a
truncated list shaped exactly like a complete one, and 45% of the day's traffic
attributed to nothing.

### Fixed

- **`stream-today` returns every campaign that served, ranked by impressions.**
  A US day is about fifty campaigns, so the cap bought nothing and cost the
  reader almost half the day. The sort is the same argument `placements`
  already made three lines above in the same function: early in a day almost
  nothing has spent, so ranking on cost puts a campaign that served 89
  impressions above one that served 900, and buries the fact that the second is
  running at all. The campaign list did it anyway.
- **A cap a caller asks for is now reported, not silent.** The reply carries
  `campaign_count` (the true total) and `campaigns_truncated`. Conflating what
  was returned with what exists is the same defect `accumulated-asins` had, and
  it hides in exactly the same way.

### Added

- **`tests/stream_map_tests.py::CampaignRollup`** — four tests, the load-bearing
  one being that the campaign rows add up to the headline totals. The existing
  add-up test could never have caught this: its fixture had two campaigns
  against a cap of twelve. Both mutations (restoring the cost sort and the cap;
  reporting the shown count as the true count) were checked to fail.

### Changed

- **CLAUDE.md: clear `__pycache__` between mutations.** CPython decides a `.pyc`
  is current from the source's mtime and size, both at one-second resolution. A
  mutate-test-restore loop rewrites one file several times inside a second, so
  the interpreter can keep running the previous version — which reads as a
  mutation nothing caught, or a fix that did not take. Half an hour was spent on
  a test that failed under `discover`, passed under a direct module run, and had
  correct source on disk both times.

Tests: 766 Python, 162 Swift.

---

## [0.4.3] — 2026-08-21 — the same bug, one level down

0.4.2 fixed `harvest_prune.build_plan`, which prints to stdout when the
economics gate is closed. It did not fix `harvest_prune._pause_batch`, which
does the same thing and which `appctl` also calls in-process — from
`harvest-prune-apply`, two lines below the call that was fixed.

Re-checking the fixes found it. The guard written in 0.4.2 could not: it runs
READ commands, and `_pause_batch` is on a write path that needs a live Amazon
client and an approved plan. No runtime test is ever going to exercise that
against the real account.

### Fixed

- **`harvest_prune._pause_batch` writes its two notices to stderr.** Confirmed
  by calling it with a stub client and capturing stdout — it produced
  `keywords: paused 1 (ok)` in front of where the envelope goes.

- **`stream_drain.drain_queue`'s handshake notice goes to stderr too.** It was
  safe only because `appctl` happens to pass `verbose=False`. That is one
  keyword argument away from breaking the envelope, with nothing to catch it.

### Added

- **`tests/stdout_contract_lint_tests.py`** — reads the call graph instead of
  running commands. It collects every `module.function(...)` call `appctl`
  makes in-process, walks each one through its own module, and fails on any
  `print()` without `file=`. That covers the write paths a test suite cannot
  reach, and it is what would have caught this in 0.4.2.

  The rule has no exceptions, deliberately: a print that is safe today because
  of what its caller passes is not safe, it is lucky. The lint carries its own
  checks that it parses something real and that it sees through one function
  into the next — the exact gap that let this one through.

Verified by putting the leak back and watching the lint name the file and line.

---

## [0.4.2] — 2026-08-21 — an audit of the whole app

Four defects found by testing every path rather than reading it. None of them
announced itself: two only appear on a machine that has never run the app
before, one told the operator that Amazon had lost data when it had not, and
one had quietly stopped 162 tests from running at all.

### Fixed

- **An hour that predates the subscription is no longer reported as a delivery
  failure.** Stream sends nothing about the past, so every hour before the
  subscription was created holds at most whatever Amazon's short catch-up
  included. Hours that carried a fragment were already named `partial`; hours
  that carried nothing were named `missing` — the word the panel prints as
  "never arrived", which means data was lost and cannot be recovered. On the
  first live day that read as three lost hours out of twelve. Nothing was lost;
  nobody was listening. `_partial_hours` is now judged over the whole expected
  range, and only an hour we were actually listening for can be missing. A real
  gap after the subscription is still reported exactly as before.

- **`seasons` and `seasonal-preview` no longer crash on a fresh install.**
  `load_config` tried three sources in order — the config, the backup, the
  shipped example — and then read the config file regardless. The example is a
  REPO file, and the app ships standalone now, so a data folder that has never
  held one made the read raise `FileNotFoundError`. It travelled out of the
  bridge intact and the Seasonal screen showed an absolute filesystem path
  where it should have said that nothing is tagged yet. A genuinely fresh
  install now gets an empty config, which every reader already handles. A
  backup still outranks it, which is the ordering the whole guard exists for.

- **`harvest-prune` no longer prints prose to stdout.** `appctl` promises
  exactly one JSON object there. With the economics gate closed, `build_plan`
  put a notice in front of the envelope. The app survived it only because its
  decoder rescans lines; anything else reading the contract got garbage.
  `phase2_apply` had already learned this and writes to stderr with a comment
  explaining why — `harvest_prune` now does the same.

- **The Swift test target had not compiled since 2026-08-21.** Adding `stream`
  to `HealthResponse` broke six fixtures. The app target and the test target
  compile separately, so the app still built and installed cleanly, and CI runs
  only the Python suite — so 162 tests simply stopped running, with nothing
  anywhere to say so.

### Added

- **`package_app.sh` now runs the Swift tests before it builds.** CI cannot do
  this: the app targets macOS 26 with Swift 6 and GitHub's runners lag that.
  Packaging is the one step the standing rule guarantees on every surviving
  Swift change, so the check belongs there. `SKIP_SWIFT_TESTS=1` bypasses it
  for an engine-only emergency rebuild.

- **`tests/serve_protocol_tests.py::OneShotStdoutContract`** — the one-shot
  path now has the guard the serve path already had. The serve worker sinks
  stray stdout, so a print that leaks there is invisible; the same command run
  directly is not. It runs every economics-driven command against an empty data
  folder — which closes the gate and takes the branch that used to print — and
  asserts stdout is exactly one JSON object carrying no filesystem error and no
  traceback. Both fixes above were confirmed by breaking them again and
  watching it fail.

- **`tests/seasonal_guard_tests.py::GenuinelyFreshInstall`** and three coverage
  tests in `tests/stream_map_tests.py`.

---

## [0.4.1] — 2026-08-21 — the first live day, and what it cost to trust it

0.4.0 put Marketing Stream on the Dashboard. The first live day proved the panel could
be internally consistent and still be wrong, and the operator caught it by eye twice
before any check did. Everything here comes out of chasing that.

### Fixed

- **The hourly drain could not keep up, and said nothing.** Stream sends roughly one
  message per impression, so a day of ~25,000 impressions is on the order of ten
  thousand messages, handed over about ten at a time. The job budgeted 60 seconds,
  which reads fewer messages than a single hour delivers, so the queue grew all day
  while every run logged a healthy count of messages banked. The budget is 300 seconds
  per queue now; `drain_queue` returns whether it EMPTIED the queue or ran out of
  clock; and a queue left full is reported through `stream_store.health()` as
  `drain_backlog` and drawn amber on System Health.
- **An hour that arrived was treated as an hour that was whole.** Amazon sends a short
  catch-up when a subscription is created and promises nothing about how far back it
  reaches. Of the eleven hours on the first day's panel, five never arrived, five held
  only fragments, and one was real — and coverage had no way to say so, because it knew
  only "delivered" and "missing". `coverage.partial_hours` is the third state; the app
  draws those hours amber and names the two shortfalls separately.
- **The header said "account time -07:00", which is precise and answers nothing.** It
  now reads "through 10:00 Amazon time (19:00 yours)".

### Added

- **`appctl stream-verify [--day D]`** — the check none of the others could do. Every
  other test proves the pipeline reads faithfully what ARRIVED; this measures one
  settled day twice, once from Stream and once from `campaign_daily`, and compares them
  per campaign. It REFUSES days Stream could not have seen whole and days the report has
  not banked, because a day that is expected to read low proves nothing.
- **A `stream_undercount` alert.** The comparison runs by itself rather than waiting for
  someone to remember the command, and lands on System Health.
- **35 tests, and mutation testing to prove they bite.** Seventeen deliberate
  breakages were planted across `stream_map`, `stream_store`, `stream_drain` and
  `stream_verify`; every one was caught. The exercise also found a test that passed for
  the wrong reason, a constant that had never been exercised, and the dedupe bug below.

### Changed

- **The two Stream datasets are deduped differently now, because they mean different
  things.** `sp-traffic` carries DELTAS — `impressions` is 1 or 2 and a correction
  arrives as -1 — so many messages share the same hour, ad, keyword and placement on
  purpose. They are keyed on `idempotency_id` alone and a message without one is KEPT
  and counted in `unkeyed_messages`, never collapsed: an overcount announces itself the
  moment `stream-verify` compares a day, an undercount announces itself nowhere.
  `sp-conversion` carries RESTATED SNAPSHOTS — one row per ad, keyword, placement and
  click-hour, resent as the figure grows — so those stay keyed on the row's natural
  grain with the newest winning, because summing two restatements would invent sales
  and inflated sales flatter ACOS. Both used to be one rule.

---

## [0.4.0] — 2026-08-21 — today, and where the ads actually showed

0.3.0 brought the hourly data in. It had no screen: it ran correctly for a day and the
only way to see it was a terminal. This release makes it visible, and adds two guards
found while looking.

### Added

- **A "Today so far" panel on the Dashboard.** Every other number on that screen comes
  from the nightly report, which is a day behind by design. This one comes from
  Marketing Stream, which is about an hour behind. It carries its own header saying so,
  because the two must never be read as like for like.
- **Placement.** Where the ad was shown — Top of Search, Detail Page, Other, Off Amazon.
  This dimension is not in the report pipeline at all and has never been visible here.
  The first day's data says the ads reach Top of Search about 1.4% of the time.
- **`engine/stream_map.py`** and **`appctl stream-today`** — the banked messages turned
  into one market's day. A message finds its market through its CAMPAIGN ids, never
  through `marketplace_id`: Merch US and KDP US both advertise on `ATVPDKIKX0DER`,
  confirmed against the profiles endpoint, so the marketplace would merge two separate
  advertisers into one number. The resolved advertiser is cached; an unresolved one is
  reported, never dropped.
- **`appctl stream-advertisers`** — that mapping, and how it was decided.
- **A Marketing Stream line in System Health**, from local state only. No AWS call, so
  the screen stays fast and works offline.
- **`tests/run_all.py`** — the suite with a watchdog. If it has not finished in five
  minutes, every stack is printed and it exits. The suite hung twice leaving nothing but
  an exit code, which is the worst kind of failure to debug.

### Changed

- **Organic Halo takes 0.20 seconds instead of 7.0.** Two causes. `analyze()` called
  `design_title()` once per design — 55,246 round trips to build 300 rows — and now does
  it in one query. And the result is cached, keyed on what the answer depends on: the
  newest banked `target_daily` day, the row counts, and the sales report's name, size and
  mtime. No clock is in the key, so nothing goes quietly stale. A caller-supplied
  connection is never served from or written to the cache.
- **Every market now has a daily-budget ceiling.** All seven had bid ceilings and none
  had a budget ceiling, so a `setBudget` from the rules language had nothing between a
  typo and a $400/day campaign. Set with headroom over what each market actually runs.
- **Every test subprocess has a hard timeout.** Six tests shell out to `appctl` against
  the real market database. Without a timeout, a child blocking on a SQLite lock hangs
  the whole suite forever. Now it fails with a name.

- **Sales and orders on the today panel**, once `sp-conversion` delivers. It began the
  same evening, so this is built against real payloads rather than a documentation page.
  Three things about that dataset shape the code:
  it reports on **30-day attribution**, the same window `phase0_pull` and
  `daily_metrics` read, so the two figures never disagree;
  a conversion is dated to the hour of the **click**, not the purchase, so a message
  arriving tonight with a six-day-old window is that day's sale and not today's;
  and it arrives late and gets restated, so today's figure only ever grows.
- **ACOS is still withheld for a day in progress.** The spend for an hour is final about
  an hour later and its sales are not, so the ratio of the two is always alarming and
  always wrong. The panel says why instead of leaving a blank.

### Known

- The suite can still occasionally block for 60 seconds on one of the shell-out tests
  when the app is running. It now reports that instead of hanging. Making those tests
  hermetic is separate work.

---

## [0.3.0] — 2026-08-21 — hourly data, pushed instead of polled

Until now the engine ASKED Amazon to build a report and then waited. `phase0_pull.py`
polls for up to 25 minutes and defers anything slower to the next night. A missed night
needs a catch-up pass with several rounds.

Amazon Marketing Stream turns that around. Amazon pushes hourly Sponsored Products rows
into an SQS queue you own, about an hour behind the hour they describe. Nothing to ask
for. Nothing to poll.

**It does not replace the report pipeline.** A subscription starts the clock, and Stream
sends little about the past — roughly a day of backlog on subscribe, not months. History,
backfill and the Monday 30-day true-up stay with reports. It also does not change
Amazon's attribution lag: the freshest day or two is under-attributed either way.

### Added

- **`engine/stream_config.py`** — the only place that knows realms, AWS regions, dataset
  ids and Amazon's per-dataset publisher accounts. **Each dataset publishes from a
  different AWS account**, so one queue policy reused for both datasets silently drops
  every message of the second while the subscription still reports `ACTIVE`. The policy
  is therefore generated by `stream-setup`, never hand-copied.
- **`engine/stream_api.py`** — subscription create / list / archive. Refuses a second
  subscription to the same dataset, because two mean every row arrives twice.
- **`engine/aws_sigv4.py`** — AWS Signature Version 4 in the standard library. No boto3:
  the Mac app ships its own CPython carrying only `requests`, and the point of that
  bundle is that it needs no `pip`. Pinned against AWS's published `get-vanilla` test
  vector — canonical request, string to sign and Authorization header, all three.
- **`engine/stream_sqs.py`** — `ReceiveMessage`, `DeleteMessageBatch`, and the SNS
  `ConfirmSubscription` handshake.
- **`engine/stream_drain.py`** — the drain loop. **It answers the SNS handshake too.** A
  new subscription parks a `SubscriptionConfirmation` in the queue and sends nothing
  until its token is confirmed; until then the subscription sits at `PENDING` and every
  screen looks healthy. Amazon's own reference implementation spends a Lambda, a second
  queue and a CDK stack on this. Messages are deleted from SQS only after they are
  committed locally, so a crash costs a redelivery, never an hour of data.
- **`engine/stream_store.py`** — `stream_data.sqlite`, its own database beside the market
  files. One queue serves a whole realm, so all five EU markets share one queue and their
  messages arrive mixed. Which market a message belongs to is a payload field, so arrival
  is kept separate from interpretation: messages are banked whole and mapped later,
  against real data rather than against a documentation page. `stream-fields` counts the
  keys real payloads carry.
- **Six `appctl.py` commands** — `stream-status`, `stream-setup`, `stream-fields` (read);
  `stream-subscribe`, `stream-unsubscribe` (live writes, kill-switch gated and logged to
  `writes_log`); `stream-drain` (reads AWS, writes only locally).
- **`run_stream_drain.sh`, `scripts/install_stream_drain.sh`** and an hourly launchd
  template. Hourly, not nightly: freshness is the whole point, and a nightly drain would
  deliver the same day-old picture reports already give. `package_app.sh` ships the drain
  script inside the bundle beside the nightly, so `--app` leaves the schedule depending
  on nothing but the app and the data folder.
- **`docs/marketing-stream.md`** — the setup walkthrough, including the AWS console steps.
- **`tests/stream_tests.py`** — 22 tests. The suite is now 683.

### Changed

- **`README.md` no longer says the app is "not self-contained".** That stopped being true
  in 0.2.6, when the bundle started carrying the engine and its interpreter.

### Notes

- Marketing Stream needs **no separate Amazon application**. An account already
  integrated with the Ads API can subscribe with the credentials it has. It does need an
  AWS account, one SQS queue per dataset, and a queue policy — `docs/marketing-stream.md`
  walks through it.
- Creating a subscription requires a `clientRequestToken`. Amazon rejects the call
  without one, and it is absent from the reference client.

---

## [0.2.6] — 2026-08-21 — the app stands on its own

### Added

- **Merch Ads runs with nothing installed beside it.** The bundle now carries the Python
  engine (`Contents/Resources/engine`), a relocatable CPython 3.12 with `requests`
  (`Contents/Resources/python`) and the nightly script. It used to need three things from
  outside itself — a checkout of this repo, a system `python3`, and `requests` installed
  into that `python3` — and any one of them going missing turned every screen into
  "appctl.py not found". A Homebrew upgrade was enough. The interpreter brings its own
  OpenSSL, so the Amazon calls work, and its own SQLite 3.53, which the app's own process
  does not have. Verified by running it with an empty environment.
- **Your data stays where it is.** The databases, `.env`, `outputs/` and the operator
  config live in the folder named in Settings and are never inside the app, so replacing or
  deleting the app cannot touch a row of banked history. The folder reaches the engine as
  `MERCHADS_DATA_DIR`, and `engine/paths.py` refuses to guess: a folder that is named but
  not there, or a guess that would land inside the bundle, stops the process instead of
  reading an empty database and reporting "no changes".
- **`install_launchd.sh --app`** points the nightly at the copy inside the app, so the
  scheduled run stops needing a checkout too.
- **System Health says when the nightly skipped markets.** A run that covers fewer markets
  than are configured is not a failed step — the loop finishes everything it starts — so it
  reported "all steps OK" for five nights while only one market was being advertised. The
  banner now names the markets that went unadvertised, and it reaches the Errors screen.

### Fixed

- **A failed request desynced the app until it was restarted.** One request to the engine
  had to produce exactly one line; a request that FAILED produced two. Every reply after it
  then belonged to the previous request — the Dashboard showed the kill list's numbers
  under "Monthly history", with no error anywhere. It only fired when a request failed,
  which in practice meant while the nightly held the database.
- **Bad arguments, an unknown market and a missing data folder** come back as the normal
  `{"ok": false, "error": …}` reply instead of an exit code with the reason cut off.
- **The Seasonal screen opens on the designs that can actually be paused.** The tag map is
  the whole catalogue, but only a few hundred designs have an ad group; sorting by season
  put the rest first. Advertised designs sort first now, with an "Advertised only" filter.
- **Packaging stamped the bundle with the wrong fingerprint** when built from a git
  worktree, and the fingerprint depended on where the repo was checked out.

---

## [0.2.5] — 2026-08-15

### Added

- **Every ASIN in the app is a link to its Amazon product page.** Click one and the
  listing opens in your browser. The domain follows the market the row belongs to, so a
  DE row opens on amazon.de — the same design is a different listing in every
  marketplace. Only the ASIN text is clickable, so clicking anywhere else in the row
  still selects it. A value that is not a real ASIN — some screens fall back to an
  ad-group id — stays plain text rather than becoming a link to a missing page.

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


---

[0.2.3]: https://github.com/zdufs/merch-ads/releases/tag/v0.2.3
[0.2.2]: https://github.com/zdufs/merch-ads/releases/tag/v0.2.2
[0.2.1]: https://github.com/zdufs/merch-ads/releases/tag/v0.2.1
[0.2.0]: https://github.com/zdufs/merch-ads/releases/tag/v0.2.0
