#!/bin/bash
# Scheduled ads job — runs daily at 10:00 via launchd.
# Loops every market that has a profile in .env (US first, then UK/DE/FR/ES/IT).
# Per market: pull data -> map types -> (derive economics for non-US) -> dry-run ->
# harvest -> AUTO-APPLY safe actions (unless KILL) -> per-market Discord digest.
# The Discord digest is skipped while the NO_DISCORD file exists (delete it to
# turn the daily alerts back on).
# US-only extras: TRAZ, dashboard, demand feed (they need the Merch sales report).

# ---- where the code is, and where the data is --------------------------------
# These used to be one folder, resolved from this script's own location, because
# launchd gives no useful working directory. They are two now: the Mac app ships
# the code inside its bundle (Contents/Resources), while the databases, .env and
# outputs/ stay in the operator's folder, where they have always been and where
# no app update can reach them.
#
#   SELF    this script's folder — holds the modules and, in a bundle, python
#   ENGINE  the Python modules
#   DATA    .env, ads_data*.sqlite, KILL, outputs/, seasonal.json
#
# A checkout leaves SELF and DATA equal, so everything behaves exactly as it did.
# In the bundle, MERCHADS_DATA_DIR names the data — set by the launchd job, and
# exported below so every python step resolves the same folder this script does.
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENGINE="$SELF/engine"
DATA="${MERCHADS_DATA_DIR:-$SELF}"
[ -f "$ENGINE/appctl.py" ] || { echo "run_scheduled: no engine at $ENGINE" >&2; exit 1; }
# Stop here rather than run a whole nightly against a folder that is not there
# and report success. Same reason the paths module fails closed.
[ -d "$DATA" ] || { echo "run_scheduled: data folder $DATA does not exist" >&2; exit 1; }
export MERCHADS_DATA_DIR="$DATA"
cd "$DATA" || exit 1
export PYTHONUNBUFFERED=1        # flush python output live so the log shows progress
export PYTHONDONTWRITEBYTECODE=1   # never write .pyc into a signed app bundle: it breaks the code signature
caffeinate -i -w $$ &           # keep the Mac awake for the duration of this run
mkdir -p outputs

# ---- one run at a time -------------------------------------------------------
# launchd fires at 10:00, and the app can start a full run from its own button,
# and a catch-up can be run by hand. Nothing stopped two of them overlapping.
# That matters because the builders LIST before they CREATE: two runs can each
# see a campaign as absent and both create it, while other phases apply bids and
# states underneath them.
#
# `mkdir` is the lock because it is atomic on every filesystem this runs on. A
# `[ -f ]` test followed by a `touch` is not: both runs can pass the test.
#
# A lock left behind by a crash or a reboot is taken over, but only after
# checking that the process named inside it is really gone. Refusing forever
# because of a stale directory would be its own silent failure.
#
# The pid is written into a temp file FIRST and moved in, so the lock gains its
# pid in one rename rather than over the life of a shell redirection. The move
# still cannot be part of the mkdir, so a second run can arrive in the sliver
# between them and find a directory with no pid inside. That used to read as
# "pid unknown is gone": the second run deleted the first run's lock, took its
# own, and both nightlies proceeded — the exact overlap the lock exists to
# stop, and afterwards the first run's EXIT trap removed the second's lock.
# A lock with no pid that is YOUNGER than the grace period is therefore treated
# as held. Only a pid-less lock older than that is stale. Found 2026-08-24.
LOCK="$DATA/outputs/run_scheduled.lock"
LOCK_PID_GRACE=60        # seconds; a run needs milliseconds to name itself
LOCK_PID_TMP="$DATA/outputs/.run_scheduled.pid.$$"
echo $$ > "$LOCK_PID_TMP"
if ! mkdir "$LOCK" 2>/dev/null; then
  holder="$(cat "$LOCK/pid" 2>/dev/null || echo "")"
  if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null; then
    rm -f "$LOCK_PID_TMP"
    echo "run_scheduled: a run is already in progress (pid $holder). Not starting a second one." >&2
    exit 75          # EX_TEMPFAIL: did not run, and that was on purpose
  fi
  if [ -z "$holder" ]; then
    lock_made="$(stat -f %m "$LOCK" 2>/dev/null || echo 0)"
    lock_age=$(( $(date +%s) - lock_made ))
    if [ "$lock_age" -lt "$LOCK_PID_GRACE" ]; then
      rm -f "$LOCK_PID_TMP"
      echo "run_scheduled: a run took the lock ${lock_age}s ago and has not written its pid yet. Not starting a second one." >&2
      exit 75
    fi
  fi
  echo "run_scheduled: clearing a stale lock (pid ${holder:-unknown} is gone)" >&2
  rm -f "$LOCK/pid"
  rmdir "$LOCK" 2>/dev/null
  mkdir "$LOCK" 2>/dev/null || {
    rm -f "$LOCK_PID_TMP"
    echo "run_scheduled: could not take the lock at $LOCK" >&2; exit 1; }
