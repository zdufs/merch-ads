#!/usr/bin/env python3
"""
SCAVENGER builder — create/refresh the cheap-clicks scavenger campaign.

What it does (per the playbook's "scavenger campaign" method):
  1. Pulls the cohort from the Merch export: live US standard tees with >=1 sale.
  2. Creates a single campaign (manual, dynamic bids UP & DOWN, $5/day) with ONE
     ad group holding up to 1000 of those ASINs as product ads.
  3. Adds ~200 BROAD keywords at a 5c bid, sourced from Amazon's keyword
     recommendations (falls back to keywords derived from your product titles if
     the recommendations endpoint returns nothing).
  4. Idempotent: re-running adds only NEW ASINs / NEW keywords (always at 5c).

SAFETY: preview by default; writes only with --apply + typed APPLY (or --auto for
the scheduled job); honors the KILL switch; every write logged to writes_log.

Usage:
  python3 scavenger_build.py                 # preview
  python3 scavenger_build.py --apply         # build/refresh (typed APPLY)
  python3 scavenger_build.py --apply --auto  # scheduled (no prompt)
"""

import csv
import datetime
import glob
import os
import re
import sys
import time
from collections import Counter

import db
import killswitch
import markets
import scavenger
from ads_client import AdsClient, success_ids

csv.field_size_limit(10**9)
HERE = os.path.dirname(os.path.abspath(__file__))
POD = os.path.dirname(HERE)
SP_CAMP = "application/vnd.spCampaign.v3+json"
SP_AG = "application/vnd.spAdGroup.v3+json"

STOPWORDS = set("""a an the and or for of to in on with by your you my our this that
shirt tshirt t-shirt tee tees gift gifts funny cool cute vintage retro design men
women mens womens kids boys girls graphic print premium classic best love lover
day gifts idea ideas humor saying quote quotes top apparel""".split())


def newest_export():
    matches = sorted(glob.glob(os.path.join(POD, "export_products_*.csv")))
    if not matches:
        raise SystemExit(f"No export_products_*.csv in {POD}")
    return matches[-1]


def find_source(keyword):
    """Newest POD csv whose filename contains `keyword` (a dedicated ad-safe export)."""
    cands = [p for p in glob.glob(os.path.join(POD, "*.csv"))
             if keyword.lower() in os.path.basename(p).lower()]
    return sorted(cands)[-1] if cands else None


def load_adsafe_file(path):
    """A MerchFlow export with an 'ASIN (Ad-Safe)' column -> [(asin, title, total)]
    for ALL live US designs (every sales tier), strongest sellers first so the
    per-cohort campaign cap keeps proven winners before unsold discovery stock."""
    out = []
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        for r in csv.DictReader(fh):
            if (r.get("Market") or "").strip() != "US" or (r.get("Status") or "").strip() != "Live":
                continue
            asin = (r.get("ASIN (Ad-Safe)") or "").strip().upper()
            if not asin:
                continue
            try:
                total = int(float((r.get("Sales Total") or "0").strip() or 0))
            except (TypeError, ValueError):
                total = 0
            out.append((asin, (r.get("Title") or "").strip(), total))
    out.sort(key=lambda r: r[2], reverse=True)
    return out


