#!/usr/bin/env python3
"""run_scheduled.sh must resolve code, data and interpreter correctly.

This script has caused one silent five-night outage already (2026-08-16 → 08-20:
the engine moved into engine/, an `import` in the market-discovery snippet broke,
and the run went US-only while reporting "all steps OK"). It now resolves three
things instead of one — SELF, ENGINE and DATA — because the Mac app ships the
code inside its bundle while the databases stay in the operator's folder.

Nothing else tests a bash script here, so this executes only its resolution
head: everything up to and including PYTHONPATH, with `caffeinate` stubbed out.
Cutting there is deliberate — one line further and the test would start pulling
reports from Amazon.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

# timeout: these shell out to appctl against the REAL market database.
# With the app running, its serve workers can hold a lock, and SQLite
# waits forever by default — that is how the suite hung twice with
# nothing but an exit code to show for it. A timeout turns an
# indefinite hang into a named test failure.
SUBPROCESS_TIMEOUT = 60

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "run_scheduled.sh")


def _head_of_script():
    """The script up to the PYTHONPATH export — its whole resolution section."""
    with open(SCRIPT) as fh:
        text = fh.read()
    marker = 'export PYTHONPATH='
    cut = text.index(marker)
    end = text.index("\n", cut)
    return text[:end + 1]


class NightlyPathsTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _layout(self, name, with_python=False):
        """A folder that looks like a checkout (or a bundle's Resources)."""
        root = os.path.join(self.tmp, name)
        os.makedirs(os.path.join(root, "engine"))
        open(os.path.join(root, "engine", "appctl.py"), "w").close()
        if with_python:
            bindir = os.path.join(root, "python", "bin")
            os.makedirs(bindir)
            py = os.path.join(bindir, "python3")
            # A stub that satisfies `import requests`, so the interpreter block
            # keeps this one instead of falling back to the machine's python3.
            with open(py, "w") as fh:
                fh.write('#!/bin/bash\nexit 0\n')
            os.chmod(py, 0o755)
        return root

    def _run(self, script_dir, env=None):
        """Execute the resolution head from script_dir and read the variables back."""
        head = _head_of_script()
        # caffeinate is a real macOS binary but pointless (and noisy) in a test.
        head = head.replace("caffeinate -i -w $$ &", ":")
        probe = head + '\necho "SELF=$SELF"\necho "ENGINE=$ENGINE"\necho "DATA=$DATA"\n' \
                       'echo "PWD=$PWD"\necho "PY=$PY"\necho "PYTHONPATH=$PYTHONPATH"\n'
        path = os.path.join(script_dir, "probe.sh")
        with open(path, "w") as fh:
            fh.write(probe)
        environ = dict(os.environ)
        environ.pop("MERCHADS_DATA_DIR", None)
        environ.pop("ADS_PYTHON", None)
        environ.update(env or {})
        proc = subprocess.run(["bash", path], capture_output=True, text=True, env=environ, timeout=SUBPROCESS_TIMEOUT)
        values = dict(re.findall(r"^(\w+)=(.*)$", proc.stdout, re.M))
        return proc, values

    # --- the checkout, which must behave exactly as it always has -------------

    def testACheckoutPutsCodeAndDataInTheSameFolder(self):
        root = self._layout("checkout")
        proc, v = self._run(root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(v["SELF"], root)
        self.assertEqual(v["ENGINE"], os.path.join(root, "engine"))
        self.assertEqual(v["DATA"], root)
        self.assertEqual(v["PWD"], root, "the run must work from the data folder")
        self.assertTrue(v["PYTHONPATH"].startswith(os.path.join(root, "engine")))

    # --- the bundle ------------------------------------------------------------

    def testTheDataDirSeparatesCodeFromData(self):
        code = self._layout("Resources")
        data = os.path.join(self.tmp, "Ads")
        os.makedirs(data)
        proc, v = self._run(code, {"MERCHADS_DATA_DIR": data})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(v["ENGINE"], os.path.join(code, "engine"),
                         "the modules come from the bundle")
        self.assertEqual(v["DATA"], data)
        self.assertEqual(v["PWD"], data,
                         "outputs/, KILL and NO_DISCORD are relative — cwd must be the data")

    def testABundledInterpreterIsPreferred(self):
        code = self._layout("Resources", with_python=True)
        data = os.path.join(self.tmp, "Ads")
        os.makedirs(data)
        _, v = self._run(code, {"MERCHADS_DATA_DIR": data})
        self.assertEqual(v["PY"], os.path.join(code, "python", "bin", "python3"))

    def testAnExplicitOverrideBeatsTheBundledInterpreter(self):
        code = self._layout("Resources", with_python=True)
        _, v = self._run(code, {"ADS_PYTHON": sys.executable})
        self.assertEqual(v["PY"], sys.executable)

    def testWithNoBundledInterpreterItFallsBackToTheSystem(self):
        code = self._layout("checkout")
        _, v = self._run(code)
        self.assertNotIn("/python/bin/python3", v["PY"])
        self.assertTrue(v["PY"], "a nightly with no interpreter at all is not a valid state")

    # --- fail closed -----------------------------------------------------------

    def testAMissingDataFolderStopsBeforeAnythingRuns(self):
        code = self._layout("Resources")
        proc, _ = self._run(code, {"MERCHADS_DATA_DIR": os.path.join(self.tmp, "not-here")})
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("does not exist", proc.stderr)

    def testAMissingEngineStopsBeforeAnythingRuns(self):
        bare = os.path.join(self.tmp, "bare")
        os.makedirs(bare)
        proc, _ = self._run(bare)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("no engine at", proc.stderr)

    # --- no call site may go back to a relative engine path --------------------

    def testEveryEngineScriptIsLaunchedFromTheResolvedFolder(self):
        """A bare `engine/x.py` reads from the DATA folder, which in a bundle
        holds no code at all. Comments may still say engine/; commands may not."""
        offenders = []
        with open(SCRIPT) as fh:
            for n, line in enumerate(fh, 1):
                if line.lstrip().startswith("#"):
                    continue
                code = line.split("#", 1)[0]
                if re.search(r'(?<![\w/"$])engine/[A-Za-z0-9_]+\.py', code):
                    offenders.append(f"{n}: {line.strip()}")
        self.assertEqual(offenders, [], "these still launch a script relative to the data folder")


if __name__ == "__main__":
    unittest.main()
