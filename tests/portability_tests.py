#!/usr/bin/env python3
"""The engine must read the same bytes on every platform it claims to run on.

Two habits are correct on macOS and Linux and silently wrong on Windows. Both
were in the engine until 2026-08-23, and neither would have raised: they change
what the code READS, not whether it runs.

1. `open(path)` with no `encoding=`. Python uses the platform's preferred
   encoding, which is UTF-8 here and cp1252 on a default Windows install. Every
   product title, every European price with a currency symbol, and every design
   name with an accent then decodes to the wrong characters, or throws while
   being written back out. The catalogue and sales-report readers had always
   named their encoding; nothing else had.

2. `sqlite3.connect(f"file:{path}?mode=ro", uri=True)`. A URI carries neither a
   backslash nor a bare drive letter, so a Windows path names a file that is not
   the database. A POSIX path holding `?` or `#` fails the same way, because
   everything after it parses as the query.

Both are enforced by reading the syntax tree rather than by grep: a text search
for `open(` also reports the word inside comments and strings, and quietening
that needs an allowlist which grows until it excuses the real thing.

Run from the Ads folder:  python3 -m unittest tests.portability_tests -v
"""

import ast
import glob
import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))

import db  # noqa: E402


def engine_modules():
    return sorted(glob.glob(os.path.join(HERE, "engine", "*.py")))


class TextFilesNameTheirEncoding(unittest.TestCase):

    def test_every_text_open_says_utf8(self):
        offenders = []
        seen = 0
        for path in engine_modules():
            tree = ast.parse(open(path, encoding="utf-8").read())
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "open"):
                    continue
                seen += 1
                mode = ""
                if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                    mode = node.args[1].value or ""
                for kw in node.keywords:
                    if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                        mode = kw.value.value or ""
                if "b" in mode:
                    continue          # a binary open takes no encoding
                if any(kw.arg == "encoding" for kw in node.keywords):
                    continue
                offenders.append(f"{os.path.relpath(path, HERE)}:{node.lineno}")

        # A lint that reads an empty graph passes forever and says nothing.
        self.assertGreater(seen, 40,
                           "found almost no open() calls — this has stopped "
                           "matching how the engine reads files")
        self.assertEqual(offenders, [],
                         "these text opens do not name an encoding, so they "
                         "read cp1252 on a default Windows install: "
                         + ", ".join(offenders))


class DatabaseURIsAreBuiltNotFormatted(unittest.TestCase):

    def test_no_module_formats_a_file_uri_by_hand(self):
        offenders = []
        for path in engine_modules():
            for i, line in enumerate(open(path, encoding="utf-8"), 1):
                if line.lstrip().startswith(("#", "*")) or '"""' in line:
                    continue
                if 'connect(f"file:' in line or "connect(f'file:" in line:
                    offenders.append(f"{os.path.relpath(path, HERE)}:{i}")
        self.assertEqual(offenders, [],
                         "build the URI with db.file_uri() — an f-string is "
                         "only correct while the path is POSIX: "
                         + ", ".join(offenders))

    def test_a_uri_never_carries_a_raw_path(self):
        uri = db.file_uri(os.path.join(HERE, "ads_data.sqlite"), "ro")
        self.assertTrue(uri.startswith("file:///"), uri)
        self.assertNotIn("\\", uri)
        self.assertTrue(uri.endswith("?mode=ro"), uri)

    def test_a_question_mark_in_the_name_cannot_become_the_query(self):
        """The bug this shape prevents, in the one form reproducible here.

        A Windows path cannot be built on macOS, but `?` breaks a hand-written
        URI on every platform for exactly the same reason, so it stands in.
        """
        uri = db.file_uri("/tmp/odd?name.sqlite", "ro")
        self.assertIn("%3F", uri, "the ? was left to be parsed as the query")
        self.assertEqual(uri.count("?"), 1, uri)

    def test_a_relative_path_is_made_absolute(self):
        self.assertTrue(db.file_uri("ads_data.sqlite", "ro").startswith("file:///"))


if __name__ == "__main__":
    unittest.main()
