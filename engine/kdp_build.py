#!/usr/bin/env python3
"""KDP Sponsored Products campaign builder — composes the day-one ad campaigns
for the book catalog from a per-book manifest, preview-first.

Mirrors the Merch builders (lottery_build.py / scavenger_build.py): PREVIEW by
default, `--apply` routes every write through ads_client (KILL-gated, econ-gated,
bid/budget ceilings clamp). This builder is KDP-only — it refuses to run unless
the market is USKDP, because books are single ASINs with Sponsored Products only
and dynamic-down-only bidding, none of which matches the tee cohorts.

Two campaign shapes, per docs/KDP_ADS_PLAN (the kdp-factory plan):

  LEAD  (one per entry point) — the manual-keyword campaign, named
        "<Title> - EXACT" to match the app's Merch naming so reports stay
        consistent. One ad group, one product ad (the ebook ASIN), the book's
        keyword slots as keywords, plus the day-one negatives.

  SERIES AD (one per fiction series) — "<Series> - SERIES". One ad group, a
        product ad for EVERY book ASIN in the series, AUTO targeting so Amazon
        serves whichever book best matches the query (the "picks the entry
        point" behavior). Plus the day-one negatives.

Scope is breadth-first: 11 entry-point leads + 6 series ads = 17 budgets. The
corvino opener is HELD OUT of the direct-ad set (its price makes break-even too
tight for a first-book direct ad) but corvino still gets its series ad.

The PREVIEW composes the exact payloads it WOULD send and prints them as JSON —
nothing reaches Amazon. The live create (`--apply`) is operator-run.
"""

import argparse
import datetime
import json
import os

import paths
import sys

import markets

HERE = paths.REPO_ROOT

# --- settings, all from the plan's section 4 (the operator's locked numbers) ---
DAILY_BUDGET = 5.00              # $/day per campaign — the throttle floor
START_BID = 0.35                 # genre-average starting bid
BID_CEILING = 0.65               # work up from low; never start high (informational)
BID_STRATEGY = "LEGACY_FOR_SALES"  # SP "dynamic bids, DOWN ONLY"
MONTHLY_CAP = 500.00             # portfolio cap — R8 alerts as pooled spend nears it
MARKET = "USKDP"

# The lead's manual keywords. The plan says "start broad or phrase, harvest the
# winners into exact later." PHRASE is the tighter of the two and the safer
# default under a $5/day budget — flagged so the operator can switch it to BROAD.
LEAD_MATCH = "PHRASE"

# ---------------------------------------------------------------------------
# Operator config (kdp_config.json — gitignored, seeded from kdp_config.example.json)
#
# These two used to be literals in this file, which meant one seller's book ASINs
# and pen names lived in shared code. They are catalogue facts, not logic.
#
#   held_out_opener_asins — series openers priced too tight for a direct
#     first-book ad to pay (e.g. a $1.99 ebook at 35% royalty). They are kept out
#     of the LEAD set; their series still gets its series ad.
#   series_pen_overrides  — pen name per series slug, for series whose books
#     carry no pen name anywhere in the manifest. Everything else resolves from a
#     sibling book in the same series.
#
# Both default to empty, so a fresh install simply holds nothing out.
# ---------------------------------------------------------------------------
_CONFIG_PATH = os.path.join(HERE, "kdp_config.json")
_CONFIG_EXAMPLE = os.path.join(HERE, "kdp_config.example.json")


def _load_config():
    for path in (_CONFIG_PATH, _CONFIG_EXAMPLE):
        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, ValueError):
            continue
    return {}


_CONFIG = _load_config()

HELD_OUT_OPENER_ASINS = set(_CONFIG.get("held_out_opener_asins") or ())

FREE_NEGATIVE = "free"           # negative PHRASE on every campaign (kill "free books")

SERIES_PEN_OVERRIDES = dict(_CONFIG.get("series_pen_overrides") or {})


def _kw_clean(text):
    """Amazon rejects keyword / negative-keyword text containing a comma, and does
    it silently at the item level — a title like "Small Balcony, Big Harvest"
    just never lands. Strip commas and collapse whitespace; Amazon matches
    punctuation-insensitively anyway, so "Small Balcony Big Harvest" is the same
    negative without the rejection."""
    return " ".join((text or "").replace(",", " ").split())


def _slug_label(series_slug):
    """A display name for a series ad from its slug: 'cedar-bend' -> 'Cedar Bend',
    'bellamy-group' -> 'Bellamy'. The '-group' suffix is a manifest artifact."""
    parts = [p for p in series_slug.split("-") if p and p != "group"]
    return " ".join(w.capitalize() for w in parts)


