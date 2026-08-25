#!/usr/bin/env python3
"""The engine has to be told where the data is, and refuse a wrong answer.

`paths.py` used to derive everything from its own `__file__`: the repository is
the folder above the modules, and the POD folder is the one above that. That is
right for a checkout and WRONG the moment `engine/` ships inside the Mac app,
where the folder above the modules is `Contents/Resources` — a real, readable
directory holding no databases at all.

Nothing raised. `appctl metrics` answered `{"ok": true, "empty": true}` for
every market, which is indistinguishable from an account that has not pulled
yet. So the environment override exists, and a folder that is named but not
there STOPS the process instead of quietly reading nothing.
"""

import importlib
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))

import paths as _paths  # noqa: E402  (path set up above)


class PathsDataDirTests(unittest.TestCase):

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ("MERCHADS_DATA_DIR", "MERCHADS_POD_DIR")}

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        importlib.reload(_paths)          # leave the module as the rest of the suite expects

    def _reload(self, **env):
        for k in ("MERCHADS_DATA_DIR", "MERCHADS_POD_DIR"):
            os.environ.pop(k, None)
        for k, v in env.items():
            os.environ[k] = v
        return importlib.reload(_paths)

    # --- the checkout layout, which must keep working exactly as before -------

    def testWithNoEnvTheRootsComeFromTheModuleLocation(self):
        p = self._reload()
        self.assertEqual(p.REPO_ROOT, os.path.dirname(p.ENGINE_DIR))
        self.assertEqual(p.POD_ROOT, os.path.dirname(p.REPO_ROOT))
        self.assertTrue(os.path.isfile(os.path.join(p.ENGINE_DIR, "appctl.py")),
                        "the derived engine dir must still be the real one")

    def testAnEmptyValueIsTreatedAsUnset(self):
        p = self._reload(MERCHADS_DATA_DIR="   ")
        self.assertEqual(p.REPO_ROOT, os.path.dirname(p.ENGINE_DIR))

    # --- the bundled layout ---------------------------------------------------

    def testTheDataDirOverridesTheRepoRoot(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._reload(MERCHADS_DATA_DIR=d)
            self.assertEqual(p.REPO_ROOT, os.path.realpath(d))
            self.assertEqual(p.repo("ads_data.sqlite"),
                             os.path.join(os.path.realpath(d), "ads_data.sqlite"))

    def testThePodRootDefaultsToTheFolderAboveTheData(self):
        with tempfile.TemporaryDirectory() as d:
            inner = os.path.join(d, "Ads")
            os.mkdir(inner)
            p = self._reload(MERCHADS_DATA_DIR=inner)
            self.assertEqual(p.POD_ROOT, os.path.realpath(d))

    def testThePodRootCanBeSetOnItsOwn(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as pod:
            p = self._reload(MERCHADS_DATA_DIR=data, MERCHADS_POD_DIR=pod)
            self.assertEqual(p.REPO_ROOT, os.path.realpath(data))
            self.assertEqual(p.POD_ROOT, os.path.realpath(pod))
            self.assertEqual(p.pod("SALES_REPORT-1_1_26-1_31_26.csv"),
                             os.path.join(os.path.realpath(pod), "SALES_REPORT-1_1_26-1_31_26.csv"))

    def testATildeIsExpanded(self):
        p = self._reload(MERCHADS_DATA_DIR="~")
        self.assertEqual(p.REPO_ROOT, os.path.realpath(os.path.expanduser("~")))

    # --- fail closed ----------------------------------------------------------

    def testAMissingDataFolderStopsTheProcess(self):
        """The whole point. A typo must not read as "no data yet"."""
        with tempfile.TemporaryDirectory() as d:
            missing = os.path.join(d, "not-here")
            with self.assertRaises(SystemExit) as caught:
                self._reload(MERCHADS_DATA_DIR=missing)
            self.assertIn("MERCHADS_DATA_DIR", str(caught.exception))
            self.assertIn(missing, str(caught.exception))

    def testAMissingPodFolderStopsTheProcess(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit) as caught:
                self._reload(MERCHADS_DATA_DIR=d, MERCHADS_POD_DIR=os.path.join(d, "nope"))
            self.assertIn("MERCHADS_POD_DIR", str(caught.exception))

    def testAFileWhereAFolderBelongsStopsTheProcess(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "ads_data.sqlite")
            open(f, "w").close()
            with self.assertRaises(SystemExit):
                self._reload(MERCHADS_DATA_DIR=f)

    # --- the case a missing-folder check cannot catch --------------------------

    def testABundleWithNoDataDirStopsTheProcess(self):
        """The one place the fallback is wrong AND does not raise.

        `Merch Ads.app/Contents/Resources` exists and is readable and holds no
        databases, so a caller who forgot to name the data folder got
        `{"ok": true, "empty": true}` for every market — indistinguishable from
        an account that has never pulled. That is exactly what happened when the
        serve worker was spawned without MERCHADS_DATA_DIR while the one-shot
        path had it: the command line was right and every screen was empty.
        """
        with tempfile.TemporaryDirectory() as d:
            engine = os.path.join(d, "Merch Ads.app", "Contents", "Resources", "engine")
            os.makedirs(engine)
            with self.assertRaises(SystemExit) as caught:
                _paths._rooted("MERCHADS_DATA_DIR", os.path.dirname(engine))
            self.assertIn("app bundle", str(caught.exception))

    def testANamedDataDirWorksFromInsideABundle(self):
        """Refusing the guess must not refuse the answer."""
        with tempfile.TemporaryDirectory() as d:
            data = os.path.join(d, "Ads")
            os.mkdir(data)
            os.environ["MERCHADS_DATA_DIR"] = data
            try:
                resolved = _paths._rooted("MERCHADS_DATA_DIR",
                                          "/Applications/Merch Ads.app/Contents/Resources")
            finally:
                os.environ.pop("MERCHADS_DATA_DIR", None)
            self.assertEqual(resolved, os.path.realpath(data))

    def testAnOrdinaryFolderIsNotMistakenForABundle(self):
        self.assertFalse(_paths._inside_app_bundle("/Users/USERNAME/Biznis/ClaudeCode/POD/Ads"))
        self.assertFalse(_paths._inside_app_bundle("/Users/USERNAME/apps/Ads"))
        self.assertTrue(_paths._inside_app_bundle("/Applications/Merch Ads.app/Contents/Resources"))
        self.assertTrue(_paths._inside_app_bundle("/x/Some.app/Contents/Resources/engine"))


if __name__ == "__main__":
    unittest.main()
