#!/usr/bin/env python3
"""The serve line-protocol must emit EXACTLY one envelope line per request.

The app keeps one long-running `appctl.py serve` worker per market and reads
one stdout line per request it sends. If any handler prints a stray line to
stdout (a warning, a progress note), that line lands in the pipe BEFORE the
JSON envelope, the app reads it as the reply, and every following reply is
shifted by one for the life of the worker — each screen then decodes some
OTHER command's envelope and shows a "contract mismatch". That is exactly what
happened to USKDP in Aug 2026: its search_term_perf / targeting_perf tables are
empty (KDP has no phase-2 pull), so phase2_apply.candidates() printed two
"DATA STALE" lines to stdout and desynced the whole worker.

These tests pin the invariant at two layers:
  1. candidates() writes its gate diagnostics to stderr, never stdout.
  2. cmd_serve emits one and only one envelope per request even when a handler
     prints stray stdout — the deterministic guard, so no future stray print
     anywhere in the call tree can ever cascade again.
  3. the ONE-SHOT path keeps the same promise. The serve worker sinks stray
     stdout, so a print that leaks there is invisible; run the same command
     directly and the prose lands in front of the envelope. That is how
     harvest_prune's econ-gate notice survived unnoticed until the 2026-08-21
     audit — the app only coped because its decoder rescans lines, and nothing
     else that reads the contract would.

Run from the Ads folder:  python3 -m unittest tests.serve_protocol_tests -v"""

import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ["ADS_MARKET"] = "US"

import db        # noqa: E402
import appctl    # noqa: E402
import phase2_apply  # noqa: E402

# timeout: these shell out to appctl against the REAL market database.
# With the app running, its serve workers can hold a lock, and SQLite
# waits forever by default — that is how the suite hung twice with
# nothing but an exit code to show for it. A timeout turns an
# indefinite hang into a named test failure.
SUBPROCESS_TIMEOUT = 60


def temp_conn():
    """A fresh, schema-only market DB — empty perf tables, like a brand-new
    market (USKDP). db.connect() builds the schema on the temp file."""
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    real = db.DB_PATH
    db.DB_PATH = path
    conn = db.connect()
    db.DB_PATH = real
    return conn, path


def drive_serve(requests, db_path):
    """Run cmd_serve in-process against db_path, feeding newline-delimited JSON
    argv arrays on stdin and capturing what reaches the app's pipe (stdout).
    cmd_serve pins whatever sys.stdout is at entry as the real pipe, so the
    capture buffer plays the app's role exactly."""
    real_path = db.DB_PATH
    old_in, old_out = sys.stdin, sys.stdout
    cap = io.StringIO()
    db.DB_PATH = db_path
    sys.stdin = io.StringIO("".join(json.dumps(r) + "\n" for r in requests))
    sys.stdout = cap
    try:
        appctl.cmd_serve(None)
    finally:
        sys.stdin, sys.stdout = old_in, old_out
        db.DB_PATH = real_path
    return cap.getvalue()


class CandidatesStdout(unittest.TestCase):
    def test_gate_diagnostics_never_touch_stdout(self):
        """The root cause: candidates() on an empty market must not print its
        gate warnings to stdout (they go to stderr)."""
        conn, path = temp_conn()
        try:
            buf = io.StringIO()
            old = sys.stdout
            sys.stdout = buf
            try:
                phase2_apply.candidates(conn)
            finally:
                sys.stdout = old
            self.assertEqual(
                buf.getvalue(), "",
                "candidates() wrote to stdout — this desyncs the serve pipe:\n"
                + repr(buf.getvalue()))
        finally:
            conn.close()
            os.unlink(path)


