# Scavenger Campaigns — Build Plan (review before coding)

**Goal:** add the one genuinely new structure from the playbook — *scavenger campaigns* — a wide
net of **broad-match keywords at rock-bottom 5¢ bids** across a big list of ASINs, to scoop up
cheap clicks the lottery and harvest campaigns miss. Built to run hands-off inside the 6am job.

Your locked decisions:
- **Keywords:** Amazon's own suggested keywords (true discovery of new cheap clicks).
- **ASINs:** live US standard tees that already have **≥1 sale** (proven organic demand).
- **Management:** fully automated in the daily job (build, prune, report — no manual step).

### Exact settings (from the video)
- **Default bid:** $0.05 (forced onto every keyword, incl. later-added suggestions).
- **Daily budget:** $5/day to start ("usually $5 or $10" → lower, safer end).
- **Bidding strategy:** dynamic bids **up & down**.
- **Match type:** **broad only** (drop the auto "keywords related to your category" — it overspends).
- **Keywords:** Amazon's suggested keywords, "add all" → ~200 per campaign, all at $0.05.
- **ASINs:** live US `standard_tshirt`, `salesTotal > 0`, ≤1000 per campaign, **US market first**.
- **Pruning:** "turn off keywords wasting clicks with no sales" → uses your existing
  **per-product-type stop-loss** thresholds (no new number invented).

---

## How scavenger differs from what you already run
| | Lottery (have) | Harvest / TAMAS (have) | **Scavenger (new)** |
|---|---|---|---|
| Targeting | Auto | Manual, known winners | Manual, **broad keywords** |
| Bid | ~15¢ fixed | break-even / test | **5¢, dynamic up&down** |
| Purpose | cast wide (auto) | scale proven terms | **scavenge cheap clicks** |
| ASINs | 1 per ad group | 1 per campaign | **many per campaign (≤1000)** |

It's a *discovery* layer: cheap, broad, high volume of keywords, low spend per click.

---

## How it will work

**1. Build / refresh (daily)**
- Pick the cohort: live US `standard_tshirt`, `salesTotal > 0` (from the export + DB).
- Batch into campaigns of ≤1000 ASINs each (API/UI limit). Name prefix `SCAVENGER - `.
- Each campaign: manual targeting, bidding = **dynamic up & down**, budget **$5/day**,
  one ad group, default bid **$0.05**.
- Add all cohort ASINs as product ads (skip ones already in the campaign).
- **Keywords:** call Amazon's *keyword recommendations* endpoint for the ASINs, dedupe, add the
  top ~200 as **broad** match at 5¢. Re-pull periodically so new suggestions get added (always at 5¢).

**2. Prune (daily)**
- Any scavenger keyword with **≥ N clicks and 0 orders** and spend over its per-type stop-loss
  → pause that keyword (reuses your existing per-product-type thresholds).
- Chronic-dead guard ("don't marry it"): if a whole scavenger campaign runs weeks with spend and
  near-zero conversion, flag it in the digest for retirement.

**3. Report**
- New "Scavenger" line in the Discord digest + a tab/section on the dashboard
  (spend, clicks, orders, ACOS, cheapest converting terms found).

**4. Stay out of each other's way**
- Scavenger campaigns are **excluded from phase2/3/4 and TAMAS logic** (same way TAMAS is excluded),
  managed only by their own prune rule. Tagged by the `SCAVENGER - ` name prefix.

---

## Self-competition (the honest caveat)
Scavenger broad keywords overlap your lottery's auto-targeting on the same ASINs. The video calls
this "fuel on the fire," and in practice a 5¢ bid rarely wins a contested auction, so overlap is
minimal — but if you see lottery CPCs rise after launch, that's the signal, and we throttle.

---

## New / changed files
- `scavenger.py` — config + helpers (`PREFIX`, `DEFAULT_BID=0.05`, `BUDGET`, `MATCH=broad`,
  `BIDDING=dynamic up&down`, `MIN_CLICKS`, `is_scavenger()`).
- `scavenger_build.py` — build/refresh campaigns, product ads, suggested keywords (gated: preview → `--apply`).
- `scavenger_optimize.py` — prune wasteful keywords + chronic-dead flag (gated).
- `ads_client.py` — add `get_keyword_recommendations()`.
- `phase2/3/4` + dashboard + `notify_discord.py` — add scavenger exclusion + reporting.
- `run_scheduled.sh` — add `scavenger_build.py --apply --auto` and `scavenger_optimize.py --apply --auto`
  (kill-switch gated, like everything else).

## Safety
Same as the rest: preview-by-default, `--apply` + typed `APPLY` for manual runs, every write logged
to `writes_log` (reversible), `touch KILL` freezes it. First live run I'll watch with you before it
auto-scales.

## One thing to verify on your Mac (I can't from here)
The sandbox can't reach Amazon's API, so I need to confirm the **keyword-recommendations endpoint**
returns suggestions for Merch ASINs. If it doesn't, fallback is broad keywords derived from your
ASIN product titles + proven converting terms. I'll test this first thing when we build.

---
**Settings locked to the video.** Per his method (one market at a time), we start with a single
US batch (≤1000 ASINs) as the live test, watch it, then roll wider.
