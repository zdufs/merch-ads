#!/usr/bin/env python3
"""Rules DSL recursive-descent parser (Spec B Layer 1). Produces an AST from
ast_nodes. Windows are CURRENT, LIFETIME, or LAST <n> DAYS. Which entities can
use a rolling window, and how far back Amazon's retention allows, is checked
later in runner._semantic_errors — not here."""

from rules.lexer import tokenize, LexError
from rules import ast_nodes as A


class ParseError(Exception):
    def __init__(self, message, line, col=0):
        super().__init__(f"{message} (line {line})")
        self.message = message
        self.line = line
        self.col = col


_COMPARATORS = {"=", "==", "!=", "<>", "<", "<=", ">", ">="}
_FUNCTIONS = {"MIN", "MAX", "CLAMP", "ROUND", "FLOOR", "CEIL", "ABS", "IF",
              "LOWER", "UPPER", "LENGTH", "REPLACE", "CONCAT", "TODAY"}
_ENTITIES = {"keyword", "target", "searchterm", "campaign", "adgroup",
             "product", "asin", "accumulated_asin", "accumulated_keyword"}


class _Cursor:
    def __init__(self, tokens):
        self.toks = tokens
        self.i = 0

    @property
    def cur(self):
        return self.toks[self.i]

    def at(self, kind, value=None):
        t = self.cur
        return t.kind == kind and (value is None or t.value == value)

    def eat(self, kind=None, value=None):
        t = self.cur
        if kind and t.kind != kind:
            raise ParseError(f"expected {kind}, got {t.kind} {t.value!r}", t.line, t.col)
        if value is not None and t.value != value:
            raise ParseError(f"expected {value!r}, got {t.value!r}", t.line, t.col)
        self.i += 1
        return t

    def skip_newlines(self):
        while self.at("NEWLINE"):
            self.i += 1


def parse(src):
    try:
        toks = tokenize(src)
    except LexError as e:
        raise ParseError(e.message, e.line, e.col)
    c = _Cursor(toks)
    prog = A.Program()
    c.skip_newlines()
    while not c.at("EOF"):
        prog.rules.append(_for_each(c))
        c.skip_newlines()
    return prog


def parse_expr(src):
    """Parse a single expression (test/eval entry). Accepts a bare expression
    with no trailing newline needed."""
    try:
        toks = tokenize(src + "\n")
    except LexError as e:
        raise ParseError(e.message, e.line, e.col)
    c = _Cursor(toks)
    node = _expr(c)
    return node


# ---- statements --------------------------------------------------------------

def _for_each(c):
    kw = c.eat("KEYWORD", "FOR")
    c.eat("KEYWORD", "EACH")
    ent = c.eat("IDENT")
    entity = ent.value
    if entity.lower() not in _ENTITIES:
        raise ParseError(f"unknown entity {entity!r}; expected one of "
                         f"{', '.join(sorted(_ENTITIES))}", ent.line)
    alias = None
    if c.at("KEYWORD", "AS"):
        c.eat()
        alias = c.eat("IDENT").value
    window = "CURRENT"
    window_days = None
    if c.at("KEYWORD", "IN"):
        c.eat()
        wt = c.cur
        if c.at("KEYWORD", "CURRENT") or c.at("KEYWORD", "LIFETIME"):
            window = c.eat().value
        elif c.at("KEYWORD", "LAST"):
            # Rolling windows read the true per-day tables (target_daily,
            # campaign_daily). Before those existed this was a parse error,
            # because the only per-entity data was an overlapping trailing-30
            # snapshot. The day count is validated in runner._semantic_errors,
            # which is where the operator sees it while typing.
            c.eat()
            nt = c.cur
            if not c.at("NUMBER"):
                raise ParseError("IN LAST needs a number of days, as in "
                                 "'IN LAST 7 DAYS'", nt.line)
            raw_days = c.eat().value
            # NUMBER tokens are always float. A whole day count becomes a
            # real int here, to match the ForEach.window_days contract. A
            # fractional count (7.5) stays a float on purpose — that is what
            # lets runner._rolling_errors still see the fraction and reject
            # it, with a message the operator sees while typing.
            window_days = int(raw_days) if raw_days == int(raw_days) else raw_days
            if not c.at("KEYWORD", "DAYS") and not c.at("KEYWORD", "DAY"):
                raise ParseError("IN LAST <n> must be followed by DAYS", c.cur.line)
            c.eat()
            window = "ROLLING"
        else:
            raise ParseError(
                "windows are CURRENT, LIFETIME, or LAST <n> DAYS", wt.line)
    c.eat("COLON")
    c.eat("NEWLINE")
    body = _block(c)
    return A.ForEach(entity=entity.lower(), alias=alias or entity, window=window,
                     body=body, line=kw.line, window_days=window_days)


