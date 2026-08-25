#!/usr/bin/env python3
"""Rules DSL read-only runner (Spec B Layer 1, Task 6). Parses a rule, evaluates
it against a market DB, and returns proposed changes with per-condition debug
traces — WITHOUT writing anything. Actions are recorded, never executed (writes
come in Layer 2).

`preview(conn, src)` and `validate(src)` back the appctl `rules-preview` /
`rules-validate` endpoints."""

import db
import markets
from rules import ast_nodes as A
from rules import econ_fields, entities
from rules.evaluator import eval_expr, eval_condition, plain
from rules.parser import parse, ParseError

# Economics fields — a change whose matched condition references any of these is
# "economics-driven" and must respect the US econ-freshness gate at apply time.
ECON_FIELDS = {"break_even", "royalty", "profit", "royalty_roi", "halo_est",
               "net_halo", "organic_per_day", "in_transition", "is_cohort",
               "econ_available", "owned_cross_sell"}

# The trailing-30 snapshot metrics. IN LIFETIME these have no per-entity data
# (the spec: lifetime_sales units only, no lifetime spend/acos), so they
# resolve to NONE and conditions on them skip every row — never silently
# evaluate the snapshot as if it were lifetime history.
SNAPSHOT_METRICS = ("impressions", "clicks", "spend", "cost", "sales", "orders",
                    "units", "acos", "roas", "ctr", "cvr", "cpc", "aov",
                    "kenp", "kenp_royalties")


# The verbs the executor can actually apply. The parser accepts any identifier
# as a verb, so validate must be the wall — an operator learns keyword.paws()
# is wrong while typing it, not from a nightly "unsupported" weeks later.
EXECUTABLE_VERBS = {"pause", "enable", "setbid", "setbudget", "addnegative", "note",
                    "pauseeverywhere", "setbideverywhere", "negateeverywhere"}
PLANNED_VERBS = {"setstate", "createkeyword", "setbiddingstrategy"}

# The everywhere verbs fan one change out to every instance of an accumulated
# entity — and ONLY work there. Regular per-instance verbs make no sense on a
# rollup. Both directions are validation errors.
EVERYWHERE_VERBS = {"pauseeverywhere", "setbideverywhere", "negateeverywhere"}
ACCUMULATED_ENTITIES = {"accumulated_asin", "accumulated_keyword"}

# Every field an entity row can carry (rules/entities.py loaders + econ).
KNOWN_FIELDS = {
    # metrics over the window
    "impressions", "clicks", "spend", "cost", "sales", "orders", "units",
    "acos", "roas", "ctr", "cvr", "cpc", "aov", "kenp", "kenp_royalties",
    # identity / settings
    "bid", "bid_inherited", "default_bid", "state", "name", "match_type",
    "keyword_text", "search_term", "targeting", "targeting_type", "ad_type",
    "asin", "campaign_id", "ad_group_id", "target_id", "budget",
    "bidding_strategy", "days_since_bid_change", "days_since_budget_change",
    "lifetime_sales", "product_type",
    # accumulated (cross-campaign) rollup counts
    "campaigns", "ad_groups",
} | {f.lower() for f in {"break_even", "royalty", "profit", "royalty_roi",
                         "halo_est", "net_halo", "organic_per_day",
                         "in_transition", "is_cohort", "econ_available",
                         "owned_cross_sell"}}

# Entity kinds with a TRUE per-day source. Defined once, in the loader that
# actually reads those tables, so this save-time check and the read-time refusal
# in entities.load can never disagree. Re-exported under the old name because
# that is what this module has always called it.
ROLLING_ENTITIES = entities.ROLLING_ENTITIES


def validate(src):
    try:
        prog = parse(src)
    except ParseError as e:
        return {"ok": False, "errors": [{"line": e.line, "col": e.col, "message": e.message}]}
    errors = _semantic_errors(prog)
    if errors:
        return {"ok": False, "errors": errors}
    return {"ok": True, "errors": []}


