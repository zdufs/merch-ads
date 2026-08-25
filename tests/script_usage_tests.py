#!/usr/bin/env python3
"""No engine script answers a foreseeable state with a stack trace.

CLAUDE.md states the rule for the JSON bridge: "A missing operator file is a
state to REPORT, not an exception to leak." The command-line scripts are held
to the same standard and were not meeting it. Run against a fresh checkout with
no `.env` and no databases — which is precisely a new install — seven of them
printed a Python traceback:

    export_reader.py      IndexError, and FileNotFoundError for `--help`
    export_snapshot.py    ValueError: no ISO date in filename: --help
    get_token.py          EOFError when not run from a terminal
    history_import.py     FileNotFoundError for `--help`
    inspect_accounts.py   FileNotFoundError for a missing .env
    notify_discord.py     FileNotFoundError for a missing .env
    sales_import.py       FileNotFoundError for `--help`

Four of those were `--help`, which is the worst possible case: somebody asking
how a tool works, answered with a stack trace. `inspect_accounts.py` is a
DIAGNOSTIC, so it is what a stuck newcomer reaches for, and it crashed on the
most ordinary state there is — not having set up credentials yet.

None of this is a crash in the engine. It is what the project looks like to
somebody on their first day, which since the repository went public is a thing
that has readers.

This runs each script in a temporary directory with no `.env` and no data, and
fails on any traceback. Scripts that need Amazon cannot reach it: with no
credentials they stop at the first check.

Run from the Ads folder:  python3 -m unittest tests.script_usage_tests -v
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(HERE, "engine")

# Not scripts: imported as modules, no __main__ path worth driving.
SKIP = {"__init__.py", "paths.py"}


def engine_scripts():
    return sorted(f for f in os.listdir(ENGINE)
                  if f.endswith(".py") and f not in SKIP)


class NoScriptLeaksATraceback(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        """A sandbox holding the code and nothing else — no .env, no
        databases, no exports. The state a repository is cloned into."""
        cls.tmp = tempfile.mkdtemp(prefix="merchads-usage-")
        shutil.copytree(ENGINE, os.path.join(cls.tmp, "engine"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        cls.env = {**os.environ,
                   "MERCHADS_DATA_DIR": cls.tmp,
                   "MERCHADS_POD_DIR": cls.tmp,
                   "ADS_MARKET": "US"}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _run(self, script, args):
        return subprocess.run(
            [sys.executable, os.path.join(self.tmp, "engine", script), *args],
            capture_output=True, text=True, cwd=self.tmp, env=self.env,
            stdin=subprocess.DEVNULL, timeout=120)

    def test_the_scan_finds_the_scripts(self):
        """A lint that reads an empty directory passes forever."""
        self.assertGreater(len(engine_scripts()), 30,
                           "found almost no engine scripts — this has stopped "
                           "matching the layout")

    def test_help_never_answers_with_a_stack_trace(self):
        """`--help` is the question "how do I use this". A traceback is the
        worst available answer, and four scripts gave one."""
        bad = []
        for script in engine_scripts():
            try:
                r = self._run(script, ["--help"])
            except subprocess.TimeoutExpired:
                bad.append(f"{script}: timed out")
                continue
            if "Traceback (most recent call last)" in (r.stderr or ""):
                bad.append(f"{script}: {(r.stderr.strip().splitlines() or [''])[-1]}")
        self.assertEqual(bad, [], "these answered --help with a traceback:\n  "
                                  + "\n  ".join(bad))

    def test_no_arguments_never_answers_with_a_stack_trace(self):
        """A fresh clone with no `.env` and no databases is the state every
        new reader starts in."""
        bad = []
        for script in engine_scripts():
            try:
                r = self._run(script, [])
            except subprocess.TimeoutExpired:
                bad.append(f"{script}: timed out")
                continue
            if "Traceback (most recent call last)" in (r.stderr or ""):
                bad.append(f"{script}: {(r.stderr.strip().splitlines() or [''])[-1]}")
        self.assertEqual(bad, [], "these crashed on a bare run in a fresh "
                                  "checkout:\n  " + "\n  ".join(bad))

    def test_a_missing_env_is_explained_rather_than_raised(self):
        """Named outright for the two that read credentials directly, because
        a diagnostic that crashes on a missing .env is useless to exactly the
        person running a diagnostic."""
        for script in ("inspect_accounts.py", "notify_discord.py"):
            with self.subTest(script=script):
                r = self._run(script, [])
                self.assertNotIn("Traceback", r.stderr or "")
                self.assertIn(".env", (r.stderr or "") + (r.stdout or ""),
                              f"{script} must say WHICH file it wanted")


if __name__ == "__main__":
    unittest.main()