def _block(c):
    c.eat("INDENT")
    stmts = []
    while not c.at("DEDENT") and not c.at("EOF"):
        c.skip_newlines()
        if c.at("DEDENT") or c.at("EOF"):
            break
        stmts.append(_statement(c))
        c.skip_newlines()
    if c.at("DEDENT"):
        c.eat("DEDENT")
    return stmts


def _statement(c):
    if c.at("KEYWORD", "LET"):
        return _let(c)
    if c.at("KEYWORD", "IF") or c.at("KEYWORD", "WHEN"):
        return _if(c)
    return _action(c)


def _let(c):
    kw = c.eat("KEYWORD", "LET")
    name = c.eat("IDENT").value
    c.eat("OP", "=")
    expr = _expr(c)
    return A.Let(name=name, expr=expr, line=kw.line)


def _if(c):
    kw = c.eat()  # IF or WHEN
    cond = _expr(c)
    c.eat("COLON")
    c.eat("NEWLINE")
    body = _block(c)
    return A.If(cond=cond, body=body, line=kw.line)


def _action(c):
    """entity[.hop]*.verb(args) — the first `.name(` terminates as the action."""
    obj_tok = c.cur
    node = A.Ident(name=c.eat("IDENT").value, line=obj_tok.line)
    while c.at("DOT"):
        c.eat("DOT")
        name = c.eat("IDENT").value
        if c.at("LPAREN"):
            args = _call_args(c)
            return A.Action(target=node, verb=name, args=args, line=obj_tok.line)
        node = A.Field(obj=node, name=name, line=obj_tok.line)
    raise ParseError("expected an action call like entity.pause()", obj_tok.line)


def _call_args(c):
    c.eat("LPAREN")
    args = []
    if not c.at("RPAREN"):
        args.append(_expr(c))
        while c.at("COMMA"):
            c.eat("COMMA")
            args.append(_expr(c))
    c.eat("RPAREN")
    return args


# ---- expressions (precedence climbing) --------------------------------------

def _expr(c):
    return _logic_or(c)


def _logic_or(c):
    node = _logic_and(c)
    while c.at("KEYWORD", "OR"):
        c.eat()
        node = A.Logic(op="OR", left=node, right=_logic_and(c))
    return node


def _logic_and(c):
    node = _logic_not(c)
    while c.at("KEYWORD", "AND"):
        c.eat()
        node = A.Logic(op="AND", left=node, right=_logic_not(c))
    return node


def _logic_not(c):
    if c.at("KEYWORD", "NOT"):
        c.eat()
        return A.Not(operand=_logic_not(c))
    return _comparison(c)


def _window_after_in(c):
    """True when the token after IN begins a window rather than a list/value, so
    `x IN FROM ...` / `x IN 3 DAYS AGO` read as a windowed metric while
    `x IN ["EXACT"]` stays membership. A bare number can only mean a window here:
    `x IN 3` (membership against a non-list) is always false, so never intended."""
    nxt = c.toks[c.i + 1] if c.i + 1 < len(c.toks) else None
    if nxt is None:
        return False
    if nxt.kind == "KEYWORD" and nxt.value in ("FROM", "YESTERDAY", "LAST"):
        return True
    return nxt.kind == "NUMBER"


def _int_days(c):
    """`<n> DAYS` — the number then the DAYS/DAY keyword. Returns int n."""
    nt = c.eat("NUMBER")
    if nt.value != int(nt.value):
        raise ParseError("a day count must be a whole number", nt.line)
    if not (c.at("KEYWORD", "DAYS") or c.at("KEYWORD", "DAY")):
        raise ParseError("expected DAYS after the number", c.cur.line)
    c.eat()
    return int(nt.value)


def _days_ago(c):
    """`<n> DAYS AGO` — returns int n."""
    n = _int_days(c)
    c.eat("KEYWORD", "AGO")
    return n


def _window_spec(c):
    """Parse the window after IN into a canonical spec tuple (see A.Windowed):
    LAST n DAYS | FROM a DAYS AGO TO b DAYS AGO | n DAYS AGO | YESTERDAY."""
    if c.at("KEYWORD", "YESTERDAY"):
        c.eat()
        return ("yesterday",)
    if c.at("KEYWORD", "LAST"):
        c.eat()
        return ("rolling", _int_days(c))
    if c.at("KEYWORD", "FROM"):
        c.eat()
        a = _days_ago(c)
        c.eat("KEYWORD", "TO")
        b = _days_ago(c)
        return ("range", a, b)
    if c.at("NUMBER"):
        return ("day", _days_ago(c))
    raise ParseError("expected a window after IN — LAST n DAYS, FROM a DAYS AGO "
                     "TO b DAYS AGO, n DAYS AGO, or YESTERDAY", c.cur.line)


