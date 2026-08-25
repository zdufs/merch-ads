#!/usr/bin/env python3
"""
PHASE 2 — APPLY (the first step that WRITES to Amazon).
Adds negative keywords and pauses non-converting ad groups, per the locked rules.

SAFETY:
  - Runs in PREVIEW mode by default — shows exactly what it would do, writes nothing.
  - Only writes when run with --apply AND you type APPLY when prompted.
  - Every change is logged to writes_log (with prev state) for review + rollback.
  - Negatives and pauses are both reversible.

Usage:
  python3 phase2_apply.py                 # preview everything (no writes)
  python3 phase2_apply.py --negatives-only --apply
  python3 phase2_apply.py --pauses-only --apply
  python3 phase2_apply.py --apply          # both
  python3 phase2_apply.py --rollback-pauses # re-enable ad groups paused in the last apply
"""

import sqlite3
import datetime
import sys
import db
import products
import killswitch
import scavenger
import cross_sell
import ads_client
from ads_client import AdsClient

FLOOR = 1.00          # ad-group pause floor (then filtered per product type)
MIN_CLICKS_NEG = 10   # negate a search term after 10 clicks with 0 sales
MIN_CLICKS_PAUSE = 20 # need 20+ clicks before pausing a CONVERTING ad group on CVR/ACOS
CVR_FLOOR = 0.08      # "no promise to convert" = under 8% CVR (metro's rule)
# converting terms are negated when ACOS exceeds the product's TARGET ACOS
# (US standard tee = 30% Model A; everything else = its break-even, per market)


def _design_target(dmap_row, trans):
    """(target_acos, unknown, econ_sfx) for ONE ad group's economics-driven rules.
    US single-ASIN tees: price-specific target; during an active transition the
    MAX break-even across all supported legs applies; any NULL/unsupported leg or
    an unknown price -> unknown=True (caller must SKIP economics-driven
    destructive rules for it, PLAN.md §4/§15). Cohorts/non-tees: per-type."""
    import markets
    pt = (dmap_row or {}).get("product_type")
    asin = (dmap_row or {}).get("asin")
    if not (markets.is_default() and pt == products.TEE and asin):
        e = products.get_econ(pt)
        return e["target_acos"], False, ""
    cents = products.parse_price_cents((dmap_row or {}).get("list_price"))
    legs = trans.get(asin, [])
    if any(o is None for o, _n, _a in legs):
        return None, True, ""                        # transition-unknown (seeded)
    if legs:
        be, unk = products.transition_break_even(cents, legs)
        if unk or be is None:
            return None, True, ""
        tgt = round(min(products.US_TEE_GROWTH_TARGET, be), 4)
        sfx = db.econ_suffix(price_cents=cents, break_even=be, target=tgt,
                             src="us_tee_table+transition",
                             model=products.US_TEE_ROYALTY_V)
        return tgt, False, sfx
    e = products.get_design_econ(pt, price=cents)
    if not e.get("known_price"):
        return None, True, ""                        # unsupported/missing price
    sfx = db.econ_suffix(price_cents=cents, royalty_cents=e.get("royalty_cents"),
                         break_even=e["break_even"], target=e["target_acos"],
                         src=e["src"], model=e["model_version"])
    return e["target_acos"], False, sfx


