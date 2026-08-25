#!/usr/bin/env python3
"""Nothing appctl calls IN-PROCESS may write to stdout.

`appctl` promises exactly one JSON object on stdout and the app decodes it with
Codable. Any `print()` in that call tree lands in the pipe ahead of the
envelope.

Two things hid this class of bug for months, and both are why a LINT is the
right guard rather than more runtime tests:

  * The `serve` worker redirects stdout into a sink while a handler runs, so a
    leak is invisible there. The same command run one-shot is not. The app only
    survived `harvest_prune.build_plan`'s notice because its decoder rescans
    lines; `jq`, a script, or any future caller would have got garbage.

  * The leaks that remain are on WRITE paths. `harvest_prune._pause_batch` is
    reached from `harvest-prune-apply`, which needs a live Amazon client and an
    approved plan. No test suite is going to exercise that against the real
    account, so no runtime check will ever see it. Reading the call graph will.

The rule has no exceptions. A print that is safe today only because its caller
happens to pass `verbose=False` is one keyword argument away from breaking the
envelope, with nothing to catch it — so those go to stderr too.

Three blind spots, found by the 2026-08-21 audit
------------------------------------------------
The first version of this lint walked only INSIDE each module appctl names, and
it started at those modules rather than at appctl. Three kinds of leak fitted
through the gaps, and all three were sitting there:

  1. `appctl.py`'s OWN handlers were never read. `cmd_kdp_titles` printed a
     per-market failure line straight onto the envelope's stream.

  2. A call that LEFT its module was dropped. `appctl cmd_sales_report` reaches
     `db.bulk_write` through `sales_import.bank`, and `appctl stream-drain`
     reaches `stream_sqs.delete_batch` through `stream_drain.drain_queue`. Both
     printed. Neither `db.bulk_write` nor `stream_sqs.delete_batch` is named in
     appctl, so neither was ever a starting point.

  3. A call on an OBJECT was dropped. This was the worst of the three.
     `AdsClient` is built inside about fourteen appctl handlers — every live
     write goes through one — and `_send_retry` printed its 429 and 5xx backoff
     notices to stdout. Amazon throttles routinely, which is why the retry
     exists at all, so `appctl setbid` answered with two lines of plain text and
     then the envelope.

So the walk now starts at every appctl function, crosses module boundaries, and
follows a method call when the local variable was built from an engine class in
the same function. `engine/rules/` is read as well; it is clean today, and a
print added there would have been invisible before.

Two more ways to reach stdout, found 2026-08-24
-----------------------------------------------
The walk found the right functions and then judged the wrong thing inside them.
A `print` carrying `file=` was exempted without ever reading the VALUE, so
`print(x, file=sys.stdout)` — the leak written out in full — was accepted. And
`sys.stdout.write(...)` is not a `print` at all: it fell through every branch
and was discarded. Both were planted in `harvest_prune._pause_batch`, the
function whose story is told above, and both passed the whole file.

Run from the Ads folder:  python3 -m unittest tests.stdout_contract_lint_tests -v
"""

import ast
import os
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(HERE, "engine")


def _engine_files():
    """dotted module name -> path, for engine/ and every package under it."""
    found = {}
    for root, dirs, files in os.walk(ENGINE):
        dirs[:] = [d for d in dirs if d != "__pycache__" and not d.startswith(".")]
        for f in files:
            if not f.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(root, f), ENGINE)
            found[rel[:-3].replace(os.sep, ".")] = os.path.join(root, f)
    return found


def _is_envelope(call):
    """True for `print(json.dumps(...))` — that print IS the reply.

    `_import_failed` answers a startup that died before there was a dispatcher
    to wrap the reason. It has to reach stdout, because stdout is where the app
    reads the envelope. Recognising the shape keeps that honest without an
    allowlist a later edit could quietly grow.
    """
    if len(call.args) != 1:
        return False
    arg = call.args[0]
    return (isinstance(arg, ast.Call)
            and isinstance(arg.func, ast.Attribute)
            and arg.func.attr == "dumps")


