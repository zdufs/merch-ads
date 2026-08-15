# Scavenger / Cohort Winner Promotion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the operator promote a converting search term that came from a multi-design cohort (Scavenger / AUTO) to its correct *family* of designs — the engine suggests the family from the whole catalogue, the operator confirms/edits, then a focused ad group + exact keyword is created and the source cohort is negated.

**Architecture:** Two new small Python modules (`harvest_suggest.py` for whole-word title ranking; `harvest_promote_group.py` for the family plan + apply, reusing `phase4_harvest_create` helpers), two new `appctl.py` endpoints (`harvest-suggest` read, `harvest-promote-group` action), a per-winner `needs_design` flag on the existing `harvest` read, and a "Needs a design" section in the SwiftUI Harvest tab. Every live write is operator-run through the existing `ads_client` rails.

**Tech Stack:** Python 3 (stdlib + sqlite3), the existing `ads_client`/`db`/`phase4_harvest_create` engine modules, SwiftUI (macOS), the app's `PythonBridge` + `ActionCoordinator`.

## Global Constraints

- Cohort = a source ad group with **NULL asin** in `ad_group_product` (Scavenger and AUTO campaigns). These are the only winners this feature handles.
- Suggestions search the **whole catalogue**, ranked by **whole-word** (token equality) title overlap — never substring (so "michael" ≠ Carmichael, "foo" ≠ Football).
- Promote **reuses** the "Harvested &lt;type&gt; - Exact" campaign (`phase4_harvest_create.camp_name`); **one ad group per (phrase, product_type)** holding the chosen designs as product ads + the phrase as an EXACT keyword.
- New keyword **bid = phrase CPC × 1.15**, floored at `$0.10`, clamped by the per-market ceiling (`ads_client` applies the ceiling itself).
- After promoting, **add a negative-exact for the phrase in the source cohort ad group**.
- Mark `harvest_log.promoted = 1` for `(search_term, source_ad_group_id)` and log a `harvest_promote` `writes_log` row via `db.log_write`.
- The promote is a **live account write → operator-run**. `harvest-promote-group` defaults to a **dry run** (returns the plan, writes nothing); `--apply` executes and is only ever triggered by the operator clicking Promote in the app. On `--apply` it honors KILL via `_guard_kill()` and the US economics freshness gate via `_check_econ_gate()` — matching the sibling `cmd_promote`.
- Money `cpc`/`acos` are fractions; money is in the market currency. `appctl` returns exactly one JSON line `{"ok":true,"data":…}` via `out(...)`.

Existing interfaces this plan builds on (verbatim):
- `harvest_log(search_term, source_ad_group_id, kind, product_type, source_campaign_id, clicks, orders, sales, acos, cpc, first_seen, last_seen, promoted)` PK `(search_term, source_ad_group_id)`.
- `ad_group_product(ad_group_id, asin, product_type, brand, list_price, mapped_at, lifetime_sales)` — cohort rows have `asin IS NULL`.
- `ad_groups(ad_group_id, campaign_id, name, state, default_bid, pulled_at)` — `name` encodes `ASIN_type_Title` for design ad groups.
- `phase4_harvest_create.camp_name(pt) -> "Harvested <label> - Exact"`, `.existing_campaigns(client) -> {name: campaignId}`, `.success_ids(js, collection, idfield) -> {index: id}`, `.CAMPAIGN_BUDGET`.
- `ads_client.AdsClient`: `create_campaigns([{name,budget,startDate}])`, `create_ad_groups([{name,campaignId,defaultBid}])`, `create_product_ads([{campaignId,adGroupId,asin}])`, `create_keywords([{campaignId,adGroupId,keywordText,bid,matchType?=EXACT}])` (applies ceiling), `create_negative_keywords([{campaignId,adGroupId,keywordText}])`.
- `db.log_write(conn, action, entity_type, entity_id, detail, prev_state, result)`; `db.connect(ro=False)`.
- `appctl.out(data)` writes the JSON envelope; commands are registered in the dispatch dict (~line 4310) and via `sub.add_parser(...)`.

---

### Task 1: Title tokenizer + whole-catalogue suggester (`harvest_suggest.py`)

**Files:**
- Create: `harvest_suggest.py`
- Test: `tests/harvest_suggest_tests.py`

