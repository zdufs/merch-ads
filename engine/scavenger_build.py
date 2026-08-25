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
import json
import os

import paths
import re
import sys
import time
from collections import Counter

import db
import export_reader
import killswitch
import markets
import scavenger
from ads_client import AdsClient, report_refused, success_ids

csv.field_size_limit(10**9)
HERE = paths.REPO_ROOT
# The catalogue folder is paths.POD_ROOT, never dirname(REPO_ROOT).
# The two agree only while MERCHADS_POD_DIR is unset. Set it, and half the
# engine reads one catalogue while products.export_signature() — which does use
# POD_ROOT — banks the signature of another, so the economics gate certifies a
# catalogue that was never mapped.
POD = paths.POD_ROOT
SP_CAMP = "application/vnd.spCampaign.v3+json"
SP_AG = "application/vnd.spAdGroup.v3+json"

STOPWORDS = set("""a an the and or for of to in on with by your you my our this that
shirt tshirt t-shirt tee tees gift gifts funny cool cute vintage retro design men
women mens womens kids boys girls graphic print premium classic best love lover
day gifts idea ideas humor saying quote quotes top apparel""".split())


def newest_export():
    """The newest product-grid export. Callers pass a specific file when the app
    scopes a build to one intake export."""
    import export_reader
    path = export_reader.newest_catalog_file(POD)
    if not path:
        raise SystemExit(f"No product export in {POD}")
    return path


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


def load_build_specs(path=None, scope=None, skipped=None):
    """Ordered list of build specs {series, asins:[(asin,title)]}.
       `path` reads ONE named export (the app scoping a build to an intake file);
       None reads the merged catalog.
       Cohorts with `source_kw` + a matching POD file load their ad-safe ASINs from it;
       all others come from one pass over the catalog (adAsins if present, else
       retail ASIN). Plus a tees-only 'New Uploads' series (uploaded this year, 0 sales).
       `scope` (a set of ad-eligible ASINs, from the app's new-design intake) restricts
       every series to those ASINs — and lets scoped 0-sale designs into their typed
       series, since intake is an explicit request to advertise them.

       `skipped` is an optional dict this fills with {series: count} for listings a
       cohort claimed but that CANNOT be advertised: a hardgood type with no ad-safe
       ASIN (`scavenger.needs_ad_safe_asin`). It is an out-parameter rather than a
       second return value so every existing caller keeps working, and it exists so
       the skip reaches `write_coverage` — a design dropped here is coverage the
       account genuinely loses, and this module's whole job since 2026-08-22 is to
       state a gap rather than let a build read Complete."""
    if skipped is None:
        skipped = {}
    # Dedicated ad-safe files (e.g. the drinkware export) are US-specific for now,
    # so other markets always fall back to main-export adAsins.
    #
    # A dedicated file SUPPLEMENTS the main export. It must never REPLACE it.
    # Replacing it is how 723 new US drinkware designs were dropped in silence on
    # 2026-08-22: the only file matching "tumbler" in the POD folder was two
    # months old, held none of the new ASINs, and once the scope filter ran the
    # whole Drinkware series left the plan. No error, no warning — the builder
    # simply had four cohorts instead of five, while the Import screen still read
    # "Complete · Drinkware 723". A stale file has to cost coverage it can no
    # longer supply, never coverage the fresh export has.
    sources = {}
    if markets.is_default():
        for c in scavenger.COHORTS:
            if c.get("source_kw"):
                f = find_source(c["source_kw"])
                if f:
                    sources[c["series"]] = f

    # main-export pass covers EVERY cohort, including the ones with a dedicated file
    type_series = {t: c["series"] for c in scavenger.COHORTS for t in c["types"]}
    buckets = {c["series"]: [] for c in scavenger.COHORTS}
    new_tees = []
    since = scavenger.new_uploads_since()
    cohort_mkt = scavenger.cohort_market()
    # One named file when the app scopes a build to an intake export, otherwise
    # the merged catalog (Snap for MOD exports it in 100k-row chunks).
    source = (export_reader.rows(path) if path
              else export_reader.catalog_rows(POD, marketplace=cohort_mkt))
    for p in source:
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
        listed = [a.strip().upper() for a in ad_field.split(",") if a.strip()]
        ad_asins = listed or ([p["asin"].upper()] if p.get("asin") else [])
        if not ad_asins:
            continue
        # A hardgood with no ad-safe ASIN cannot be advertised through its retail
        # ASIN — Amazon answers adEligibilityError AD_INELIGIBLE and the ad is
        # never created. Submitting it anyway is not free: because `new_asins`
        # is computed against Amazon's live product-ad list, the refusal keeps
        # the ASIN permanently "new" and it goes back every single night.
        # The skip is counted below, never silent: it is real lost coverage, and
        # a fresh export with the ad-safe column populated is what recovers it.
        no_ad_safe = not listed and scavenger.needs_ad_safe_asin(ptype)
        in_scope = bool(scope) and any(aa in scope for aa in ad_asins)
        if total > 0 or in_scope:
            series = type_series.get(ptype)
            if series:
                if no_ad_safe:
                    # Counted only once a cohort would actually have advertised
                    # it. Counting every unpriced listing in the catalogue would
                    # make this inventory rather than impact, and an alarm that
                    # is wrong by two orders of magnitude gets muted.
                    skipped[series] = skipped.get(series, 0) + 1
                    continue
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
        rows = list(buckets[c["series"]])
        if c["series"] in sources:
            rows += load_adsafe_file(sources[c["series"]])
        # One sort over BOTH sources, so the best sellers lead however they were
        # read. Sorting the dedicated file first would bury every export row
        # under its zero-sale tail and re-create the drop the merge exists to end.
        rows.sort(key=lambda r: r[2], reverse=True)
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


