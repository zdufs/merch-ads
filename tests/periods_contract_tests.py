#!/usr/bin/env python3
"""Every field `appctl periods` emits must reach the app.

The Dashboard's period stack is a money screen: five windows, four figures
each, and the operator reads business conclusions straight off it.

Two of those fields say what the numbers do NOT cover. `profit_note` says the
profit figure covers a SHORTER window than the spend beside it, because a
period can be extended backwards with months imported from the Ads console and
those months carry no per-design economics. `months_imported` says how many.

`PeriodRow` decoded neither, so both were dropped in silence. The Dashboard's
Year to date row read:

    Ad spend          the whole year   (2026-01-01 -> 2026-08-21)
    Estimated profit  its last 143 days (2026-04-01 -> 2026-08-21)

with nothing marking the difference — three months of spend with no profit
beside them. The reply's All time row is starker: five years of spend against
that same profit figure, and it reaches no screen only because
`PeriodRow.hiddenFromDashboard` drops it. That is a layout choice, not a guard,
and it is one edit away from being on the Dashboard too.

Nothing else could have caught it. The engine deliberately leaves `partial`
FALSE on those rows — correctly, because the window is not partial for the
three figures that do cover it — so the app's "partial" badge cannot fire
there. `tests/ytd_definition_tests.py` guards the arithmetic, which was right
all along. The failure was entirely in what the app was told and never asked.

This is the SECOND time a truth field decoded into nothing: the 2026-08-22
review found `killlist.skipped`, `ytd.partial` and
`stream-today.unresolved_advertisers` the same way, by listing both sides and
diffing them by hand. So it stops being done by hand.

Run from the Ads folder:  python3 -m unittest tests.periods_contract_tests -v
"""

import ast
import os
import re
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPCTL = os.path.join(HERE, "engine", "appctl.py")
MODELS = os.path.join(HERE, "MerchAds", "Models", "Models.swift")

# The functions that BUILD the reply. `_modeled_period_profit` returns a dict
# that `cmd_periods` merges straight into a row, so its keys are period fields
# too — reading only the command would leave a hole exactly where the profit
# fields live.
BUILDERS = ("cmd_periods", "_modeled_period_profit", "_monthly_rows")

# The Swift types the reply decodes into. A key belongs to whichever one names
# it; asking which is which from the syntax tree would be guesswork, and the
# question that matters is whether the app was told at all.
SWIFT_TYPES = ("PeriodsResponse", "PeriodRow", "PeriodsCoverage",
               "MonthRow", "MonthlyResponse")


def camel(snake):
    head, *rest = snake.split("_")
    return head + "".join(w[:1].upper() + w[1:] for w in rest)


class PeriodsReplyIsFullyDecoded(unittest.TestCase):

    @staticmethod
    def emitted_keys():
        """Every string key the period builders put into a dict.

        Both shapes count: a dict literal (`{"key": ...}`) and a later
        assignment (`entry["profit_note"] = ...`). `profit_note` is set the
        second way in one branch and the first way in another, so reading only
        literals would have missed half of the very field this exists for.
        """
        tree = ast.parse(open(APPCTL, encoding="utf-8").read())
        keys = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name not in BUILDERS:
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Dict):
                    keys |= {k.value for k in inner.keys
                             if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                if isinstance(inner, ast.Subscript) and isinstance(inner.slice, ast.Constant) \
                        and isinstance(inner.slice.value, str) \
                        and isinstance(inner.ctx, ast.Store):
                    keys.add(inner.slice.value)
        return keys

    @staticmethod
    def decoded_properties():
        """Every property name DECLARED on the period structs in Models.swift.

        The declaration has to start its line. An earlier version searched the
        struct body for `let name:` anywhere, and a commented-out property
        still matched — so deleting `profitNote` by commenting it out passed
        this test. That was found by making exactly that edit and watching
        nothing fail, which is the only way such a hole is ever found.
        """
        src = open(MODELS, encoding="utf-8").read()
        names = set()
        for kind in SWIFT_TYPES:
            m = re.search(r"struct\s+" + kind + r"\b[^{]*\{(.*?)\n\}", src, re.S)
            if not m:
                continue
            names |= set(re.findall(r"^[ \t]*let\s+([A-Za-z_][A-Za-z0-9_]*)\s*:",
                                    m.group(1), re.M))
        return names

    def test_every_period_field_the_engine_sends_is_decoded(self):
        emitted = self.emitted_keys()
        decoded = self.decoded_properties()

        # A lint that reads an empty graph passes forever and says nothing.
        self.assertGreater(len(emitted), 20,
                           "found almost no keys in the period builders — this "
                           "has stopped matching how the reply is assembled")
        self.assertGreater(len(decoded), 15,
                           "found almost no properties on the period structs — "
                           "the Swift scan has stopped matching Models.swift")

        missing = sorted(k for k in emitted if camel(k) not in decoded and k not in decoded)
        self.assertEqual(missing, [],
                         "appctl periods sends these and no period struct in "
                         "Models.swift names them, so the app decodes them into "
                         "nothing. If one says what the data does NOT cover, "
                         "render it too — a truth field on the floor is worse "
                         "than one that was never sent, because the reply looks "
                         "careful.")

    def test_the_two_fields_this_was_written_for_are_present(self):
        """Named outright, so deleting them from either side fails here loudly
        rather than shrinking a set nobody is watching."""
        emitted = self.emitted_keys()
        decoded = self.decoded_properties()
        for field in ("profit_note", "months_imported"):
            self.assertIn(field, emitted, f"the engine stopped sending {field}")
            self.assertIn(camel(field), decoded, f"PeriodRow stopped decoding {field}")


if __name__ == "__main__":
    unittest.main()