**Interfaces:**
- Produces:
  - `STOPWORDS: set[str]`
  - `tokenize(text: str) -> list[str]` — lowercased meaningful word tokens (drops stopwords + pure punctuation).
  - `catalogue_titles(conn) -> dict[str, tuple[str, str, int]]` — `asin -> (title, product_type, lifetime_sales)`, derived from `ad_groups.name` (strip the `ASIN_type_` prefix) joined to `ad_group_product`.
  - `suggest(conn, term: str, limit: int = 50) -> list[dict]` — ranked `{asin, title, product_type, matched_words, score, lifetime_sales}`, score = count of shared meaningful tokens, descending, tie-break by `lifetime_sales` desc; only `score > 0`; capped at `limit`.

- [ ] **Step 1: Write the failing test**

```python
# tests/harvest_suggest_tests.py
import os, sys, tempfile, unittest
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["ADS_MARKET"] = "US"
import db                # noqa: E402
import harvest_suggest   # noqa: E402


def temp_conn():
    fd, path = tempfile.mkstemp(suffix=".sqlite"); os.close(fd)
    real = db.DB_PATH; db.DB_PATH = path
    conn = db.connect(); db.DB_PATH = real
    return conn, path


def add_design(conn, asin, title, pt="standard_tshirt", life=0):
    # a design ad group whose NAME encodes ASIN_type_Title, mapped to the ASIN
    agid = "ag_" + asin
    conn.execute("INSERT INTO ad_groups(ad_group_id,campaign_id,name,state) VALUES(?,?,?,?)",
                 (agid, "c1", f"{asin}_{pt}_{title}", "ENABLED"))
    conn.execute("INSERT INTO ad_group_product(ad_group_id,asin,product_type,lifetime_sales) VALUES(?,?,?,?)",
                 (agid, asin, pt, life))
    conn.commit()


class Tokenize(unittest.TestCase):
    def test_drops_generic_words(self):
        self.assertEqual(harvest_suggest.tokenize("st michael t shirt"), ["st", "michael"])
        self.assertEqual(harvest_suggest.tokenize("rookie of the year first birthday outfit"),
                         ["rookie", "year", "first", "birthday"])


class Suggest(unittest.TestCase):
    def setUp(self): self.conn, self.path = temp_conn()
    def tearDown(self): self.conn.close(); os.unlink(self.path)

    def test_whole_word_not_substring(self):
        add_design(self.conn, "B1", "Saint Michael Archangel Streetwear")
        add_design(self.conn, "B2", "Carmichael Retro 70s 80s Sunset Stripe")
        add_design(self.conn, "B3", "Michaela Retro 70s 80s Sunset Stripe")
        out = harvest_suggest.suggest(self.conn, "st michael t shirt")
        asins = [r["asin"] for r in out]
        self.assertIn("B1", asins)          # 'Michael' whole word
        self.assertNotIn("B2", asins)       # 'Carmichael' is a different token
        self.assertNotIn("B3", asins)       # 'Michaela' is a different token

    def test_ranks_family_and_orders_by_match_then_sales(self):
        add_design(self.conn, "B1", "Daddy of Rookie 1st Birthday Baseball Theme Matching Party", life=5)
        add_design(self.conn, "B2", "Mama of Rookie 1st Birthday Baseball Theme Matching Party", life=9)
        add_design(self.conn, "B3", "Some Unrelated Football Design")
        out = harvest_suggest.suggest(self.conn, "rookie of the year first birthday outfit")
        asins = [r["asin"] for r in out]
        self.assertEqual(set(asins), {"B1", "B2"})   # B3 shares no meaningful token
        # equal score -> higher lifetime_sales first
        self.assertEqual(asins[0], "B2")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.harvest_suggest_tests -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'harvest_suggest'`.

- [ ] **Step 3: Write minimal implementation**

