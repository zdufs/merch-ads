#!/bin/bash
# Block engine .py edits while run_scheduled.sh is running (standing rule in
# CLAUDE.md). The nightly launches a fresh python3 per script per market, with
# --apply --auto against LIVE accounts. An edit mid-run hands later markets
# different code than earlier ones got. This happened on 2026-08-05 (benign by
# luck). The hook makes the rule deterministic instead of memory-dependent.
#
# PreToolUse on Edit|Write|MultiEdit. Exit 2 blocks the call and shows the
# message to Claude. tests/ is exempt: the nightly never imports it.

INPUT=$(cat)
FILE=$(printf '%s' "$INPUT" | /usr/bin/python3 -c \
  "import json,sys; print(json.load(sys.stdin).get('tool_input',{}).get('file_path',''))" 2>/dev/null)

case "$FILE" in
  *"/POD/Ads/tests/"*) exit 0 ;;
  *"/POD/Ads/"*.py) ;;
  *) exit 0 ;;
esac

if pgrep -f "run_scheduled.sh" >/dev/null 2>&1; then
  echo "BLOCKED: run_scheduled.sh is running — engine .py edits wait until it finishes (standing rule). Check: ps ax | grep run_scheduled" >&2
  exit 2
fi
exit 0