fi
mv "$LOCK_PID_TMP" "$LOCK/pid" 2>/dev/null || echo $$ > "$LOCK/pid"
trap 'rm -f "$LOCK/pid" "$LOCK_PID_TMP"; rmdir "$LOCK" 2>/dev/null' EXIT

LOG="outputs/scheduled_runs.log"
# rotate once past ~10MB — append-only since June, already 4MB. One generation
# of history is enough; .1 gets overwritten on the next rotation.
if [ -f "$LOG" ] && [ "$(stat -f%z "$LOG" 2>/dev/null || echo 0)" -gt 10485760 ]; then
  mv "$LOG" "$LOG.1"
fi
STATUS_FILE="outputs/last_run_status.json"
RUN_STARTED="$(date '+%Y-%m-%dT%H:%M:%S')"
FAILURES=""
# Markets that ran their reads but applied NOTHING, and why. A closed economics
# gate skips every auto-apply stage for a market — phase2, preempt, both harvest
# promoters, harvest prune, phase3, both builders, seasonal and the DSL rules —
# and it was not a step failure, so the status file said "ok" and the
# notification said "digest". The one place that said a whole market did no
# automation all night was a line in a 4 MB log.
# Entries are MARKET:reason, and reasons carry no spaces or colons.
GATED=""

# Run one phase, remember a non-zero exit, never abort the run. The loop has
# always continued past a crashed phase on purpose (one market's failure must
# not strand the other five) — but until now a crash left no trace outside
# this log, and with Discord off nobody heard about it. Every failure now
# lands in $STATUS_FILE (the app's System Health reads it) and in the final
# macOS notification.  Labels must have no spaces or colons.
# Every step is also TIMED. The run takes hours — 2h43m on 2026-08-23 — and
# until now the only two numbers recorded were the moment it started and the
# moment it finished. Nothing said which phase owned the time, so a phase that
# doubled would look exactly like a night that was busier, and there was no way
# to tell whether an optimisation helped. The catalogue cache is claimed to save
# about seven minutes a night; that claim could not be checked from this file.
#
# `date +%s` twice per step, appended to a string. No behaviour change, no
# writes, and nothing here can fail the run.
TIMINGS=""
step() {
  _label="$1"; shift
  _t0=$(date +%s)
  "$@"
  _rc=$?
  _secs=$(( $(date +%s) - _t0 ))
  TIMINGS="$TIMINGS ${ADS_MARKET:-global}:${_label}:${_secs}"
  if [ "$_rc" -ne 0 ]; then
    FAILURES="$FAILURES ${ADS_MARKET:-global}:${_label}:${_rc}"
    echo "*** STEP FAILED [${ADS_MARKET:-global}] ${_label} (exit ${_rc}) ***"
  fi
  return 0
}

