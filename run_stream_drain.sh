#!/bin/bash
# Hourly Marketing Stream pickup — runs every hour via launchd.
#
# Amazon pushes Sponsored Products rows into our SQS queues about an hour behind
# the hour they describe. This empties those queues into stream_data.sqlite.
#
# It touches NO Amazon Ads account. It reads AWS and writes one local file, so
# it is safe to run alongside the nightly, mid-phase, at any time.
#
# It also answers the SNS handshake a new subscription parks in the queue. That
# is why a fresh subscription starts flowing without a separate step.
#
# WHY HOURLY AND NOT NIGHTLY. Freshness is the entire point of Stream. Draining
# once a night would deliver the same day-old picture the report pipeline
# already gives, at more cost. The queues hold 14 days, so a missed hour — or a
# week with the Mac shut — costs nothing but a longer catch-up pass.

# ---- where the code is, and where the data is --------------------------------
# Same split as run_scheduled.sh: the app ships this script inside its bundle,
# while the databases and .env stay in the operator's folder. MERCHADS_DATA_DIR
# is the only thing that says where that is, and launchd starts a job with
# almost no environment, so the plist states it.
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENGINE="$SELF/engine"
DATA="${MERCHADS_DATA_DIR:-$SELF}"
[ -f "$ENGINE/stream_drain.py" ] || { echo "run_stream_drain: no engine at $ENGINE" >&2; exit 1; }
# Stop rather than drain into a folder that is not there and report success.
[ -d "$DATA" ] || { echo "run_stream_drain: data folder $DATA does not exist" >&2; exit 1; }
export MERCHADS_DATA_DIR="$DATA"
cd "$DATA" || exit 1
export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1   # never write .pyc into a signed app bundle: it breaks the code signature

# Same interpreter order as the nightly: explicit override, then the bundled
# CPython, then Homebrew, then whatever is on PATH. Announced on every fallback,
# because two layers quietly running under different interpreters is a split
# that has cost this project days before.
BUNDLED_PY="$SELF/python/bin/python3"
if [ -n "${ADS_PYTHON:-}" ]; then
  PY="$ADS_PYTHON"
elif [ -x "$BUNDLED_PY" ]; then
  PY="$BUNDLED_PY"
else
  PY="/opt/homebrew/bin/python3"
fi
if ! "$PY" -c "import requests" >/dev/null 2>&1; then
  echo "WARNING: $PY unusable (missing, or no requests) — falling back to PATH python3" >&2
  PY="$(command -v python3)"
fi
export PYTHONPATH="$ENGINE${PYTHONPATH:+:$PYTHONPATH}"

mkdir -p outputs
LOG="outputs/stream_drain.log"
# One generation of history is enough. A drain line is a few dozen bytes, so
# this rotates roughly never — it exists so "roughly never" cannot become "the
# disk filled up in 2029".
if [ -f "$LOG" ] && [ "$(stat -f%z "$LOG" 2>/dev/null || echo 0)" -gt 5242880 ]; then
  mv "$LOG" "$LOG.1"
fi

# 300 seconds per queue. 60 was the first guess and it was wrong: Stream sends
# roughly one message per impression, SQS hands them over about ten at a time,
# and 60 seconds read fewer messages than a single hour delivers — so the queue
# only ever grew. The loop still stops the moment the queue is empty, so a quiet
# hour still costs about 40 seconds. This only changes what a busy hour can
# finish, and the drain now says so out loud when even 300 is not enough.
# The exit code has to be the DRAIN's, not the trailing echo's. A shell group
# exits with its last command, so an SQS failure, a database failure, a parser
# crash or expired credentials were all logged and then reported to launchd as
# success. Save it, write the separator, and hand it back.
{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="
  "$PY" "$ENGINE/stream_drain.py" --seconds 300
  drain_rc=$?
  echo
} >> "$LOG" 2>&1

# A `{ }` group runs in the CURRENT shell, so drain_rc is still here.
exit "$drain_rc"
