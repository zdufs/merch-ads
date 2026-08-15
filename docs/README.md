# Documentation

Start with the row that matches what you are doing.

| I want to… | Read |
|---|---|
| Understand what this is | [../README.md](../README.md) |
| Install it | **[SETUP.md](SETUP.md)** |
| Get Amazon API credentials | **[api-access-setup.md](api-access-setup.md)** |
| Not lose money | **[SAFETY.md](SAFETY.md)** |
| Look up a command | [COMMANDS.md](COMMANDS.md) |
| Fix something broken | [TROUBLESHOOTING.md](TROUBLESHOOTING.md) |
| Understand the code | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Write my own automation | [rules-dsl.md](rules-dsl.md) |
| Contribute | [../CONTRIBUTING.md](../CONTRIBUTING.md) |

---

## For users

### Getting started

- **[SETUP.md](SETUP.md)** — zero to a working nightly run. Start here.
- **[api-access-setup.md](api-access-setup.md)** — the Amazon Ads API application,
  written for non-developers. **Allow days to weeks for Amazon's approval.**
- **[SAFETY.md](SAFETY.md)** — the kill switch and the five gates. Read before you
  automate anything.

### Daily use

- **[COMMANDS.md](COMMANDS.md)** — every command, marked read / live-read / write.
- **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** — when it breaks or goes quiet.
- **[multi-market.md](multi-market.md)** — running several marketplaces.
- **[packaging.md](packaging.md)** — building and installing the Mac app.

### Writing your own automation

- **[rules-dsl.md](rules-dsl.md)** — the rules language. This is how you make the tool
  follow *your* strategy instead of the built-in one.

---

## How it decides things

- **[bidding-rules.md](bidding-rules.md)** — the built-in bidding logic, per campaign
  type, including the lottery-campaign routing policy.
- **[royalty-reference.md](royalty-reference.md)** — Merch royalty maths per product type.
  Read this before trusting the profit figures in a new market.
- **[ads-automation-spec.md](ads-automation-spec.md)** — the original specification for
  the whole engine.
- **[scavenger-plan.md](scavenger-plan.md)** — the scavenger campaign strategy.

---

## For contributors

- **[../CONTRIBUTING.md](../CONTRIBUTING.md)** — ground rules that exist because breaking
  them cost real money.
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — how the engine, the database and the app fit
  together, and a reading order for the code.
- **[swiftui-app-plan.md](swiftui-app-plan.md)** — the Mac app's product specification.
- **[../DESIGN.md](../DESIGN.md)** — the app's visual design system.
- **[../PRODUCT.md](../PRODUCT.md)** — what the app is for and who it is for.

---

## History

- **[../CHANGELOG.md](../CHANGELOG.md)** — what changed and when.
- **[review-2026-08-04.md](review-2026-08-04.md)** — a full engine and app review, with
  what was found, what was fixed, and what was deliberately closed without code. The best
  single document for understanding the system's real trade-offs.
- **[changelog-2026-06-27.md](changelog-2026-06-27.md)** — an earlier snapshot.

---

## A note on the older documents

Some documents here were written as working notes for one operator and one AI coding
assistant, not as a manual. `claude-code-handoff.md` in particular is a build brief, and
`CLAUDE.md` in the repository root is an instruction file for Claude Code.

They are kept because they are accurate and detailed — often more detailed than the
polished docs. Just read them knowing they were addressed to someone who already had all
the context.