def _semantic_errors(prog):
    """Unknown verbs and unknown field names — everything the parser is too
    permissive to catch but the evaluator/executor would choke on. Also the
    rolling-window rules: which entities have per-day data, and how far back
    Amazon actually keeps it."""
    errors = []
    for fe in prog.rules:
        if fe.window == "ROLLING":
            errors.extend(_rolling_errors(fe))
        errors.extend(_window_errors(fe))
        errors.extend(_accumulated_errors(fe))
        allowed = set(KNOWN_FIELDS)
        allowed.add(fe.alias.lower())
        allowed.add(fe.entity.lower())
        _collect_let_names(fe.body, allowed)
        _check_stmts(fe.body, allowed, errors)
    return errors


def _rolling_errors(fe):
    """Everything wrong with `IN LAST n DAYS`, reported at save time."""
    import db
    out = []
    days = fe.window_days
    if days is None or float(days) != int(days):
        out.append({"line": fe.line, "col": 0,
                    "message": "IN LAST needs a whole number of days"})
        return out
    days = int(days)
    if days < 1 or days > db.MAX_DAILY_WINDOW_DAYS:
        out.append({"line": fe.line, "col": 0,
                    "message": (f"a {days}-day window is outside what Amazon keeps "
                                f"— reporting retention is about "
                                f"{db.MAX_DAILY_WINDOW_DAYS} days")})
    if fe.entity not in ROLLING_ENTITIES:
        out.append({"line": fe.line, "col": 0,
                    "message": (f"{fe.entity} has no per-day history, so it cannot "
                                f"use IN LAST n DAYS — rolling windows work on "
                                f"target, keyword, adGroup and campaign")})
    return out


def _window_errors(fe):
    """Inline baseline/trend windows (`metric IN <window>`) read the same per-day
    tables as IN LAST n DAYS, so they need a per-day entity and offsets inside
    Amazon's retention — caught at save time, not as a quiet nightly no-op."""
    import db
    out = []
    specs = _collect_windows(fe.body)
    if not specs:
        return out
    if fe.entity not in ROLLING_ENTITIES:
        out.append({"line": fe.line, "col": 0,
                    "message": (f"{fe.entity} has no per-day history, so it cannot use "
                                f"an inline window (metric IN ...) — those work on "
                                f"target, keyword, adGroup and campaign")})
    cap = db.MAX_DAILY_WINDOW_DAYS
    for spec in specs:
        offs = [int(x) for x in spec[1:]]      # day offsets / counts; () for YESTERDAY
        if offs and (min(offs) < 0 or max(offs) > cap):
            out.append({"line": fe.line, "col": 0,
                        "message": (f"a window reaching {max(offs)} days back is outside "
                                    f"Amazon's ~{cap}-day reporting retention")})
            break
    return out


def _all_actions(body):
    for stmt in body:
        if stmt.__class__.__name__ == "Action":
            yield stmt
        elif stmt.__class__.__name__ == "If":
            yield from _all_actions(stmt.body)


def _accumulated_errors(fe):
    """Accumulated entities read only the CURRENT rollup and pair with the
    everywhere verbs; the everywhere verbs work nowhere else. Both directions are
    caught at save time. negate/setbid everywhere are keyword-only (an ASIN can
    only be paused)."""
    out = []
    is_accum = fe.entity in ACCUMULATED_ENTITIES
    if is_accum and fe.window != "CURRENT":
        out.append({"line": fe.line, "col": 0,
                    "message": f"{fe.entity} is a current cross-campaign rollup; "
                               f"it has no {fe.window} window"})
    for stmt in _all_actions(fe.body):
        verb = stmt.verb.lower()
        if verb in EVERYWHERE_VERBS and not is_accum:
            out.append({"line": stmt.line, "col": 0,
                        "message": f"{stmt.verb}() only works on accumulated_asin / "
                                   f"accumulated_keyword"})
        elif is_accum and verb not in EVERYWHERE_VERBS and verb != "note":
            out.append({"line": stmt.line, "col": 0,
                        "message": f"on {fe.entity} use pauseEverywhere / setBidEverywhere "
                                   f"/ negateEverywhere, not {stmt.verb}()"})
        elif (fe.entity == "accumulated_asin"
              and verb in ("setbideverywhere", "negateeverywhere")):
            out.append({"line": stmt.line, "col": 0,
                        "message": f"{stmt.verb}() is keyword-only; on an ASIN only "
                                   f"pauseEverywhere applies"})
    return out


