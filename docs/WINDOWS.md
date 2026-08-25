# Windows

**The engine runs on Windows. The Mac app does not, and never will.**

That sentence is the whole page in short. The rest explains what you get, what
you do not, and how to set it up.

---

## What runs where

| | Windows | macOS | Linux |
|---|---|---|---|
| **The engine** — pulls data, sets bids, pauses losers, runs your rules | **yes**, through WSL | yes | yes |
| **The nightly automation** | **yes** | yes | yes |
| **The rules language** | **yes** | yes | yes |
| **The Mac app** — Dashboard, charts, approval queue | **no** | yes | no |

The Mac app is written in SwiftUI, which is Apple's alone. There is no port and
there will not be one.

**This matters less than it sounds.** The engine is the product. The app is a
window onto it. Everything the app can do, it does by calling the engine, and
you can call the engine yourself. See [COMMANDS.md](COMMANDS.md).

If you want a screen, see [BUILD-A-UI.md](BUILD-A-UI.md) — the engine speaks
JSON on purpose, so building your own front end is a real option rather than a
consolation.

---

## Why WSL and not native Windows

WSL is Linux, running inside Windows. Microsoft ships it. One command installs
it, and after that every instruction written for Linux is correct for you.

The alternative — running the Python directly on Windows — half works. The
engine itself is careful about this: it names UTF-8 on every file it reads and
builds its database URIs properly, both fixed on 2026-08-23 and guarded by
`tests/portability_tests.py`. But the nightly automation is a shell script, and
so are the install helpers. Rewriting those for PowerShell means two copies of
the same logic drifting apart forever, and this project has enough of those
lessons already.

So: WSL. One extra install step, and then nothing is special about your machine.

**An honesty note.** The Linux half of this page is exercised constantly — that
is the same code path the macOS engine takes. The Windows-specific wrapper steps
below are written from Microsoft's documentation and have **not** been run on a
Windows machine by the author. If a step is wrong, please open an issue; it will
be fixed and credited.

---

## Step 1 — Install WSL

Open **PowerShell as Administrator** and run:

```powershell
wsl --install
```

Restart when it asks. On the next boot it opens an Ubuntu window and asks you to
pick a username and password. That password is for Ubuntu, not for Windows, and
you will need it for `sudo`.

If `wsl --install` is not recognised, your Windows is too old. You need Windows
10 version 2004 or newer, or any Windows 11.

To check it worked, open **Ubuntu** from the Start menu and run:

```bash
uname -a
```

You are now in Linux. Every command from here on goes in that Ubuntu window, not
in PowerShell.

---

