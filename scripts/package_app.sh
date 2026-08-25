#!/bin/bash
#
# package_app.sh — build a reproducible, ad-hoc-signed local macOS bundle at
# dist/Merch Ads.app from the MerchAds Xcode project.
#
# Unlike a SwiftPM package, xcodebuild already emits a complete .app (Info.plist
# from GENERATE_INFOPLIST_FILE, icon from MerchAds/AppIcon.icns, resources
# baked in). This script builds Release to a dedicated derived-data path, copies
# the product to a stable repo-relative location, re-signs ad-hoc, and validates.
#
# NOTE: Merch Ads is a SwiftUI UI over a Python engine, and the bundle carries
# BOTH — Contents/Resources/engine (the modules) and Contents/Resources/python
# (a relocatable CPython with requests). No system python3, no repo checkout and
# no pip install are needed to run it.
#
# What stays outside is DATA: the per-market SQLite databases, .env, and the
# operator config, at the folder in Settings (default ~/Biznis/.../Ads). The app
# passes that folder to the engine as MERCHADS_DATA_DIR. So the app can be
# replaced or deleted without touching a single row of history.
#
# Ad-hoc signing (codesign --sign -) gives a consistent signature and catches
# malformed nested code. It does NOT establish a Developer ID; Gatekeeper may
# still refuse it, which is expected for this local-only workflow.

set -euo pipefail

# --- resolve repo root independent of caller's working directory ---------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# --- configuration -------------------------------------------------------------
PROJECT="MerchAds.xcodeproj"
SCHEME="MerchAds"
CONFIG="Release"
EXPECTED_BUNDLE_ID="io.github.zdufs.MerchAds"   # keep — settings persistence depends on it
APP_NAME="Merch Ads.app"
EXECUTABLE_NAME="Merch Ads"

# Derived data lives in the system temp dir, NOT the repo: a repo-local path
# gives SQLite "disk I/O error" on build.db here (the dedicated-scratch fix).
DERIVED="${TMPDIR:-/tmp}/merchads-package-derived"
DIST="${REPO_ROOT}/dist"
APP_DST="${DIST}/${APP_NAME}"

fail() { printf 'package_app: %s\n' "$1" >&2; exit 1; }

LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/Support/lsregister"

# --- arguments -----------------------------------------------------------------
INSTALL=0
for arg in "$@"; do
  case "${arg}" in
    --install) INSTALL=1 ;;
    -h|--help) echo "usage: package_app.sh [--install]   # --install also copies to /Applications"; exit 0 ;;
    *) fail "unknown argument: ${arg} (use --install)" ;;
  esac
done

# --- preflight -----------------------------------------------------------------
command -v xcodebuild >/dev/null 2>&1 || fail "xcodebuild not found (install Xcode command line tools)"
command -v codesign   >/dev/null 2>&1 || fail "codesign not found"
command -v plutil     >/dev/null 2>&1 || fail "plutil not found"
[ -d "${REPO_ROOT}/${PROJECT}" ] || fail "missing ${PROJECT} at repo root"

