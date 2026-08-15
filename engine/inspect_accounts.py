#!/usr/bin/env python3
"""
Diagnostic: list ALL Amazon Ads accounts reachable with our credentials,
using the newer Account Management API (/adsAccounts/list). This tells us
whether the MERCH account (amzn1.ads-account.g.5qa3q62qbt9zd33mdcplulcig)
is reachable via the API and what its country-level profile IDs are.

Run:  python3 inspect_accounts.py
"""

import json
import os

import paths
import sys

try:
    import requests
except ImportError:
    sys.exit("Missing 'requests'. Run: pip3 install requests --break-system-packages")

ENV_PATH = os.path.join(paths.REPO_ROOT, ".env")
LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"
HOSTS = {
    "NA": "https://advertising-api.amazon.com",
    "EU": "https://advertising-api-eu.amazon.com",
}
MERCH_ACCOUNT_ID = "amzn1.ads-account.g.5qa3q62qbt9zd33mdcplulcig"
LIST_ACCEPT = "application/vnd.listaccountsresource.v1+json"


def load_env(path):
    env = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def get_access_token(env):
    resp = requests.post(
        LWA_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": env["AMZN_ADS_REFRESH_TOKEN"],
            "client_id": env["AMZN_ADS_CLIENT_ID"],
            "client_secret": env["AMZN_ADS_CLIENT_SECRET"],
        },
        timeout=30,
    )
    if resp.status_code != 200:
        sys.exit(f"FAIL — token refresh ({resp.status_code}): {resp.text}")
    return resp.json()["access_token"]


def list_accounts(host, client_id, token):
    """POST /adsAccounts/list — returns list of ads accounts with alternateIds."""
    resp = requests.post(
        host + "/adsAccounts/list",
        headers={
            "Amazon-Advertising-API-ClientId": client_id,
            "Authorization": f"Bearer {token}",
            "Content-Type": LIST_ACCEPT,
            "Accept": LIST_ACCEPT,
        },
        data=json.dumps({}),
        timeout=30,
    )
    return resp


def main():
    env = load_env(ENV_PATH)
    token = get_access_token(env)
    cid = env["AMZN_ADS_CLIENT_ID"]

    merch_hits = []
    for region, host in HOSTS.items():
        print("=" * 72)
        print(f"Region {region} — {host}/adsAccounts/list")
        resp = list_accounts(host, cid, token)
        if resp.status_code != 200:
            print(f"  HTTP {resp.status_code}: {resp.text[:400]}")
            continue
        data = resp.json()
        accounts = data.get("adsAccounts", data.get("accounts", []))
        if not accounts:
            print("  (no accounts returned)")
            print("  raw:", json.dumps(data)[:400])
            continue
        for a in accounts:
            acc_id = a.get("adsAccountId", a.get("accountId", "?"))
            name = a.get("accountName", a.get("name", ""))
            status = a.get("status", "")
            print(f"\n  account: {name}")
            print(f"    adsAccountId: {acc_id}")
            print(f"    status: {status}")
            alts = a.get("alternateIds", [])
            for alt in alts:
                print(f"      country={alt.get('countryCode')}  "
                      f"profileId={alt.get('profileId')}  "
                      f"entityId={alt.get('entityId')}")
            if acc_id == MERCH_ACCOUNT_ID:
                merch_hits.append((region, name, status, alts))

    print("\n" + "=" * 72)
    if merch_hits:
        print("RESULT: MERCH ACCOUNT IS REACHABLE via the API.")
        for region, name, status, alts in merch_hits:
            print(f"  [{region}] {name}  status={status}")
            for alt in alts:
                print(f"     {alt.get('countryCode')} -> profileId {alt.get('profileId')}")
        print("\nUse the US + GB profileIds above for Merch campaign management.")
    else:
        print("RESULT: Merch account NOT found in /adsAccounts/list output above.")
        print("Paste me everything printed and we'll decide the next move.")


if __name__ == "__main__":
    main()