# ---- interpreter -------------------------------------------------------------
# Every python step below runs under $PY.
# Default is Homebrew's python3 because it reports SQLite EXTENDED error codes
# (e.sqlite_errorname / e.sqlite_errorcode, python >= 3.11). Xcode's 3.9 does
# not, which is why five nights of "disk I/O error" in store_targeting_perf
# could not be diagnosed — the subcode says whether it was a failed write,
# fsync, unlink or lock, and those are different bugs.
# The engine's only third-party dependency is `requests` (checked below).
#
# When this script sits in the app bundle, the interpreter sits beside it with
# requests already installed, and it is preferred: the nightly then depends on
# nothing the operator has to keep installed — no Homebrew, no pip, no Command
# Line Tools. Otherwise Homebrew's python3, as before.
#
# Order: ADS_PYTHON (explicit override) -> the bundled one -> Homebrew -> PATH.
# Every fallback is announced, because a nightly quietly running under a
# different interpreter than the app is exactly the kind of split that took
# five nights to notice the last time.
BUNDLED_PY="$SELF/python/bin/python3"
if [ -n "${ADS_PYTHON:-}" ]; then
  PY="$ADS_PYTHON"
elif [ -x "$BUNDLED_PY" ]; then
  PY="$BUNDLED_PY"
else
  PY="/opt/homebrew/bin/python3"
fi
if ! "$PY" -c "import requests" >/dev/null 2>&1; then
  echo "WARNING: $PY unusable (missing, or no requests) — falling back to PATH python3" >&2
  PY="$(command -v python3)"
fi

# The engine modules live in engine/ (moved 2026-08-15). Every script below is
# launched by path, but the short `-c` snippets IMPORT the engine instead, and
# the repo root is not on sys.path. Put engine/ on the path once, for every
# python in this run. Without it those imports fail and the market list below
# falls back to US — which is exactly what happened for five nights.
export PYTHONPATH="$ENGINE${PYTHONPATH:+:$PYTHONPATH}"

# markets with a configured Ads profile (falls back to US)
MARKETS=$("$PY" -c "import ads_client,markets;print(' '.join(markets.available(ads_client.load_env())))" 2>>"$LOG")
DISCOVERY_FAILED=""
if [ -z "$MARKETS" ]; then
  # The fallback used to be silent. When the engine moved into engine/ the
  # import broke, the run went US-only, and the status file still said
  # "all steps OK" for five nights. A failed discovery is now a reported
  # failure: it lands in the status file, System Health and the notification.
  MARKETS="US"
  FAILURES="$FAILURES global:market_discovery:1"
  DISCOVERY_FAILED=1
fi

