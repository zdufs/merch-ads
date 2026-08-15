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

## Screenshots

<!-- Add screenshots here. Suggested: Dashboard, Rules editor, Approval queue, Profit. -->
<!-- Drop PNG files in docs/images/ and reference them like: -->
<!-- ![Dashboard](docs/images/dashboard.png) -->

---

## Requirements

| | |
|---|---|
| **Mac** | macOS 26 or newer for the app. The Python engine runs on any macOS or Linux with Python 3.9+. |
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
| **[docs/packaging.md](docs/packaging.md)** | Building and installing the Mac app. |

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
bash scripts/install_launchd.sh            # daily at 10:00
bash scripts/install_launchd.sh --hour 7   # or pick your own hour
bash scripts/install_launchd.sh --uninstall
```

---

## The Mac app

```bash
bash scripts/package_app.sh --install   # builds Release, installs to /Applications
open "/Applications/Merch Ads.app"
```

The app is **not self-contained**. At runtime it shells out to `appctl.py` and reads the
SQLite databases in this folder, so the engine has to be present and configured. Point it
at this folder in Settings on first launch.

The app never writes to the databases and never calls Amazon directly. Every action goes
through `appctl.py`, so it inherits the same kill switch, the same gates and the same
audit log as the nightly run.

Build details and the signing story: [docs/packaging.md](docs/packaging.md).

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

419 Python tests cover the engine. Run them with:

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
