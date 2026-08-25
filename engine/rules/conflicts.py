#!/usr/bin/env python3
"""Cross-rule conflict detection.

Every rule is previewed and executed on its own. Nothing used to look across
rules, so two enabled rules that both moved the same target's bid BOTH wrote:
the second call overwrote the first, the last one silently won, and the audit
trail showed two writes with no hint that they fought. The operator had no way
to see it coming — each rule's own preview looked perfectly reasonable.

A conflict here is narrow on purpose: **two or more DIFFERENT rules proposing a
change to the same entity.** One rule emitting several statements for one entity
is authored intent, not a clash, and must not be flagged.

Two policies read this module:

- AUTO mode (`rules-nightly`) keeps the FIRST rule's change and skips the rest.
  First means first in rule order, which is stable, so the outcome does not
  depend on which rule happened to finish first.
- REVIEW mode (the Approval Queue) keeps every proposal and marks them, because
  there the operator is the one deciding and hiding an option would be worse.
"""

# What a change competes for. Two changes clash when they land on the same
# entity — a pause makes a bid move pointless just as surely as a second bid
# does — so the surface is recorded for the message, not to narrow the match.
_SURFACE = {
    "setbid": "bid",
    "pause": "state",
    "enable": "state",
    "setbudget": "budget",
    "addnegative": "negatives",
}


def surface_of(change):
    return _SURFACE.get(str(change.get("action", "")).lower(), "other")


# The accumulated verbs that fan ONE change out to many concrete entities.
_EVERYWHERE = {"pauseeverywhere": ("asin", "pause"),
               "setbideverywhere": ("keyword", "setbid"),
               "negateeverywhere": ("keyword", "negate")}


def entity_key(change):
    return (change.get("entity_kind"), str(change.get("entity_id")))


def entity_keys(change, conn=None):
    """Every entity this change will actually write to.

    For an ordinary change that is one key. For an accumulated "everywhere"
    verb it is the whole fanout: one row proposing `pauseEverywhere` on an ASIN
    becomes a pause on every ad group advertising it. The guard keyed on the
    ACCUMULATED entity, which can never collide with a concrete ad group id — so
    one rule pausing an ASIN everywhere and another enabling one of its ad
    groups both survived, both wrote, and the later one silently won.

    Resolving needs a database. Without one this falls back to the single
    accumulated key, which is the old behaviour: the guard is then no worse than
    it was, and `find` is still a pure function for tests that do not need it.
    """
    verb = str(change.get("action", "")).lower()
    if verb not in _EVERYWHERE or conn is None:
        return [entity_key(change)]
    kind, action = _EVERYWHERE[verb]
    try:
        import appctl
        args = change.get("args") or []
        match = str(args[0]).lower() if action == "negate" and args else "exact"
        plan = appctl._everywhere_plan(conn, kind, action,
                                       [change.get("entity_id")], match)
    except Exception:
        return [entity_key(change)]
    out = []
    for op in plan.get("ops") or []:
        if op.get("target_id"):
            out.append(("target", str(op["target_id"])))
        elif op.get("ad_group_id"):
            out.append(("adgroup", str(op["ad_group_id"])))
    return out or [entity_key(change)]


def find(changes, conn=None):
    """Group `changes` by entity and report the ones more than one rule wants.

    Returns {entity_key: [change, ...]} for contested entities only, preserving
    the order the changes arrived in — that order is what the auto policy calls
    "first". Pass `conn` to expand accumulated everywhere verbs into the
    concrete entities they will touch; without it they are keyed on the rollup,
    as before.
    """
    groups = {}
    for ch in changes:
        for key in entity_keys(ch, conn):
            bucket = groups.setdefault(key, [])
            if ch not in bucket:
                bucket.append(ch)
    return {k: v for k, v in groups.items()
            if len({c.get("rule") for c in v if c.get("rule")}) > 1}


def annotate(changes, conn=None):
    """Tag every contested change with a `conflict` block, in place-ish.

    Each tagged change gets:
      conflict = {"with": [other rule names], "surface": "bid",
                  "winner": "<rule that would be applied>", "kept": bool}
    `kept` is what the AUTO policy would do. Review mode shows every row and
    ignores `kept` — it is there so one definition of "who wins" serves both.

    Returns (changes, conflict_count) where the count is contested ENTITIES, not
    rows: "3 entities two rules both want" is the number that means something.
    """
    out = [dict(ch) for ch in changes]
    groups = {}
    for ch in out:
        # Expand an everywhere verb into the entities it will really touch, so a
        # rollup change can collide with a concrete ad group. Without `conn` this
        # is the single accumulated key, exactly as before.
        for key in entity_keys(ch, conn):
            bucket = groups.setdefault(key, [])
            if not any(b is ch for b in bucket):
                bucket.append(ch)
    contested = 0
    for group in groups.values():
        rules_in_group = [c.get("rule") for c in group if c.get("rule")]
        if len(set(rules_in_group)) < 2:
            continue
        contested += 1
        winner = rules_in_group[0]
        for ch in group:
            # A fanned-out change lands in many groups. The FIRST conflict it is
            # found in decides it, so the verdict is stable and a change can
            # never be marked kept in one group and dropped in another.
            if ch.get("conflict"):
                continue
            others = sorted({r for r in rules_in_group if r and r != ch.get("rule")})
            ch["conflict"] = {
                "with": others,
                "surface": surface_of(ch),
                "winner": winner,
                # Every statement from the winning rule is kept, so a rule that
                # legitimately emits two changes for one entity is not cut in
                # half by its own conflict with someone else.
                "kept": ch.get("rule") == winner,
            }
    return out, contested


def describe(change):
    """A one-line, human-readable account of one skipped change."""
    c = change.get("conflict") or {}
    others = ", ".join(c.get("with", [])) or "another rule"
    return {
        "rule": change.get("rule"),
        "entity_kind": change.get("entity_kind"),
        "entity_id": change.get("entity_id"),
        "label": change.get("label"),
        "action": change.get("action"),
        "surface": c.get("surface"),
        "winner": c.get("winner"),
        "message": (f"{change.get('rule')} wanted to {change.get('action')} "
                    f"{change.get('label') or change.get('entity_id')}, but "
                    f"{others} got there first — skipped"),
    }


def resolve(changes, conn=None):
    """The AUTO policy: keep the first rule's changes, drop the losers.

    Returns (kept, skipped). Skipped changes carry their `conflict` block, so
    the nightly summary can name what was dropped and why instead of quietly
    applying fewer changes than the rules matched.
    """
    annotated, _ = annotate(changes, conn)
    kept, skipped = [], []
    for ch in annotated:
        if ch.get("conflict") and not ch["conflict"]["kept"]:
            skipped.append(ch)
        else:
            kept.append(ch)
    return kept, skipped
