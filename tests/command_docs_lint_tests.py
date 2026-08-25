#!/usr/bin/env python3
"""Every appctl command is named in the docs that CLAUDE.md calls ground truth.

CLAUDE.md opens with "Ground truth lives in the docs — read before building".
That only holds while the docs keep up. On 2026-08-21 they did not: seven
commands were built, tested and shipped without ever being written down —
`everywhere-preview`, `everywhere-apply`, `export-date`,
`harvest-promote-group`, `harvest-suggest`, `portfolio-cap` and `run-status`.

A missing command is worse than an undocumented function. The next session
reads the contract, does not find the command, and builds a second one beside
it. Nothing fails; the engine just grows two ways to do the thing.

So this is a lint rather than a habit. It reads the dispatcher, reads the two
documents, and fails naming whatever is in the first and not the second.

Matching is BACKTICK-STRICT on purpose. A loose substring search would count
the word "run" in any sentence and pass forever, which is the failure mode the
stdout lint already had once. A command counts as documented when it opens a
code span — `` `run [--phase …]` `` — or follows appctl inside one, as in
`ADS_MARKET=US python3 engine/appctl.py royalty-set …`.

Adding a command? Write the line. It is two minutes, and the alternative is the
next reader not knowing it exists.

Run from the Ads folder:  python3 -m unittest tests.command_docs_lint_tests -v
"""

import ast
import os
import re
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = [os.path.join(HERE, "docs", "claude-code-handoff.md"),
        os.path.join(HERE, "CLAUDE.md")]


def dispatcher_commands():
    """Every key of appctl's DISPATCH — the real list of what the app can call."""
    with open(os.path.join(HERE, "engine", "appctl.py"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(getattr(t, "id", "") == "DISPATCH" for t in node.targets):
            continue
        return {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
    return set()


def documented_commands():
    """Names that OPEN a code span, plus anything following appctl inside one."""
    text = ""
    for path in DOCS:
        with open(path, encoding="utf-8") as f:
            text += f.read()
    named = set()
    for span in re.findall(r"`([^`]+)`", text):
        span = span.strip()
        head = re.split(r"[\s(|]+", span)[0].strip(".,;:'\"")
        if head:
            named.add(head)
        after = re.match(r"(?:ADS_MARKET=\S+\s+)?(?:python3\s+)?(?:engine/)?"
                         r"appctl(?:\.py)?\s+([a-z0-9\-]+)", span)
        if after:
            named.add(after.group(1))
    return named


class TheDocsKnowEveryCommand(unittest.TestCase):

    def test_no_appctl_command_is_missing_from_the_docs(self):
        missing = sorted(dispatcher_commands() - documented_commands())
        if not missing:
            return
        self.fail(
            "These commands exist in appctl's dispatcher and are written down "
            "nowhere:\n\n"
            + "".join(f"    {c}\n" for c in missing)
            + "\nAdd a line to docs/claude-code-handoff.md under READ or "
              "LIVE / ACTION, with the reply shape and the screen that uses "
              "it. A command nobody can find gets built a second time.")


class TheLintCannotQuietlyBecomeANoOp(unittest.TestCase):
    """Both halves have to keep finding things, or the check above is theatre."""

    def test_the_dispatcher_parse_finds_the_real_list(self):
        cmds = dispatcher_commands()
        self.assertGreater(len(cmds), 90,
                           "the DISPATCH parse found almost nothing — the "
                           "check is broken, not the engine")
        for known in ("metrics", "setbid", "stream-today", "rules-run"):
            self.assertIn(known, cmds)

    def test_the_doc_parse_finds_the_real_list(self):
        named = documented_commands()
        self.assertGreater(len(named), 200,
                           "the doc parse found almost nothing — every "
                           "command would look undocumented")
        self.assertIn("metrics", named)

    def test_a_command_nobody_wrote_down_is_reported(self):
        """Plant one, and the difference must name it."""
        fake = "command-that-is-in-no-doc"
        self.assertNotIn(fake, documented_commands())
        missing = sorted(({fake} | {"metrics"}) - documented_commands())
        self.assertEqual(missing, [fake])

    def test_the_match_is_strict_enough_to_mean_something(self):
        """Prose is not documentation.

        A loose substring check would call `run` documented because the word
        appears in a hundred sentences. Only a code span counts.
        """
        named = documented_commands()
        self.assertNotIn("nightly", named,
                         "a plain prose word was counted as a command name — "
                         "the match is too loose to catch anything")