def _names_stdout(node):
    """True for `sys.stdout`, `sys.__stdout__` or a bare `stdout`.

    `file=` used to exempt a print without ever reading its VALUE, so
    `print(x, file=sys.stdout)` — the leak spelled out in full — was accepted.
    The envelope streams the engine really uses are named (`real_stdout`,
    `_RESP_STREAM or sys.stdout`), and those stay exempt: they ARE where the
    reply goes.
    """
    if isinstance(node, ast.Attribute) and node.attr in ("stdout", "__stdout__"):
        return True
    return isinstance(node, ast.Name) and node.id == "stdout"


def _writes_stdout(call):
    """True for `sys.stdout.write(...)` / `.writelines(...)` and `os.write(1, …)`.

    Neither is a `print`, so neither was recorded at all — the call fell
    through every branch and was discarded. Both put bytes on the stream the
    envelope owns just as surely.
    """
    target = call.func
    if not isinstance(target, ast.Attribute):
        return False
    if target.attr in ("write", "writelines") and _names_stdout(target.value):
        return True
    return (target.attr == "write"
            and isinstance(target.value, ast.Name) and target.value.id == "os"
            and bool(call.args)
            and isinstance(call.args[0], ast.Constant)
            and call.args[0].value == 1)


def _read_module(path):
    """(functions, classes) for one file.

    functions: name -> (bare-print lines, set of (base, attr) calls, set of bare
    names called, local variable -> class name).
    classes:   every class defined here, so a sibling module can resolve
               `AdsClient(...)` back to this file.
    """
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    # `import rules.runner as rr` / `from rules import runner` both bind a name
    # that is NOT the module's short name, and the resolver below only knew
    # short names and class names. So `rr.preview(...)` resolved to nothing and
    # the walk stopped there — a print inside `rules.runner.preview` was
    # unreachable to this lint while one-shot `rules-preview` would emit it on
    # stdout ahead of the envelope.
    aliases = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for al in n.names:
                aliases[al.asname or al.name.split(".")[0]] = al.name
        elif isinstance(n, ast.ImportFrom) and n.module:
            for al in n.names:
                aliases[al.asname or al.name] = f"{n.module}.{al.name}"
    funcs = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        prints, qualified, bare, local_types = [], set(), set(), {}
        for sub in ast.walk(node):
            # `x = SomeClass(...)` / `x = module.SomeClass(...)` — remember the
            # type so `x.method()` below can be followed to the right file.
            if isinstance(sub, ast.Assign) and isinstance(sub.value, ast.Call):
                ctor = sub.value.func
                name = (ctor.id if isinstance(ctor, ast.Name)
                        else ctor.attr if isinstance(ctor, ast.Attribute) else None)
                if name and name[:1].isupper():
                    for t in sub.targets:
                        if isinstance(t, ast.Name):
                            local_types[t.id] = name
            if not isinstance(sub, ast.Call):
                continue
            target = sub.func
            if isinstance(target, ast.Name):
                if target.id == "print":
                    to = next((k.value for k in sub.keywords if k.arg == "file"),
                              None)
                    on_stdout = to is None or _names_stdout(to)
                    if on_stdout and not _is_envelope(sub):
                        prints.append(sub.lineno)
                else:
                    bare.add(target.id)
            elif _writes_stdout(sub):
                prints.append(sub.lineno)
            elif isinstance(target, ast.Attribute):
                base = target.value
                if isinstance(base, ast.Name):
                    qualified.add((base.id, target.attr))
                elif isinstance(base, ast.Call):
                    # `module.Class(...).method()` in one expression
                    ctor = base.func
                    name = (ctor.id if isinstance(ctor, ast.Name)
                            else ctor.attr if isinstance(ctor, ast.Attribute) else None)
                    if name:
                        qualified.add((name, target.attr))
        funcs[node.name] = (prints, qualified, bare, local_types)
    return funcs, classes, aliases


