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

import ast
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


# ---------------------------------------------------------------------------
# The same rule, asked per QUERY instead of per module.
#
# Found by mutation on 2026-08-24. A cross-table date was planted in
# harvest_prune.py -- a date read from campaign_perf, used to filter
# targeting_perf, which is this repo's most-repeated defect and the one that
# froze US bids for four nights -- and the check above passed.
#
# It passed for a structural reason, not a careless one. The check above
# compares two SETS over a whole module: the perf tables it filters by date,
# and the perf tables it reads a date from. harvest_prune already reads
# targeting_perf's date honestly on line 75, so targeting_perf was in both
# sets and the difference was empty. The planted line was invisible.
#
# That is not one gap. Sixteen engine modules read a date from a perf table
# they also filter, which is the CORRECT shape, and every one of them is
# therefore exempt for that table everywhere else in the file --
# phase2_apply.py and phase3_bids.py among them, the two modules that write
# bids and pauses to the live account.
#
# So this asks the narrower question: for one `execute(SQL, (params,))` whose
# SQL filters `date=?`, the value passed must come from a date helper naming a
# table that SQL actually reads. Function scope alone was tried first and
# reported 26 false positives, because most of these queries take their date
# from a helper a few lines up or from a caller.
#
# The set check above is KEPT, not replaced. It catches a different real
# shape: a module that filters a perf table by date and never reads that
# table's date anywhere at all. This one cannot see that, because with no
# binding to trace there is nothing to compare.
# ---------------------------------------------------------------------------

DATE_HELPERS = {"latest_snapshot", "snapshot_gate", "_latest_date"}


def _helper_table(node):
    """The perf table a date-helper call names, or None if it is not one."""
    if not isinstance(node, ast.Call):
        return None
    name = getattr(node.func, "attr", getattr(node.func, "id", ""))
    if name not in DATE_HELPERS:
        return None
    for arg in node.args[1:]:
        if isinstance(arg, ast.Constant) and arg.value in PERF_TABLES:
            return arg.value
    return None


def _date_bindings(fn):
    """{name: perf table} for names bound from a date helper in this function.

    `snapshot_gate(...)["date"]` is the same source as `snapshot_gate(...)`,
    so one subscript is unwrapped before the call is read.

    The gate is also commonly kept and unpacked in TWO statements:

        tg_gate = db.snapshot_gate(conn, "targeting_perf")
        end     = tg_gate["date"]

    which is the shape phase2_apply and phase3_bids use — the two modules that
    write pauses and bids to the live account. Only `tg_gate` used to be bound,
    so `end` named nothing and every dated query in those files went unjudged.
    The planted defect (phase 3's targeting_perf query filtered by
    campaign_perf's date, the exact Aug 2026 freeze) passed the whole suite.
    So a name assigned from a SUBSCRIPT OF an already-bound name inherits its
    table, repeated until nothing new appears — the assignments are not
    necessarily in source order.
    """
    found = {}
    assigns = [n for n in ast.walk(fn) if isinstance(n, ast.Assign)]
    for node in assigns:
        value = node.value
        if isinstance(value, ast.Subscript):
            value = value.value
        table = _helper_table(value)
        if table is None:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                found[target.id] = table
    # One hop at a time, and a name is only ever bound once here, so a pair of
    # mutually-referring assignments cannot make this spin.
    for _ in range(len(assigns) + 1):
        grew = False
        for node in assigns:
            value = node.value
            if not (isinstance(value, ast.Subscript)
                    and isinstance(value.value, ast.Name)):
                continue
            table = found.get(value.value.id)
            if table is None:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id not in found:
                    found[target.id] = table
                    grew = True
        if not grew:
            break
    return found


def _tables_named(sql):
    return {t for t in set(FROM_TABLE.findall(sql)) | set(JOIN_TABLE.findall(sql))
            if t in PERF_TABLES}


