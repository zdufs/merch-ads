#!/usr/bin/env python3
"""The app's Run button must give a whole-account run a whole run's time.

Found by mutation on 2026-08-24. Swapping the two constants in `cmd_run` broke
nothing in the whole suite, and the failure it causes is the one this ceiling
was raised to stop: the button killing a healthy nightly partway through, after
some markets had written to the live account and before the rest had run at all.

The measured evidence is asserted, not only the ordering. The last complete
nightly ran 9,793 seconds across seven markets and succeeded, so a ceiling that
does not clear that number is not a ceiling, it is a timer. Ordering alone would
pass with both values set to a minute.

Run from the Ads folder:  python3 -m unittest tests.run_timeout_tests -v
"""

import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ.setdefault("ADS_MARKET", "US")

import appctl  # noqa: E402

# 2026-08-23 10:00:03 -> 12:43:16, all seven markets, exit 0.
LONGEST_REAL_NIGHTLY_SECS = 9793


class _FakeProc:
    returncode = 0

    def __init__(self, seen):
        self._seen = seen

    def communicate(self, timeout=None):
        self._seen["timeout"] = timeout
        return ("", "")


def timeout_for(phase):
    """What cmd_run would hand to communicate() for this phase."""
    seen = {}
    with mock.patch.object(appctl.subprocess, "Popen",
                           lambda *a, **k: _FakeProc(seen)), \
         mock.patch.object(appctl, "out", lambda d: None), \
         mock.patch.object(appctl, "_check_econ_gate", lambda *a, **k: None), \
         mock.patch.object(appctl, "_nightly_script", lambda: "/bin/true"), \
         mock.patch.object(appctl, "_engine_script", lambda s: "/bin/true"):
        appctl.cmd_run(type("Args", (), {"phase": phase})())
    return seen["timeout"]


class TheRunButtonGivesAFullRunAFullRunsTime(unittest.TestCase):

    def test_a_full_run_gets_the_full_run_ceiling(self):
        self.assertEqual(timeout_for(None), appctl.FULL_RUN_TIMEOUT_SECS)

    def test_one_phase_gets_the_phase_ceiling(self):
        self.assertEqual(timeout_for("phase3"), appctl.PHASE_TIMEOUT_SECS)

    def test_the_full_ceiling_clears_a_real_nightly(self):
        self.assertGreater(
            appctl.FULL_RUN_TIMEOUT_SECS, LONGEST_REAL_NIGHTLY_SECS,
            "the ceiling must clear the longest run actually observed, or it "
            "will kill a healthy one partway through")
        self.assertGreater(appctl.FULL_RUN_TIMEOUT_SECS,
                           appctl.PHASE_TIMEOUT_SECS)


if __name__ == "__main__":
    unittest.main()