def load_manifest(path):
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise SystemExit(f"manifest {path} is not a list of books")
    return data


def _ebook(book):
    return (book.get("formats") or {}).get("ebook") or {}


def _series_pens(books):
    """series_slug -> the pen name to use for that series' author-name negative.
    Operator overrides win (they fill the manifest gaps); otherwise a lead or
    series ad borrows the first non-null pen a sibling book records."""
    pens = dict(SERIES_PEN_OVERRIDES)
    for b in books:
        ss, pen = b.get("series_slug"), b.get("pen_name")
        if ss and pen and ss not in pens:
            pens[ss] = pen
    return pens


def is_entry_point(book):
    """Standalones (no series) are their own entry point; a series' entry point is
    book one (series_position == 1). Mid-series books get no direct campaign."""
    return book.get("series_slug") is None or book.get("series_position") == 1


def _pen_for(book, series_pens):
    return book.get("pen_name") or series_pens.get(book.get("series_slug"))


def _negatives(*, campaign_kind, titles, pen, own_asins):
    """The section-4 day-one negatives, as ads_client payload items (campaign/ad-group
    ids are filled in at apply time). Author name + own titles as NEGATIVE_EXACT,
    'free' as NEGATIVE_PHRASE, own ASINs as negative product targets. `flags` names
    anything we could not build (e.g. an unknown pen)."""
    neg_keywords, flags = [], []
    if pen:
        neg_keywords.append({"keywordText": _kw_clean(pen), "matchType": "NEGATIVE_EXACT",
                             "reason": "own author name"})
    else:
        flags.append("no pen name in manifest — author-name negative omitted; confirm the pen")
    for t in titles:
        neg_keywords.append({"keywordText": _kw_clean(t), "matchType": "NEGATIVE_EXACT",
                             "reason": "own title"})
    neg_keywords.append({"keywordText": FREE_NEGATIVE, "matchType": "NEGATIVE_PHRASE",
                         "reason": "kill 'free' searches"})
    neg_targets = [{"asin": a, "reason": "own ASIN"} for a in own_asins]
    return neg_keywords, neg_targets, flags


def _econ(asin):
    """Break-even for the preview, if the book's royalty is already loaded into the
    engine (appctl kdp-book). Absent until the operator loads royalties — reported
    honestly rather than guessed, so nobody reads a made-up break-even."""
    try:
        import kdp_econ
        e = kdp_econ.book_econ(asin)
    except Exception:
        e = None
    if not e:
        return {"available": False, "note": "royalty not loaded (appctl kdp-book)"}
    return {"available": True, "royalty": e["royalty"], "break_even": e["break_even"],
            "list_price": e["list_price"]}


def compose_lead(book, series_pens, start_date):
    """One entry-point lead: the '<Title> - EXACT' manual-keyword campaign."""
    eb = _ebook(book)
    asin = eb.get("asin")
    title = book["title"]
    pen = _pen_for(book, series_pens)
    kws = book.get("keyword_slots") or []
    neg_kw, neg_tgt, flags = _negatives(
        campaign_kind="lead", titles=[title], pen=pen, own_asins=[asin])
    return {
        "kind": "lead",
        "book_slug": book.get("slug"),
        # Named for what it IS — PHRASE-match manual keywords. With AUTO deferred,
        # this lone launch campaign is the only discovery vehicle; exact-only on 7
        # seeds would starve for impressions. Harvested winners land in a separate
        # exact campaign later.
        "name": f"{title} - PHRASE",
        "targetingType": "MANUAL",
        "state": "ENABLED",
        "budget": DAILY_BUDGET,
        "biddingStrategy": BID_STRATEGY,
        "startDate": start_date,
        "ad_group": {"name": title, "defaultBid": START_BID},
        "product_ads": [asin],
        "keywords": [{"keywordText": _kw_clean(k), "matchType": LEAD_MATCH, "bid": START_BID}
                     for k in kws],
        "negatives": {"keywords": neg_kw, "product_targets": neg_tgt},
        "econ": _econ(asin),
        "flags": flags,
    }


