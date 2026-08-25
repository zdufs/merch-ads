#!/usr/bin/env python3
"""Rules DSL evaluator (Spec B Layer 1). Evaluates expressions/conditions against
a scope (name -> value). Fail-closed None semantics: a None operand never
satisfies a numeric/relational comparison, so economics-gated rules skip rows
where economics is unavailable (matches the phase behaviour)."""

from rules import ast_nodes as A


class EvalError(Exception):
    pass


class _Null:
    """The NONE the AUTHOR wrote, kept apart from data that is simply missing.

    Both are "no value", and the DSL has to treat them differently. A metric
    that is missing is UNKNOWN: it must not satisfy any comparison, not even a
    negated one. `NONE` typed into a rule is a QUESTION about missing data and
    deserves a real answer, so `IF target.bid != NONE` still means "is a bid
    set".

    Telling them apart syntactically — by looking for a NoneLit node beside the
    operator — was the first attempt and it missed two shapes: `x IN [NONE]`,
    where the literal is nested in a list, and

        LET missing = NONE
        IF target.bid = missing:

    where the binding drops the marker entirely and a rule that used to match
    silently stopped. A value carries this with it, so both work. Found by
    review, 2026-08-23.
    """

    __slots__ = ()

    def __repr__(self):
        return "NONE"

    def __bool__(self):
        return False


NULL = _Null()


def plain(v):
    """The value with the authored-NONE marker removed.

    Everything OUTSIDE expression evaluation wants this: action arguments, the
    debug trace and anything that gets JSON-encoded. Only `_compare` and a LET
    binding need to know the difference.
    """
    if v is NULL:
        return None
    if isinstance(v, (list, tuple)):
        return [plain(x) for x in v]
    return v


def _is_null(v):
    return v is NULL


def _field_lookup(obj, name):
    if obj is None:
        return None
    if isinstance(obj, dict):
        if name in obj:
            return obj[name]
        raise EvalError(f"unknown field {name!r}")
    fn = getattr(obj, "field", None)
    if callable(fn):
        return fn(name)
    raise EvalError(f"cannot read field {name!r} of {type(obj).__name__}")


def eval_expr(node, scope):
    t = node.__class__.__name__
    if t == "Num" or t == "Money" or t == "Percent":
        return node.value
    if t == "Str":
        return node.value
    if t == "Bool":
        return node.value
    if t == "NoneLit":
        return NULL
    if t == "ListLit":
        return [eval_expr(x, scope) for x in node.items]
    if t == "Ident":
        if node.name in scope:
            return scope[node.name]
        raise EvalError(f"unknown name {node.name!r}")
    if t == "Field":
        return _field_lookup(eval_expr(node.obj, scope), node.name)
    if t == "Unary":
        v = plain(eval_expr(node.operand, scope))
        return -v if v is not None else None
    if t == "Binary":
        return _binary(node.op, eval_expr(node.left, scope), eval_expr(node.right, scope))
    if t == "Compare":
        return _compare(node.op, eval_expr(node.left, scope),
                        eval_expr(node.right, scope))
    if t == "Logic":
        # Three-valued, the way SQL is: FALSE AND UNKNOWN is FALSE, but
        # TRUE AND UNKNOWN is UNKNOWN. Collapsing unknown to False here would
        # make `NOT (...)` turn it into a match one level up.
        left = _tv(eval_expr(node.left, scope))
        if node.op == "AND":
            if left is False:
                return False                    # short-circuit still sound
            right = _tv(eval_expr(node.right, scope))
            if right is False:
                return False
            return True if (left and right) else None
        if left is True:
            return True                         # short-circuit still sound
        right = _tv(eval_expr(node.right, scope))
        if right is True:
            return True
        return False if (left is False and right is False) else None
    if t == "Not":
        v = _tv(eval_expr(node.operand, scope))
        return None if v is None else (not v)
    if t == "Call":
        return _call(node.fn, [eval_expr(a, scope) for a in node.args])
    if t == "Windowed":
        return _eval_windowed(node, scope)
    raise EvalError(f"cannot evaluate node {t}")


# Metric names a window can restate; everything else (asin, bid, state, name…)
# is identity and comes from the base row unchanged.
_METRIC_FIELDS = frozenset({
    "impressions", "clicks", "spend", "cost", "sales", "orders", "units",
    "acos", "cvr", "ctr", "cpc", "roas", "aov"})


class _WindowedRow:
    """The current entity with its metrics swapped for one window's values.
    A metric with no value in that window reads NONE (fail-closed) rather than
    leaking the outer window's number; identity fields defer to the base row."""
    def __init__(self, base, wm):
        self._base = base
        self._wm = wm

    def field(self, name):
        key = name.lower()
        if key in self._wm:
            return self._wm[key]
        if key in _METRIC_FIELDS:
            return None
        return self._base.field(name)


def _eval_windowed(node, scope):
    """`<expr> IN <window>`: evaluate expr with the entity's metric fields taken
    from the pre-computed window map (runner attaches it as __windows__)."""
    wins = scope.get("__windows__") or {}
    wm = wins.get(node.window, {})
    sub = dict(scope)
    for m in _METRIC_FIELDS:            # bare metric names -> windowed (or NONE)
        sub[m] = wm.get(m)
    alias = scope.get("__alias__")
    base = scope.get(alias) if alias else None
    if base is not None:
        sub[alias] = _WindowedRow(base, wm)
    return eval_expr(node.expr, sub)


def eval_condition(node, scope):
    return _truthy(eval_expr(node, scope))


def _truthy(v):
    """Collapse three-valued truth to a decision. UNKNOWN never matches."""
    v = plain(v)
    return bool(v) if v is not None else False