{
  echo ""
  echo "================ $(date '+%Y-%m-%d %H:%M')  | markets: $MARKETS ================"
  [ -n "$DISCOVERY_FAILED" ] && echo "*** STEP FAILED [global] market_discovery (exit 1) — could not list the configured markets, running US only ***"
  echo "python: $PY  ($("$PY" -c 'import sys,sqlite3;print(sys.version.split()[0], "| sqlite", sqlite3.sqlite_version)'))"
  # The product catalogue is several CSV chunks merged at read time, and the
  # per-market steps below perform that merge about twenty times — roughly seven
  # minutes a night re-parsing 1.1 GB that did not change. Bank it once, here.
  # It is a pure optimisation: `--auto` is a single query when the exports have
  # not moved, and every reader falls back to the CSVs if this fails.
  step catalog_cache "$PY" "$ENGINE"/catalog_cache.py --auto

  for M in $MARKETS; do
    echo ""
    echo "################  MARKET $M  ################"
    export ADS_MARKET="$M"

    # KDP markets (kind=kdp) run in a dedicated block below — pull + metrics +
    # rules only. They must never run the tee pipeline here: lottery, scavenger,
    # phase2/3 and preempt_negatives all target the shared t-shirt catalogue, so a
    # KDP pass builds book ASINs as t-shirt campaigns. The merch builders now
    # refuse a KDP profile (exit 1) — correct, but it was counted as a nightly
    # STEP FAILED every night, and the merch loop also pulled USKDP a second time.
    # Skip KDP here so the merch loop stays merch-only and the run reports ok.
    if "$PY" -c "import markets,sys; sys.exit(0 if markets.is_kdp('$M') else 1)"; then
      echo "[$M] KDP profile — handled by the KDP block below (no tee phases). Skipping merch loop."
      continue
    fi

    step phase0_pull "$PY" "$ENGINE"/phase0_pull.py
    [ "$M" != "US" ] && step derive_econ "$PY" "$ENGINE"/derive_econ.py   # derive economics first (US = hardcoded)
    step map_products "$PY" "$ENGINE"/map_products.py

    step phase1_dryrun "$PY" "$ENGINE"/phase1_dryrun.py
    step harvest "$PY" "$ENGINE"/harvest.py

    # Economics freshness gate: stale or unmapped economics -> NO writes for
    # this market this run. Reads and the pull above already happened, and the
    # other markets are unaffected. See appctl econ-gate.
    #
    # EVERY market, not just US. This ran `if [ "$M" = "US" ]`, so the five EU
    # markets and KDP kept auto-applying whatever the state of their economics,
    # while `appctl econ-gate` is market-aware and `derive_econ` runs for each
    # of them a few lines above. Checked on 2026-08-24 before this changed: the
    # gate is open in all seven markets, so this refuses nothing today and
    # closes the hole for the night it matters.
    ECON_OK=1
    "$PY" "$ENGINE"/appctl.py econ-gate 2>/dev/null | "$PY" -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get('data',{}).get('ok') else 1)" || ECON_OK=0
    # The hint deliberately does NOT spell out a path. Written as a runnable
    # command line it contains `engine/appctl.py`, and tests/nightly_paths_tests
    # reads that as a script being launched relative to the data folder — a lint
    # failing on its own help text, which this repo has done before.
    if [ "$ECON_OK" = "0" ]; then
      echo "*** ECON GATE CLOSED for $M — skipping ALL auto-apply stages (ask appctl econ-gate under ADS_MARKET=$M for the reasons) ***"
      GATED="$GATED ${M}:econ_gate"
    fi

    if [ -f KILL ]; then
      echo "KILL switch ON — skipping auto-apply for $M."
    elif [ "$ECON_OK" = "0" ]; then
      echo "ECON GATE CLOSED — previews only for $M this run."
    else
      if [ -f REQUIRE_APPROVAL ]; then
        echo "APPROVAL mode ON — phase2 collects only; approve negatives/pauses in the app."
        step phase2_preview "$PY" "$ENGINE"/phase2_apply.py   # preview only, no writes
      else
        step phase2_apply "$PY" "$ENGINE"/phase2_apply.py --apply --auto  # reactive negatives (10 clicks / ACOS>30%) + pauses
      fi
      step preempt_negatives "$PY" "$ENGINE"/preempt_negatives.py --apply --auto  # preemptive wrong-format negatives (lottery/scavenger)
      step phase4_harvest "$PY" "$ENGINE"/phase4_harvest_create.py --apply --auto # promote keyword winners
      step phase4b_asins "$PY" "$ENGINE"/phase4b_harvest_asins.py --apply --auto  # promote ASIN winners
      if [ -f REQUIRE_APPROVAL ]; then
        step harvest_prune_collect "$PY" "$ENGINE"/harvest_prune.py  # collect only — approve keyword pauses in the app
      else
        step harvest_prune "$PY" "$ENGINE"/harvest_prune.py --apply --auto  # pause wasteful Harvested-Exact keywords (per-keyword)
      fi
      step phase3_bids "$PY" "$ENGINE"/phase3_bids.py --apply --auto  # bids (per-type, market economics)
      step lottery_build "$PY" "$ENGINE"/lottery_build.py --apply --auto     # lottery: fill 'Lotto N' to cap, then create next (US window = last 60d uploads)
      step scavenger_build "$PY" "$ENGINE"/scavenger_build.py --apply --auto # scavenger: add new ASINs/keywords
      step scavenger_optimize "$PY" "$ENGINE"/scavenger_optimize.py --apply --auto  # scavenger: prune + retire dead
      step seasonal_pause "$PY" "$ENGINE"/seasonal_pause.py --apply --auto   # pause tagged seasonal designs out of window, re-enable in window (no-op until designs are tagged)
      step rules_nightly "$PY" "$ENGINE"/appctl.py rules-nightly </dev/null  # operator-authored DSL rules (enabled + in-season + auto); self-gates KILL/econ/cap; no-op until rules exist
    fi

    step daily_metrics "$PY" "$ENGINE"/daily_metrics.py   # daily + month-to-date spend/ACOS (this market)
    [ "$(date +%u)" = "1" ] && step backfill_daily "$PY" "$ENGINE"/backfill_daily.py   # Mondays: true-up ~90d of daily history (30d attribution)
    [ "$(date +%u)" = "1" ] && step backfill_target_daily "$PY" "$ENGINE"/backfill_target_daily.py --days 35   # Mondays: true-up per-target daily history (30d attribution)
    # Mondays: cap the perf snapshots. Each pull adds a row per entity per table
    # and nothing had ever deleted one — US targeting_perf was 2.0M rows on
    # 2026-08-22. The window (db.SNAPSHOT_RETENTION_DAYS, 400 days) is far past
    # anything on disk, so this logs 0 until the account is over a year old; it
    # bounds the future rather than reclaiming the present. Weekly is plenty for
    # something that removes at most one day's rows per day.
    [ "$(date +%u)" = "1" ] && step prune_snapshots "$PY" "$ENGINE"/appctl.py prune-snapshots --apply </dev/null
    step dashboard "$PY" "$ENGINE"/dashboard.py           # per-market dashboard (dashboard_<M>.html; US = dashboard.html)
    step demand_feed "$PY" "$ENGINE"/demand_feed.py       # per-market MerchPirate demand feed (demand_feed_<M>.json)
    if [ "$M" = "US" ]; then
      step weekly_report "$PY" "$ENGINE"/weekly_report.py
      step traz "$PY" "$ENGINE"/traz.py                   # TRAZ/EPC — royalty vs ad spend (US)
    fi
    if [ -f NO_DISCORD ]; then
      echo "NO_DISCORD file present — skipping the Discord digest for $M."
    else
      step notify_discord "$PY" "$ENGINE"/notify_discord.py   # per-market Discord digest
    fi
  done
  unset ADS_MARKET

  # ---- KDP (books) — a separate advertiser profile; only pull + metrics + rules
  # (NO tee-specific phases: no lottery/scavenger/phase2-3). No-op until the
  # KDP profile id is in .env. The engine checks .env presence (this script never
  # reads it). Book economics come from kdp_books.json (appctl kdp-book). ----
  if "$PY" -c "import ads_client,markets,sys; sys.exit(0 if 'USKDP' in markets.available(ads_client.load_env()) else 1)" 2>/dev/null; then
    export ADS_MARKET=USKDP
    step phase0_pull "$PY" "$ENGINE"/phase0_pull.py
    step daily_metrics "$PY" "$ENGINE"/daily_metrics.py
    if [ ! -f KILL ] && [ ! -f REQUIRE_APPROVAL ]; then
      step rules_nightly "$PY" "$ENGINE"/appctl.py rules-nightly </dev/null   # KDP automation rules (self-gates)
    fi
    if [ -f NO_DISCORD ]; then
      echo "NO_DISCORD file present — skipping the KDP Discord digest."
    else
      step notify_discord "$PY" "$ENGINE"/notify_discord.py
    fi
    unset ADS_MARKET
  fi

  # bank per-ASIN economics from every unbanked catalog file BEFORE pruning old
  # ones (export_snapshot.py skips in one query per file already banked)
  step export_snapshot "$PY" "$ENGINE"/export_snapshot.py --auto
  # Keep only the newest MerchFlow export (they are ~2GB each and each one is a
  # WHOLE catalog, so an older one carries nothing the newer lacks).
  # Snap for MOD files are NOT pruned: each is a chunk of at most 100k rows and
  # the catalog is the merge of them all, so deleting older chunks would delete
  # coverage. Prune those by hand once a refresh has replaced them.
  ls "$HOME/Biznis/ClaudeCode/POD"/export_products_*.csv 2>/dev/null | sort | sed '$d' | xargs rm -f 2>/dev/null

  # ---- machine-readable run status (System Health reads this) ----
  # Four fixed arguments, not varargs: FAILURES and TIMINGS are two lists and
  # a flat argv could not tell where one ended. Labels carry no spaces or
  # colons (see step() above), so joining them is unambiguous.
  "$PY" - "$RUN_STARTED" "$MARKETS" "$FAILURES" "$TIMINGS" "$GATED" > "$STATUS_FILE" <<'PYEOF'