def compose_series(series_slug, series_books, series_pens, start_date):
    """One series ad: '<Series> - SERIES', AUTO targeting, a product ad per book."""
    ordered = sorted(series_books, key=lambda b: b.get("series_position") or 0)
    label = _slug_label(series_slug)
    asins = [_ebook(b).get("asin") for b in ordered]
    titles = [b["title"] for b in ordered]
    pen = series_pens.get(series_slug)
    # A series ad gets NO own-ASIN product negatives. Negating the in-series ASINs
    # would block the cross-promotion (book 2's ad on book 1's page) that is the
    # whole point of a series ad, and Amazon won't serve a product ad on its own
    # page anyway, so the self-serving waste it would prevent is negligible.
    neg_kw, neg_tgt, flags = _negatives(
        campaign_kind="series", titles=titles, pen=pen, own_asins=[])
    return {
        "kind": "series",
        "series_slug": series_slug,
        "name": f"{label} - SERIES",
        "targetingType": "AUTO",
        "state": "ENABLED",
        "budget": DAILY_BUDGET,
        "biddingStrategy": BID_STRATEGY,
        "startDate": start_date,
        "ad_group": {"name": f"{label} series", "defaultBid": START_BID},
        "product_ads": asins,
        "keywords": [],                     # AUTO — Amazon generates the targeting
        "negatives": {"keywords": neg_kw, "product_targets": neg_tgt},
        "econ": _econ(asins[0]) if asins else {"available": False},
        "flags": flags,
    }


def build_plan(books, start_date=None):
    """Compose the full day-one set: leads for every entry point (minus held-out)
    plus one series ad per series. Returns the structured plan dict."""
    start_date = start_date or datetime.date.today().isoformat()
    series_pens = _series_pens(books)

    leads, held_out = [], []
    for b in books:
        if not is_entry_point(b):
            continue
        if _ebook(b).get("asin") in HELD_OUT_OPENER_ASINS:
            held_out.append({"slug": b.get("slug"), "title": b["title"],
                             "reason": "held out of direct ads (price makes break-even too tight)"})
            continue
        leads.append(compose_lead(b, series_pens, start_date))

    series_map = {}
    for b in books:
        ss = b.get("series_slug")
        if ss:
            series_map.setdefault(ss, []).append(b)
    series = [compose_series(ss, bs, series_pens, start_date)
              for ss, bs in series_map.items()]

    campaigns = leads + series
    daily = round(len(campaigns) * DAILY_BUDGET, 2)
    return {
        "market": MARKET,
        "settings": {"daily_budget": DAILY_BUDGET, "start_bid": START_BID,
                     "bid_ceiling": BID_CEILING, "strategy": BID_STRATEGY,
                     "lead_match": LEAD_MATCH, "monthly_cap": MONTHLY_CAP,
                     "start_date": start_date},
        "summary": {
            "leads": len(leads), "series_ads": len(series),
            "total_budgets": len(campaigns),
            "daily_spend_ceiling": daily,
            "monthly_cap": MONTHLY_CAP,
            "cap_note": (f"{len(campaigns)} campaigns x ${DAILY_BUDGET:.0f}/day = "
                         f"${daily:.0f}/day theoretical max; the ${MONTHLY_CAP:.0f}/mo "
                         f"portfolio cap is the real limiter (R8 guards it)"),
            "held_out": held_out,
        },
        "campaigns": campaigns,
    }


