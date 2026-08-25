# Building your own front end

**Short answer: yes, this is a real option, and pointing an AI coding agent at
this repository is a reasonable way to do it.**

That is not a throwaway suggestion. The Mac app in this repository was built
that way, against the same contract described below, and
[claude-code-handoff.md](claude-code-handoff.md) is the brief that was used.

This page is for you if you are on Windows or Linux, or if you simply want a
different screen than the one that ships.

---

## Why this repository suits it

Most projects make you reverse-engineer them before you can build on top. This
one does not, for one reason: **the engine already has a front-end contract, and
the shipped app uses nothing else.**

```
MERCHADS_DATA_DIR=<your data folder> ADS_MARKET=US python3 engine/appctl.py metrics
```

answers with exactly one JSON object on standard output:

```json
{"ok": true, "data": { ... }}
```

or

```json
{"ok": false, "error": "a sentence saying what went wrong"}
```

That is the whole interface. **108 commands**, all the same shape. Failures are
in the envelope too — a bad flag, an unknown market, a missing data folder — so
your UI never has to parse an exit code or read standard error to find out what
happened.

There is a test that keeps it true. `tests/serve_protocol_tests.py` runs the
economics commands against an empty data folder and fails on anything that is
not one clean envelope, and `tests/stdout_contract_lint_tests.py` reads the call
graph and fails on any `print()` that could reach standard output. The contract
is enforced, not merely intended.

---

## The four rules your UI must follow

These are not style preferences. Each one is a way people lose money.

**1. Do not rewrite the engine.** It is the brain, and it holds years of
decisions that look arbitrary until they are not — see
[review-2026-08-04.md](review-2026-08-04.md). Your UI is a viewer and a set of
buttons. Ask an agent to "build a Windows version of this app" and it may
cheerfully port the Python; that is the wrong answer.

**2. Never write the database from your UI.** Open it read-only if you read it
directly at all, and prefer the commands. The nightly job writes to those files
and you will fight it for locks.

**3. Never call the Amazon API from your UI.** Every write goes through
`appctl.py`, because that is where the safety rails live: the `KILL` file, the
economics gate, the snapshot-freshness gate, the bid ceiling, the volume cap,
the conflict guard, and the `writes_log` that makes a change undoable. A UI that
calls Amazon directly has none of those. Read [SAFETY.md](SAFETY.md) and believe
it.

**4. Never read or print `.env`.** It holds the Amazon client secret and refresh
token.

---

## Start here — the commands a dashboard needs

Every one of these only reads. None of them can change anything.

| Command | What you get |
|---|---|
| `markets` | which marketplaces are configured, and which have data |
| `metrics` | the headline: trailing-30 spend, sales, ACOS, plus a trend series |
| `periods` | the period stack — this month, last month, year to date, all time |
| `daily --days 30` | true per-day account totals, for a chart |
| `campaigns` | every campaign with its spend, sales and ACOS |
| `adgroups --campaign <id>` | one campaign's ad groups |
| `targets --adgroup <id>` | one ad group's keywords and targets |
| `killlist` | designs losing money, with the reason |
| `alerts` | anything that needs a human |
| `health` | per-market freshness — call this **without** `ADS_MARKET` |
| `audit --limit 100` | every change ever made, newest first |

Then the ones that change things, so you know what a button would call:
`pause`, `enable`, `setbid`, `setbudget`, `negate`, `undo`. Each returns what it
did and whether it applied.

[COMMANDS.md](COMMANDS.md) lists all 108 and marks each as read, live-read, or write.

---

## Two gotchas worth knowing before you start

**Numbers are fractions, not percentages.** `acos: 0.1816` means 18.16%. Format
it in the UI; do not multiply it into the data.

**A field that says what the data does NOT cover must be shown.** Several
replies carry one: `killlist.skipped`, `periods[].profit_note`,
`stream-today.coverage.missing_hours`. Each exists because a screen once read as
complete when it was not. If you drop them, your UI is confidently wrong in
exactly the way this one was. There is a whole section about this in
[../CLAUDE.md](../CLAUDE.md).

---

## A prompt to start from

Paste this into Claude Code, Codex, or whatever you use, from inside a clone of
this repository:

> I want to build a desktop UI for this project, for Windows.
>
> Read `docs/BUILD-A-UI.md`, `docs/COMMANDS.md` and `docs/SAFETY.md` first.
>
> Rules, and they are not negotiable:
> - Do not modify or reimplement anything in `engine/`. It is the brain. My UI
>   is only a viewer and a set of buttons.
> - Talk to it only by running `python3 engine/appctl.py <command>` and decoding
>   the single JSON object it prints.
> - Never open the SQLite files for writing, never call the Amazon API directly,
>   never read `.env`.
>
> Start small. Build one window that runs `markets`, lets me pick one, then runs
> `metrics` and `periods` for it and shows spend, sales, ACOS and estimated
> profit as cards, with a 30-day chart from `daily`.
>
> Show me that working before adding anything else.

Then grow it: the campaign list, the kill list, the alerts, and only after those
are solid, the buttons that write.

**Build the read-only screens first and live with them for a week.** The buttons
are the easy part and the dangerous part.

---

## What to build it in

Anything that can run a subprocess and parse JSON. The engine does not care.

Reasonable choices on Windows: a Tauri or Electron app, a small local web app in
Flask or FastAPI that you open in a browser, or plain WPF. A local web app is
usually the fastest to get working and the easiest for an agent to write.

If you build something good, a link in the issues would be welcome.