import datetime, json, sys

started, markets = sys.argv[1], sys.argv[2]
raw_failures = sys.argv[3] if len(sys.argv) > 3 else ""
raw_timings = sys.argv[4] if len(sys.argv) > 4 else ""
raw_gated = sys.argv[5] if len(sys.argv) > 5 else ""

failures = []
for item in raw_failures.split():
    market, label, rc = item.split(":")
    failures.append({"market": market, "step": label, "exit": int(rc)})

# Every step's wall time. The run takes hours and nothing recorded where they
# went, so a phase that doubled read as a busier night and no optimisation
# could be checked. Kept whole rather than summarised: about a hundred small
# rows, read once, and the shape a future question has not been asked yet.
steps = []
for item in raw_timings.split():
    market, label, secs = item.split(":")
    steps.append({"market": market, "step": label, "seconds": int(secs)})
steps.sort(key=lambda s: -s["seconds"])

# A market that applied nothing. Kept apart from `failures` on purpose: nothing
# crashed, so calling it a failure would put a RUN FAILED notification on a run
# that did exactly what its own gate told it to. It still has to be visible —
# a market can sit gated for weeks and every other number on the screen looks
# healthy.
gated = []
for item in raw_gated.split():
    market, reason = item.split(":")
    gated.append({"market": market, "reason": reason})

