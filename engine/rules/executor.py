#!/usr/bin/env python3
"""Rules DSL executor (Spec B Layer 2). Applies proposed changes from
runner.preview to Amazon, routing every write through ads_client (so the Spec A
max-bid clamp applies), logging each to writes_log, and honoring the safety
rails: KILL freeze (hard block), snapshot freshness (per entity source table,
fail closed like phase2/phase3), the US econ-freshness gate (economics-driven
changes only), and the per-run change cap.

Every Amazon response is checked: a 4xx/5xx batch is reported as status
"failed", logged as result=failed, and never counted as applied — the operator
must never read a rejected write as a change that happened.

Live writes are operator-run (the auto-mode classifier blocks agent-initiated
production writes); the nightly job / operator invokes this via `rules-run
--apply`. Unit-tested with a fake client — no live Amazon here."""

import ads_client
import db
import killswitch
import markets
import products


# verb -> (surface for the clamp / logging entity_type)
_STATE = {"pause": "PAUSED", "enable": "ENABLED"}

# entity kind -> the perf table its rows were evaluated from. The freshness
# gate reads the SAME table the rule read (standing rule: never date one perf
# table from another — they are filled by independent report jobs).
_SOURCE_TABLE = {
    "target": "targeting_perf", "keyword": "targeting_perf",
    "adgroup": "targeting_perf", "product": "targeting_perf",
    "asin": "targeting_perf",
    "searchterm": "search_term_perf",
    "campaign": "campaign_perf",
}

# A rolling-window change was measured over the true per-day tables, so its
# gate has to check those rather than the trailing-30 snapshots. The question
# is different too: not "is the newest snapshot fresh" but "are all the days in
# this window actually there". A week that holds six days makes every entity
# look about 14% cheaper and 14% worse-selling than it was.
#
# Kinds with no per-day table are deliberately ABSENT rather than defaulted.
# Search terms and products cannot produce a rolling change (entities.load
# refuses to load them that way), but if one ever arrives here it gets blocked,
# not gated against target_daily. Judging one perf table's numbers by another
# table's state is the exact mistake this engine has been burned by twice.
_ROLLING_SOURCE = {
    "target": "target_daily", "keyword": "target_daily",
    "adgroup": "target_daily", "campaign": "campaign_daily",
}


