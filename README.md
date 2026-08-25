# Merch Ads

**Amazon Sponsored Products automation for print-on-demand sellers, with a native macOS app on top.**

Two layers in one repository:

- A **Python engine** that talks to the Amazon Ads API. It runs every night, pulls your
  performance data into a local SQLite database, and applies bid changes, negative
  keywords, pauses and new campaigns according to rules you control.
- **Merch Ads**, a **native SwiftUI Mac app** that reads that database and gives you 27
  screens over it — dashboard, campaign browser, profit, kill list, approval queue,
  audit trail, and a rules editor.

It runs six Merch marketplaces (US, UK, DE, FR, ES, IT) plus a separate KDP books
profile. Everything is local. There is no server, no account, and no subscription.

Performance data arrives two ways: the nightly report pull, and — optionally —
**Amazon Marketing Stream**, which pushes hourly figures to a queue you own instead of
making you wait for a report to build. See [docs/marketing-stream.md](docs/marketing-stream.md).

---

## What makes it different

Most Amazon Ads tools optimise **ACOS** — advertising cost divided by ad revenue.
ACOS does not know what you actually earn.

This engine knows your **royalty**. It reads your real list price per design, applies
the Merch royalty table, and computes each design's **own break-even ACOS**. A $19.99
tee and a $24.99 hoodie get different verdicts on the same ACOS number. Every automated
decision — pause, bid, negative — is measured against profit, not against a single
account-wide ACOS target.

If the economics for a design are missing or stale, the engine **refuses to write**
rather than guessing. That is the design principle throughout: fail closed.

---

<!-- Screenshots: drop PNGs in docs/images/ and add a "## Screenshots" section
     here referencing them, e.g. ![Dashboard](docs/images/dashboard.png).
     Held back until there are real ones — an empty heading reads as unfinished,
     and every screen of this app shows a live account, so any capture has to be
     of test data or scrubbed before it goes in. -->

## Requirements

| | |
|---|---|
| **Operating system** | **The engine** runs on macOS, Linux, and Windows through WSL. **The app** is macOS 26 or newer, and macOS only. |
| **Xcode** | 16 or newer, only if you want to build the app. Swift 6. |
| **Python** | 3.9 or newer. One dependency: `requests`. |
| **Amazon Ads API access** | You must apply and be approved. **This is the slow part — allow days to weeks.** See [docs/api-access-setup.md](docs/api-access-setup.md). |
| **A Merch by Amazon or KDP account** | With live Sponsored Products campaigns. |

**You do not need the Mac app.** The engine is a complete command-line tool on its own.
The app is a viewer and a control surface over it.

---

## Quick start

```bash
git clone https://github.com/zdufs/merch-ads.git
cd merch-ads

python3 -m pip install -r requirements.txt

cp .env.example .env      # then fill in your Amazon credentials
```

Getting those credentials is the real work. The walkthrough is
**[docs/api-access-setup.md](docs/api-access-setup.md)** — written for non-developers,
step by step, with the two Amazon websites that trip everyone up.

Once `.env` is filled in:

```bash
python3 engine/list_profiles.py                       # find your advertising profile ids
ADS_MARKET=US python3 engine/phase0_pull.py           # first data pull (slow — expect minutes)
ADS_MARKET=US python3 engine/appctl.py metrics        # confirm you get real numbers back
```

Nothing above writes to your Amazon account. The first pull is read-only.

Full walkthrough: **[docs/SETUP.md](docs/SETUP.md)**.

**On Windows?** Start at **[docs/WINDOWS.md](docs/WINDOWS.md)** instead. The engine and
the nightly automation run there through WSL, which Microsoft ships and one command
installs. The Mac app does not run on Windows and never will — but the engine is the
product, and the app is a window onto it. If you want a window of your own, the engine
speaks JSON on purpose: **[docs/BUILD-A-UI.md](docs/BUILD-A-UI.md)**.

---

## Safety, before you automate anything

This software **spends your money**. Read **[docs/SAFETY.md](docs/SAFETY.md)** before you
enable any automation. The short version:

- **Kill switch.** `touch KILL` in the repo folder freezes every write, immediately. Every
  apply path checks it before it touches Amazon. `rm KILL` resumes.
- **Preview first.** Every destructive command has a preview mode and runs in preview by
  default. `--apply` is always opt-in.
