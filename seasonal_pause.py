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
import sys

import db
import killswitch
import markets
import ads_client
from ads_client import AdsClient

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "seasonal.json")
CONFIG_EXAMPLE = os.path.join(HERE, "seasonal.example.json")


def load_config():
    """Read the seasonal config.

    seasonal.json is operator data (which ASIN belongs to which season), so it is
    gitignored and absent on a fresh clone. Seed it from the shipped example on
    first read instead of crashing: the example carries the season windows and
    keyword lists but no ASINs, so a new install starts with everything untagged
    and seasonal_pause is a no-op until the operator tags designs.
    """
    if not os.path.exists(CONFIG) and os.path.exists(CONFIG_EXAMPLE):
        with open(CONFIG_EXAMPLE) as src, open(CONFIG, "w") as dst:
            dst.write(src.read())
    with open(CONFIG) as f:
        return json.load(f)


def save_config(cfg):
    with open(CONFIG, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")


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
        rejected = ads_client.failed_ids(res)
        ok = ads_client.items_ok(res)
        for it in items:
            db.log_write(conn, action, "adGroup", it["ad_group_id"],
                         f"seasonal {it['season']}: {prev}->{state}", prev,
                         "failed" if str(it["ad_group_id"]) in rejected
                         or not any(b.get("http") in (200, 207) for b in res)
                         else "submitted")
        accepted = [i for i in ids if str(i) not in rejected] \
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
