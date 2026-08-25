#!/usr/bin/env python3
"""EVERY appctl command answers with exactly one clean JSON envelope.

`tests/serve_protocol_tests.py::OneShotStdoutContract` already does this, for
eight economics-driven commands. That left 97 unchecked, and on 2026-08-22 a
sweep of all of them found six replies that broke the standing rules:

  * `stream-status`, `stream-setup`, `stream-drain` and `seasonal-apply`
    answered `{"ok": false, "error": "[Errno 2] No such file or directory:
    '…/.env'"}` — a raw errno where a sentence belongs.
  * `status` and `backfill-daily` wrap a script and capture its stderr, so the
    reply carried a full Python TRACEBACK, with `code: 1` the only hint that
    anything had gone wrong.

All six were one fault: `ads_client.load_env` raised a bare FileNotFoundError.

Why an EMPTY data folder
------------------------
It is the state a new machine is actually in, and the one nothing was covering:
no `.env`, no databases, no `seasonal.json`, no `rule_defs/`. It is also what
makes this test safe to run. `paths.REPO_ROOT` follows `MERCHADS_DATA_DIR`, and
`ENV_PATH` is derived from it, so with no credentials on disk NOTHING here can
reach Amazon — a command that would normally write to a live account cannot get
past `load_env`. Verified before this test was written, by pointing `status` at
a temp folder and watching it fail on the missing `.env` rather than call out.

`serve` is excluded: it is a long-running line protocol, not a one-shot.

Two lessons from the same day are baked in. Pass an ABSOLUTE script path — the
first version of this sweep ran `engine/appctl.py` with `cwd` set to the temp
folder and every one of the 105 commands returned empty, which read like a total
failure and was a bug in the harness. And pass `stdin=DEVNULL` — `capture_output`
leaves stdin inherited, and a command that reads stdin will sit on the terminal
until the timeout.

Run from the Ads folder:  python3 -m unittest tests.envelope_contract_tests -v
"""

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPCTL = os.path.join(HERE, "engine", "appctl.py")

# Long enough for the slowest read against an EMPTY folder, short enough that a
# hang names itself instead of stalling the suite.
COMMAND_TIMEOUT = 60

# `serve` never returns — it reads requests until its stdin closes.
NOT_ONE_SHOT = {"serve"}


