#!/usr/bin/env python3
"""What the nightly says about itself when something goes wrong.

Run from the Ads folder:  python3 -m unittest tests.nightly_outcome_tests -v

Two silences, found 2026-08-24:

1. `run_scheduled.sh` always exited 0. Every failed step was counted into
   $FAILURES and announced in the log, and the script's last statement was an
   `echo`, whose exit status is 0 forever. `appctl run` reports that code, so
   the app's "Full nightly run" button said success however many phases had
   crashed — beside an output pane that was empty, because everything the run
   prints goes into the log.

2. A market whose economics gate closed skipped every auto-apply stage —
   negatives, pauses, both harvest promoters, harvest prune, bids, both
   builders, seasonal and the DSL rules — and that was not a step failure. The
   status file said ok, the notification said digest, and the one place that
   said a whole market did no automation all night was a line in a 4 MB log.

THE NIGHTLY IS RUN FOR REAL HERE. It is driven under a stub interpreter, so no
Amazon call, no database and no engine module is ever reached: `ADS_PYTHON`
points at a shell script that records its arguments, answers the three inline
snippets, and hands the two real python steps (the JSON reader and the
status-file writer) to the system python. HOME and PATH are redirected too, so
the run's export pruning sees an empty folder and its notification goes to a
stub. That is what makes this a behaviour test rather than another regex over
the script's text.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER = os.path.join(HERE, "run_scheduled.sh")

# Every invocation is one stub process, and a run makes a few dozen.
NIGHTLY_TIMEOUT = 180

STUB_PY = r"""#!/bin/bash
# Stands in for python3 inside the nightly. Records the call, answers the
# inline snippets, and reaches nothing real.
echo "$*" >> "$STUB_LOG"
if [ -n "$FAIL_STEP" ]; then
  case "$*" in *"$FAIL_STEP"*) exit 1 ;; esac
fi
case "$1" in
  -c)
    case "$2" in
      *USKDP*)   exit 1 ;;                  # no KDP profile configured
      *is_kdp*)  exit 1 ;;                  # this market is not KDP
      *markets.available*) echo "US" ;;     # the market list
      *json.load*) exec "$REAL_PY" "$@" ;;  # reads the econ-gate envelope
      *) exit 0 ;;
    esac ;;
  -) exec "$REAL_PY" "$@" ;;                # the status-file writer
  *appctl.py)
    case "$2" in
      econ-gate) echo "$ECON_ENVELOPE" ;;
      *) exit 0 ;;
    esac ;;
