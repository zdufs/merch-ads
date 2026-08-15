#!/usr/bin/env python3
"""
Fill in product types for ad groups whose ASIN wasn't in the Merch export,
using the campaign name (keyword -> type, else the campaign's dominant type).
Pure local SQLite — no Amazon calls. Safe to run anytime after map_products.py.

Run:  python3 enrich_types.py
"""

import sqlite3
from collections import Counter, defaultdict
import db
import products

conn = db.connect()
cur = conn.cursor()

camp_name = dict(cur.execute("SELECT campaign_id, name FROM campaigns").fetchall())
ag_camp = dict(cur.execute("SELECT ad_group_id, campaign_id FROM ad_groups").fetchall())

# campaign -> dominant already-mapped product type
dom = defaultdict(Counter)
for agid, pt in cur.execute("SELECT ad_group_id, product_type FROM ad_group_product WHERE product_type<>''"):
    cid = ag_camp.get(agid)
    if cid:
        dom[cid][pt] += 1
camp_dom = {cid: c.most_common(1)[0][0] for cid, c in dom.items()}

unmapped = [r[0] for r in cur.execute(
    "SELECT ad_group_id FROM ad_group_product WHERE COALESCE(product_type,'')=''").fetchall()]

updates, by_kw, by_dom = [], 0, 0
for agid in unmapped:
    name = camp_name.get(ag_camp.get(agid), "")
    t = products.infer_type_from_campaign(name)
    if t:
        by_kw += 1
    else:
        t = camp_dom.get(ag_camp.get(agid))
        if t:
            by_dom += 1
    if t:
        updates.append((t, agid))

cur.executemany("UPDATE ad_group_product SET product_type=? WHERE ad_group_id=?", updates)
conn.commit()

rem = cur.execute("SELECT COUNT(*) FROM ad_group_product WHERE COALESCE(product_type,'')=''").fetchone()[0]
print(f"Filled {len(updates)} ad groups  (by campaign keyword: {by_kw}, by campaign-dominant: {by_dom})")
print(f"Still unmapped: {rem}")
conn.close()