def _collect_let_names(body, allowed):
    for stmt in body:
        kind = stmt.__class__.__name__
        if kind == "Let":
            allowed.add(stmt.name.lower())
        elif kind == "If":
            _collect_let_names(stmt.body, allowed)


def _check_stmts(body, allowed, errors):
    for stmt in body:
        kind = stmt.__class__.__name__
        if kind == "Let":
            _check_expr(stmt.expr, allowed, errors)
        elif kind == "If":
            _check_expr(stmt.cond, allowed, errors)
            _check_stmts(stmt.body, allowed, errors)
        elif kind == "Action":
            verb = stmt.verb.lower()
            if verb in PLANNED_VERBS:
                errors.append({"line": stmt.line, "col": 0,
                               "message": f"{stmt.verb}() parses but is not executable "
                                          f"in this version — it would fail at apply time"})
            elif verb not in EXECUTABLE_VERBS:
                errors.append({"line": stmt.line, "col": 0,
                               "message": f"unknown action {stmt.verb!r}; executable "
                                          f"actions are pause, enable, setBid, "
                                          f"setBudget, addNegative, note"})
            for arg in stmt.args:
                _check_expr(arg, allowed, errors)


def _check_expr(node, allowed, errors):
    kind = node.__class__.__name__
    if kind == "Ident":
        if node.name.lower() not in allowed:
            errors.append({"line": node.line, "col": 0,
                           "message": f"unknown field {node.name!r}"})
        return
    if kind == "Field":
        if node.name.lower() not in allowed:
            errors.append({"line": node.line, "col": 0,
                           "message": f"unknown field {node.name!r}"})
        _check_expr(node.obj, allowed, errors)
        return
    for attr in ("left", "right", "operand", "obj", "expr", "cond"):
        child = getattr(node, attr, None)
        if child is not None and hasattr(child, "__class__") \
                and child.__class__.__module__ == node.__class__.__module__:
            _check_expr(child, allowed, errors)
    for attr in ("args", "items"):
        for child in getattr(node, attr, []) or []:
            _check_expr(child, allowed, errors)


