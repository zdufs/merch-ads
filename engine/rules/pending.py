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
    try:
        with open(_path(market)) as f:
            data = json.load(f)
            data.setdefault("changes", [])
            return data
    except (FileNotFoundError, ValueError):
        return {"changes": []}


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
    tagged = []
    for ch in changes:
        c = dict(ch)
        c["rule"] = rule_name
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


def select(market, ids):
    ids = set(ids)
    return [c for c in load(market)["changes"] if c.get("id") in ids]


def clear(market):
    _save(market, {"changes": []})