esac
exit 0
"""

STUB_NOTIFIER = "#!/bin/bash\nexit 0\n"


def _write(path, text, mode=0o755):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(path, mode)


class NightlyRun(unittest.TestCase):
    """One temp data folder, one stub interpreter, one real run of the script."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nightly")
        self.home = os.path.join(self.dir, "home")
        self.bin = os.path.join(self.dir, "bin")
        self.data = os.path.join(self.dir, "data")
        for d in (self.home, self.bin, self.data):
            os.makedirs(d)
        self.stub = os.path.join(self.bin, "stub-python")
        self.log = os.path.join(self.dir, "calls.log")
        _write(self.stub, STUB_PY)
        # The run ends by posting a macOS notification. Ours goes nowhere.
        for name in ("osascript", "terminal-notifier", "caffeinate"):
            _write(os.path.join(self.bin, name), STUB_NOTIFIER)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_nightly(self, econ_ok=True, fail_step=""):
        env = dict(os.environ)
        env.update({
            "MERCHADS_DATA_DIR": self.data,
            "ADS_PYTHON": self.stub,
            "REAL_PY": sys.executable,
            "STUB_LOG": self.log,
            "FAIL_STEP": fail_step,
            "ECON_ENVELOPE": json.dumps({"ok": True, "data": {"ok": bool(econ_ok)}}),
            # An empty HOME keeps the run's export pruning away from the
            # operator's real POD folder, and PATH keeps its notification here.
            "HOME": self.home,
            "PATH": self.bin + os.pathsep + env.get("PATH", ""),
        })
        proc = subprocess.run(["bash", RUNNER], env=env, cwd=self.dir,
                              capture_output=True, text=True,
                              timeout=NIGHTLY_TIMEOUT)
        return proc

    def status(self):
        path = os.path.join(self.data, "outputs", "last_run_status.json")
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def calls(self):
        with open(self.log, encoding="utf-8") as fh:
            return fh.read()

    # ---- the exit code -------------------------------------------------

    def test_a_clean_run_exits_zero(self):
        proc = self.run_nightly()
        self.assertEqual(0, proc.returncode, proc.stderr[-800:])
        got = self.status()
        self.assertTrue(got["ok"])
        self.assertEqual([], got["failures"])

    def test_a_failed_step_carries_out_of_the_script(self):
        proc = self.run_nightly(fail_step="phase0_pull.py")
        self.assertEqual(1, proc.returncode,
                         "a run with a failed step exited 0, so the app called "
                         "it a success")
        failed = {(f["market"], f["step"]) for f in self.status()["failures"]}
        self.assertIn(("US", "phase0_pull"), failed)

    def test_the_closing_line_reaches_stdout_not_only_the_log(self):
        """Everything the run prints goes into the log, so the caller's output
        pane was empty however the night went."""
        proc = self.run_nightly()
        self.assertIn("done:", proc.stdout)

    # ---- the economics gate --------------------------------------------

    def test_a_closed_econ_gate_is_named_in_the_status_file(self):
        self.run_nightly(econ_ok=False)
        got = self.status()
        self.assertEqual([{"market": "US", "reason": "econ_gate"}], got["gated"])

    def test_a_closed_econ_gate_really_does_skip_the_writing_stages(self):
        """The reason the gate has to be reported: the market applied nothing.

        This asserts the skip as well as the report, so a gate that stopped
        gating could not pass by naming itself.
        """
        self.run_nightly(econ_ok=False)
        calls = self.calls()
        for skipped in ("phase2_apply.py", "phase3_bids.py", "lottery_build.py",
                        "rules-nightly"):
            self.assertNotIn(skipped, calls)
        self.assertIn("daily_metrics.py", calls)   # the reads still run

    def test_an_open_gate_leaves_the_gated_list_empty(self):
        self.run_nightly()
        self.assertEqual([], self.status()["gated"])
        self.assertIn("phase3_bids.py", self.calls())

    def test_a_gated_market_is_not_counted_as_a_failed_step(self):
        """Nothing crashed. Calling it a failure would put RUN FAILED on a run
        that did exactly what its own gate told it to."""
        proc = self.run_nightly(econ_ok=False)
        self.assertEqual(0, proc.returncode)
        self.assertEqual([], self.status()["failures"])
        self.assertTrue(self.status()["ok"])


class TheRunReplyCarriesWhatTheRunWrote(unittest.TestCase):
    """`appctl run` answered with an exit code that was 0 forever and a text
    field that was empty, because the nightly prints into its log.

    The status file is where the failed steps and the gated markets are — but
    only if THIS run wrote it. Yesterday's file reads exactly like today's, so a
    run that died before writing one would be reported with the previous
    outcome, which is worse than reporting nothing.
    """

    def setUp(self):
        sys.path.insert(0, os.path.join(HERE, "engine"))
        import appctl                                   # noqa: E402
        self.appctl = appctl
        self.dir = tempfile.mkdtemp(prefix="lastrun")
        os.makedirs(os.path.join(self.dir, "outputs"))
        self.original = appctl.HERE
        appctl.HERE = self.dir

    def tearDown(self):
        self.appctl.HERE = self.original
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write_status(self, started, **kw):
        got = {"started": started, "finished": started, "ok": True,
               "failures": [], "gated": [], "markets": ["US"]}
        got.update(kw)
        with open(os.path.join(self.dir, "outputs", "last_run_status.json"),
                  "w", encoding="utf-8") as fh:
            json.dump(got, fh)

    def test_this_runs_status_is_read_back(self):
        self._write_status("2026-08-24T10:00:00",
                           gated=[{"market": "DE", "reason": "econ_gate"}])
        got = self.appctl._last_run_status(newer_than="2026-08-24T09:59:59")
        self.assertEqual([{"market": "DE", "reason": "econ_gate"}], got["gated"])

    def test_a_file_from_an_earlier_run_is_no_answer(self):
        self._write_status("2026-08-23T10:00:00", ok=False)
        self.assertIsNone(
            self.appctl._last_run_status(newer_than="2026-08-24T10:00:00"),
            "a run that wrote no status must not be reported with the previous "
            "run's outcome")

    def test_no_file_at_all_is_no_answer(self):
        self.assertIsNone(self.appctl._last_run_status())

    def test_health_still_reads_the_newest_whenever_it_was_written(self):
        """System Health asks about the LAST run, not about one it started."""
        self._write_status("2026-08-23T10:00:00")
        self.assertIsNotNone(self.appctl._last_run_status())


if __name__ == "__main__":
    unittest.main()
