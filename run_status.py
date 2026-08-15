"""run_status.py — the in-progress nightly run, parsed from scheduled_runs.log.

The nightly (run_scheduled.sh) writes its machine-readable status
(outputs/last_run_status.json) only when it FINISHES. So while a run is still
going, the only live signal is the log it appends to the whole time. This reads
the tail of that log and reconstructs the run in progress: which markets it means
to do, which it has reached, any steps that failed so far, and the latest
activity line. Read-only — it never writes and never calls Amazon, so it is safe
to poll mid-run.

Exposed via `appctl.py run-status`; the Mac app polls it while System Health is
open. Mirrors the app-side fallback reader (MerchAds/NightlyRunMonitor.swift).
"""
import datetime
import os
import re
import time

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs", "scheduled_runs.log")

# A run that has emitted nothing for this long is treated as not running, so a
# crashed run (which never wrote its "done:" line) does not read as "still going"
# forever.
STALE_AFTER_SECONDS = 20 * 60
TAIL_BYTES = 768 * 1024

_MARKET = re.compile(r"^#+\s*MARKET\s+(\S+)")
_FAILED = re.compile(r"STEP FAILED \[(?P<market>[^\]]+)\]\s+(?P<step>\S+)\s+\(exit\s+(?P<exit>\d+)\)")


def current_run(path=LOG, now=None):
    """The in-progress run as a dict, or None when the newest run block has
    already finished (a "done:" line) or the log is stale/absent."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    now = now if now is not None else time.time()
    if now - st.st_mtime > STALE_AFTER_SECONDS:
        return None
    try:
        with open(path, "rb") as f:
            if st.st_size > TAIL_BYTES:
                f.seek(st.st_size - TAIL_BYTES)
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    return parse(text, now=now)


def parse(text, now=None):
    lines = text.split("\n")
    header_idx = None
    for i in range(len(lines) - 1, -1, -1):
        if "| markets:" in lines[i]:
            header_idx = i
            break
    if header_idx is None:
        return None
    block = lines[header_idx:]
    if any(ln.startswith("done:") for ln in block):
        return None  # that run finished — last_run_status.json covers it

    label, mkts = _parse_header(lines[header_idx])
    reached, failures, last_activity = [], [], None
    for raw in block[1:]:
        line = raw.strip()
        if not line:
            continue
        m = _MARKET.match(line)
        if m:
            reached.append(m.group(1))
            continue
        f = _FAILED.search(line)
        if f:
            failures.append({"market": f.group("market"), "step": f.group("step"),
                             "exit": int(f.group("exit"))})
            continue
        if set(line) <= {"#", "="}:  # pure decoration
            continue
        last_activity = line
    return {
        "active": True,
        "label": label,
        "started": _started_iso(label),
        "elapsed_seconds": _elapsed_seconds(label, now),
        "markets": mkts,
        "reached": reached,
        "current_market": reached[-1] if reached else None,
        "failures": failures,
        "last_activity": last_activity,
    }


def _parse_header(header):
    # "================ 2026-08-14 10:00  | markets: US UK DE FR ES IT USKDP ==="
    if "| markets:" in header:
        left, right = header.split("| markets:", 1)
        return left.strip("= ").strip(), right.strip("= ").strip().split()
    return "", []


def _started_iso(label):
    # "2026-08-14 10:00" -> "2026-08-14T10:00:00" (local naive, like db._now)
    try:
        d, t = label.split(" ")
        return f"{d}T{t}:00"
    except ValueError:
        return None


def _elapsed_seconds(label, now=None):
    try:
        dt = datetime.datetime.strptime(label, "%Y-%m-%d %H:%M")  # naive = local
        now = now if now is not None else time.time()
        return max(0, int(now - dt.timestamp()))
    except ValueError:
        return None


if __name__ == "__main__":
    import json
    import sys
    print(json.dumps(current_run(), indent=1), file=sys.stdout)
