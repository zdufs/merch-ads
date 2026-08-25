#!/usr/bin/env python3
"""
Diagnostic: list ALL Amazon Ads accounts reachable with our credentials,
using the newer Account Management API (/adsAccounts/list). This tells us
whether your Merch account is reachable via the API and what its
country-level profile IDs are.

Every reachable account is listed either way. To have one singled out as
"yours", put its id in `.env` as AMZN_ADS_ACCOUNT_ID — the value Amazon
shows as the account id, of the form `amzn1.ads-account.g.<id>`. Without it
the script simply prints everything it can see, which is what a first run
needs anyway: you do not yet know the id.

It used to carry one account id as a hardcoded constant. That published the
id of whoever wrote it, and told every other reader their own account was
unreachable.

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
LIST_ACCEPT = "application/vnd.listaccountsresource.v1+json"


def load_env(path):
    env = {}
    # This is a DIAGNOSTIC, so it is exactly what someone runs when things are
    # not working — including before there is a .env at all. A stack trace is
    # the least useful thing to hand that person.
    if not os.path.exists(path):
        sys.exit(f"No .env found at {path}\n"
                 "Copy .env.example to .env and fill in your Amazon client id, "
                 "secret and refresh token first (see docs/SETUP.md).")
    with open(path, encoding="utf-8") as f:
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
    # Optional. Unset, every reachable account is still listed in full — which
    # is what a first run needs, because you cannot name the id before you have
    # seen it.
    merch_account_id = (env.get("AMZN_ADS_ACCOUNT_ID")
                        or os.environ.get("AMZN_ADS_ACCOUNT_ID") or "").strip()

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
            if merch_account_id and acc_id == merch_account_id:
                merch_hits.append((region, name, status, alts))

    print("\n" + "=" * 72)
    if merch_hits:
        print("RESULT: MERCH ACCOUNT IS REACHABLE via the API.")
        for region, name, status, alts in merch_hits:
            print(f"  [{region}] {name}  status={status}")
            for alt in alts:
                print(f"     {alt.get('countryCode')} -> profileId {alt.get('profileId')}")
        print("\nUse the US + GB profileIds above for Merch campaign management.")
    elif merch_account_id:
        print(f"RESULT: {merch_account_id} was NOT found above.")
        print("Check the id, or drop AMZN_ADS_ACCOUNT_ID to just list everything.")
    else:
        print("RESULT: every account these credentials can reach is listed above.")
        print("Take the profileId for each country you advertise in and put them")
        print("in .env as AMZN_ADS_PROFILE_ID_<CODE>. To have one account called")
        print("out by name on future runs, set AMZN_ADS_ACCOUNT_ID as well.")


if __name__ == "__main__":
    main()
