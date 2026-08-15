#!/usr/bin/env python3
"""
HARVEST PRUNE — pause underperforming targets inside 'Harvested …' campaigns,
one target at a time:
  • 'Harvested … - Exact' → EXACT KEYWORDS   (paused via set_keywords_state)
  • 'Harvested … - ASIN'  → ASIN_SAME_AS PRODUCT TARGETS (paused via set_targets_state)

Why this exists (the gap it fills):
  A harvested target is promoted because a search term / product converted once.
  But each ad group holds SEVERAL targets per ASIN, and the existing engine never
  judges a target on its own:
    • phase2 pauses whole AD GROUPS — it averages a bleeding target against a good
      sibling in the same ad group, so the blend looks fine and nothing is cut.
    • phase3 bids AUTO/PRODUCT clauses but only ever lowers a loser to the floor bid;
      it never PAUSES one, and it doesn't touch KEYWORDS at all.
  So a bad target bleeds next to a good sibling. This evaluates each target on its
  OWN latest snapshot and pauses only the wasteful ones (never the good sibling):
    • DEAD         : >= MIN_CLICKS clicks and 0 orders
    • UNPROFITABLE : >= MIN_CLICKS clicks, ACOS over the product's BREAK-EVEN,
                     and CVR under the floor — converts, but loses money with no promise.
  Profitable targets, and ones that just need a lower bid, are left alone.

Per market. Preview by default; --apply writes (typed APPLY, or --auto). KILL-aware.
Skips anything we've already paused (the 30-day snapshot keeps a paused target's
history for weeks, so without this it would re-propose every run).

Usage:
  python3 harvest_prune.py                 # preview US
  python3 harvest_prune.py --apply         # pause (typed APPLY)
  ADS_MARKET=DE python3 harvest_prune.py --apply --auto
"""

import re
import sys

import db
import killswitch
import markets
import products
from ads_client import AdsClient

MIN_CLICKS = 15          # evidence needed before pausing a target (matches the kill list)
CVR_FLOOR = 0.08         # csmetro's "no promise to convert" line

RX_TARGET_ASIN = re.compile(r'asin\s*=\s*"?([A-Z0-9]{10})"?', re.I)


def _cvr(orders, clicks):
    return (orders / clicks) if clicks else 0.0


def _already_paused(conn):
    """Keyword/target ids we've already paused and not since re-enabled."""
    latest = {}
    for action, eid in conn.execute(
        """SELECT action, entity_id FROM writes_log
           WHERE action IN ('pause_keyword','undo_pause_keyword',
                            'pause_target','undo_pause_target') ORDER BY rowid"""):
        latest[str(eid)] = action
    return {eid for eid, a in latest.items() if a in ("pause_keyword", "pause_target")}


