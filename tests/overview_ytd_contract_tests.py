#!/usr/bin/env python3
"""All Markets called six partial years "YTD".

`appctl._ytd_totals` computes `partial` and `first_month` and has done all
along. `cmd_overview` did not pass either of them on, so the All Markets table
printed a plain "YTD spend" heading over every row.

Measured on the live databases, 2026-08-24:

    US     partial=False from=2026-01
    UK     partial=True  from=2026-06
    DE     partial=True  from=2026-06
    FR     partial=True  from=2026-06
    ES     partial=True  from=2026-06
    IT     partial=True  from=2026-06
    USKDP  partial=True  from=2026-08

Six of seven. The EU markets only began advertising 2026-06-24, so three months
of spend read as a full year, and USKDP's single month of August did too.

This is the SECOND time this class was found by hand on this reply — see
`tests/periods_contract_tests.py`, written when the Dashboard's Year-to-date row
paired a whole year of spend with a profit figure covering its last 143 days.
So this file pins both halves: the engine sends the fields, and a Swift struct
names them.

Run from the Ads folder:  python3 -m unittest tests.overview_ytd_contract_tests -v
"""

import ast
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(HERE, "engine")
sys.path.insert(0, ENGINE)

# The wire keys, chosen to match ytd_spend / ytd_sales / ytd_supplemented /
# ytd_basis, which already sit beside them in the same row.
TRUTH_KEYS = ("ytd_partial", "ytd_first_month")


def _camel(key):
    head, *rest = [p for p in key.split("_") if p]
    return head + "".join(p[:1].upper() + p[1:] for p in rest)


def _cmd_overview_keys():
    path = os.path.join(ENGINE, "appctl.py")
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), path)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "cmd_overview")
    keys = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
    return keys


class TheEngineSendsWhatTheYearDoesNotCover(unittest.TestCase):

    def test_ytd_totals_still_computes_both(self):
        """If this ever stops, the reply below is passing on nothing."""
        import appctl
        import inspect
        src = inspect.getsource(appctl._ytd_totals)
        self.assertIn('"partial"', src)
        self.assertIn('"first_month"', src)

    def test_overview_passes_them_on(self):
        keys = _cmd_overview_keys()
        for key in TRUTH_KEYS:
            with self.subTest(key=key):
                self.assertIn(key, keys,
                              f"cmd_overview drops {key}, so every market's "
                              f"year-to-date reads as a whole year")

    def test_they_sit_beside_the_other_ytd_fields(self):
        """A different prefix here would be a third naming convention on one row."""
        keys = _cmd_overview_keys()
        for key in ("ytd_spend", "ytd_sales", "ytd_supplemented", "ytd_basis"):
            self.assertIn(key, keys)


class TheAppNamesThem(unittest.TestCase):
    """A truth field nobody renders is the same as not having it, and worse,
    because the reply looks careful."""

    def test_a_swift_struct_names_both(self):
        swift = ""
        for root, _dirs, files in os.walk(os.path.join(HERE, "MerchAds")):
            for name in files:
                if name.endswith(".swift"):
                    with open(os.path.join(root, name), encoding="utf-8") as f:
                        swift += f.read()
        tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", swift))
        for key in TRUTH_KEYS:
            with self.subTest(key=key):
                self.assertIn(_camel(key), tokens,
                              f"the engine sends {key} and no Swift property "
                              f"named {_camel(key)} decodes it — the app "
                              f"decodes with .convertFromSnakeCase")


if __name__ == "__main__":
    unittest.main(verbosity=2)