def execute(conn, changes, market=None, client=None, cap=None, today=None):
    market = market or markets.current()
    if killswitch.active():
        return {"applied": False, "blocked": "kill", "count": 0, "results": [],
                "message": "KILL freeze is active — release it in Actions to apply."}

    # VOLUME gate. Every other guard below asks whether ONE change is safe; this
    # one asks whether there are an absurd NUMBER of them, which is the shape a
    # mistyped condition takes. cap=None reads the market's setting; an explicit
    # cap wins, and `rules-approve` passes 0 because the operator picked those
    # ids by hand. See db.get_auto_change_cap for where 500 comes from.
    if cap is None:
        cap = db.get_auto_change_cap(conn)
    try:
        volume = _write_volume(conn, changes) if cap else len(changes)
    except _VolumeResolutionError as e:
        return {"applied": False, "blocked": "change_volume_unresolved",
                "count": len(changes), "cap": cap, "results": [],
                "message": str(e)}
    if cap and volume > cap:
        # Refuse the WHOLE run. This used to apply changes[:cap] and set
        # truncated:True — half an account acted on, no refusal, and a flag
        # that reached no screen. A partial apply leaves the account in a state
        # no rule described and no operator chose.
        return {
            "applied": False, "blocked": "change_volume",
            "count": volume, "changes": len(changes), "cap": cap, "results": [],
            "message": (
                f"{volume} writes proposed in {market}, over the "
                f"{cap}-change limit for one automatic run — so NOTHING was "
                f"applied. A normal night is tens of changes, so this is "
                f"either a rule matching far more than it was meant to, or a "
                f"real backlog that deserves a human. Read them with "
                f"rules-preview, then either fix the rule, or run it in REVIEW "
                f"mode and approve from the queue, or raise the limit with "
                f"`appctl change-cap --set N`."),
        }

    gate = products.econ_gate(conn=conn)
    gate_ok = gate.get("ok", False)

    if client is None:
        from ads_client import AdsClient
        client = AdsClient(market)

    snap_gates = {}

    def snap_gate(ch):
        kind = ch["entity_kind"]
        if ch.get("window") == "ROLLING":
            days = int(ch.get("window_days") or 0)
            table = _ROLLING_SOURCE.get(kind)
            if table is None:
                return {"ok": False,
                        "reason": (f"{kind} has no per-day table, so a rolling "
                                   f"window over it cannot be checked for holes")}
            cache_key = (table, days)
            if cache_key not in snap_gates:
                snap_gates[cache_key] = db.daily_window_gate(conn, table, days,
                                                            today=today)
            return snap_gates[cache_key]
        table = _SOURCE_TABLE.get(kind, "targeting_perf")
        if table not in snap_gates:
            snap_gates[table] = db.snapshot_gate(conn, table, today=today)
        gate = snap_gates[table]
        if not gate.get("ok"):
            return gate
        # A CURRENT rule can still READ a rolling window inline
        # (`keyword.spend IN LAST 7 DAYS`), and a hole in that window is exactly
        # as dangerous as a hole in an outer one — six days summed and acted on
        # as seven. The gate only ever looked at the outer window, so these went
        # through untested.
        for spec in (ch.get("inline_windows") or []):
            rtable = _ROLLING_SOURCE.get(kind)
            if rtable is None:
                return {"ok": False,
                        "reason": (f"{kind} has no per-day table, so the inline "
                                   f"window in this rule cannot be checked for holes")}
            try:
                start, end = db.window_dates(tuple(spec), today=today)
            except Exception as exc:
                return {"ok": False,
                        "reason": f"inline window {tuple(spec)} is unusable: {exc}"}
            key = (rtable, "inline", start, end)
            if key not in snap_gates:
                snap_gates[key] = db.daily_range_gate(conn, rtable, start, end)
            g = snap_gates[key]
            if not g.get("ok"):
                return g
        return gate

    # No slice here any more. Past the cap the run was refused above, so by
    # this point every proposed change is going to be judged on its own merits.
    results = []
    applied = 0
    for ch in changes:
        snap = snap_gate(ch)
        if not snap["ok"]:
            results.append({**_slim(ch), "status": "blocked_stale_data",
                            "reason": snap["reason"]})
            continue
        if ch.get("econ_driven") and not gate_ok:
            results.append({**_slim(ch), "status": "blocked_econ_gate",
                            "reasons": gate.get("reasons", [])})
            continue
        try:
            res = _apply_one(conn, client, ch)
        except _UnsupportedAction as e:
            results.append({**_slim(ch), "status": "unsupported", "message": str(e)})
            continue
        except Exception as e:                      # pragma: no cover - defensive
            results.append({**_slim(ch), "status": "error", "message": str(e)})
            continue
        if res.get("noop"):
            results.append({**_slim(ch), "status": "skipped_noop"})
        elif res.get("ok"):
            applied += 1
            results.append({**_slim(ch), "status": "applied",
                            "adjusted": res.get("adjusted", False),
                            **({"fanout": res["fanout"]} if res.get("fanout") else {})})
        else:
            results.append({**_slim(ch), "status": "failed", "http": res.get("http")})

    return {"applied": True, "market": market, "count": applied,
            "cap": cap, "econ_gate_ok": gate_ok, "results": results}


def _slim(ch):
    return {"entity_kind": ch["entity_kind"], "entity_id": ch["entity_id"],
            "label": ch["label"], "action": ch["action"], "args": ch.get("args", []),
            "note": ch.get("note")}


class _UnsupportedAction(Exception):
    pass


def _reason(ch):
    return ch.get("note") or f"rule:{ch['action']}"


def _ok(batches):
    """Same success test as appctl._http_ok: 2xx/207 AND zero item-level
    failures (ads_client parses the v3 success/error arrays — a 207 whose
    items all errored must never read as success)."""
    return ads_client.items_ok(batches)


def _codes(batches):
    return [b.get("http") for b in (batches or [])]


_EVERYWHERE_ACTION = {"pauseeverywhere": "pause", "setbideverywhere": "setbid",
                      "negateeverywhere": "negate"}
_ACCUM_RESOLVE_KIND = {"accumulated_asin": "asin", "accumulated_keyword": "keyword"}


class _VolumeResolutionError(RuntimeError):
    pass


