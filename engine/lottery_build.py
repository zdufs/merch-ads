#!/usr/bin/env python3
"""
LOTTERY builder — create auto-targeting, ad-group-per-ASIN lottery campaigns for the
active market, from proven sellers (designs with >=1 sale).

Structure (matches your US Lotto): campaign = AUTO targeting + dynamic-bids-DOWN-ONLY;
ONE ad group per ASIN at a 30c base bid; one product ad per ad group. Amazon auto-targets
and lowers bids when a click is unlikely to convert; each ASIN keeps its own bid +
search-term data. The EXISTING engine manages them after creation (phase2 pauses dead
ASIN ad groups, phase3 tunes bids, harvest promotes winners).

Cohort = ALL live standard t-shirts (every sales tier) for this market — tees only,
strongest sellers first. Idempotent: skips ASINs that already have an ad group in an
existing LOTTO campaign.

SAFETY: preview by default; writes only with --apply + typed APPLY (or --auto); KILL aware.

Usage:
  ADS_MARKET=DE python3 lottery_build.py            # preview
  ADS_MARKET=DE python3 lottery_build.py --apply    # build (typed APPLY)
  ADS_MARKET=DE python3 lottery_build.py --apply --auto   # scheduled
"""

import csv
import datetime
import glob
import os

import paths
import re
import sys
import time

import db
import killswitch
import lottery
import markets
from ads_client import AdsClient, report_refused, success_ids

csv.field_size_limit(10**9)
POD = paths.POD_ROOT
SP_CAMP = "application/vnd.spCampaign.v3+json"
SP_AG = "application/vnd.spAdGroup.v3+json"


def newest_export():
    """The newest product-grid export. Callers pass a specific file when the app
    scopes a build to one intake export."""
    import export_reader
    path = export_reader.newest_catalog_file(POD)
    if not path:
        raise SystemExit(f"No product export in {POD}")
    return path


def _opt(args, name):
    """Value of `--name VALUE` in args, else None."""
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1]
    return None


def load_scope(path):
    """One ASIN per line -> uppercase set (the app's new-design intake scope)."""
    with open(path, encoding="utf-8") as fh:
        return {line.strip().upper() for line in fh if line.strip()}


RX_US_LOTTO = re.compile(r"^Lotto (\d+)$")

# US lottery campaigns hold up to 1000 tees each (the operator's rule); EU keeps the
# original 500 shard size.
US_MAX_ADGROUPS = 1000
# Nightly US cohort window: only tees uploaded recently (the full US catalog is
# tens of thousands — intake drops handle the day-to-day; this is the catch-up).
US_NIGHTLY_DAYS = 60


def per_campaign_cap():
    return US_MAX_ADGROUPS if markets.is_default() else lottery.MAX_ADGROUPS


def lotto_number(name):
    """'Lotto 7' (US) / 'LOTTO - 3' (EU) -> campaign number, else None.
    US non-numbered Lotto campaigns (hoodies/sweatshirts) are NOT lottery-tee
    campaigns and must never be filled."""
    if markets.is_default():
        m = RX_US_LOTTO.match((name or "").strip())
        return int(m.group(1)) if m else None
    if (name or "").startswith(lottery.PREFIX):
        try:
            return int((name or "")[len(lottery.PREFIX):].strip())
        except ValueError:
            return None
    return None


def numbered_name(n):
    return f"Lotto {n}" if markets.is_default() else lottery.camp_name(n)


def load_tees(export_path=None):
    """ALL live standard t-shirts for the active market (any sales tier)
    -> [(asin, title, created)], strongest sellers first (tees are apparel:
    the retail ASIN is ad-eligible)."""
    import export_reader
    xm = markets.cfg()["export_mkt"]
    rows, seen = [], set()
    # One named file when the app scopes a build to an intake export, otherwise
    # the merged catalog (Snap for MOD exports it in 100k-row chunks).
    source = (export_reader.rows(export_path) if export_path
              else export_reader.catalog_rows(POD, marketplace=xm))
    for p in source:
        if (p.get("marketplace") != xm or p.get("status") != "published"
                or p.get("productType") != lottery.COHORT_TYPE or not p.get("asin")):
            continue
        asin = p["asin"].upper()
        if asin in seen:
            continue
        try:
            tot = int(float(p.get("salesTotal") or 0))
        except (TypeError, ValueError):
            tot = 0
        seen.add(asin)
        rows.append((asin, p.get("productTitle") or "", tot, p.get("createdDate") or ""))
    rows.sort(key=lambda r: r[2], reverse=True)     # proven sellers first
    return [(a, t, c) for a, t, _, c in rows]


