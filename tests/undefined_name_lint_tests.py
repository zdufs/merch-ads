#!/usr/bin/env python3
"""Structural guard: a function may not READ a name that nothing ever binds.

Python resolves names at RUN time, so an unbound read is a NameError that the
whole test suite can walk straight past. That is exactly how it got here.

On 2026-08-23 an audit fix renamed `rejected` to `accepted` in phase 2's pause
path, phase 2's rollback and phase 3's bid path, and updated the logic — but
left `rejected` in each function's closing print. The name was never assigned
again. Python evaluates the condition of `A if cond else B` before choosing a
branch, so `f"…{len(rejected)}…" if rejected else ""` raises EVERY time, not
only when something was rejected.

Both crashes land AFTER the live writes have been sent, after `writes_log` is
updated and after the local mirror is set. So Amazon has the change, the audit
trail has the change, and the nightly reports the step FAILED. 1041 tests
passed over that code because no test reaches the last line of an apply.

pyflakes finds this in a second, but the app ships a relocatable CPython
carrying only `requests` — the bundle's whole point is needing no pip — so the
check has to run on the standard library alone.

Scope rules kept deliberately conservative, because a false positive here fails
the build for something that is fine:
  * a name bound ANYWHERE in the function counts, in any branch
  * module-level names count, and so do the names bound by every ENCLOSING
    function of a nested def
  * nested defs, lambdas and comprehension bodies are their own scope and are
    not descended into
Measured against the whole engine on 2026-08-24: three hits, all three real.

The module scope used to be built by walking the WHOLE file, so a local of one
function became a binding for every other function in it. `phase2_apply.py`
binds `rejected` in apply_pauses AND in rollback_pauses, and deleting only the
first — reintroducing the exact NameError this file exists for, in the live
pause path that crashes after Amazon already has the change — was reported as
clean. So the module scope is now collected from module-level statements alone,
and an enclosing function's names are passed down the nesting chain instead.

Run from the Ads folder:  python3 -m unittest tests.undefined_name_lint_tests -v
"""

import ast
import builtins
import glob
import os
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(HERE, "engine")

BUILTINS = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "WindowsError"}