def chunked_create(fn, items, key, id_field, label=""):
    """Create in batches of 100, retrying any batch that gets rate-limited (429),
    with light pacing so big campaigns don't trip the write limits.

    A batch that is not fully accepted says so on STDERR, with the HTTP status
    and the first few reasons out of Amazon's error block. Both `st` and `js`
    used to go out of scope here, so a refused ASIN left a count and nothing
    else — and the count read as "everything was already advertised", which is
    the benign reading the comment beside the caller's print asserts. That is
    how the same refused ASINs were re-submitted every night for sixty nights.
    """
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
        accepted = len(success_ids(js, key, id_field))
        report_refused(key, len(batch), accepted, st, js, label=label, items=batch)
        ok += accepted
        time.sleep(1)   # gentle pacing between write batches
    return ok


def build_one(client, conn, series, n, chunk_asins, chunk_titles, write, camp_index):
    """Create/refresh one scavenger campaign.

    Returns (asins_added, kw_added, used_recs, asins_refused). `asins_refused`
    is submitted minus accepted for the PRODUCT ADS — the ASINs Amazon turned
    down. It leaves this function because the coverage report is the only place
    a screen can read it: `added` alone says 0 whether every ASIN was already
    advertised or every one of them was rejected.

    camp_index = {name: campaign} fetched once per run (avoids re-listing every campaign).
    """
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
        return len(new_asins), len(new_kw), used_recs, 0
    if camp and not new_asins and not new_kw:
        print("  up to date."); return 0, 0, used_recs, 0

    today = datetime.date.today().isoformat()
    if not camp:
        st, js = client.create_campaigns([{
            "name": name, "budget": scavenger.DEFAULT_BUDGET, "startDate": today,
            "strategy": scavenger.BIDDING_STRATEGY}])
        ids = success_ids(js, "campaigns", "campaignId")
        if not ids:
            report_refused("campaigns", 1, 0, st, js, label=name)
            print(f"  ⚠️ campaign create failed: {str(js)[:300]}"); return 0, 0, used_recs, 0
        cid = ids[0]
        print(f"  campaign created (HTTP {st}).")
        db.log_write(conn, "scav_create_campaign", "campaign", cid, name, "", "submitted")
    if agid is None:
        st, js = client.create_ad_groups([{
            "name": "scavenger", "campaignId": cid, "defaultBid": scavenger.DEFAULT_BID}])
        ag_ids = success_ids(js, "adGroups", "adGroupId")
        if not ag_ids:
            report_refused("adGroups", 1, 0, st, js, label=name)
            print(f"  ⚠️ ad group create failed: {str(js)[:300]}"); return 0, 0, used_recs, 0
        agid = ag_ids[0]
        print(f"  ad group created (HTTP {st}).")

    ads_added = kw_added = ads_refused = 0
    if new_asins:
        pa = [{"campaignId": cid, "adGroupId": agid, "asin": a} for a in new_asins]
        ads_added = chunked_create(client.create_product_ads, pa, "productAds", "adId",
                                   label=name)
        ads_refused = len(pa) - ads_added
        print(f"  product ads added: {ads_added}/{len(pa)}")
        # An ASIN already advertised here never reaches `pa` — new_asins is the
        # difference against Amazon's own live product-ad list. So anything
        # submitted and not accepted was REFUSED, and the old reading of this
        # line as a harmless no-op was wrong every time it was not zero.
        if ads_refused:
            print(f"  ** {ads_refused} of {len(pa)} product ads REFUSED by Amazon "
                  f"— reasons on stderr **")
        if ads_added:
            db.log_write(conn, "scav_add_ads", "adGroup", agid, f"{ads_added} ASINs", "", "submitted")
        else:
            print(f"  nothing created ({len(pa)} submitted) — no writes_log row")
    if new_kw:
        kw = [{"campaignId": cid, "adGroupId": agid, "keywordText": k,
               "matchType": scavenger.MATCH, "bid": scavenger.DEFAULT_BID} for k in new_kw]
        kw_added = chunked_create(client.create_keywords, kw, "keywords", "keywordId",
                                  label=name)
        print(f"  keywords added: {kw_added}/{len(kw)} (broad @ ${scavenger.DEFAULT_BID})")
        if kw_added:
            db.log_write(conn, "scav_add_kw", "adGroup", agid, f"{kw_added} broad kw [{source}]", "", "submitted")
        else:
            print(f"  nothing created ({len(kw)} keywords submitted) — no writes_log row")
    return ads_added, kw_added, used_recs, ads_refused