def dated_queries():
    """(module, line, function, tables filtered, tables the date came from).

    Only queries the scan can actually JUDGE are yielded: the SQL is a literal
    that filters on `date=?`, it names a perf table, and at least one name in
    the parameters traces back to a date helper. Everything else is invisible
    to this check, which is why the coverage test below exists.
    """
    for rel, path in engine_files():
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        try:
            tree = ast.parse(source, path)
        except SyntaxError:                         # not our problem to report
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            bound = _date_bindings(fn)
            if not bound:
                continue
            for call in ast.walk(fn):
                if not isinstance(call, ast.Call):
                    continue
                if getattr(call.func, "attr", "") != "execute":
                    continue
                if len(call.args) < 2:
                    continue
                sql = call.args[0]
                if not (isinstance(sql, ast.Constant)
                        and isinstance(sql.value, str)):
                    continue
                if not DATE_FILTER.search(sql.value):
                    continue
                wanted = _tables_named(sql.value)
                if not wanted:
                    continue
                passed = {bound[n] for n in
                          (e.id for e in ast.walk(call.args[1])
                           if isinstance(e, ast.Name))
                          if n in bound}
                if passed:
                    yield rel, call.lineno, fn.name, wanted, passed


class NoQueryTakesAnotherTablesDate(unittest.TestCase):

    def test_every_dated_query_uses_its_own_tables_date(self):
        offenders = [
            f"{rel}:{line} in {fn}(): filters {sorted(wanted)} by date, but "
            f"the date passed was read from {sorted(passed)}. Those tables are "
            f"filled by independent report jobs and drift apart, so this "
            f"matches zero rows and reports 'no changes' instead of 'no data'."
            for rel, line, fn, wanted, passed in dated_queries()
            if not (passed & wanted)]
        self.assertEqual(offenders, [], "\n" + "\n".join(offenders))

    def test_the_modules_that_write_bids_and_pauses_are_judged(self):
        """Coverage, not correctness — and it is the half that was missing.

        phase2_apply and phase3_bids are where a cross-table date costs money,
        and phase3 contributed ZERO judged queries until the two-step gate
        idiom was understood. A lint that reaches nothing in the file it was
        written for passes forever and says nothing while it does.
        """
        judged = {}
        for rel, _line, fn, _wanted, _passed in dated_queries():
            judged.setdefault(rel, set()).add(fn)
        for module in ("phase2_apply.py", "phase3_bids.py"):
            self.assertIn(module, judged,
                          f"no dated query in {module} is being judged — the "
                          f"date bindings stopped resolving")
        self.assertIn("build_proposals", judged["phase3_bids.py"])

    def test_a_gate_unpacked_in_two_statements_still_names_its_table(self):
        """The idiom the live-write modules use, reduced to six lines.

        `end` is the name the query is filtered by. Binding only `tg_gate` left
        `end` naming nothing, so the query could not be judged at all.
        """
        fn = ast.parse(
            'def build():\n'
            '    cp_gate = db.snapshot_gate(conn, "campaign_perf")\n'
            '    camp_end = cp_gate["date"]\n'
            '    tg_gate = db.snapshot_gate(conn, "targeting_perf")\n'
            '    end = tg_gate["date"]\n'
            '    cur.execute("SELECT x FROM targeting_perf WHERE date=?", (end,))\n'
        ).body[0]
        self.assertEqual(
            {"cp_gate": "campaign_perf", "camp_end": "campaign_perf",
             "tg_gate": "targeting_perf", "end": "targeting_perf"},
            _date_bindings(fn))

    def test_the_scan_is_not_reading_an_empty_graph(self):
        """A lint that walks nothing passes forever and says nothing while it
        does. Two of the checks guarding this file exist for that reason."""
        seen = 0
        for _rel, path in engine_files():
            with open(path, encoding="utf-8") as fh:
                try:
                    tree = ast.parse(fh.read(), path)
                except SyntaxError:
                    continue
            for fn in ast.walk(tree):
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    seen += len(_date_bindings(fn))
        self.assertGreater(seen, 20,
                           "the engine resolves snapshot dates in dozens of "
                           "functions; finding almost none means the matcher "
                           "stopped recognising the helpers")


if __name__ == "__main__":
    unittest.main()
