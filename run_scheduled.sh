#!/bin/bash
# Scheduled ads job — runs daily at 10:00 via launchd.
# Loops every market that has a profile in .env (US first, then UK/DE/FR/ES/IT).
# Per market: pull data -> map types -> (derive economics for non-US) -> dry-run ->
# harvest -> AUTO-APPLY safe actions (unless KILL) -> per-market Discord digest.
# The Discord digest is skipped while the NO_DISCORD file exists (delete it to
# turn the daily alerts back on).
# US-only extras: TAMAS, TRAZ, dashboard, MerchPirate demand feed.

# Resolve the engine folder from this script's own location, so the job works
# wherever the repo is cloned (launchd gives us no useful working directory).
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || exit 1
export PYTHONUNBUFFERED=1        # flush python output live so the log shows progress
caffeinate -i -w $$ &           # keep the Mac awake for the duration of this run
mkdir -p outputs
LOG="outputs/scheduled_runs.log"
# rotate once past ~10MB — append-only since June, already 4MB. One generation
# of history is enough; .1 gets overwritten on the next rotation.
if [ -f "$LOG" ] && [ "$(stat -f%z "$LOG" 2>/dev/null || echo 0)" -gt 10485760 ]; then
  mv "$LOG" "$LOG.1"
fi
STATUS_FILE="outputs/last_run_status.json"
RUN_STARTED="$(date '+%Y-%m-%dT%H:%M:%S')"
FAILURES=""

