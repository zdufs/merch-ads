#!/bin/bash
# Contract test: every appctl READ endpoint's live JSON must decode through the
# app's Codable models. Run after touching appctl.py or Models.swift.
#   scripts/verify_models.sh            # full sweep (US + DE, all read endpoints)
set -e
cd "$(dirname "$0")/.."
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
echo "compiling models…"
# swiftc only allows top-level code in a file literally named main.swift
cp scripts/verify_models_main.swift "$TMP/main.swift"
# Models.swift is RESOLVED by name, not by path. It used to be named as
# MerchAds/Models.swift; the sources moved into folders (ebd4790) and this
# contract test had been failing to compile ever since — `set -e` plus a swiftc
# error reads a lot like a test that ran. Same rot hit scripts/stress_bridge.sh.
# Models.swift picked up a dependency on Format (Formatters.swift) since, so
# both are resolved. If this list ever grows again, glob the whole source set
# the way scripts/stress_bridge.sh does.
SRC=""
for f in Models Formatters; do
    path=$(find MerchAds -name "$f.swift" -type f | head -1)
    [ -z "$path" ] && { echo "verify_models: cannot find $f.swift under MerchAds/" >&2; exit 1; }
    SRC="$SRC $path"
done
# shellcheck disable=SC2086
swiftc -o "$TMP/verify" $SRC "$TMP/main.swift"

FAIL=0
run() {  # run <market|-> <endpoint> [extra args…]
    local MKT="$1" EP="$2"; shift 2
    local ENV=()
    [ "$MKT" != "-" ] && ENV=(env "ADS_MARKET=$MKT")
    if ! "${ENV[@]}" python3 engine/appctl.py "$EP" "$@" | "$TMP/verify" "$EP"; then
        FAIL=1
    fi
}

run - markets
run - health
run - overview
run - kill
for M in US DE; do
    echo "--- $M"
    run "$M" metrics
    run "$M" monthly
    run "$M" campaigns
    run "$M" killlist
    run "$M" stale
    run "$M" alerts
    run "$M" profit
    run "$M" harvest
    run "$M" harvest-prune
    run "$M" bidreport --days 7
    run "$M" audit --limit 20
    run "$M" digest --since 2026-01-01T00:00:00
    run "$M" demandfeed
done
# drill endpoints need real ids — take them from the US DB
CID=$(sqlite3 "file:ads_data.sqlite?mode=ro" "SELECT campaign_id FROM campaigns LIMIT 1")
AGID=$(sqlite3 "file:ads_data.sqlite?mode=ro" "SELECT ad_group_id FROM targeting_perf WHERE target_id IS NOT NULL LIMIT 1")
TID=$(sqlite3 "file:ads_data.sqlite?mode=ro" "SELECT entity_id FROM writes_log WHERE action='bid_change' LIMIT 1")
ASIN=$(sqlite3 "file:ads_data.sqlite?mode=ro" "SELECT asin FROM ad_group_product WHERE asin IS NOT NULL LIMIT 1")
run US adgroups --campaign "$CID"
run US targets --adgroup "$AGID"
run US searchterms --adgroup "$AGID"
run US asin "$ASIN"
run US bidhistory --target "$TID"
CSV=$(ls snap-grid-export-*.csv export_products_*.csv 2>/dev/null | tail -1)
[ -n "$CSV" ] && run US import-preview "$CSV" --days 14

if [ "$FAIL" = "1" ]; then
    echo "❌ CONTRACT BROKEN — a model no longer matches appctl's JSON"
    exit 1
fi
echo "✅ all endpoints decode"
