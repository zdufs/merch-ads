#!/usr/bin/env python3
"""Every field the engine sends to a screen must reach that screen.

This class of fault has now been found BY HAND five times:

  2026-08-22  `killlist.skipped`  — 49 US designs excluded before any threshold
              ran, under a screen reading "No design in US is below the CVR
              floor".
  2026-08-22  `ytd.partial` — six of seven markets are part-year.
  2026-08-22  `stream-today.unresolved_advertisers` — an unresolved advertiser
              is dropped from every total on the panel.
  2026-08-22  `periods.profit_note` / `months_imported` — a whole year of ad
              spend beside a profit figure covering its last 143 days.
  2026-08-23  `killlist.econ`, `import-apply.export_error`,
              `everywhere-preview.instances` — this audit.

Each time the method was the same: list both sides and diff them by eye.
`tests/periods_contract_tests.py` automated that for ONE command, and this does
it for every command the app calls.

The failure is always shaped the same way, and it is why a missing field is
worse than a missing feature: the reply still decodes, the screen still renders,
and what the operator reads is a confident, complete-looking answer. A truth
field on the floor is worse than one that was never sent, because the reply
looks careful.

**The exception list below is not an allowlist to grow into.** Every entry
names a field and says why it legitimately reaches no screen, and
`test_no_exception_has_gone_stale` FAILS when an entry stops applying — so the
list shrinks as the app catches up and can never quietly absorb the next real
one.

Run from the Ads folder:  python3 -m unittest tests.app_contract_tests -v
"""

import ast
import os
import re
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPCTL = os.path.join(HERE, "engine", "appctl.py")
SWIFT_ROOT = os.path.join(HERE, "MerchAds")


# Fields the engine sends that no Swift property names, each with the reason it
# is allowed to stay that way. Anything not listed here is a failure.
#
# "CLI-only command" is NOT a valid reason: this only looks at commands whose
# name appears in the Swift sources, so a command no screen calls never reaches
# the diff in the first place.
EXPECTED_UNDECODED = {
    ("metrics", "trend"): (
        "Deliberate, and Models.swift says so at the struct: `trend` is a "
        "series of rolling trailing-30 SNAPSHOTS, where consecutive points "
        "overlap by 29 days. The dashboard charts true per-day history from "
        "`daily` instead, because drawing overlapping aggregates as a daily "
        "line shows drift and reads as days."),

    ("kdp-book", "format"): "Input to the print-cost compute path, which is CLI-only by design.",
    ("kdp-book", "ink"): "Input to the print-cost compute path, which is CLI-only by design.",
    ("kdp-book", "page_count"): "Input to the print-cost compute path, which is CLI-only by design.",
    ("kdp-book", "file_size_mb"): "Input to the ebook delivery-cost path, which is CLI-only by design.",
    ("kdp-book", "marketplace"): "Always US today; the KDP screen shows one marketplace.",

    ("kdp-titles", "titles"): (
        "The count of titles cached. The app re-reads `kdp-book` afterwards "
        "and shows the titles themselves, so this is a progress number for a "
        "call the operator is already watching."),

    ("rules-collect", "queued"): (
        "How many proposals the re-evaluation queued. The app immediately "
        "renders the queue itself, so the count is the length of a list "
        "already on screen."),
    ("rules-collect", "pruned"): (
        "Stale proposals dropped during the rebuild. They are gone from the "
        "queue the operator is looking at, so nothing on screen is wrong "
        "without it."),
    ("rules-delete", "deleted"): "Echo of the rule name just deleted; the list refreshes.",

    ("undo", "removed_negative"): (
        "The negative-keyword id that was deleted. `applied` already carries "
        "whether the undo worked, and the id is an Amazon internal the "
        "operator has no use for."),

    ("negatives-apply", "negatives_http"): (
        "Raw per-batch HTTP codes. Since 2026-08-23 the reply also carries "
        "`negatives_applied` and `negatives_rejected` counted PER ITEM, which "
        "is what the screen needs; the codes are for a bug report."),
    ("negatives-apply", "pauses_http"): (
        "Raw per-batch HTTP codes — see negatives_http."),

    # Amazon request-payload keys, not reply fields. They appear because the
    # scan reads every dict literal in the function, including the body it
    # sends. Harmless, and cheaper to name than to teach the scan to tell a
    # request from a reply.
    ("negate", "keywordText"): "Amazon request payload, not part of the reply.",
    ("negatives-apply", "keywordText"): "Amazon request payload, not part of the reply.",

    # `run --phase X` argument values, likewise picked up as dict keys.
    ("run", "pull"): "A --phase argument value, not a reply field.",
    ("run", "phase2"): "A --phase argument value, not a reply field.",
    ("run", "phase3"): "A --phase argument value, not a reply field.",
    ("run", "harvest"): "A --phase argument value, not a reply field.",
    ("run", "promote"): "A --phase argument value, not a reply field.",
    ("run", "promote-asins"): "A --phase argument value, not a reply field.",
}