def confirm(n):
    return input(f"\nType APPLY to build/refresh {n} lottery campaign(s) (anything else cancels): ").strip() == "APPLY"


def lotto_inventory(client):
    """LIVE inventory of this market's numbered lottery campaigns:
    {num: {cid, name, asins, count}}. Ad-group names ARE the ASINs; archived
    ad groups don't occupy capacity. US 'Lotto Hoodies' etc. are excluded by
    lotto_number()."""
    inventory = {}
    for c in client.list_all("/sp/campaigns/list", SP_CAMP, "campaigns"):
        num = lotto_number(c.get("name"))
        if num is None or c.get("state") == "ARCHIVED":
            continue
        ags = client.list_all("/sp/adGroups/list", SP_AG, "adGroups",
                              extra_body={"campaignIdFilter": {"include": [str(c.get("campaignId"))]}})
        asins = {(a.get("name") or "").upper()
                 for a in ags if a.get("state") != "ARCHIVED"}
        raw_budget = c.get("budget")
        budget = (raw_budget or {}).get("budget") if isinstance(raw_budget, dict) else raw_budget
        inventory[num] = {"cid": str(c.get("campaignId")), "name": c.get("name"),
                          "asins": asins, "count": len(asins), "budget": budget,
                          # A campaign is reused by NUMBER whatever its state, so
                          # a PAUSED Lotto takes new ad groups and Amazon accepts
                          # every one of them — ads that can never serve, under
                          # counts that all look healthy. Reuse stays; the
                          # operator gets told. Same fault the scavenger builder
                          # already reports as `paused_campaigns`.
                          "state": c.get("state")}
    return inventory


def fill_plan(inventory, new_asins, cap):
    """Assign new ASINs: fill existing numbered campaigns in numeric order up to
    `cap` each, then plan new numbered campaigns after the highest existing one.
    Returns ordered [(num, exists, [(asin,title)…])]."""
    remaining = list(new_asins)
    plan = []
    for num in sorted(inventory):
        space = cap - inventory[num]["count"]
        if space <= 0 or not remaining:
            continue
        take, remaining = remaining[:space], remaining[space:]
        plan.append((num, True, take))
    next_num = max(inventory) + 1 if inventory else 1
    while remaining:
        take, remaining = remaining[:cap], remaining[cap:]
        plan.append((next_num, False, take))
        next_num += 1
    return plan


def add_asins(client, conn, cid, name, pairs):
    """Add ad-group-per-ASIN (+product ad, +clause bids) to one campaign.

    A batch Amazon does not fully accept says so on STDERR, with the HTTP status
    and the first few reasons out of the error block. This is the same blind
    spot scavenger_build had: `js` and `js2` were read only for their `success`
    entries and then dropped, so a refused ad group or product ad left a count
    and no cause anywhere on disk.
    """
    dbid = lottery.clause_bids()["close-match"]
    ag_added = pa_added = 0
    new_agids = set()
    stranded = []          # ASINs whose ad group landed but whose product ad did not
    for i in range(0, len(pairs), 100):
        batch = pairs[i:i + 100]
        ag_items = [{"name": a, "campaignId": cid, "defaultBid": dbid} for a, _ in batch]
        st, js = client.create_ad_groups(ag_items)
        made = success_ids(js, "adGroups", "adGroupId")   # {index: adGroupId}
        report_refused("adGroups", len(ag_items), len(made), st, js, label=name,
                       items=ag_items)
        ag_added += len(made)
        new_agids.update(str(made[idx]) for idx in made)
        order = sorted(made)
        pa_items = [{"campaignId": cid, "adGroupId": made[idx], "asin": batch[idx][0]}
                    for idx in order]
        if pa_items:
            st2, js2 = client.create_product_ads(pa_items)
            pa_ok = success_ids(js2, "productAds", "adId")
            report_refused("productAds", len(pa_items), len(pa_ok), st2, js2, label=name,
                           items=pa_items)
            pa_added += len(pa_ok)
            stranded.extend(batch[order[j]][0] for j in range(len(order))
                            if j not in pa_ok)
        time.sleep(1)   # gentle pacing
    print(f"  ad groups added: {ag_added}/{len(pairs)} | product ads: {pa_added}")
    # An ad group with no product ad advertises NOTHING, and the next run's
    # inventory reads ad-group NAMES — the name is the ASIN — so it treats that
    # ASIN as already placed and never comes back for it. The empty ad group
    # sits there for good.
    #
    # So the count that leaves this function, and the count in the audit row,
    # are CONFIRMED PRODUCT ADS. `ag_added` was both, which meant a failed
    # product ad was recorded as an ASIN placed.
    if stranded:
        print(f"  !! {len(stranded)} ad group(s) got NO product ad and cannot "
              f"serve: {', '.join(stranded[:10])}"
              + (" …" if len(stranded) > 10 else ""))
        db.log_write(conn, "lotto_add_asins", "campaign", cid,
                     f"{len(stranded)} ASINs got an ad group but NO product ad — "
                     f"they advertise nothing and the next run will treat them "
                     f"as already placed: {', '.join(stranded[:20])}",
                     "", "failed")
    db.log_write(conn, "lotto_add_asins", "campaign", cid,
                 f"{pa_added} ASINs placed ({ag_added} ad groups created)",
                 "", "submitted")
    if new_agids:
        set_clause_bids(client, conn, cid, new_agids)
    return pa_added


