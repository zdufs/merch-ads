#!/bin/bash
# Install (or reinstall) the HOURLY Marketing Stream pickup as a launchd job.
#
#   bash scripts/install_stream_drain.sh                 # every hour, from this checkout
#   bash scripts/install_stream_drain.sh --minutes 30    # every 30 minutes instead
#   bash scripts/install_stream_drain.sh --app           # run the copy inside Merch Ads.app
#   bash scripts/install_stream_drain.sh --data DIR      # databases live somewhere else
#   bash scripts/install_stream_drain.sh --uninstall
#
# This is the sibling of install_launchd.sh and follows the same two rules:
# WHICH script runs, and WHERE the data is. The drain touches no Amazon Ads
# account — it reads AWS and writes one local file — so it is safe to have
# running while the nightly is mid-phase.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$REPO/io.github.zdufs.merchads.stream.plist.template"
LABEL="io.github.zdufs.merchads.stream"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
APP_SCRIPT="/Applications/Merch Ads.app/Contents/Resources/run_stream_drain.sh"
MINUTES=60
UNINSTALL=0
USE_APP=0
DATA=""

while [ $# -gt 0 ]; do
  case "$1" in
    --minutes)   MINUTES="$2"; shift 2 ;;
    --app)       USE_APP=1; shift ;;
    --data)      DATA="$2"; shift 2 ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help)   sed -n '2,13p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *)           echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

DATA="${DATA:-$REPO}"
DATA="$(cd "$DATA" 2>/dev/null && pwd)" || { echo "--data folder does not exist" >&2; exit 1; }

unload() {
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || launchctl unload "$TARGET" 2>/dev/null || true
}

if [ "$UNINSTALL" = "1" ]; then
  unload
  rm -f "$TARGET"
  echo "Removed $LABEL. Stream messages will pile up in SQS instead — they are"
  echo "kept 14 days, so drain by hand before then or the oldest are lost."
  exit 0
fi

[ -f "$TEMPLATE" ] || { echo "Missing $TEMPLATE" >&2; exit 1; }

case "$MINUTES" in
  ''|*[!0-9]*) echo "--minutes must be a whole number" >&2; exit 2 ;;
esac
[ "$MINUTES" -ge 1 ] || { echo "--minutes must be at least 1" >&2; exit 2; }

if [ "$USE_APP" = "1" ]; then
  # Fail here rather than install a job that cannot start. launchd would log a
  # spawn error nobody reads and the queues would quietly fill.
  [ -f "$APP_SCRIPT" ] || {
    echo "No drain script inside the app: $APP_SCRIPT" >&2
    echo "Install a current build first:  bash scripts/package_app.sh --install" >&2
    exit 1
  }
  SCRIPT="$APP_SCRIPT"
else
  SCRIPT="$REPO/run_stream_drain.sh"
  [ -x "$SCRIPT" ] || chmod +x "$SCRIPT"
fi

mkdir -p "$HOME/Library/LaunchAgents" "$DATA/outputs"
sed -e "s#__ADS_SCRIPT__#$SCRIPT#g" \
    -e "s#__ADS_DATA__#$DATA#g" \
    "$TEMPLATE" > "$TARGET"

/usr/libexec/PlistBuddy -c "Set :StartInterval $((MINUTES * 60))" "$TARGET" >/dev/null
plutil -lint "$TARGET" >/dev/null

unload
launchctl bootstrap "gui/$(id -u)" "$TARGET" 2>/dev/null || launchctl load "$TARGET"

printf 'Installed %s\n  script: %s\n  data:   %s\n  runs:   every %s minute(s), and once now\n  log:    %s/outputs/stream_drain.log\n' \
  "$LABEL" "$SCRIPT" "$DATA" "$MINUTES" "$DATA"
echo
echo "Run it once right now with:  launchctl kickstart gui/$(id -u)/$LABEL"