# ---------------------------------------------------------------------------
# The half the appctl scan cannot see.
#
# Reading `cmd_*` alone found three faults in this audit and MISSED the worst
# one, because `cmd_demandfeed` builds nothing: it loads a JSON file that
# `demand_feed.py` wrote hours earlier. No walk that starts at appctl reaches
# those keys, and the screen drew 60 of 60 proven sellers at 0.00 royalty for
# eight days.
#
# So the modules that BUILD a payload the app reads are scanned too. Attribution
# is by module rather than by command, because one module's output can reach
# several screens and asking which is guesswork.
PAYLOAD_MODULES = {
    "demand_feed": "outputs/demand_feed[_M].json -> the Demand Feed screen",
    "stream_map": "stream-today / stream-verify -> the Dashboard's live panel",
    "run_status": "run-status -> the System Health run banner",
    "stream_verify": "stream-verify -> the stream_undercount alert",
    "stream_store": "stream health -> System Health",
}

MODULE_EXPECTED_UNDECODED = {
    # Empty on purpose. Every payload field a screen reads is now decoded.
    # An entry here must name a field and say why it belongs on no screen;
    # `test_no_module_exception_has_gone_stale` deletes it again when the app
    # catches up, so this can only shrink.
}


def camel(snake):
    head, *rest = snake.split("_")
    return head + "".join(w[:1].upper() + w[1:] for w in rest)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _appctl_source():
    return _read(APPCTL)


def app_called_commands():
    """{command name: handler function} for every command the app names.

    The command strings live all over the Swift sources — `bridge.call(..., ["killlist"])`,
    an ActionIntent's arguments, a rehearsal allow-list — so the test asks the
    simple question: does this command's name appear in the app at all. A
    command no screen calls is not this test's business.
    """
    tree = ast.parse(_appctl_source())
    pairs = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "DISPATCH" for t in node.targets):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for k, v in zip(node.value.keys, node.value.values):
            if isinstance(k, ast.Constant) and isinstance(k.value, str) \
                    and isinstance(v, ast.Name):
                pairs[k.value] = v.id

    strings = set()
    for root, _dirs, files in os.walk(SWIFT_ROOT):
        for f in files:
            if f.endswith(".swift"):
                s = _read(os.path.join(root, f))
                strings |= set(re.findall(r'"([a-z][a-z0-9-]+)"', s))
    return {c: fn for c, fn in pairs.items() if c in strings}


