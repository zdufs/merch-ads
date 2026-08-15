#!/bin/bash
# Prints a single content hash of the MerchAds app source: every *.swift,
# *.plist, *.entitlements and *.icns file, plus every file inside an *.xcassets
# bundle.
#
# Used by two places, so they always agree on what "the source" is:
#   - scripts/package_app.sh --install stamps this hash into the installed bundle.
#   - .claude/hooks/check_app_fresh.sh compares the current hash to that stamp.
#
# It hashes CONTENT, not timestamps, on purpose. git commit / merge / checkout
# rewrite working-tree file mtimes to "now" without changing a byte, which made
# the old mtime-based freshness check flag a freshly-installed app as stale.
REPO="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
SRC="${1:-$REPO/MerchAds}"
{ find "$SRC" -type f \( -name '*.swift' -o -name '*.plist' -o -name '*.entitlements' -o -name '*.icns' \) -print0
  find "$SRC" -path '*.xcassets/*' -type f -print0
} 2>/dev/null | sort -z | xargs -0 shasum 2>/dev/null | shasum | awk '{print $1}'
