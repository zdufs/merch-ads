# Setup — from zero to your first nightly run

This walks you all the way from a fresh clone to an automated nightly job.

Work through it in order. Each step depends on the one before. Nothing writes to your
Amazon account until **Step 8**, and even then only after you say so.

Budget your time realistically:

| Step | How long |
|---|---|
| 1–3 (install, clone) | 10 minutes |
| 4 (Amazon API access) | **days to weeks** — Amazon has to approve you |
| 5–7 (credentials, first pull) | 30–60 minutes |
| 8 (going live) | your call, take it slowly |
| 9 (the Mac app) | 15 minutes |

---

## Step 1 — Check what you already have

**On Windows, read [WINDOWS.md](WINDOWS.md) first.** It sets up WSL, and then this
page is correct for you from Step 4 onward.

Open Terminal and run:

```bash
python3 --version
git --version
```

You need **Python 3.9 or newer**. Python 3.11 or newer is better — it reports SQLite
errors with names instead of numbers, which makes any future problem far easier to
diagnose.

If either command fails, install Apple's command line tools:

```bash
xcode-select --install
```

For the Mac app you also need **Xcode 16 or newer** and **macOS 26 or newer**. You can
skip the app entirely and use the engine from the command line.

---

## Step 2 — Clone the repository

```bash
git clone https://github.com/zdufs/merch-ads.git
cd merch-ads
```

Put it somewhere permanent. The nightly job and the Mac app both point at this folder.
Moving it later means re-running `scripts/install_launchd.sh` and updating the app's
Settings.

---

## Step 3 — Install the one dependency

```bash
python3 -m pip install -r requirements.txt
```

If macOS refuses with an "externally managed environment" error, either use a virtual
environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

or override it, which is fine for a single-purpose machine:

```bash
python3 -m pip install -r requirements.txt --break-system-packages
```

If you use a virtual environment, remember that the nightly job needs the same Python.
`run_scheduled.sh` resolves `python3` from your login shell's PATH, so activate the
environment in your shell profile, or give the job an absolute path.

---

## Step 4 — Get Amazon Ads API access

**This is the slow part and you cannot rush it.**

Follow **[api-access-setup.md](api-access-setup.md)**. It is written for non-developers
and walks through both Amazon websites that are involved:

- **developer.amazon.com** — where you create a Login with Amazon security profile.
  That gives you a **Client ID** and a **Client Secret**.
- **advertising.amazon.com** — where you apply for Ads API access, then link it to that
  security profile.

Amazon reviews the application by hand. Approval takes days, sometimes weeks. There is
no way to speed it up. Do the rest of this guide once you are approved.

At the end you will have:

| Credential | What it is |
|---|---|
| Client ID | Public id of your app |
| Client Secret | Password for your app — keep it secret |
| Refresh Token | Long-lived key so the tool can log in forever without you |
| Profile ID, one per market | Your advertising account id in each marketplace |

---

## Step 5 — Create your `.env`

```bash
cp .env.example .env
```

Open `.env` in a text editor and paste in your Client ID and Client Secret.

Then produce the refresh token. This helper walks you through the browser login and
prints the token:

```bash
python3 engine/get_token.py
```

Paste the refresh token into `.env` as `AMZN_ADS_REFRESH_TOKEN`.

Now list the advertising profiles your account can see:

```bash
python3 engine/list_profiles.py
```

Copy each marketplace's profile id into the matching line in `.env`.

**Start with one market.** Fill in `AMZN_ADS_PROFILE_ID_US` (or whichever is your
biggest) and leave the rest blank. The nightly job only runs markets that have a profile
id, so blank lines are how you stay scoped while you learn the tool.

> **`.env` is the only place your secrets live.** It is gitignored. Never commit it,
> never paste it into a chat or an issue, and never screenshot it. If it leaks, rotate
> the client secret at developer.amazon.com immediately.

---

## Step 6 — First data pull

This downloads your Amazon performance reports into a local SQLite database. It is
**read-only** — it cannot change anything in your account.

```bash
ADS_MARKET=US python3 engine/phase0_pull.py
```

Expect this to be slow the first time. A large US Merch account takes several minutes,
and the per-target bid mirror alone can take around nine. Amazon generates the reports
asynchronously, so much of that time is the engine politely waiting and polling.

You now have `ads_data.sqlite` in the folder. Other markets get their own file, named
`ads_data_<CODE>.sqlite`. All of them are gitignored.

---

## Step 7 — Confirm it worked

```bash
ADS_MARKET=US python3 engine/appctl.py metrics
```

You should get one JSON object with real numbers in it. Check the trailing-30 spend and
sales against what the Amazon Ads console shows you. They should agree.

A few more read-only commands worth trying:

```bash
ADS_MARKET=US python3 engine/appctl.py campaigns          # your campaigns
ADS_MARKET=US python3 engine/appctl.py killlist           # designs losing money
python3 engine/appctl.py health                           # all markets at once
```

