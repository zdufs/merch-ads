#!/usr/bin/env python3
"""
List your Amazon Ads profiles (EU region) so you can grab the DE/FR/ES/IT Merch
profile ids and add them to .env. Read-only.

Run:  python3 list_profiles.py
Then copy the Merch profile id for each country into .env, e.g.:
    AMZN_ADS_PROFILE_ID_DE=1234567890
    AMZN_ADS_PROFILE_ID_FR=...
Once added, the daily job (and any per-market command) picks that market up
automatically — no code changes.
"""

import requests
import markets
from ads_client import AdsClient

# any EU market gives us a valid token on the EU host (UK is already configured)
c = AdsClient("UK")
tok = c.access_token()
headers = {"Amazon-Advertising-API-ClientId": c.client_id, "Authorization": f"Bearer {tok}"}

print(f"{'country':8} {'profileId':>16}  {'type':10} name")
for region, base in [("EU", markets.EU), ("NA", markets.NA)]:
    try:
        r = requests.get(base + "/v2/profiles", headers=headers, timeout=30)
        if r.status_code != 200:
            print(f"  [{region}] HTTP {r.status_code}: {r.text[:120]}")
            continue
        for p in r.json():
            ai = p.get("accountInfo", {}) or {}
            print(f"{p.get('countryCode',''):8} {str(p.get('profileId','')):>16}  "
                  f"{ai.get('type',''):10} {ai.get('name','')}")
    except Exception as e:
        print(f"  [{region}] error: {e}")