# --- never replace the bundle while the nightly is running ----------------------
# Since 2026-08-21 the nightly RUNS FROM THE BUNDLE
# (/Applications/Merch Ads.app/Contents/Resources/run_scheduled.sh), and the
# relaunch that follows every install kills the app's python workers. Doing
# either mid-run kills whatever step is in flight.
#
# That is not hypothetical. On 2026-08-24 an install at 12:57:30 landed while
# the run was on DE. Three steps died on SIGTERM — daily_metrics,
# backfill_daily, backfill_target_daily — so DE's Monday attribution true-up
# never ran. Nothing looked broken afterwards: the run carried on to FR, the
# log recorded three failed steps among twelve, and DE's Dashboard simply
# showed sales about 15% low against a trailing-30 snapshot that had matured
# normally. It was found two hours later by comparing two tables that are
# supposed to agree, not by anything that raised.
#
# SIGTERM is only the half of it that looks like an interruption. Replacing the
# bundle also pulls the files out from under processes that keep running, and
# they fail somewhere that reads nothing like an install: every TLS call in
# DE's phase0_pull and scavenger_build that same afternoon died with
#   OSError: Could not find a suitable TLS CA certificate bundle, invalid path:
#   /Applications/Merch Ads.app/Contents/Resources/python/.../certifi/cacert.pem
# Amazon looked unreachable, all five DE reports were never requested, and the
# step exited 1 rather than 143. If that error ever appears in the run log
# again, look for an install at that minute before looking at the network.
#
# The run takes hours, so this refuses rather than waits. Override with
# FORCE_DURING_NIGHTLY=1 when the interruption is the point (a bad build is
# live, say) — the cost is one market's history needing a re-run afterwards.
#
# It is a FUNCTION because it has to be asked twice. Checking once here answers
# a question about 09:55 and then acts on it at 10:20: the Swift tests, the
# Release build, the engine copy and the CPython fetch take many minutes, and
# launchd fires at 10:00. A build begun on a quiet machine therefore sailed
# past this check and replaced the bundle from underneath a run that had
# started in the meantime — the same damage the guard exists to prevent, just
# through the window it left open. The second call sits immediately above the
# `rm -rf` that deletes the installed copy.
refuse_during_nightly() {
  local _extra="${1:-}"
  [ "${INSTALL}" = "1" ] || return 0
  [ "${FORCE_DURING_NIGHTLY:-0}" != "1" ] || return 0
  pgrep -f "run_scheduled.sh" >/dev/null 2>&1 || return 0
  # `|| true` is load-bearing under `set -euo pipefail`. The run can exit
  # between the pgrep above and this ps, and then ps fails, pipefail promotes
  # that to the assignment, and set -e kills the script — exit 1, no message,
  # no build, nothing said. Found 2026-08-24 by the test for this guard,
  # which stubs pgrep with a pid that does not exist.
  local _since=""
  _since="$(ps -o etime= -p "$(pgrep -f 'run_scheduled.sh' | head -1)" 2>/dev/null | tr -d ' ' || true)"
  fail "the nightly is running (${_since:-unknown} elapsed) and it runs FROM the
bundle this would replace. Installing now kills the step in flight and that
market silently keeps stale numbers.${_extra}

  wait for it:   until ! pgrep -f run_scheduled.sh >/dev/null; do sleep 60; done
  or force it:   FORCE_DURING_NIGHTLY=1 bash scripts/package_app.sh --install

After a forced install, re-run the market that was interrupted — check
outputs/scheduled_runs.log for 'STEP FAILED' with exit 143."
}

refuse_during_nightly

# --- clean only our generated locations ----------------------------------------
rm -rf "${DERIVED}" "${DERIVED}-tests" "${APP_DST}"
mkdir -p "${DIST}"

# --- Swift tests ---------------------------------------------------------------
# The app target and the test target compile separately, so the app can build
# perfectly while the tests do not compile at all. That happened on 2026-08-21:
# a new field on HealthResponse broke six fixtures, CI runs only the Python
# suite, and 162 Swift tests stopped running with nothing to show for it until
# an audit found them. Packaging is the one step the standing rule guarantees
# happens on every surviving Swift change, so the check lives here.
#
# Skip with SKIP_SWIFT_TESTS=1 for an engine-only emergency rebuild.
if [ "${SKIP_SWIFT_TESTS:-0}" != "1" ]; then
  echo "==> Running Swift tests…"
  xcodebuild test \
    -project "${PROJECT}" \
    -scheme "${SCHEME}" \
    -destination 'platform=macOS' \
    -derivedDataPath "${DERIVED}-tests" \
    >/dev/null 2>&1 \
    || fail "Swift tests failed or did not compile. Re-run to see why:
    xcodebuild test -project ${PROJECT} -scheme ${SCHEME} -destination 'platform=macOS'"
fi

# --- build Release -------------------------------------------------------------
echo "==> Building ${SCHEME} (${CONFIG})…"
xcodebuild \
  -project "${PROJECT}" \
  -scheme "${SCHEME}" \
  -configuration "${CONFIG}" \
  -derivedDataPath "${DERIVED}" \
  -destination 'platform=macOS' \
  build \
  >/dev/null || fail "xcodebuild failed (re-run without >/dev/null to see the log)"

# --- locate the built product --------------------------------------------------
APP_SRC="$(/usr/bin/find "${DERIVED}/Build/Products/${CONFIG}" -maxdepth 1 -name '*.app' -print -quit || true)"
[ -n "${APP_SRC}" ] && [ -d "${APP_SRC}" ] || fail "no .app produced under ${DERIVED}/Build/Products/${CONFIG}"

# --- copy to the stable dist/ path ---------------------------------------------
echo "==> Copying to ${APP_DST}…"
/bin/cp -R "${APP_SRC}" "${APP_DST}"

