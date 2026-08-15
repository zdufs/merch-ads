#!/usr/bin/env python3
"""Rules DSL store (Spec B Layer 3). Rules live as editable text files in
rule_defs/<slug>.rule with metadata in rule_defs/index.json — so the operator and
Claude Code can edit them directly, and the app lists/saves/runs them. Each rule
carries a `kind` (merch|kdp) — the advertiser family it was authored under — so
KDP books and Merch tees keep separate rule sets. The nightly loop runs each
enabled rule only on markets of its own kind; legacy rules with no kind default
to merch.

Mirrors the seasonal.json read-modify-write convention."""

import datetime
import json
import os
import re

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_DIR = os.path.join(HERE, "rule_defs")
INDEX = os.path.join(RULES_DIR, "index.json")

# Display name: any printable text 1–64 chars (parentheses, punctuation OK — the
# library templates use them). The FILENAME is always the sanitized _slug(), so
# the permissive display name never reaches the filesystem raw.
_VALID_NAME = re.compile(r"^[^\x00-\x1f\x7f/\\]{1,64}$")


def _slug(name):
    return re.sub(r"[^A-Za-z0-9_-]+", "-", name.strip().lower()).strip("-")


def _check_name(name):
    name = name or ""
    if not _VALID_NAME.match(name) or ".." in name or not _slug(name):
        raise ValueError(f"invalid rule name {name!r} (1–64 printable chars, no / \\ or control characters)")


def _ensure():
    os.makedirs(RULES_DIR, exist_ok=True)


def _load_index():
    try:
        with open(INDEX) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def _save_index(idx):
    _ensure()
    with open(INDEX, "w") as f:
        json.dump(idx, f, indent=2)
        f.write("\n")


def _path(name):
    return os.path.join(RULES_DIR, _slug(name) + ".rule")


def save_rule(name, text, enabled=False, mode="review", season=None, kind="merch"):
    _check_name(name)
    if mode not in ("review", "auto"):
        raise ValueError(f"mode must be review|auto, got {mode!r}")
    if kind not in ("merch", "kdp"):
        raise ValueError(f"kind must be merch|kdp, got {kind!r}")
    idx = _load_index()
    # A rule name belongs to one advertiser family. Merch and KDP rules share the
    # one rule_defs directory and slug space, so re-saving a name under a different
    # kind would silently move the rule across families. Reject it — pick a
    # distinct name per family instead.
    existing = idx.get(name)
    if existing and existing.get("kind", "merch") != kind:
        raise ValueError(
            f"a {existing.get('kind', 'merch')} rule named {name!r} already exists; "
            f"use a different name for the {kind} rule")
    _ensure()
    with open(_path(name), "w") as f:
        f.write(text if text.endswith("\n") else text + "\n")
    idx[name] = {"slug": _slug(name), "enabled": bool(enabled), "mode": mode,
                 "season": season, "kind": kind,
                 "updated": datetime.datetime.now().isoformat(timespec="seconds")}
    _save_index(idx)
    return get_rule(name)


def list_rules(kind=None):
    """All rules, or only those of one advertiser family when `kind` is given.
    Legacy entries with no kind count as merch."""
    idx = _load_index()
    return [{"name": n, "enabled": m.get("enabled", False), "mode": m.get("mode", "review"),
             "season": m.get("season"), "kind": m.get("kind", "merch"),
             "updated": m.get("updated")}
            for n, m in sorted(idx.items())
            if kind is None or m.get("kind", "merch") == kind]


def get_rule(name):
    idx = _load_index()
    if name not in idx:
        return None
    m = idx[name]
    try:
        with open(_path(name)) as f:
            text = f.read()
    except FileNotFoundError:
        text = ""
    return {"name": name, "text": text, "enabled": m.get("enabled", False),
            "mode": m.get("mode", "review"), "season": m.get("season"),
            "kind": m.get("kind", "merch"), "updated": m.get("updated")}


def delete_rule(name):
    idx = _load_index()
    idx.pop(name, None)
    _save_index(idx)
    try:
        os.unlink(_path(name))
    except FileNotFoundError:
        pass


def enabled_rules(kind=None):
    return [r for r in list_rules(kind=kind) if r["enabled"]]


def in_season(season, today=None):
    """True when no season window, or today (UTC date) is inside the MM-DD window
    (year-wrap aware). Reuses seasonal_pause.in_window."""
    if not season:
        return True
    start, end = season.get("start"), season.get("end")
    if not start or not end:
        return True
    import seasonal_pause
    return seasonal_pause.in_window(today or datetime.date.today(), start, end)
