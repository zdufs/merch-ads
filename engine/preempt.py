#!/usr/bin/env python3
"""
Preemptive "can't-fulfill" negatives — wrong-FORMAT terms only (the genuinely
zero-downside set). A t-shirt can never fulfill a "hoodie" / "mug" / "sticker"
search no matter the design, so we block other product formats at the campaign
level, immediately (no spend threshold).

Deliberately NOT included: youth/kid terms — those power big ADULT niches
(occasion and family-role niches above all), so they're a per-design
human call, not a blanket rule. Also excluded: "sweater" (ugly-christmas-sweater).

negatives are applied as NEGATIVE_PHRASE at campaign level.
"""

# Each format group's search synonyms. A campaign of one group negates the OTHER
# groups' synonyms. Tee synonyms are never negated on tee campaigns, etc.
FORMAT_GROUPS = {
    "tee":        ["t-shirt", "t shirt", "tshirt", "tee", "tees", "shirt"],
    "hoodie":     ["hoodie", "hoodies", "pullover hoodie", "zip hoodie", "zip up hoodie"],
    "sweatshirt": ["sweatshirt", "sweatshirts", "crewneck", "crew neck"],
    "tank":       ["tank top", "tank tops", "muscle tank"],
    "longsleeve": ["long sleeve", "long-sleeve", "longsleeve"],
    "vneck":      ["v neck", "v-neck", "vneck"],
    "mug":        ["mug", "mugs", "coffee mug"],
    "drinkware":  ["tumbler", "tumblers", "water bottle"],
    "hat":        ["hat", "cap", "beanie", "trucker hat", "baseball cap", "snapback"],
    "sticker":    ["sticker", "stickers", "decal"],
    "wallart":    ["poster", "canvas", "wall art", "print"],
    "case":       ["phone case", "popsocket", "pop socket", "iphone case"],
    "home":       ["pillow", "throw pillow", "blanket", "tote bag", "tote"],
}

# product_type (from the export / map_products) -> which format group it belongs to
TYPE_GROUP = {
    "standard_tshirt": "tee", "premium_tshirt": "tee",
    "performance_tshirt": "tee", "comfort_colors_heavyweight": "tee",
    "standard_pullover_hoodie": "hoodie", "zip_hoodie": "hoodie", "performance_hoodie": "hoodie",
    "standard_sweatshirt": "sweatshirt", "comfort_colors_sweatshirt": "sweatshirt",
    "comfort_colors_crop_sweatshirt": "sweatshirt",
    "tank_top": "tank", "long_sleeve": "longsleeve",
    "vneck": "vneck", "crop_top": "tee",
    "mug": "mug", "tumbler": "drinkware", "water_bottle": "drinkware",
    "printed_trucker_hat": "hat", "printed_baseball_hat": "hat", "sport_sun_visor": "hat",
    "throw_pillow": "home", "tote_bag": "home",
    "phone_case_apple_iphone": "case", "pop_socket": "case",
}


def negatives_for(product_type):
    """Wrong-format NEGATIVE_PHRASE terms for a product type (its OTHER formats).
    Unknown type -> [] (no preemptive negatives, to stay safe)."""
    grp = TYPE_GROUP.get(product_type)
    if not grp:
        return []
    out = []
    for g, terms in FORMAT_GROUPS.items():
        if g != grp:
            out.extend(terms)
    return sorted(set(out))