```python
# harvest_suggest.py
"""Whole-catalogue design suggester for promoting a cohort search-term winner.

A converting search term from a multi-design cohort (Scavenger/AUTO) belongs to a
FAMILY of designs, not one design and not the grab-bag. This ranks every design in
the catalogue by WHOLE-WORD title overlap with the term, so "michael" matches
'Saint Michael' but never 'Carmichael', and "foo" never matches 'Football'. The
result is a suggestion the operator confirms — never an automatic decision.
"""

import re

# Generic words that carry no design meaning — dropped before matching.
STOPWORDS = {
    "t", "tee", "tees", "shirt", "shirts", "tshirt", "tshirts", "the", "of", "for",
    "a", "an", "and", "or", "to", "with", "outfit", "outfits", "design", "designs",
    "gift", "gifts", "men", "women", "kids", "funny",
}
_WORD = re.compile(r"[a-z0-9]+")


def tokenize(text):
    """Lowercased meaningful word tokens (stopwords + punctuation removed)."""
    return [w for w in _WORD.findall((text or "").lower()) if w not in STOPWORDS]


def catalogue_titles(conn):
    """asin -> (title, product_type, lifetime_sales). Title comes from the design
    ad group's name (ASIN_type_Title); the ASIN prefix and type are stripped."""
    out = {}
    for asin, pt, life, name in conn.execute(
        """SELECT p.asin, p.product_type, p.lifetime_sales, ag.name
           FROM ad_group_product p JOIN ad_groups ag ON ag.ad_group_id = p.ad_group_id
           WHERE p.asin IS NOT NULL"""):
        # name is "ASIN_type_Title..." — the title is everything after the 2nd '_'
        title = name.split("_", 2)[-1] if name and name.count("_") >= 2 else (name or "")
        # keep the row with the richest title / highest lifetime for a repeated ASIN
        prev = out.get(asin)
        if prev is None or (life or 0) > prev[2]:
            out[asin] = (title, pt, life or 0)
    return out


def suggest(conn, term, limit=50):
    """Ranked design suggestions for a search term (score > 0 only)."""
    wanted = set(tokenize(term))
    if not wanted:
        return []
    rows = []
    for asin, (title, pt, life) in catalogue_titles(conn).items():
        matched = wanted & set(tokenize(title))
        if not matched:
            continue
        rows.append({"asin": asin, "title": title, "product_type": pt,
                     "matched_words": sorted(matched), "score": len(matched),
                     "lifetime_sales": life})
    rows.sort(key=lambda r: (-r["score"], -r["lifetime_sales"], r["asin"]))
    return rows[:limit]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.harvest_suggest_tests -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add harvest_suggest.py tests/harvest_suggest_tests.py
git commit -m "Add whole-catalogue design suggester for cohort winners"
```

---

### Task 2: `harvest-suggest` read endpoint (appctl)

**Files:**
- Modify: `appctl.py` (add `cmd_harvest_suggest`, `sub.add_parser("harvest-suggest")`, dispatch entry)
- Test: `tests/harvest_suggest_tests.py` (add an endpoint test)

**Interfaces:**
- Consumes: `harvest_suggest.suggest`.
- Produces: `appctl harvest-suggest --term "<phrase>" [--limit N=50]` → `{"ok":true,"data":{term, count, suggestions:[…]}}`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/harvest_suggest_tests.py
import json, subprocess