REV_EXPR = {v: k for k, v in lottery.EXPRESSION_TYPE.items()}


def set_clause_bids(client, conn, cid, new_agids):
    """For the given (new) ad groups in a campaign, set close/loose/substitutes bids and
    pause complements, per lottery.clause_bids(). Retries so large batches whose auto
    clauses generate slowly are still caught."""
    cb = lottery.clause_bids()
    pending = {str(a) for a in new_agids}
    bid_updates, pause_ids = [], []
    for attempt in range(3):
        time.sleep(5 if attempt == 0 else 12)   # let Amazon auto-generate the clauses
        seen = set()
        for c in client.list_targets([cid]):
            ag = str(c.get("adGroupId"))
            if ag not in pending:
                continue
            seen.add(ag)
            expr = c.get("expression") or []
            etype = expr[0].get("type") if expr and isinstance(expr[0], dict) else c.get("expressionType")
            name = REV_EXPR.get(etype)
            tid = c.get("targetId")
            if not tid or not name:
                continue
            if name in lottery.PAUSE_EXPRESSIONS:
                pause_ids.append(tid)
            elif name in cb and abs((c.get("bid") or 0) - cb[name]) >= 0.01:
                bid_updates.append({"targetId": tid, "bid": cb[name]})
        pending -= seen
        if not pending:
            break
    # Count what Amazon ACCEPTED, not what was requested. These two return
    # per-batch results and both were discarded, so the line below printed the
    # size of the plan — a 207 with half its items rejected read exactly like a
    # clean run.
    import ads_client as _ac
    bid_ok = pause_ok = 0
    if bid_updates:
        res = client.update_target_bids(bid_updates)
        bid_ok = len(_ac.certain_ids(res, [b["targetId"] for b in bid_updates]))
    if pause_ids:
        res = client.set_targets_state(pause_ids, "PAUSED")
        pause_ok = len(_ac.certain_ids(res, pause_ids))
    print(f"  clauses set: {bid_ok}/{len(bid_updates)} bids (close {cb['close-match']}/loose {cb['loose-match']}/"
          f"subs {cb['substitutes']}) · {pause_ok}/{len(pause_ids)} complements paused")
    if bid_ok < len(bid_updates) or pause_ok < len(pause_ids):
        print(f"  ⚠️ Amazon did not confirm every clause write "
              f"({len(bid_updates) - bid_ok} bid(s), {len(pause_ids) - pause_ok} pause(s)).")
    if pending:
        print(f"  ⚠️ {len(pending)} ad groups' clauses not ready yet — phase3 will still tune them, "
              f"but re-run lottery_build to pause their complements.")