def _engine_graph():
    """module -> functions, plus a lookup from a short name back to a module."""
    modules, by_class, by_short, by_alias = {}, {}, {}, {}
    for dotted, path in _engine_files().items():
        try:
            funcs, classes, aliases = _read_module(path)
        except (OSError, SyntaxError):
            continue
        modules[dotted] = funcs
        by_short[dotted.split(".")[-1]] = dotted
        by_alias[dotted] = aliases
        for c in classes:
            by_class[c] = dotted
    return modules, by_class, by_short, by_alias


def reachable_stdout():
    """[(module, function, line)] — every stdout print appctl can reach.

    The walk starts at every function in appctl.py, because appctl's own
    handlers are part of the call tree and used to be skipped.
    """
    modules, by_class, by_short, by_alias = _engine_graph()
    leaks, seen = {}, set()

    def resolve(mod, base, attr, local_types):
        """Which module owns `base.attr()`, if we can say for sure."""
        cls = local_types.get(base, base)
        if cls in by_class:                       # an engine class, or a local var of one
            return by_class[cls]
        if base in by_short:                      # a plain `module.func()` call
            return by_short[base]
        # An import alias in THIS module: `import rules.runner as rr`.
        target = (by_alias.get(mod) or {}).get(base)
        if target:
            if target in modules:
                return target
            # `from rules import runner` binds `runner` to `rules.runner`.
            short = target.split(".")[-1]
            if short in by_short:
                return by_short[short]
        return None

    def walk(mod, fn, trail):
        if (mod, fn) in seen:
            return
        seen.add((mod, fn))
        funcs = modules.get(mod)
        if not funcs or fn not in funcs:
            return
        prints, qualified, bare, local_types = funcs[fn]
        for line in prints:
            leaks.setdefault((mod, fn, line), trail)
        for name in bare:
            if name in funcs:
                walk(mod, name, trail + [f"{mod}.{name}"])
        for base, attr in qualified:
            owner = resolve(mod, base, attr, local_types)
            if owner and owner in modules and attr in modules[owner]:
                walk(owner, attr, trail + [f"{owner}.{attr}"])

    for fn in modules.get("appctl", {}):
        walk("appctl", fn, [f"appctl.{fn}"])
    return leaks


class TheEnvelopeIsTheOnlyThingOnStdout(unittest.TestCase):

    def test_no_appctl_entry_point_can_reach_a_bare_print(self):
        leaks = reachable_stdout()
        if not leaks:
            return
        lines = "\n".join(
            f"    engine/{mod.replace('.', '/')}.py:{line} (in {fn})\n"
            f"        reached by {' -> '.join(trail[:6])}"
            for (mod, fn, line), trail in sorted(leaks.items()))
        self.fail(
            "These print to stdout, which appctl has promised belongs to the "
            "JSON envelope alone.\n"
            "Add file=sys.stderr — the message stays visible to a human "
            "running the command, and the contract survives.\n\n" + lines)