- **Approval mode.** `python3 engine/appctl.py approval-mode --on` makes the nightly run propose
  changes instead of applying them. You approve them in the app's Approval Queue.
- **Ceilings and a change cap.** Set a maximum bid and daily budget per market and every
  write is clamped to it. Separately, one automatic rules run may apply at most **500 changes
  per market** — past that it applies *nothing* and says why, because a condition
  one character too loose can match tens of thousands of rows and every other gate would
  wave it through.
- **Audit trail.** Every write is logged to the `writes_log` table with its previous
  value. Most writes can be undone with one click.
- **Start in one market.** Leave the other profile ids blank in `.env` until you trust it.

---

## Documentation

| Doc | What it covers |
|---|---|
| **[docs/SETUP.md](docs/SETUP.md)** | Install from zero to first nightly run. |
| **[docs/api-access-setup.md](docs/api-access-setup.md)** | Getting Amazon Ads API credentials. Plain language. |
| **[docs/SAFETY.md](docs/SAFETY.md)** | Kill switch, gates, previews, undo. **Read this.** |
| **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** | How the engine, the database and the app fit together. |
| **[docs/COMMANDS.md](docs/COMMANDS.md)** | Every command, what it does, whether it writes. |
| **[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)** | When something breaks or goes quiet. |
| **[docs/rules-dsl.md](docs/rules-dsl.md)** | Writing your own automation rules in plain English. |
| **[docs/multi-market.md](docs/multi-market.md)** | Running several marketplaces. |
| **[docs/bidding-rules.md](docs/bidding-rules.md)** | The built-in bidding logic. |
| **[docs/royalty-reference.md](docs/royalty-reference.md)** | Merch royalty maths, per product type. |
| **[docs/marketing-stream.md](docs/marketing-stream.md)** | Hourly data pushed from Amazon, instead of polling for reports. |
| **[docs/packaging.md](docs/packaging.md)** | Building and installing the Mac app. |
| **[docs/WINDOWS.md](docs/WINDOWS.md)** | Running the engine on Windows, through WSL. |
| **[docs/BUILD-A-UI.md](docs/BUILD-A-UI.md)** | Building your own front end on the JSON contract. |
| **[CHANGELOG.md](CHANGELOG.md)** | Every version and what changed. This project publishes no release history beyond the current one, so this file is the record. |

The full documentation index is **[docs/README.md](docs/README.md)**.

---

## How the nightly run works

One launchd job fires once a day and loops every market that has a profile id in `.env`.
Per market:

1. **Pull** — download the Amazon performance reports into SQLite.
2. **Map** — resolve each ASIN to its product type and its real price.
3. **Dry run** — compute what would change.
4. **Harvest** — find search terms that converted and are worth their own keyword.
5. **Apply** — negatives, pauses, bids, new campaigns. Each step gated by the kill
   switch, by data freshness, and by an economics check.
6. **Rules** — run your own rules from the DSL.
7. **Bank** — store today's true per-day totals so history survives Amazon's ~95-day
   reporting window.

Install the job:

```bash
bash scripts/install_launchd.sh            # 01:00 Merch time, wherever you live
bash scripts/install_launchd.sh --hour 14  # override with your own local hour
bash scripts/install_launchd.sh --uninstall
```