def emitted_keys():
    """{function name: every string key it puts into a dict}.

    Both shapes count, because `profit_note` is set one way in one branch and
    the other way in another: a dict literal (`{"key": ...}`) and a later
    assignment (`entry["key"] = ...`).
    """
    tree = ast.parse(_appctl_source())
    out = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        keys = set()
        for inner in ast.walk(node):
            if isinstance(inner, ast.Dict):
                keys |= {k.value for k in inner.keys
                         if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            if (isinstance(inner, ast.Subscript)
                    and isinstance(inner.slice, ast.Constant)
                    and isinstance(inner.slice.value, str)
                    and isinstance(inner.ctx, ast.Store)):
                keys.add(inner.slice.value)
        out[node.name] = keys
    return out


def decoded_names():
    """Every property DECLARED on a Swift type, plus every explicit CodingKey.

    The declaration has to start its line. Searching anywhere in the file would
    match a commented-out property, and deleting a field by commenting it out
    would then pass — which is exactly how the periods version of this test was
    found to have a hole.
    """
    names = set()
    for root, _dirs, files in os.walk(SWIFT_ROOT):
        for f in files:
            if not f.endswith(".swift"):
                continue
            s = _read(os.path.join(root, f))
            names |= set(re.findall(r"^[ \t]*(?:let|var)\s+([A-Za-z_]\w*)\s*:", s, re.M))
            names |= set(re.findall(r'case\s+\w+\s*=\s*"([^"]+)"', s))
    return names


def undecoded_pairs():
    """[(command, key)] for every emitted key no Swift property names."""
    keys = emitted_keys()
    known = decoded_names()
    found = []
    for cmd, fn in sorted(app_called_commands().items()):
        for k in sorted(keys.get(fn, ())):
            if k not in known and camel(k) not in known:
                found.append((cmd, k))
    return found


class TheScanItselfWorks(unittest.TestCase):
    """A lint that reads an empty graph passes forever and says nothing while
    it does. Three of the five guards in this repo carry a check like this for
    exactly that reason."""

    def test_it_finds_the_commands(self):
        cmds = app_called_commands()
        self.assertGreater(len(cmds), 60,
                           "found almost no app-called commands — the DISPATCH "
                           "table or the Swift scan has stopped matching")

    def test_it_finds_the_emitted_keys(self):
        keys = emitted_keys()
        self.assertGreater(sum(len(v) for v in keys.values()), 500,
                           "found almost no emitted keys — the AST walk has "
                           "stopped matching how appctl assembles a reply")

    def test_it_finds_the_swift_properties(self):
        self.assertGreater(len(decoded_names()), 400,
                           "found almost no Swift properties — the scan has "
                           "stopped matching the app sources")

    def test_a_planted_field_is_caught(self):
        """The scan must actually be capable of failing. A field name no Swift
        file could possibly carry has to come back undecoded."""
        known = decoded_names()
        planted = "a_field_no_screen_will_ever_name_xyzzy"
        self.assertNotIn(planted, known)
        self.assertNotIn(camel(planted), known)


class EveryFieldReachesAScreen(unittest.TestCase):

    def test_no_undecoded_field_is_unaccounted_for(self):
        unexpected = [(c, k) for c, k in undecoded_pairs()
                      if (c, k) not in EXPECTED_UNDECODED]
        self.assertEqual(
            unexpected, [],
            "appctl sends these to a command the app calls, and no Swift "
            "property names them, so the app decodes them into nothing:\n  "
            + "\n  ".join(f"{c} -> {k}" for c, k in unexpected)
            + "\n\nIf the field says what the data does NOT cover, RENDER it — "
            "that is the whole class of fault this test exists for. If it "
            "genuinely belongs on no screen, add it to EXPECTED_UNDECODED with "
            "the reason. Never add one without reading what it means.")

    def test_no_exception_has_gone_stale(self):
        """An allowlist that only grows eventually excuses the real thing.

        When the app learns to decode a field, its exception here stops being
        true — and a stale entry is a place the next real fault can hide
        unnoticed. So the list is required to shrink."""
        live = set(undecoded_pairs())
        stale = sorted(pair for pair in EXPECTED_UNDECODED if pair not in live)
        self.assertEqual(
            stale, [],
            "these are listed as deliberately undecoded but the app now "
            "decodes them (or the engine stopped sending them). Delete the "
            "entries:\n  " + "\n  ".join(f"{c} -> {k}" for c, k in stale))

    def test_every_exception_carries_a_reason(self):
        for pair, reason in EXPECTED_UNDECODED.items():
            self.assertIsInstance(reason, str)
            self.assertGreater(
                len(reason), 40,
                f"{pair} is excused without a real reason. A one-word note is "
                "how an allowlist starts excusing the thing it was meant to "
                "catch.")


class TheFieldsThisAuditFoundStayDecoded(unittest.TestCase):
    """Named outright, so removing one from either side fails here loudly
    rather than shrinking a set nobody is watching."""

    CASES = [
        ("cmd_killlist", "econ", "econ",
         "an empty kill list on a database with no economics reads exactly "
         "like a healthy market with nothing worth killing"),
        ("cmd_import_apply", "export_error", "exportError",
         "the campaigns were built, so the envelope is a success and this is "
         "the only place a failed catalogue adoption appears"),
        ("cmd_everywhere_preview", "instances", "instances",
         "the engine keeps the instances it will not write so a selection of "
         "40 landing on 12 can explain itself"),
    ]

    def test_each_is_still_sent_and_still_decoded(self):
        keys = emitted_keys()
        known = decoded_names()
        for fn, field, swift_name, why in self.CASES:
            with self.subTest(field=field):
                self.assertIn(field, keys.get(fn, set()),
                              f"the engine stopped sending {fn}.{field} — {why}")
                self.assertIn(swift_name, known,
                              f"the app stopped decoding {field} — {why}")


class ModulesThatBuildPayloadsAreCheckedToo(unittest.TestCase):
    """The appctl scan reads `cmd_*`. Some replies are assembled elsewhere.

    `cmd_demandfeed` reads a file `demand_feed.py` wrote, so its keys never
    appear in appctl at all — and that is where the audit's worst fault lived:
    `royalty_basis` said the figures were LIFETIME, `royalty` carried the real
    number, and `ProvenSeller` decoded neither, so the table drew zeros under a
    heading that said "30d".
    """

    @staticmethod
    def _module_keys(module):
        path = os.path.join(HERE, "engine", module + ".py")
        tree = ast.parse(_read(path))
        keys = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                keys |= {k.value for k in node.keys
                         if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        return keys

    def test_the_scan_reads_something(self):
        for module in PAYLOAD_MODULES:
            with self.subTest(module=module):
                self.assertGreater(
                    len(self._module_keys(module)), 5,
                    f"found almost no dict keys in {module}.py — the scan has "
                    "stopped matching how it builds its payload")

    def test_no_payload_field_is_unaccounted_for(self):
        known = decoded_names()
        unexpected = []
        for module in sorted(PAYLOAD_MODULES):
            for k in sorted(self._module_keys(module)):
                if k in known or camel(k) in known:
                    continue
                if (module, k) in MODULE_EXPECTED_UNDECODED:
                    continue
                unexpected.append((module, k))

        # These modules also hold internal bookkeeping dicts, so a blanket
        # failure would be noise. Only fields that look like REPLY fields are
        # judged: ones whose name the app would plausibly render.
        interesting = [(m, k) for m, k in unexpected
                       if re.search(r"royalt|sales|spend|basis|note|reason|"
                                    r"partial|missing|skip|unresolved|truncat|"
                                    r"error|stale|withheld|coverage|unknown",
                                    k)]
        self.assertEqual(
            interesting, [],
            "these modules build a payload the app reads, and no Swift "
            "property names these fields:\n  "
            + "\n  ".join(f"{m}.py -> {k}   ({PAYLOAD_MODULES[m]})"
                          for m, k in interesting)
            + "\n\nRender it, or add it to MODULE_EXPECTED_UNDECODED with the "
            "reason it belongs on no screen.")

    def test_no_module_exception_has_gone_stale(self):
        known = decoded_names()
        stale = sorted(pair for pair in MODULE_EXPECTED_UNDECODED
                       if pair[1] in known or camel(pair[1]) in known)
        self.assertEqual(stale, [],
                         "the app now decodes these — delete the entries: "
                         + ", ".join(f"{m}.{k}" for m, k in stale))


class TheDemandFeedFiguresStayReal(unittest.TestCase):
    """Named outright. Snap for MOD exports no 30-day columns, so
    `royalty_last30` is legitimately 0 on every proven seller and `royalty`
    plus `royalty_basis` are the only honest figures on that screen."""

    def test_the_engine_still_sends_the_real_royalty_and_its_basis(self):
        keys = ModulesThatBuildPayloadsAreCheckedToo._module_keys("demand_feed")
        for field in ("royalty", "royalty_basis", "royalty_total", "sales_total"):
            self.assertIn(field, keys, f"demand_feed stopped sending {field}")

    def test_the_app_still_decodes_them(self):
        known = decoded_names()
        for field in ("royalty", "royaltyBasis", "royaltyTotal", "salesTotal"):
            self.assertIn(field, known,
                          f"ProvenSeller stopped decoding {field} — the table "
                          "goes back to drawing zeros")


if __name__ == "__main__":
    unittest.main()