def run_command(cmd, env, cwd):
    """One appctl call, captured. Absolute script path and no inherited stdin.

    Both lessons cost a session. `engine/appctl.py` relative to a temp `cwd`
    returned empty for all 105 commands, which read like a total failure and
    was a bug in the harness; and `capture_output` leaves stdin inherited, so a
    command that reads stdin sits on the terminal until the timeout.
    """
    try:
        p = subprocess.run([sys.executable, APPCTL, cmd],
                           capture_output=True, text=True, env=env,
                           cwd=cwd, timeout=COMMAND_TIMEOUT,
                           stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return {"_timeout": True}
    return {"stdout": p.stdout, "stderr": p.stderr}


def dispatcher_commands():
    with open(APPCTL, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(getattr(t, "id", "") == "DISPATCH" for t in node.targets):
            continue
        return sorted({k.value for k in node.value.keys
                       if isinstance(k, ast.Constant)} - NOT_ONE_SHOT)
    return []


class EveryCommandAnswersOneCleanEnvelope(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.data = tempfile.mkdtemp(prefix="merchads-envelope-")
        cls.pod = tempfile.mkdtemp(prefix="merchads-pod-")
        cls.env = dict(os.environ, ADS_MARKET="US",
                       MERCHADS_DATA_DIR=cls.data, MERCHADS_POD_DIR=cls.pod)
        cmds = dispatcher_commands()
        with ThreadPoolExecutor(max_workers=8) as pool:
            cls.replies = dict(zip(cmds, pool.map(cls._run, cmds)))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.data, ignore_errors=True)
        shutil.rmtree(cls.pod, ignore_errors=True)

    @classmethod
    def _run(cls, cmd):
        return run_command(cmd, cls.env, cls.data)

    def test_the_sweep_actually_ran_every_command(self):
        """If the enumeration breaks, every assertion below passes on nothing."""
        self.assertGreater(len(self.replies), 90,
                           "almost no commands were swept — the DISPATCH parse "
                           "is broken, not the engine")
        for known in ("metrics", "health", "stream-status", "status"):
            self.assertIn(known, self.replies)

    def test_stdout_is_exactly_one_json_object(self):
        for cmd, r in sorted(self.replies.items()):
            with self.subTest(cmd=cmd):
                self.assertNotIn("_timeout", r,
                                 f"`appctl {cmd}` did not finish in "
                                 f"{COMMAND_TIMEOUT}s against an empty folder")
                try:
                    payload = json.loads(r["stdout"])
                except ValueError:
                    self.fail(
                        f"`appctl {cmd}` put something other than one JSON "
                        f"object on stdout. Diagnostics belong on stderr.\n"
                        f"stdout was: {r['stdout'][:400]!r}")
                self.assertIn("ok", payload,
                              f"`appctl {cmd}` stdout is not an envelope")

    def test_no_reply_carries_a_traceback_or_an_errno(self):
        """A missing operator file is a state to REPORT, not an exception.

        The reply has to be readable by whoever is setting the thing up. A
        stack, or `[Errno 2]` with an absolute path, is neither an explanation
        nor something they can act on.
        """
        for cmd, r in sorted(self.replies.items()):
            if "_timeout" in r:
                continue
            with self.subTest(cmd=cmd):
                blob = r["stdout"]
                for smell in ("Traceback (most recent call last)", "[Errno "):
                    self.assertNotIn(
                        smell, blob,
                        f"`appctl {cmd}` leaked {smell.strip()} into the "
                        f"envelope. Fail closed with a sentence instead — see "
                        f"ads_client.load_env for the shape.\n"
                        f"reply was: {blob[:400]!r}")

    def test_no_reply_carries_the_SHAPE_of_a_traceback(self):
        """The word "Traceback" is not the only way a stack gets in.

        `demandfeed` reported a failed refresh as the LAST 300 BYTES of the
        child's stderr. A byte slice cuts the "Traceback (most recent call
        last):" header off and keeps the body, so the reply carried source
        lines, caret markers and absolute file paths while the literal check
        above passed. These two markers are what a traceback body looks like
        when its header is gone.
        """
        for cmd, r in sorted(self.replies.items()):
            if "_timeout" in r:
                continue
            with self.subTest(cmd=cmd):
                blob = r["stdout"]
                for smell in ('File "', ", line "):
                    self.assertNotIn(
                        smell, blob,
                        f"`appctl {cmd}` put the body of a traceback in the "
                        f"envelope. Put the child's output on stderr and send "
                        f"a sentence — see appctl._child_failure_reason.\n"
                        f"reply was: {blob[:400]!r}")


class EveryCommandSurvivesAnObstructedFolder(unittest.TestCase):
    """The same sweep, against a data folder where the writes cannot land.

    An empty folder only ever exercises the happy half of "fail closed". It is
    writable, so every seeding path succeeds and the branch that reports a
    failure is never taken. On 2026-08-24 four commands leaked `[Errno …]` plus
    an absolute path into the envelope from exactly that branch:

      * `seasons` and `seasonal-preview` when `seasonal.json` cannot be written
        or read — the same Seasonal screen the 2026-08-21 audit fixed once for
        the MISSING-file case;
      * `halo`, from a module-level `os.makedirs` in `traz.py` that ran at
        IMPORT time; and
      * `demandfeed`, whose child process died against the unreadable database.

    So the fixture obstructs each of them deliberately: the market database is
    4 KB of random bytes, `seasonal.json` is a DIRECTORY, and `outputs` is a
    regular file. Nothing here can reach Amazon — there is still no `.env`.
    """

    @classmethod
    def setUpClass(cls):
        cls.data = tempfile.mkdtemp(prefix="merchads-obstructed-")
        cls.pod = tempfile.mkdtemp(prefix="merchads-obstructed-pod-")
        with open(os.path.join(cls.data, "ads_data.sqlite"), "wb") as fh:
            fh.write(os.urandom(4096))
        os.mkdir(os.path.join(cls.data, "seasonal.json"))
        with open(os.path.join(cls.data, "outputs"), "w", encoding="utf-8") as fh:
            fh.write("not a directory\n")
        cls.env = dict(os.environ, ADS_MARKET="US",
                       MERCHADS_DATA_DIR=cls.data, MERCHADS_POD_DIR=cls.pod)
        cmds = dispatcher_commands()
        with ThreadPoolExecutor(max_workers=8) as pool:
            cls.replies = dict(zip(cmds, pool.map(cls._run, cmds)))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.data, ignore_errors=True)
        shutil.rmtree(cls.pod, ignore_errors=True)

    @classmethod
    def _run(cls, cmd):
        return run_command(cmd, cls.env, cls.data)

    def test_the_sweep_actually_ran_every_command(self):
        self.assertGreater(len(self.replies), 90,
                           "almost no commands were swept — the DISPATCH parse "
                           "is broken, not the engine")
        for known in ("seasons", "seasonal-preview", "halo", "demandfeed"):
            self.assertIn(known, self.replies)

    def test_the_fixture_really_is_obstructed(self):
        """Otherwise this class is a second copy of the empty-folder sweep."""
        self.assertTrue(os.path.isdir(os.path.join(self.data, "seasonal.json")))
        self.assertTrue(os.path.isfile(os.path.join(self.data, "outputs")))

    def test_stdout_is_exactly_one_json_object(self):
        for cmd, r in sorted(self.replies.items()):
            with self.subTest(cmd=cmd):
                self.assertNotIn("_timeout", r,
                                 f"`appctl {cmd}` did not finish in "
                                 f"{COMMAND_TIMEOUT}s against an obstructed folder")
                try:
                    payload = json.loads(r["stdout"])
                except ValueError:
                    self.fail(
                        f"`appctl {cmd}` put something other than one JSON "
                        f"object on stdout. Diagnostics belong on stderr.\n"
                        f"stdout was: {r['stdout'][:400]!r}")
                self.assertIn("ok", payload,
                              f"`appctl {cmd}` stdout is not an envelope")

    def test_no_reply_carries_a_traceback_or_an_errno(self):
        for cmd, r in sorted(self.replies.items()):
            if "_timeout" in r:
                continue
            with self.subTest(cmd=cmd):
                blob = r["stdout"]
                for smell in ("Traceback (most recent call last)", "[Errno ",
                              'File "', ", line "):
                    self.assertNotIn(
                        smell, blob,
                        f"`appctl {cmd}` leaked {smell.strip()} into the "
                        f"envelope from a path that could not be written. "
                        f"Report the state in a sentence instead.\n"
                        f"reply was: {blob[:400]!r}")
