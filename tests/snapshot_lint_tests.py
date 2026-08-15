#!/usr/bin/env python3
"""Structural guard: never filter one perf table by another table's date.

Second occurrence of this defect class, so it gets a deterministic check rather
than another note. Each perf table is filled by its own Amazon report job. When
one job fails the tables drift apart, and a query like

    end = MAX(date) FROM campaign_perf      # a table this module never reads
    ... FROM targeting_perf WHERE date=?    # matches ZERO rows

reports "no changes" instead of "no data". In Aug 2026 that silently froze US
bids, pauses, harvest and the dashboard's Estimated profit for four nights.

The rule enforced here: if a module filters a perf table by date, it must also
derive a date FROM that same table (directly, or via db.latest_snapshot /
db.snapshot_gate).

Run from the Ads folder:  python3 -m unittest tests.snapshot_lint_tests -v
"""

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(HERE, "engine")
sys.path.insert(0, ENGINE)

PERF_TABLES = {"campaign_perf", "targeting_perf", "search_term_perf"}

# Modules that resolve the table name dynamically (a variable, not a literal),
# so the static scan below cannot see the pairing. Both already route every read
# through a per-table helper — appctl._latest_date and rules.entities' own — which
# is exactly the discipline this test enforces everywhere else.
DYNAMIC_TABLE_MODULES = {"appctl.py", os.path.join("rules", "entities.py")}

# phase0_pull writes the snapshots. Its END is computed (today-1) and its summary
# reads back exactly the rows it just stored, so there is no other table to
# borrow a date from.
WRITER_MODULES = {"phase0_pull.py"}

# Anything that is not engine code.
SKIP_DIRS = {"tests", "outputs", "build", "dist", ".git", "__pycache__",
             "MerchAds", "docs", "rule_defs"}

STRING_LITERAL = re.compile(r'"""(.*?)"""|\'\'\'(.*?)\'\'\'|"([^"\n]*)"|\'([^\'\n]*)\'', re.S)
DATE_FILTER = re.compile(r"\bdate\s*=\s*\?")
FROM_TABLE = re.compile(r"\bFROM\s+(\w+)", re.I)
JOIN_TABLE = re.compile(r"\bJOIN\s+(\w+)", re.I)

# A module "has a date source" for a table if it reads snapshot dates OUT of that
# table in any of the shapes the engine uses: MAX/MIN(date), DISTINCT date,
# SELECT date … GROUP BY date, or the db helpers.
DATE_READ = re.compile(r"(?:MAX|MIN)\(date\)|DISTINCT\s+date|GROUP\s+BY\s+date|SELECT\s+date\b",
                       re.I)
HELPER_SOURCE = re.compile(
    r"(?:latest_snapshot|snapshot_gate)\(\s*\w+\s*,\s*[\"'](\w+)[\"']", re.I)


def engine_files():
    for root, dirs, names in os.walk(ENGINE):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for name in names:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, ENGINE)
            if rel.split(os.sep)[0] in SKIP_DIRS:
                continue
            yield rel, path


def literals(text):
    for match in STRING_LITERAL.finditer(text):
        value = next((g for g in match.groups() if g is not None), "")
        if value:
            yield value


def tables_filtered_by_date(text):
    """Perf tables named in a SQL literal that also filters on `date=?`."""
    found = set()
    for chunk in literals(text):
        if not DATE_FILTER.search(chunk):
            continue
        named = set(FROM_TABLE.findall(chunk)) | set(JOIN_TABLE.findall(chunk))
        found |= {t for t in named if t in PERF_TABLES}
    return found


def tables_dated_from(text):
    """Perf tables this module resolves a snapshot date from."""
    found = {t for t in HELPER_SOURCE.findall(text) if t in PERF_TABLES}
    for chunk in literals(text):
        if not DATE_READ.search(chunk):
            continue
        named = set(FROM_TABLE.findall(chunk)) | set(JOIN_TABLE.findall(chunk))
        found |= {t for t in named if t in PERF_TABLES}
    return found


class NoCrossTableSnapshotDates(unittest.TestCase):

    def test_every_dated_perf_read_has_its_own_date_source(self):
        offenders = []
        for rel, path in engine_files():
            if rel in DYNAMIC_TABLE_MODULES or rel in WRITER_MODULES:
                continue
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            needs = tables_filtered_by_date(text)
            if not needs:
                continue
            have = tables_dated_from(text)
            missing = needs - have
            if missing:
                offenders.append(
                    f"{rel}: filters {sorted(missing)} by date but never reads "
                    f"a snapshot date from {'it' if len(missing) == 1 else 'them'} "
                    f"(dates taken from {sorted(have) or 'nowhere'})")
        self.assertEqual(
            offenders, [],
            "cross-table snapshot dates found — use db.latest_snapshot(conn, <table>) "
            "or db.snapshot_gate(conn, <table>) for each table you filter:\n  "
            + "\n  ".join(offenders))

    def test_the_scan_actually_detects_the_original_bug(self):
        """Guard the guard: the Aug 2026 phase3 code must be reported."""
        buggy = '''
            end = cur.execute("SELECT MAX(date) FROM campaign_perf").fetchone()[0]
            cur.execute("""SELECT target_id FROM targeting_perf
                           WHERE date=? AND target_id IS NOT NULL""", (end,))
        '''
        self.assertEqual(tables_dated_from(buggy), {"campaign_perf"})
        self.assertIn("targeting_perf", tables_filtered_by_date(buggy))
        self.assertTrue(tables_filtered_by_date(buggy) - tables_dated_from(buggy))

    def test_the_scan_accepts_the_fixed_shape(self):
        fixed = '''
            end = db.latest_snapshot(conn, "targeting_perf")
            cur.execute("""SELECT target_id FROM targeting_perf
                           WHERE date=? AND target_id IS NOT NULL""", (end,))
        '''
        self.assertEqual(tables_filtered_by_date(fixed) - tables_dated_from(fixed), set())


if __name__ == "__main__":
    unittest.main()