# --- bundle the engine and its interpreter -------------------------------------
#
# The app used to need three things from outside itself: this repo's engine/
# folder, a system python3, and the `requests` package installed into it. Any
# of the three going missing turned every screen into "appctl.py not found" or
# "python3 not found", and neither is something the operator can be expected to
# repair. Both now ship inside the bundle:
#
#   Contents/Resources/engine/        the 60 .py modules + rules/
#   Contents/Resources/python/        a relocatable CPython with requests
#
# DATA stays outside — the SQLite databases are ~2 GB, .env holds the Amazon
# credentials, and the Snap exports are dropped into the POD folder by hand. The
# app passes MERCHADS_DATA_DIR / MERCHADS_POD_DIR so the bundled engine reads
# them where they already live (engine/paths.py). Code in the bundle, data in
# the folder — and replacing the app can never touch the data.
bundle_engine() {
  local res="${APP_DST}/Contents/Resources"
  echo "==> Bundling the engine…"
  /bin/rm -rf "${res}/engine"
  /bin/mkdir -p "${res}/engine"
  # -a keeps the tree; the excludes keep bytecode and test scratch out of a
  # signed bundle, where a stale .pyc would be sealed in and never regenerate.
  /usr/bin/rsync -a --exclude '__pycache__' --exclude '*.pyc' --exclude '.DS_Store' \
    "${REPO_ROOT}/engine/" "${res}/engine/" || fail "could not copy engine/"
  [ -f "${res}/engine/appctl.py" ] || fail "bundled engine has no appctl.py"
  [ -f "${res}/engine/rules/runner.py" ] || fail "bundled engine is missing the rules package"

  # The nightly job goes in too, so `install_launchd.sh --app` can point launchd
  # at the app and the scheduled run stops needing a checkout as well.
  /bin/cp "${REPO_ROOT}/run_scheduled.sh" "${res}/run_scheduled.sh" \
    || fail "could not copy run_scheduled.sh"
  chmod +x "${res}/run_scheduled.sh"
  # The hourly Stream pickup ships too, for the same reason the nightly does:
  # with --app the launchd jobs depend on nothing but the app and the data
  # folder — no checkout, no Homebrew python.
  /bin/cp "${REPO_ROOT}/run_stream_drain.sh" "${res}/run_stream_drain.sh" \
    || fail "could not copy run_stream_drain.sh"
  chmod +x "${res}/run_stream_drain.sh"
}

# The interpreter is a python-build-standalone distribution, fetched by uv and
# cached outside the repo. It carries its own OpenSSL (so the Amazon HTTPS calls
# work with no system dependency) and its own SQLite 3.53 (new enough to read
# the WAL databases read-only — the system 3.51 in the app's own process is
# not, which is what "no local data" was in Aug 2026).
PY_VERSION="3.12"
PY_CACHE="${HOME}/Library/Caches/MerchAds/python"

bundle_python() {
  local res="${APP_DST}/Contents/Resources"
  command -v uv >/dev/null 2>&1 || fail "uv not found — needed to fetch the bundled Python (brew install uv)"

  echo "==> Fetching CPython ${PY_VERSION} (cached in ${PY_CACHE})…"
  /bin/mkdir -p "${PY_CACHE}"
  uv python install --install-dir "${PY_CACHE}" "${PY_VERSION}" >/dev/null 2>&1 \
    || fail "uv could not install CPython ${PY_VERSION}"

  # uv leaves both a versioned directory and a moving alias. Take the versioned
  # one so a bundle is reproducible from its own contents.
  local src
  src="$(/usr/bin/find "${PY_CACHE}" -maxdepth 1 -type d -name "cpython-${PY_VERSION}.*-macos-*" -print | sort | tail -1)"
  [ -n "${src}" ] && [ -x "${src}/bin/python3" ] || fail "no usable CPython under ${PY_CACHE}"

  # requests is the engine's only third-party dependency (requirements.txt).
  # --break-system-packages: a python-build-standalone build ships the PEP 668
  # marker, and this interpreter exists to serve exactly one application.
  "${src}/bin/python3" -m pip install --quiet --disable-pip-version-check \
    --break-system-packages -r "${REPO_ROOT}/requirements.txt" \
    || fail "could not install ${REPO_ROOT}/requirements.txt into the bundled Python"

  echo "==> Bundling CPython $("${src}/bin/python3" -c 'import sys;print(sys.version.split()[0])')…"
  /bin/rm -rf "${res}/python"
  /bin/mkdir -p "${res}/python"
  /usr/bin/rsync -a --exclude '__pycache__' --exclude '*.pyc' --exclude 'test/' --exclude 'idlelib/' \
    --exclude 'tkinter/' --exclude '.DS_Store' "${src}/" "${res}/python/" \
    || fail "could not copy the Python distribution"

  local py="${res}/python/bin/python3"
  [ -x "${py}" ] || fail "bundled python3 is missing or not executable"
  # Prove the copy still runs where it landed. A relocatable build should, but a
  # broken one must fail HERE and not on the operator's first launch.
  "${py}" -c "import sqlite3, ssl, requests" \
    || fail "the bundled Python cannot import sqlite3/ssl/requests after the copy"
}

bundle_engine
bundle_python

