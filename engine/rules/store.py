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

import paths
import re
import sys

HERE = paths.REPO_ROOT
RULES_DIR = os.path.join(HERE, "rule_defs")
INDEX = os.path.join(RULES_DIR, "index.json")


def _backup_path():
    """Where the whole rule set is mirrored: a SIBLING of rule_defs/.

    Outside the directory, because the accident to survive is losing that
    directory. DERIVED from RULES_DIR rather than fixed at import, because the
    tests redirect RULES_DIR into a temp folder — a constant here meant they
    kept writing their two fake rules over the operator's real backup, and the
    guard would then have "restored" those. Derive it and no test can miss it.
    """
    root = os.path.abspath(RULES_DIR).rstrip(os.sep)
    return root + ".backup.json"

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


def _read_backup():
    """The backed-up rule set, or {} when there is none or it is unreadable.

    Never raises: this is read on the alerts path and on every index load, and
    a corrupt backup must not take either of them down."""
    try:
        with open(_backup_path()) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) and isinstance(data.get("index"), dict) else {}


def _write_backup(idx):
    """Copy the whole rule set — index and every rule's text — to BACKUP.

    An EMPTY rule set never overwrites a backup that still holds rules. Without
    that rule the backup would be destroyed by the same accident it exists to
    undo, on the very next save. Deliberate deletion is still honoured:
    delete_rule() prunes just that rule out of the backup."""
    if not idx:
        return
    rules = {}
    for name in idx:
        try:
            with open(_path(name)) as f:
                rules[name] = f.read()
        except OSError:
            continue
    try:
        with open(_backup_path(), "w") as f:
            json.dump({"index": idx, "rules": rules}, f, indent=2)
            f.write("\n")
    except OSError:
        pass          # a backup we cannot write is not worth failing a save over


def _restore_from_backup():
    """Put rule_defs/ back from BACKUP. Returns the restored index, or {}.

    Only ever called when index.json is ABSENT. An index that exists is the
    truth, even when it says zero rules — the operator is allowed to delete
    every rule, and putting thirteen back behind their back would be a write
    nobody asked for."""
    data = _read_backup()
    idx = data.get("index") or {}
    if not idx:
        return {}
    _ensure()
    for name, text in (data.get("rules") or {}).items():
        try:
            with open(_path(name), "w") as f:
                f.write(text)
        except OSError:
            continue
    with open(INDEX, "w") as f:
        json.dump(idx, f, indent=2)
        f.write("\n")
    auto = sum(1 for m in idx.values() if m.get("enabled") and m.get("mode") == "auto")
    # STDERR, never stdout. appctl's contract is exactly one JSON object on
    # stdout, and the app decodes it with Codable — a chatty restore line here
    # turned every read that triggered it into "the app couldn't decode it".
    print(f"!! rule_defs was missing — restored {len(idx)} rules ({auto} on auto) "
          f"from {os.path.basename(_backup_path())}", file=sys.stderr)
    return idx


def _load_index():
    try:
        with open(INDEX) as f:
            return json.load(f)
    except FileNotFoundError:
        # rule_defs/ is gone. Returning {} here is what made the nightly run
        # zero rules and report success — it is indistinguishable from a fresh
        # install. Try the backup before accepting that.
        return _restore_from_backup()
    except ValueError:
        return {}


def _save_index(idx):
    _ensure()
    with open(INDEX, "w") as f:
        json.dump(idx, f, indent=2)
        f.write("\n")
    _write_backup(idx)


def rules_lost():
    """Why an empty rule set looks LOST rather than deliberately empty, or None.

    Zero rules is normal on a fresh install and after the operator deletes the
    last one — delete_rule() prunes the backup, so neither leaves proof. It is
    NOT normal when the backup still holds rules the live index does not: that
    means index.json was emptied or replaced outside the store, and every rule
    in it stopped running without a word.

    Reported through `appctl alerts` as the `rules_lost` kind."""
    try:
        live = _load_index()
    except Exception:
        return None
    if live:
        return None
    backed = _read_backup().get("index") or {}
    if not backed:
        return None
    auto = sum(1 for m in backed.values() if m.get("enabled") and m.get("mode") == "auto")
    return {"backup_rules": len(backed), "backup_auto": auto,
            "reason": f"no rules are loaded, but {os.path.basename(_backup_path())} still holds "
                      f"{len(backed)} ({auto} of them on auto) — the nightly is "
                      f"evaluating nothing"}


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
    # The index is keyed by DISPLAY NAME and the files are keyed by slug, and the
    # slug throws away punctuation: "Pause: losers" and "Pause losers" both
    # become pause-losers. Two index entries then pointed at ONE file, so saving
    # the second silently overwrote the first rule's text — and _write_backup
    # copied the replacement under both names, destroying the backup of the
    # original at the same time. Two rules, one body, no error.
    clash = next((other for other, meta in idx.items()
                  if other != name and meta.get("slug") == _slug(name)), None)
    if clash:
        raise ValueError(
            f"the rule name {name!r} stores to the same file as {clash!r} "
            f"({_slug(name)}.rule) — punctuation and spacing are dropped when the "
            f"file name is made. Rename one of them so the two differ by a letter "
            f"or digit, not only by punctuation.")
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


def rule_meta(name):
    """The index entry for one rule, or None. Read-only, no file access beyond
    the index — the pending store calls this to ask whether a queued proposal is
    still something its rule would make."""
    return _load_index().get(name)


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
    # Unlink BEFORE saving the index: _save_index rewrites the backup by reading
    # each remaining rule's file, so a deleted rule that is still on disk would
    # be copied straight back into the backup and returned by any later restore.
    try:
        os.unlink(_path(name))
    except FileNotFoundError:
        pass
    _save_index(idx)
    # Deleting the LAST rule leaves _save_index nothing to back up (an empty set
    # never overwrites the backup), so prune this rule out of the backup by hand.
    # Otherwise a deliberate clear-out would read as a loss forever.
    if not idx:
        data = _read_backup()
        if data.get("index", {}).pop(name, None) is not None:
            data.get("rules", {}).pop(name, None)
            try:
                with open(_backup_path(), "w") as f:
                    json.dump(data, f, indent=2)
                    f.write("\n")
            except OSError:
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