def candidates(conn):
    cur = conn.cursor()
    # Negatives read search_term_perf, pauses read targeting_perf. Each table is
    # filled by its OWN Amazon report job, so they drift apart whenever one job
    # fails. This used to take MAX(date) from campaign_perf — a third table that
    # phase 2 never reads — so once targeting fell behind, `WHERE date=?` matched
    # nothing and the run reported "0 negatives, 0 pauses" instead of "no data".
    # Resolve each date from the table it belongs to, and gate each source.
    st_gate = db.snapshot_gate(conn, "search_term_perf")
    tg_gate = db.snapshot_gate(conn, "targeting_perf")
    # Honest "as of": the plan is no fresher than the oldest evidence behind it.
    end = min([g["date"] for g in (st_gate, tg_gate) if g["date"]], default=None)
    gate = products.econ_gate(conn=conn)
    # Diagnostics go to stderr, never stdout: candidates() is called in-process
    # by appctl's negatives-preview, whose serve worker reads ONE stdout line
    # per request. A stray stdout line here lands in the pipe before the JSON
    # envelope and desyncs every following reply (the USKDP contract-mismatch
    # cascade, Aug 2026). stderr keeps the warning visible to a CLI human.
    if not gate["ok"]:
        print("ECON GATE CLOSED — no phase-2 proposals this run: "
              + "; ".join(gate["reasons"]), file=sys.stderr)
        return end, [], []
    if not st_gate["ok"]:
        print(f"SEARCH-TERM DATA STALE — no negative-keyword proposals: {st_gate['reason']}", file=sys.stderr)
    if not tg_gate["ok"]:
        print(f"TARGETING DATA STALE — no ad-group pause proposals: {tg_gate['reason']}", file=sys.stderr)
    dmap = db.get_design_map(conn)    # ad_group_id -> {asin, product_type, list_price,…}
    trans = db.active_price_changes(conn)
    pmap = db.get_product_map(conn)   # ad_group_id -> product_type
    # Scavenger runs on its OWN optimizer — keep the standard rules off it. Anything
    # not ENABLED is skipped on STATE: Amazon keeps reporting trailing-30 rows for
    # paused and archived campaigns, so they still surface here. This used to name a
    # retired strategy instead, whose campaigns happened to be archived — the name
    # test was standing in for a state test.
    skip_ids = {r[0] for r in cur.execute("SELECT campaign_id,name,state FROM campaigns")
                if scavenger.is_scavenger(r[1]) or (r[2] or "") != "ENABLED"}

    # Negative-keyword rules (ad-group level, per ASIN):
    #   1) 0 sales AND >= 10 clicks  -> wasted clicks
    #   2) has sales BUT ACOS > 30%  -> converting unprofitably
    raw = cur.execute(
        """SELECT search_term, campaign_id, ad_group_id, clicks, orders, cost, acos
           FROM search_term_perf WHERE date=? AND search_term IS NOT NULL""",
        (st_gate["date"],)).fetchall() if st_gate["ok"] else []
    negs = []
    for st, cid, agid, clicks, orders, cost, acos in raw:
        if cid in skip_ids:
            continue
        clicks, orders, cost = (clicks or 0), (orders or 0), (cost or 0)
        if orders == 0 and clicks >= MIN_CLICKS_NEG:
            # waste rule — price-independent, applies regardless of transition
            negs.append((st, cid, agid, cost, f"{clicks} clicks, 0 sales", "",
                         {"rule": "waste", "clicks": clicks, "orders": orders,
                          "min_clicks": MIN_CLICKS_NEG}))
        elif orders >= 1 and acos is not None:
            ceiling, unknown, sfx = _design_target(dmap.get(str(agid)), trans)
            if unknown:
                continue   # transition-unknown / unsupported price: no econ-driven negative
            if acos > ceiling:
                negs.append((st, cid, agid, cost,
                             f"converts but ACOS {acos*100:.0f}% > {ceiling*100:.0f}%", sfx,
                             {"rule": "acos", "acos": acos, "ceiling": ceiling}))

    # Drop terms we've ALREADY negated — otherwise phase2 re-proposes the same
    # NEGATIVE_EXACTs every night (their clicks linger in the trailing window long
    # after negation), Amazon rejects them as duplicateValueError, and the digest /
    # Discord alert reports "N negatives" that are all no-ops. Ad-group negatives
    # from phase2 AND the app (negate / negatives-apply) all log as add_negative
    # with entity_id=ad_group_id, detail=keyword text — so this catches every path.
    # detail may carry an ` econ_v1={...}` suffix — always compare the PREFIX
    # A FAILED attempt is not an existing negative. Every add_negative row used to
    # count, so a term whose negative Amazon rejected was filtered out of the
    # candidates for good and went on spending — the audit row that recorded the
    # failure was the very thing that hid it.
    already = {(str(agid), db.detail_prefix(term)) for agid, term, res in cur.execute(
        "SELECT entity_id, detail, result FROM writes_log WHERE action='add_negative' "
        "AND NOT " + db.FAILED_RESULT_SQL)}
    negs = [n for n in negs if (str(n[2]), n[0]) not in already]
    negs.sort(key=lambda r: r[3], reverse=True)

    # Ad-group PAUSE rules — both gated by the per-type, proven-aware spend threshold
    # ("overspend"). Then pause if EITHER:
    #   1) 0 sales                                   (never converted)
    #   2) converts but ACOS > target AND CVR < 8%   (overspends with no promise of profit)
    # Profitable low-CVR designs (ACOS at/under target) are KEPT — phase3 just tunes their bids.
    lmap = db.get_lifetime_map(conn)   # proven-winner guardrail
    # An ad group that is already PAUSED or ARCHIVED cannot be paused again, and
    # proposing it is not harmless. The campaign filter above was standing in for
    # this one, so on 2026-08-23 all 23 proposals across US/DE/FR/ES/IT pointed at
    # ad groups that were ALREADY paused — every night, in the Approval Queue and
    # in the Discord digest, under a count that read like real work. Worse, each
    # one was logged with prev_state "ENABLED" (see apply_pauses), so an Undo
    # would have ENABLED an ad group the operator had deliberately paused.
    ag_state = dict(cur.execute("SELECT ad_group_id, state FROM ad_groups"))
    raw_p = cur.execute(
        """SELECT ad_group_id, campaign_id, SUM(cost) spend, SUM(orders) orders,
                  SUM(clicks) clicks, SUM(sales) sales
           FROM targeting_perf WHERE date=? GROUP BY ad_group_id
           HAVING spend>=? ORDER BY spend DESC""",
        (tg_gate["date"], FLOOR)).fetchall() if tg_gate["ok"] else []
    pauses = []
    for agid, cid, spend, orders, clicks, sales in raw_p:
        if cid in skip_ids:
            continue
        if (ag_state.get(str(agid)) or "") != "ENABLED":
            continue   # already paused/archived, or never mirrored — nothing to do
        pt = pmap.get(str(agid))
        if (spend or 0) < products.pause_threshold(pt, lmap.get(str(agid), 0)):
            continue   # hasn't overspent enough (proven winners get 3x runway)
        orders, clicks, sales = (orders or 0), (clicks or 0), (sales or 0)
        target, unknown, sfx = _design_target(dmap.get(str(agid)), trans)
        if unknown:
            continue   # transition(-unknown)/unsupported price: skip destructive pause
        if orders == 0:
            pauses.append((agid, cid, round(spend, 2), "0 sales", sfx, {"rule": "no_sales"}))
            continue
        acos = spend / sales if sales else None
        cvr = orders / clicks if clicks else 0
        if acos is not None and acos > target and cvr < CVR_FLOOR and clicks >= MIN_CLICKS_PAUSE:
            pauses.append((agid, cid, round(spend, 2),
                           f"ACOS {acos*100:.0f}% > {target*100:.0f}% & CVR {cvr*100:.0f}% < 8%", sfx,
                           {"rule": "acos_cvr", "acos": acos, "target": target,
                            "cvr": cvr, "cvr_floor": CVR_FLOOR}))

    # Spare a bleeding design when its ad drives enough owned cross-sell royalty
    # to cover its own spend — pausing it would kill the catalogue sales the ad
    # creates. Same threshold and helper as the kill list. Fail-open: an empty
    # map (EU markets, no purchased-product snapshot) spares nothing, so this can
    # only ever KEEP a design enabled, never pause one.
    cross_map = cross_sell.owned_cross_sell_royalty(conn)
    if cross_map:
        kept = [p for p in pauses if not cross_sell.spares_pause(cross_map, p[0], p[2])]
        spared_n = len(pauses) - len(kept)
        if spared_n:
            print(f"CROSS-SELL SPARE — kept {spared_n} ad group(s) whose ads drive "
                  "enough owned cross-sell royalty to cover their own spend",
                  file=sys.stderr)
        pauses = kept
    return end, negs, pauses


