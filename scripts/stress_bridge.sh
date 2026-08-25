#!/bin/bash
# Stampede regression test: concurrent mixed requests on the same serve worker
# must each decode as their own type (guards against response cross-wiring).
set -e
cd "$(dirname "$0")/.."
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
# Compile the WHOLE app source set, minus its @main entry point.
#
# This used to name four files. The app then grew: sources moved into folders
# (ebd4790) and PythonBridge picked up IssueCenter, which pulls Route, Theme and
# more. A hand-picked list cannot track that, and the harness had simply stopped
# compiling — a stampede test that never ran. Globbing costs a few seconds and
# survives the next reorganisation.
#
# MerchAdsApp.swift carries @main and would fight stress_bridge.swift's own
# entry point, so it is the one file left out.
SRC=$(find MerchAds -name '*.swift' -type f ! -name 'MerchAdsApp.swift' | sort)
[ -z "$SRC" ] && { echo "stress_bridge: no Swift sources under MerchAds/" >&2; exit 1; }
# shellcheck disable=SC2086
swiftc -o "$TMP/stress" $SRC scripts/stress_bridge.swift
for i in 1 2 3; do "$TMP/stress"; done
