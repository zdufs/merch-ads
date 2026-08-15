#!/usr/bin/env python3
"""Rules DSL lexer — tokenizes rule source into a flat token stream with
INDENT/DEDENT markers (Python-style, two-space unit; tabs rejected).

The DSL is small and readable (mirrors MerchDash's language). Keywords are
case-insensitive. Literals: money $0.85, percent 45% (=0.45), plain numbers,
"double-quoted strings". `#` starts a line comment."""


KEYWORDS = {
    "FOR", "EACH", "AS", "IN", "IF", "WHEN", "AND", "OR", "NOT", "LET",
    "CURRENT", "LIFETIME", "LAST", "DAYS", "DAY", "TRUE", "FALSE", "NONE",
    "CONTAINS", "STARTS", "ENDS", "WITH", "IS",
    # baseline / trend windows: FROM n DAYS AGO TO m DAYS AGO, n DAYS AGO, YESTERDAY
    "FROM", "TO", "AGO", "YESTERDAY",
}

# multi-char operators tried longest-first
_OPS3 = ()
_OPS2 = ("==", "!=", "<>", "<=", ">=")
_OPS1 = "=<>+-*/%"

_PUNCT = {
    "(": "LPAREN", ")": "RPAREN", "[": "LBRACK", "]": "RBRACK",
    ":": "COLON", ",": "COMMA", ".": "DOT",
}


class Token:
    __slots__ = ("kind", "value", "line", "col")

    def __init__(self, kind, value, line, col):
        self.kind = kind
        self.value = value
        self.line = line
        self.col = col

    def __repr__(self):
        return f"Token({self.kind}, {self.value!r}, {self.line}:{self.col})"


class LexError(Exception):
    def __init__(self, message, line, col):
        super().__init__(f"{message} (line {line}, col {col})")
        self.message = message
        self.line = line
        self.col = col


def _is_ident_start(c):
    return c.isalpha() or c == "_"


def _is_ident(c):
    return c.isalnum() or c == "_"


def tokenize(src):
    tokens = []
    indent_stack = [0]
    lines = src.split("\n")
    for lineno, raw in enumerate(lines, start=1):
        # strip a trailing comment (outside strings — handled during scan below,
        # but a leading/whitespace-only comment line is skipped wholesale)
        stripped = raw.lstrip(" ")
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise LexError("tabs are not allowed for indentation; use spaces", lineno, 1)
        # blank or comment-only line: no indentation change, emit a NEWLINE
        if stripped == "" or stripped.startswith("#"):
            tokens.append(Token("NEWLINE", "\n", lineno, len(raw) + 1))
            continue
        indent = len(raw) - len(stripped)
        if indent > indent_stack[-1]:
            indent_stack.append(indent)
            tokens.append(Token("INDENT", indent, lineno, 1))
        while indent < indent_stack[-1]:
            indent_stack.pop()
            tokens.append(Token("DEDENT", indent, lineno, 1))
            if indent > indent_stack[-1]:
                raise LexError("inconsistent dedent", lineno, 1)
        col = indent
        i = indent
        n = len(raw)
        while i < n:
            c = raw[i]
            if c == " ":
                i += 1
                continue
            if c == "#":
                break  # rest of line is a comment
            start_col = i + 1
            if c == "$":
                j = i + 1
                while j < n and (raw[j].isdigit() or raw[j] == "."):
                    j += 1
                num = raw[i + 1:j]
                if not num:
                    raise LexError("expected number after $", lineno, start_col)
                tokens.append(Token("MONEY", float(num), lineno, start_col))
                i = j
                continue
            if c.isdigit():
                j = i
                while j < n and (raw[j].isdigit() or raw[j] == "."):
                    j += 1
                num = raw[i:j]
                if j < n and raw[j] == "%":
                    tokens.append(Token("PERCENT", float(num) / 100.0, lineno, start_col))
                    i = j + 1
                else:
                    tokens.append(Token("NUMBER", float(num), lineno, start_col))
                    i = j
                continue
            if c == '"':
                j = i + 1
                buf = []
                while j < n and raw[j] != '"':
                    if raw[j] == "\\" and j + 1 < n:
                        buf.append(raw[j + 1])
                        j += 2
                        continue
                    buf.append(raw[j])
                    j += 1
                if j >= n:
                    raise LexError("unterminated string", lineno, start_col)
                tokens.append(Token("STRING", "".join(buf), lineno, start_col))
                i = j + 1
                continue
            if _is_ident_start(c):
                j = i
                while j < n and _is_ident(raw[j]):
                    j += 1
                word = raw[i:j]
                upper = word.upper()
                kind = "KEYWORD" if upper in KEYWORDS else "IDENT"
                tokens.append(Token(kind, upper if kind == "KEYWORD" else word,
                                    lineno, start_col))
                i = j
                continue
            two = raw[i:i + 2]
            if two in _OPS2:
                tokens.append(Token("OP", two, lineno, start_col))
                i += 2
                continue
            if c in _OPS1:
                tokens.append(Token("OP", c, lineno, start_col))
                i += 1
                continue
            if c in _PUNCT:
                tokens.append(Token(_PUNCT[c], c, lineno, start_col))
                i += 1
                continue
            raise LexError(f"unexpected character {c!r}", lineno, start_col)
        tokens.append(Token("NEWLINE", "\n", lineno, n + 1))
    # close out remaining indents
    last_line = len(lines)
    while len(indent_stack) > 1:
        indent_stack.pop()
        tokens.append(Token("DEDENT", 0, last_line, 1))
    tokens.append(Token("EOF", None, last_line, 1))
    return tokens