def _write_volume(conn, changes):
    """How many WRITES these changes actually are.

    The cap used to count rows in `changes`, and an accumulated "everywhere"
    verb is ONE row that fans out at apply time to every instance of an ASIN or
    keyword in the account. So a single negateEverywhere resolving to 800 ad
    groups counted as 1, sailed past a 500 limit, and wrote all 800 — which is
    precisely the runaway the cap exists to stop.

    Resolving the plan here is a read-only database query. A resolution failure
    refuses the run because no safe write count is available.
    """
    total = 0
    for ch in changes:
        if str(ch.get("action", "")).lower() not in _EVERYWHERE_ACTION:
            total += 1
            continue
        try:
            import appctl
            kind = _ACCUM_RESOLVE_KIND.get(ch["entity_kind"])
            args = ch.get("args") or []
            match = str(args[0]).lower() if str(ch["action"]).lower().startswith("negate") and args else "exact"
            plan = appctl._everywhere_plan(
                conn, kind, _EVERYWHERE_ACTION[str(ch["action"]).lower()],
                [ch["entity_id"]], match)
            total += appctl._everywhere_applicable(plan)
        except Exception as e:
            raise _VolumeResolutionError(
                f"could not resolve {ch.get('action')} write volume: {e}") from e
    return total


def _apply_everywhere(conn, client, ch):
    """Fan an accumulated everywhere verb out to every instance, reusing appctl's
    resolver and shared apply loop (lazy import to avoid an import cycle). One
    change becomes many writes, each logged and undoable."""
    import appctl
    action = _EVERYWHERE_ACTION[ch["action"].lower()]
    kind = _ACCUM_RESOLVE_KIND.get(ch["entity_kind"])
    if kind is None:
        raise _UnsupportedAction(f"{ch['action']} needs an accumulated entity")
    args = ch.get("args") or []
    setbid_value = round(float(args[0]), 2) if action == "setbid" and args else None
    match = str(args[0]).lower() if action == "negate" and args else "exact"
    plan = appctl._everywhere_plan(conn, kind, action, [ch["entity_id"]], match)
    applied, skipped, failed, _ = appctl._everywhere_apply_ops(
        conn, client, plan["ops"], setbid_value)
    return {"ok": failed == 0, "http": [], "noop": applied == 0 and failed == 0,
            "fanout": {"applied": applied, "skipped": skipped, "failed": failed}}


def _is_keyword(ch):
    """A keyword (match EXACT/PHRASE/BROAD) vs a product/auto target. They are
    different Amazon entities with different bid and state endpoints; the change
    carries match_type so the executor can route to the right one."""
    mt = (ch.get("ref", {}).get("match_type") or "").upper()
    return mt in ("EXACT", "PHRASE", "BROAD")