class TheLintSeesWhatItUsedToMiss(unittest.TestCase):
    """One test per blind spot, so a rewrite cannot quietly reopen one.

    Each of these describes a leak that was live on 2026-08-21 and that the
    first version of this lint called clean.
    """

    def setUp(self):
        self.modules, self.by_class, self.by_short, self.by_alias = _engine_graph()

    def test_it_reads_appctl_s_own_handlers(self):
        """`cmd_kdp_titles` printed to stdout and nothing looked at it."""
        self.assertIn("appctl", self.modules)
        self.assertIn("cmd_kdp_titles", self.modules["appctl"])

    def test_it_follows_a_call_out_of_its_module(self):
        """appctl -> sales_import.bank -> db.store_sales_report_rows."""
        _, qualified, _, _ = self.modules["sales_import"]["bank"]
        self.assertIn(("db", "store_sales_report_rows"), qualified)
        self.assertIn("store_sales_report_rows", self.modules["db"])

    def test_it_follows_a_method_call_on_an_engine_object(self):
        """`client = AdsClient(mkt)` then `client.<method>()`.

        This is the path every live write takes, and the one that hid the 429
        backoff notice.
        """
        self.assertEqual(self.by_class.get("AdsClient"), "ads_client")
        found = False
        for fn, (_, _, _, local_types) in self.modules["appctl"].items():
            if "AdsClient" in local_types.values():
                found = True
                break
        self.assertTrue(found, "no appctl handler was seen building an AdsClient")

    def test_it_reads_the_rules_package(self):
        """`engine/rules/` is called in-process and was never scanned."""
        self.assertIn("rules.executor", self.modules)

    def test_the_envelope_print_is_the_only_shape_allowed_through(self):
        """`print(json.dumps(...))` is the reply; anything else is a leak."""
        allowed = ast.parse('print(json.dumps({"ok": False}))').body[0].value
        plain = ast.parse('print("hello")').body[0].value
        self.assertTrue(_is_envelope(allowed))
        self.assertFalse(_is_envelope(plain))

    def test_a_print_aimed_at_stdout_by_name_is_still_a_leak(self):
        """`file=` used to exempt a print without reading where it pointed."""
        leak = ast.parse('print("x", file=sys.stdout)').body[0].value
        allowed = ast.parse('print("x", file=sys.stderr)').body[0].value
        kept = ast.parse('print("x", file=real_stdout)').body[0].value
        self.assertTrue(_names_stdout(leak.keywords[0].value))
        self.assertFalse(_names_stdout(allowed.keywords[0].value))
        self.assertFalse(_names_stdout(kept.keywords[0].value),
                         "the serve worker's own envelope stream must stay exempt")

    def test_writing_to_stdout_without_print_is_a_leak_too(self):
        """`sys.stdout.write` is not a print, so it was never recorded."""
        for src in ('sys.stdout.write("x")',
                    'sys.stdout.writelines(["x"])',
                    'os.write(1, b"x")'):
            with self.subTest(src=src):
                self.assertTrue(_writes_stdout(ast.parse(src).body[0].value))
        for src in ('sys.stderr.write("x")', 'fh.write("x")', 'os.write(2, b"x")'):
            with self.subTest(src=src):
                self.assertFalse(_writes_stdout(ast.parse(src).body[0].value))

    def test_both_new_shapes_are_reported_from_a_real_module(self):
        """Plant each shape in the function the file's own story is about.

        `harvest_prune._pause_batch` is reached only from harvest-prune-apply,
        which needs a live Amazon client, so no runtime test will ever see it.
        The lint is the whole guard, and it called both of these clean.
        """
        import tempfile
        for line in ('    sys.stdout.write("paused")\n',
                     '    print("paused", file=sys.stdout)\n'):
            with self.subTest(line=line.strip()):
                src = "import sys\n\n\ndef _pause_batch(client, ids):\n" + line
                with tempfile.NamedTemporaryFile("w", suffix=".py",
                                                 delete=False) as fh:
                    fh.write(src)
                    tmp = fh.name
                try:
                    funcs, _classes, _aliases = _read_module(tmp)
                    self.assertEqual([5], funcs["_pause_batch"][0])
                finally:
                    os.unlink(tmp)

    def test_the_walk_still_goes_a_level_below_the_entry_point(self):
        """The original bug: `build_plan` was fixed and `_pause_batch` missed."""
        funcs = self.modules["harvest_prune"]
        self.assertIn("_pause_batch", funcs)
        self.assertIn("build_plan", funcs)
        _, _, bare, _ = funcs["apply"]
        self.assertIn("_pause_batch", bare,
                      "the graph must see through apply() to its helpers")