def load_build_specs(path, scope=None):
    """Ordered list of build specs {series, asins:[(asin,title)]}.
       Cohorts with `source_kw` + a matching POD file load their ad-safe ASINs from it;
       all others come from one pass over the main export (adAsins if present, else
       retail ASIN). Plus a tees-only 'New Uploads' series (uploaded this year, 0 sales).
       `scope` (a set of ad-eligible ASINs, from the app's new-design intake) restricts
       every series to those ASINs — and lets scoped 0-sale designs into their typed
       series, since intake is an explicit request to advertise them."""
    # dedicated ad-safe files (e.g. the drinkware export) are US-specific for now,
    # so only use them on the US market; other markets fall back to main-export adAsins.
    sources = {}
    if markets.is_default():
        for c in scavenger.COHORTS:
            if c.get("source_kw"):
                f = find_source(c["source_kw"])
                if f:
                    sources[c["series"]] = f

    # main-export pass covers every cohort NOT loaded from a dedicated file
    type_series = {t: c["series"] for c in scavenger.COHORTS
                   if c["series"] not in sources for t in c["types"]}
    buckets = {c["series"]: [] for c in scavenger.COHORTS if c["series"] not in sources}
    new_tees = []
    since = scavenger.new_uploads_since()
    cohort_mkt = scavenger.cohort_market()
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        for p in csv.DictReader(fh):
            if (p.get("marketplace") != cohort_mkt
                    or p.get("status") != "published" or not p.get("asin")):
                continue
            ptype = p.get("productType") or ""
            try:
                total = int(float(p.get("salesTotal") or 0))
            except (TypeError, ValueError):
                total = 0
            title = p.get("productTitle") or ""
            # advertise the AD-ELIGIBLE ASIN(s): adAsins if present (hardgoods),
            # else the retail ASIN (apparel). adAsins may be comma-separated variants.
            ad_field = p.get("adAsins") or ""
            ad_asins = [a.strip().upper() for a in ad_field.split(",") if a.strip()] \
                or ([p["asin"].upper()] if p.get("asin") else [])
            if not ad_asins:
                continue
            in_scope = bool(scope) and any(aa in scope for aa in ad_asins)
            if total > 0 or in_scope:
                series = type_series.get(ptype)
                if series:
                    for aa in ad_asins:
                        buckets[series].append((aa, title, total))
            elif ptype == scavenger.COHORT_TYPE and (p.get("createdDate") or "") >= since:
                for aa in ad_asins:
                    new_tees.append((aa, title, p.get("createdDate") or ""))

    def dedup(rows):
        """rows sorted by total desc -> unique (asin,title), keeping the best instance."""
        seen, out = set(), []
        for a, t, _ in rows:
            if a not in seen:
                seen.add(a); out.append((a, t))
        return out

    specs = []
    for c in scavenger.COHORTS:                       # keep COHORTS order
        if c["series"] in sources:
            rows = load_adsafe_file(sources[c["series"]])
        else:
            rows = sorted(buckets[c["series"]], key=lambda r: r[2], reverse=True)
        asins = dedup(rows)
        if scope is not None:
            asins = [(a, t) for a, t in asins if a in scope]
        if asins:
            specs.append({"series": c["series"], "asins": asins})
    new_tees.sort(key=lambda r: r[2], reverse=True)    # newest uploads first
    nt = dedup(new_tees)
    if scope is not None:
        nt = [(a, t) for a, t in nt if a in scope]
    if nt:
        specs.append({"series": scavenger.NEW_SERIES, "asins": nt})
    return specs


def title_keywords(titles, limit):
    """Fallback keyword source: frequent words + bigrams from product titles."""
    words = Counter()
    bigrams = Counter()
    for t in titles:
        toks = [w for w in re.sub(r"[^a-z0-9 ]", " ", t.lower()).split()
                if len(w) >= 3 and w not in STOPWORDS]
        words.update(toks)
        bigrams.update(f"{toks[i]} {toks[i+1]}" for i in range(len(toks) - 1))
    out = []
    for kw, _ in bigrams.most_common():
        if len(out) >= limit:
            break
        out.append(kw)
    for kw, _ in words.most_common():
        if len(out) >= limit:
            break
        if kw not in out:
            out.append(kw)
    return out[:limit]


def find_campaign(client, name):
    for c in client.list_all("/sp/campaigns/list", SP_CAMP, "campaigns"):
        if c.get("name") == name:
            return c
    return None


def ad_groups_of(client, campaign_id):
    return client.list_all("/sp/adGroups/list", SP_AG, "adGroups",
                           extra_body={"campaignIdFilter": {"include": [str(campaign_id)]}})


def confirm(ncamps):
    return input(f"\nType APPLY to build/refresh {ncamps} scavenger campaign(s) (anything else cancels): ").strip() == "APPLY"