def _binds(fn):
    """Every name this function body binds. Nested scopes are not descended."""
    out = set()
    a = fn.args
    for arg in a.posonlyargs + a.args + a.kwonlyargs:
        out.add(arg.arg)
    if a.vararg:
        out.add(a.vararg.arg)
    if a.kwarg:
        out.add(a.kwarg.arg)

    stack = list(fn.body)
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
            continue
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            out.add(n.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for al in n.names:
                out.add((al.asname or al.name).split(".")[0])
        elif isinstance(n, ast.ExceptHandler) and n.name:
            out.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            out.update(n.names)
        stack.extend(ast.iter_child_nodes(n))
    return out


def _reads(fn):
    """Every (name, line) this function body reads. Nested scopes excluded."""
    out, stack = [], list(fn.body)
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            out.append((n.id, n.lineno))
        stack.extend(ast.iter_child_nodes(n))
    return out


def _module_scope(tree):
    """Names bound at MODULE level. Function and class bodies are their own.

    Descending into a function body here is what made the guard blind: a local
    of one function became a module-level binding for every sibling, so the
    same name misspelt in one of two places was called clean.
    """
    out = set(BUILTINS)
    stack = list(tree.body)
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
            continue                       # its body is a scope of its own
        if isinstance(n, ast.Lambda):
            continue
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            out.add(n.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for al in n.names:
                out.add((al.asname or al.name).split(".")[0])
        elif isinstance(n, ast.ExceptHandler) and n.name:
            out.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            out.update(n.names)
        stack.extend(ast.iter_child_nodes(n))
    return out


def _functions(node, outer):
    """(function, names its enclosing scopes bind), for every def in the tree.

    A nested def can read its enclosing function's locals, so those travel down
    the chain. A class body does NOT become an enclosing scope for its methods,
    which is Python's own rule.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield child, outer
            yield from _functions(child, outer | _binds(child))
        else:
            yield from _functions(child, outer)


def unbound_reads(path):
    """(path, line, function, name) for every read nothing in scope binds."""
    with open(path) as fh:
        tree = ast.parse(fh.read(), path)

    module = _module_scope(tree)

    hits = []
    for fn, outer in _functions(tree, module):
        bound = _binds(fn) | outer
        for name, line in _reads(fn):
            if name not in bound:
                hits.append((path, line, fn.name, name))
    return sorted(set(hits))


def engine_files():
    return sorted(glob.glob(os.path.join(ENGINE, "*.py"))
                  + glob.glob(os.path.join(ENGINE, "rules", "*.py")))


class NoUnboundNames(unittest.TestCase):
    def test_every_engine_module_binds_what_it_reads(self):
        hits = []
        for path in engine_files():
            hits += unbound_reads(path)
        self.assertEqual(
            [], hits,
            "a name is read that nothing in scope binds — this is a NameError "
            "waiting for the branch that reaches it:\n"
            + "\n".join(f"  {os.path.relpath(p, HERE)}:{ln}  {fn}()  "
                        f"reads unbound '{nm}'" for p, ln, fn, nm in hits))


class TheCheckItselfWorks(unittest.TestCase):
    """A lint that reads an empty graph passes forever and says nothing."""

    def test_it_actually_reads_the_engine(self):
        files = engine_files()
        self.assertGreater(len(files), 50,
                           "the walk found almost no engine modules")
        self.assertTrue(any(p.endswith("phase3_bids.py") for p in files))

    def test_it_catches_the_bug_it_was_written_for(self, ):
        import tempfile
        src = (
            "def send(ids):\n"
            "    return set()\n"
            "\n"
            "def apply(client, ids):\n"
            "    accepted = send(ids)\n"
            "    print(f'{len(rejected)}' if rejected else '')\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
            fh.write(src)
            tmp = fh.name
        try:
            hits = unbound_reads(tmp)
            self.assertEqual(["rejected"], [h[3] for h in hits])
            self.assertEqual("apply", hits[0][2])
        finally:
            os.unlink(tmp)

    def test_a_sibling_functions_local_does_not_count_as_a_binding(self):
        """The real shape of the regression, reduced to eight lines.

        phase2_apply binds `rejected` twice — once in the pause path and once
        in the rollback. Losing only the first left a NameError in the live
        pause path, and the module scope borrowed the rollback's binding and
        called the file clean.
        """
        src = (
            "def apply_pauses(ids, accepted):\n"
            "    print(len(rejected) if rejected else 0)\n"
            "\n"
            "def rollback(ids, accepted):\n"
            "    rejected = [i for i in ids if i not in accepted]\n"
            "    print(len(rejected) if rejected else 0)\n"
        )
        hits = self._scan(src)
        self.assertEqual([("apply_pauses", "rejected")],
                         sorted({(h[2], h[3]) for h in hits}))

    def test_a_nested_def_still_reads_its_enclosing_functions_names(self):
        """The over-approximation that was dropped must not become a false
        positive: a closure legitimately reads the outer function's locals."""
        src = (
            "def outer(rows):\n"
            "    total = sum(rows)\n"
            "    def inner(x):\n"
            "        return x + total\n"
            "    return inner\n"
        )
        self.assertEqual([], self._scan(src))

    def _scan(self, src):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
            fh.write(src)
            tmp = fh.name
        try:
            return unbound_reads(tmp)
        finally:
            os.unlink(tmp)

    def test_a_name_bound_in_any_branch_is_not_reported(self):
        import tempfile
        src = (
            "def apply(flag):\n"
            "    if flag:\n"
            "        n = 1\n"
            "    else:\n"
            "        n = 2\n"
            "    return n\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
            fh.write(src)
            tmp = fh.name
        try:
            self.assertEqual([], unbound_reads(tmp))
        finally:
            os.unlink(tmp)


if __name__ == "__main__":
    unittest.main(verbosity=2)
