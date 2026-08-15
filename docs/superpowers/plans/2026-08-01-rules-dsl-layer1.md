# Rules DSL — Layer 1 (Language Core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline) — the tasks share files (`rules/` package) and must be built in order. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A read-only economics-aware rule language: tokenize → parse → evaluate a rule against a market DB and return proposed changes with per-condition debug traces, exposed via `appctl rules-validate` and `rules-preview`. NO writes in Layer 1.

**Architecture:** New `rules/` Python package: `lexer.py` → `parser.py` (AST) → `evaluator.py` (walks entities, resolves fields, evaluates conditions, records proposed actions). Economics fields resolve through the SAME helpers the phases use (`products.get_design_econ`, `products.econ_gate`, `db.active_price_changes`, `tamas_halo`) — never a re-implementation. `appctl` wraps it.

**Tech Stack:** Python 3 stdlib only; `unittest` + temp-SQLite fixtures (pattern: `tests/econ_tests.py`, `tests/accumulated_tests.py`).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-01-rules-dsl-design.md`. Read it first.
- Windows are `CURRENT` (latest cumulative snapshot) and `LIFETIME` only. `IN LAST N DAYS` etc. is a **validation error** — the per-entity daily data does not exist.
- Perf tables are cumulative snapshots: read the latest date, SUM across entities within it, never across dates.
- Zero-denominator ratios (acos with 0 sales) resolve to `None`; a `None` value never satisfies a numeric comparison (so economics-gated rules skip zero-sale/econ-unavailable rows — fail-closed, matching the phases).
- Economics fields resolve via existing helpers; when economics is unavailable for a design, EVERY economics field returns `None`.
- Layer 1 is READ-ONLY: no `ads_client`, no `writes_log`, no KILL needed (no writes).
- Keywords/fields/functions are case-insensitive. Money `$0.85`, percent `45%`=0.45.
- Branch `tamas-method-halo-candidates`; commit per task with `git commit -F -`; never `main`.
- Test command: `python3 -m unittest tests.<module> -v` from the Ads folder.
- Reuse Spec A's trace shape: proposed changes carry `trace:[{condition,actual,threshold,pass}]` (same fields as `appctl._cond`).

---

## Task 1: Lexer

**Files:**
- Create: `rules/__init__.py` (empty), `rules/lexer.py`
- Test: `tests/rules_lexer_tests.py`

**Interfaces:**
- Produces: `rules.lexer.tokenize(src: str) -> list[Token]`; `Token` = a small class/namedtuple `Token(kind, value, line, col)`. Kinds: `KEYWORD, IDENT, NUMBER, MONEY, PERCENT, STRING, OP, NEWLINE, INDENT, DEDENT, EOF, LBRACK, RBRACK, LPAREN, RPAREN, COLON, COMMA, DOT`. `LexError(message, line, col)` raised on bad input.
- Keywords set: `FOR EACH AS IN IF WHEN AND OR NOT LET CURRENT LIFETIME TRUE FALSE NONE CONTAINS STARTS ENDS WITH IS`.

- [ ] **Step 1: Write failing tests**

```python
# tests/rules_lexer_tests.py
import os, sys, unittest
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from rules.lexer import tokenize, LexError  # noqa: E402


def kinds(src):
    return [t.kind for t in tokenize(src) if t.kind not in ("NEWLINE", "EOF")]


class Lexer(unittest.TestCase):
    def test_numbers_money_percent(self):
        toks = [t for t in tokenize("$0.85 45% 12") if t.kind in ("MONEY", "PERCENT", "NUMBER")]
        self.assertEqual([(t.kind, t.value) for t in toks],
                         [("MONEY", 0.85), ("PERCENT", 0.45), ("NUMBER", 12.0)])

    def test_keywords_case_insensitive(self):
        ks = kinds("for each Keyword")
        self.assertEqual(ks[:2], ["KEYWORD", "KEYWORD"])  # FOR, EACH
        # 'Keyword' is an IDENT (entity name), not a language keyword
        self.assertEqual(ks[2], "IDENT")

    def test_comment_and_string(self):
        toks = tokenize('LET x = "hi"  # ignored\n')
        vals = [t.value for t in toks if t.kind == "STRING"]
        self.assertEqual(vals, ["hi"])

    def test_indent_dedent(self):
        src = "FOR EACH keyword:\n  IF x:\n    y\n"
        ks = [t.kind for t in tokenize(src)]
        self.assertIn("INDENT", ks)
        self.assertIn("DEDENT", ks)

    def test_bad_char_raises(self):
        with self.assertRaises(LexError):
            tokenize("a ^ b")   # ^ not an operator here


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run — expect fail** (`ModuleNotFoundError: rules.lexer`).
- [ ] **Step 3: Implement `rules/lexer.py`.** Line-oriented; track indentation with a stack to emit `INDENT`/`DEDENT` (two-space unit, but accept any consistent indent; tabs → LexError). Recognize: `$<num>`→MONEY, `<num>%`→PERCENT, `<num>`→NUMBER (float), `"..."`→STRING (no escapes needed beyond `\"`), identifiers→KEYWORD if upper in keyword set else IDENT, multi-char ops `== != <> <= >=`, single-char `= < > + - * / % . , : ( ) [ ]`, `#` to end of line = comment. Blank lines emit NEWLINE only (no INDENT churn). At EOF emit DEDENTs to 0 then EOF.
- [ ] **Step 4: Run — expect pass.**
- [ ] **Step 5: Commit** (`git add rules/ tests/rules_lexer_tests.py` → "Rules DSL: lexer").