class TheLintCannotQuietlyBecomeANoOp(unittest.TestCase):
    """A lint that finds nothing passes forever, and says nothing while it does.

    Both of these guard the machinery rather than the engine. If the parse
    breaks — a rename, a walk that returns early, a graph built from an empty
    folder — the leak test above goes green and stays green. These fail instead.
    """

    def test_the_graph_is_not_empty(self):
        modules, by_class, by_short, by_alias = _engine_graph()
        self.assertGreater(len(modules), 40,
                           "almost no engine modules were parsed — the walk is "
                           "broken, not the engine")
        self.assertIn("appctl", modules)
        self.assertIn("db", modules)
        self.assertGreater(len(modules["appctl"]), 50,
                           "appctl's handlers were not parsed")
        self.assertIn("AdsClient", by_class)

    def test_a_planted_leak_is_found_through_every_kind_of_call(self):
        """Walk a synthetic graph that needs all three resolution rules.

        The chain is: appctl's handler calls another MODULE, that function calls
        a BARE private helper beside it, and the helper calls a METHOD on an
        object it built. The print sits at the far end, in a third module.

        The bare hop is deliberately placed outside appctl. Every appctl
        function is a root, so a bare call there would be reached anyway and the
        test would pass with bare-following deleted — which is how the first
        version of this test passed for the wrong reason.
        """
        modules = {
            "appctl": {"cmd_thing": ([], {("worker", "run")}, set(), {})},
            "worker": {
                "run": ([], set(), {"_private"}, {}),          # bare hop
                "_private": ([], set(), set(), {"c": "Widget"}),
            },
            "gadget": {"spill": ([99], set(), set(), {})},
        }
        modules["worker"]["_private"] = ([], {("c", "spill")}, set(), {"c": "Widget"})
        by_class = {"Widget": "gadget"}
        by_short = {"appctl": "appctl", "worker": "worker", "gadget": "gadget"}
        by_alias = {}

        import unittest.mock as mock
        with mock.patch(f"{__name__}._engine_graph",
                        return_value=(modules, by_class, by_short, by_alias)):
            leaks = reachable_stdout()
        self.assertEqual(sorted(leaks), [("gadget", "spill", 99)],
                         "the walker did not reach a leak three calls away")

    def test_a_leak_behind_an_import_alias_is_found(self):
        """`import rules.runner as rr` binds a name that is NOT the module's
        short name, so the resolver knew nothing about it and the walk stopped.

        A print inside `rules.runner.preview` was therefore invisible to this
        lint, while one-shot `rules-preview` would put it on stdout ahead of the
        envelope. Nothing else in the walk could have caught it: `rr` is not a
        class, and it is not a module short name.
        """
        modules = {
            "appctl": {"cmd_preview": ([], {("rr", "preview")}, set(), {})},
            "rules.runner": {"preview": ([42], set(), set(), {})},
        }
        by_short = {"appctl": "appctl", "runner": "rules.runner"}
        by_alias = {"appctl": {"rr": "rules.runner"}}

        import unittest.mock as mock
        with mock.patch(f"{__name__}._engine_graph",
                        return_value=(modules, {}, by_short, by_alias)):
            leaks = reachable_stdout()
        self.assertEqual(sorted(leaks), [("rules.runner", "preview", 42)],
                         "the walk did not follow an aliased import")

    def test_a_from_import_alias_is_followed_too(self):
        """`from rules import runner` binds `runner` -> `rules.runner`."""
        modules = {
            "appctl": {"cmd_x": ([], {("runner", "preview")}, set(), {})},
            "rules.runner": {"preview": ([7], set(), set(), {})},
        }
        by_short = {"appctl": "appctl", "runner": "rules.runner"}
        by_alias = {"appctl": {"runner": "rules.runner"}}

        import unittest.mock as mock
        with mock.patch(f"{__name__}._engine_graph",
                        return_value=(modules, {}, by_short, by_alias)):
            leaks = reachable_stdout()
        self.assertEqual(sorted(leaks), [("rules.runner", "preview", 7)])