# --- re-sign ad-hoc (clean signature after copy) -------------------------------
echo "==> Ad-hoc signing…"
codesign --force --sign - "${APP_DST}" >/dev/null 2>&1 || fail "codesign --sign - failed"

# --- validate ------------------------------------------------------------------
echo "==> Validating…"
PLIST="${APP_DST}/Contents/Info.plist"
EXE="${APP_DST}/Contents/MacOS/${EXECUTABLE_NAME}"

[ -f "${PLIST}" ] || fail "missing Info.plist"
plutil -lint "${PLIST}" >/dev/null || fail "Info.plist failed plutil -lint"

GOT_ID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "${PLIST}" 2>/dev/null || true)"
[ "${GOT_ID}" = "${EXPECTED_BUNDLE_ID}" ] || fail "bundle id is '${GOT_ID}', expected '${EXPECTED_BUNDLE_ID}'"

[ -x "${EXE}" ] || fail "missing/non-executable ${EXE}"
[ -f "${APP_DST}/Contents/Resources/AppIcon.icns" ] || fail "missing Resources/AppIcon.icns (icon would not show)"
/usr/libexec/PlistBuddy -c 'Print :CFBundleIconFile' "${PLIST}" >/dev/null 2>&1 \
  || fail "Info.plist has no CFBundleIconFile (icon would not show)"
# The icon must stay on the legacy .icns path. An asset-catalog icon sets
# CFBundleIconName, and macOS 26 then draws the icon on its light "platter",
# which shows as a grey rim around our white tile.
/usr/libexec/PlistBuddy -c 'Print :CFBundleIconName' "${PLIST}" >/dev/null 2>&1 \
  && fail "Info.plist has CFBundleIconName — the macOS 26 platter rim is back"

codesign --verify --deep --strict --verbose=2 "${APP_DST}" >/dev/null 2>&1 \
  || fail "codesign --verify --deep --strict failed"

# --- register with Launch Services so Finder/Dock pick up the identity ---------
"${LSREGISTER}" -f "${APP_DST}" >/dev/null 2>&1 || true

# --- optional install to /Applications -----------------------------------------
INSTALLED=""
if [ "${INSTALL}" -eq 1 ]; then
  DEST="/Applications/${APP_NAME}"
  echo "==> Installing to ${DEST}…"
  [ -w /Applications ] || fail "/Applications is not writable — drag \"${APP_DST}\" there manually, or re-run with the right permissions"
  # Ask again, here, where it counts. Everything above took minutes and the
  # nightly starts at 10:00 on a timer, so the answer from the preflight is
  # stale by now. The next line is the destructive one.
  refuse_during_nightly "

The build finished and is at ${APP_DST}; only the install was refused, so
nothing under /Applications was touched."
  rm -rf "${DEST}"
  # -p preserves mtimes. Without it every engine .py in the installed copy looks
  # NEWER than the .pyc shipped beside it, so the first python run rewrites
  # those .pyc inside Contents/Resources — a sealed directory — and the code
  # signature is invalid from that moment. The app also sets
  # PYTHONDONTWRITEBYTECODE now; this keeps the shipped bytecode usable so
  # nothing pays to re-parse it either.
  /bin/cp -Rp "${APP_DST}" "${DEST}"
  # Stamp the installed copy with a content hash of the app source. The freshness
  # hook (.claude/hooks/check_app_fresh.sh) compares this stamp against the current
  # source, so git mtime churn (commit / merge / checkout rewrites file times to
  # "now") never reads a byte-identical fresh install as stale. Written before
  # signing so it is sealed into the bundle.
  # Pass the source folder EXPLICITLY. The hook otherwise resolves it from
  # CLAUDE_PROJECT_DIR, so packaging from a git worktree stamped the bundle with
  # the main checkout's fingerprint — a build from one tree wearing another
  # tree's label, which reads as stale as soon as the two differ.
  "${REPO_ROOT}/.claude/hooks/app_src_hash.sh" "${REPO_ROOT}" \
    > "${DEST}/Contents/Resources/.src_manifest" 2>/dev/null || true
  codesign --force --sign - "${DEST}" >/dev/null 2>&1 || fail "re-sign of installed copy failed"
  codesign --verify --deep --strict "${DEST}" >/dev/null 2>&1 || fail "installed copy failed signature verify"
  "${LSREGISTER}" -f "${DEST}" >/dev/null 2>&1 || true
  INSTALLED="${DEST}"
fi

echo
echo "OK  ${APP_DST}"
echo "    bundle id : ${EXPECTED_BUNDLE_ID}"
[ -n "${INSTALLED}" ] && echo "    installed : ${INSTALLED}"
echo "    launch    : open \"${INSTALLED:-${APP_DST}}\""