---

## Task 2: Parser + AST

**Files:**
- Create: `rules/ast_nodes.py`, `rules/parser.py`
- Test: `tests/rules_parser_tests.py`

**Interfaces:**
- Consumes: `rules.lexer.tokenize`.
- Produces: `rules.parser.parse(src) -> Program`. AST nodes (dataclasses) in `ast_nodes.py`: `Program(rules)`, `ForEach(entity, alias, window, body)`, `If(cond, body)`, `Let(name, expr)`, `Action(target, verb, args)`, `Note(text_parts)`, and expression nodes `Num/Money/Percent/Str/Bool/NoneLit/List/Ident/Field(obj, name)/Unary/Binary/Compare/Call(fn,args)`. `ParseError(message, line, col)`.
- Window: `"CURRENT"` (default when omitted) or `"LIFETIME"`; anything else (e.g. `LAST`) → `ParseError` with message "windows are CURRENT or LIFETIME only (no rolling windows — the data is a cumulative snapshot)".

- [ ] **Step 1: Write failing tests**

```python
# tests/rules_parser_tests.py
import os, sys, unittest
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from rules.parser import parse, ParseError  # noqa: E402


class Parser(unittest.TestCase):
    def test_minimal_for_each_if_action(self):
        p = parse("FOR EACH keyword:\n  IF keyword.acos > 45%:\n    keyword.pause()\n")
        self.assertEqual(len(p.rules), 1)
        fe = p.rules[0]
        self.assertEqual(fe.entity, "keyword")
        self.assertEqual(fe.window, "CURRENT")
        self.assertEqual(fe.body[0].__class__.__name__, "If")

    def test_rolling_window_rejected(self):
        with self.assertRaises(ParseError):
            parse("FOR EACH keyword IN LAST 7 DAYS:\n  keyword.pause()\n")

    def test_lifetime_window(self):
        p = parse("FOR EACH product IN LIFETIME:\n  product.note(\"x\")\n")
        self.assertEqual(p.rules[0].window, "LIFETIME")

    def test_let_and_compare_precedence(self):
        p = parse("FOR EACH keyword:\n  LET b = keyword.bid * 0.85\n  IF keyword.orders >= 1 AND keyword.acos > break_even:\n    keyword.setBid(MAX($0.05, b))\n")
        self.assertEqual(p.rules[0].body[0].__class__.__name__, "Let")

    def test_parse_error_has_line(self):
        with self.assertRaises(ParseError) as cm:
            parse("FOR EACH keyword:\n  IF keyword.acos >:\n    keyword.pause()\n")
        self.assertEqual(cm.exception.line, 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run — expect fail.**
- [ ] **Step 3: Implement** a recursive-descent parser: `program` = list of `ForEach`; expression grammar with precedence `OR < AND < NOT < comparison < add/sub < mul/div/mod < unary < postfix(.field / call) < primary`. `Field` handles `keyword.acos` and bare `break_even` (an `Ident` resolved against the loop entity at eval time). Actions = `target.verb(args)`. `note("... {x} ...")` keeps the raw string (placeholder parsing happens at eval). Raise `ParseError(line,col)` on unexpected token; reject non-`CURRENT/LIFETIME` windows.
- [ ] **Step 4: Run — expect pass.**
- [ ] **Step 5: Commit** ("Rules DSL: parser + AST").

---

## Task 3: Value model + expression/condition evaluator

**Files:**
- Create: `rules/values.py` (unit-aware value helpers), `rules/evaluator.py` (expression eval only in this task)
- Test: `tests/rules_eval_tests.py`

**Interfaces:**
- Produces: `rules.evaluator.eval_expr(node, scope: dict) -> value` where `scope` maps names → python values (numbers/str/bool/None/list). Comparators return bool; `None` operand in a numeric/`<>` comparison → `False` (never matches). Text ops case-insensitive. Functions `MIN MAX CLAMP ROUND FLOOR CEIL ABS IF LOWER UPPER`. Money/percent are plain floats at eval time.
- `evaluator.eval_condition(node, scope) -> bool`.

- [ ] **Step 1: Write failing tests**

```python
# tests/rules_eval_tests.py
import os, sys, unittest
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from rules.parser import parse_expr  # small helper exposed for tests
from rules.evaluator import eval_expr, eval_condition  # noqa: E402