def _comparison(c):
    node = _additive(c)
    # Inline baseline/trend window: `<expr> IN <window>` yields expr's metrics
    # over that date range. Distinguished from membership `x IN [list]` by what
    # follows IN (a window keyword / bare number vs a list or value).
    if c.at("KEYWORD", "IN") and _window_after_in(c):
        line = c.cur.line
        c.eat()
        node = A.Windowed(expr=node, window=_window_spec(c), line=line)
    # relational / equality
    if c.at("OP") and c.cur.value in _COMPARATORS:
        op = c.eat().value
        return A.Compare(op=op, left=node, right=_additive(c))
    if c.at("KEYWORD", "IS"):
        c.eat()
        neg = False
        if c.at("KEYWORD", "NOT"):
            c.eat()
            neg = True
        right = _additive(c)
        return A.Compare(op="!=" if neg else "==", left=node, right=right)
    if c.at("KEYWORD", "IN"):
        c.eat()
        return A.Compare(op="IN", left=node, right=_additive(c))
    if c.at("KEYWORD", "NOT"):
        # NOT IN / NOT CONTAINS
        c.eat()
        if c.at("KEYWORD", "IN"):
            c.eat()
            return A.Compare(op="NOT IN", left=node, right=_additive(c))
        if c.at("KEYWORD", "CONTAINS"):
            c.eat()
            return A.Compare(op="NOT CONTAINS", left=node, right=_additive(c))
        raise ParseError("expected IN or CONTAINS after NOT", c.cur.line)
    if c.at("KEYWORD", "CONTAINS"):
        c.eat()
        return A.Compare(op="CONTAINS", left=node, right=_additive(c))
    if c.at("KEYWORD", "STARTS"):
        c.eat()
        c.eat("KEYWORD", "WITH")
        return A.Compare(op="STARTS WITH", left=node, right=_additive(c))
    if c.at("KEYWORD", "ENDS"):
        c.eat()
        c.eat("KEYWORD", "WITH")
        return A.Compare(op="ENDS WITH", left=node, right=_additive(c))
    return node


def _additive(c):
    node = _multiplicative(c)
    while c.at("OP") and c.cur.value in ("+", "-"):
        op = c.eat().value
        node = A.Binary(op=op, left=node, right=_multiplicative(c))
    return node


def _multiplicative(c):
    node = _unary(c)
    while c.at("OP") and c.cur.value in ("*", "/", "%"):
        op = c.eat().value
        node = A.Binary(op=op, left=node, right=_unary(c))
    return node


def _unary(c):
    if c.at("OP", "-"):
        c.eat()
        return A.Unary(op="-", operand=_unary(c))
    return _postfix(c)


def _postfix(c):
    node = _primary(c)
    while c.at("DOT"):
        c.eat("DOT")
        tok = c.eat("IDENT")
        if c.at("LPAREN"):
            raise ParseError("method calls are only allowed as statements, "
                             "not inside expressions", c.cur.line)
        node = A.Field(obj=node, name=tok.value, line=tok.line)
    return node


def _primary(c):
    t = c.cur
    # IF is both a statement keyword and the IF(cond,then,else) function.
    if c.at("KEYWORD", "IF") and c.toks[c.i + 1].kind == "LPAREN":
        c.eat()
        args = _call_args(c)
        return A.Call(fn="IF", args=args, line=t.line)
    if c.at("NUMBER"):
        c.eat()
        return A.Num(value=t.value)
    if c.at("MONEY"):
        c.eat()
        return A.Money(value=t.value)
    if c.at("PERCENT"):
        c.eat()
        return A.Percent(value=t.value)
    if c.at("STRING"):
        c.eat()
        return A.Str(value=t.value)
    if c.at("KEYWORD", "TRUE"):
        c.eat()
        return A.Bool(value=True)
    if c.at("KEYWORD", "FALSE"):
        c.eat()
        return A.Bool(value=False)
    if c.at("KEYWORD", "NONE"):
        c.eat()
        return A.NoneLit()
    if c.at("LBRACK"):
        c.eat("LBRACK")
        items = []
        if not c.at("RBRACK"):
            items.append(_expr(c))
            while c.at("COMMA"):
                c.eat("COMMA")
                items.append(_expr(c))
        c.eat("RBRACK")
        return A.ListLit(items=items)
    if c.at("LPAREN"):
        c.eat("LPAREN")
        node = _expr(c)
        c.eat("RPAREN")
        return node
    if c.at("IDENT"):
        name = c.eat("IDENT").value
        if c.at("LPAREN"):
            if name.upper() not in _FUNCTIONS:
                raise ParseError(f"unknown function {name!r}", t.line)
            args = _call_args(c)
            return A.Call(fn=name.upper(), args=args, line=t.line)
        return A.Ident(name=name, line=t.line)
    raise ParseError(f"unexpected {t.kind} {t.value!r}", t.line, t.col)
