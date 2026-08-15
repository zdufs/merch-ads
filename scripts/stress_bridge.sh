#!/bin/bash
# Stampede regression test: concurrent mixed requests on the same serve worker
# must each decode as their own type (guards against response cross-wiring).
set -e
cd "$(dirname "$0")/.."
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
swiftc -o "$TMP/stress" MerchAds/Models.swift MerchAds/AppSettings.swift \
    MerchAds/PythonBridge.swift MerchAds/PythonWorkerPool.swift scripts/stress_bridge.swift
for i in 1 2 3; do "$TMP/stress"; done
