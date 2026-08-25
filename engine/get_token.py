#!/usr/bin/env python3
"""
Amazon Ads API — credential helper (Steps 4-5 of api-access-setup.md)

Non-developer friendly. Run it, follow the prompts. It does three things:
  1) builds the one-time Amazon login URL
  2) exchanges the resulting code for a permanent REFRESH TOKEN
  3) lists your advertising PROFILE IDs (US + UK)

Requirements: Python 3 and the `requests` library.
  Install once:  pip3 install requests --break-system-packages

Run:  python3 get_token.py
"""

import json
import sys
import urllib.parse

try:
    import requests
except ImportError:
    sys.exit("Missing 'requests'. Run: pip3 install requests --break-system-packages")

# LWA (login) token endpoint — same for all regions
LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"

# Regional Ads API hosts. We check both so we catch US (NA) and UK (EU) accounts.
PROFILE_HOSTS = {
    "North America (US/CA/MX)": "https://advertising-api.amazon.com",
    "Europe (UK/DE/FR/etc)": "https://advertising-api-eu.amazon.com",
}

REDIRECT_URI = "https://localhost:443"  # must match the Allowed Return URL on your security profile
SCOPE = "advertising::campaign_management"


def clean_code(raw: str) -> str:
    """Forgiving extraction of the auth code from whatever the user pasted:
    a full redirect URL, a 'code=XXX' fragment, or the bare code."""
    raw = raw.strip().strip('"').strip("'")
    # If they pasted a full URL, pull the 'code' query parameter.
    if "://" in raw or raw.startswith("localhost") or "?" in raw:
        try:
            qs = urllib.parse.urlparse(raw if "://" in raw else "https://" + raw).query
            params = urllib.parse.parse_qs(qs)
            if "code" in params:
                return params["code"][0]
        except Exception:
            pass
    # Strip a leading 'code=' if present.
    if raw.lower().startswith("code="):
        raw = raw[5:]
    # Drop anything after an '&'.
    raw = raw.split("&", 1)[0]
    return raw.strip()


def step1_build_login_url(client_id: str) -> None:
    params = {
        "client_id": client_id,
        "scope": SCOPE,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
    }
    url = "https://www.amazon.com/ap/oa?" + urllib.parse.urlencode(params)
    print("\n" + "=" * 70)
    print("STEP A — Open this URL in your browser, log in, and click 'Allow':\n")
    print(url)
    print("\nAmazon will then try to send you to https://localhost:443/?code=...")
    print("The page WON'T load — that's expected. Copy the 'code=' value from")
    print("the address bar (everything after code= and before any '&').")
    print("=" * 70 + "\n")


def step2_exchange_code(client_id: str, client_secret: str, code: str) -> str:
    resp = requests.post(
        LWA_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if resp.status_code != 200:
        sys.exit(f"\nToken exchange failed ({resp.status_code}):\n{resp.text}\n"
                 "Common cause: the code expired (it lasts only minutes) — redo STEP A for a fresh one.")
    data = resp.json()
    refresh = data["refresh_token"]
    access = data["access_token"]
    print("\n*** SUCCESS — your REFRESH TOKEN (credential #3). Save it safely: ***\n")
    print(refresh + "\n")
    return access


def step3_list_profiles(client_id: str, access_token: str) -> None:
    print("=" * 70)
    print("Your advertising PROFILE IDs (pick US + UK):\n")
    found_any = False
    for region_name, host in PROFILE_HOSTS.items():
        try:
            resp = requests.get(
                host + "/v2/profiles",
                headers={
                    "Amazon-Advertising-API-ClientId": client_id,
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
        except requests.RequestException as e:
            print(f"[{region_name}] request error: {e}")
            continue
        if resp.status_code != 200:
            print(f"[{region_name}] no access / error ({resp.status_code}): {resp.text[:200]}")
            continue
        profiles = resp.json()
        if not profiles:
            print(f"[{region_name}] (no profiles)")
            continue
        found_any = True
        for p in profiles:
            cc = p.get("countryCode", "?")
            pid = p.get("profileId", "?")
            ptype = p.get("accountInfo", {}).get("type", "?")
            name = p.get("accountInfo", {}).get("name", "")
            print(f"  [{region_name}] country={cc}  profileId={pid}  type={ptype}  {name}")
    if not found_any:
        print("\nNo profiles returned. Usually means the API application isn't fully approved/linked yet,")
        print("or your ads account is in a region not checked above. Send me the output and I'll help.")
    print("=" * 70 + "\n")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        sys.exit("usage: python3 get_token.py\n"
                 "Walks you through Login with Amazon and prints a refresh "
                 "token to paste into .env. Asks questions, so run it in a "
                 "terminal you can type into.")
    print("\nAmazon Ads API credential helper\n--------------------------------")
    try:
        client_id = input("Paste your Client ID: ").strip()
        client_secret = input("Paste your Client Secret: ").strip()
    except EOFError:
        # Piped, redirected, or run from a script. Foreseeable, so it gets a
        # sentence rather than the EOFError traceback it used to print.
        sys.exit("\nThis helper asks questions, so it needs a terminal it can "
                 "read from. Run it directly rather than piping input to it.")

    step1_build_login_url(client_id)
    raw = input("Paste the code (or the whole localhost URL) from the address bar: ").strip()
    code = clean_code(raw)

    access_token = step2_exchange_code(client_id, client_secret, code)
    step3_list_profiles(client_id, access_token)

    print("Done. Send me the refresh token + the US and UK profileId numbers")
    print("(or save them yourself) and we'll store them and start Phase 0.\n")


if __name__ == "__main__":
    main()
