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
# NOTE: Merch Ads is a thin SwiftUI UI over the local Python engine. The bundle
# is NOT self-contained — at runtime it shells out to appctl.py and reads the
# per-market SQLite DBs at the engine root (Settings → default ~/Biznis/.../Ads).
# Copying the .app elsewhere is fine; the engine must still be present locally.
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

# --- clean only our generated locations ----------------------------------------
rm -rf "${DERIVED}" "${APP_DST}"
mkdir -p "${DIST}"

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
  rm -rf "${DEST}"
  /bin/cp -R "${APP_DST}" "${DEST}"
  # Stamp the installed copy with a content hash of the app source. The freshness
  # hook (.claude/hooks/check_app_fresh.sh) compares this stamp against the current
  # source, so git mtime churn (commit / merge / checkout rewrites file times to
  # "now") never reads a byte-identical fresh install as stale. Written before
  # signing so it is sealed into the bundle.
  "${REPO_ROOT}/.claude/hooks/app_src_hash.sh" > "${DEST}/Contents/Resources/.src_manifest" 2>/dev/null || true
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
