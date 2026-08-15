# Amazon Ads API Access — Setup Walkthrough

> Plain-language, non-developer guide. Goal: end up with the 5 credentials the automation tool needs.
> Built 2026-06-15. Do the steps in order — each one depends on the last.

## What you're collecting (the finish line)

By the end you'll have these saved somewhere safe. The tool needs all five:

| Credential | What it is | Where it comes from |
|---|---|---|
| **Client ID** | Public ID of your "app" | Step 1 |
| **Client Secret** | Password for your "app" — keep secret | Step 1 |
| **Refresh Token** | Long-lived key that lets the tool log in forever without you | Step 4 |
| **Profile ID (US)** | Your US Merch advertising account ID | Step 5 |
| **Profile ID (UK)** | Your UK Merch advertising account ID | Step 5 |

(The tool also uses a short-lived "access token", but it generates that itself from the refresh token every hour — you don't manage it.)

There are **two separate Amazon websites** involved, and that trips everyone up:
- **developer.amazon.com** — where you make the "app" (Login with Amazon security profile).
- **advertising.amazon.com** — where you apply for permission to use the Ads API.

You need to do both and then **link them together**.

---

## Step 0 — Prerequisites (5 min)

- Log in to your Amazon Ads console with the email tied to your Merch advertising account. Confirm you can see your Sponsored Products campaigns. If you already advertise, you are eligible.
- Have your business details handy for the application: your registered business name, your website, and your role (owner, director, employee).
- Decide on a privacy-policy URL. The app registration asks for one and will not accept a blank. A single simple page on your own domain is enough — it only has to state what data your tool collects and what you do with it.

---

## Step 1 — Create the "app" (Login with Amazon security profile) → Client ID + Secret

1. Go to **developer.amazon.com**, sign in with your Amazon account, accept the developer agreement if prompted.
2. Top menu: **Apps & Services → Login with Amazon**.
3. Click **Create a New Security Profile**. Fill in:
   - **Name:** e.g. `My Ads Automation`
   - **Description:** e.g. `Internal tool for managing Merch Sponsored Products bids`
   - **Privacy Notice URL:** your privacy page.
4. Save. Your profile now appears in the list.
5. Hover the gear/settings icon on the profile → **Web Settings** → click **Show** next to Client ID and Client Secret.
   - **Copy both. This is credential #1 and #2.**
6. Still in **Web Settings**, find **Allowed Return URLs** and add exactly:
   `https://localhost:443`
   (We use this as a harmless "catch" address during the one-time login in Step 4. Save.)

> Keep this browser tab — you'll come back here in Step 3.

---

## Step 2 — Apply for Amazon Ads API access (the approval wait)

1. Go to the Amazon Ads API onboarding page (**advertising.amazon.com/API/docs** → "Apply for access"), signed in with your **Merch ads** email.
2. Choose the path for a **direct advertiser** (you're managing your *own* account, not other people's).
3. Complete the form with your business details and a short description of use: *"Automating bid, budget and keyword management for my own Amazon Merch Sponsored Products campaigns."*
4. Submit. **Approval can take up to ~72 hours** (sometimes faster). You'll get an email.

> This is the step most likely to stall. If you get rejected or asked for more info, send me the message and I'll help you respond. This is the real gate — nothing else works until it's approved.

---

## Step 3 — Link the approval to your app

1. When the approval email arrives, **click its link**, then **Continue**.
2. It asks which Login with Amazon security profile to attach — pick the security profile you created in Step 1, and **Submit**.
3. Done — your Client ID now actually has Ads API permission. (Without this link, the credentials exist but don't work.)

---

## Step 4 — One-time login to get your Refresh Token

This is the only fiddly part, and **I'll do it with you** — either by driving your browser, or by handing you a tiny script. Here's what happens so it's not a black box:

1. We build a special Amazon login URL using your Client ID (scope = `advertising::campaign_management`).
2. You open it, log in, and click **Allow**.
3. Amazon bounces you to `https://localhost:443/?code=XXXX...` — the page won't load (that's fine), but the **`code=` value in the address bar** is what we need. You copy that.
4. We exchange that one-time code for a permanent **Refresh Token** using your Client ID + Secret. (I provide a ready-to-run script — `get_token.py` — that does this and prints the refresh token. You run one command.)
   - **That refresh token is credential #3.** It doesn't expire as long as you keep using it.

> The code in step 3 is single-use and expires in minutes, so we do the exchange immediately. I'll have the script ready before we start.

---

## Step 5 — Get your Profile IDs (US + UK)

1. Using the refresh token, the same helper script calls the **Profiles** endpoint.
2. It prints a list of your advertising accounts with their region and a **profileId** number.
3. Pick the **US** profile (credential #4) and the **UK** profile (credential #5).

---

## Step 6 — Store the credentials safely

- I'll create a `.env` file (or macOS Keychain entries) on the Mac Mini holding all five values.
- It lives **outside** any git repo and is never logged. The tool reads from it.
- That's the finish line — Phase 0 of the build can begin (pull a report, store it).

---

## Reality check

- **Amazon's approval is the bottleneck**, not the technical setup. Apply first; the rest
  takes about thirty minutes once you are approved.
- Approval is reviewed by a person and can take days or weeks. There is no way to speed
  it up, and there is no path around it — the official Amazon Ads MCP server needs the
  same approval.
- If your application is rejected, the rejection email says why. The usual causes are a
  missing or unreachable privacy-policy URL, and a description of use that sounds like
  you will manage other people's accounts. Keep it to managing your own.

---

## When you are approved

Come back to **[SETUP.md](SETUP.md)** at Step 5 and run `python3 engine/get_token.py`. It walks
you through the browser login and prints the refresh token you paste into `.env`.
