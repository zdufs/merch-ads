# MerchAds Quick Wins Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four MerchDash-inspired features to MerchAds — a per-market max-bid ceiling, per-entity debug traces on previews, cross-campaign accumulated reports, and a private watchlist — with all enforcement/computation engine-side.

**Architecture:** The Python engine (`appctl.py` JSON API + `ads_client.py` Amazon writes + per-market `ads_data*.sqlite`) is the source of truth; the SwiftUI app "Merch Ads" is editor/viewer only. The max-bid clamp lives in the three `ads_client.py` bid methods (the single funnel every bid write passes through, including nightly launchd runs). New appctl read commands route through the existing `serve` worker automatically via `PARSER`/`DISPATCH`.

**Tech Stack:** Python 3 (stdlib + `requests`), SQLite (`db.py`), `unittest` with temp-SQLite fixtures; SwiftUI (macOS 14+), Swift `Table`, Swift Charts, `UserDefaults`, `XCTest`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-01-merchads-quick-wins-design.md`. Read it before starting.
- Enforcement/computation is engine-side; Swift never calls Amazon and never writes the DBs.
- Never read or print `.env`.
- Swift opens SQLite read-only (`?mode=ro`); all mutations go through `appctl.py`.
- Perf snapshot tables (`campaign_perf`, `targeting_perf`, `search_term_perf`) are CUMULATIVE trailing-30 at the latest `date`: read the latest snapshot, `SUM` across entities within that one date, NEVER `SUM` across dates. `daily_totals` is the only table summed over dates.
- `writes_log.detail` readers that match exactly must strip via `db.detail_prefix()`; append machine data only as a new versioned ` <marker>_v1=` suffix, never hand-format around ` econ_v1=`.
- Money is per-market currency; `acos`/`cvr` are fractions (0.1816 = 18.16%).
- Branch `tamas-method-halo-candidates`; never commit to `main`. Use `git commit -F -` for messages with quotes/parens/apostrophes.
- Standing build rule: after any surviving change, commit the same turn; relaunch the app (`pkill -x "Merch Ads"; open "/Applications/Merch Ads.app"`). If Swift/plist/xcassets changed, `bash scripts/package_app.sh --install` first. The Stop hook `check_app_fresh.sh` is the backstop.
- Live-account bid-writing commands remain operator-run via `!`; the ceiling changes none of that human-gate policy.
- Engine test command: `python3 -m unittest tests.<module> -v` (run from the Ads folder). App tests: `xcodebuild -project MerchAds.xcodeproj -scheme MerchAds -configuration Debug -derivedDataPath /tmp/merchads-derived test`.

---

# Feature 1 — Max-bid ceiling

## Task 1: Ceiling read/write helpers in `db.py`

**Files:**
- Modify: `db.py` (add after `meta_set`, `db.py:171`)
- Test: `tests/maxbid_tests.py` (create)

**Interfaces:**
- Consumes: `db.meta_get(conn, key)`, `db.meta_set(conn, key, value)` (`db.py:162,167`).
- Produces:
  - `db.get_bid_ceiling(conn, surface) -> float | None` where `surface in {"target","keyword"}`.
  - `db.set_bid_ceiling(conn, surface, value)` — `value` a float or `None` to clear.
  - Meta keys: `max_bid_target`, `max_bid_keyword`.

- [ ] **Step 1: Write the failing test**

```python
# tests/maxbid_tests.py
#!/usr/bin/env python3
"""Unit tests for the per-market max-bid ceiling (Spec A feature 1)."""
import os, sys, tempfile, unittest
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["ADS_MARKET"] = "US"
import db  # noqa: E402


def temp_conn():
    fd, path = tempfile.mkstemp(suffix=".sqlite"); os.close(fd)
    real = db.DB_PATH; db.DB_PATH = path
    conn = db.connect(); db.DB_PATH = real
    return conn, path


class Ceiling(unittest.TestCase):
    def test_roundtrip_and_default_none(self):
        conn, path = temp_conn()
        try:
            self.assertIsNone(db.get_bid_ceiling(conn, "target"))
            db.set_bid_ceiling(conn, "target", 1.20)
            self.assertEqual(db.get_bid_ceiling(conn, "target"), 1.20)
            self.assertIsNone(db.get_bid_ceiling(conn, "keyword"))
            db.set_bid_ceiling(conn, "keyword", 0.90)
            self.assertEqual(db.get_bid_ceiling(conn, "keyword"), 0.90)
        finally:
            conn.close(); os.unlink(path)

    def test_clear(self):
        conn, path = temp_conn()
        try:
            db.set_bid_ceiling(conn, "target", 1.0)
            db.set_bid_ceiling(conn, "target", None)
            self.assertIsNone(db.get_bid_ceiling(conn, "target"))
        finally:
            conn.close(); os.unlink(path)

    def test_bad_surface_raises(self):
        conn, path = temp_conn()
        try:
            with self.assertRaises(ValueError):
                db.get_bid_ceiling(conn, "campaign")
        finally:
            conn.close(); os.unlink(path)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.maxbid_tests -v`
Expected: FAIL — `AttributeError: module 'db' has no attribute 'get_bid_ceiling'`.

- [ ] **Step 3: Write minimal implementation** (add to `db.py` after line 171)

```python
_BID_CEILING_KEYS = {"target": "max_bid_target", "keyword": "max_bid_keyword"}


def get_bid_ceiling(conn, surface):
    """Per-market bid ceiling for a write surface ('target' or 'keyword').
    Returns a float dollar amount or None when unset."""
    key = _BID_CEILING_KEYS.get(surface)
    if key is None:
        raise ValueError(f"unknown bid-ceiling surface {surface!r}")
    v = meta_get(conn, key)
    return float(v) if v not in (None, "") else None


