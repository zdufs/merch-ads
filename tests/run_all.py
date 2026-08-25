#!/usr/bin/env python3
"""Run the whole suite with a watchdog, so a HANG names itself.

    python3 tests/run_all.py [-v] [--timeout SECS]

Why this exists
---------------
The suite finishes in about eight seconds. Twice it has instead hung until
something killed it, and both times the only evidence was an exit code — no
test name, no line, no stack. That is the worst possible failure to debug,
because the next run passes and there is nothing left to look at.

`faulthandler.dump_traceback_later` fixes that for good: if the suite has not
finished by `--timeout`, every thread's stack is printed and the process exits.
The hang stops being a mystery and becomes a file and a line number.

The likeliest cause is a test that opens a REAL database while the app's `serve`
workers hold it — a plain SQLite lock wait, which has no timeout by default.
`tests/_no_operator_data.py` keeps the economics overlay off operator files, but
nothing stops a module opening a market database, so this watchdog is the
backstop rather than the fix.

CI runs plain `unittest discover`; this runner is for the Mac, where the app is
usually running and the contention is real.
"""

import argparse
import faulthandler
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# The suite normally takes ~8s. 300 is far past any honest slow run, so it only
# ever fires on a real hang.
DEFAULT_TIMEOUT = 300


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                    help=f"seconds before dumping every stack and giving up "
                         f"(default {DEFAULT_TIMEOUT})")
    ap.add_argument("--pattern", default="*_tests.py")
    args = ap.parse_args()

    faulthandler.enable()
    faulthandler.dump_traceback_later(args.timeout, exit=True)

    os.chdir(ROOT)
    suite = unittest.defaultTestLoader.discover(start_dir=HERE, pattern=args.pattern,
                                                top_level_dir=ROOT)
    runner = unittest.TextTestRunner(verbosity=2 if args.verbose else 1)
    result = runner.run(suite)

    faulthandler.cancel_dump_traceback_later()
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
