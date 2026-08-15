#!/usr/bin/env python3
"""Rules DSL AST node types (Spec B Layer 1). Plain dataclasses; the parser
builds these, the evaluator walks them."""

from dataclasses import dataclass, field
from typing import Any, List, Optional


# ---- program structure -------------------------------------------------------

@dataclass
class Program:
    rules: List["ForEach"] = field(default_factory=list)


@dataclass
class ForEach:
    entity: str                     # keyword | target | searchTerm | campaign | adGroup | product
    alias: Optional[str]            # the loop-var name (defaults to entity)
    window: str                     # "CURRENT" | "LIFETIME" | "ROLLING"
    body: List[Any]                 # Let | If | Action | Note
    line: int = 0
    window_days: Optional[int] = None   # set only when window == "ROLLING"


@dataclass
class If:
    cond: Any
    body: List[Any]
    line: int = 0


@dataclass
class Let:
    name: str
    expr: Any
    line: int = 0


@dataclass
class Action:
    target: Any                     # expression naming the entity (Ident/Field)
    verb: str                       # pause | enable | setBid | setBudget | addNegative | createKeyword | note | ...
    args: List[Any]
    line: int = 0


# ---- expressions -------------------------------------------------------------

@dataclass
class Num:
    value: float

@dataclass
class Money:
    value: float

@dataclass
class Percent:
    value: float

@dataclass
class Str:
    value: str

@dataclass
class Bool:
    value: bool

@dataclass
class NoneLit:
    pass

@dataclass
class ListLit:
    items: List[Any]

@dataclass
class Ident:
    name: str
    line: int = 0

@dataclass
class Field:
    obj: Any
    name: str
    line: int = 0

@dataclass
class Unary:
    op: str
    operand: Any

@dataclass
class Binary:
    op: str
    left: Any
    right: Any

@dataclass
class Compare:
    op: str
    left: Any
    right: Any

@dataclass
class Logic:
    op: str                         # AND | OR
    left: Any
    right: Any

@dataclass
class Not:
    operand: Any

@dataclass
class Call:
    fn: str
    args: List[Any]
    line: int = 0


@dataclass
class Windowed:
    """An inline baseline/trend window: `<expr> IN <window>` evaluates expr's
    metrics over a different date range for the current entity. `window` is a
    canonical spec tuple the evaluator resolves to dates:
      ("rolling", n)    LAST n DAYS            (settled: ends lag days back)
      ("range", a, b)   FROM a DAYS AGO TO b DAYS AGO   (unlagged offsets)
      ("day", n)        n DAYS AGO             (one unlagged day)
      ("yesterday",)    the latest settled day (lag days back)"""
    expr: Any
    window: tuple
    line: int = 0
