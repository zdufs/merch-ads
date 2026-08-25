#!/usr/bin/env python3
"""Seasonal design scheduler — pause tagged designs out of their season, re-enable in season.

A "season" is a recurring yearly ACTIVE window [resume..pause] (MM-DD) defined in seasonal.json.
Designs are tagged by ASIN. Out of the window, a tagged design's ENABLED ad groups get paused;
back in the window, the ad groups WE seasonal-paused get re-enabled.

SAFETY:
- preview by default; writes only with --apply (+ typed APPLY, or --auto). KILL aware.
- re-enable touches ONLY ad groups whose latest seasonal action was our own pause, so a design
  paused for bad performance (phase2 / kill list) is never resurrected by the calendar.
- market-aware via ADS_MARKET (default US); run one invocation per market.

  ADS_MARKET=US python3 seasonal_pause.py                 # preview for US
  ADS_MARKET=US python3 seasonal_pause.py --apply --auto  # execute (nightly)
"""
import datetime as dt
import json
import os

import paths
import sys

import db
import killswitch
import markets
import ads_client
from ads_client import AdsClient

HERE = paths.REPO_ROOT
CONFIG = os.path.join(HERE, "seasonal.json")
CONFIG_EXAMPLE = os.path.join(HERE, "seasonal.example.json")
# Written beside the config on every save that HAS tags. It is what makes the
# seed-from-example path below safe, and it is untracked from birth, so a
# `git rm` on the config cannot take it too.
CONFIG_BACKUP = os.path.join(HERE, "seasonal.backup.json")


def EMPTY_CONFIG():
    """The shape every reader expects, with nothing in it.

    Written when a fresh install has no config, no backup and no example. It is
    a function, not a constant, so one caller mutating the result can never
    reach the next one.
    """
    return {"seasons": {}, "asins": {}}


def tag_count(cfg):
    """How many designs this config tags."""
    return len((cfg or {}).get("asins") or {})


def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write(path, cfg):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")


def _seed(path, cfg):
    """Write the starting config, and carry on if the folder will not take it.

    Seeding is a convenience: the caller already has the config in hand. So a
    data folder that is read-only, or one where `seasonal.json` is a directory,
    must not turn a READ into a failure. It used to: the OSError travelled out
    of the bridge and the Seasonal screen showed `[Errno 13] Permission denied`
    plus an absolute path where it should have said nothing is tagged yet."""
    try:
        _write(path, cfg)
        return True
    except OSError as e:
        # STDERR — appctl promises exactly one JSON object on stdout.
        print(f"!! could not write {os.path.basename(path)} ({e.strerror}); "
              f"reading the seasonal config from memory this run", file=sys.stderr)
        return False


def load_config():
    """Read the seasonal config.

    seasonal.json is operator data (which ASIN belongs to which season), so it is
    gitignored and absent on a fresh clone. A missing file is filled in rather
    than crashing — but the order matters, and it cost six dead days to learn it.

    On 2026-08-15 the public-release commit deleted the working copy with a plain
    `git rm`. This function then seeded the shipped example over the gap, and the
    example carries the season windows and keywords but NO ASINs. The scheduler
    became a no-op and said nothing until an audit on 2026-08-21.

    So the backup is tried FIRST. Only a machine with neither file is a genuinely
    fresh install, and only that machine gets the empty example.

    A config that EXISTS is never touched, even when it is empty. Untagging every
    design is something an operator may do on purpose, and re-tagging 15,674
    designs behind their back would be a write nobody asked for. That case is
    reported by tags_lost() instead.

    A config that cannot be READ — an unreadable file, a directory wearing the
    name, or malformed JSON — reports the empty shape and says so on stderr.
    An operator can fix a file they are told about; they cannot fix a screen
    that will not open.
    """
    if not os.path.exists(CONFIG):
        cfg = None
        if os.path.exists(CONFIG_BACKUP):
            try:
                cfg = _read(CONFIG_BACKUP)
            except (OSError, ValueError) as e:
                print(f"!! {os.path.basename(CONFIG_BACKUP)} could not be read "
                      f"({e}) — starting from an empty seasonal config",
                      file=sys.stderr)
        if cfg is not None:
            _seed(CONFIG, cfg)
            # STDERR, never stdout — appctl promises exactly one JSON object on
            # stdout and the app decodes it with Codable. cmd_seasons and the
            # season-tag commands all read this config.
            print(f"!! seasonal.json was missing — restored {tag_count(cfg)} design "
                  f"tags from {os.path.basename(CONFIG_BACKUP)}", file=sys.stderr)
            return cfg
        if os.path.exists(CONFIG_EXAMPLE):
            try:
                cfg = _read(CONFIG_EXAMPLE)
            except (OSError, ValueError):
                cfg = EMPTY_CONFIG()
        else:
            # Genuinely fresh: no config, no backup, and no example either.
            # The example is a REPO file, so a standalone install of the app
            # points at a data folder that has never held one. Reading a file
            # that is not there raised FileNotFoundError all the way out of the
            # bridge, and the Seasonal screen showed a filesystem path where it
            # should have said "nothing is tagged yet". An empty config is the
            # honest answer and every caller already handles it.
            cfg = EMPTY_CONFIG()
        # Return what was seeded rather than reading it back: the seed may not
        # have landed, and the answer is the same either way.
        _seed(CONFIG, cfg)
        return cfg
    try:
        return _read(CONFIG)
    except (OSError, ValueError) as e:
        print(f"!! seasonal.json could not be read ({e}) — reporting no seasonal "
              f"tags this run", file=sys.stderr)
        return EMPTY_CONFIG()


