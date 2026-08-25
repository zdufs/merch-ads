#!/usr/bin/env python3
"""Numbers the user docs state must be the numbers the code uses.

This is the third time a figure in the documentation went quietly wrong:

  * `docs/SAFETY.md` promised "at most 50,000 changes" long after that had
    become the wrong description of the guard — and it was the SAFETY page,
    read by someone deciding whether to trust automation with their money.
  * Four pages told the reader to expect `Ran 766 tests ... OK` when the suite
    had passed 800. Harmless, but it teaches people that the docs are stale and
    that nothing here is worth re-reading.
  * `docs/superpowers/specs/…` still described the cap as it was designed
    rather than as it shipped.

A prose claim cannot be linted. A NUMBER can, so this pins the handful that
actually matter: the ones a reader would act on.

The anchor is a PHRASE, not the bare number. "3" and "500" appear all over
ordinary prose, so matching a bare figure would pass on a coincidence and prove
nothing. Each check looks for the number inside the words around it, which is
also what makes the failure message useful — it can name the sentence to fix.

Adding a number to the docs? Add it here, or accept that it will be wrong within
a few releases and nobody will notice.

Run from the Ads folder:  python3 -m unittest tests.doc_numbers_lint_tests -v
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))

import db              # noqa: E402
import stream_config   # noqa: E402


def claims():
    """(what it is, the phrase the docs must contain, the files that state it)."""
    return [
        ("db.AUTO_CHANGE_CAP_DEFAULT",
         f"{db.AUTO_CHANGE_CAP_DEFAULT} changes",
         ["README.md", "docs/SAFETY.md"]),
        ("db.AUTO_CHANGE_CAP_DEFAULT (command reference)",
         f"Default **{db.AUTO_CHANGE_CAP_DEFAULT} per market**",
         ["docs/COMMANDS.md"]),
        ("db.SNAPSHOT_RETENTION_DAYS",
         f"{db.SNAPSHOT_RETENTION_DAYS} days",
         ["docs/COMMANDS.md", "docs/ARCHITECTURE.md"]),
        ("db.SNAPSHOT_STALE_AFTER_DAYS",
         f"{db.SNAPSHOT_STALE_AFTER_DAYS} days old",
         ["docs/SAFETY.md"]),
        ("stream_config.AWS_PLAN_EXPIRY",
         str(stream_config.AWS_PLAN_EXPIRY),
         ["docs/marketing-stream.md"]),
        ("stream_config.AWS_PLAN_WARN_DAYS",
         f"{stream_config.AWS_PLAN_WARN_DAYS} days out",
         ["docs/marketing-stream.md", "docs/TROUBLESHOOTING.md"]),
    ]


class TheDocsStateTheRealNumbers(unittest.TestCase):

    def test_every_documented_figure_matches_the_code(self):
        for what, phrase, files in claims():
            for rel in files:
                with self.subTest(constant=what, doc=rel):
                    path = os.path.join(HERE, rel)
                    self.assertTrue(os.path.exists(path), f"{rel} is missing")
                    text = open(path, encoding="utf-8").read()
                    self.assertIn(
                        phrase, text,
                        f"{rel} no longer states {what}. The code says "
                        f"{phrase!r}. Update the sentence, or update this "
                        f"check if the wording moved — but do not leave the "
                        f"page telling the reader a number that is not true.")


class TheLintCannotQuietlyBecomeANoOp(unittest.TestCase):

    def test_there_are_claims_to_check(self):
        rows = claims()
        self.assertGreaterEqual(len(rows), 5,
                                "the claim table shrank — this check would "
                                "pass on almost nothing")
        for what, phrase, files in rows:
            self.assertTrue(files, f"{what} lists no files")
            self.assertTrue(any(ch.isdigit() for ch in phrase),
                            f"{what} anchors on a phrase with no number in it, "
                            f"so it cannot detect drift")

    def test_a_changed_constant_would_be_noticed(self):
        """The point of the whole file, checked directly.

        If the cap moved to 501 the docs would still say 500, and the phrase
        built from the new value would not be found.
        """
        moved = f"{db.AUTO_CHANGE_CAP_DEFAULT + 1} changes"
        text = open(os.path.join(HERE, "docs/SAFETY.md"), encoding="utf-8").read()
        self.assertNotIn(moved, text,
                         "SAFETY.md already contains the phrase for a DIFFERENT "
                         "cap, so this lint could not tell the two apart")
