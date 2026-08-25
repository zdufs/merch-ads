#!/bin/bash
# Install (or reinstall) the nightly run as a launchd job for the CURRENT user.
#
#   bash scripts/install_launchd.sh            # install / reinstall, 01:00 Seattle
#   bash scripts/install_launchd.sh --hour 14  # override with your own local hour
#   bash scripts/install_launchd.sh --app      # run the copy inside Merch Ads.app
#   bash scripts/install_launchd.sh --data DIR # databases live somewhere else
#   bash scripts/install_launchd.sh --uninstall
#
# WHICH HOUR. The default is 01:00 MERCH TIME — Seattle, America/Los_Angeles —
# translated into whatever that is on this computer's clock. It is computed, not
# typed, so it is right wherever you live.
#
# Seattle is the right clock for every marketplace, not just the US.
# daily_metrics.py anchors "yesterday" and the month boundary to Pacific time
# for EVERY market, so that Amazon's ad calendar means the same thing no matter
# where the job runs from. Scheduling against your own marketplace's midnight
# would drift away from the date the engine actually asks for.
#
# 01:00 is one hour after that day closes, and that is the point: the run asks
# Amazon for YESTERDAY, and daily_metrics.py resolves which date that is on the
# Seattle clock. At 01:00 there, yesterday is the day that just ended — finished,
# and ready to report. At 23:00 there the date has not rolled over yet, so
# "yesterday" still means the day before, one already banked; the job would
# re-ask for an old day every night and never collect the fresh one.
#
# Beyond that the exact minute does not matter. Amazon keeps re-attributing for
# days, so the freshest day or two is always incomplete, daily_metrics.py fills
# in any settled day it finds missing, and the rules refuse to act on stale
# evidence. What matters is that the computer is awake.
#
# --hour and --minute still override, and are your LOCAL clock.
#
# Two things get baked in: WHICH script runs, and WHERE the data is.
#
# By default both come from this checkout, which is how it has always worked.
# With --app the script comes from /Applications/Merch Ads.app instead, and the
# nightly then depends on nothing but the app and the data folder — no checkout,
# no Homebrew python, no pip install, because the bundle carries its own. The
# data folder never moves either way.
#
# Re-run this after moving the folder, and after --app if you ever delete the app.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$REPO/io.github.zdufs.merchads.plist.template"
LABEL="io.github.zdufs.merchads"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
APP_SCRIPT="/Applications/Merch Ads.app/Contents/Resources/run_scheduled.sh"
HOUR=""       # empty => computed below as 01:00 Seattle, in local time
MINUTE=""
UNINSTALL=0
USE_APP=0
DATA=""

while [ $# -gt 0 ]; do
  case "$1" in
    --hour)      HOUR="$2"; shift 2 ;;
    --minute)    MINUTE="$2"; shift 2 ;;
    --app)       USE_APP=1; shift ;;
    --data)      DATA="$2"; shift 2 ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help)   sed -n '2,26p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *)           echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

