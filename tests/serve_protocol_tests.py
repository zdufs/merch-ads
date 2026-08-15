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

Run from the Ads folder:  python3 -m unittest tests.serve_protocol_tests -v"""

import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["ADS_MARKET"] = "US"

import db        # noqa: E402
import appctl    # noqa: E402
import phase2_apply  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
