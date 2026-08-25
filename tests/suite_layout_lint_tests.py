#!/usr/bin/env python3
"""Nothing may sit after a test file's `if __name__ == "__main__"` block.

`unittest.main()` collects the classes defined SO FAR and then exits. A class
appended below that line is invisible to `python3 tests/<file>.py`, and silently
so: the run reports OK and a smaller number nobody is comparing against
anything.

Found 2026-08-24 in `fail_closed_tests.py`, which grew six classes past its
main block — the write cap, the economics gate and the approval queue's
stale-evidence guard among them. Running the file the obvious way executed 27
of its 53 tests. The same shape was in fourteen other files.

CI is unaffected: `.github/workflows/tests.yml` uses `unittest discover`, which
imports the module and never runs the main block. That is exactly what made it
survive — the only person it lied to was whoever ran the file directly, which
is what everybody does while writing one.

Run from the Ads folder:  python3 -m unittest tests.suite_layout_lint_tests -v
"""

import ast
import glob
import os
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(HERE, "tests")


def _test_files():
    return sorted(glob.glob(os.path.join(TESTS, "*_tests.py")))


def stranded_after_main(source, path="<memory>"):
    """Names of the top-level statements that follow the main block.

    Empty when there is no main block, or when it is the last thing in the
    file — which is the only correct place for it.
    """
    body = ast.parse(source, path).body
    guards = [i for i, n in enumerate(body)
              if isinstance(n, ast.If) and "__main__" in ast.unparse(n.test)]
    if not guards:
        return []
    return [getattr(n, "name", type(n).__name__)
            for n in body[guards[-1] + 1:]]


class EveryTestInAFileRunsWhenTheFileIsRun(unittest.TestCase):

    def test_no_file_defines_anything_below_its_main_block(self):
        offenders = []
        for path in _test_files():
            with open(path, encoding="utf-8") as fh:
                stranded = stranded_after_main(fh.read(), path)
            if stranded:
                offenders.append(
                    f"  tests/{os.path.basename(path)}: {len(stranded)} "
                    f"statement(s) never reached by `python3` on this file — "
                    f"{', '.join(stranded[:6])}")
        self.assertEqual(
            [], offenders,
            "unittest.main() collects what is defined above it and exits, so "
            "these are skipped without saying so. Move the main block to the "
            "end of the file:\n" + "\n".join(offenders))


class TheCheckItselfWorks(unittest.TestCase):
    """A lint that reads an empty folder passes forever and says nothing."""

    def test_it_actually_reads_the_suite(self):
        files = _test_files()
        self.assertGreater(len(files), 80, "the walk found almost no test files")
        with_main = []
        for path in files:
            with open(path, encoding="utf-8") as fh:
                if "__main__" in fh.read():
                    with_main.append(path)
        self.assertGreater(len(with_main), 60,
                           "almost no file was seen carrying a main block — "
                           "the matcher stopped recognising the shape")

    def test_it_reports_a_class_left_below_the_main_block(self):
        src = ('import unittest\n'
               '\n'
               '\n'
               'class Early(unittest.TestCase):\n'
               '    pass\n'
               '\n'
               '\n'
               'if __name__ == "__main__":\n'
               '    unittest.main()\n'
               '\n'
               '\n'
               'class Stranded(unittest.TestCase):\n'
               '    pass\n')
        self.assertEqual(["Stranded"], stranded_after_main(src))

    def test_the_correct_shape_is_accepted(self):
        src = ('import unittest\n'
               '\n'
               '\n'
               'class Both(unittest.TestCase):\n'
               '    pass\n'
               '\n'
               '\n'
               'if __name__ == "__main__":\n'
               '    unittest.main()\n')
        self.assertEqual([], stranded_after_main(src))

    def test_a_file_with_no_main_block_is_not_an_offender(self):
        self.assertEqual([], stranded_after_main("import unittest\n"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
