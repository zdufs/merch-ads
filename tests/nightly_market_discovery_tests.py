#!/usr/bin/env python3
"""The nightly's inline python snippets must import the engine.

Run from the Ads folder:
    python3 -m unittest tests.nightly_market_discovery_tests -v

run_scheduled.sh launches every engine script by path (engine/phase0_pull.py),
but it also runs three short `"$PY" -c "import ads_client,markets; ..."`
snippets: the market list, the per-market KDP check, and the KDP block's own
check. Those import instead of launching, and the repo root is not on
sys.path since the engine moved into engine/ (2026-08-15).

The import failed, its stderr went to /dev/null, and the empty market list
silently fell back to US. Five nights (16-20 Aug 2026) pulled US only while
the run still reported "all steps OK" — UK, DE, FR, ES, IT and USKDP got
nothing and nobody was told.

This is the guard. It runs each snippet exactly as the shell does: from the
repo root, with the same interpreter.
"""

import os
import re
import subprocess
import sys
import unittest

# timeout: these shell out to appctl against the REAL market database.
# With the app running, its serve workers can hold a lock, and SQLite
# waits forever by default — that is how the suite hung twice with
# nothing but an exit code to show for it. A timeout turns an
# indefinite hang into a named test failure.
SUBPROCESS_TIMEOUT = 60

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER = os.path.join(HERE, "run_scheduled.sh")


def script():
    with open(RUNNER, encoding="utf-8") as fh:
        return fh.read()


def inline_snippets():
    """Every `"$PY" -c "…"` in the nightly, with $M standing in as a market."""
    found = re.findall(r'"\$PY" -c "([^"]+)"', script())
    return [s.replace("$M", "US") for s in found]


def shell_env():
    """The environment the shell hands those snippets, PYTHONPATH included.

    The nightly resolves the modules into `$ENGINE` now, because the app runs
    the same script from inside its bundle where the code and the data are two
    different folders. Both spellings are substituted so this test keeps
    checking what it has always checked: that the inline snippets can actually
    import the engine. They could not, for five nights, and the run went US-only
    without saying so.
    """
    env = dict(os.environ)
    exported = re.search(r'export PYTHONPATH="([^"]+)"', script())
    if exported:
        path = exported.group(1)
        path = path.replace("$ENGINE", os.path.join(HERE, "engine")).replace("$PWD", HERE)
        path = re.sub(r'\$\{PYTHONPATH:[^}]*\}', "", path)
        assert "$" not in path, f"an unresolved shell variable is left in PYTHONPATH: {path}"
        env["PYTHONPATH"] = path
    return env


class NightlyInlineSnippetTests(unittest.TestCase):
    def test_snippets_are_still_there(self):
        self.assertGreaterEqual(len(inline_snippets()), 3,
                                "the nightly's inline python calls moved — update this test")

    def test_every_snippet_imports_the_engine_from_the_repo_root(self):
        for snippet in inline_snippets():
            with self.subTest(snippet=snippet[:60]):
                # stdin=DEVNULL is load-bearing. One snippet begins
                # `json.load(sys.stdin)`, and capture_output only redirects
                # stdout and stderr — stdin stays INHERITED. Run from a
                # terminal the child then waits on the tty for 60 seconds and
                # the whole suite jumps from 13s to 73s before this fails.
                # Run from a pipe already at EOF it passes instantly, which is
                # why it looked like a flake: it failed three times in one
                # session on 2026-08-22 and passed every time it was re-run on
                # its own. The header below blames SQLite locks, which is the
                # right worry for the OTHER snippets and was the wrong
                # diagnosis for this one. Empty stdin gives a JSONDecodeError,
                # and this test only ever asked about ModuleNotFoundError.
                run = subprocess.run([sys.executable, "-c", snippet],
                                     cwd=HERE, env=shell_env(),
                                     stdin=subprocess.DEVNULL,
                                     capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT)
                self.assertNotIn("ModuleNotFoundError", run.stderr,
                                 f"the nightly cannot import the engine: {run.stderr.strip()}")

    def test_the_market_list_is_not_silently_narrowed_to_us(self):
        """A failed discovery used to fall back to US with no trace. It must
        now record a failure, so the run reports it and System Health shows it."""
        fallback = re.search(r'MARKETS="US"(.*?)\nfi', script(), re.S)
        self.assertIsNotNone(fallback, "the US fallback moved — update this test")
        self.assertIn("FAILURES", fallback.group(1),
                      "a failed market discovery must be recorded in FAILURES, not swallowed")


if __name__ == "__main__":
    unittest.main()
