# Merch Ads — Mac App (SwiftUI) Plan

A native macOS app (SwiftUI) that sits on top of the existing Python engine.
Mac-only, single user. v1 = **viewer + actions**.

## Architecture (Approach A)

- **SwiftUI = the window.** Reads the per-market SQLite DBs (`ads_data*.sqlite`) directly
  for all dashboards/lists — instant, native, no Python in the loop.
- **Python = the brain.** For anything that touches Amazon (live status, pauses, bids,
  building campaigns) the app runs the existing scripts in the background and reads back
  **JSON**. So the app and the nightly job always behave identically.
- **The contract:** give each script a `--json` mode that prints a structured result.
  New thin entry points where needed (e.g. `metrics.py --json`, `actions.py`). Secrets
  stay in `.env` / Python — Swift never sees credentials.
- **Safety is inherited:** preview-before-write, the `KILL` freeze file, and `writes_log`
  already exist. Every action the app fires goes through them.

---

## ESSENTIAL (v1)

1. **Per-market dashboard** — market switcher (US/UK/DE/FR/ES/IT) + an "All markets"
   rollup. KPI cards: spend, ACOS, CVR, orders, sales for **daily / 7-day / month**.
   - Headline ACOS = **7-day rolling** (not the attribution-lagged single day), with the
     freshest day flagged "still settling." Directly fixes the "66% ACOS" scare.
   - Trend charts (spend / ACOS / sales over time) from the stored snapshots.

2. **Live status lookup** — search any ASIN or campaign → **Refresh from Amazon** pulls
   real-time state via the API (the `status.py` logic), shown next to cached 30-day perf.
   Lists every ad group an ASIN runs in, across campaigns, with live state + current bid.

3. **Campaign browser** — browse by type (Standard / Lottery / Scavenger /
   Harvested), filter by state, sort by spend / ACOS / CVR. Drill: campaign → ad group →
   targets → search terms. Each bid shows its **bid-change history timeline** (from
   `writes_log`) — so the daily-vs-weekly bid story is visible at a glance.

4. **Actions panel (with safety)** — every write previews first, asks to confirm, respects
   `KILL`, and logs to `writes_log`:
   - Pause / enable ad groups & campaigns.
   - Manual bid edit (single or bulk) + one-click "reset inflated bids."
   - Add negative keywords (ad-group or campaign level).
   - Trigger a run on demand — whole daily job, a single market, or one phase.

5. **Approval queue** — review what the automation *wants* to do (proposed negatives,
   pauses, bid moves) and **approve / reject** before it applies — plus a log of what was
   auto-applied on the last run. Turns "the robot surprised me" into "I clicked yes."

6. **New-design intake (your CSV example)** — see detailed flow below.

7. **Settings** — edit the knobs you kept wanting to tune, no code:
   - Per-market economics (royalty, break-even, target ACOS, tee price caps).
   - Strategy knobs: CVR floor (8%), negative threshold (10 clicks), bid step (10%),
     weekly review day (Mon), lottery clause bids, scavenger bid/budget.
   - Schedule (run time, Seattle TZ) and a **KILL toggle** (freeze all writes).

8. **System health** — last successful pull + last run status per market, recent errors
   (from `pull_log` / launchd logs), API token + rate-limit health. One glance = "is the
   nightly job OK?"

---

## NEW-DESIGN INTAKE — the CSV → campaigns flow (your example, fleshed out)

Goal: replace the daily manual "I uploaded new designs, now add them to campaigns" step.

1. **Drop a file** (drag-drop or pick) — your product-grid export of new ASINs.
   Snap for MOD: Products tab → select the new products → three-dots menu (⋮) →
   Export selected data → Export full data (CSV), which saves
   `snap-grid-export-*.csv`. An older MerchFlow `export_products_*.csv` is still read.
2. **Parse + dedup** — cross-reference the DB to keep only ASINs **not already advertised**
   (same idea as the MerchPirate import idempotency we discussed).
3. **Classify by product type** and route automatically:
   - Standard tees → **Lottery** (new ad-group-per-ASIN in the right 500-shard) **and**
     **Scavenger Tees**.
   - Hoodies / sweatshirts → **Scavenger** (matching cohort).
   - Drinkware / hats → **Scavenger**, using the **ad-safe ASIN** (not the retail ASIN).
4. **Preview** — "12 new: 9 tees → Lottery shard 3 + Scavenger Tees; 3 hoodies → Scavenger
   Hoodies." Nothing built yet.
5. **Approve → build** — calls `lottery_build` / `scavenger_build` scoped to those ASINs;
   logs everything; shows results.
6. Optional: **watch a folder** so dropping the daily export in auto-queues it.

---

## GOOD TO HAVE (v1.x)

- **Native notifications** — ASIN crossing ACOS/CVR thresholds, campaign maxing budget,
  chronic-dead campaign retired, unusual spend spike.
- **"Designs to kill" screen** — live list of CVR < 8% **and** ACOS over break-even (the
  exact analysis you asked for), with one-click pause. Plus a **stale-design finder**.
- **Export** any view to CSV / PDF (e.g. the low-CVR tee list).
- **Weekly bid-change report** — what moved up / down / held on Monday.
- **Harvest review** — winning search terms ready to promote to manual campaigns, approve
  in one click.

## FUTURE / NICE

- In-app **MerchPirate demand-feed** view + import.
- **Profit modeling** (royalty-aware true margin, not just ACOS) and what-if bid sims.
- **Pricing reminders** (no API for price, but reminders: "raise price after N sales").
- **csmetro playbook checks** surfaced as gentle nudges (start tees $14.99, kill <8% CVR,
  turn off women's fit, DeepL EU listings).
- Phone access later (would mean a hosted backend — out of scope for the Mac-only v1).

---

## SAFETY MODEL (because v1 has actions)

- Every write: **preview → confirm → log**. Bulk actions show counts + a sample first.
- `KILL` toggle in the UI freezes all writes instantly.
- Full **audit trail** screen (reads `writes_log`) with one-click rollback where supported
  (pauses, bid changes already have rollbacks).
- Actions never invent data — they call the same Python that runs on schedule.

## PROPOSED BUILD ORDER

1. Xcode project + SQLite read layer + market switcher + dashboard (read-only first).
2. `--json` modes on the Python scripts (status, metrics, actions, import).
3. Live status lookup + campaign browser + bid history.
4. Actions panel + approval queue + audit/rollback.
5. New-design CSV intake.
6. Settings + system health.
7. Good-to-have: notifications, kill-list screen, exports.

## DECISIONS I NEED FROM YOU

- Trim or reorder the v1 essentials — anything here you'd cut or must-have sooner?
- For actions: always require a confirm click, or allow a "trusted" one-click for the
  small stuff (single pause/bid) and confirm only for bulk?
- New-design intake: drag-drop only, or also watch a folder for your daily export?