def shard(cohort):
    cohort = cohort[:scavenger.MAX_ASINS * scavenger.MAX_CAMPAIGNS]
    return [cohort[i:i + scavenger.MAX_ASINS] for i in range(0, len(cohort), scavenger.MAX_ASINS)]


def coverage_path():
    return os.path.join(HERE, "outputs", f"scav_build_{markets.current()}.json")


def write_coverage(scope, specs, plan, built, write, stopped=None, no_ad_safe=None):
    """Record what this build did NOT do, where the caller can read it.

    `appctl import-apply` runs this module as a subprocess and keeps only the
    last 2500 characters of its stdout, so a cohort that never entered the plan
    leaves no trace there at all. On 2026-08-22 the Import screen therefore
    showed the REQUEST — "US · Drinkware 723" — and marked the run Complete,
    while zero drinkware ads had been created. Three ways that happens, every
    one of them silent on stdout, all three reported in this file:

      - a series matched nothing, so it never reached the plan
      - shard() dropped the tail past MAX_ASINS x MAX_CAMPAIGNS
      - ads landed in a campaign that is PAUSED, so they will never serve
      - Amazon REFUSED ads that were submitted (`refused`)
      - a hardgood the cohort wanted has no ad-safe ASIN (`no_ad_safe`)

    That last one used to be invisible in a different way: the builder fell back
    to the retail ASIN, Amazon refused it, and the design was reported as
    submitted rather than as unadvertisable. Skipping it is correct — the ad
    could never have served — but a silent skip would simply move the lie, so
    the count is stated here. It is the number a fresh export with the ad-safe
    column populated would recover.

    The last one is the 2026-06-25 residue. About 873 ASINs a night across six
    markets were submitted and turned down, and because `new_asins` is computed
    against Amazon's live product-ad list a refused ad never lands there, so the
    same ASINs went back every night for sixty nights. Nothing counted them:
    `added` was the only number recorded, and a night that added nothing looked
    exactly like a night with nothing to add.

    `stopped` is a fourth: the build died part-way. On 2026-08-24 two markets
    did — US when the new write cap refused a batch, DE when the app bundle was
    replaced underneath the running nightly and every TLS call failed. Neither
    reached this function, so the file on disk still described YESTERDAY's
    successful build, and only its `as_of` said otherwise. A report that is a
    day old reads exactly like this run's.

    Written on preview runs too, so the app can warn before anything is built.
    """
    matched = {s["series"]: len(s["asins"]) for s in specs}
    rows = {}
    for series, chunks in plan:
        rows[series] = {"series": series, "matched": matched.get(series, 0),
                        "planned": sum(len(c) for c in chunks),
                        "added": 0, "refused": 0,
                        "campaigns": [], "paused_campaigns": []}
    for b in built:
        r = rows.setdefault(b["series"], {"series": b["series"], "matched": 0,
                                          "planned": 0, "added": 0, "refused": 0,
                                          "campaigns": [], "paused_campaigns": []})
        r["added"] += b["added"]
        r["refused"] += b.get("refused", 0)
        r["campaigns"].append({"name": b["campaign"], "state": b["state"],
                               "added": b["added"], "refused": b.get("refused", 0)})
        if b["state"] == "PAUSED" and b["added"]:
            r["paused_campaigns"].append(b["campaign"])

    planned_asins = {a for _, chunks in plan for ch in chunks for a, _ in ch}
    unplanned = sorted(scope - planned_asins) if scope else []
    no_ad_safe = {s: n for s, n in (no_ad_safe or {}).items() if n}
    for r in rows.values():
        r["over_cap"] = max(0, r["matched"] - r["planned"])
        r["no_ad_safe"] = no_ad_safe.get(r["series"], 0)

    report = {
        "market": markets.current(),
        "as_of": datetime.datetime.now().isoformat(timespec="seconds"),
        "applied": bool(write),
        "stopped": stopped,
        "scoped": len(scope) if scope else None,
        "planned": len(planned_asins),
        "unplanned": len(unplanned),
        "unplanned_sample": unplanned[:50],
        # Product ads submitted and turned down by Amazon. Reasons go to stderr
        # and into outputs/scheduled_runs.log; this is the count a screen reads.
        "refused": sum(r["refused"] for r in rows.values()),
        # Hardgoods a cohort wanted and cannot advertise: no ad-safe ASIN in the
        # export. Kept as a per-series map AND a total, because a series that is
        # skipped down to nothing never reaches `series` below at all, and a gap
        # that disappears when it grows total is the worst shape to report.
        "no_ad_safe": sum(no_ad_safe.values()),
        "no_ad_safe_series": no_ad_safe,
        "paused_campaigns": sorted({c for r in rows.values()
                                    for c in r["paused_campaigns"]}),
        "series": [rows[s["series"]] for s in specs if s["series"] in rows],
    }
    os.makedirs(os.path.dirname(coverage_path()), exist_ok=True)
    with open(coverage_path(), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
    if unplanned:
        print(f"\n** {len(unplanned)} of {len(scope)} scoped ASINs matched NO cohort "
              f"and were not built ** (see {os.path.basename(coverage_path())})")
    if report["refused"]:
        print(f"\n** Amazon REFUSED {report['refused']} product ad(s) this build "
              f"** (reasons on stderr; counts in {os.path.basename(coverage_path())})")
    if report["no_ad_safe"]:
        detail = ", ".join(f"{s} {n}" for s, n in sorted(no_ad_safe.items()))
        print(f"\n** {report['no_ad_safe']} hardgood design(s) have NO ad-safe ASIN "
              f"and cannot be advertised: {detail} ** "
              f"(export the ad-safe ASIN column to recover them)")
    if report["paused_campaigns"]:
        print(f"** ads were added to PAUSED campaign(s) and will not serve: "
              f"{', '.join(report['paused_campaigns'])} **")
    if stopped:
        print(f"** this build STOPPED part-way and is incomplete: {stopped} **")
    return report


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
    # A campaign builder enumerates the catalogue instead of comparing a
    # metric to a threshold, so an ordinary night is thousands of entities.
    # Without this it counts against the 500-change cap, which stopped US
    # scavenger_build at 475 of about 700 product ads on 2026-08-24.
    client.declare_campaign_builder()
    write = "--apply" in args

    scope = None
    scope_file = _opt(args, "--asins-file")
    if scope_file:
        with open(scope_file, encoding="utf-8") as fh:
            scope = {line.strip().upper() for line in fh if line.strip()}
        print(f"** --asins-file: build scoped to {len(scope)} intake ASINs **")
    no_ad_safe = {}
    specs = load_build_specs(_opt(args, "--export"), scope, skipped=no_ad_safe)
    plan = [(s["series"], shard(s["asins"])) for s in specs]   # [(series, chunks)]
    if not specs:
        print("No cohort ASINs found in the export.")
        write_coverage(scope, specs, plan, [], write, no_ad_safe=no_ad_safe)
        return

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

    built = []
    # The try opens HERE, at the first call that touches Amazon, not at the
    # build loop. Listing the campaigns is a TLS call like any other, and on
    # 2026-08-24 the whole class of failure was TLS: the bundle was replaced
    # under the running nightly and `certifi/cacert.pem` stopped existing. A
    # build that dies on this line has still done nothing AND still overwritten
    # nothing — the report it leaves says exactly that.
    try:
        # fetch the campaign list ONCE (not per campaign) -> far fewer API calls
        camp_index = {c.get("name"): c
                      for c in client.list_all("/sp/campaigns/list", SP_CAMP, "campaigns")}

        # flat list so we can space EVERY recommendation call (across both cohorts)
        jobs = [(series, n, chunk) for series, chunks in plan
                for n, chunk in enumerate(chunks, start=1)]
        _build_all(client, conn, jobs, camp_index, write, built)
    except BaseException as e:
        # Record what was built BEFORE re-raising. Everything below this line —
        # the totals, the coverage report — used to be skipped entirely when a
        # build died part-way, which left the previous run's report on disk
        # where a reader takes it for this one's.
        _record_stop(scope, specs, plan, built, write, e, no_ad_safe)
        raise
    tot_ads = sum(b["added"] for b in built)
    tot_kw = sum(b["kw_added"] for b in built)

    tag = " (preview)" if not write else ""
    print(f"\nscavenger totals{tag}: +{tot_ads} ASINs, +{tot_kw} keywords across {total_camps} campaign(s)")
    write_coverage(scope, specs, plan, built, write, no_ad_safe=no_ad_safe)
    if not write:
        print("PREVIEW ONLY. Re-run with --apply."); return
    print("Done. Scavenger campaigns live — scavenger_optimize.py prunes wasteful keywords.")


def _record_stop(scope, specs, plan, built, write, exc, no_ad_safe=None):
    """Write the coverage report for a build that is about to raise.

    It must not become the failure itself. Writing the report can fail on its
    own — a full disk, a data folder that is not there — and if it raised from
    inside the `except` it would REPLACE the real error, so the log would name
    a permissions problem where a write cap or a missing CA bundle actually
    stopped the build.
    """
    try:
        write_coverage(scope, specs, plan, built, write,
                       stopped=f"{type(exc).__name__}: {exc}".strip()
                               or type(exc).__name__,
                       no_ad_safe=no_ad_safe)
    except Exception as e:
        print(f"  could not write the coverage report ({type(e).__name__}: {e}); "
              f"the build's own failure follows", file=sys.stderr)


def _build_all(client, conn, jobs, camp_index, write, built):
    """Run every planned campaign, appending each result to `built` as it lands.

    `built` is an argument rather than a return value on purpose: the caller
    needs what was finished even when this raises part-way through.
    """
    for i, (series, n, chunk) in enumerate(jobs):
        cname = scavenger.camp_name(n, series)
        # camp_index was fetched before the loop, so "not in it" IS "created now".
        # The state matters: build_one reuses a campaign by NAME whatever its
        # state, and on 2026-08-22 that put 446 new US hat ads into
        # "SCAVENGER - Hats 1", PAUSED since June. They were created, counted and
        # reported as built, and not one of them can ever serve.
        prior = camp_index.get(cname)
        state = (prior.get("state") or "UNKNOWN") if prior else "NEW"
        a, k, used_recs, refused = build_one(
            client, conn, series, n,
            [x for x, _ in chunk], [t for _, t in chunk], write, camp_index)
        built.append({"series": series, "campaign": cname, "state": state,
                      "added": a, "kw_added": k, "refused": refused})
        # only pause when a recommendation call was actually made (full campaigns skip it)
        if used_recs and i < len(jobs) - 1:
            time.sleep(scavenger.REC_DELAY_SEC)


if __name__ == "__main__":
    main()