def preview(negs, pauses):
    neg_spend = sum(r[3] for r in negs)
    pause_spend = sum(r[2] for r in pauses)
    print("PREVIEW — nothing has been changed.\n")
    print(f"  Negative keywords to add : {len(negs)}  (${neg_spend:,.2f}/mo)")
    print(f"  Ad groups to pause       : {len(pauses)}  (${pause_spend:,.2f}/mo)")
    print(f"  Total recoverable        : ${neg_spend + pause_spend:,.2f}/mo\n")
    print("  Sample negatives:")
    for r in negs[:6]:
        reason = r[4] if len(r) > 4 else ""
        print(f"    ${r[3]:5.2f}  '{r[0]}'  ({reason})")
    print("  Sample pauses:")
    for r in pauses[:6]:
        reason = r[3] if len(r) > 3 else ""
        print(f"    ${r[2]:6.2f}  ad group {r[0]}  ({reason})")


def confirm():
    ans = input('\nType APPLY to write these changes to Amazon (anything else cancels): ').strip()
    return ans == "APPLY"


def apply_negatives(client, conn, negs):
    items = [{"campaignId": r[1], "adGroupId": r[2], "keywordText": r[0]} for r in negs]
    print(f"Adding {len(items)} negative keywords…")
    res = client.create_negative_keywords(items)
    sfx_by_key = {(str(n[2]), n[0]): (n[5] if len(n) > 5 else "") for n in negs}
    # log the created id per keyword — without `negid=` in the detail the
    # Audit Trail cannot offer Undo for it (same contract as cmd_negate)
    created = [cid for b in res for cid in (b.get("created_ids") or [])]
    outcomes = ads_client.item_outcomes(res, range(len(items)))
    for i, item in enumerate(items):
        sfx = sfx_by_key.get((str(item["adGroupId"]), item["keywordText"]), "")
        negid = created[i] if i < len(created) else None
        status = outcomes[i] if i < len(outcomes) else "uncertain"
        result = "submitted" if status == "accepted" else (
            "duplicate" if status == "duplicate" else "failed")
        detail = item["keywordText"] + sfx
        if status == "accepted" and negid:
            detail += f" negid={negid}"
        db.log_write(conn, "add_negative", "searchTerm", item["adGroupId"],
                     detail, "none", result)
    ok = sum(1 for b in res if b["http"] in (200, 207))
    print(f"  done ({ok}/{len(res)} batches accepted). See writes_log.")