def save_config(cfg):
    _write(CONFIG, cfg)
    # An empty config never overwrites a backup that still holds tags. Without
    # that rule the backup would be emptied by the same accident it exists to
    # undo, one read later.
    if tag_count(cfg):
        _write(CONFIG_BACKUP, cfg)


def tags_lost(conn=None):
    """Why an empty tag map looks LOST rather than deliberately empty — or None.

    An empty seasonal.json is normal twice: on a fresh install, and after the
    operator untags everything. It is not normal when we can prove there used to
    be tags. Two proofs, either is enough:

      * a backup sitting next to it that still holds tags; and
      * ad groups in THIS market whose last seasonal action was our own pause.
        Those are stranded: plan() walks the tag map to decide what to release,
        so with no tags they can never be re-enabled by the calendar again.

    `conn` is optional — the file-level proof needs no database, and a DB with no
    writes_log yet must not turn this into an exception on the alerts path.
    """
    try:
        cfg = _read(CONFIG) if os.path.exists(CONFIG) else {}
    except (OSError, ValueError):
        return None
    if tag_count(cfg):
        return None

    backup_tags = 0
    if os.path.exists(CONFIG_BACKUP):
        try:
            backup_tags = tag_count(_read(CONFIG_BACKUP))
        except (OSError, ValueError):
            backup_tags = 0

    stranded = 0
    if conn is not None:
        try:
            stranded = len(_seasonally_paused_ids(conn))
        except Exception:
            stranded = 0

    if not backup_tags and not stranded:
        return None

    parts = []
    if backup_tags:
        parts.append(f"{os.path.basename(CONFIG_BACKUP)} still holds {backup_tags} of them")
    if stranded:
        parts.append(f"{stranded} ad group{'s' if stranded != 1 else ''} we seasonal-paused "
                     f"cannot be re-enabled without the tags")
    return {"backup_tags": backup_tags, "stranded": stranded,
            "reason": "the seasonal tag map is empty but designs were tagged before — "
                      + " and ".join(parts)}


def _md(s):
    m, d = s.split("-")
    return int(m), int(d)


def in_window(today, resume, pause):
    """Is `today` (a date) inside the recurring [resume..pause] MM-DD window?
    Handles windows that wrap New Year (resume MM-DD later in the year than pause)."""
    t, r, p = (today.month, today.day), _md(resume), _md(pause)
    if r <= p:
        return r <= t <= p
    return t >= r or t <= p          # wraps year-end


def next_transition(today, resume, pause):
    """ISO date of the next active-state flip (this year or next)."""
    cands = []
    for md in (resume, pause):
        m, d = _md(md)
        for y in (today.year, today.year + 1):
            try:
                cd = dt.date(y, m, d)
            except ValueError:
                continue
            if cd >= today:
                cands.append(cd)
                break
    return min(cands).isoformat() if cands else None


def _asin_ad_groups(conn):
    """asin -> [(ad_group_id, state)] for the current market's DB."""
    out = {}
    for asin, agid, state in conn.execute(
            """SELECT p.asin, p.ad_group_id, g.state
                 FROM ad_group_product p JOIN ad_groups g ON g.ad_group_id = p.ad_group_id
                WHERE p.asin IS NOT NULL"""):
        out.setdefault(asin, []).append((str(agid), state))
    return out


def _seasonally_paused_ids(conn):
    """Ad group ids whose most-recent seasonal action was our pause (still ours to release)."""
    latest = {}
    for eid, action in conn.execute(
            """SELECT entity_id, action FROM writes_log
                WHERE entity_type='adGroup' AND action IN ('seasonal_pause','seasonal_enable')
                ORDER BY rowid"""):
        latest[str(eid)] = action
    return {eid for eid, action in latest.items() if action == "seasonal_pause"}