def set_bid_ceiling(conn, surface, value):
    """Set (float) or clear (None) a per-market bid ceiling."""
    key = _BID_CEILING_KEYS.get(surface)
    if key is None:
        raise ValueError(f"unknown bid-ceiling surface {surface!r}")
    if value is None:
        conn.execute("DELETE FROM engine_meta WHERE key=?", (key,)); conn.commit()
    else:
        meta_set(conn, key, f"{float(value):.2f}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.maxbid_tests -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add db.py tests/maxbid_tests.py
git commit -F - <<'EOF'
Max-bid ceiling: per-market get/set_bid_ceiling helpers (engine_meta)
EOF
```

## Task 2: Clamp in the three `ads_client.py` bid methods

**Files:**
- Modify: `ads_client.py` (`__init__` L40-54; `update_target_bids` L258-267; `create_keywords` L304-313; `update_keyword_bids` L322-330)
- Test: `tests/maxbid_tests.py` (extend)

**Interfaces:**
- Consumes: `db.get_bid_ceiling` (Task 1).
- Produces: on `AdsClient`, `self.last_clamps: list[dict]` populated per write call, each `{"id": <targetId|keywordId|keywordText>, "requested": float, "cap": float}`; a private `self._ceiling(surface) -> float | None` (lazy-loaded, cached). Clamped bid value = `min(requested, cap)`.

- [ ] **Step 1: Write the failing test** (append to `tests/maxbid_tests.py`)

```python
class Clamp(unittest.TestCase):
    def _client_with(self, target_cap=None, keyword_cap=None):
        # Build an AdsClient without touching .env / Amazon: bypass __init__.
        import ads_client
        c = ads_client.AdsClient.__new__(ads_client.AdsClient)
        c._ceilings = {"target": target_cap, "keyword": keyword_cap}
        c.last_clamps = []
        return c

    def test_clamp_math_caps_above_and_passes_below(self):
        c = self._client_with(target_cap=1.20)
        self.assertEqual(c._apply_ceiling("target", "T1", 3.00), 1.20)
        self.assertEqual(c._apply_ceiling("target", "T2", 0.80), 0.80)
        self.assertEqual(len(c.last_clamps), 1)
        self.assertEqual(c.last_clamps[0], {"id": "T1", "requested": 3.00, "cap": 1.20})

    def test_no_ceiling_no_clamp(self):
        c = self._client_with(target_cap=None)
        self.assertEqual(c._apply_ceiling("target", "T1", 9.99), 9.99)
        self.assertEqual(c.last_clamps, [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.maxbid_tests.Clamp -v`
Expected: FAIL — `AttributeError: 'AdsClient' object has no attribute '_apply_ceiling'`.

- [ ] **Step 3: Write minimal implementation**

In `AdsClient.__init__` add after `self._token_expiry = 0` (`ads_client.py:54`):

```python
        self._ceilings = {}          # lazy per-surface cache
        self.last_clamps = []        # populated per bid-write call
```

Add these methods to `AdsClient` (e.g. right before `update_target_bids`, `ads_client.py:258`):

```python
    def _ceiling(self, surface):
        """Per-market bid ceiling for 'target'/'keyword', cached. Read once from
        this market's DB (read-only). Fail-open only if the DB is absent."""
        if surface not in self._ceilings:
            import db
            try:
                conn = db.connect(ro=True)
                try:
                    self._ceilings[surface] = db.get_bid_ceiling(conn, surface)
                finally:
                    conn.close()
            except Exception:
                self._ceilings[surface] = None
        return self._ceilings[surface]

    def _apply_ceiling(self, surface, entity_id, bid):
        """Return the bid to actually write, clamped to the surface ceiling.
        Records any clamp in self.last_clamps for the caller to log."""
        cap = self._ceiling(surface)
        b = round(float(bid), 2)
        if cap is not None and b > cap:
            self.last_clamps.append({"id": str(entity_id), "requested": b, "cap": float(cap)})
            return round(float(cap), 2)
        return b
```

Then route the three bid methods through it. `update_target_bids` (`ads_client.py:262-264`) becomes:

```python
        self.last_clamps = []
        for batch in self._chunks(items, 100):
            payload = {"targetingClauses": [
                {"targetId": str(it["targetId"]),
                 "bid": self._apply_ceiling("target", it["targetId"], it["bid"])} for it in batch]}
```

`update_keyword_bids` (`ads_client.py:326-327`) becomes:

```python
        self.last_clamps = []
        for batch in self._chunks(items, 100):
            payload = {"keywords": [
                {"keywordId": str(it["keywordId"]),
                 "bid": self._apply_ceiling("keyword", it["keywordId"], it["bid"])} for it in batch]}
```

`create_keywords` (`ads_client.py:307-311`) — clamp the embedded bid (identify by keywordText):

```python
        self.last_clamps = []
        payload = {"keywords": [{
            "campaignId": str(it["campaignId"]), "adGroupId": str(it["adGroupId"]),
            "keywordText": it["keywordText"], "matchType": it.get("matchType", "EXACT"),
            "bid": self._apply_ceiling("keyword", it["keywordText"], it["bid"]),
            "state": "ENABLED",
        } for it in items]}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.maxbid_tests -v`
Expected: PASS (all classes).

- [ ] **Step 5: Commit**

```bash
git add ads_client.py tests/maxbid_tests.py
git commit -F - <<'EOF'
Max-bid ceiling: clamp every bid write at the ads_client funnel

Covers update_target_bids / update_keyword_bids / create_keywords — the single
path all 11 callers (setbid, resetbids, phase3, lottery, tamas, harvest) route
through, so nightly runs are capped too. Clamps recorded in last_clamps.
EOF
```

## Task 3: `maxbid` appctl command + `setbid` clamp-audit

**Files:**
- Modify: `appctl.py` — add `cmd_maxbid` (near `cmd_seasons`, ~L2015), register in `build_parser` (~L2010) and `DISPATCH` (~L2233); update `cmd_setbid` (L1379-1394) to record clamp in `writes_log.detail`.
- Test: `tests/maxbid_tests.py` (extend with a detail-suffix test)

**Interfaces:**
- Consumes: `db.get_bid_ceiling`/`set_bid_ceiling` (Task 1), `client.last_clamps` (Task 2), `db.detail_prefix` (`db.py:224`), `out`/`err`/`_guard_kill` (`appctl.py:68,74,1091`).
- Produces: `maxbid` command returning `{market, target, keyword}` (dollar strings or null); a ` cap_v1={...}` detail suffix + ` [adjusted]` prefix marker convention for clamped bid writes.

- [ ] **Step 1: Write the failing test** (append to `tests/maxbid_tests.py`)

```python
class DetailSuffix(unittest.TestCase):
    def test_prefix_strips_cap_suffix(self):
        import db
        human = "snap=live 2.00->1.20 (manual) [adjusted]"
        full = human + ' cap_v1={"req":2.0,"cap":1.2}'
        self.assertEqual(db.detail_prefix(full), human)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.maxbid_tests.DetailSuffix -v`
Expected: FAIL — `detail_prefix` splits on ` econ_v1=` only, so it returns `full` unchanged.

- [ ] **Step 3: Write minimal implementation**

Extend `db.detail_prefix` (`db.py:224`) to strip any ` <name>_v1=` machine suffix, not just econ. Replace the marker split with a regex that cuts at the first ` \w+_v1=`:

```python
import re as _re
_SUFFIX_RE = _re.compile(r" \w+_v1=")

def detail_prefix(detail):
    """Human-readable prefix of a writes_log.detail, stripping any trailing
    machine suffix(es) of the form ' <name>_v1={...}' (econ_v1, cap_v1, ...)."""
    if not detail:
        return detail
    m = _SUFFIX_RE.search(detail)
    return detail[:m.start()] if m else detail
```

Add `cmd_maxbid` in `appctl.py`:

```python
def cmd_maxbid(args):
    """Per-market bid ceiling. --get (default) reads; --set writes; --clear unsets.
    Local config only (no Amazon call)."""
    _guard_kill()
    conn = db.connect()
    if args.set:
        if args.target is not None:
            db.set_bid_ceiling(conn, "target", float(args.target) if args.target != "" else None)
        if args.keyword is not None:
            db.set_bid_ceiling(conn, "keyword", float(args.keyword) if args.keyword != "" else None)
    elif args.clear:
        db.set_bid_ceiling(conn, "target", None)
        db.set_bid_ceiling(conn, "keyword", None)
    t = db.get_bid_ceiling(conn, "target")
    k = db.get_bid_ceiling(conn, "keyword")
    out({"market": markets.current(),
         "target": f"{t:.2f}" if t is not None else None,
         "keyword": f"{k:.2f}" if k is not None else None})
```

Register in `build_parser` (near `appctl.py:2010`):

```python
    p = sub.add_parser("maxbid")
    p.add_argument("--set", action="store_true")
    p.add_argument("--clear", action="store_true")
    p.add_argument("--target")
    p.add_argument("--keyword")
```

Add to `DISPATCH` (`appctl.py:2233`): `"maxbid": cmd_maxbid,`.

In `cmd_setbid` (`appctl.py:1389-1392`), after the `update_target_bids` call, fold any clamp into the log detail:

```python
    res = client.update_target_bids([{"targetId": args.target, "bid": bid}])
    clamp = client.last_clamps[0] if client.last_clamps else None
    written = clamp["cap"] if clamp else bid
    reason = "manual" + (" [adjusted]" if clamp else "")
    detail = f"snap=live {args.prev or '?'}->{written} ({reason})"
    if clamp:
        detail += f' cap_v1={{"req":{clamp["requested"]},"cap":{clamp["cap"]}}}'
    db.log_write(conn, "bid_change", "target", args.target, detail, args.prev, str(res))
    out({"market": markets.current(), "target_id": args.target, "prev_bid": args.prev,
         "new_bid": written, "adjusted": clamp is not None, "applied": True, "http": res})
```

(Adjust variable names to match the existing `cmd_setbid` body; keep its `_guard_kill`/floor checks.)

- [ ] **Step 4: Run tests + smoke the command**

Run: `python3 -m unittest tests.maxbid_tests -v` → PASS.
Run: `ADS_MARKET=US python3 appctl.py maxbid` → `{"ok":true,"data":{"market":"US","target":null,"keyword":null}}`.
Run: `ADS_MARKET=US python3 appctl.py maxbid --set --target 1.20` then `maxbid` → target `"1.20"`. Then `maxbid --clear` → both null.

- [ ] **Step 5: Commit**

```bash
git add appctl.py db.py tests/maxbid_tests.py
git commit -F - <<'EOF'
Max-bid ceiling: appctl maxbid get/set/clear + setbid clamp audit

detail_prefix now strips any _v1= machine suffix (econ_v1, cap_v1). A clamped
setbid logs cap_v1={req,cap} and marks the human reason [adjusted].
EOF
```

## Task 4: Settings UI for the ceiling

**Files:**
- Modify: `MerchAds/Models.swift` (add `MaxBidResponse`); `MerchAds/Views/SettingsView.swift` (new section after Actions, ~L56); `MerchAds/PythonBridge.swift:76-82` (add `"maxbid"` to `fastCommands`).
- Test: none new (UI); existing app tests must stay green.

**Interfaces:**
- Consumes: `bridge.call(MaxBidResponse.self, ["maxbid"], market:)`, and `["maxbid","--set","--target",v,"--keyword",v]`.
- Produces: `struct MaxBidResponse: Codable { let market: String; let target: String?; let keyword: String? }`.

- [ ] **Step 1: Add the model** — in `Models.swift` near the other response structs:

```swift
struct MaxBidResponse: Codable {
    let market: String
    let target: String?
    let keyword: String?
}
```

- [ ] **Step 2: Add `"maxbid"` to `fastCommands`** (`PythonBridge.swift:81`), e.g. append after `"demandfeed",`. The `--set`/`--clear` forms carry mutating flags and are correctly excluded from rehearsal by the existing `mutatingFlags` guard (`PythonBridge.swift:93`).

- [ ] **Step 3: Add the Settings section** — in `SettingsView.swift`, insert before the App section (`SettingsView.swift:58`):

```swift
            settingsSection(title: "Max bid ceiling",
                            subtitle: "Caps every bid written for \(appState.selectedMarket) — including nightly automation. Clamped writes show as “adjusted” in Audit.") {
                HStack(spacing: 16) {
                    LabeledContent("Target bid") {
                        TextField("none", text: $targetCeiling).frame(width: 90)
                    }
                    LabeledContent("Keyword bid") {
                        TextField("none", text: $keywordCeiling).frame(width: 90)
                    }
                    Button("Save") { Task { await saveCeiling() } }
                }
            }
            .task(id: appState.selectedMarket) { await loadCeiling() }
```

Add `@State private var targetCeiling = ""`, `@State private var keywordCeiling = ""`, and (matching the file's `AppState` access pattern) `@EnvironmentObject var appState: AppState` if not already present. Add the two async helpers:

```swift
    private func loadCeiling() async {
        guard let bridge = try? appState.makeBridge() else { return }
        if let r = try? await bridge.call(MaxBidResponse.self, ["maxbid"], market: appState.selectedMarket) {
            targetCeiling = r.target ?? ""
            keywordCeiling = r.keyword ?? ""
        }
    }
    private func saveCeiling() async {
        guard let bridge = try? appState.makeBridge() else { return }
        var args = ["maxbid", "--set"]
        args += ["--target", targetCeiling]     // "" clears that surface
        args += ["--keyword", keywordCeiling]
        _ = try? await bridge.call(MaxBidResponse.self, args, market: appState.selectedMarket)
        await loadCeiling()
    }
```

(Verify `appState` is reachable in `SettingsView` the same way the other sections read `engineRoot`; if the view uses `@AppStorage` only, add the `@EnvironmentObject`.)

- [ ] **Step 4: Build, install, verify**

Run: `bash scripts/package_app.sh --install`
Then: `pkill -x "Merch Ads"; open "/Applications/Merch Ads.app"`
Verify: Settings shows the new section; setting Target to 1.20, Save, reopening Settings shows 1.20; `ADS_MARKET=US python3 appctl.py maxbid` confirms. Existing app tests: `xcodebuild ... test` green.

- [ ] **Step 5: Commit**

```bash
git add MerchAds/Models.swift MerchAds/Views/SettingsView.swift MerchAds/PythonBridge.swift
git commit -F - <<'EOF'
Max-bid ceiling: Settings section (per selected market) via appctl maxbid
EOF
```

---

# Feature 2 — Debug traces

## Task 5: `trace` field builder + `killlist` traces

**Files:**
- Modify: `appctl.py` — add a small `_trace_row` helper; extend `cmd_killlist` (L517-551).
- Test: `tests/trace_tests.py` (create)

**Interfaces:**
- Produces: helper `_cond(name, actual, threshold, passed) -> dict` returning `{"condition": name, "actual": actual, "threshold": threshold, "pass": bool(passed)}`. Each `killlist` design row gains `"trace": [ _cond("cvr < floor", cvr, FLOOR_CVR, cvr < FLOOR_CVR), _cond("acos > break_even", acos, be, acos > be) ]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/trace_tests.py
#!/usr/bin/env python3
"""Debug-trace fields on preview endpoints (Spec A feature 2)."""
import os, sys, unittest
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["ADS_MARKET"] = "US"
import appctl  # noqa: E402


class Cond(unittest.TestCase):
    def test_cond_shape_and_pass(self):
        c = appctl._cond("cvr < floor", 0.06, 0.08, 0.06 < 0.08)
        self.assertEqual(c["condition"], "cvr < floor")
        self.assertEqual(c["actual"], 0.06)
        self.assertEqual(c["threshold"], 0.08)
        self.assertTrue(c["pass"])

    def test_cond_null_actual(self):
        c = appctl._cond("acos > be", None, 0.41, False)
        self.assertIsNone(c["actual"])
        self.assertFalse(c["pass"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.trace_tests -v`
Expected: FAIL — `module 'appctl' has no attribute '_cond'`.

- [ ] **Step 3: Write minimal implementation**

Add near the top helpers of `appctl.py` (by `_acos`/`_cvr`, ~L102):

```python
def _cond(name, actual, threshold, passed):
    """One debug-trace row: what a single condition evaluated to."""
    return {"condition": name, "actual": actual, "threshold": threshold, "pass": bool(passed)}
```

In `cmd_killlist`, where each included design row dict is built (`appctl.py:545`), add:

```python
            "trace": [
                _cond("cvr < floor", cvr, FLOOR_CVR, cvr is not None and cvr < FLOOR_CVR),
                _cond("acos > break_even", acos, be, acos is not None and acos > be),
            ],
```

- [ ] **Step 4: Run tests + smoke**

Run: `python3 -m unittest tests.trace_tests -v` → PASS.
Run: `ADS_MARKET=US python3 appctl.py killlist` → each design carries a two-item `trace` whose `pass` flags are both true (included rows satisfy both conditions).

- [ ] **Step 5: Commit**

```bash
git add appctl.py tests/trace_tests.py
git commit -F - <<'EOF'
Debug traces: _cond helper + killlist per-design condition trace
EOF
```

## Task 6: Traces on `negatives-preview` and `resetbids`

**Files:**
- Modify: `appctl.py` — `cmd_negatives_preview` (L1466-1476), `cmd_resetbids` preview branch (L1430-1441); optionally surface `phase2_apply._design_target` econ suffix.
- Test: `tests/trace_tests.py` (extend with a resetbids-plan trace assertion using a fixture, or assert shape on a hand-built plan)

**Interfaces:**
- Produces: each `negatives-preview` negative row gains `"trace"` (clicks vs `MIN_CLICKS_NEG`, orders vs 0) or (acos vs ceiling); each pause row gains (acos vs target, cvr vs 0.08); each `resetbids` item gains `"trace": [ _cond("current > original", current, original, current > original) ]`.

- [ ] **Step 1: Write the failing test** — extend `tests/trace_tests.py`:

```python
class ResetTrace(unittest.TestCase):
    def test_reset_item_trace(self):
        item = {"targetId": "T", "original": 0.50, "current": 0.90, "new": 0.45}
        traced = appctl._reset_trace(item)
        self.assertEqual(traced["trace"][0]["condition"], "current > original")
        self.assertEqual(traced["trace"][0]["actual"], 0.90)
        self.assertEqual(traced["trace"][0]["threshold"], 0.50)
        self.assertTrue(traced["trace"][0]["pass"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.trace_tests.ResetTrace -v`
Expected: FAIL — no `_reset_trace`.

- [ ] **Step 3: Write minimal implementation**

Add helper + wire into `cmd_resetbids` preview (map over the plan items before `out`):

```python
def _reset_trace(item):
    return {**item, "trace": [
        _cond("current > original", item["current"], item["original"],
              item["current"] > item["original"])]}
```

In `cmd_resetbids` preview branch (`appctl.py:1438-1441`) wrap items: `items = [_reset_trace(i) for i in plan]` before returning. In `cmd_negatives_preview` (`appctl.py:1472-1476`), attach a `trace` per negative/pause row using the same `_cond` helper and the thresholds from `phase2_apply` (`MIN_CLICKS_NEG`, the design ceiling); reuse the values already unpacked from `candidates`. Keep the existing `reason` strings unchanged (additive).

- [ ] **Step 4: Run tests + smoke**

Run: `python3 -m unittest tests.trace_tests -v` → PASS.
Run: `ADS_MARKET=US python3 appctl.py resetbids` and `... negatives-preview` → rows carry `trace`; pre-existing fields (`reason`, `preview`, counts) unchanged.

- [ ] **Step 5: Commit**

```bash
git add appctl.py tests/trace_tests.py
git commit -F - <<'EOF'
Debug traces: negatives-preview + resetbids per-row condition traces
EOF
```

## Task 7: Render traces in the app (inspector + opt-in column)

**Files:**
- Modify: `MerchAds/Models.swift` (add `ConditionTrace`; add optional `trace` to `KillDesign`, `ProposedNegative`, `ProposedPause`, resetbids item model); `MerchAds/Views/CampaignBrowserInspectors.swift` (extend `KillRowInspectorView`, L166-180); `MerchAds/Views/ApprovalsView.swift` (opt-in Trace column).
- Test: `MerchAdsTests` decode test for `trace`.

**Interfaces:**
- Consumes: engine `trace` arrays (Tasks 5-6).
- Produces: `struct ConditionTrace: Codable, Identifiable { var id: String { condition }; let condition: String; let actual: Double?; let threshold: Double?; let pass: Bool }`. `actual`/`threshold` are `Double?` (engine sends fractions/dollars; some are strings for skip-reason rows — if a string form is needed, add a second optional `String` field rather than overloading).

- [ ] **Step 1: Write the failing decode test** — `MerchAdsTests/TraceDecodeTests.swift`:

```swift
import XCTest
@testable import MerchAds

final class TraceDecodeTests: XCTestCase {
    func testDecodeConditionTrace() throws {
        let json = """
        {"condition":"cvr < floor","actual":0.06,"threshold":0.08,"pass":true}
        """.data(using: .utf8)!
        let d = JSONDecoder(); d.keyDecodingStrategy = .convertFromSnakeCase
        let c = try d.decode(ConditionTrace.self, from: json)
        XCTAssertEqual(c.condition, "cvr < floor")
        XCTAssertEqual(c.actual, 0.06)
        XCTAssertTrue(c.pass)
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `xcodebuild -project MerchAds.xcodeproj -scheme MerchAds -configuration Debug -derivedDataPath /tmp/merchads-derived test`
Expected: FAIL to compile — `ConditionTrace` undefined.

- [ ] **Step 3: Implement**

Add `ConditionTrace` to `Models.swift`; add `let trace: [ConditionTrace]?` to `KillDesign` (`Models.swift:367`), `ProposedNegative` (L870), `ProposedPause` (L878), and the resetbids item struct. In `KillRowInspectorView` (`CampaignBrowserInspectors.swift:180`, after the metrics block) add:

```swift
            if let trace = design.trace, !trace.isEmpty {
                Divider()
                Text("Debug trace").font(.caption).foregroundStyle(.secondary)
                ForEach(trace) { c in
                    LabeledContent(c.condition) {
                        Text(traceValue(c))
                            .foregroundStyle(c.pass ? .primary : .secondary)
                    }
                }
            }
```

with a small `traceValue(_:)` formatter (`"<actual> vs <threshold>"`, using the app's existing percent/money formatters where the condition name implies units). In `ApprovalsView`, add a `TableColumn("Trace")` with a `.customizationID("trace")` (hidden by default) whose cell shows the first failing/deciding condition; full trace via row inspect.

- [ ] **Step 4: Run tests, build, install, verify**

Run app tests → green (incl. new decode test). `bash scripts/package_app.sh --install`; relaunch. Verify Kill-list inspector shows the trace; Approvals Trace column toggles on.

- [ ] **Step 5: Commit**

```bash
git add MerchAds/Models.swift MerchAds/Views/CampaignBrowserInspectors.swift MerchAds/Views/ApprovalsView.swift MerchAdsTests/TraceDecodeTests.swift
git commit -F - <<'EOF'
Debug traces: ConditionTrace model + kill-list inspector + Approvals trace column
EOF
```

---

# Feature 3 — Accumulated reports

## Task 8: `accumulated-asins` endpoint

**Files:**
- Modify: `appctl.py` — add `cmd_accumulated_asins`, register in `build_parser` + `DISPATCH`; add `"accumulated-asins"` to Swift `fastCommands` later (Task 10).
- Test: `tests/accumulated_tests.py` (create)

**Interfaces:**
- Produces: `accumulated-asins [--limit N=500] [--expand ASIN] [--csv]` →
  `{market, as_of, count, rows:[{asin, product_type, campaigns, ad_groups, impressions, clicks, spend, orders, sales, acos, cvr}]}`; with `--expand`, `{market, asin, breakdown:[{campaign_id, campaign, ad_group_id, ad_group, impressions, clicks, spend, orders, sales, acos, cvr}]}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/accumulated_tests.py
#!/usr/bin/env python3
"""Cross-campaign accumulated rollups (Spec A feature 3)."""
import os, sys, tempfile, unittest
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["ADS_MARKET"] = "US"
import db, appctl  # noqa: E402


def seed(conn):
    # ASIN B0AAA in two campaigns/ad groups on the latest date; a NULL-asin cohort too.
    d = "2026-07-31"
    conn.executescript("""
        INSERT INTO campaigns(campaign_id,name,state) VALUES ('c1','C1','ENABLED'),('c2','C2','ENABLED');
        INSERT INTO ad_groups(ad_group_id,campaign_id,name,state) VALUES ('g1','c1','G1','ENABLED'),('g2','c2','G2','ENABLED');
        INSERT INTO ad_group_product(ad_group_id,asin,product_type) VALUES ('g1','B0AAA','standard_tee'),('g2','B0AAA','standard_tee');
    """)
    for (cid, gid) in (("c1","g1"), ("c2","g2")):
        conn.execute("""INSERT INTO targeting_perf(date,campaign_id,ad_group_id,targeting,match_type,
            target_id,impressions,clicks,cost,orders,sales,acos) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (d, cid, gid, "auto", "EXACT", "t"+gid, 100, 10, 5.0, 1, 20.0, 0.25))
    conn.commit()


def temp_conn():
    fd, path = tempfile.mkstemp(suffix=".sqlite"); os.close(fd)
    real = db.DB_PATH; db.DB_PATH = path
    conn = db.connect(); db.DB_PATH = real
    return conn, path


class AccumAsins(unittest.TestCase):
    def test_sums_across_two_campaigns(self):
        conn, path = temp_conn()
        try:
            seed(conn)
            data = appctl._accumulated_asins(conn, limit=500)
            row = next(r for r in data["rows"] if r["asin"] == "B0AAA")
            self.assertEqual(row["campaigns"], 2)
            self.assertEqual(row["ad_groups"], 2)
            self.assertEqual(row["clicks"], 20)
            self.assertEqual(row["spend"], 10.0)
            self.assertEqual(row["orders"], 2)
            self.assertAlmostEqual(row["sales"], 40.0)
        finally:
            conn.close(); os.unlink(path)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.accumulated_tests -v`
Expected: FAIL — no `_accumulated_asins`.

- [ ] **Step 3: Implement** — add a pure function `_accumulated_asins(conn, limit, expand=None)` that queries the **latest** `targeting_perf` date (via `_latest_two_dates`, `appctl.py:110`), joins `ad_group_product` for asin/type, `GROUP BY asin` (skip/bucket NULL asin), computes `acos`/`cvr` via `_acos`/`_cvr`, counts distinct campaigns/ad_groups, sorts by spend desc, truncates to `limit`. Then `cmd_accumulated_asins(args)` wraps it with `out(...)`. Register parser (`--limit`, `--expand`, `--csv`) + `DISPATCH`. `--csv` writes `outputs/accumulated_asins{_MKT}.csv` via `csv.DictWriter` (market suffix `"" if mkt==DEFAULT else f"_{mkt}"`).

Follow the cumulative-not-summed rule: `WHERE date = <latest>` only.

- [ ] **Step 4: Run test + smoke**

Run: `python3 -m unittest tests.accumulated_tests -v` → PASS.
Run: `ADS_MARKET=US python3 appctl.py accumulated-asins --limit 5` → valid JSON, rows sorted by spend.

- [ ] **Step 5: Commit**

```bash
git add appctl.py tests/accumulated_tests.py
git commit -F - <<'EOF'
Accumulated reports: accumulated-asins cross-campaign rollup endpoint
EOF
```

## Task 9: `accumulated-keywords` endpoint

**Files:**
- Modify: `appctl.py` — add `cmd_accumulated_keywords` + `_accumulated_keywords`, register.
- Test: `tests/accumulated_tests.py` (extend)

**Interfaces:**
- Produces: `accumulated-keywords [--limit N=500] [--expand TERM] [--csv]` →
  `{market, as_of, count, rows:[{targeting, match_type, campaigns, ad_groups, impressions, clicks, spend, orders, sales, acos, cvr}]}`. Groups `targeting_perf` by `(targeting, match_type)` at latest snapshot across all campaigns (keywords/targets you bid on — not search terms).

- [ ] **Step 1: Write the failing test** — extend `tests/accumulated_tests.py`:

```python
class AccumKeywords(unittest.TestCase):
    def test_groups_by_targeting(self):
        conn, path = temp_conn()
        try:
            seed(conn)  # both rows are targeting "auto"/EXACT
            data = appctl._accumulated_keywords(conn, limit=500)
            row = next(r for r in data["rows"] if r["targeting"] == "auto")
            self.assertEqual(row["match_type"], "EXACT")
            self.assertEqual(row["campaigns"], 2)
            self.assertEqual(row["clicks"], 20)
        finally:
            conn.close(); os.unlink(path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.accumulated_tests.AccumKeywords -v`
Expected: FAIL — no `_accumulated_keywords`.

- [ ] **Step 3: Implement** `_accumulated_keywords(conn, limit, expand=None)` mirroring Task 8 but `GROUP BY targeting, match_type` over the latest `targeting_perf` snapshot; `cmd_accumulated_keywords` + parser + DISPATCH + optional `--csv` (`outputs/accumulated_keywords{_MKT}.csv`).

- [ ] **Step 4: Run tests + smoke**

Run: `python3 -m unittest tests.accumulated_tests -v` → PASS. Smoke `accumulated-keywords`.

- [ ] **Step 5: Commit**

```bash
git add appctl.py tests/accumulated_tests.py
git commit -F - <<'EOF'
Accumulated reports: accumulated-keywords cross-campaign rollup endpoint
EOF
```

## Task 10: Two accumulated-report screens

**Files:**
- Modify: `MerchAds/Models.swift` (`AccumulatedAsinsResponse`, `AccumulatedKeywordsResponse`, row + breakdown structs); `MerchAds/Views/ContentView.swift` (two `Screen` cases + sidebar rows in Insights + `detailView` arms); `MerchAds/TablePrefs.swift` (`TableID.accumulatedAsins/.accumulatedKeywords`); `MerchAds/PythonBridge.swift:76-82` (`"accumulated-asins","accumulated-keywords"` in `fastCommands`); Create: `MerchAds/Views/AccumulatedAsinsView.swift`, `MerchAds/Views/AccumulatedKeywordsView.swift`.
- Test: `MerchAdsTests` decode test for one response.

**Interfaces:**
- Consumes: `accumulated-asins`/`accumulated-keywords` (Tasks 8-9).
- Produces: two SwiftUI screens using the `Table` + `ColumnPrefs`/`SortPrefs`/`SavedViewPicker` stack (template: `CampaignListView.swift`), row disclosure calling `--expand`, selection feeding existing bulk pause/negate via `ActionCoordinator`.

- [ ] **Step 1: Add models + a decode test** in `MerchAdsTests` for `AccumulatedAsinsResponse` (mirror `TraceDecodeTests` shape). Run → fails to compile.
- [ ] **Step 2: Implement models + both views + routing + `TableID`s + `fastCommands`.** Reuse the per-view persistence wiring pattern (`CampaignListView.swift:21-24,76-77`). Add `sidebarRow(.accumulatedAsins)`/`.accumulatedKeywords` to the Insights `Section` (`ContentView.swift` ~L131) and `case` arms in `detailView`.
- [ ] **Step 3: Wire bulk actions** — reuse the existing selection→`ActionCoordinator` path (pause ASIN everywhere / negate keyword everywhere) already used elsewhere; no new action kind needed if the existing pause/negate intents accept an ASIN/term.
- [ ] **Step 4: Build, install, verify** both screens render real rollups; disclosure loads per-campaign breakdown; app tests green.
- [ ] **Step 5: Commit**

```bash
git add MerchAds/Models.swift MerchAds/Views/ContentView.swift MerchAds/Views/AccumulatedAsinsView.swift MerchAds/Views/AccumulatedKeywordsView.swift MerchAds/TablePrefs.swift MerchAds/PythonBridge.swift MerchAdsTests
git commit -F - <<'EOF'
Accumulated reports: Accumulated ASINs + Keywords screens (Insights)
EOF
```

---

# Feature 4 — Watchlist

## Task 11: `watchlist` engine endpoint

**Files:**
- Modify: `appctl.py` — add `cmd_watchlist` (reads pins from stdin) + `_watchlist_rows` helper, register in `build_parser` + `DISPATCH`.
- Test: `tests/watchlist_tests.py` (create)

**Interfaces:**
- Produces: `watchlist` reads stdin `{"pins":[{kind, campaign_id?, ad_group_id?, target_id?, asin?}]}` → `{market, as_of, rows:[{kind, id, label, impressions, clicks, spend, orders, sales, acos, cvr, resolved}], summary:{impressions, clicks, spend, orders, sales, acos, cvr}}`. Unresolvable pins → `resolved:false` with zeroed metrics (never crash).

- [ ] **Step 1: Write the failing test** (seed via the `tests/accumulated_tests.py` `seed`/`temp_conn` idiom):

```python
# tests/watchlist_tests.py
import os, sys, tempfile, unittest
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["ADS_MARKET"] = "US"
import db, appctl  # noqa: E402
# reuse seed() from accumulated_tests
from tests.accumulated_tests import seed, temp_conn  # noqa: E402


class Watchlist(unittest.TestCase):
    def test_summary_sums_resolved_pins(self):
        conn, path = temp_conn()
        try:
            seed(conn)
            pins = [{"kind": "campaign", "campaign_id": "c1"},
                    {"kind": "campaign", "campaign_id": "c2"}]
            data = appctl._watchlist_rows(conn, pins)
            self.assertEqual(data["summary"]["clicks"], 20)
            self.assertEqual(data["summary"]["orders"], 2)
            self.assertTrue(all(r["resolved"] for r in data["rows"]))

    def test_unresolvable_pin_reported(self):
        conn, path = temp_conn()
        try:
            seed(conn)
            data = appctl._watchlist_rows(conn, [{"kind": "campaign", "campaign_id": "ZZZ"}])
            self.assertFalse(data["rows"][0]["resolved"])
        finally:
            conn.close(); os.unlink(path)
```

(Fix the missing `finally` in the first method when implementing.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.watchlist_tests -v`
Expected: FAIL — no `_watchlist_rows`.

- [ ] **Step 3: Implement** `_watchlist_rows(conn, pins)`: for each pin, resolve metrics from the latest snapshot by kind — campaign→`campaign_perf`; ad group→`targeting_perf` filtered by `ad_group_id`; target→`targeting_perf` by `target_id`; asin→`targeting_perf` joined via `ad_group_product`. Sum into a `summary`. `cmd_watchlist(args)` reads `sys.stdin` JSON, calls the helper, `out(...)`. Register parser (`watchlist`, no args) + DISPATCH. Reads stdin → NOT a fast/serve command (serve is line-based); the app calls it one-shot.

- [ ] **Step 4: Run tests + smoke**

Run: `python3 -m unittest tests.watchlist_tests -v` → PASS.
Run: `echo '{"pins":[]}' | ADS_MARKET=US python3 appctl.py watchlist` → valid JSON, empty rows + zeroed summary.

- [ ] **Step 5: Commit**

```bash
git add appctl.py tests/watchlist_tests.py
git commit -F - <<'EOF'
Watchlist: engine endpoint resolving pinned entities into aggregated rows
EOF
```

## Task 12: Per-market watchlist store (app)

**Files:**
- Create: `MerchAds/WatchlistStore.swift`.
- Modify: `MerchAds/TablePrefs.swift` (add `TableID.watchlist`).
- Test: `MerchAdsTests/WatchlistStoreTests.swift` (create).

**Interfaces:**
- Produces: `struct WatchlistPin: Codable, Identifiable, Hashable { let id: String; let kind: PinKind; let market: String; let campaignID: String?; let adGroupID: String?; let targetID: String?; let asin: String?; let label: String }` (`id` = stable composite); `enum PinKind: String, Codable { case campaign, adGroup, target, asin }`; `enum WatchlistStore { static func pins(market:) -> [WatchlistPin]; static func add(_:market:); static func remove(_:market:); static let capacity = 1000 }`, persisted under `"watchlist.v1.<market>"` in `UserDefaults`.

- [ ] **Step 1: Write the failing test**

```swift
import XCTest
@testable import MerchAds

final class WatchlistStoreTests: XCTestCase {
    override func setUp() {
        UserDefaults.standard.removeObject(forKey: "watchlist.v1.US")
        UserDefaults.standard.removeObject(forKey: "watchlist.v1.DE")
    }
    func testPerMarketIsolationAndDedup() {
        let p = WatchlistPin(id: "campaign:c1", kind: .campaign, market: "US",
                             campaignID: "c1", adGroupID: nil, targetID: nil, asin: nil, label: "C1")
        WatchlistStore.add(p, market: "US")
        WatchlistStore.add(p, market: "US")           // dedup by id
        XCTAssertEqual(WatchlistStore.pins(market: "US").count, 1)
        XCTAssertEqual(WatchlistStore.pins(market: "DE").count, 0)
        WatchlistStore.remove(p, market: "US")
        XCTAssertEqual(WatchlistStore.pins(market: "US").count, 0)
    }
}
```

- [ ] **Step 2: Run to verify it fails** (compile error — types undefined).
- [ ] **Step 3: Implement** `WatchlistStore.swift` (JSON-encode `[WatchlistPin]` to the per-market key; `add` dedups by `id` and enforces `capacity`; `remove` filters) + `TableID.watchlist`.
- [ ] **Step 4: Run app tests** → green.
- [ ] **Step 5: Commit**

```bash
git add MerchAds/WatchlistStore.swift MerchAds/TablePrefs.swift MerchAdsTests/WatchlistStoreTests.swift
git commit -F - <<'EOF'
Watchlist: per-market UserDefaults pin store + tests
EOF
```

## Task 13: Watchlist screen + pin affordances

**Files:**
- Create: `MerchAds/Views/WatchlistView.swift`.
- Modify: `MerchAds/Models.swift` (`WatchlistResponse`, row/summary structs); `MerchAds/Views/ContentView.swift` (`Screen.watchlist` in Manage + `detailView` arm); the entity table views (Campaigns, Ad groups, Targets, ASINs, Accumulated) to add a "Pin to watchlist" row context menu.
- Test: existing app tests stay green.

**Interfaces:**
- Consumes: `WatchlistStore` (Task 12), `watchlist` endpoint (Task 11) via `bridge.call(WatchlistResponse.self, ["watchlist"], market:, stdin: <pins JSON>)`.
- Produces: `WatchlistView` — Table of resolved rows + Swift Charts aggregate trend line + `StatCard` summary; unpin control; empty state.

- [ ] **Step 1: Add `WatchlistResponse` model + `Screen.watchlist`** (title/icon/blurb arms + Manage sidebar row + `detailView` case). Build → confirms wiring compiles.
- [ ] **Step 2: Implement `WatchlistView`**: load `WatchlistStore.pins(market:)`, encode to stdin JSON, `bridge.call(...)`, render Table (reuse `ColumnPrefs`/`SortPrefs` with `TableID.watchlist`) + summary + trend chart; unpin calls `WatchlistStore.remove`.
- [ ] **Step 3: Add "Pin to watchlist" context menu** to the entity tables' rows, constructing a `WatchlistPin` from the row + `appState.selectedMarket` and calling `WatchlistStore.add`.
- [ ] **Step 4: Build, install, verify** — pin a campaign, open Watchlist, see the aggregated row + summary; switch market → pins are market-scoped; unpin works. App tests green.
- [ ] **Step 5: Commit**

```bash
git add MerchAds/Views/WatchlistView.swift MerchAds/Models.swift MerchAds/Views/ContentView.swift MerchAds/Views
git commit -F - <<'EOF'
Watchlist: per-market pinboard screen + pin-to-watchlist context menus
EOF
```

---

## Final verification (whole Spec A)

- [ ] `python3 -m unittest tests.maxbid_tests tests.trace_tests tests.accumulated_tests tests.watchlist_tests -v` all green.
- [ ] `xcodebuild ... test` — all app tests green (24 existing + new).
- [ ] Smoke each new/changed appctl command returns valid JSON (`maxbid`, `killlist`, `negatives-preview`, `resetbids`, `accumulated-asins`, `accumulated-keywords`, `watchlist`).
- [ ] `/Applications/Merch Ads.app` is the fresh Release build; app relaunched; all four features usable in the running app.
- [ ] A clamped `setbid` shows `adjusted` + a `writes_log` row in the Audit trail.
- [ ] Update `memory/merchads-app-progress.md` with the four shipped features.

## Self-review notes
- Spec coverage: max-bid (Tasks 1-4), debug traces (5-7), accumulated (8-10), watchlist (11-13) — all four spec features covered, engine-before-UI ordering preserved.
- The clamp guarantee depends on `ads_client` being the sole bid funnel; Task 2's implementer must re-verify no caller hand-builds a `targetingClauses`/`keywords` bid payload around the three methods (the exploration confirmed all 11 paths use them).
- Swift UI tasks (7, 10, 13) intentionally give real models/wiring + exact insertion points rather than every line of verbose view code; the interfaces block fixes all cross-task type names.
