# Scavenger / cohort winner promotion — design

Date: 2026-08-14
Status: approved (design), pending implementation plan

## The problem

Harvest collects converting search terms as "winners." `phase4_harvest_create.py`
promotes each winner into its own exact-match keyword on the source **design**.
To do that it needs one ASIN. It reads the source ad group's ASIN and, if there
is none, skips the winner (`build_plan`, the `if not asin: skipped_no_asin` path).

Scavenger and AUTO campaigns are **multi-design cohorts** — one ad group holds
many unrelated designs, so `ad_group_product.asin` is NULL for them. Every winner
that comes from a cohort is therefore skipped, every night, forever. It sits in
the Harvest tab as "pending" and never resolves. Today there are 4 such US terms,
stuck for weeks (e.g. "rookie of the year first birthday outfit", "st michael t
shirt"). `scavenger_optimize.py` even says winners are "promoted to focused manual
campaigns by the normal harvest step" — but for cohorts that step cannot run.

The winners already promoted (134 of 137) came from single-design ad groups and
are correct — one design in, so the keyword landed on the right shirt. Only the
cohort path is broken, and it fails by *skipping*, never by mis-firing. So the
existing promoted list is trustworthy; the only failure mode is "never promoted."

### Why it can't just auto-fix

A cohort winner should NOT target one design (misses the rest of the family) and
should NOT target the whole grab-bag (pulls in unrelated designs — a "st michael"
search would serve a Foo Fighters shirt). The right target is the **family** of
related designs the phrase belongs to — e.g. all 40 "… of Rookie 1st Birthday"
relative variants (daddy, mama, aunt, uncle, grandpa…).

The engine cannot reliably find that family on its own. Naive title matching
mis-groups: matching "michael" also grabs *Carmichael* and *Michaela* name shirts;
matching "foo" + "retro" grabs *Retro Football* (because "football" contains
"foo"). Only the operator knows "st michael" means the archangel designs. So the
**operator confirms the design set**; the engine only suggests it.

## Goal

Let the operator promote a cohort winner to its correct **family** of designs. The
engine suggests the family from the whole catalogue; the operator adds or removes
designs; then the engine creates a focused ad group with those designs plus the
exact keyword, negates the phrase in the source cohort, and marks the winner done.

## Decisions (locked with the operator)

1. Suggestions search the **whole catalogue**, not just the cohort's members — the
   family is larger than what happened to be in the grab-bag.
2. After promoting, **negate the phrase in the source cohort** campaign, so the
   proven phrase funnels into the focused campaign instead of being paid for twice.
3. **Reuse** the existing "Harvested &lt;type&gt; - Exact" campaign; add **one new ad
   group per promoted phrase** (per product type when the chosen designs span more
   than one type — see Edge cases), holding the chosen designs + the exact keyword.
4. New keyword **bid = phrase CPC × 1.15** (same as the existing promoter).
5. "Cohort" = **Scavenger AND AUTO** sources (both are asin-NULL grab-bags). Fix both.
6. The promote is a **live account write → operator-run** (an app button), routed
   through the engine's safety rails. It never auto-fires. Cohort winners are
   never auto-promoted (the suggestions are not trustworthy enough).

## Components

### 1. Suggestion engine (read-only)

New read endpoint: `appctl.py harvest-suggest --term "<phrase>"`.

Ranking:
- Tokenize the phrase; drop generic words (`t`, `shirt`, `tshirt`, `tee`, `the`,
  `of`, `for`, `a`, `and`, `outfit`, `design`, …).
- Match each meaningful token against catalogue design titles by **whole word**
  (token equality), never substring — this is what excludes *Carmichael* from
  "michael" and *Football* from "foo".
- Score = number of shared meaningful tokens; rank descending, tie-break by
  lifetime sales.
- Titles come from `ad_groups.name` (encodes `ASIN_type_Title`) and/or
  `sales_report_rows.title`.
- Return the top N (~50) rows with score &gt; 0:
  `{asin, title, product_type, matched_words, score, lifetime_sales, in_source_cohort}`.
  The operator can also search-and-add any design beyond the list.

### 2. Confirm screen (Harvest tab)

- A new **"Needs a design"** section listing cohort winners: harvest winners whose
  source ad group is a Scavenger/AUTO cohort (NULL asin). These are shown here,
  separate from the normal single-design auto-promote pending, so they no longer
  read as "stuck in the auto path."
- Per winner: the phrase, its orders / sales / ACOS, and a checklist of suggested
  designs (title + thumbnail), pre-ticked above a score threshold. The operator
  unticks wrong ones and can search-add others.
- Winners that look **trademark or sensitive** (a small keyword flag list, e.g.
  band names, suicide/self-harm) get a ⚠️ marker and are never pre-ticked — the
  operator decides.
- A **Promote** button with a confirm dialog (it is a live write).

### 3. Promote action (live, operator-run)

New action endpoint: `appctl.py harvest-promote-group`, stdin
`{term, source_ad_group_id, source_campaign_id, asins:[…]}`.

Steps (all through `ads_client`, honoring KILL, econ gate, writes_log, max-bid
ceiling — exactly like `phase4`):
1. Group the chosen designs by `product_type`. One ad group per type, under the
   matching "Harvested &lt;type&gt; - Exact" campaign (reuse or create the campaign).
2. Create the ad group, add the chosen designs as product ads, add the phrase as
   an **exact** keyword (bid = phrase CPC × 1.15, clamped by the ceiling).
3. Add a **negative-exact** for the phrase in the source cohort ad group.
4. Mark `harvest_log.promoted = 1` for `(term, source_ad_group_id)`; log
   `harvest_promote` writes.
- Idempotent: if the phrase already exists as a keyword in the target ad group
  (Amazon duplicate), skip creation but still mark promoted.
- A **preview / dry-run** path returns the plan and writes nothing (for verifying
  before the live write).

### 4. De-nagging

Once promoted, the winner leaves the list (`promoted = 1`). Until then, cohort
winners live only in the "Needs a design" section — not the auto-promote pending —
so the operator sees them as "waiting on my decision," not "silently broken."

## Edge cases

- **No good match** (e.g. "suicide prevention shirt" has no matching design): the
  suggestion list is empty or weak; the operator adds designs by hand or skips.
  Never auto-promoted.
- **Mixed product types** among chosen designs: group by type, one ad group per
  type under that type's Harvested-Exact campaign.
- **Trademark / sensitive** phrases: flagged, operator decides (e.g. "foo fighters"
  may be better negated than promoted; the operator can just not promote it).

## Testing

- Suggester ranking unit tests, with the real failure cases as fixtures:
  "st michael" ranks *Saint Michael* above *Carmichael* / *Michaela*;
  "rookie … birthday" returns the Rookie family; "foo fighters" surfaces *FOO
  Retro*; whole-word not substring; stopwords dropped.
- Promote-group plan-builder unit tests: groups by type, bid math, negation
  target, mark-promoted, idempotent duplicate skip.
- Dry-run returns the plan with no writes; tests mock `ads_client` (no live calls).

## Success criteria

- The 4 stuck US winners appear under "Needs a design" with sensible suggestions.
- The operator can promote one to a chosen family: the dry-run shows the correct
  focused ad group + keyword + source negation; the live promote (operator-run)
  creates it, negates the source, and the winner leaves the list.
- Nothing auto-fires; every live write is operator-run and logged to the Audit Trail.

## Out of scope

- Auto-promotion of cohort winners (explicitly rejected — suggestions are not
  reliable enough; the operator confirms).
- Automatic family/cluster detection beyond title-word ranking.