`health` is the one to remember. It reports, per market, how fresh each data table is and
whether anything is stale. Run it without `ADS_MARKET` — it opens every market itself.

**If the numbers look wrong, stop here.** Do not turn on automation over data you do not
trust. See [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

---

## Step 8 — Going live, carefully

Read **[SAFETY.md](SAFETY.md)** first. Genuinely. This is the step where the software
starts spending your money.

### 8a. Test the kill switch before you need it

```bash
touch KILL
python3 engine/appctl.py kill          # should report kill_active: true
rm KILL
```

Learn this now, while nothing is at stake.

### 8b. Turn on approval mode

```bash
python3 engine/appctl.py approval-mode --on
```

The nightly run will now **propose** changes instead of applying them. You review and
approve them in the app's Approval Queue, or discard them. This is the right setting for
your first few weeks.

### 8c. Preview one phase by hand

Every phase runs in preview unless you pass `--apply`:

```bash
ADS_MARKET=US python3 engine/phase2_apply.py     # shows proposed negatives and pauses
ADS_MARKET=US python3 engine/phase3_bids.py      # shows proposed bid changes
```

Read the output. Do you agree with it? If not, that is what the
[Rules DSL](rules-dsl.md) is for — write your own logic instead of the built-in one.

### 8d. Install the nightly job

```bash
bash scripts/install_launchd.sh            # 01:00 Merch time, wherever you live
bash scripts/install_launchd.sh --hour 14  # override with your own local hour
```

It prints what it chose:

```
==> 01:00 Seattle is 10:00 here — scheduling for that.
```

**You do not have to work the time out.** The next section explains it if you want it.

Run it once immediately to check it works end to end:

```bash
launchctl kickstart gui/$(id -u)/io.github.zdufs.merchads
tail -f outputs/scheduled_runs.log
```

To remove it later:

```bash
bash scripts/install_launchd.sh --uninstall
```

### Why 01:00 Seattle, and why that is right for every marketplace

**Merch runs on Seattle time — `America/Los_Angeles` — and so does this engine.**
`daily_metrics.py` anchors "yesterday" and the month boundary to Pacific time for
**every** market, so that Amazon's ad calendar means the same date no matter where the
job runs from. Amazon still composes each marketplace's day in its own hours; what
Seattle fixes is *which calendar date counts as yesterday*.

So the schedule follows Seattle, not your own midnight and not your marketplace's.
Scheduling against your local clock would drift away from the date the engine asks for
— and if you sell in several markets, there is no single local midnight to use anyway.

**01:00 Seattle is one hour after that day closes — and that is the whole point.** The
run asks Amazon for *yesterday*, and the engine works out which date that is on the
Seattle clock. Fire the job at 01:00 there and "yesterday" is the day that ended an hour
ago: finished, and ready to be reported.

Fire it at 23:00 Seattle instead and the Seattle date has not rolled over yet, so
"yesterday" still means the day before — one you banked last night. You would re-ask for
an old day every night and never collect the fresh one.

The installer converts 01:00 Seattle to your clock for you, using your system's timezone
database, so DST on both sides is handled:

| If you are in | 01:00 Seattle is |
|---|---|
| Seattle / US Pacific | 01:00 |
| US Eastern | 04:00 |
| UK | 09:00 |
| Central Europe | 10:00 |
| India | 13:30 |
| Sydney | 18:00 |

One honest limit: the conversion is fixed when you install. The US and Europe change
their clocks on different dates, so for a couple of weeks each spring and autumn the job
runs an hour away from 01:00 Seattle. Re-run the installer if you want it exact — but
read the next paragraph first, because it almost certainly does not matter.

**This is hard to get badly wrong.** The engine does not depend on catching a day the
moment it closes.

- Amazon keeps re-attributing sales for days, so the freshest day or two is *always*
  incomplete. The engine knows this and treats it that way
  (`db.DAILY_ATTRIBUTION_LAG_DAYS`).
- `daily_metrics.py` fills in any settled day it finds missing, so one badly-timed or
  skipped run repairs itself on the next.
- The bidding rules refuse to act on stale evidence rather than acting on half of it.

So take the default and do not agonise. **The one thing that does matter: your computer
has to be awake.** A Mac can be woken by the job; a Windows machine cannot. After a
missed night, run `python3 engine/catchup.py`.

---

## Step 9 — The Mac app (optional)

```bash
bash scripts/package_app.sh --install
open "/Applications/Merch Ads.app"
```

This builds a Release binary, installs it to `/Applications/Merch Ads.app`, signs it
ad-hoc and validates the bundle.

On first launch, open **Settings** and point the app at this repository folder. The app
shells out to `appctl.py` and reads the SQLite files there, so it will show nothing until
that path is right.

macOS may refuse to open it the first time because it is not notarized. Right-click the
app and choose **Open**, then confirm. You only have to do that once.

Details, including why the packaging script exists: [packaging.md](packaging.md).

---

## Optional extras

### Discord digest

Put a webhook URL in `DISCORD_WEBHOOK_URL` in `.env` and the nightly run posts a summary
per market. To silence it without deleting the URL:

```bash
touch NO_DISCORD      # rm NO_DISCORD to turn it back on
```

### More markets

Add each market's profile id to `.env`, then pull it once:

```bash
for M in UK DE FR ES IT; do ADS_MARKET=$M python3 engine/phase0_pull.py; done
```

The nightly job picks up every market that has a profile id. See
[multi-market.md](multi-market.md).

### Hourly data instead of report polling (Amazon Marketing Stream)

The nightly pull asks Amazon to build a report and then waits. Marketing Stream does the
opposite: Amazon pushes hourly Sponsored Products rows into a queue you own, about an
hour behind the hour they describe.

It needs **no extra Amazon application** — an account already using the Ads API can
subscribe with the credentials it already has. It does need an **AWS account** and one
SQS queue per dataset.

```bash
ADS_MARKET=US python3 engine/appctl.py stream-setup        # what to create in AWS
# create the queues, paste the generated policy, add 4 lines to .env, then:
ADS_MARKET=US python3 engine/appctl.py stream-subscribe --dataset sp-traffic
ADS_MARKET=US python3 engine/appctl.py stream-subscribe --dataset sp-conversion
python3 engine/stream_drain.py --seconds 30                # answers the SNS handshake
bash scripts/install_stream_drain.sh --app                 # then hourly, on its own
```

The console steps are in **[marketing-stream.md](marketing-stream.md)**. Two traps it
guards for you: each dataset is published from a different AWS account, so one policy
reused for both queues silently drops half the data; and a new subscription sends nothing
at all until the SNS handshake in the queue is answered.

**This does not replace the nightly pull.** A subscription starts the clock and sends
little about the past. History and the Monday true-up stay with reports.

### Organic sales history (US only)

The organic-halo estimate and true profit need your dated Merch `SALES_REPORT`
CSV, which is the only source of **organic** royalty. The Ads API only reports
ad-attributed sales.

Export it from Merch by Amazon, then:

```bash
python3 engine/appctl.py sales-report --import ~/Downloads/SALES_REPORT-....csv
```

The app's **Import** screen accepts the same file by drag and drop.

### Historical months beyond Amazon's window

Amazon's reporting only reaches back about 95 days. To go further, export the monthly
account history CSV from the Ads **console** and bank it:

```bash
python3 engine/appctl.py history-import ~/Downloads/history.csv
```

Once banked, that is the only copy. It back-extends the year-to-date figures.

### KDP books

If you advertise books, add `AMZN_ADS_PROFILE_ID_US_KDP` to `.env`. A "KDP US" account
appears in the app's profile switcher.

Book economics are not guessed. Enter each book's list price and royalty — take the
royalty straight off your KDP dashboard, it is the most accurate source:

```bash
python3 engine/appctl.py kdp-book --asin B0XXXXXXX --list-price 12.99 --royalty 4.55
```

A book with no entry fails closed: its economics report as unavailable rather than
inventing a number.

---

## Where things live once you are set up

```
merch-ads/
├── engine/                  the Python engine — every module lives here
│   ├── appctl.py              the JSON API the app and you both call
│   ├── paths.py               the one definition of where everything is
│   └── rules/                 the automation DSL
├── MerchAds/                the Mac app's Swift sources
├── tests/                   the test suite — no credentials needed
├── scripts/                 install the nightly job, build the app
│
├── .env                     your credentials — gitignored, never commit
├── ads_data.sqlite          US data. Other markets: ads_data_<CODE>.sqlite
├── stream_data.sqlite       Marketing Stream messages, if you enabled it
├── KILL                     create this file to freeze all writes
├── NO_DISCORD               create this file to silence Discord
├── seasonal.json            your season tags — gitignored operator data
├── rule_defs/               your DSL rules — gitignored operator data
├── outputs/                 dashboards, reports, logs — gitignored
│   ├── dashboard.html         open this in a browser
│   ├── scheduled_runs.log     what the nightly job did
│   └── last_run_status.json   whether it succeeded
└── docs/                    this documentation
```

**Your data stays at the repository root, not inside `engine/`.** The databases, `.env`,
`KILL` and `outputs/` are all resolved from `engine/paths.py`, which is the single place
that knows where the repository is. The catalogue exports and the Merch `SALES_REPORT`
are read from the folder *above* the repository.

---

## Next

- **[SAFETY.md](SAFETY.md)** — the gates and the kill switch, in detail.
- **[COMMANDS.md](COMMANDS.md)** — everything you can run.
- **[rules-dsl.md](rules-dsl.md)** — write your own automation in plain English.
- **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** — when it goes quiet or breaks.
