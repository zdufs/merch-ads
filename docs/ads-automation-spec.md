# Amazon Ads Automation — Spec & Architecture (BulkFlow replacement)

> The original design document. Written 2026-06-15, before any code existed, and kept
> for the reasoning rather than as current documentation.
> Goal: replace a retired bulk-file tool with an API-driven one that manages Merch
> Sponsored Products bids, budgets and keywords automatically.

---

## 1. What you asked for

API-driven (not bulk-file). Five jobs the tool must do automatically:

1. **Bid adjustments** — raise/lower keyword & target bids toward an ACOS goal.
2. **Pause losers** — stop keywords/targets that spend with no (or bad) return.
3. **Search-term harvesting** — find converting search terms in auto/broad campaigns, promote them to exact match.
4. **Budget management** — push budget to winners, cap losers.
5. **Negative keywords** — block search terms that spend without converting.

Deliverable today: this spec. Build starts only after you sign off.

---

## 2. The one thing that gates everything: API access

Both architecture options below need the **same** thing first — **active Amazon Advertising API credentials**. This is the real bottleneck, not the code.

What that involves, in plain terms:

- You register a developer application with Amazon Ads and get approved. Approval is a review process (can take days to a couple of weeks; sometimes needs a back-and-forth explaining what you're building). Solo creators do get approved, but it's not instant.
- Once approved you get five credentials: a **Client ID**, **Client Secret**, **Refresh Token**, **Access Token** (auto-expires every 60 min and is auto-renewed from the refresh token), and your **Profile ID** (your Merch advertising account).
- These are the keys the tool uses to read reports and change bids. They're sensitive — treat them like banking passwords.

**Action before anything else:** confirm you can get API access for your Merch advertising account. If you advertised via BulkFlow, your account already runs Sponsored Products, so you're eligible — but BulkFlow may have been a bulk-file tool that never needed API keys. Getting *API* access is a separate step. This is open question Q1 below.

---

## 3. Architecture: two real options

As of **Feb 2026** Amazon released an **official Amazon Ads MCP Server** (open beta, worldwide) that plugs straight into Claude. That gives us a genuine build-vs-use choice that didn't exist when BulkFlow was around.

### Option A — Custom Python tool (the "BulkFlow rebuild")

A Python program on your Mac Mini M4 that talks to the Ads API directly, runs on a schedule, applies your rules deterministically, logs every change, and notifies you.

- **Pros:** Fully autonomous (set rules, walk away). Deterministic — same inputs always give same decisions. Complete audit log. Runs unattended on the always-on Mini. No per-action approval needed once trusted. Fits your existing pattern (kdp-factory, MerchPirate).
- **Cons:** More to build and maintain. You own the logic and the bugs.

### Option B — Official Amazon Ads MCP Server + Claude

Connect the Amazon Ads MCP to Claude; manage campaigns conversationally ("lower bids on anything above 45% ACOS this week").

- **Pros:** Almost no code. Official, maintained by Amazon. Flexible — ask anything in plain English.
- **Cons:** **Not autonomous** — it's a chat assistant, not a scheduled robot. You'd be in the loop each run. Still needs the same API credentials. Less repeatable; an LLM can make different calls on different days. Weaker as a hands-off "run nightly and tell me what changed" system.

### Recommendation

**Hybrid, leaning A.** Build the custom Python tool for the **deterministic, scheduled, money-moving work** (bids, pausing, budget, negatives) where you want repeatable rules and a clean audit trail and zero babysitting. Keep the **Amazon Ads MCP connected to Claude for ad-hoc exploration** ("why did campaign X spike yesterday?") where conversation beats code.

Rationale: bid/budget changes are real euros moving nightly. You want rules you can read, results you can audit, and a system that runs without you. An LLM-in-the-loop is the wrong tool for the recurring money decisions, but the right tool for investigation. This matches your heavy-automation bias.

The rest of this spec assumes **Option A** as the build, with B as a companion.

---

## 4. How the data flows (Option A)

```
                ┌─────────────────────────────────────────────┐
                │   Mac Mini M4 (always-on)  —  nightly cron   │
                └─────────────────────────────────────────────┘
                                   │
   1. PULL          ┌──────────────┴──────────────┐
   reports  ───────▶│  Amazon Ads API (Reporting   │
                    │  v3): SP campaign, keyword,   │
                    │  search-term, targeting       │
                    └──────────────┬───────────────┘
                                   │
   2. STORE                        ▼
   history  ───────▶  Local SQLite DB (every metric, every night)
                                   │
   3. DECIDE                       ▼
   rules    ───────▶  Rules engine (bids / pause / harvest / budget / negatives)
                                   │
   4. STAGE                        ▼
   changes  ───────▶  Proposed-changes list  ──(dry-run report)──▶ you
                                   │
   5. APPLY                        ▼   (after trust window / auto)
   writes   ───────▶  Amazon Ads API (update bids, state, budgets, add keywords/negatives)
                                   │
   6. NOTIFY                       ▼
            ───────▶  scripts/notify.py  →  "Tonight: 14 bids down, 3 paused, 2 harvested, €X projected save"
```

Why local SQLite history matters: good bid decisions need *trend*, not one day. Amazon reports are noisy day-to-day. Keeping your own rolling history (you already use SQLite in MerchPirate) lets rules look at 7/14/30/60-day windows and ignore one-off spikes.

---

## 5. Decision rules (the actual logic)

All thresholds below are **starting defaults** — every one is a config value you can tune. Numbers assume a target ACOS; set yours in Q3.

Let **target ACOS = T** (e.g. 40%). Royalty-model note: on Merch you don't control price, so ACOS/royalty math is your true margin guardrail — break-even ACOS = royalty ÷ price-equivalent. We'll set the real break-even once you give me your typical royalty (Q3).

### 5.1 Bid adjustments
Look at each keyword/target over a rolling window (default 14 days) with enough data (default ≥ 10 clicks OR ≥ 1 order before acting):

- **ACOS well above T and it has orders** → lower bid. Step = move partway toward the bid that *would* hit T (default 50% of the gap), capped at −15% per run so nothing swings wildly.
- **ACOS comfortably below T with orders** → raise bid to win more volume. Default +10% per run, capped at a max bid ceiling.
- **High spend, zero orders** (default: spend ≥ 2.5× target CPA with 0 orders) → this is a *pause/negative* candidate, not a bid cut (see 5.2 / 5.5).
- **Thin data** (few clicks, no orders) → left untouched; let it gather data up to a spend cap, then treat as a loser.
- Never move more than X% of keywords in one night (default 25%) — prevents the whole account lurching.

### 5.2 Pause losers
- Keyword/target with **0 orders** and **spend ≥ kill-threshold** (default €X = ~2.5× your acceptable cost-per-sale) over the window → pause.
- Keyword with **ACOS persistently > 2×T** across two consecutive windows despite bid cuts → pause.
- Pausing is reversible and logged; nothing is deleted.

### 5.3 Search-term harvesting
- In auto / broad / phrase campaigns, find **customer search terms** with **≥ N orders** (default 2) **and ACOS ≤ T**.
- Promote each to an **exact-match keyword** in a dedicated "harvested winners" ad group / campaign, with a starting bid derived from its proven CPC.
- Simultaneously add it as a **negative exact** in the *source* auto/broad campaign so the two don't cannibalize each other and you stop paying discovery prices for a known winner.

### 5.4 Budget management
- **Winner campaigns** (ACOS ≤ T and hitting/near daily budget = "budget-constrained") → raise daily budget (default +20%, capped).
- **Loser campaigns** (ACOS ≥ 1.5×T) → cut budget toward a floor, or down to minimum if no orders.
- Optional **account spend cap**: a hard ceiling so total daily spend never exceeds €Y regardless of rules (Q4).

### 5.5 Negative keywords
- Search term with **spend ≥ negative-threshold** (default ~1.5× cost-per-sale) and **0 orders** → add as **negative exact** in that campaign.
- Search terms that are clearly irrelevant (you can maintain a blocklist of words) → negative immediately.
- Negatives are added at ad-group or campaign level depending on scope; all logged.

---

## 6. Safety / guardrails (this moves real money)

Non-negotiables baked into the build:

- **Dry-run first.** Phase 1 only *proposes* changes and emails you the list. Nothing writes to Amazon until you approve a trust window. Matches your "explicit sign-off before anything ships" rule.
- **Per-run caps.** Max % of keywords changed, max bid swing per run, max budget increase per run, hard account daily-spend ceiling.
- **Full audit log.** Every change (old value → new value, the rule that fired, timestamp) stored in SQLite and summarized in the notify message. One-command rollback of the last run.
- **Data minimums.** No decision on thin data; spend caps before anything is judged a "loser."
- **Idempotent + safe re-runs.** If a run is interrupted it can resume without double-applying.
- **Credentials** stored in a local secrets file / keychain on the Mini, never in the repo, never logged.
- **Kill switch.** Single flag to halt all writes (revert to dry-run) instantly.

---

## 7. Deployment

- Lives in `~/Biznis/ClaudeCode/POD/Ads/` (new repo, sibling to kdp-factory / MerchPirate).
- Python, SQLite (consistent with your stack).
- Runs on the **Mac Mini M4** via `launchd`/cron, nightly after Amazon's reporting day closes (reports lag a few hours, so a late-night run reads yesterday clean).
- Notifications through your existing `scripts/notify.py` dispatcher.
- US + UK marketplaces = two profiles; tool loops over both.

---

## 8. Phased rollout (de-risked)

| Phase | What it does | Risk | Exit criteria |
|-------|--------------|------|---------------|
| **0. Access** | Get Ads API credentials approved; connect, pull one report, store it. | None (read-only) | Reports landing in SQLite nightly. |
| **1. Observe** | Run full rules engine in **dry-run**: produces nightly "here's what I *would* change" report. No writes. | None | 1–2 weeks of proposals that look sane to you. |
| **2. Assist** | Apply **low-risk** writes only (negatives, pausing zero-order losers). Bids/budgets stay proposal-only. | Low | Spend on dead terms drops, no surprises. |
| **3. Autopilot** | Enable bid + budget + harvesting writes with caps. Nightly summary. | Medium | ACOS holding/improving at target; you trust the log. |
| **4. Tune** | Adjust thresholds from real results; add per-niche overrides if needed. | — | Ongoing. |

You never hand it the keys to your spend on day one. It earns trust in dry-run first.

---

## 9. Open questions (need your answers before/at build)

1. **API access** — Can you get / do you already have Amazon Ads **API** developer credentials for your Merch advertising account? (BulkFlow may not have used the API.) This gates Phase 0.
2. **Scope** — One Merch advertising account, US + UK profiles, correct? Any KDP ads (separate vendor account) in scope, or Merch only for v1?
3. **Targets & economics** — Your **target ACOS**, typical **royalty per unit** (for break-even), and acceptable **cost-per-sale**? These set every threshold in §5.
4. **Spend ceiling** — Hard maximum total daily ad spend you never want exceeded?
5. **Autonomy comfort** — Happy with the dry-run → assist → autopilot ramp, or do you want it to stay proposal-only permanently (you click "apply")?
6. **Companion MCP** — Want me to also set up the official Amazon Ads MCP with Claude for ad-hoc questions, or skip it for now?

---

## 10. Automation opportunities I'd flag

- **Tie ads to MerchPirate uploads:** auto-create a starter campaign when a new ASIN is uploaded, so new designs get ad exposure without manual setup. (Phase 5 idea.)
- **Feed `niche_performance.py`:** join ad data with your per-niche royalty report so bidding can favor niches that actually convert, not just keyword-level ACOS.
- **Weekly digest:** a Monday email — top movers, what got paused/harvested, spend vs target — as a scheduled task, so you read one summary instead of logging into the console.

---

### Bottom line
The code is the easy part. The real first step is confirming **API access** (Q1). Recommend: custom Python tool on the Mini for the nightly money decisions, official Ads MCP + Claude alongside for investigation, and a dry-run-first rollout so it earns trust before it spends. Tell me your answers to §9 (especially Q1 and Q3) and I'll move to the build.