def main():
    args = sys.argv[1:]
    if markets.is_kdp():
        raise SystemExit(
            f"lottery_build is Merch-only and refuses a KDP profile "
            f"(ADS_MARKET={markets.current()}, kind=kdp). KDP campaigns are built by "
            f"kdp_build.py. This guard exists because a KDP run once read the shared US "
            f"t-shirt export and created bogus 'LOTTO - N' campaigns under the books "
            f"advertising profile (Aug 2026).")
    conn = db.connect()
    client = AdsClient()
    # A campaign builder enumerates the catalogue instead of comparing a
    # metric to a threshold, so an ordinary night is thousands of entities.
    # Without this it counts against the 500-change cap, which stopped US
    # scavenger_build at 475 of about 700 product ads on 2026-08-24.
    client.declare_campaign_builder()
    write = "--apply" in args
    cap = per_campaign_cap()

    cohort = load_tees(_opt(args, "--export"))          # [(asin, title, created)]
    scope_file = _opt(args, "--asins-file")
    if scope_file:
        scope = load_scope(scope_file)
        cohort = [(a, t, c) for a, t, c in cohort if a in scope]
        print(f"** --asins-file: cohort scoped to {len(cohort)} of {len(scope)} requested ASINs **")
    elif markets.is_default():
        # US unscoped (nightly) run: only recent uploads — the full US catalog is
        # far too large, and the pre-engine backlog stays out by design.
        cutoff = (datetime.date.today() - datetime.timedelta(days=US_NIGHTLY_DAYS)).isoformat()
        before = len(cohort)
        cohort = [(a, t, c) for a, t, c in cohort if c >= cutoff]
        print(f"US window: {len(cohort)} tees uploaded in the last {US_NIGHTLY_DAYS} days "
              f"(of {before} live)")
    if not cohort:
        print("No live standard t-shirts for this market."); return
    if "--limit" in args:
        i = args.index("--limit")
        lim = int(args[i + 1]) if i + 1 < len(args) else 5
        cohort = cohort[:lim]
        print(f"** --limit {lim}: building only the top {len(cohort)} tees (sample test) **")

    # live inventory of numbered campaigns -> global dedup + fill plan
    inventory = lotto_inventory(client)
    already = set().union(*(v["asins"] for v in inventory.values())) if inventory else set()
    new = [(a, t) for a, t, _ in cohort if a not in already]
    plan = fill_plan(inventory, new, cap)

    print(f"LOTTERY build [{client.market}] — cohort {len(cohort)} | already placed "
          f"{len(cohort) - len(new)} | to add {len(new)} | cap {cap}/campaign")
    for num, camp in sorted(inventory.items()):
        print(f"  {camp['name']:12} {camp['count']:5}/{cap}"
              + ("  (over cap — treated as full)" if camp["count"] > cap else ""))
    for num, exists, take in plan:
        label = inventory[num]["name"] if exists else f"{numbered_name(num)} (NEW)"
        print(f"  → {label}: +{len(take)}")
    if not new:
        print("  everything already placed."); return

    if not write:
        print("\nPREVIEW ONLY. Re-run with --apply."); return
    killswitch.check()
    if "--auto" not in args and not confirm(len(plan)):
        print("Cancelled."); return

    today = datetime.date.today().isoformat()
    total = 0
    for num, exists, take in plan:
        if exists:
            cid, name = inventory[num]["cid"], inventory[num]["name"]
        else:
            name = numbered_name(num)
            st, js = client.create_campaigns([{
                "name": name, "budget": lottery.DEFAULT_BUDGET, "startDate": today,
                "strategy": lottery.BIDDING_STRATEGY, "targetingType": lottery.TARGETING_TYPE}])
            ids = success_ids(js, "campaigns", "campaignId")
            if not ids:
                print(f"  ⚠️ {name}: campaign create failed: {str(js)[:300]}")
                continue
            cid = ids[0]
            print(f"[{name}] campaign created (HTTP {st}, AUTO targeting, "
                  f"${lottery.DEFAULT_BUDGET:.0f}/day).")
            db.log_write(conn, "lotto_create_campaign", "campaign", cid, name, "", "submitted")
        print(f"[{name}] adding {len(take)} ASINs…")
        total += add_asins(client, conn, cid, name, take)

    # enforce the config budget on the engine's own (EU 'LOTTO - ') campaigns only —
    # the US 'Lotto N' budgets are the operator's, hand-tuned, and never touched.
    fixes = [{"campaignId": v["cid"], "budget": lottery.DEFAULT_BUDGET}
             for v in inventory.values()
             if lottery.is_lottery(v["name"])
             and abs((v.get("budget") or 0) - lottery.DEFAULT_BUDGET) >= 0.01]
    if fixes:
        import ads_client as _ac
        res = client.update_campaign_budgets(fixes)
        ok = len(_ac.certain_ids(res, [f["campaignId"] for f in fixes]))
        print(f"  budget normalized on {ok}/{len(fixes)} LOTTO campaigns "
              f"-> ${lottery.DEFAULT_BUDGET}/day"
              + ("  ⚠️ some were NOT confirmed" if ok < len(fixes) else ""))

    # ASINs actually ADVERTISING, not ad groups created. An ad group whose
    # product ad failed advertises nothing, and the next run's inventory reads
    # ad-group names, so it would never come back for that ASIN.
    print(f"\nlottery totals: +{total} ASINs now advertising across {len(plan)} campaign(s)")
    stalled = sorted({inventory[n]["name"] for n, exists, take in plan
                      if exists and take and n in inventory
                      and (inventory[n].get("state") or "") != "ENABLED"})
    if stalled:
        print(f"\n** ad groups were added to campaign(s) that are NOT enabled and "
              f"will not serve: {', '.join(stalled)} **")
    print("Done. Lottery campaigns live — phase2/phase3/harvest manage them daily.")


if __name__ == "__main__":
    main()