# Run one phase, remember a non-zero exit, never abort the run. The loop has
# always continued past a crashed phase on purpose (one market's failure must
# not strand the other five) — but until now a crash left no trace outside
# this log, and with Discord off nobody heard about it. Every failure now
# lands in $STATUS_FILE (the app's System Health reads it) and in the final
# macOS notification.  Labels must have no spaces or colons.
step() {
  _label="$1"; shift
  "$@"
  _rc=$?
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
# Override with ADS_PYTHON=/path/to/python3. Falls back to PATH python3 when the
# chosen interpreter is missing or lacks requests, so the nightly never dies here.
PY="${ADS_PYTHON:-/opt/homebrew/bin/python3}"
if ! "$PY" -c "import requests" >/dev/null 2>&1; then
  echo "WARNING: $PY unusable (missing, or no requests) — falling back to PATH python3" >&2
  PY="$(command -v python3)"
fi

# markets with a configured Ads profile (falls back to US)
MARKETS=$("$PY" -c "import ads_client,markets;print(' '.join(markets.available(ads_client.load_env())))" 2>/dev/null)
[ -z "$MARKETS" ] && MARKETS="US"

{
  echo ""
  echo "================ $(date '+%Y-%m-%d %H:%M')  | markets: $MARKETS ================"
  echo "python: $PY  ($("$PY" -c 'import sys,sqlite3;print(sys.version.split()[0], "| sqlite", sqlite3.sqlite_version)'))"
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

    step phase0_pull "$PY" phase0_pull.py
    [ "$M" != "US" ] && step derive_econ "$PY" derive_econ.py   # derive economics first (US = hardcoded)
    step map_products "$PY" map_products.py

    step phase1_dryrun "$PY" phase1_dryrun.py
    step harvest "$PY" harvest.py

    # economics freshness gate (US only): stale/unmapped export -> NO writes
    # for this market this run (reads/pull above already happened; other
    # markets are unaffected). See PLAN.md / appctl econ-gate.
    ECON_OK=1
    if [ "$M" = "US" ]; then
      "$PY" appctl.py econ-gate 2>/dev/null | "$PY" -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get('data',{}).get('ok') else 1)" || ECON_OK=0
      [ "$ECON_OK" = "0" ] && echo "*** ECON GATE CLOSED for US — skipping ALL auto-apply stages (run 'python3 appctl.py econ-gate' for reasons) ***"
    fi

    if [ -f KILL ]; then
      echo "KILL switch ON — skipping auto-apply for $M."
    elif [ "$ECON_OK" = "0" ]; then
      echo "ECON GATE CLOSED — previews only for $M this run."
    else
      if [ -f REQUIRE_APPROVAL ]; then
        echo "APPROVAL mode ON — phase2 collects only; approve negatives/pauses in the app."
        step phase2_preview "$PY" phase2_apply.py   # preview only, no writes
      else
        step phase2_apply "$PY" phase2_apply.py --apply --auto  # reactive negatives (10 clicks / ACOS>30%) + pauses
      fi
      step preempt_negatives "$PY" preempt_negatives.py --apply --auto  # preemptive wrong-format negatives (lottery/scavenger)
      step phase4_harvest "$PY" phase4_harvest_create.py --apply --auto # promote keyword winners
      step phase4b_asins "$PY" phase4b_harvest_asins.py --apply --auto  # promote ASIN winners
      if [ -f REQUIRE_APPROVAL ]; then
        step harvest_prune_collect "$PY" harvest_prune.py  # collect only — approve keyword pauses in the app
      else
        step harvest_prune "$PY" harvest_prune.py --apply --auto  # pause wasteful Harvested-Exact keywords (per-keyword)
      fi
      step phase3_bids "$PY" phase3_bids.py --apply --auto  # bids (per-type, market economics)
      [ "$M" = "US" ] && step tamas_optimize "$PY" tamas_optimize.py --apply --auto   # TAMAS = US only
      step lottery_build "$PY" lottery_build.py --apply --auto     # lottery: fill 'Lotto N' to cap, then create next (US window = last 60d uploads)
      step scavenger_build "$PY" scavenger_build.py --apply --auto # scavenger: add new ASINs/keywords
      step scavenger_optimize "$PY" scavenger_optimize.py --apply --auto  # scavenger: prune + retire dead
      step seasonal_pause "$PY" seasonal_pause.py --apply --auto   # pause tagged seasonal designs out of window, re-enable in window (no-op until designs are tagged)
      step rules_nightly "$PY" appctl.py rules-nightly </dev/null  # operator-authored DSL rules (enabled + in-season + auto); self-gates KILL/econ/cap; no-op until rules exist
    fi

    step daily_metrics "$PY" daily_metrics.py   # daily + month-to-date spend/ACOS (this market)
    [ "$(date +%u)" = "1" ] && step backfill_daily "$PY" backfill_daily.py   # Mondays: true-up ~90d of daily history (30d attribution)
    [ "$(date +%u)" = "1" ] && step backfill_target_daily "$PY" backfill_target_daily.py --days 35   # Mondays: true-up per-target daily history (30d attribution)
    step dashboard "$PY" dashboard.py           # per-market dashboard (dashboard_<M>.html; US = dashboard.html)
    step demand_feed "$PY" demand_feed.py       # per-market MerchPirate demand feed (demand_feed_<M>.json)
    if [ "$M" = "US" ]; then
      step weekly_report "$PY" weekly_report.py
      step traz "$PY" traz.py                   # TRAZ/EPC (TAMAS, US)
    fi
    if [ -f NO_DISCORD ]; then
      echo "NO_DISCORD file present — skipping the Discord digest for $M."
    else
      step notify_discord "$PY" notify_discord.py   # per-market Discord digest
    fi
  done
  unset ADS_MARKET

  # ---- KDP (books) — a separate advertiser profile; only pull + metrics + rules
  # (NO tee-specific phases: no lottery/scavenger/tamas/phase2-3). No-op until the
  # KDP profile id is in .env. The engine checks .env presence (this script never
  # reads it). Book economics come from kdp_books.json (appctl kdp-book). ----
  if "$PY" -c "import ads_client,markets,sys; sys.exit(0 if 'USKDP' in markets.available(ads_client.load_env()) else 1)" 2>/dev/null; then
    export ADS_MARKET=USKDP
    step phase0_pull "$PY" phase0_pull.py
    step daily_metrics "$PY" daily_metrics.py
    if [ ! -f KILL ] && [ ! -f REQUIRE_APPROVAL ]; then
      step rules_nightly "$PY" appctl.py rules-nightly </dev/null   # KDP automation rules (self-gates)
    fi
    if [ -f NO_DISCORD ]; then
      echo "NO_DISCORD file present — skipping the KDP Discord digest."
    else
      step notify_discord "$PY" notify_discord.py
    fi
    unset ADS_MARKET
  fi

  # bank per-ASIN economics from the newest catalog export BEFORE pruning old
  # ones (export_snapshot.py skips in one query when the file is already banked)
  step export_snapshot "$PY" export_snapshot.py --auto
  # keep only the newest catalog export (they're ~2GB each; 7 scripts read the newest)
  ls "$HOME/Biznis/ClaudeCode/POD"/export_products_*.csv 2>/dev/null | sort | sed '$d' | xargs rm -f 2>/dev/null

  # ---- machine-readable run status (System Health reads this) ----
  "$PY" - "$RUN_STARTED" "$MARKETS" $FAILURES > "$STATUS_FILE" <<'PYEOF'
import datetime, json, sys
started, markets = sys.argv[1], sys.argv[2]
failures = []
for item in sys.argv[3:]:
    market, label, rc = item.split(":")
    failures.append({"market": market, "step": label, "exit": int(rc)})
print(json.dumps({
    "started": started,
    "finished": datetime.datetime.now().isoformat(timespec="seconds"),
    "markets": markets.split(),
    "ok": not failures,
    "failures": failures,
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
# a failed step outranks everything else in the notification
set -- $FAILURES
if [ "$#" -gt 0 ]; then
  MSG="$# step(s) FAILED:$FAILURES · $MSG"
  TITLE="Merch Ads · RUN FAILED"
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
echo "done: $MSG" >> "$LOG"