# --------------------------------------------------------------------------- #
# Live create (operator-run). Mirrors lottery_build's chain: create campaign ->
# read its id -> ad group -> product ads -> keywords -> negatives, per campaign.
# KILL + econ gated. Never run by Claude; the operator runs `--apply`.
# --------------------------------------------------------------------------- #
def apply_plan(plan, auto=False):
    import killswitch
    import products
    from ads_client import AdsClient, success_ids
    killswitch.check()
    if killswitch.active():
        return {"applied": False, "blocked": "kill"}
    conn = None
    try:
        import db
        conn = db.connect(ro=True)
        gate = products.econ_gate(conn)
    except Exception:
        gate = {"ok": True}
    if not gate.get("ok", True):
        return {"applied": False, "blocked": "econ_gate", "reasons": gate.get("reasons")}
    if not auto:
        print(f"About to CREATE {len(plan['campaigns'])} live campaigns on {plan['market']}.")
        if input("Type APPLY to proceed: ").strip() != "APPLY":
            return {"applied": False, "blocked": "unconfirmed"}

    client = AdsClient(MARKET)
    # Idempotency: a live campaign of the same name is left alone, so re-running
    # --apply never duplicates a budget. The builder is not otherwise idempotent.
    existing = {c.get("name") for c in client.list_all(
        "/sp/campaigns/list", "application/vnd.spCampaign.v3+json", "campaigns")}
    made = []
    for c in plan["campaigns"]:
        if c["name"] in existing:
            made.append({"name": c["name"], "created": False, "skipped": "already exists"})
            continue
        st, js = client.create_campaigns([{
            "name": c["name"], "budget": c["budget"], "startDate": c["startDate"],
            "targetingType": c["targetingType"], "strategy": c["biddingStrategy"]}])
        ids = success_ids(js, "campaigns", "campaignId")
        if 0 not in ids:
            made.append({"name": c["name"], "created": False, "http": st, "body": js})
            continue
        cid = ids[0]
        st, js = client.create_ad_groups([{
            "name": c["ad_group"]["name"], "campaignId": cid,
            "defaultBid": c["ad_group"]["defaultBid"]}])
        ag = success_ids(js, "adGroups", "adGroupId").get(0)
        if ag is None:
            made.append({"name": c["name"], "campaign_id": cid, "created": "campaign_only"})
            continue
        # Count what each downstream batch actually created. Amazon answers 207
        # with per-item errors (a comma in a negative keyword, an ad-ineligible
        # ASIN), so "the call didn't raise" is NOT "everything landed" — report
        # the ratio and flag any shortfall instead of hiding it.
        _, pa_js = client.create_product_ads([{"campaignId": cid, "adGroupId": ag, "asin": a}
                                              for a in c["product_ads"]])
        pa_ok = len(success_ids(pa_js, "productAds", "adId"))
        kw_ok = 0
        if c["keywords"]:
            _, kw_js = client.create_keywords([{"campaignId": cid, "adGroupId": ag,
                                               "keywordText": k["keywordText"],
                                               "matchType": k["matchType"], "bid": k["bid"]}
                                              for k in c["keywords"]])
            kw_ok = len(success_ids(kw_js, "keywords", "keywordId"))
        neg_ok = 0
        if c["negatives"]["keywords"]:
            nres = client.create_negative_keywords([{"campaignId": cid, "adGroupId": ag,
                                                    "keywordText": n["keywordText"],
                                                    "matchType": n["matchType"]}
                                                   for n in c["negatives"]["keywords"]])
            neg_ok = sum(1 for r in nres for x in r.get("created_ids", []) if x is not None)
        if c["negatives"]["product_targets"]:
            client.create_negative_product_targets([{"campaignId": cid, "adGroupId": ag,
                                                    "asin": n["asin"]}
                                                   for n in c["negatives"]["product_targets"]])
        entry = {"name": c["name"], "campaign_id": cid, "ad_group_id": ag, "created": True,
                 "product_ads": f"{pa_ok}/{len(c['product_ads'])}",
                 "keywords": f"{kw_ok}/{len(c['keywords'])}",
                 "negatives": f"{neg_ok}/{len(c['negatives']['keywords'])}"}
        partial = [name for name, ok, want in
                   (("product_ads", pa_ok, len(c["product_ads"])),
                    ("keywords", kw_ok, len(c["keywords"])),
                    ("negatives", neg_ok, len(c["negatives"]["keywords"]))) if ok < want]
        if partial:
            entry["partial"] = partial
        made.append(entry)
    partials = [m["name"] for m in made if m.get("partial")]
    return {"applied": True, "market": MARKET, "campaigns": made,
            "partials": partials}


def main():
    ap = argparse.ArgumentParser(description="KDP Sponsored Products campaign builder")
    ap.add_argument("--manifest", default=os.path.join(HERE, "outputs", "kdp_ads_manifest.json"),
                    help="per-book manifest JSON (default: outputs/kdp_ads_manifest.json)")
    ap.add_argument("--start-date", default=None, help="campaign start date (default: today)")
    ap.add_argument("--sample", action="store_true",
                    help="preview just one lead + one series ad")
    ap.add_argument("--apply", action="store_true", help="CREATE live (operator-run)")
    ap.add_argument("--auto", action="store_true", help="skip the APPLY confirmation")
    args = ap.parse_args()

    # KDP-only: books are single ASINs, SP-only, dynamic-down-only. Refuse elsewhere.
    if markets.current() != MARKET:
        raise SystemExit(f"kdp_build is {MARKET}-only; set ADS_MARKET={MARKET}.")
    if not os.path.exists(args.manifest):
        raise SystemExit(f"manifest not found: {args.manifest} (pass --manifest PATH)")

    books = load_manifest(args.manifest)
    plan = build_plan(books, start_date=args.start_date)

    if args.apply:
        print(json.dumps(apply_plan(plan, auto=args.auto), indent=2))
        return

    out = dict(plan)
    out["dry_run"] = True
    if args.sample:
        lead = next((c for c in plan["campaigns"] if c["kind"] == "lead"), None)
        series = next((c for c in plan["campaigns"] if c["kind"] == "series"), None)
        out["campaigns"] = [c for c in (lead, series) if c]
        out["sample"] = True
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
