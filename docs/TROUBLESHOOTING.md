# Troubleshooting

Start here whenever something looks wrong.

---

## Always run this first

```bash
python3 engine/appctl.py health
```

Run it **without** `ADS_MARKET` — it opens every market itself. It tells you, per market:

| Field | What it means |
|---|---|
| `latest_data` | The **worst** of the three performance tables. Each is filled by its own Amazon report job, and they drift apart. |
| `tables` | Per-table dates, so you can see which one froze. |
| `stale_tables` | Anything past the write-freeze threshold (3 days). |
| `target_daily` | Per-day coverage. **Rules with a rolling window refuse to write when their window has holes — this column is where you find out why a rule went quiet.** |
| `last_run` | Did the last nightly run finish, and which step failed if not. |

Most of the answers below start with something `health` showed you.

---

## Setup problems

### `ModuleNotFoundError: No module named 'requests'`

```bash
python3 -m pip install -r requirements.txt
```

If macOS refuses with "externally managed environment", add `--break-system-packages`, or
use a virtual environment.

### `KeyError: 'AMZN_ADS_CLIENT_ID'`

Your `.env` is missing, or missing that key.

```bash
cp .env.example .env      # then fill it in
```

Check for stray quotes and trailing spaces. The file is parsed as plain `KEY=value` lines.

### `401 Unauthorized` from Amazon

In order of likelihood:

1. **The refresh token is wrong or expired.** Rerun `python3 engine/get_token.py`.
2. **Your Ads API application is not linked** to your Login with Amazon security profile.
   Go back to [api-access-setup.md](api-access-setup.md) and check the linking step. This
   is the single most common setup mistake.
3. **The client secret was rotated** at developer.amazon.com and `.env` still has the old
   one.

### `403 Forbidden` on one market

That profile id belongs to a marketplace your account cannot reach, or you pasted the
wrong one. Re-run `python3 engine/list_profiles.py` and copy the ids again.

### `python3: command not found` in the nightly log, but it works in Terminal

launchd does not load your shell profile, so it has a much smaller `PATH`. If your Python
is from Homebrew or a virtual environment, put its absolute path into `run_scheduled.sh`,
or make sure the interpreter it resolves is one that has `requests`.

---

## Data problems

### The numbers do not match the Amazon console

Check which number you are comparing.

- Use **`trailing30`** from `appctl.py metrics` as the stable headline. It matches
  Amazon's trailing-30 view.
- `daily.settling == true` means the most recent day is **under-attributed**. Sales
  arrive for days after the click. Today and yesterday always look worse than they are.
  This is Amazon's behaviour, not a bug here.
- Currency is the market's own. Do not compare a EUR market against a USD total.
- **Month-to-date figures may disagree slightly** between `metrics.mtd` and `periods`.
  They use different sources on purpose: Amazon's MTD report attributes a little
  differently from banked per-day history. `periods` is internally consistent, so use it
  when comparing months against each other.

### A market's data is stale, others are fine

Look at `health` and find which table froze.

Amazon generates each report as a separate asynchronous job, and those jobs fail
independently. One market's daily report can outrun the polling window and get abandoned
while everything else succeeds.

The nightly run recovers automatically:

- Reports left `PROCESSING` or `FAILED` are retried the next night.
- `daily_metrics.py` gap-fills settled days it missed, up to 14 days back.

So **one stale day usually fixes itself overnight.** If a table is still stuck after two
nights, force a pull:

```bash
ADS_MARKET=ES python3 engine/phase0_pull.py
ADS_MARKET=ES python3 engine/daily_metrics.py
```

### The heat map has gaps but everything else looks fine

That is the daily **account** report having timed out on those days. The performance
tables and `target_daily` come from different report jobs and stay fresh, so trailing-30
figures, rules and the kill list are all unaffected. Only the per-day trend has holes.

### `data_stale` alert

A performance table's newest snapshot is 4 or more days old — the same threshold at which
writes freeze. One alert per incident.

Note that **3 days behind is normal for EU markets** on a pre-pull morning. They sit at a
structural two-day Amazon lag. The threshold is deliberately not tighter.

### `disk I/O error` during a bulk write

Databases run in WAL mode, which lets the app read while the nightly job writes, and bulk
writes are chunked with retries. If you still see this:

1. Check free disk space.
2. Make sure the databases are on a **local** disk. Network volumes, iCloud Drive and
   external drives that sleep all produce this.
3. Close anything else holding the file open, then retry.

You will see this message in the test output too. Some tests simulate the failure on
purpose — those runs still end in `OK`.

### Year-to-date looks too small

Amazon's reporting retention starts about **95 days back** and rolls forward. Anything
older is gone unless you banked it.

To reach further back, export the monthly history CSV from the Ads **console** and import
it:

```bash
python3 engine/appctl.py history-import ~/Downloads/history.csv
```

`supplemented: false` in the reply means that market's history cannot be extended. Only US
and UK can be: one console export covers every marketplace and carries no country column,
so DE, FR, ES and IT share a single merged EUR series.

