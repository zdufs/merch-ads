#!/usr/bin/env python3
"""Every script appctl launches must actually exist where appctl looks for it.
Run from the Ads folder:  python3 -m unittest tests.engine_script_path_tests -v

The engine modules live in engine/, the repo root holds run_scheduled.sh, the
databases and outputs/. appctl built subprocess paths as repo_root/<name> for
months after the move — so `adopt-export` marked US economics STALE on every
run, `demandfeed --refresh` served a stale file in silence, and `status`,
`run --phase`, `backfill-daily` and `promote` failed outright. Nothing caught
it, because a wrong path does not raise at import time; the subprocess simply
returns a non-zero code that the caller shrugs off.

This is the guard. It reads appctl.py as text, so it sees the paths the code
will really build.
"""

import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(HERE, "engine")
sys.path.insert(0, ENGINE)
import paths  # noqa: E402  (needs ENGINE on the path first)
APPCTL = os.path.join(ENGINE, "appctl.py")


def source():
    with open(APPCTL, encoding="utf-8") as fh:
        return fh.read()


class EngineScriptPathTests(unittest.TestCase):
    def test_every_named_engine_script_exists(self):
        names = set(re.findall(r'_engine_script\("([^"]+)"\)', source()))
        self.assertTrue(names, "no _engine_script() calls found — did the helper get renamed?")
        for name in sorted(names):
            self.assertTrue(os.path.exists(os.path.join(ENGINE, name)),
                            f"appctl launches engine/{name}, which does not exist")

    def test_every_phase_script_exists(self):
        """cmd_run maps a phase name to a script; those go through the same
        helper as a variable, so the literals live in the map instead."""
        block = re.search(r'script = \{(.*?)\}\.get\(args\.phase\)', source(), re.S)
        self.assertIsNotNone(block, "cmd_run's phase map moved — update this test")
        for name in re.findall(r'"([a-z_0-9]+\.py)"', block.group(1)):
            self.assertTrue(os.path.exists(os.path.join(ENGINE, name)),
                            f"phase map points at engine/{name}, which does not exist")

    def test_builder_scripts_exist(self):
        for name in ("lottery_build.py", "scavenger_build.py"):
            self.assertTrue(os.path.exists(os.path.join(ENGINE, name)), name)

    def test_no_engine_script_is_launched_from_the_repo_root(self):
        """The defect itself: os.path.join(HERE, "<something>.py") builds a path
        in the repo root, where no engine module lives."""
        stray = re.findall(r'os\.path\.join\(HERE, ["\']([a-z_0-9]+\.py)["\']\)', source())
        self.assertEqual(stray, [],
                         f"launched from the repo root instead of engine/: {stray}")

    def test_the_shell_runner_is_found_beside_the_engine_not_in_the_data_folder(self):
        """This test used to PIN the bug.

        It asserted that the source contains `os.path.join(HERE, "run_scheduled.sh")`
        and called that "the one thing correctly addressed from HERE". HERE is
        the DATA folder. Since 2026-08-21 the app is standalone: it ships the
        script at Contents/Resources/run_scheduled.sh and the data folder holds
        only databases. A standalone install therefore ran a script that is not
        there and got exit 127, and this test stayed green because a checkout
        happens to keep both in the same place.

        run_scheduled.sh sits BESIDE the engine folder in both layouts:
        repo root in a checkout, Contents/Resources in the bundle.
        """
        import appctl
        beside = os.path.join(os.path.dirname(paths.ENGINE_DIR), "run_scheduled.sh")
        self.assertTrue(os.path.exists(beside),
                        f"run_scheduled.sh is not beside the engine at {beside}")
        self.assertEqual(beside, appctl._nightly_script())

    def test_the_runner_path_does_not_come_from_the_data_folder(self):
        """A checkout makes the two the same folder, so equality proves nothing.

        Point the data folder somewhere else and the answer must not move.
        """
        import appctl
        beside = os.path.join(os.path.dirname(paths.ENGINE_DIR), "run_scheduled.sh")
        original = appctl.HERE
        try:
            appctl.HERE = tempfile.mkdtemp()      # a data folder with no checkout
            self.assertEqual(beside, appctl._nightly_script(),
                             "the nightly script was resolved from the data "
                             "folder, which is empty in a standalone install")
        finally:
            appctl.HERE = original



class IntakeCsvResolutionTests(unittest.TestCase):
    """Dropping an export MOVES it into the POD folder and then rebuilds the
    product map, which takes minutes. A build started in that window still
    carried the pre-move path and died with "no such file"."""

    def setUp(self):
        sys.path.insert(0, ENGINE)
        os.environ.setdefault("ADS_MARKET", "US")
        import appctl
        self.appctl = appctl
        self.pod = appctl.paths.POD_ROOT

    def test_an_existing_path_is_returned_untouched(self):
        self.assertEqual(self.appctl._resolve_intake_csv(APPCTL), APPCTL)

    def test_a_moved_export_is_followed_into_the_catalog_folder(self):
        name = "snap-grid-export-2026-01-01_00-00-00.csv"
        planted = os.path.join(self.pod, name)
        if os.path.exists(planted):
            self.skipTest("a real export already uses this name")
        with open(planted, "w", encoding="utf-8") as fh:
            fh.write("Marketplace,ASIN,Status,Product Type,Design ID\n")
        try:
            stale = os.path.join(os.path.expanduser("~/Desktop"), name)
            self.assertEqual(self.appctl._resolve_intake_csv(stale), planted)
        finally:
            os.remove(planted)

    def test_an_unknown_file_is_left_alone_so_the_error_still_names_it(self):
        missing = "/tmp/definitely-not-an-export-9f3a1c.csv"
        self.assertEqual(self.appctl._resolve_intake_csv(missing), missing)

if __name__ == "__main__":
    unittest.main()
