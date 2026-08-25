#!/bin/bash
# Prints a single content hash of EVERYTHING the app bundle ships:
#
#   MerchAds/    every *.swift, *.plist, *.entitlements, *.icns, and every file
#                inside an *.xcassets bundle
#   MerchAds.xcodeproj/  project.pbxproj and the shared schemes
#   engine/      every *.py, including engine/rules/
#   run_scheduled.sh, run_stream_drain.sh, requirements.txt
#
# The project file and requirements.txt joined the set on 2026-08-24. Neither
# is a source file, and both decide what the bundle CONTAINS. project.pbxproj
# says which sources compile, with which build settings and which entitlements;
# requirements.txt is installed into the bundled interpreter by package_app.sh.
# Changing either one alone left this hash identical, so the freshness hook
# called an out-of-date /Applications copy fresh and the turn ended.
#
# Used by two places, so they always agree on what "the source" is:
#   - scripts/package_app.sh --install stamps this hash into the installed bundle.
#   - .claude/hooks/check_app_fresh.sh compares the current hash to that stamp.
#
# The engine half was added 2026-08-22, and its absence was a silent hole.
# The app became STANDALONE on 2026-08-21: it carries its own copy of the
# modules at Contents/Resources/engine and runs those, not the checkout. So an
# engine-only fix does not reach the running app until the bundle is rebuilt.
# Until this hash covered engine/, three things agreed on the wrong answer —
# the tests passed, the freshness hook said the app was fresh, and CLAUDE.md
# said a relaunch was enough. A fix could sit in the repo, green, while the app
# went on running the old code. Widening the hash is what makes the Stop hook
# say so instead of anyone having to remember.
#
# It hashes CONTENT, not timestamps, on purpose. git commit / merge / checkout
# rewrite working-tree file mtimes to "now" without changing a byte, which made
# the old mtime-based freshness check flag a freshly-installed app as stale.
#
# The path is the REPO ROOT (it used to be the MerchAds folder, back when only
# Swift shipped). An explicit argument WINS over CLAUDE_PROJECT_DIR: it used to
# be the other way round, so package_app.sh running in a git worktree stamped
# the bundle with the hash of the MAIN checkout's sources — a build from one
# tree labelled with another tree's fingerprint, which reads as stale the moment
# they differ.
if [ -n "${1:-}" ]; then
  ROOT="$1"
else
  ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
fi

# Hash names RELATIVE to ROOT, so the same sources hash the same wherever the
# tree is checked out. `shasum` prints the path next to each digest, so absolute
# paths made the result depend on the folder the repo happened to live in.
cd "$ROOT" 2>/dev/null || exit 1

# Sort in BYTE order, everywhere. `sort` collates by the caller's locale, so the
# same unmodified tree hashed to two different digests under LC_ALL=C and under
# en_US.UTF-8. package_app.sh stamps the bundle with whichever locale the
# install ran under, and the Stop hook recomputes under whichever locale the
# turn ran under. When the two differ, a byte-identical fresh install reads
# STALE for good — the exact false alarm the content hash replaced mtimes to
# remove. Found 2026-08-24.
export LC_ALL=C

{ find ./MerchAds -type f \( -name '*.swift' -o -name '*.plist' \
       -o -name '*.entitlements' -o -name '*.icns' \) -print0
  find ./MerchAds -path '*.xcassets/*' -type f -print0
  # xcuserdata holds per-user window and scheme state, which is not shipped and
  # would churn on its own.
  find ./MerchAds.xcodeproj -type f \( -name '*.pbxproj' -o -name '*.xcscheme' \) \
       -not -path '*/xcuserdata/*' -print0
  find ./engine -type f -name '*.py' -not -path '*/__pycache__/*' -print0
  find . -maxdepth 1 -type f \( -name 'run_scheduled.sh' \
       -o -name 'run_stream_drain.sh' -o -name 'requirements.txt' \) -print0
} 2>/dev/null | sort -z | xargs -0 shasum 2>/dev/null | shasum | awk '{print $1}'
