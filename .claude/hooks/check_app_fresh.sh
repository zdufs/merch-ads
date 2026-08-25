#!/bin/bash
# Stop-hook backstop for the standing rule: the /Applications copy of Merch Ads
# must always be the latest build. If the app source changed since the app in
# /Applications was last installed, block turn-end and tell Claude to repackage.
# Exit 0 = fine (let the turn end). Exit 2 = block, stderr shown to the model.
#
# Freshness is judged by CONTENT, not timestamps. package_app.sh --install stamps
# a content hash of the source into the bundle (Contents/Resources/.src_manifest);
# here we recompute that hash and compare. This is deliberate: git commit / merge /
# checkout rewrite working-tree file mtimes to "now" without changing a byte, and
# the old mtime comparison flagged a freshly-installed app as stale every time
# (twice in one session on 2026-08-14), forcing a pointless rebuild.
REPO="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
SRC="$REPO/MerchAds"
APP="/Applications/Merch Ads.app"
MANIFEST="$APP/Contents/Resources/.src_manifest"
HASHER="$REPO/.claude/hooks/app_src_hash.sh"
[ -d "$SRC" ] || exit 0    # not this project / no app sources

# The nightly runs FROM the bundle, so installing mid-run kills the step in
# flight — that is what package_app.sh refuses to do since 2026-08-24, after an
# install at 12:57 killed three DE steps and lost that market's Monday
# attribution true-up.
#
# So while a run is in progress this hook and that guard would contradict each
# other: the hook demands an install, the guard refuses it, and the turn cannot
# end for the several hours the run takes. Blocking on an instruction that
# cannot be followed teaches everyone to ignore the block.
#
# Report it and step aside. The rule is not weakened: the run ends, and the
# very next turn blocks again with nothing changed.
if pgrep -f "run_scheduled.sh" >/dev/null 2>&1; then
  if [ -f "$MANIFEST" ] && [ -x "$HASHER" ]; then
    _cur=$(CLAUDE_PROJECT_DIR="$REPO" "$HASHER" 2>/dev/null)
    _got=$(cat "$MANIFEST" 2>/dev/null)
    if [ -n "$_cur" ] && [ "$_cur" != "$_got" ]; then
      echo "NOTE: /Applications is stale, and the nightly is running — installing now would kill the step in flight. Package and relaunch as soon as it finishes; this hook blocks again on the next turn." >&2
    fi
  fi
  exit 0
fi

if [ ! -d "$APP" ]; then
  echo "BLOCK: '/Applications/Merch Ads.app' is missing. Run: bash scripts/package_app.sh --install  then relaunch from /Applications before ending the turn." >&2
  exit 2
fi

# Preferred path: compare the source content hash to what was installed.
if [ -f "$MANIFEST" ] && [ -x "$HASHER" ]; then
  current=$(CLAUDE_PROJECT_DIR="$REPO" "$HASHER" 2>/dev/null)
  installed=$(cat "$MANIFEST" 2>/dev/null)
  if [ -n "$current" ] && [ "$current" != "$installed" ]; then
    echo "BLOCK: app source changed since the /Applications copy was last installed — it is STALE. Run: bash scripts/package_app.sh --install  then 'pkill -x \"Merch Ads\"; open \"/Applications/Merch Ads.app\"'. (Standing rule: /Applications must always be the latest.)" >&2
    exit 2
  fi
  exit 0
fi

# Fallback (no manifest yet — installed before this guard existed): mtime compare.
# Reinstalling once writes the manifest and switches to the content check above.
newest=$(find "$SRC" -type f \( -name '*.swift' -o -name '*.plist' -o -name '*.entitlements' -o -name '*.xcassets' \) \
           -exec stat -f '%m' {} + 2>/dev/null | sort -nr | head -1)
[ -n "$newest" ] || exit 0
appm=$(stat -f '%m' "$APP" 2>/dev/null || echo 0)
if [ "$newest" -gt "$appm" ]; then
  echo "BLOCK: app source changed since the /Applications copy was last installed — it is STALE. Run: bash scripts/package_app.sh --install  then 'pkill -x \"Merch Ads\"; open \"/Applications/Merch Ads.app\"'. (Standing rule: /Applications must always be the latest.)" >&2
  exit 2
fi
exit 0