print(json.dumps({
    "started": started,
    "finished": datetime.datetime.now().isoformat(timespec="seconds"),
    "markets": markets.split(),
    "ok": not failures,
    "failures": failures,
    "gated": gated,
    # Sorted slowest first, so reading the first row answers "what owns the
    # night" without the reader doing arithmetic.
    "steps": steps,
    "total_step_seconds": sum(s["seconds"] for s in steps),
}, indent=1))
PYEOF
} >> "$LOG" 2>&1

# ---- one macOS summary (per-market detail is in the Discord digests) ----
if [ -f KILL ]; then
  MSG="KILL ON · previews only · markets: $MARKETS"
elif [ -f NO_DISCORD ]; then
  MSG="ran markets: $MARKETS · Discord digests off"
else
  MSG="ran markets: $MARKETS · see Discord for per-market digests"
fi
# A market that applied nothing is named too. It is not a failure — nothing
# crashed — but a night where a whole market wrote nothing must not read as a
# clean digest.
set -- $GATED
if [ "$#" -gt 0 ]; then
  MSG="$# market(s) GATED (no auto-apply):$GATED · $MSG"
fi
# a failed step outranks everything else in the notification
set -- $FAILURES
if [ "$#" -gt 0 ]; then
  MSG="$# step(s) FAILED:$FAILURES · $MSG"
  TITLE="Merch Ads · RUN FAILED"
  SOUND="Basso"
elif [ -n "$GATED" ]; then
  TITLE="Merch Ads · digest (gated)"
  SOUND="Basso"
else
  TITLE="Merch Ads · digest"
  SOUND="Glass"
fi
if command -v terminal-notifier >/dev/null 2>&1; then
  terminal-notifier -title "$TITLE" -message "$MSG" -sound "$SOUND"
else
  osascript -e "display notification \"$MSG\" with title \"$TITLE\" sound name \"$SOUND\"" 2>/dev/null
fi
# To the log AND to stdout. Everything above runs inside a block redirected
# into the log, so a caller — `appctl run`, the app's full-run button — saw an
# empty output pane whatever happened.
echo "done: $MSG" | tee -a "$LOG"

# The exit status of this script used to be the exit status of that echo, which
# is 0 forever. Every failed step was already counted; nothing carried the count
# out to whoever started the run.
set -- $FAILURES
[ "$#" -gt 0 ] && exit 1
exit 0