def chunked_create(fn, items, key, id_field):
    """Create in batches of 100, retrying any batch that gets rate-limited (429),
    with light pacing so big campaigns don't trip the write limits."""
    ok = 0
    for i in range(0, len(items), 100):
        batch = items[i:i + 100]
        st, js = fn(batch)
        for attempt in range(2):
            if st != 429:
                break
            wait = 20 * (attempt + 1)
            print(f"  {key} HTTP 429 — backing off {wait}s")
            time.sleep(wait)
            st, js = fn(batch)
        ok += len(success_ids(js, key, id_field))
        time.sleep(1)   # gentle pacing between write batches
    return ok


def build_one(client, conn, series, n, chunk_asins, chunk_titles, write, camp_index):
    """Create/refresh one scavenger campaign. Returns (asins_added, kw_added, used_recs).
    camp_index = {name: campaign} fetched once per run (avoids re-listing every campaign)."""
    name = scavenger.camp_name(n, series)
    camp = camp_index.get(name)
    cid = camp.get("campaignId") if camp else None
    existing_asins, existing_kw, agid = set(), set(), None
    if camp:
        ags = ad_groups_of(client, cid)
        if ags:
            agid = ags[0].get("adGroupId")
            existing_asins = {a.get("asin", "").upper()
                              for a in client.list_product_ads([agid]) if a.get("asin")}
            existing_kw = {(k.get("keywordText") or "").lower()
                           for k in client.list_keywords([cid])}

    new_asins = [a for a in chunk_asins if a not in existing_asins]

    # only fetch/add keywords when the campaign is UNDER the cap. Once it's full
    # we make no recommendation call at all -> no rate limits, no keyword bloat.
    need_kw = max(0, scavenger.MAX_KEYWORDS - len(existing_kw))
    new_kw, source, used_recs = [], "full", False
    if need_kw > 0:
        used_recs = True
        suggested = client.get_keyword_recommendations(chunk_asins[:100], scavenger.MAX_KEYWORDS)
        source = "Amazon suggestions"
        if not suggested:
            suggested = title_keywords(chunk_titles, scavenger.MAX_KEYWORDS)
            source = "title fallback"
        new_kw = [k for k in suggested if k not in existing_kw][:need_kw]

    print(f"\n[{name}]  cohort {len(chunk_asins)} | exists: {'yes' if camp else 'NO'} "
          f"| {len(existing_kw)} kw | +{len(new_asins)} ASINs | +{len(new_kw)} kw [{source}]")
    if not write:
        return len(new_asins), len(new_kw), used_recs
    if camp and not new_asins and not new_kw:
        print("  up to date."); return 0, 0, used_recs

    today = datetime.date.today().isoformat()
    if not camp:
        st, js = client.create_campaigns([{
            "name": name, "budget": scavenger.DEFAULT_BUDGET, "startDate": today,
            "strategy": scavenger.BIDDING_STRATEGY}])
        ids = success_ids(js, "campaigns", "campaignId")
        if not ids:
            print(f"  ⚠️ campaign create failed: {str(js)[:300]}"); return 0, 0, used_recs
        cid = ids[0]
        print(f"  campaign created (HTTP {st}).")
        db.log_write(conn, "scav_create_campaign", "campaign", cid, name, "", "submitted")
    if agid is None:
        st, js = client.create_ad_groups([{
            "name": "scavenger", "campaignId": cid, "defaultBid": scavenger.DEFAULT_BID}])
        ag_ids = success_ids(js, "adGroups", "adGroupId")
        if not ag_ids:
            print(f"  ⚠️ ad group create failed: {str(js)[:300]}"); return 0, 0, used_recs
        agid = ag_ids[0]
        print(f"  ad group created (HTTP {st}).")

    ads_added = kw_added = 0
    if new_asins:
        pa = [{"campaignId": cid, "adGroupId": agid, "asin": a} for a in new_asins]
        ads_added = chunked_create(client.create_product_ads, pa, "productAds", "adId")
        print(f"  product ads added: {ads_added}/{len(pa)}")
        # Amazon accepts nothing when every ASIN is already advertised here. That
        # is a no-op, not a write — logging it every night buries the real bid /
        # pause / negative rows in the audit trail. Log only what actually landed.
        if ads_added:
            db.log_write(conn, "scav_add_ads", "adGroup", agid, f"{ads_added} ASINs", "", "submitted")
        else:
            print(f"  nothing created ({len(pa)} submitted) — no writes_log row")
    if new_kw:
        kw = [{"campaignId": cid, "adGroupId": agid, "keywordText": k,
               "matchType": scavenger.MATCH, "bid": scavenger.DEFAULT_BID} for k in new_kw]
        kw_added = chunked_create(client.create_keywords, kw, "keywords", "keywordId")
        print(f"  keywords added: {kw_added}/{len(kw)} (broad @ ${scavenger.DEFAULT_BID})")
        if kw_added:
            db.log_write(conn, "scav_add_kw", "adGroup", agid, f"{kw_added} broad kw [{source}]", "", "submitted")
        else:
            print(f"  nothing created ({len(kw)} keywords submitted) — no writes_log row")
    return ads_added, kw_added, used_recs


