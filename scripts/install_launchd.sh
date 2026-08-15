#!/bin/bash
# Install (or reinstall) the nightly run as a launchd job for the CURRENT user.
#
#   bash scripts/install_launchd.sh            # install / reinstall, default 10:00
#   bash scripts/install_launchd.sh --hour 7   # run at 07:00 instead
#   bash scripts/install_launchd.sh --uninstall
#
# The repo path is baked in at install time from wherever this script lives, so
# the job keeps working no matter where you cloned the repo. Re-run it after you
# move the folder.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$REPO/io.github.zdufs.merchads.plist.template"
LABEL="io.github.zdufs.merchads"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
HOUR=10
MINUTE=0
UNINSTALL=0

while [ $# -gt 0 ]; do
  case "$1" in
    --hour)      HOUR="$2"; shift 2 ;;
    --minute)    MINUTE="$2"; shift 2 ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help)   sed -n '2,12p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *)           echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

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
[ -x "$REPO/run_scheduled.sh" ] || chmod +x "$REPO/run_scheduled.sh"

mkdir -p "$HOME/Library/LaunchAgents" "$REPO/outputs"
sed -e "s#__ADS_REPO__#$REPO#g" \
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

printf 'Installed %s\n  repo:  %s\n  runs:  daily at %02d:%02d\n  logs:  %s/outputs/launchd.{out,err}.log\n' \
  "$LABEL" "$REPO" "$HOUR" "$MINUTE" "$REPO"
echo
echo "Run it once right now with:  launchctl kickstart gui/$(id -u)/$LABEL"
