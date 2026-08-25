"""Whole-catalogue design suggester for promoting a cohort search-term winner.

A converting search term from a multi-design cohort (Scavenger/AUTO) belongs to a
FAMILY of designs, not one design and not the grab-bag. This ranks every design in
the catalogue by WHOLE-WORD title overlap with the term, so "heron" matches
'Grey Heron' but never 'Heronry', and "foo" never matches 'Football'. The
result is a suggestion the operator confirms — never an automatic decision.
"""

import re

# Generic words that carry no design meaning — dropped before matching.
STOPWORDS = {
    "t", "tee", "tees", "shirt", "shirts", "tshirt", "tshirts", "the", "of", "for",
    "a", "an", "and", "or", "to", "with", "outfit", "outfits", "design", "designs",
    "gift", "gifts", "men", "women", "kids", "funny",
}
_WORD = re.compile(r"[a-z0-9]+")


def tokenize(text):
    """Lowercased meaningful word tokens (stopwords + punctuation removed)."""
    return [w for w in _WORD.findall((text or "").lower()) if w not in STOPWORDS]


def catalogue_titles(conn):
    """asin -> (title, product_type, lifetime_sales). Title comes from the design
    ad group's name (ASIN_type_Title); the ASIN prefix and type are stripped."""
    out = {}
    for asin, pt, life, name in conn.execute(
        """SELECT p.asin, p.product_type, p.lifetime_sales, ag.name
           FROM ad_group_product p JOIN ad_groups ag ON ag.ad_group_id = p.ad_group_id
           WHERE p.asin IS NOT NULL"""):
        # name is "ASIN_type_Title..." — strip the EXACT known prefix (asin + pt),
        # since a naive split("_", 2) leaves a fragment on the front whenever the
        # product_type itself contains an underscore (e.g. "standard_pullover_hoodie").
        prefix = f"{asin}_{pt}_"
        title = name[len(prefix):] if name and name.startswith(prefix) else (name or "")
        # keep the row with the richest title / highest lifetime for a repeated ASIN
        prev = out.get(asin)
        if prev is None or (life or 0) > prev[2]:
            out[asin] = (title, pt, life or 0)
    return out


def suggest(conn, term, limit=50):
    """Ranked design suggestions for a search term (score > 0 only)."""
    wanted = set(tokenize(term))
    if not wanted:
        return []
    rows = []
    for asin, (title, pt, life) in catalogue_titles(conn).items():
        matched = wanted & set(tokenize(title))
        if not matched:
            continue
        rows.append({"asin": asin, "title": title, "product_type": pt,
                     "matched_words": sorted(matched), "score": len(matched),
                     "lifetime_sales": life})
    rows.sort(key=lambda r: (-r["score"], -r["lifetime_sales"], r["asin"]))
    return rows[:limit]