def shard(cohort):
    cohort = cohort[:scavenger.MAX_ASINS * scavenger.MAX_CAMPAIGNS]
    return [cohort[i:i + scavenger.MAX_ASINS] for i in range(0, len(cohort), scavenger.MAX_ASINS)]


def _opt(args, name):
    """Value of `--name VALUE` in args, else None."""
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1]
    return None


def main():
    args = sys.argv[1:]
    if markets.is_kdp():
        raise SystemExit(
            f"scavenger_build is Merch-only and refuses a KDP profile "
            f"(ADS_MARKET={markets.current()}, kind=kdp). KDP campaigns are built by "
            f"kdp_build.py. Same root cause as the lottery_build guard: a KDP run must "
            f"never build t-shirt campaigns under the books advertising profile.")
    conn = db.connect()
    client = AdsClient()
    write = "--apply" in args

    scope = None
    scope_file = _opt(args, "--asins-file")
    if scope_file:
        with open(scope_file, encoding="utf-8") as fh:
            scope = {line.strip().upper() for line in fh if line.strip()}
        print(f"** --asins-file: build scoped to {len(scope)} intake ASINs **")
    specs = load_build_specs(_opt(args, "--export") or newest_export(), scope)
    if not specs:
        print("No cohort ASINs found in the export."); return
    plan = [(s["series"], shard(s["asins"])) for s in specs]   # [(series, chunks)]

    total_camps = sum(len(ch) for _, ch in plan)
    print(f"SCAVENGER build — ${scavenger.DEFAULT_BUDGET * total_camps:.0f}/day across "
          f"{total_camps} campaign(s):")
    for series, chunks in plan:
        n_asins = sum(len(c) for c in chunks)
        print(f"  • {series}: {n_asins} ASINs / {len(chunks)} campaign(s)")

    if write:
        killswitch.check()
        if "--auto" not in args and not confirm(total_camps):
            print("Cancelled."); return

    # fetch the campaign list ONCE (not per campaign) -> far fewer API calls
    camp_index = {c.get("name"): c
                  for c in client.list_all("/sp/campaigns/list", SP_CAMP, "campaigns")}

    # flat list so we can space EVERY recommendation call (across both cohorts)
    jobs = [(series, n, chunk) for series, chunks in plan
            for n, chunk in enumerate(chunks, start=1)]
    tot_ads = tot_kw = 0
    for i, (series, n, chunk) in enumerate(jobs):
        a, k, used_recs = build_one(client, conn, series, n,
                                    [x for x, _ in chunk], [t for _, t in chunk], write, camp_index)
        tot_ads += a; tot_kw += k
        # only pause when a recommendation call was actually made (full campaigns skip it)
        if used_recs and i < len(jobs) - 1:
            time.sleep(scavenger.REC_DELAY_SEC)

    tag = " (preview)" if not write else ""
    print(f"\nscavenger totals{tag}: +{tot_ads} ASINs, +{tot_kw} keywords across {total_camps} campaign(s)")
    if not write:
        print("PREVIEW ONLY. Re-run with --apply."); return
    print("Done. Scavenger campaigns live — scavenger_optimize.py prunes wasteful keywords.")


if __name__ == "__main__":
    main()