## Step 2 — Install what the engine needs

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv git
```

---

## Step 3 — Get the code

**Put it in your Linux home folder, not on the Windows C: drive.**

```bash
cd ~
git clone https://github.com/zdufs/merch-ads.git
cd merch-ads
```

This matters for speed, not for taste. Files under `/mnt/c/` are reached across
a translation layer, and the catalogue reader walks a million rows. The same job
that takes seconds in `~` can take minutes on `/mnt/c/`.

You can still reach these files from Windows. In File Explorer, type
`\\wsl$\Ubuntu\home\YOUR-NAME\merch-ads` in the address bar.

---

## Step 4 — Install the one dependency

```bash
python3 -m pip install -r requirements.txt
```

If Ubuntu refuses with "externally managed environment", use a virtual
environment instead:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

Remember that `source .venv/bin/activate` line — you need it in every new
terminal, and in the scheduled job in step 7.

---

## Step 5 — Everything else is the normal setup

From here, follow **[SETUP.md](SETUP.md)** from Step 4 onward. Nothing in it is
macOS-specific except the section about the Mac app, which you can skip.

The slow part is Amazon's API approval, and it has nothing to do with your
operating system. Allow days to weeks. See
[api-access-setup.md](api-access-setup.md).

---

## Step 6 — Where to put your Merch exports

The engine reads your product export and your sales report from the folder
**above** the repository. In WSL that is your Linux home:

```
~/                          <- the "POD folder"
├── snap-grid-export-*.csv  <- your product exports
├── SALES_REPORT-*.csv      <- your Merch sales reports
└── merch-ads/              <- this repository
```

When you download an export in Windows it lands in `C:\Users\You\Downloads`.
Move it across with:

```bash
mv /mnt/c/Users/YOUR-WINDOWS-NAME/Downloads/snap-grid-export-*.csv ~/
```

---

## Step 7 — Run it every night

Two ways. **Use the first one.**

### Windows Task Scheduler (recommended)

This is more reliable than cron inside WSL, because it starts WSL if it is not
already running. A cron job cannot run inside a Linux that Windows has shut
down, and Windows shuts it down when you close the last terminal.

1. Open **Task Scheduler** from the Start menu.
2. **Create Task** (not "Create Basic Task").
3. **General** tab: name it `Merch Ads nightly`. Tick **Run whether user is
   logged on or not**.
4. **Triggers** tab: **New**, daily, at whatever **01:00 Seattle** is on your
   clock — 04:00 US Eastern, 09:00 UK, 10:00 Central Europe, 18:00 Sydney.
   Merch runs on Seattle time and so does the engine, for every marketplace and
   not just the US. There is a conversion table and the reasoning in
   [SETUP.md](SETUP.md#why-0100-seattle-and-why-that-is-right-for-every-marketplace).
   It is hard to get badly wrong — the engine backfills any settled day it finds
   missing. (On macOS the installer works this out for you; Task Scheduler has
   no equivalent, so you type the hour.)
5. **Actions** tab: **New**, Program/script:

   ```
   wsl.exe
   ```

   Arguments:

   ```
   -d Ubuntu -- bash -lc "cd ~/merch-ads && ./run_scheduled.sh >> ~/merch-ads-nightly.log 2>&1"
   ```

   If you used a virtual environment in step 4, put the activate line in first:

   ```
   -d Ubuntu -- bash -lc "cd ~/merch-ads && source .venv/bin/activate && ./run_scheduled.sh >> ~/merch-ads-nightly.log 2>&1"
   ```

6. **Conditions** tab: untick **Start the task only if the computer is on AC
   power** if this is a laptop you want it to run on regardless.

Check it afterwards by reading the log:

```bash
tail -50 ~/merch-ads-nightly.log
```

**Your computer must be awake at that time.** This is the one real difference
from a Mac, where the same job can wake the machine. A desktop that stays on is
ideal. If yours sleeps, run the catch-up by hand when you next open it:

```bash
python3 engine/catchup.py
```

That asks every market for its reports, then collects in rounds until nothing is
pending. It exists precisely for missed nights.

### cron inside WSL (only if you keep WSL running)

```bash
sudo service cron start
crontab -e
```

Add:

```
0 3 * * * cd ~/merch-ads && ./run_scheduled.sh >> ~/merch-ads-nightly.log 2>&1
```

Cron does not start by itself in WSL unless systemd is enabled. To enable it,
put this in `/etc/wsl.conf` and then run `wsl --shutdown` from PowerShell:

```ini
[boot]
systemd=true
```

---

## Reading the numbers without the app

Every command answers with one JSON object. That is readable on its own, and
much easier with `jq`:

```bash
sudo apt install -y jq

python3 engine/appctl.py metrics | jq '.data.trailing30'
python3 engine/appctl.py killlist | jq '.data.count'
ADS_MARKET=UK python3 engine/appctl.py health | jq '.data.markets[].market'
```

[COMMANDS.md](COMMANDS.md) lists every command and marks which ones only read,
which ones call Amazon, and which ones write.

---

## Known differences from macOS

| | What happens |
|---|---|
| The Mac app | Not available. See [BUILD-A-UI.md](BUILD-A-UI.md). |
| Desktop notifications at the end of a run | Not sent. Read the log, or turn on the Discord digest — see [SETUP.md](SETUP.md). |
| Waking a sleeping machine for the nightly | Windows does not. Run `python3 engine/catchup.py` after a missed night. |
| `scripts/package_app.sh` and `scripts/install_launchd.sh` | macOS only. You do not need either. |

---

## If something breaks

Start with [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — almost nothing in it is
platform-specific.

Two things to check first that are specific to here:

**"It cannot find my export."** The engine looks in the folder *above* the
repository. Run `ls ~/*.csv` and confirm the file is there, not in
`~/merch-ads/`.

**Everything is very slow.** You put the repository on `/mnt/c/`. Move it to
`~/` and re-run.