def E(src, scope=None):
    return eval_expr(parse_expr(src), scope or {})


class Eval(unittest.TestCase):
    def test_arithmetic_units(self):
        self.assertAlmostEqual(E("$0.10 * 2"), 0.20)
        self.assertAlmostEqual(E("45% + 5%"), 0.50)

    def test_none_comparison_is_false(self):
        self.assertFalse(eval_condition(parse_expr("x > 0.45"), {"x": None}))
        self.assertFalse(eval_condition(parse_expr("x < 0.45"), {"x": None}))

    def test_functions(self):
        self.assertEqual(E("MAX($0.05, 0.02)"), 0.05)
        self.assertEqual(E("CLAMP(9, 0, 1.5)"), 1.5)
        self.assertEqual(E("ROUND(0.126, 2)"), 0.13)

    def test_text_ops_case_insensitive(self):
        self.assertTrue(eval_condition(parse_expr('name CONTAINS "xmas"'), {"name": "Merry XMAS tee"}))

    def test_and_or_not(self):
        self.assertTrue(eval_condition(parse_expr("a AND (b OR NOT c)"),
                                       {"a": True, "b": False, "c": False}))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run — expect fail** (`parse_expr` / evaluator missing).
- [ ] **Step 3: Implement** `parse_expr` (expose the parser's expression entry for tests), `values.py` (money/percent already floats; helpers for None-safe compare), and `eval_expr`/`eval_condition`. Numeric comparison with a `None` operand → `False`. `IN`/`NOT IN` over lists. Text `CONTAINS/STARTS WITH/ENDS WITH`/`=` case-insensitive on strings.
- [ ] **Step 4: Run — expect pass.**
- [ ] **Step 5: Commit** ("Rules DSL: value model + expression evaluator").

---

## Task 4: Entity loaders + metric field resolver

**Files:**
- Create: `rules/entities.py`
- Test: `tests/rules_entities_tests.py`

**Interfaces:**
- Produces: `rules.entities.load(conn, entity_kind) -> list[EntityRow]` for `keyword`/`target`/`searchTerm`/`campaign`/`adGroup`/`product`. Each `EntityRow` exposes `.field(name)` returning metric/setting values at the latest snapshot; unknown field → `FieldError`. `.raw` dict for the evaluator scope; `.id`, `.label`.
- Metric fields: `impressions clicks spend cost sales orders units acos roas ctr cvr cpc`; identity/settings: `bid bid_inherited state name match_type keyword_text search_term asin ad_type targeting_type budget bidding_strategy days_since_bid_change`. `acos`/`cvr` computed via the same `_acos`/`_cvr` (None-on-zero) semantics.

- [ ] **Step 1: Write failing tests** — seed a temp DB (reuse the `accumulated_tests.seed` idiom: campaigns/ad_groups/ad_group_product/targeting_perf) and assert `load(conn,"target")` returns rows whose `.field("clicks")`, `.field("acos")` match hand values, and `.field("acos")` is `None` when sales are 0.
- [ ] **Step 2: Run — expect fail.**
- [ ] **Step 3: Implement** loaders querying the latest snapshot (`SELECT MAX(date)` per table), mirroring the existing `cmd_targets`/`cmd_campaigns`/`cmd_searchterms` queries. `days_since_bid_change` from `writes_log` (latest `bid_change` for the target). Relations (`keyword.campaign`) resolved lazily via id maps.
- [ ] **Step 4: Run — expect pass.**
- [ ] **Step 5: Commit** ("Rules DSL: entity loaders + metric fields").

---

## Task 5: Economics field resolver (the moat)

**Files:**
- Create: `rules/econ_fields.py`
- Test: `tests/rules_econ_fields_tests.py`

**Interfaces:**
- Consumes: `products.get_design_econ`, `products.econ_gate`, `db.get_design_map`, `db.active_price_changes`, `db.get_lifetime_map`, and (US) `tamas_halo`.
- Produces: `rules.econ_fields.resolve(conn, entity_row) -> dict` adding `break_even royalty profit royalty_roi product_type is_cohort lifetime_sales in_transition econ_available halo_est net_halo organic_per_day`. When economics is unavailable for the design (unmapped / cohort / transition-unknown / unsupported price / econ tables absent), `econ_available=False` and every economics numeric field = `None`. Off-US, halo fields = `None`.

- [ ] **Step 1: Write failing tests** — temp DB with the price-aware econ tables migrated (like `tests/econ_tests.py`): a mapped standard tee resolves `break_even` to the expected value and `econ_available=True`; a design in a 30-day price transition resolves `in_transition=True` and economics fields `None`; a NULL-asin cohort → `is_cohort=True`, economics `None`.
- [ ] **Step 2: Run — expect fail.**
- [ ] **Step 3: Implement** by REUSING the phase resolvers (do not re-derive economics). Break-even from the same path `appctl._design_be_for` uses (`products.get_design_econ` + transition handling). `royalty`/`profit`/`royalty_roi` from the same royalty source as `cmd_profit`. Halo fields from `tamas_halo` (US only; guard `supported`). Fail-closed everywhere economics can't be resolved.
- [ ] **Step 4: Run — expect pass.**
- [ ] **Step 5: Commit** ("Rules DSL: economics field resolver (reuses phase economics)").

---

## Task 6: Rule runner (read-only) + `rules-validate` / `rules-preview`

**Files:**
- Create: `rules/runner.py`
- Modify: `appctl.py` (add `cmd_rules_validate`, `cmd_rules_preview`; register in `build_parser` + `DISPATCH`)
- Test: `tests/rules_runner_tests.py`

**Interfaces:**
- Produces: `rules.runner.preview(conn, src) -> {ok, market, evaluated, matched, changes:[{entity_kind, entity_id, label, action, args, note, trace:[{condition,actual,threshold,pass}]}], errors}`. Walks each `ForEach`, loads entities (Task 4) + econ fields (Task 5) into scope, evaluates conditions (Task 3), and for matched rows records the would-be actions WITHOUT executing them, attaching a `trace` built from the rule's top-level `IF` comparisons (reuse `appctl._cond` shape). `rules.runner.validate(src) -> {ok, errors:[{line,col,message}]}` (lex+parse only).
- `appctl rules-validate` reads rule text from **stdin**, returns `{ok, errors}`.
- `appctl rules-preview` reads rule text from **stdin** (or `--rule <name>` later in Layer 3), returns the preview envelope.

- [ ] **Step 1: Write failing tests**

```python
# tests/rules_runner_tests.py — seed like accumulated_tests, then:
#   src = 'FOR EACH target:\n  IF target.clicks >= 15 AND target.orders = 0:\n    target.pause() note("{clicks} clicks 0 sales")\n'
#   res = rules.runner.preview(conn, src)
#   assert res["ok"]; assert res["matched"] == <hand count>
#   change = res["changes"][0]; assert change["action"] == "pause"
#   assert any(c["condition"] and c["pass"] for c in change["trace"])
# also: validate() returns ok False + a line/col for a syntax error;
#       an economics rule (IF target.profit < 0) skips econ-unavailable rows.
```
(Write the concrete assertions with a seeded fixture mirroring `tests/accumulated_tests.py`.)

- [ ] **Step 2: Run — expect fail.**
- [ ] **Step 3: Implement** `runner.preview`/`validate`; add `cmd_rules_validate`/`cmd_rules_preview` to `appctl.py` reading stdin, plus `build_parser` entries (`sub.add_parser("rules-validate")`, `sub.add_parser("rules-preview")`) and `DISPATCH`. These are read-only → also add to the Swift `fastCommands` list is NOT needed yet (Layer 4). Note stdin-reading commands are one-shot (not `serve`).
- [ ] **Step 4: Run — expect pass; smoke:** `echo 'FOR EACH target:\n  IF target.clicks >= 15 AND target.orders = 0:\n    target.pause()' | ADS_MARKET=DE python3 appctl.py rules-preview` returns matched changes with traces.
- [ ] **Step 5: Commit** ("Rules DSL: read-only runner + rules-validate/rules-preview").

---

## Final verification (Layer 1)

- [ ] `python3 -m unittest tests.rules_lexer_tests tests.rules_parser_tests tests.rules_eval_tests tests.rules_entities_tests tests.rules_econ_fields_tests tests.rules_runner_tests -v` all green.
- [ ] `rules-validate` rejects `IN LAST 7 DAYS` with the snapshot-data message.
- [ ] `rules-preview` on a real market returns proposed changes + traces, and skips econ-unavailable rows for an economics rule.
- [ ] No writes occurred anywhere (Layer 1 is read-only); no `ads_client`/`writes_log` imports in `rules/`.
- [ ] Update `memory/merchads-app-progress.md` with Layer 1 shipped.

## Self-review notes
- Every spec Layer-1 requirement maps to a task: lexer(1), parser+window-guard(2), evaluator+None-semantics(3), entity metrics(4), economics moat(5), runner+endpoints(6).
- Economics reuse (Task 5) is the key correctness risk — the plan mandates calling the SAME resolvers as the phases, never re-deriving.
- Layers 2–4 (actions/safety, storage/scheduling, app editor) are separate plans, authored after Layer 1 lands.
