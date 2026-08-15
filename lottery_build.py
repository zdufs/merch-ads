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
import re
import sys
import time

import db
import killswitch
import lottery
import markets
from ads_client import AdsClient, success_ids

csv.field_size_limit(10**9)
POD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SP_CAMP = "application/vnd.spCampaign.v3+json"
SP_AG = "application/vnd.spAdGroup.v3+json"


def newest_export():
    matches = sorted(glob.glob(os.path.join(POD, "export_products_*.csv")))
    if not matches:
        raise SystemExit(f"No export_products_*.csv in {POD}")
    return matches[-1]


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
    xm = markets.cfg()["export_mkt"]
    rows, seen = [], set()
    with open(export_path or newest_export(), newline="", encoding="utf-8", errors="replace") as fh:
        for p in csv.DictReader(fh):
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
                          "asins": asins, "count": len(asins), "budget": budget}
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
    """Add ad-group-per-ASIN (+product ad, +clause bids) to one campaign."""
    dbid = lottery.clause_bids()["close-match"]
    ag_added = pa_added = 0
    new_agids = set()
    for i in range(0, len(pairs), 100):
        batch = pairs[i:i + 100]
        ag_items = [{"name": a, "campaignId": cid, "defaultBid": dbid} for a, _ in batch]
        st, js = client.create_ad_groups(ag_items)
        made = success_ids(js, "adGroups", "adGroupId")   # {index: adGroupId}
        ag_added += len(made)
        new_agids.update(str(made[idx]) for idx in made)
        pa_items = [{"campaignId": cid, "adGroupId": made[idx], "asin": batch[idx][0]}
                    for idx in made]
        if pa_items:
            st2, js2 = client.create_product_ads(pa_items)
            pa_added += len(success_ids(js2, "productAds", "adId"))
        time.sleep(1)   # gentle pacing
    print(f"  ad groups added: {ag_added}/{len(pairs)} | product ads: {pa_added}")
    db.log_write(conn, "lotto_add_asins", "campaign", cid, f"{ag_added} ASINs", "", "submitted")
    if new_agids:
        set_clause_bids(client, conn, cid, new_agids)
    return ag_added


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
    if bid_updates:
        client.update_target_bids(bid_updates)
    if pause_ids:
        client.set_targets_state(pause_ids, "PAUSED")
    print(f"  clauses set: {len(bid_updates)} bids (close {cb['close-match']}/loose {cb['loose-match']}/"
          f"subs {cb['substitutes']}) · {len(pause_ids)} complements paused")
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
        client.update_campaign_budgets(fixes)
        print(f"  budget normalized on {len(fixes)} LOTTO campaigns -> ${lottery.DEFAULT_BUDGET}/day")

    print(f"\nlottery totals: +{total} ASIN ad groups across {len(plan)} campaign(s)")
    print("Done. Lottery campaigns live — phase2/phase3/harvest manage them daily.")


if __name__ == "__main__":
    main()