class ServeProtocol(unittest.TestCase):
    def test_one_envelope_per_request_no_desync(self):
        """The guard: even the stray-printing command yields exactly one
        envelope, and every reply stays matched to its request."""
        conn, path = temp_conn()
        conn.close()
        try:
            requests = [
                ["campaigns"],          # clean control
                ["negatives-preview"],  # the historical stray-printer
                ["campaigns"],          # must still be routed correctly after it
                ["negatives-preview"],
            ]
            raw = drive_serve(requests, path)
            lines = [ln for ln in raw.splitlines() if ln.strip()]

            # No desync: one line out per line in.
            self.assertEqual(
                len(lines), len(requests),
                f"expected {len(requests)} envelope lines, got {len(lines)}:\n" + raw)

            # Every line is a valid envelope (a stray non-JSON line fails here).
            parsed = [json.loads(ln) for ln in lines]
            for p in parsed:
                self.assertTrue(p.get("ok"), f"unexpected error envelope: {p}")

            # Correct routing: each reply carries its own command's shape.
            self.assertIn("campaigns", parsed[0]["data"])
            self.assertIn("negatives", parsed[1]["data"])
            self.assertIn("campaigns", parsed[2]["data"])
            self.assertIn("negatives", parsed[3]["data"])
        finally:
            os.unlink(path)

    def test_a_failed_request_also_emits_exactly_one_line(self):
        """The other half of the invariant, and it was broken for months.

        The stray-print sink covered handlers that SUCCEED. A request that
        FAILED printed its error envelope and then fell through to the "no
        response produced" backstop, because neither handler set the responded
        flag — two lines for one request, and every reply after it on that
        worker belonged to the previous one. No error, no crash, just the wrong
        numbers under the right heading until the app was restarted.

        In practice it fired while the nightly held the database, which is the
        one time of day the operator is most likely to be looking.
        """
        conn, path = temp_conn()
        conn.close()
        try:
            requests = [
                ["campaigns"],        # clean
                ["no-such-command"],  # argparse rejects it
                ["campaigns"],        # must still be THIS request's reply
                ["targets"],          # a real command missing its required flag
                ["campaigns"],
            ]
            raw = drive_serve(requests, path)
            lines = [ln for ln in raw.splitlines() if ln.strip()]
            self.assertEqual(
                len(lines), len(requests),
                f"expected {len(requests)} lines, got {len(lines)} — the pipe is desynced:\n" + raw)

            parsed = [json.loads(ln) for ln in lines]
            self.assertIn("campaigns", parsed[0].get("data", {}))
            self.assertFalse(parsed[1]["ok"])
            self.assertIn("no-such-command", parsed[1]["error"],
                          "the reply must name what was wrong, not just 'bad arguments'")
            self.assertIn("campaigns", parsed[2].get("data", {}),
                          "the reply after a failure belongs to the request that asked for it")
            self.assertFalse(parsed[3]["ok"])
            self.assertIn("campaigns", parsed[4].get("data", {}))
        finally:
            os.unlink(path)

    def test_an_unknown_market_is_an_envelope_not_plain_text(self):
        """`ADS_MARKET=ZZ` raised SystemExit out of db.py's module body, before
        there was a dispatcher to wrap it. The operator saw an exit code and the
        sentence explaining it went to a stderr tail."""
        import subprocess
        engine = os.path.join(HERE, "engine")
        proc = subprocess.run(
            [sys.executable, os.path.join(engine, "appctl.py"), "metrics"],
            capture_output=True, text=True,
            env=dict(os.environ, ADS_MARKET="NOSUCHMARKET"), cwd=HERE, timeout=SUBPROCESS_TIMEOUT)
        payload = json.loads(proc.stdout)
        self.assertFalse(payload["ok"])
        self.assertIn("NOSUCHMARKET", payload["error"])


class OneShotStdoutContract(unittest.TestCase):
    """One JSON object on stdout, from a plain `appctl.py <cmd>` run.

    Every command here is economics-driven, so pointing the engine at an EMPTY
    data folder closes the econ gate and takes the branch that used to print.
    That is the condition the operator will actually meet on a new machine, and
    it is the one nothing was covering.
    """

    ECON_DRIVEN = ["harvest-prune", "negatives-preview", "killlist",
                   "profit", "resetbids", "econ-gate", "seasons",
                   "seasonal-preview"]

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.pod = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.pod, ignore_errors=True)

    def run_one(self, cmd):
        import subprocess
        engine = os.path.join(HERE, "engine")
        return subprocess.run(
            [sys.executable, os.path.join(engine, "appctl.py")] + cmd.split(),
            capture_output=True, text=True, cwd=self.tmp,
            env=dict(os.environ, ADS_MARKET="US",
                     MERCHADS_DATA_DIR=self.tmp, MERCHADS_POD_DIR=self.pod),
            timeout=SUBPROCESS_TIMEOUT)

    def test_stdout_is_exactly_one_json_object_with_the_econ_gate_closed(self):
        for cmd in self.ECON_DRIVEN:
            with self.subTest(cmd=cmd):
                proc = self.run_one(cmd)
                try:
                    payload = json.loads(proc.stdout)
                except ValueError:
                    self.fail(
                        f"`appctl {cmd}` put something other than one JSON "
                        f"object on stdout. Diagnostics belong on stderr.\n"
                        f"stdout was: {proc.stdout[:400]!r}")
                self.assertIn("ok", payload,
                              f"`appctl {cmd}` stdout is not an envelope")

    def test_a_fresh_data_folder_never_answers_with_a_python_traceback(self):
        """A missing operator file is a state to report, not an exception to
        leak. `seasons` returned a raw FileNotFoundError with an absolute path
        until this audit, and the Seasonal screen showed it verbatim."""
        for cmd in self.ECON_DRIVEN:
            with self.subTest(cmd=cmd):
                payload = json.loads(self.run_one(cmd).stdout)
                if payload.get("ok"):
                    continue
                self.assertNotIn("Errno", payload.get("error", ""),
                                 f"`appctl {cmd}` leaked a filesystem error")
                self.assertNotIn("Traceback", payload.get("error", ""))


if __name__ == "__main__":
    unittest.main()