def apply_pauses(client, conn, pauses):
    ids = [r[0] for r in pauses]
    print(f"Pausing {len(ids)} ad groups…")
    # The previous state is what Undo restores, so it has to be READ, not assumed.
    # It was the literal "ENABLED" for every row, which is only ever right by
    # luck — and candidates() was not filtering on ad-group state at all, so it
    # was routinely wrong. An Undo then enabled an ad group that had been paused
    # on purpose. An id we have never mirrored records an empty previous state
    # rather than a guess; db.log_write already treats that as "not undoable".
    prev = dict(conn.execute("SELECT ad_group_id, state FROM ad_groups"))
    res = client.pause_ad_groups(ids)
    # Amazon can reject individual items inside a 207 batch — mirror and log
    # only what it actually accepted, or the local state lies until tomorrow.
    # Mirror and log only what Amazon PROVED it accepted. `failed_ids` alone
    # cannot see a 500, or a 207 whose body we could not read: both produce an
    # empty rejected set, so every item looked accepted and the local state
    # started disagreeing with the account.
    accepted = ads_client.certain_ids(res, ids)
    for r in pauses:
        detail = (r[3] if len(r) > 3 else "pause") + (r[4] if len(r) > 4 else "")
        db.log_write(conn, "pause_ad_group", "adGroup", r[0],
                     detail, prev.get(str(r[0])) or "",
                     "submitted" if str(r[0]) in accepted else "failed")
    db.set_local_ad_group_state(conn, sorted(accepted), "PAUSED")
    ok = sum(1 for b in res if b["http"] in (200, 207))
    # Everything we asked for that Amazon did not PROVE it accepted. Derived
    # from the request, not from a failure list: a 500, or a 207 whose body we
    # could not read, names no failures and still wrote nothing.
    rejected = [i for i in ids if str(i) not in accepted]
    print(f"  done ({ok}/{len(res)} batches accepted"
          + (f", {len(rejected)} item(s) REJECTED" if rejected else "")
          + "). See writes_log.")


