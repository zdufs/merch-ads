#!/usr/bin/env python3
"""Rules DSL lexer (Spec B Layer 1, Task 1).
Run from the Ads folder:  python3 -m unittest tests.rules_lexer_tests -v"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))

from rules.lexer import tokenize, LexError  # noqa: E402


def kinds(src):
    return [t.kind for t in tokenize(src) if t.kind not in ("NEWLINE", "EOF")]


class Lexer(unittest.TestCase):
    def test_numbers_money_percent(self):
        toks = [t for t in tokenize("$0.85 45% 12") if t.kind in ("MONEY", "PERCENT", "NUMBER")]
        self.assertEqual([(t.kind, t.value) for t in toks],
                         [("MONEY", 0.85), ("PERCENT", 0.45), ("NUMBER", 12.0)])

    def test_keywords_case_insensitive(self):
        ks = kinds("for each Keyword")
        self.assertEqual(ks[:2], ["KEYWORD", "KEYWORD"])   # FOR, EACH
        self.assertEqual(ks[2], "IDENT")                    # 'Keyword' entity name

    def test_comment_and_string(self):
        toks = tokenize('LET x = "hi"  # ignored\n')
        vals = [t.value for t in toks if t.kind == "STRING"]
        self.assertEqual(vals, ["hi"])

    def test_indent_dedent(self):
        src = "FOR EACH keyword:\n  IF x:\n    y\n"
        ks = [t.kind for t in tokenize(src)]
        self.assertIn("INDENT", ks)
        self.assertIn("DEDENT", ks)

    def test_operators(self):
        ks = [(t.kind, t.value) for t in tokenize("a >= b != c")
              if t.kind == "OP"]
        self.assertEqual(ks, [("OP", ">="), ("OP", "!=")])

    def test_bad_char_raises(self):
        with self.assertRaises(LexError):
            tokenize("a ^ b")

    def test_tabs_rejected(self):
        with self.assertRaises(LexError):
            tokenize("FOR EACH keyword:\n\tIF x:\n")


if __name__ == "__main__":
    unittest.main()
