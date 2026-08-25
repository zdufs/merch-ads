#!/usr/bin/env python3
"""A promotion must not silence a source it cannot replace.

Both harvest builders do the same two-step: build a destination, then NEGATE the
term in the source ad group it came from. That is what a promotion IS — "serve
this over here, stop serving it over there" — and it is only safe while the
destination can actually serve.

Two defects, found 2026-08-24, in both files:

1. A newly created ad group went into `resolved` the moment Amazon returned its
   id, BEFORE the product ad was requested. An ad group with no product ad
   advertises nothing. So a failed product ad produced a destination that can
   never serve, the keyword or target was created there anyway, and the earning
   source was negated. The term stopped serving everywhere, was marked
   promoted=1 so it never came back, and writes_log recorded a clean promotion.

2. The negative's response was discarded. Every term was logged "negated in
   <src>" as submitted whether or not it landed. A failed negative leaves the
   term serving in BOTH places, competing with its own replacement and paying
   twice, with nothing saying so. `harvest_promote_group` was fixed for this on
   2026-08-23; these two are its twins and were not.

THESE TESTS ARE STRUCTURAL, and that is a compromise worth naming. `promote()`
needs fakes for six Amazon endpoints plus a populated harvest_log, and no such
harness exists in this repo. What is checked here is not the presence of a
string — it is the ORDER of statements in the syntax tree, and whether a call's
result is bound at all. Both defects were exactly a violation of those two
properties, and either one reappearing fails these tests.
`harvest_promote_group_tests.py` covers the same behaviour end-to-end against a
FakeClient, which is the stronger pattern to follow when a harness exists.

Run from the Ads folder:  python3 -m unittest tests.harvest_promote_order_tests -v
"""

import ast
import os
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(HERE, "engine")

BUILDERS = {
    "phase4_harvest_create.py": "create_negative_keywords",
    "phase4b_harvest_asins.py": "create_negative_product_targets",
}


def tree(name):
    path = os.path.join(ENGINE, name)
    with open(path, encoding="utf-8") as f:
        return ast.parse(f.read(), path)


def calls_named(node, name):
    """Every Call to `<anything>.name(...)` under `node`."""
    return [n for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == name]


def resolved_writes(node):
    """Every `resolved[...] = ...` statement under `node`."""
    out = []
    for n in ast.walk(node):
        if not isinstance(n, ast.Assign):
            continue
        for t in n.targets:
            if (isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name)
                    and t.value.id == "resolved"):
                out.append(n)
    return out


class ADestinationIsNotResolvedUntilItsProductAdLands(unittest.TestCase):

    def test_no_new_ad_group_is_resolved_before_the_product_ad_call(self):
        for name in BUILDERS:
            with self.subTest(module=name):
                t = tree(name)
                pa = calls_named(t, "create_product_ads")
                self.assertEqual(1, len(pa),
                                 f"{name}: expected exactly one product-ad call")
                pa_line = pa[0].lineno

                # The reuse branch resolves an ad group that ALREADY advertises
                # this ASIN, and that one is fine before the call. What must not
                # happen is resolving a FRESHLY CREATED id — those are the ones
                # read out of `ag_ids`.
                for assign in resolved_writes(t):
                    src = ast.unparse(assign.value)
                    if "ag_ids" not in src:
                        continue
                    self.assertGreater(
                        assign.lineno, pa_line,
                        f"{name}:{assign.lineno} resolves a new ad group from "
                        f"ag_ids before create_product_ads on line {pa_line} — "
                        f"an ad group with no product ad cannot serve, and the "
                        f"source gets negated anyway")

    def test_the_product_ad_result_is_actually_read(self):
        """Discarding it is how the failure became invisible in the first place."""
        for name in BUILDERS:
            with self.subTest(module=name):
                t = tree(name)
                assigns = [n for n in ast.walk(t)
                           if isinstance(n, ast.Assign)
                           and calls_named(n, "create_product_ads")]
                self.assertTrue(assigns,
                                f"{name}: the product-ad response is discarded")