ROLLBACK_WINDOW_HOURS = 24


def rollback_pauses(client, conn):
    """Re-enable the ad groups THIS apply paused — not every one ever paused.

    The query took every pause_ad_group row in the whole log, with no time bound
    and no result filter. So it re-enabled failed attempts, and ad groups paused
    months ago that the operator had since paused again on purpose. The flag's
    own help says "paused in the last apply". It now means that.
    """
    since = (datetime.datetime.now()
             - datetime.timedelta(hours=ROLLBACK_WINDOW_HOURS)).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT DISTINCT entity_id FROM writes_log WHERE action='pause_ad_group' "
        "AND applied_at >= ? AND NOT " + db.FAILED_RESULT_SQL, (since,)).fetchall()
    ids = [r[0] for r in rows]
    if not ids:
        print("No paused ad groups in the log to roll back.")
        return
    print(f"Re-enabling {len(ids)} ad groups paused earlier…")
    res = client.set_ad_groups_state(ids, "ENABLED")
    accepted = ads_client.certain_ids(res, ids)
    for pid in ids:
        db.log_write(conn, "rollback_enable", "adGroup", pid, "rollback", "PAUSED",
                     "submitted" if str(pid) in accepted else "failed")
    db.set_local_ad_group_state(conn, sorted(accepted), "ENABLED")
    ok = sum(1 for b in res if b["http"] in (200, 207))
    rejected = [i for i in ids if str(i) not in accepted]
    print(f"  done ({ok}/{len(res)} batches accepted"
          + (f", {len(rejected)} item(s) REJECTED" if rejected else "") + ").")


def _flatten(batch_results):
    # one log row per item is overkill to parse from 207s; approximate 1:1
    out = []
    for b in batch_results:
        out.extend([b] * b.get("count", 1))
    return out


def main():
    args = set(sys.argv[1:])
    conn = db.connect()
    client = AdsClient()

    if "--rollback-pauses" in args:
        # KILL first. This is a live write to every ad group it touches, and it
        # ran BEFORE the freeze check — so the one command whose whole purpose is
        # to undo an apply was the one command a freeze could not stop.
        killswitch.check()
        if confirm():
            rollback_pauses(client, conn)
        else:
            print("Cancelled.")
        return

    end, negs, pauses = candidates(conn)
    print(f"Snapshot {end} | profile {client.profile_id}\n")
    preview(negs, pauses)

    do_neg = "--pauses-only" not in args
    do_pause = "--negatives-only" not in args

    if "--apply" not in args:
        print("\nPREVIEW ONLY. Re-run with --apply to execute (you'll be asked to confirm).")
        return

    killswitch.check()   # halts here if the KILL file is present
    # --auto skips the typed confirmation (for the scheduled job); else require APPLY
    if "--auto" not in args and not confirm():
        print("Cancelled — nothing written.")
        return

    if do_neg:
        apply_negatives(client, conn, negs)
    if do_pause:
        apply_pauses(client, conn, pauses)
    print("\nDone. Every change is in writes_log. Re-run phase0_pull.py later to see the effect.")


if __name__ == "__main__":
    main()