**The default is 01:00 Seattle**, converted to your local clock for you. Merch runs on
Seattle time and so does the engine — `daily_metrics.py` anchors "yesterday" to Pacific
for **every** market, not just the US — so that is the clock the schedule follows.
[docs/SETUP.md](docs/SETUP.md#why-0100-seattle-and-why-that-is-right-for-every-marketplace)
explains it. It is hard to get wrong: the engine backfills any settled day it finds
missing.

---

## Hourly data, instead of waiting for reports (optional)

The nightly run asks Amazon to build a report and then waits — up to 25 minutes, and
anything slower is deferred to the next night.

**Amazon Marketing Stream** does the opposite. Amazon pushes hourly Sponsored Products
rows into an SQS queue you own, about an hour behind the hour they describe.

It needs **no extra Amazon application** — an account already using the Ads API can
subscribe with the credentials it has. It does need an AWS account and one queue per
dataset. Setup, with the console steps:
**[docs/marketing-stream.md](docs/marketing-stream.md)**.

```bash
ADS_MARKET=US python3 engine/appctl.py stream-setup        # what to create in AWS
ADS_MARKET=US python3 engine/appctl.py stream-subscribe --dataset sp-traffic
python3 engine/stream_drain.py --seconds 30                # answers the SNS handshake
bash scripts/install_stream_drain.sh --app                 # then hourly, on its own
```

**Stream does not replace the nightly pull.** A subscription starts the clock and sends
little about the past. History, backfill and the Monday true-up stay with reports.

---

Once it is running, the Dashboard grows a **"Today so far"** panel: spend, clicks and
CTR for the current advertising day, plus **where the ads actually showed** — Top of
Search, Detail Page, Other, Off Amazon. That last one is not in the report pipeline at
all, so it has never been visible here before.

Two things the panel deliberately refuses to say. It shows no sales, ACOS or conversion
rate while the `sp-conversion` dataset is empty, because a zero would read as "spent
money, sold nothing" rather than "cannot see sales yet". And when an hour was never
delivered it says so, because Stream does not resend and the day's total is then an
undercount.

## The Mac app

```bash
bash scripts/package_app.sh --install   # builds Release, installs to /Applications
open "/Applications/Merch Ads.app"
```

The app **is self-contained**. The bundle carries the Python engine, a relocatable
CPython with `requests`, and the nightly script, so a Mac with no Homebrew, no `pip` and
no checkout can run it. What stays outside is your **data** — the SQLite databases,
`.env` and `outputs/`. Point the app at that folder in Settings on first launch.

The app never writes to the databases and never calls Amazon directly. Every action goes
through `appctl.py`, so it inherits the same kill switch, the same gates and the same
audit log as the nightly run.

Build details and the signing story: [docs/packaging.md](docs/packaging.md).

**Not on a Mac?** You are not locked out of anything the engine does — only out of this
particular screen. Every button in the app is a call to `appctl.py`, which you can make
yourself, and building your own front end is a genuinely reasonable project:
[docs/BUILD-A-UI.md](docs/BUILD-A-UI.md).

---

## Your data stays yours

- Credentials live only in `.env`, which is gitignored. The Swift app never reads it.
- Performance data lives only in local SQLite files, which are gitignored.
- Your design catalog (`seasonal.json`) is gitignored operator data. The repo ships a
  `.example` version instead.
- Nothing is sent anywhere except to Amazon's own API, and to your own Discord webhook if
  you choose to configure one.

---

## Status and honesty

This was built by one seller for one operation, then cleaned up for sharing. That shows:

- The built-in campaign strategies (lottery, scavenger) encode **one** way of
  running Merch ads. Yours may differ. The **Rules DSL** exists so you can write your own
  logic without touching the engine.
- **The organic-halo estimate is US-only.** It needs the dated Merch `SALES_REPORT`
  export, which does not carry a marketplace column.
- The tee royalty table is calibrated for **US Merch prices**. Other markets derive their
  economics from observed data. Check `docs/royalty-reference.md` before trusting the
  profit figures for a new market.
- There is no installer and no code signing certificate. You build the app yourself.

The engine is covered by a large Python suite — currently 897 tests, and the number is
in `CHANGELOG.md` rather than repeated here, because a figure maintained by hand drifts.
Run them with:

```bash
python3 -m unittest discover -s tests -p '*_tests.py' -t .
```

---

## Contributing

Issues and pull requests are welcome. Start with
**[CONTRIBUTING.md](CONTRIBUTING.md)**. If you found a security problem, do not open an
issue — see **[SECURITY.md](SECURITY.md)**.

---

## License

**[Elastic License 2.0](LICENSE)** — source-available.

In plain terms:

- ✅ You may clone it, change it, and run it on your own Amazon account, including to
  make money.
- ✅ You may share your changes.
- ❌ You may not offer it to other people as a hosted or managed service.
- ❌ You may not remove the copyright and license notices.

If you want a different arrangement, open an issue and ask.

---

## Disclaimer

This software makes changes to a live Amazon Advertising account, which spends real
money. It is provided as-is, with no warranty. You are responsible for what it does to
your account. Read [docs/SAFETY.md](docs/SAFETY.md), start in preview mode, and keep the
kill switch in reach.

Not affiliated with, endorsed by, or connected to Amazon.