def _apply_one(conn, client, ch):
    verb = ch["action"]
    ref = ch.get("ref", {})
    kind = ch["entity_kind"]
    reason = _reason(ch)

    if verb.lower() in _EVERYWHERE_ACTION:
        return _apply_everywhere(conn, client, ch)

    if verb in _STATE:
        state = _STATE[verb]
        cur = ch.get("prev_state")
        if cur is not None and str(cur).upper() == state:
            # Already in the target state. State can move between preview and
            # apply (approval queue, a second rule), so this guards the write
            # even though runner._is_noop already dropped it at preview time.
            return {"ok": True, "noop": True, "http": []}
        # Log the REAL previous state so Undo restores it, not a guess.
        prev = str(cur).upper() if cur is not None else ("ENABLED" if verb == "pause" else "PAUSED")
        if kind in ("target", "keyword"):
            tid = ref.get("target_id") or ch["entity_id"]
            # A keyword and a product/auto target are different Amazon entities on
            # different endpoints; a keyword id sent to /sp/targets just bounces.
            if _is_keyword(ch):
                res = client.set_keywords_state([tid], state)
                act, etype = ("pause_keyword" if verb == "pause" else "enable_keyword"), "keyword"
            else:
                res = client.set_targets_state([tid], state)
                act, etype = ("pause_target" if verb == "pause" else "enable_target"), "target"
            ok = _ok(res)
            if ok:
                # Mirror it, exactly as the two branches below do. Without this
                # the `targets` table still said ENABLED after a successful
                # pause, so the next preview proposed the same pause again and
                # logged ENABLED as the state Undo should restore.
                db.set_local_target_state(conn, [tid], state)
            _log(conn, act, etype, tid, reason, prev, ok)
        elif kind == "adgroup":
            agid = ref.get("ad_group_id") or ch["entity_id"]
            res = client.set_ad_groups_state([agid], state)
            ok = _ok(res)
            if ok:
                db.set_local_ad_group_state(conn, [agid], state)
            _log(conn, "pause_ad_group" if verb == "pause" else "enable_ad_group",
                 "adGroup", agid, reason, prev, ok)
        elif kind == "campaign":
            cid = ref.get("campaign_id") or ch["entity_id"]
            res = client.set_campaigns_state([cid], state)
            ok = _ok(res)
            if ok:
                db.set_local_campaign_state(conn, [cid], state)
            _log(conn, "pause_campaign" if verb == "pause" else "enable_campaign",
                 "campaign", cid, reason, prev, ok)
        else:
            raise _UnsupportedAction(f"{verb} not supported on {kind}")
        return {"ok": ok, "http": _codes(res)}

    if verb == "setBid":
        bid = round(float(ch["args"][0]), 2)
        prev_bid = ch.get("prev_bid")
        try:
            prev_bid = round(float(prev_bid), 2) if prev_bid is not None else None
        except (TypeError, ValueError):
            prev_bid = None
        if prev_bid is not None and prev_bid == bid:
            return {"ok": True, "noop": True, "http": []}
        tid = ref.get("target_id") or ch["entity_id"]
        # Keyword bids go through /sp/keywords, product-target bids through
        # /sp/targets — routing to the wrong one silently fails every write.
        if _is_keyword(ch):
            res = client.update_keyword_bids([{"keywordId": tid, "bid": bid}])
        else:
            res = client.update_target_bids([{"targetId": tid, "bid": bid}])
        ok = _ok(res)
        clamp = client.last_clamps[0] if getattr(client, "last_clamps", None) else None
        written = clamp["cap"] if clamp else bid
        # Carry the OLD bid in the detail so Undo (restore_bid) can parse it —
        # a rule bid change used to log "?->new" and could not be reverted.
        old_txt = f"{prev_bid}" if prev_bid is not None else "?"
        detail = f"snap=rule {old_txt}->{written} ({reason}{' [adjusted]' if clamp else ''})"
        if clamp:
            detail += f' cap_v1={{"req":{clamp["requested"]},"cap":{clamp["cap"]}}}'
        _log(conn, "bid_change", "target", tid, detail, None, ok)
        if ok:
            db.set_local_target_bids(conn, [(tid, written)])
        return {"ok": ok, "http": _codes(res), "adjusted": clamp is not None}

    if verb == "setBudget":
        budget = round(float(ch["args"][0]), 2)
        cid = ref.get("campaign_id") or ch["entity_id"]
        res = client.update_campaign_budgets([{"campaignId": cid, "budget": budget}])
        ok = _ok(res)
        # The client clamps the budget to the market ceiling. The audit row used
        # to record what the RULE asked for, so a rule requesting 400 under a 50
        # ceiling wrote 50 to Amazon and 400 to writes_log — and the local mirror
        # kept the old figure, so all three disagreed. setBid already did this
        # correctly; the budget branch was written later and never caught up.
        clamp = client.last_clamps[0] if getattr(client, "last_clamps", None) else None
        written = clamp["cap"] if clamp else budget
        detail = (f"snap=rule ->{written} ({reason}"
                  + (" [adjusted]" if clamp else "") + ")")
        if clamp:
            detail += f' cap_v1={{"req":{clamp["requested"]},"cap":{clamp["cap"]}}}'
        _log(conn, "budget_change", "campaign", cid, detail, None, ok)
        if ok:
            db.set_local_campaign_budget(conn, [(cid, written)])
        return {"ok": ok, "http": _codes(res), "adjusted": clamp is not None}

    if verb == "addNegative":
        text = str(ch["args"][0])
        match = (str(ch["args"][1]).upper() if len(ch["args"]) > 1 else "EXACT")
        cid = ref.get("campaign_id")
        agid = ref.get("ad_group_id")
        res = client.create_negative_keywords([{"campaignId": cid, "adGroupId": agid,
                                                "keywordText": text,
                                                "matchType": "NEGATIVE_" + match}])
        ok = _ok(res)
        # Record the created id so Undo can delete it — a negative that turns out
        # to block a winner is one revert from gone.
        negid = (res[0].get("created_ids") or [None])[0] if res else None
        detail = text + f" ({reason})" + (f" negid={negid}" if negid else "")
        _log(conn, "add_negative", "searchTerm", agid, detail, "none", ok)
        return {"ok": ok, "http": _codes(res)}

    raise _UnsupportedAction(f"action {verb!r} is not executable in this version "
                             f"(createKeyword / setBiddingStrategy land in a later layer)")


def _log(conn, action, entity_type, entity_id, detail, prev_state, ok):
    db.log_write(conn, action, entity_type, str(entity_id), detail, prev_state,
                 "submitted" if ok else "failed")