def _tv(v):
    """Three-valued truth: True, False, or None for UNKNOWN."""
    v = plain(v)
    return None if v is None else bool(v)


def _binary(op, a, b):
    # Missing FIRST, before the string branch. `x + ""` used to format a None
    # through an f-string and hand back the four characters "None", so
    # `IF x + "" = "None"` was TRUE for a row with no data — UNKNOWN turned
    # into a match, in front of an action (found by review, 2026-08-23).
    a, b = plain(a), plain(b)
    if a is None or b is None:
        return None
    if op == "+":
        if isinstance(a, str) or isinstance(b, str):
            return f"{a}{b}"
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if op == "/":
        return a / b if b else None
    if op == "%":
        return a % b if b else None
    raise EvalError(f"unknown operator {op}")


def _compare(op, a, b):
    """Compare two values, answering None for UNKNOWN.

    The relational operators always refused a missing operand. The EQUALITY and
    MEMBERSHIP ones did not, and their negations turned that into a match:
    `_eq(None, 0)` is False, so `not _eq(...)` made

        IF adGroup.cvr != 0:  adGroup.pause()

    TRUE for every ad group nobody had ever clicked. `NOT IN` and
    `NOT CONTAINS` had the identical shape, and `NOT (...)` over any of them
    inverted a fail-closed False into a match one level up.

    So a missing operand now answers UNKNOWN, which propagates through AND / OR
    / NOT and is collapsed to "does not match" at `eval_condition` — the same
    fail-closed rule the rest of this engine uses for economics, snapshot dates
    and rolling windows.

    `null_test` is set when the author wrote the NONE literal themselves, which
    is a real question about missing data and keeps its real answer.
    Found by review, 2026-08-23.
    """
    asked_about_none = (_is_null(a) or _is_null(b)
                        or (isinstance(b, (list, tuple))
                            and any(_is_null(x) for x in b)))
    a, b = plain(a), plain(b)
    missing = a is None or (b is None and not isinstance(b, (list, tuple)))
    # An authored NONE is a null test only for the EQUALITY and MEMBERSHIP
    # operators, which are the ones that can answer it. `bid < NONE` is not a
    # question, it is nonsense — and letting it through returned False, so
    # `NOT (bid < NONE)` inverted that into a match and could pause a target
    # whose bid was perfectly well known (found by review, 2026-08-23).
    if missing and not (asked_about_none and op in _NULL_TESTABLE):
        return None
    if op in ("==", "="):
        return _eq(a, b)
    if op in ("!=", "<>"):
        return not _eq(a, b)
    if op in ("IN", "NOT IN"):
        found = isinstance(b, (list, tuple)) and any(_eq(a, x) for x in b)
        return found if op == "IN" else not found
    if op in ("CONTAINS", "NOT CONTAINS", "STARTS WITH", "ENDS WITH"):
        sa = "" if a is None else str(a).lower()
        sb = "" if b is None else str(b).lower()
        if op == "CONTAINS":
            return sb in sa
        if op == "NOT CONTAINS":
            return sb not in sa
        if op == "STARTS WITH":
            return sa.startswith(sb)
        return sa.endswith(sb)
    # relational: None never matches
    if a is None or b is None:
        return False
    if op == "<":
        return a < b
    if op == "<=":
        return a <= b
    if op == ">":
        return a > b
    if op == ">=":
        return a >= b
    raise EvalError(f"unknown comparator {op}")


# The operators that can answer "is this NONE". Everything else — relational,
# CONTAINS, STARTS WITH — has no null test, so a NONE operand stays UNKNOWN.
_NULL_TESTABLE = frozenset({"==", "=", "!=", "<>", "IN", "NOT IN"})


def _eq(a, b):
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, str) and isinstance(b, str):
        return a.lower() == b.lower()
    return a == b


def _call(fn, args):
    """Every function here is STRICT: one UNKNOWN argument makes the answer
    UNKNOWN, so the runner skips the action instead of dying on a TypeError.

    It used to name the numeric functions only, and the string ones then coerced
    a missing value with `str()`. `LOWER(x)` answered "none", `CONCAT(x, "")`
    answered "None", and `IF(x != 0, FALSE, TRUE)` picked its else-branch and
    answered TRUE — so a rule reading

        IF IF(target.cvr != 0, FALSE, TRUE):  target.pause()

    proposed a live pause on a row nobody had measured, with an empty trace
    behind it. Found by review, 2026-08-23.
    """
    args = [plain(a) for a in args]
    if fn == "IF":
        cond, then, els = args
        if cond is None:
            return None            # an UNKNOWN condition picks NEITHER branch
        return then if _truthy(cond) else els
    if fn != "TODAY" and any(a is None for a in args):
        return None
    if fn == "MIN":
        return min(args)
    if fn == "MAX":
        return max(args)
    if fn == "CLAMP":
        v, lo, hi = args
        return max(lo, min(hi, v))
    if fn == "ROUND":
        return round(args[0], int(args[1]) if len(args) > 1 else 0)
    if fn == "FLOOR":
        import math
        return math.floor(args[0])
    if fn == "CEIL":
        import math
        return math.ceil(args[0])
    if fn == "ABS":
        return abs(args[0])
    if fn == "LOWER":
        return str(args[0]).lower()
    if fn == "UPPER":
        return str(args[0]).upper()
    if fn == "LENGTH":
        return len(args[0])
    if fn == "REPLACE":
        return str(args[0]).replace(str(args[1]), str(args[2]))
    if fn == "CONCAT":
        return "".join(str(a) for a in args)
    if fn == "TODAY":
        import datetime
        return datetime.date.today().isoformat()
    raise EvalError(f"unknown function {fn}")