def preview(conn, src, cap=50000, today=None):
    """`today` pins the rolling-window date; it exists so tests are not fragile
    against the real calendar. Production leaves it None, which resolves each
    IN LAST n DAYS window against the actual date."""
    try:
        prog = parse(src)
    except ParseError as e:
        return {"ok": False, "market": markets.current(), "evaluated": 0, "matched": 0,
                "changes": [], "row_errors": 0,
                "errors": [{"line": e.line, "col": e.col, "message": e.message}]}

    # The SAME semantic checks `validate` and `save` apply. Preview used to skip
    # them, so a rule that could never be saved still produced a confident
    # number and the two failures read very differently:
    #
    #   `target.explode()` and `target.createKeyword(...)` previewed as
    #   "72 changes". The verb does not exist, or exists and cannot execute,
    #   so the answer is a count of writes that can never happen.
    #
    #   `target.clickz >= 12` previewed as "matched 0", which is exactly what a
    #   correct rule matching nothing looks like. The operator reads "no rows
    #   meet my condition" when the truth is that the field is misspelt.
    #
    # A preview is what an author believes about a rule before saving it, so it
    # has to refuse everything the save will refuse — otherwise it teaches the
    # wrong thing and the disagreement surfaces later, at the Save button.
    semantic = _semantic_errors(prog)
    if semantic:
        return {"ok": False, "market": markets.current(), "evaluated": 0,
                "matched": 0, "changes": [], "row_errors": 0, "errors": semantic}

    ctx = econ_fields.Context(conn)
    evaluated = 0
    matched = 0
    changes = []
    errors = []
    no_evidence = []
    row_errors = 0
    truncated = False

    for fe in prog.rules:
        try:
            # What this rule was evaluated AGAINST. An empty snapshot table
            # produces the same `matched: 0` a correct rule matching nothing
            # produces, and the two mean opposite things.
            ev = entities.evidence(conn, fe.entity, window=fe.window)
            if ev and not ev["ok"]:
                no_evidence.append({"rule": fe.entity, "line": fe.line, **ev})
            rows = entities.load(conn, fe.entity, window=fe.window,
                                 window_days=fe.window_days, today=today)
            # Pre-load each inline baseline/trend window once (one grouped query
            # per distinct window), then hand each row its own slice below. Cheap
            # even at 30k+ rows, and keeps evaluation a dict lookup.
            window_maps = {spec: entities.windowed_metrics(
                               conn, fe.entity, *db.window_dates(spec, today=today))
                           for spec in _collect_windows(fe.body)}
        except Exception as e:
            return {"ok": False, "market": markets.current(), "evaluated": evaluated,
                    "matched": matched, "changes": changes, "row_errors": row_errors,
                    "errors": [{"line": fe.line, "col": 0, "message": str(e)}]}
        for row in rows:
            if len(changes) >= cap:
                truncated = True
                break
            evaluated += 1
            # One bad row must not abort the whole preview (or the nightly):
            # record what broke, keep evaluating the other 40k rows.
            try:
                econ_fields.resolve(ctx, row)
                if fe.window == "LIFETIME":
                    for metric in SNAPSHOT_METRICS:
                        if metric in row.fields:
                            row.fields[metric] = None
                windows = {spec: wm.get(entities._daily_key(fe.entity, row.fields), {})
                           for spec, wm in window_maps.items()}
                scope = {fe.alias: row, **row.fields,
                         "__windows__": windows, "__alias__": fe.alias}
                row_changes = _run_body(fe.body, scope, row, fe)
            except Exception as e:
                row_errors += 1
                if len(errors) < 5:
                    errors.append({"line": fe.line, "col": 0,
                                   "message": f"row {row.label!r}: {e}"})
                continue
            if row_changes:
                matched += 1
                changes.extend(row_changes)
        if truncated:
            break

    if row_errors > len(errors):
        errors.append({"line": 0, "col": 0,
                       "message": f"...and {row_errors - len(errors) + 1} more rows errored"})
    # `matched` alone cannot be read without `row_errors`. A row that raised is
    # not a row that failed to match, and the reply used to say ok:true with
    # matched:0 for a rule where EVERY row had errored — which reads as "nothing
    # qualified" and is how a broken rule goes on reporting a healthy nightly.
    # Rows that did evaluate still produce their changes; that is deliberate, and
    # this is the count that says how much of the account they represent.
    all_failed = evaluated > 0 and row_errors >= evaluated
    return {"ok": not all_failed, "market": markets.current(), "evaluated": evaluated,
            "matched": matched, "changes": changes, "truncated": truncated,
            "row_errors": row_errors, "errors": errors,
            # Empty, not absent, when there is nothing to say — a caller that
            # reads `if reply["no_evidence"]` must not have to know the key can
            # be missing.
            "no_evidence": no_evidence}


