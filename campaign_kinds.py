#!/usr/bin/env python3
"""One place that decides what kind of campaign a name describes.

Amazon has no field for "this is a lottery campaign" — the strategy lives in the
name the builder gave it. So every part of the engine that treats campaign types
differently has to read the name, and they all have to read it the same way. When
two copies drift, a campaign gets bid on by one set of rules and reported under
another.

Kinds:
  scavenger  typed cohort campaigns, one per product type
  lottery    very wide, very cheap discovery campaigns (up to 1,000 ASINs each)
  harvested  promoted search-term winners, given their own campaign
  standard   everything else, including anything created by hand in the console
"""

import scavenger


def classify(name):
    """The engine's view of a campaign's strategy, from its name.

    Lottery covers both the EU 'LOTTO - ' prefix and the US 'Lotto'/'Lottery'
    naming. Scavenger uses its own prefix test.
    """
    n = name or ""
    low = n.lower()
    if scavenger.is_scavenger(n):
        return "scavenger"
    if n.startswith("LOTTO - ") or low.split(" ", 1)[0] in ("lotto", "lottery"):
        return "lottery"
    if low.startswith("harvested"):
        return "harvested"
    return "standard"