---

## Automation problems

### The nightly job did not run

```bash
launchctl list | grep merchads
tail -50 outputs/scheduled_runs.log
cat outputs/last_run_status.json
```

Reinstall it if it is missing:

```bash
bash scripts/install_launchd.sh
```

**If you moved the repository folder, you must reinstall it.** The path is baked in.

The job only fires when the Mac is awake. It calls `caffeinate` to stay awake for the
duration, but it cannot wake a sleeping machine.

### The run finished but changed nothing

Work through these in order:

1. **Kill switch on?** `python3 engine/appctl.py kill`
2. **Approval mode on?** `python3 engine/appctl.py approval-mode` — changes are waiting in the
   Approval Queue.
3. **Data stale?** `python3 engine/appctl.py health` — a stale table freezes writes on purpose.
4. **Economics gate closed?** `python3 engine/appctl.py econ-gate` — the reply lists reasons.
   Usually a price export older than 21 days.
5. **Nothing actually qualified.** Run the phase in preview and read the output:
   `ADS_MARKET=US python3 engine/phase3_bids.py`

That order is roughly the order of likelihood.

### A rule stopped firing

- **`appctl.py health` → `target_daily`.** A rule with `IN LAST N DAYS` refuses to write
  when its window has holes. That is the most common cause.
- **`appctl.py rules-preview --rule "Name"`.** The per-condition trace shows exactly which
  condition failed and what the actual value was.
- **Conflict.** If another rule proposed a change to the same entity, the first rule in
  rule order wins and the rest are skipped. Check `conflicts` in the reply.
- **Season.** A rule scoped to a season only runs inside that season's window.
- **Mode.** A `REVIEW` rule queues instead of applying. `appctl.py rules-pending`.

### A bid was clamped

Expected. Every bid and budget write is clamped to your ceiling and logged with
`[adjusted]`. Check or change it:

```bash
python3 engine/appctl.py maxbid
python3 engine/appctl.py maxbid --set --target 0.75
```

### The kill list is empty

Usually correct rather than broken. A design only qualifies when CVR is below the floor
**and** ACOS is over that design's **own** break-even. Designs in a 30-day price
transition, with an unsupported price, or in a multi-ASIN cohort are excluded and counted
in `skipped`.

If `econ` says unavailable, run `python3 engine/map_products.py` and check
`python3 engine/appctl.py econ-gate`.

---

## Mac app problems

### The app shows nothing, or "no data"

Open **Settings** and check the engine folder path. It must point at the repository
folder that has `appctl.py` and the `.sqlite` files.

Then confirm the bridge works from Terminal:

```bash
ADS_MARKET=US python3 engine/appctl.py metrics
```

If that fails, the app will fail too. Fix it there first.

### "Merch Ads.app is damaged" or macOS refuses to open it

It is not notarized. Right-click the app and choose **Open**, then confirm. You only have
to do that once.

### The app looks stale after you changed something

Engine changes take effect on relaunch, because the app shells out to a fresh `appctl.py`.
Swift changes need a rebuild:

```bash
bash scripts/package_app.sh --install
pkill -x "Merch Ads"
open "/Applications/Merch Ads.app"
```

### The Dock icon is the old one

macOS caches Dock tiles aggressively.

```bash
mv "$(getconf DARWIN_USER_CACHE_DIR)com.apple.dock.iconcache" /tmp/
killall Dock
```

### The app beachballs on one screen

Known pattern: a text-heavy, width-capped screen with app-wide text selection enabled
pegs the main thread in SwiftUI's selection overlay. If you hit a new instance of it,
`sample "Merch Ads"` while it hangs will show it. The fix is
`.textSelection(.disabled)` on that screen.

---

## Build problems

### `xcodebuild` fails on a fresh clone

- Xcode 16 or newer, and macOS 26 or newer. The deployment target is macOS 26.
- Open `MerchAds.xcodeproj` in Xcode once and let it resolve.
- Signing is ad-hoc. You do not need a developer account, but you may need to pick a team
  in Xcode's Signing tab for local builds.

### Tests fail on a fresh clone

```bash
python3 -m unittest discover -s tests -p '*_tests.py' -t .
```

Expect `Ran 419 tests ... OK`. Tests need no credentials and no network; they use
temporary databases. Loud `BULK WRITE FAILED` and `still PROCESSING` lines in the output
are **simulated failures inside passing tests** — read the final summary line, not the
noise above it.

---

## Still stuck?

Open an issue with:

1. What you ran, exactly.
2. What happened, including the error.
3. `python3 engine/appctl.py health` output, **with your profile ids and any ASINs removed**.
4. Your macOS and Python versions.

**Never paste `.env`, an access token, a refresh token, or a client secret into an
issue.** If you already did, rotate the client secret at developer.amazon.com right away.

See [SECURITY.md](../SECURITY.md) for how to report a security problem privately.