class Endpoint(unittest.TestCase):
    def test_cli_returns_envelope(self):
        # smoke: unknown term -> empty suggestions, ok:true
        env = dict(os.environ, ADS_MARKET="US")
        p = subprocess.run(["python3", "appctl.py", "harvest-suggest", "--term", "zzz-nomatch-zzz"],
                           capture_output=True, cwd=HERE, env=env)
        d = json.loads(p.stdout.decode())
        self.assertTrue(d["ok"])
        self.assertEqual(d["data"]["count"], 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.harvest_suggest_tests.Endpoint -v`
Expected: FAIL — `invalid choice: 'harvest-suggest'` (non-zero exit / JSON error).

- [ ] **Step 3: Write minimal implementation**

Add near the other read commands in `appctl.py` (import `harvest_suggest` at top with the other imports):

```python
def cmd_harvest_suggest(args):
    """Ranked whole-catalogue design suggestions for a cohort winner (read-only)."""
    conn = db.connect(ro=True)
    rows = harvest_suggest.suggest(conn, args.term, limit=args.limit)
    out({"term": args.term, "count": len(rows), "suggestions": rows})
```

Register the parser (near `sub.add_parser("harvest")`):

```python
sp = sub.add_parser("harvest-suggest")
sp.add_argument("--term", required=True)
sp.add_argument("--limit", type=int, default=50)
```

Add to the dispatch dict (near `"harvest": cmd_harvest`):

```python
"harvest-suggest": cmd_harvest_suggest,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.harvest_suggest_tests.Endpoint -v`
Expected: PASS.
Also spot-check live: `ADS_MARKET=US python3 appctl.py harvest-suggest --term "st michael t shirt"` returns Saint Michael designs ranked first.

- [ ] **Step 5: Commit**

```bash
git add appctl.py tests/harvest_suggest_tests.py
git commit -m "Add harvest-suggest read endpoint"
```

---

### Task 3: Family promote plan builder + apply (`harvest_promote_group.py`)

**Files:**
- Create: `harvest_promote_group.py`
- Test: `tests/harvest_promote_group_tests.py`

**Interfaces:**
- Consumes: `phase4_harvest_create.camp_name/existing_campaigns/success_ids/CAMPAIGN_BUDGET`, `ads_client.AdsClient`, `db`.
- Produces:
  - `build_group_plan(conn, term, source_ad_group_id, source_campaign_id, asins) -> dict`:
    `{term, source_ad_group_id, source_campaign_id, bid, groups:[{product_type, campaign_name, asins:[…]}], skipped_asins:[…]}`.
    `bid = max(0.10, round(cpc*1.15, 2))` where `cpc` is read from `harvest_log`; designs are grouped by `product_type` from `ad_group_product`; an ASIN with no product_type is dropped into `skipped_asins`.
  - `apply_group(client, conn, plan) -> dict`: creates campaign(s)/ad group(s)/product ads/keyword, negates the source, marks promoted, logs. Returns `{campaigns_created, ad_groups_created, keywords_created, negations, promoted:bool}`.
  - `promote_group(term, source_ad_group_id, source_campaign_id, asins, apply=False) -> dict`: opens a conn, builds the plan; if `apply` is False returns `{"plan": plan, "applied": False}`; if True constructs an `AdsClient`, calls `apply_group`, returns `{"plan": plan, "applied": True, "result": …}`.

- [ ] **Step 1: Write the failing test** (plan builder is the pure, high-value unit)

```python
# tests/harvest_promote_group_tests.py
import os, sys, tempfile, unittest
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["ADS_MARKET"] = "US"
import db                       # noqa: E402
import harvest_promote_group as hpg   # noqa: E402


def temp_conn():
    fd, path = tempfile.mkstemp(suffix=".sqlite"); os.close(fd)
    real = db.DB_PATH; db.DB_PATH = path
    conn = db.connect(); db.DB_PATH = real
    return conn, path


def add_winner(conn, term, src_ag="scav1", cpc=0.20):
    conn.execute("""INSERT INTO harvest_log(search_term,source_ad_group_id,kind,product_type,
                    source_campaign_id,cpc,promoted) VALUES(?,?,?,?,?,?,0)""",
                 (term, src_ag, "keyword", "standard_tshirt", "c1", cpc))
    conn.commit()


def add_design(conn, asin, pt="standard_tshirt"):
    conn.execute("INSERT INTO ad_group_product(ad_group_id,asin,product_type) VALUES(?,?,?)",
                 ("ag_"+asin, asin, pt))
    conn.commit()


class BuildGroupPlan(unittest.TestCase):
    def setUp(self): self.conn, self.path = temp_conn()
    def tearDown(self): self.conn.close(); os.unlink(self.path)

    def test_groups_by_type_and_computes_bid(self):
        add_winner(self.conn, "rookie first birthday", cpc=0.20)
        add_design(self.conn, "B1", "standard_tshirt")
        add_design(self.conn, "B2", "standard_tshirt")
        add_design(self.conn, "B3", "standard_pullover_hoodie")
        plan = hpg.build_group_plan(self.conn, "rookie first birthday", "scav1", "c1",
                                    ["B1", "B2", "B3"])
        self.assertEqual(plan["bid"], 0.23)                 # 0.20 * 1.15
        types = {g["product_type"]: set(g["asins"]) for g in plan["groups"]}
        self.assertEqual(types["standard_tshirt"], {"B1", "B2"})
        self.assertEqual(types["standard_pullover_hoodie"], {"B3"})
        self.assertTrue(all(g["campaign_name"].startswith("Harvested") for g in plan["groups"]))

    def test_unmapped_asin_is_skipped_not_crashed(self):
        add_winner(self.conn, "rookie first birthday")
        add_design(self.conn, "B1", "standard_tshirt")
        plan = hpg.build_group_plan(self.conn, "rookie first birthday", "scav1", "c1",
                                    ["B1", "GHOST"])
        self.assertEqual(plan["skipped_asins"], ["GHOST"])
        self.assertEqual([a for g in plan["groups"] for a in g["asins"]], ["B1"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.harvest_promote_group_tests -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'harvest_promote_group'`.

- [ ] **Step 3: Write minimal implementation**

```python
# harvest_promote_group.py
"""Promote a cohort search-term winner to a chosen FAMILY of designs.

Unlike phase4 (one ad group per single design), a cohort winner targets a set of
related designs the operator picked. This builds one ad group per product type
under the reused "Harvested <type> - Exact" campaign, puts the chosen designs in
it as product ads, adds the phrase as an EXACT keyword, negates the phrase in the
source cohort, and marks the winner promoted. The plan builder is pure and tested;
apply runs through ads_client and is operator-run.
"""

import datetime

import db
import phase4_harvest_create as p4


def build_group_plan(conn, term, source_ad_group_id, source_campaign_id, asins):
    cur = conn.cursor()
    row = cur.execute("SELECT cpc FROM harvest_log WHERE search_term=? AND source_ad_group_id=?",
                      (term, source_ad_group_id)).fetchone()
    cpc = (row[0] if row and row[0] else 0.20)
    bid = max(0.10, round(cpc * 1.15, 2))
    ptmap = {r[0]: r[1] for r in cur.execute(
        "SELECT asin, product_type FROM ad_group_product WHERE asin IS NOT NULL")}
    by_type, skipped = {}, []
    for a in asins:
        pt = ptmap.get(a)
        if not pt:
            skipped.append(a); continue
        by_type.setdefault(pt, []).append(a)
    groups = [{"product_type": pt, "campaign_name": p4.camp_name(pt), "asins": aa}
              for pt, aa in by_type.items()]
    return {"term": term, "source_ad_group_id": source_ad_group_id,
            "source_campaign_id": source_campaign_id, "bid": bid,
            "groups": groups, "skipped_asins": skipped}


def apply_group(client, conn, plan):
    today = datetime.date.today().isoformat()
    term, bid = plan["term"], plan["bid"]
    # 1) campaigns (reuse by name, else create) — one per product type in the plan
    have = p4.existing_campaigns(client)
    names = {g["campaign_name"] for g in plan["groups"]}
    to_make = [n for n in names if n not in have]
    camp_id = {n: have[n] for n in names if n in have}
    campaigns_created = 0
    if to_make:
        st, js = client.create_campaigns(
            [{"name": n, "budget": p4.CAMPAIGN_BUDGET, "startDate": today} for n in to_make])
        ids = p4.success_ids(js, "campaigns", "campaignId")
        for i, n in enumerate(to_make):
            if i in ids:
                camp_id[n] = ids[i]; campaigns_created += 1
    # 2) one ad group per group, product ads = chosen designs, keyword = the phrase
    ad_groups_created = keywords_created = 0
    for g in plan["groups"]:
        cid = camp_id.get(g["campaign_name"])
        if not cid:
            continue
        ag_name = f"{term[:70]} [{g['product_type']}]"
        st, js = client.create_ad_groups([{"name": ag_name, "campaignId": cid, "defaultBid": bid}])
        ag_ids = p4.success_ids(js, "adGroups", "adGroupId")
        agid = ag_ids.get(0)
        if not agid:
            continue
        ad_groups_created += 1
        client.create_product_ads([{"campaignId": cid, "adGroupId": agid, "asin": a}
                                    for a in g["asins"]])
        st, js = client.create_keywords([{"campaignId": cid, "adGroupId": agid,
                                          "keywordText": term, "bid": bid, "matchType": "EXACT"}])
        keywords_created += len(p4.success_ids(js, "keywords", "keywordId"))
    # 3) negate the phrase in the source cohort ad group
    client.create_negative_keywords([{"campaignId": plan["source_campaign_id"],
                                      "adGroupId": plan["source_ad_group_id"], "keywordText": term}])
    # 4) mark promoted + log
    conn.execute("UPDATE harvest_log SET promoted=1 WHERE search_term=? AND source_ad_group_id=?",
                 (term, plan["source_ad_group_id"]))
    conn.commit()
    db.log_write(conn, "harvest_promote", "keyword", term,
                 f"family exact-match created ({keywords_created} kw); negated in {plan['source_ad_group_id']}",
                 "", "submitted")
    return {"campaigns_created": campaigns_created, "ad_groups_created": ad_groups_created,
            "keywords_created": keywords_created, "negations": 1, "promoted": True}


def promote_group(term, source_ad_group_id, source_campaign_id, asins, apply=False):
    conn = db.connect()
    plan = build_group_plan(conn, term, source_ad_group_id, source_campaign_id, asins)
    if not apply:
        return {"plan": plan, "applied": False}
    from ads_client import AdsClient
    result = apply_group(AdsClient(), conn, plan)
    return {"plan": plan, "applied": True, "result": result}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.harvest_promote_group_tests -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add harvest_promote_group.py tests/harvest_promote_group_tests.py
git commit -m "Add family promote plan builder + apply for cohort winners"
```

---

### Task 4: `harvest-promote-group` action endpoint (appctl, KILL/econ-gated)

**Files:**
- Modify: `appctl.py` (add `cmd_harvest_promote_group`, parser, dispatch)
- Test: `tests/harvest_promote_group_tests.py` (add a dry-run CLI test)

**Interfaces:**
- Consumes: `harvest_promote_group.promote_group`, the existing `_guard_kill()` helper (used at the top of `cmd_negate`).
- Produces: `appctl harvest-promote-group [--apply]`, stdin `{term, source_ad_group_id, source_campaign_id, asins:[…]}` → `{"ok":true,"data":{plan, applied, result?}}`. Without `--apply` it is a pure dry run (no Amazon calls). With `--apply` it calls `_guard_kill()` then `_check_econ_gate()` (both emit the JSON error and exit when active), then executes — matching the sibling `cmd_promote`, which gates harvest promotion on both KILL and the US economics freshness gate.

- [ ] **Step 1: Write the failing test** (dry run needs no Amazon and no live writes)

```python
# append to tests/harvest_promote_group_tests.py
import json, subprocess

class Endpoint(unittest.TestCase):
    def test_dry_run_returns_plan_no_apply(self):
        env = dict(os.environ, ADS_MARKET="US")
        payload = json.dumps({"term": "zzz-nomatch", "source_ad_group_id": "x",
                              "source_campaign_id": "c", "asins": []})
        p = subprocess.run(["python3", "appctl.py", "harvest-promote-group"],
                           input=payload.encode(), capture_output=True, cwd=HERE, env=env)
        d = json.loads(p.stdout.decode())
        self.assertTrue(d["ok"])
        self.assertFalse(d["data"]["applied"])
        self.assertIn("plan", d["data"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.harvest_promote_group_tests.Endpoint -v`
Expected: FAIL — `invalid choice: 'harvest-promote-group'`.

- [ ] **Step 3: Write minimal implementation**

Add to `appctl.py` (import `harvest_promote_group` at top). `_guard_kill()` already exists in `appctl.py` (used at the top of `cmd_negate`): it calls `err(...)` — which prints the `{"ok":false,"error":…}` envelope and exits — when the `KILL` file is present. Call it only on `--apply`:

```python
def cmd_harvest_promote_group(args):
    """Promote a cohort winner to a chosen family of designs. Dry run by default;
    --apply writes to the live account (KILL-gated, operator-run)."""
    try:
        body = json.loads(sys.stdin.read() or "{}")
    except Exception as e:
        err(f"could not parse promote request: {e}")
    term = body.get("term"); src_ag = body.get("source_ad_group_id")
    src_cid = body.get("source_campaign_id"); asins = body.get("asins") or []
    if not (term and src_ag and src_cid):
        err("term, source_ad_group_id, source_campaign_id required")   # top-level ok:false
    if args.apply:
        _guard_kill()        # KILL check — prints JSON error + exits if frozen
        _check_econ_gate()   # US economics freshness gate, same as cmd_promote
    res = harvest_promote_group.promote_group(term, src_ag, src_cid, asins, apply=args.apply)
    out(res)
```

Register parser + dispatch:

```python
sp = sub.add_parser("harvest-promote-group")
sp.add_argument("--apply", action="store_true")
# dispatch:
"harvest-promote-group": cmd_harvest_promote_group,
```

Note: `harvest-promote-group` maps to a one-shot (writes/live) command — ensure it is NOT added to any read-only `serve` fast path.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.harvest_promote_group_tests.Endpoint -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add appctl.py tests/harvest_promote_group_tests.py
git commit -m "Add harvest-promote-group action endpoint (dry-run default, KILL/econ gated)"
```

---

### Task 5: Flag cohort winners on the `harvest` read

**Files:**
- Modify: `appctl.py` (`cmd_harvest`) — add `needs_design: bool` to each winner.
- Test: extend the existing `tests/` harvest coverage or add a focused assertion.

**Interfaces:**
- Produces: each winner dict gains `needs_design` — `True` when the winner is not promoted AND its `source_ad_group_id` has `asin IS NULL` in `ad_group_product` (a cohort). The app uses this to route the winner into the "Needs a design" section instead of the auto-promote pending list.

- [ ] **Step 1: Write the failing test**

```python
# tests/harvest_needs_design_tests.py
import os, sys, tempfile, unittest, json, subprocess
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE); os.environ["ADS_MARKET"] = "US"
import db  # noqa: E402

class NeedsDesign(unittest.TestCase):
    def test_cohort_winner_flagged(self):
        # Build a temp DB with one cohort winner + one single-design winner, then
        # call the in-process cmd_harvest data builder. (If cmd_harvest has a
        # testable core, call it; else assert on the JSON via a temp DB path.)
        ...
```

Note to implementer: if `cmd_harvest` has no separable core, refactor the winner-row assembly into a small `def _harvest_winners(conn)` first (pure), then have `cmd_harvest` call it — and test `_harvest_winners`. Keep the refactor in this task.

- [ ] **Step 2: Run test to verify it fails** — `needs_design` KeyError.

- [ ] **Step 3: Implement** — in the winner assembly, look up cohort status once:

```python
cohort = {r[0] for r in cur.execute(
    "SELECT ad_group_id FROM ad_group_product WHERE asin IS NULL")}
# per winner:
w["needs_design"] = (not w["promoted"]) and (str(w["source_ad_group_id"]) in cohort)
```

- [ ] **Step 4: Run tests** — PASS. Spot-check: `ADS_MARKET=US python3 appctl.py harvest` shows `needs_design:true` on the 4 stuck US terms and `false` elsewhere.

- [ ] **Step 5: Commit**

```bash
git add appctl.py tests/harvest_needs_design_tests.py
git commit -m "Flag cohort winners (needs_design) on the harvest read"
```

---

### Task 6: App models for suggestions + cohort winners

**Files:**
- Modify: `MerchAds/Models.swift`
- Modify: `MerchAds/Models.swift` `HarvestWinner` — add `let needsDesign: Bool?` (optional; old replies omit it).

**Interfaces:**
- Produces:
  - `struct SuggestedDesign: Codable, Identifiable, Hashable { let asin: String; let title: String?; let productType: String?; let matchedWords: [String]?; let score: Int; let lifetimeSales: Int?; var id: String { asin } }`
  - `struct HarvestSuggestResponse: Codable { let term: String; let count: Int; let suggestions: [SuggestedDesign] }`
  - `struct PromoteGroupResult: Codable { let applied: Bool }` (decode just what the UI needs from the envelope's `data`).
  - `HarvestWinner.needsDesign: Bool?`

- [ ] **Step 1:** Add the structs and the `needsDesign` field. (No manual initializers exist for `HarvestWinner` elsewhere — verify with `grep -rn "HarvestWinner(" MerchAds MerchAdsTests`; the bridge decodes with snake_case→camelCase.)
- [ ] **Step 2:** Build: `xcodebuild -project MerchAds.xcodeproj -scheme MerchAds -configuration Debug -derivedDataPath /tmp/merchads-derived build` → BUILD SUCCEEDED.
- [ ] **Step 3:** Commit.

```bash
git add MerchAds/Models.swift
git commit -m "App models: SuggestedDesign, HarvestSuggestResponse, needsDesign flag"
```

---

### Task 7: "Needs a design" section + confirm-designs UI (Harvest tab)

**Files:**
- Modify: `MerchAds/Views/HarvestView.swift` (add the section + a per-winner sheet)
- Create: `MerchAds/Views/PromoteGroupSheet.swift` (the confirm-designs sheet)

**Interfaces:**
- Consumes: `HarvestWinner.needsDesign`, `bridge.call(HarvestSuggestResponse.self, ["harvest-suggest", "--term", term], market:)`, `SuggestedDesign`.
- Produces: a `PromoteGroupSheet(winner:)` presenting suggested designs (pre-ticked), a search-to-add field over the full catalogue (reuse `harvest-suggest` with the user's typed text, or an `alltargets`/catalogue search), and a Promote button that fires the action in Task 8.

- [ ] **Step 1:** In `HarvestView`, split winners into `needsDesign == true` (new "Needs a design" section, top) vs the rest (existing pending list). Each cohort row shows term, orders, sales, ACOS, a ⚠️ when the term hits a small `sensitiveOrTrademark(term)` heuristic (band/brand or suicide/self-harm words — a static Swift set), and a "Choose designs…" button opening `PromoteGroupSheet`.
- [ ] **Step 2:** Build `PromoteGroupSheet`: `.task` loads `harvest-suggest` for the winner; renders a checklist of `SuggestedDesign` (title + ASIN), pre-ticked when `score >= 2` and not sensitive; a `TextField` that re-queries `harvest-suggest` with the typed phrase to add other designs; a selected-count; and a "Promote N designs" button (disabled while `appState.killActive`).
- [ ] **Step 3:** Verify build + run the app; open Harvest → the 4 stuck terms appear under "Needs a design"; the sheet shows sensible suggestions (St Michael → Saint Michael designs). Screenshot and self-critique against HIG (macos-design-guidelines skill).
- [ ] **Step 4:** Commit.

```bash
git add MerchAds/Views/HarvestView.swift MerchAds/Views/PromoteGroupSheet.swift
git commit -m "Harvest tab: Needs-a-design section + confirm-designs sheet"
```

Note: this task is UI-heavy; follow the existing `StrategyBuilderView`/`HarvestView` patterns (tables, `ContentUnavailableView`, `Theme`/`Layout` tokens). No new HIG patterns.

---

### Task 8: Promote action wiring (ActionCoordinator, operator-run live write)

**Files:**
- Modify: `MerchAds/Views/PromoteGroupSheet.swift`
- Modify: `MerchAds/ActionCoordinator.swift` + `MerchAds/Models.swift` if a new `responseKind`/receipt case is needed (follow how `promote` / `negativesApply` are wired).

**Interfaces:**
- Consumes: `appState.marketIntent(title:arguments:stdin:cardinality:responseKind:)`, `appState.actionCoordinator.execute(...)`. Arguments: `["harvest-promote-group", "--apply"]`; stdin JSON `{term, source_ad_group_id, source_campaign_id, asins:[…]}`.
- Produces: pressing Promote runs the action through the coordinator (confirm dialog, KILL guard, rehearsal mode, Audit Trail logging), then reloads the Harvest list so the promoted winner disappears.

- [ ] **Step 1:** Build the stdin from the sheet's selected ASINs + the winner's `sourceAdGroupId`/`sourceCampaignId`. Create the intent with `cardinality: .bulk`, a new `responseKind` (e.g. `.promoteGroup`) that decodes `{applied}` — mirror the existing `.promote`/`.negativesApply` wiring in `ActionCoordinator`/`Models`.
- [ ] **Step 2:** On success (not rehearsed), clear selection, toast "Promoted <term> to N designs", and `await load()` on the Harvest view. On a non-2xx / `ok:false`, surface the error (the endpoint returns `{"ok":false,...}` for KILL/econ-closed).
- [ ] **Step 3:** Verify: with KILL on, Promote is blocked with the KILL message; with KILL off, a **dry-run** first (call without `--apply` via a "Preview" affordance or by running the endpoint dry in a test) shows the correct plan. The actual live promote is operator-run — do NOT execute a live promote during development; pre-stage the exact `ADS_MARKET=US python3 appctl.py harvest-promote-group --apply` command with a sample stdin for the operator to run via `!`, or verify through the app by the operator.
- [ ] **Step 4:** `bash scripts/package_app.sh --install` then relaunch from `/Applications` (standing rule). Commit.

```bash
git add MerchAds/
git commit -m "Wire cohort-winner promote through ActionCoordinator (operator-run)"
```

---

## Self-Review

**Spec coverage:**
- Suggestion engine (whole catalogue, whole-word) → Task 1/2. ✓
- Confirm screen in Harvest tab (suggested + add/remove, ⚠️ flags) → Task 7. ✓
- Promote action (family ad group, exact keyword, negate source, mark promoted, reuse Harvested-Exact, bid ×1.15, per-type grouping) → Task 3/4/8. ✓
- Scavenger AND AUTO cohorts → Task 5 uses `asin IS NULL` (both). ✓
- Live write operator-run, dry-run default, KILL/econ gated → Task 4/8. ✓
- De-nagging (cohort winners routed to their own section) → Task 5/7. ✓
- Testing (suggester fixtures, plan builder, dry-run) → Tasks 1/3/4. ✓
- Edge cases: no-match (empty suggestions, operator adds/skips) → Task 1/7; mixed types (group by type) → Task 3; trademark/sensitive flag → Task 7. ✓

**Placeholder scan:** Task 4 references `_kill_active()` and Task 5 references `_harvest_winners` — both are explicitly flagged as "copy the exact guard from `cmd_negate`" / "extract a pure core first," which are concrete instructions, not deferred work. Task 7 UI has no literal code for every control by design (UI task, follow existing view patterns) but names every element and its data source.

**Type consistency:** `harvest_suggest.suggest` return keys (`asin,title,product_type,matched_words,score,lifetime_sales`) match `SuggestedDesign` (snake→camel). `build_group_plan` keys (`term,source_ad_group_id,source_campaign_id,bid,groups,skipped_asins`) are consumed only by `apply_group`/`promote_group` and the app decodes only `applied`. `harvest-promote-group` stdin keys match `promote_group` params. ✓