def plan(conn, today=None):
    """Read-only: (to_pause, to_enable) action lists for the current market."""
    today = today or dt.date.today()
    cfg = load_config()
    seasons, tags = cfg.get("seasons", {}), cfg.get("asins", {})
    ag_by_asin = _asin_ad_groups(conn)
    seasonally_paused = _seasonally_paused_ids(conn)

    to_pause, to_enable = [], []
    for asin, season_key in tags.items():
        season = seasons.get(season_key)
        if not season:
            continue
        active = in_window(today, season["resume"], season["pause"])
        label = season.get("label", season_key)
        for agid, state in ag_by_asin.get(asin, []):
            row = {"ad_group_id": agid, "asin": asin, "season": season_key, "label": label}
            if not active and state == "ENABLED":
                to_pause.append(row)
            elif active and state == "PAUSED" and agid in seasonally_paused:
                to_enable.append(row)
    return to_pause, to_enable


def suggest(conn):
    """Scan design titles (ad group names) for each season's keywords and propose
    tags — one best match per ASIN. Read-only; the operator confirms before tagging.
    Keywords match on WORD boundaries (so 'santa' hits "Santa" but not "Santander")."""
    import re
    cfg = load_config()
    seasons, tags = cfg.get("seasons", {}), cfg.get("asins", {})

    def norm(s):
        # underscores separate the ASIN_type_Title name parts; treat as spaces so a
        # leading title word ("..._hoodie_CHRISTMAS") gets a real word boundary.
        return (s or "").lower().replace("’", "'").replace("_", " ")

    # precompile a boundary regex per keyword, once
    patterns = {key: [(k, re.compile(r"\b" + re.escape(norm(k)) + r"\b"))
                      for k in s.get("keywords", [])]
                for key, s in seasons.items()}

    matches = {}
    for asin, name in conn.execute(
            """SELECT DISTINCT p.asin, g.name
                 FROM ad_group_product p JOIN ad_groups g ON g.ad_group_id = p.ad_group_id
                WHERE p.asin IS NOT NULL AND g.name IS NOT NULL"""):
        if asin in matches:
            continue
        n = norm(name)
        for key, s in seasons.items():
            hit = next((k for k, rx in patterns[key] if rx.search(n)), None)
            if hit:
                matches[asin] = {
                    "asin": asin, "name": name, "season": key, "label": s.get("label", key),
                    "keyword": hit, "current_season": tags.get(asin),
                    "already_tagged": tags.get(asin) == key}
                break
    return sorted(matches.values(), key=lambda x: (x["label"], x["asin"]))


def apply(conn, to_pause, to_enable):
    client = AdsClient()
    results = {"paused": 0, "enabled": 0, "errors": []}

    def _set(items, state, action, prev):
        if not items:
            return 0
        ids = [it["ad_group_id"] for it in items]
        res = client.set_ad_groups_state(ids, state)
        # 207 batches can reject individual items — mirror and count only what
        # Amazon accepted, and log rejected ids as failed.
        accepted = ads_client.certain_ids(res, ids)
        rejected = {str(i) for i in ids if str(i) not in accepted}
        ok = ads_client.items_ok(res)
        for it in items:
            db.log_write(conn, action, "adGroup", it["ad_group_id"],
                         f"seasonal {it['season']}: {prev}->{state}", prev,
                         "failed" if str(it["ad_group_id"]) in rejected
                         or not any(b.get("http") in (200, 207) for b in res)
                         else "submitted")
        accepted = [i for i in ids if str(i) in accepted] \
            if any(b.get("http") in (200, 207) for b in res) else []
        if accepted:
            db.set_local_ad_group_state(conn, accepted, state)
        if not ok:
            results["errors"].append({action: [b.get("http") for b in res],
                                      "rejected": sorted(rejected)})
        return len(accepted)

    results["paused"] = _set(to_pause, "PAUSED", "seasonal_pause", "ENABLED")
    results["enabled"] = _set(to_enable, "ENABLED", "seasonal_enable", "PAUSED")
    return results


def main():
    args = sys.argv[1:]
    conn = db.connect()
    mkt = markets.current()
    to_pause, to_enable = plan(conn)
    print(f"[{mkt}] seasonal: {len(to_pause)} to pause, {len(to_enable)} to enable")
    for it in to_pause:
        print(f"  PAUSE  {it['asin']} ({it['label']}) ad group {it['ad_group_id']}")
    for it in to_enable:
        print(f"  ENABLE {it['asin']} ({it['label']}) ad group {it['ad_group_id']}")
    if not to_pause and not to_enable:
        print("  nothing to do.")
        return
    if "--apply" not in args:
        print("\nPREVIEW ONLY. Re-run with --apply.")
        return
    killswitch.check()
    if "--auto" not in args and input("\nType APPLY to execute: ").strip() != "APPLY":
        print("aborted.")
        return
    res = apply(conn, to_pause, to_enable)
    msg = f"[{mkt}] applied: paused {res['paused']}, enabled {res['enabled']}"
    if res["errors"]:
        msg += f", errors {res['errors']}"
    print(msg)


if __name__ == "__main__":
    main()
