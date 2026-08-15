# Security

## Reporting a vulnerability

**Do not open a public issue.**

Use GitHub's private reporting instead: go to the **Security** tab of this repository and
choose **Report a vulnerability**. That opens a private thread with the maintainer.

Please include:

- What the problem is.
- How to reproduce it.
- What an attacker could do with it.

You will get a first response within a week. This is a one-person project, so please be
patient with the timeline — but the response will be honest about what will and will not
be fixed.

---

## What counts as a vulnerability here

This is local software with no server and no multi-user surface, so the realistic threats
are narrow:

**In scope:**

- Anything that could **leak credentials** — a code path that logs, prints, commits or
  transmits `.env`, an access token, a refresh token or a client secret.
- Anything that lets untrusted input reach the **Amazon write path** — a rule, an imported
  CSV, or a filename that causes an unintended bid, pause or campaign change.
- **Command injection** through the Python bridge, an imported file, or a rule.
- A **safety gate that can be bypassed** — the kill switch, the freshness gate, the
  economics gate, or the bid ceiling. A bypass here spends real money, so it is treated as
  a security bug even though it is not a classic one.
- Anything that writes to a database or an account other than the one configured.

**Out of scope:**

- Amazon's own API behaviour, rate limits or report failures.
- Anything requiring an attacker to already have access to your Mac user account.
- The software losing you money through bad advertising decisions. That is a bug or a
  disagreement about strategy — open a normal issue.
- Missing code signing or notarization. Known and documented.

---

## How credentials are handled

- Everything secret lives in **`.env` only**. Nothing else stores a credential.
- `.env` is gitignored, and so is `*.env`.
- **The Swift app never reads `.env`.** Only the Python engine does.
- Access tokens are held in memory for the life of a process and never written to disk.
- `writes_log` records what changed and the previous value. It never records credentials.

If you contribute code, keep it that way. Never add a debug print of the environment, and
never log a request header.

---

## If your credentials leak

Act in this order:

1. **Rotate the client secret** at [developer.amazon.com](https://developer.amazon.com) →
   your Login with Amazon security profile. This invalidates the refresh token too.
2. **Generate a new refresh token**: `python3 engine/get_token.py`.
3. Update `.env`.
4. Check `python3 engine/appctl.py audit` for writes you did not make.
5. If a secret was committed to git, rotating it is what actually protects you. Removing
   the commit is secondary — assume anything pushed was copied.

---

## Hardening your own install

- Keep the repository folder on a local disk with FileVault on.
- Set the bid and budget ceilings before your first live run:
  `python3 engine/appctl.py maxbid --set --target 0.50 --budget 20`
- Run in approval mode until you trust the automation:
  `python3 engine/appctl.py approval-mode --on`
- Know the kill switch: `touch KILL`.
- Never paste `appctl.py health` output into a public issue without removing your profile
  ids first.

---

## Supported versions

Only the latest `main` is supported. There are no backports.