class AFailedSourceNegativeIsNeverLoggedAsDone(unittest.TestCase):

    def test_the_negative_response_is_bound_not_thrown_away(self):
        for name, method in BUILDERS.items():
            with self.subTest(module=name):
                t = tree(name)
                found = calls_named(t, method)
                self.assertTrue(found, f"{name}: no {method} call any more")
                bound = [n for n in ast.walk(t)
                         if isinstance(n, ast.Assign) and calls_named(n, method)]
                self.assertTrue(
                    bound,
                    f"{name}: the {method} response is discarded, so a refused "
                    f"negative is logged as submitted")

    def test_a_promotion_can_be_logged_as_failed(self):
        """Before the fix every branch wrote "submitted"."""
        for name in BUILDERS:
            with self.subTest(module=name):
                t = tree(name)
                results = []
                for call in calls_named(t, "log_write"):
                    for arg in call.args:
                        if isinstance(arg, ast.Constant) and arg.value in ("submitted", "failed"):
                            results.append(arg.value)
                self.assertIn("failed", results,
                              f"{name}: no promotion can ever be logged failed")

    def test_the_failed_row_says_what_it_costs(self):
        """"failed" alone does not tell the operator the term is now serving in
        two places and paying twice."""
        for name in BUILDERS:
            with self.subTest(module=name):
                path = os.path.join(ENGINE, name)
                with open(path, encoding="utf-8") as f:
                    t = ast.parse(f.read(), path)
                text = " ".join(ast.unparse(c) for c in calls_named(t, "log_write"))
                self.assertIn("competing with its own replacement", text)


class TheResolveIsGuardedByTheProductAdResult(unittest.TestCase):
    """Not just AFTER the product-ad call — GUARDED BY its result.

    Found by mutation on 2026-08-24. Deleting the `if j in pa_ok:` line, so
    every freshly created ad group is resolved whether or not its product ad
    landed, broke nothing in the whole suite — including the two tests above,
    which were written for this exact defect.

    They survived because they pin the SHAPE of the code. One checks that
    `resolved` writes mentioning `ag_ids` come after the product-ad call; the
    mutated line resolves from `pa_owner`, so the filter skips it, and the
    ordering never changed. The other checks the response is assigned, and it
    still is.

    This is still a structural test — driving the real function needs a client
    stub, a database and a campaign map, which is a fair build and worth doing
    if this code is ever refactored. But it pins the GUARD rather than the
    layout: every `resolved[...] = ...` that happens after the product-ad call
    must sit inside a condition that reads the product-ad success set.
    """

    @staticmethod
    def _binds_success_ids(node):
        """`calls_named` above matches `obj.method(...)` only, and success_ids
        is a bare call, so it needs its own matcher rather than a reuse that
        silently finds nothing."""
        return any(isinstance(n, ast.Call)
                   and getattr(n.func, "id", getattr(n.func, "attr", ""))
                   == "success_ids"
                   for n in ast.walk(node))

    def test_every_post_call_resolve_tests_the_product_ad_success_set(self):
        for name in BUILDERS:
            with self.subTest(module=name):
                t = tree(name)
                pa_call = calls_named(t, "create_product_ads")[0]

                # the name the success ids were bound to, e.g. `pa_ok`
                ok_names = {
                    target.id
                    for node in ast.walk(t)
                    if isinstance(node, ast.Assign)
                    and self._binds_success_ids(node)
                    and node.lineno >= pa_call.lineno
                    for target in node.targets
                    if isinstance(target, ast.Name)}
                self.assertTrue(
                    ok_names,
                    f"{name}: nothing binds the product-ad success ids, so no "
                    f"resolve can possibly be guarded by them")

                guarded = []
                for node in ast.walk(t):
                    if not isinstance(node, ast.If):
                        continue
                    if not (ok_names & {n.id for n in ast.walk(node.test)
                                        if isinstance(n, ast.Name)}):
                        continue
                    guarded += [a.lineno for a in resolved_writes(node)]

                after = [a.lineno for a in resolved_writes(t)
                         if a.lineno > pa_call.lineno]
                unguarded = sorted(set(after) - set(guarded))
                self.assertEqual(
                    unguarded, [],
                    f"{name}: line(s) {unguarded} resolve a destination after "
                    f"the product-ad call without testing {sorted(ok_names)}. "
                    f"An ad group whose product ad failed advertises nothing, "
                    f"and the code below then creates the target there and "
                    f"NEGATES the earning source.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
