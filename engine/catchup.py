#!/usr/bin/env python3
"""Catch every market's data up in ONE command, unattended.

    python3 engine/catchup.py                    # every configured market
    python3 engine/catchup.py --markets UK DE    # just these

Amazon builds its reports slowly, and routinely takes longer than the 25-minute
poll a pull is willing to spend. The report ids are banked either way, so the
run that asks and the run that collects have always been two separate runs. The
nightly hides this: it asks tonight and collects tomorrow.

A catch-up has no tomorrow. Doing it by hand means running six markets serially,
each burning the full poll before deferring, then running them all again — which
is how five nights of missed EU data cost three rounds of babysitting on
2026-08-20.

So this asks EVERY market first (--max-wait 0, seconds each), then collects in
rounds until nothing is pending or the deadline passes. Collect rounds use
--reports-only: the structure pull already ran in the ask round, and the targets
mirror alone is ~9 minutes for US.

Nothing here writes to Amazon. It pulls reports and banks them, exactly like the
nightly's read phases.
"""

import argparse
import os
import sqlite3
import subprocess
import sys
import time

import db
import markets
import paths

HERE = os.path.dirname(os.path.abspath(__file__))
PULL = os.path.join(HERE, "phase0_pull.py")
DAILY = os.path.join(HERE, "daily_metrics.py")

# Report jobs in these states are history, not work in progress. Same list the
# health endpoint uses, for the same reason: a dead job counted as "pending"
# forever would keep the loop spinning to its deadline.
DEAD = ("FAILED", "CANCELLED", "EXPIRED")


def market_db_path(code):
    name = "ads_data.sqlite" if code == "US" else f"ads_data_{code}.sqlite"
    return os.path.join(paths.REPO_ROOT, name)


def pending_reports(code):
    """How many report jobs this market has asked for and not yet collected."""
    path = market_db_path(code)
    if not os.path.exists(path):
        return 0
    conn = db.open_readonly(path)
    try:
        placeholders = ",".join("?" * len(DEAD))
        return conn.execute(
            f"""SELECT COUNT(*) FROM report_jobs
                WHERE downloaded=0 AND status NOT IN ({placeholders})""", DEAD).fetchone()[0]
    except sqlite3.OperationalError as e:
        # A market whose database has no report_jobs table yet has nothing
        # pending, and that is the only thing this may quietly answer 0 for.
        if "no such table" in str(e):
            return 0
        # Anything else — a renamed column, a corrupt page — must not answer
        # "nothing pending". The whole loop is `keep collecting until this
        # reaches 0`, so a swallowed error ends the catch-up immediately and
        # reports a clean finish over the reports it never collected. That is
        # the failure a catch-up exists to repair.
        raise
    finally:
        conn.close()


def banked(code):
    """(newest perf snapshot, newest banked day) — what the operator came for."""
    path = market_db_path(code)
    if not os.path.exists(path):
        return None, None
    conn = db.open_readonly(path)
    try:
        perf = min((conn.execute(f"SELECT MAX(date) FROM {t}").fetchone()[0] or "—")
                   for t in ("campaign_perf", "targeting_perf", "search_term_perf"))
        day = conn.execute("SELECT MAX(date) FROM daily_totals").fetchone()[0] or "—"
        return perf, day
    except Exception:
        return None, None
    finally:
        conn.close()


def run(script, code, *flags):
    env = dict(os.environ, ADS_MARKET=code, PYTHONUNBUFFERED="1")
    label = os.path.basename(script)
    print(f"  [{code}] {label} {' '.join(flags)}", flush=True)
    result = subprocess.run([sys.executable, script, *flags], env=env,
                            cwd=paths.REPO_ROOT, capture_output=True, text=True)
    for line in result.stdout.splitlines()[-3:]:
        print(f"      {line}", flush=True)
    if result.returncode != 0:
        print(f"      !! exit {result.returncode}", flush=True)
    return result.returncode


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--markets", nargs="+", metavar="CODE",
                    help="market codes (default: every configured market)")
    ap.add_argument("--round-wait", type=int, default=240, metavar="SECS",
                    help="how long each collect round polls one market "
                         "(default %(default)s)")
    ap.add_argument("--deadline-mins", type=int, default=90, metavar="MINS",
                    help="give up after this long and say what is still pending "
                         "(default %(default)s)")
    ap.add_argument("--skip-daily", action="store_true",
                    help="perf reports only; leave the day history alone")
    return ap.parse_args(argv)


def configured_markets():
    """Every market this account is configured for.

    A failure here used to become `["US"]` silently, so a catch-up that could not
    read the environment quietly skipped six markets and then reported success.
    Falling back to one market is not a smaller version of the job; it is a
    different job with the same ending. Say what happened and let the caller
    decide — --markets is always available to name them by hand.
    """
    from ads_client import load_env
    return markets.available(load_env())


def main(argv=None):
    args = parse_args(argv)
    codes = args.markets or configured_markets()
    print(f"Catch-up for: {' '.join(codes)}")
    print(f"Round 1 of many — ASKING every market for its reports.\n")

    # A child that DIED did not ask for anything, so its market has no pending
    # report — and "no pending report" is exactly what this script reads as
    # finished. Discarding these return codes meant a market whose pull crashed
    # on startup came out the far end as "everything was collected".
    broke = {}
    for code in codes:
        if run(PULL, code, "--max-wait", "0"):
            broke.setdefault(code, []).append("pull")
        if not args.skip_daily and run(DAILY, code, "--max-wait", "0"):
            broke.setdefault(code, []).append("daily")

    deadline = time.time() + args.deadline_mins * 60
    rnd = 1
    while time.time() < deadline:
        waiting = [c for c in codes if pending_reports(c)]
        if not waiting:
            break
        rnd += 1
        left = int((deadline - time.time()) / 60)
        print(f"\nRound {rnd} — collecting {' '.join(waiting)} ({left} min left).")
        for code in waiting:
            if run(PULL, code, "--reports-only", "--max-wait", str(args.round_wait)):
                broke.setdefault(code, []).append("pull")
            if not args.skip_daily and run(DAILY, code, "--max-wait", str(args.round_wait)):
                broke.setdefault(code, []).append("daily")

    print("\n%-8s %-12s %-12s %s" % ("MARKET", "PERF DATA", "DAY HISTORY", "PENDING"))
    still = 0
    for code in codes:
        perf, day = banked(code)
        waiting = pending_reports(code)
        still += waiting
        print("%-8s %-12s %-12s %s" % (code, perf or "—", day or "—", waiting or ""))
    if still:
        print(f"\n{still} report(s) still generating. They are saved: run this "
              f"again later, or the nightly will collect them.")
        return 1
    if broke:
        print("\n⚠️ These steps exited non-zero, so their data may be incomplete:")
        for code, steps in sorted(broke.items()):
            print(f"   {code}: {', '.join(sorted(set(steps)))}")
        print("   Nothing on Amazon was changed. Read the output above before"
              " treating this catch-up as done.")
        return 1
    print("\nEverything asked for was collected. Nothing on Amazon was changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