def _run_body(body, scope, row, fe, trace=None, econ_driven=False, econ_lets=None):
    """Execute a statement body read-only, returning proposed change dicts. LET
    binds into scope; IF recurses (attaching its condition trace + whether the
    condition is economics-driven); actions are recorded; note() sets the reason
    on the sibling actions.

    econ_lets tracks LET names whose value derives from an economics field, so
    `LET floor = break_even*0.9 … setBid(floor)` is econ-driven even when the
    IF condition never names an economics field — the econ gate must see every
    write whose VALUE came from economics, not just every econ condition."""
    out = []
    note_text = None
    econ_lets = econ_lets if econ_lets is not None else set()
    for stmt in body:
        kind = stmt.__class__.__name__
        if kind == "Let":
            if _refs_econ(stmt.expr, econ_lets):
                econ_lets.add(stmt.name.lower())
            scope[stmt.name] = eval_expr(stmt.expr, scope)
        elif kind == "If":
            if eval_condition(stmt.cond, scope):
                child_econ = econ_driven or _refs_econ(stmt.cond, econ_lets)
                out.extend(_run_body(stmt.body, scope, row, fe,
                                     _trace_of(stmt.cond, scope), child_econ, econ_lets))
        elif kind == "Action":
            if stmt.verb == "note":
                note_text = _fmt_note(stmt.args, scope, row)
                for c in out:
                    if c.get("note") is None:
                        c["note"] = note_text
            else:
                # plain(): a LET holding the authored NONE carries a marker
                # object, and an action argument must be an ordinary value —
                # otherwise the None check below would not see it and the
                # marker itself would be handed to the executor.
                args = [plain(eval_expr(a, scope)) for a in stmt.args]
                if any(a is None for a in args):
                    # a NONE argument (NULL bid, unavailable economics) can't
                    # be written — skip this row's action, fail closed
                    continue
                args = _round_money(stmt.verb, args)
                cur_state = row.fields.get("state")
                cur_bid = row.fields.get("bid")
                if _is_noop(stmt.verb, args, cur_state, cur_bid):
                    # No-op protection: don't propose pausing an already-paused
                    # entity, enabling an enabled one, or setting a bid to the
                    # value it already has. Amazon would accept the redundant
                    # write, but it burns the change cap and — worse — a paused
                    # entity recorded with a guessed prev_state of ENABLED would
                    # Undo the wrong way. Skip the phantom change.
                    continue
                out.append({
                    "entity_kind": fe.entity, "entity_id": row.id, "label": row.label,
                    "action": stmt.verb, "args": args,
                    "args_text": ", ".join(_fmt_arg(a) for a in args),
                    "window": fe.window, "window_days": fe.window_days,
                    # The INLINE windows this rule body reads (`metric IN LAST n
                    # DAYS` inside a CURRENT rule). The executor gates on
                    # `window` alone, so an inline window was treated as a
                    # current-snapshot write and its holes were never checked:
                    # six of seven requested days summed to a total the rule
                    # then acted on as if it were seven.
                    "inline_windows": [list(spec) for spec in _collect_windows(fe.body)],
                    "note": note_text, "trace": trace or [],
                    "econ_driven": econ_driven
                        or any(_refs_econ(a, econ_lets) for a in stmt.args),
                    # The REAL current state/bid, so the executor logs a
                    # prev_state Undo can trust instead of a hardcoded guess.
                    "prev_state": cur_state,
                    "prev_bid": cur_bid,
                    "ref": {
                        "campaign_id": row.fields.get("campaign_id"),
                        "ad_group_id": row.fields.get("ad_group_id"),
                        "target_id": row.fields.get("target_id"),
                        "asin": row.fields.get("asin"),
                        # keyword (EXACT/PHRASE/BROAD) vs product target — they use
                        # DIFFERENT Amazon endpoints, so the executor must route by it.
                        "match_type": row.fields.get("match_type"),
                    },
                })
    return out


def _collect_windows(body):
    """Every inline window spec (A.Windowed.window) used anywhere in a rule body,
    so preview can pre-load each distinct window once instead of per row."""
    specs = set()

    def walk(n):
        if n is None or not hasattr(n, "__class__"):
            return
        if n.__class__.__name__ == "Windowed":
            specs.add(n.window)
            walk(n.expr)
            return
        for attr in ("left", "right", "operand", "obj", "expr", "cond", "target"):
            walk(getattr(n, attr, None))
        for attr in ("args", "items", "body"):
            for child in getattr(n, attr, None) or []:
                walk(child)

    for stmt in body:
        walk(stmt)
    return specs


def _is_noop(verb, args, cur_state, cur_bid):
    """True when a proposed change would not change anything: pausing an
    already-paused entity, enabling an enabled one, or setting a bid to the
    value it already holds. Skipping these keeps the queue and the change cap
    to real changes, and keeps Undo honest. When the current value is unknown
    (None) we do NOT treat it as a no-op — better a redundant write than a
    silently dropped one. An ARCHIVED entity is terminal — no state or bid write
    can change it, so any action on it is a no-op that would only fail."""
    if cur_state is not None and str(cur_state).upper() == "ARCHIVED":
        return True
    if cur_state is not None and verb in ("pause", "enable"):
        target = "PAUSED" if verb == "pause" else "ENABLED"
        return str(cur_state).upper() == target
    if verb == "setBid" and cur_bid is not None and args:
        try:
            return round(float(cur_bid), 2) == round(float(args[0]), 2)
        except (TypeError, ValueError):
            return False
    return False


