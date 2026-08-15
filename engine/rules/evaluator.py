#!/usr/bin/env python3
"""Rules DSL evaluator (Spec B Layer 1). Evaluates expressions/conditions against
a scope (name -> value). Fail-closed None semantics: a None operand never
satisfies a numeric/relational comparison, so economics-gated rules skip rows
where economics is unavailable (matches the phase behaviour)."""

from rules import ast_nodes as A


class EvalError(Exception):
    pass


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
        return None
    if t == "ListLit":
        return [eval_expr(x, scope) for x in node.items]
    if t == "Ident":
        if node.name in scope:
            return scope[node.name]
        raise EvalError(f"unknown name {node.name!r}")
    if t == "Field":
        return _field_lookup(eval_expr(node.obj, scope), node.name)
    if t == "Unary":
        v = eval_expr(node.operand, scope)
        return -v if v is not None else None
    if t == "Binary":
        return _binary(node.op, eval_expr(node.left, scope), eval_expr(node.right, scope))
    if t == "Compare":
        return _compare(node.op, eval_expr(node.left, scope), eval_expr(node.right, scope))
    if t == "Logic":
        left = _truthy(eval_expr(node.left, scope))
        if node.op == "AND":
            return left and _truthy(eval_expr(node.right, scope))
        return left or _truthy(eval_expr(node.right, scope))
    if t == "Not":
        return not _truthy(eval_expr(node.operand, scope))
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
    return bool(v) if v is not None else False


def _binary(op, a, b):
    if op == "+":
        if isinstance(a, str) or isinstance(b, str):
            return f"{a}{b}"
        if a is None or b is None:
            return None
        return a + b
    if a is None or b is None:
        return None
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


def _eq(a, b):
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, str) and isinstance(b, str):
        return a.lower() == b.lower()
    return a == b


def _call(fn, args):
    # NONE propagates through numeric functions (fail-closed, same as the
    # binary operators): MAX($0.05, bid*0.85) with a NULL default_bid is NONE,
    # and the runner then skips the action instead of the whole preview dying
    # on a TypeError.
    if fn in ("MIN", "MAX", "CLAMP", "ROUND", "FLOOR", "CEIL", "ABS") \
            and any(a is None for a in args):
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
    if fn == "IF":
        cond, then, els = args
        return then if _truthy(cond) else els
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
