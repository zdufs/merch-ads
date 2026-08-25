#!/usr/bin/env python3
"""Pending rule-change store (Spec B Layer 4 follow-up). Review-mode rules queue
their proposed changes here instead of applying; the app's Approval queue reads
them, the operator approves a subset, and rules-approve executes + clears those.

Per-market JSON at outputs/rule_pending{_M}.json. Mirrors the outputs/ convention."""

import hashlib
import json
import os

import paths

HERE = paths.REPO_ROOT
OUTDIR = os.path.join(HERE, "outputs")


def _path(market):
    import markets
    sfx = "" if market == markets.DEFAULT else f"_{market}"
    return os.path.join(OUTDIR, f"rule_pending{sfx}.json")


def _change_id(rule, ch):
    # args are part of the identity: approving an id must approve the VALUE the
    # operator saw, not whatever a later re-collect wrote for the same entity.
    args = json.dumps(ch.get("args", []), sort_keys=True, default=str)
    key = (f"{rule}|{ch.get('entity_kind')}|{ch.get('entity_id')}|"
           f"{ch.get('action')}|{args}")
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def load(market):
    path = _path(market)
    try:
        with open(path) as f:
            data = json.load(f)
            data.setdefault("changes", [])
            return data
    except (FileNotFoundError, ValueError):
        return {"changes": []}
    except OSError as e:
        # Any OTHER problem with the file is a fault, and it must not read as an
        # empty queue — "nothing to approve" is exactly what a broken store
        # looks like from the Approval Queue. A string SystemExit reaches the
        # operator as one clean sentence through appctl's dispatcher; the raw
        # OSError reached them as `[Errno 20] Not a directory` and an absolute
        # path, which the standing rule forbids in the envelope.
        raise SystemExit(
            f"The pending rules queue at outputs/{os.path.basename(path)} "
            f"could not be read ({e.strerror}). Fix that path, then reload "
            f"the Approval Queue.")


def _save(market, data):
    os.makedirs(OUTDIR, exist_ok=True)
    with open(_path(market), "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def set_rule(market, rule_name, changes):
    """Replace all pending entries for one rule with its latest proposals (each
    tagged with rule + a stable id). Re-collecting a rule refreshes, never
    duplicates."""
    data = load(market)
    kept = [c for c in data["changes"] if c.get("rule") != rule_name]
    # Stamp the version of the rule these proposals came from. A queued change
    # outlives an edit: the operator queues setBid(0.50), then edits the rule to
    # 0.20 or disables it, and until the next collect the old entry is still
    # sitting there — approving it writes 0.50, from a rule that no longer says
    # so. The id already covers the ARGS, so a re-collect makes a new entry; what
    # it could not cover is a rule that changed and was not re-collected.
    stamp = _rule_stamp(rule_name)
    tagged = []
    for ch in changes:
        c = dict(ch)
        c["rule"] = rule_name
        c["rule_version"] = stamp
        c["id"] = _change_id(rule_name, ch)
        tagged.append(c)
    data["changes"] = kept + tagged
    _save(market, data)
    return data


def remove_rule(market, rule_name):
    """Drop every proposal from one rule — used when that rule is deleted.

    Without this, deleting a rule left its proposals in the queue forever and
    the operator could approve a write from a rule that no longer exists."""
    data = load(market)
    data["changes"] = [c for c in data["changes"] if c.get("rule") != rule_name]
    _save(market, data)
    return data


def keep_only(market, rule_names):
    """Drop proposals from rules that are no longer enabled, in-season review
    rules. Returns how many rows were pruned.

    `set_rule` only replaces the entries for the rule it is writing, so a rule
    that stopped being collected — deleted, disabled, switched to auto, out of
    season — used to leave its last proposals behind as a queue that could never
    refresh itself."""
    names = set(rule_names)
    data = load(market)
    before = len(data["changes"])
    data["changes"] = [c for c in data["changes"] if c.get("rule") in names]
    _save(market, data)
    return before - len(data["changes"])


def remove(market, ids):
    ids = set(ids)
    data = load(market)
    data["changes"] = [c for c in data["changes"] if c.get("id") not in ids]
    _save(market, data)
    return data


def _rule_stamp(rule_name):
    """What the rule looked like when these proposals were made.

    `updated` moves on every save and the two flags decide whether the rule
    should be acting at all, so together they answer "is this proposal still
    something the rule would say".
    """
    try:
        from rules import store
        meta = store.rule_meta(rule_name)
    except Exception:
        return None
    if not meta:
        return None
    return {"updated": meta.get("updated"), "enabled": bool(meta.get("enabled")),
            "mode": meta.get("mode")}


def select(market, ids):
    """(fresh, stale) — approved entries, split on whether their rule still
    stands behind them. A stale entry is one whose rule was edited, disabled or
    deleted after the proposal was queued; applying it writes something no rule
    currently proposes."""
    ids = set(ids)
    chosen = [c for c in load(market)["changes"] if c.get("id") in ids]
    fresh, stale = [], []
    for c in chosen:
        was = c.get("rule_version")
        now = _rule_stamp(c.get("rule"))
        if was is None:
            fresh.append(c)          # queued before versions existed: unchanged behaviour
        elif now is None:
            stale.append({**c, "stale_reason": "the rule has been deleted"})
        elif not now.get("enabled"):
            stale.append({**c, "stale_reason": "the rule has been disabled"})
        elif now.get("updated") != was.get("updated"):
            stale.append({**c, "stale_reason": "the rule was edited after this was queued"})
        else:
            fresh.append(c)
    return fresh, stale


def clear(market):
    _save(market, {"changes": []})