# 01:00 Seattle, expressed on this machine's clock.
#
# launchd only understands local time, so the conversion has to happen here. It
# uses the system tz database through `date`, which needs no python and gets DST
# right on both sides. Half-hour zones work too, which is why the minute is
# computed rather than assumed to be zero.
#
# One honest limit: this is fixed at INSTALL time. The US and Europe change
# their clocks on different dates, so for a couple of weeks each spring and
# autumn the job runs an hour away from 01:00 Seattle. That is well inside the
# slack described above. Re-run this script if you would rather it were exact.
_offset_minutes() {                      # "+0200" -> 120,  "-0730" -> -450
  local z="$1" sign="${1:0:1}" h="${1:1:2}" m="${1:3:2}" total
  total=$((10#$h * 60 + 10#$m))
  [ "$sign" = "-" ] && total=$((-total))
  echo "$total"
}

if [ -z "$HOUR" ] || [ -z "$MINUTE" ]; then
  # Refuse anything that is not a numeric offset. A `date` that answers "CEST"
  # would otherwise be parsed into a plausible-looking wrong hour, and a wrong
  # schedule is silent — the job simply runs at a time nobody chose.
  _lz=$(date +%z); _pz=$(TZ=America/Los_Angeles date +%z)
  case "$_lz$_pz" in
    [+-][0-9][0-9][0-9][0-9][+-][0-9][0-9][0-9][0-9]) ;;
    *) echo "Cannot read a numeric UTC offset from date (+%z gave '$_lz' / '$_pz'); pass --hour yourself." >&2; exit 1 ;;
  esac
  _local=$(_offset_minutes "$_lz")
  _pac=$(_offset_minutes "$_pz")
  if [ -n "$_local" ] && [ -n "$_pac" ]; then
    _mins=$(( 60 + _local - _pac ))
    _mins=$(( (_mins % 1440 + 1440) % 1440 ))
    HOUR="${HOUR:-$((_mins / 60))}"
    MINUTE="${MINUTE:-$((_mins % 60))}"
  else
    echo "Could not read this machine's timezone; pass --hour yourself." >&2
    exit 1
  fi
  printf '==> 01:00 Seattle is %02d:%02d here — scheduling for that.\n' "$HOUR" "$MINUTE"
fi

# The data folder defaults to this checkout — that is where the databases are.
DATA="${DATA:-$REPO}"
DATA="$(cd "$DATA" 2>/dev/null && pwd)" || { echo "--data folder does not exist" >&2; exit 1; }

unload() {
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || launchctl unload "$TARGET" 2>/dev/null || true
}

if [ "$UNINSTALL" = "1" ]; then
  unload
  rm -f "$TARGET"
  echo "Removed $LABEL. The nightly run will not fire again."
  exit 0
fi

[ -f "$TEMPLATE" ] || { echo "Missing $TEMPLATE" >&2; exit 1; }

if [ "$USE_APP" = "1" ]; then
  # Fail here rather than install a job that cannot start. launchd would just
  # log a spawn error nobody reads, and the nightly would go quiet.
  [ -f "$APP_SCRIPT" ] || {
    echo "No nightly script inside the app: $APP_SCRIPT" >&2
    echo "Install a current build first:  bash scripts/package_app.sh --install" >&2
    exit 1
  }
  SCRIPT="$APP_SCRIPT"
else
  SCRIPT="$REPO/run_scheduled.sh"
  [ -x "$SCRIPT" ] || chmod +x "$SCRIPT"
fi

mkdir -p "$HOME/Library/LaunchAgents" "$DATA/outputs"
sed -e "s#__ADS_SCRIPT__#$SCRIPT#g" \
    -e "s#__ADS_DATA__#$DATA#g" \
    -e "s#<key>Hour</key>[[:space:]]*<integer>[0-9]*</integer>#<key>Hour</key><integer>$HOUR</integer>#" \
    -e "s#<key>Minute</key>[[:space:]]*<integer>[0-9]*</integer>#<key>Minute</key><integer>$MINUTE</integer>#" \
    "$TEMPLATE" > "$TARGET"

# The sed above only rewrites Hour/Minute when they sit on one line; normalise
# with PlistBuddy so the schedule is correct either way.
/usr/libexec/PlistBuddy -c "Set :StartCalendarInterval:Hour $HOUR" "$TARGET" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Set :StartCalendarInterval:Minute $MINUTE" "$TARGET" 2>/dev/null || true
plutil -lint "$TARGET" >/dev/null

unload
launchctl bootstrap "gui/$(id -u)" "$TARGET" 2>/dev/null || launchctl load "$TARGET"

printf 'Installed %s\n  script: %s\n  data:   %s\n  runs:   daily at %02d:%02d\n  logs:   %s/outputs/launchd.{out,err}.log\n' \
  "$LABEL" "$SCRIPT" "$DATA" "$HOUR" "$MINUTE" "$DATA"
echo
echo "Run it once right now with:  launchctl kickstart gui/$(id -u)/$LABEL"