def build_plan(conn):
    """-> (snapshot_date, [dict…]). Each dict: entity_id, kind ('keyword'|'target'),
    label, ad_group_id, campaign_id, asin, type, clicks, orders, cost, sales, acos,
    cvr, break_even, reason. One per target to pause."""
    cur = conn.cursor()
    end = cur.execute("SELECT MAX(date) FROM targeting_perf").fetchone()[0]
    gate = products.econ_gate(conn=conn)
    if not gate["ok"]:
        print("ECON GATE CLOSED — no harvest-prune plan this run: "
              + "; ".join(gate["reasons"]))
        return end, []
    pmap = db.get_product_map(conn)                       # ad_group_id -> product_type
    dmap = db.get_design_map(conn)
    trans = db.active_price_changes(conn)
    prod = {r[0]: r[1] for r in cur.execute("SELECT ad_group_id, asin FROM ad_group_product")}
    paused = _already_paused(conn)                        # skip ones we already cut

    # harvested EXACT (keywords) + ASIN (product targets); kind per campaign
    camps = {}
    for cid, name in cur.execute(
        """SELECT campaign_id, name FROM campaigns
           WHERE name LIKE 'Harvested %- Exact' OR name LIKE 'Harvested %- ASIN'"""):
        camps[cid] = (name, "keyword" if (name or "").endswith("- Exact") else "target")
    if not camps:
        return end, []

    plan = []
    for cid, agid, tid, targeting, clicks, orders, cost, sales, acos in cur.execute(
        """SELECT campaign_id, ad_group_id, target_id, targeting, clicks, orders, cost, sales, acos
           FROM targeting_perf
           WHERE date=? AND target_id IS NOT NULL""", (end,)):
        if cid not in camps or str(tid) in paused:
            continue
        name, kind = camps[cid]
        clicks, orders, cost, sales = (clicks or 0), (orders or 0), (cost or 0), (sales or 0)
        if clicks < MIN_CLICKS:
            continue
        ptype = pmap.get(str(agid)) or products.infer_type_from_campaign(name)
        # per-design break-even (harvested groups are single-ASIN by construction);
        # designs in an active price transition use the MAX break-even across legs,
        # transition-unknown/unsupported-price designs are skipped (PLAN.md §4/§15)
        d = dmap.get(str(agid))
        econ_sfx = ""
        if markets.is_default() and (d or {}).get("product_type") == products.TEE \
                and (d or {}).get("asin"):
            cents = products.parse_price_cents(d.get("list_price"))
            legs = trans.get(d["asin"], [])
            if any(o is None for o, _n, _a in legs):
                continue                                  # transition-unknown
            if legs:
                be, unk = products.transition_break_even(cents, legs)
                if unk or be is None:
                    continue
                econ_sfx = db.econ_suffix(price_cents=cents, break_even=be,
                                          src="us_tee_table+transition",
                                          model=products.US_TEE_ROYALTY_V)
            else:
                e = products.get_design_econ(products.TEE, price=cents)
                if not e.get("known_price"):
                    continue                              # unsupported price: skip
                be = e["break_even"]
                econ_sfx = db.econ_suffix(price_cents=cents,
                                          royalty_cents=e.get("royalty_cents"),
                                          break_even=be, src=e["src"],
                                          model=e["model_version"])
        else:
            be = products.get_econ(ptype).get("break_even")
        cvr = _cvr(orders, clicks)
        acos = acos if acos is not None else (cost / sales if sales else None)
        reason = None
        if orders == 0:
            reason = f"{clicks} clicks, 0 sales"
        elif be is not None and acos is not None and acos > be and cvr < CVR_FLOOR:
            reason = (f"ACOS {acos*100:.0f}% > break-even {be*100:.0f}% "
                      f"& CVR {cvr*100:.0f}% < {CVR_FLOOR*100:.0f}%")
        if not reason:
            continue
        if kind == "target":
            m = RX_TARGET_ASIN.search(targeting or "")
            label = f"product {m.group(1)}" if m else (targeting or "")
        else:
            label = targeting or ""
        plan.append(dict(entity_id=str(tid), kind=kind, label=label, ad_group_id=str(agid),
                         campaign_id=str(cid), asin=prod.get(agid), type=ptype,
                         clicks=clicks, orders=orders, cost=round(cost, 2),
                         sales=round(sales, 2), acos=acos, cvr=round(cvr, 4),
                         break_even=be, reason=reason, econ_sfx=econ_sfx))
    plan.sort(key=lambda p: p["cost"], reverse=True)
    return end, plan


def preview(plan):
    wasted = sum(p["cost"] for p in plan)
    kws = sum(1 for p in plan if p["kind"] == "keyword")
    tgts = len(plan) - kws
    print("PREVIEW — nothing changed.\n")
    print(f"  Harvested targets to pause: {len(plan)}  ({kws} keywords, {tgts} ASIN targets, "
          f"${wasted:,.2f}/mo wasted)")
    for p in plan[:12]:
        tag = "kw " if p["kind"] == "keyword" else "asn"
        print(f"    [{tag}] ${p['cost']:6.2f}  {(p['label'] or '')[:42]:42}  {p['reason']}")


def confirm():
    return input("\nType APPLY to pause these targets (anything else cancels): ").strip() == "APPLY"


def _pause_batch(client, conn, rows, api, action, etype):
    if not rows:
        return 0
    try:
        res = api([p["entity_id"] for p in rows], "PAUSED")
        ok = all(b["http"] in (200, 207) for b in res) if res else False
    except Exception as e:
        print(f"  {etype}s: pause call raised {e!r}")
        ok = False
    for p in rows:
        db.log_write(conn, action, etype, p["entity_id"],
                     f"{p['label']} ({p['reason']})" + (p.get("econ_sfx") or ""),
                     "ENABLED", "submitted" if ok else "failed")
    print(f"  {etype}s: paused {len(rows)} ({'ok' if ok else 'FAILED'})")
    return len(rows) if ok else 0


def apply(client, conn, plan):
    kws = [p for p in plan if p["kind"] == "keyword"]
    tgts = [p for p in plan if p["kind"] == "target"]
    n = 0
    n += _pause_batch(client, conn, kws, client.set_keywords_state, "pause_keyword", "keyword")
    n += _pause_batch(client, conn, tgts, client.set_targets_state, "pause_target", "target")
    print(f"  paused {n} total. See writes_log.")


def main():
    args = set(sys.argv[1:])
    mkt = markets.current()
    conn = db.connect()
    end, plan = build_plan(conn)
    print(f"[{mkt}] snapshot {end} | harvested targets over {MIN_CLICKS} clicks reviewed")
    if not plan:
        print("  nothing to prune — every harvested target is pulling its weight.")
        return
    preview(plan)
    if "--apply" not in args:
        print("\nPREVIEW ONLY. Re-run with --apply.")
        return
    killswitch.check()
    if "--auto" not in args and not confirm():
        print("Cancelled — nothing written.")
        return
    apply(AdsClient(mkt), conn, plan)


if __name__ == "__main__":
    main()