def _round_money(verb, args):
    """Round a bid or budget to the cents Amazon actually accepts.

    `setBid target.bid * 1.25` lands on something like 0.187, and the executor
    rounds it to 0.19 on the way out. The preview did not, so every screen that
    reads a proposed change — the Approval Queue most of all — showed a number
    the account was never going to get. The operator approved 0.187 and 0.19 was
    written.

    Rounding here instead means the preview, the no-op check, the queue and the
    write all speak about one number. Nothing downstream changes: `_is_noop` and
    the executor already rounded to two places, so this only moves the rounding
    to where it becomes visible.
    """
    if verb not in ("setBid", "setBudget") or not args:
        return args
    try:
        return [round(float(args[0]), 2)] + list(args[1:])
    except (TypeError, ValueError):
        return args


def _fmt_arg(v):
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def _fmt_note(args, scope, row):
    if not args:
        return None
    text = plain(eval_expr(args[0], scope))
    if not isinstance(text, str):
        return str(text)
    # {field} and {field:percent|money|number} placeholders from the row fields
    import re

    def sub(m):
        name, _, fmt = m.group(1).partition(":")
        val = scope.get(name, row.fields.get(name))
        if val is None:
            return "—"
        if fmt == "percent":
            return f"{val * 100:.0f}%"
        if fmt == "money":
            return f"${val:.2f}"
        if isinstance(val, float):
            return f"{val:g}"
        return str(val)

    return re.sub(r"\{([^}]+)\}", sub, text)


def _trace_of(cond, scope):
    """Flatten a condition into its comparison leaves as debug-trace rows
    (reuses the Spec A trace shape: condition/actual/threshold/pass)."""
    leaves = []
    _collect_compares(cond, leaves)
    trace = []
    for cmp_node in leaves:
        try:
            # plain(): the trace is JSON-encoded for the app.
            actual = plain(eval_expr(cmp_node.left, scope))
            threshold = plain(eval_expr(cmp_node.right, scope))
            passed = eval_condition(cmp_node, scope)
        except Exception:
            actual = threshold = None
            passed = False
        trace.append({"condition": f"{_unparse(cmp_node.left)} {cmp_node.op} "
                                   f"{_unparse(cmp_node.right)}",
                      "actual": actual, "threshold": threshold, "pass": passed})
    return trace


def _refs_econ(node, tainted=frozenset()):
    """True if the AST references any economics field — directly, or through a
    LET name in `tainted` (a binding whose value derived from economics)."""
    kind = node.__class__.__name__
    if kind == "Field":
        return node.name.lower() in ECON_FIELDS or _refs_econ(node.obj, tainted)
    if kind == "Ident":
        name = node.name.lower()
        return name in ECON_FIELDS or name in tainted
    for attr in ("left", "right", "operand", "obj"):
        child = getattr(node, attr, None)
        if child is not None and _refs_econ(child, tainted):
            return True
    for attr in ("args", "items"):
        for child in getattr(node, attr, []) or []:
            if _refs_econ(child, tainted):
                return True
    return False


def _collect_compares(node, out):
    kind = node.__class__.__name__
    if kind == "Compare":
        out.append(node)
    elif kind == "Logic":
        _collect_compares(node.left, out)
        _collect_compares(node.right, out)
    elif kind == "Not":
        _collect_compares(node.operand, out)


def _unparse(node):
    kind = node.__class__.__name__
    if kind == "Field":
        return f"{_unparse(node.obj)}.{node.name}"
    if kind == "Ident":
        return node.name
    if kind == "Num":
        return f"{node.value:g}"
    if kind == "Money":
        return f"${node.value:g}"
    if kind == "Percent":
        return f"{node.value * 100:g}%"
    if kind == "Str":
        return f'"{node.value}"'
    if kind == "Bool":
        return "TRUE" if node.value else "FALSE"
    if kind == "NoneLit":
        return "NONE"
    if kind == "Binary":
        return f"{_unparse(node.left)} {node.op} {_unparse(node.right)}"
    if kind == "Call":
        return f"{node.fn}({', '.join(_unparse(a) for a in node.args)})"
    if kind == "ListLit":
        return f"[{', '.join(_unparse(a) for a in node.items)}]"
    return "?"
