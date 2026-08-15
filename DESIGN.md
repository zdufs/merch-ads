# Merch Ads — Visual Language (frozen at the Phase 1 gate, 2026-07-11)

Approved on the redesigned Dashboard (PLAN.md Phase 1; operator sign-off after one
Codex rework round + four direct tweaks). Phase 2 batches apply THIS language —
deviations need a new gate decision, not taste-in-the-moment.

## Tokens (single source of truth — never inline styles)
- **Colors**: `Theme.Colors.*` only; all semantic/adaptive, tuned dark-first.
  Campaign types: lottery purple · scavenger orange · tamas blue · harvested green ·
  standard gray. States: enabled green · paused orange · archived muted.
- **ACOS is two-tier** (`AcosTier.select`): without economics → neutral tiers
  (comfort = primary, elevated = teal, high = blue — informational, NO good/bad
  claim); with per-row economics (`break_even`, royalty ROI) → profitable green /
  unprofitable red. Never color a bare ACOS red/green.
- **Spacing**: `Layout.Spacing` (4/8/12/16/24/32). **Radii**: `Layout.Radius`
  (8/12/16/24). No magic paddings.
- **Type**: `Typography.*`; every numeric cell uses the table-numeral style
  (`.monospacedDigit()` centralized). Hero numbers via `Typography.metricNumber`.

## Chart rules (`ChartStyle.merchAdsChartStyle`)
- Palette: sales mint · spend indigo · profit green · loss red; grid =
  `chartGrid` (separator @ 0.65).
- Y-axis labels: **concrete `Color.primary`** (hierarchical .secondary picked up a
  series tint in dark mode — keep the concrete color).
- Heights are FLOORS (`Layout.ChartHeight`), charts scale with viewport
  (Dashboard: daily = 34% of viewport clamped 240–620; monthly = 26% clamped
  190–460). `.frame(minHeight:maxHeight:.infinity)` in the style.
- Legend top-trailing; plot background `surface @ 0.35`; hover = per-chart typed
  overlay + the shared caption readout line above the chart.
- Monthly bars: `width: .fixed(18)`, grouped by series.

## Layout rules
- Screen = status first: KPI band (glass cards) + alert-pill strip above the fold,
  then content under a Divider in a scroll view.
- Two-column content grid at ≥820pt (63/37 via `DashboardColumnsLayout`), single
  column below. Primary charts left, lists/meta right.
- No in-content page titles: `navigationTitle` + `navigationSubtitle`
  ("US · data through 2026-07-10") carry identity. Section headers via
  `SectionHeader` (title + lowercase caption subtitle).
- Liquid Glass on chrome and KPI cards (`GlassEffectContainer` + `glassEffect`);
  readability beats glass on dense data surfaces.

## Honesty rules (data presentation)
- `dataAsOf` (engine date) and `fetchedAt` (client clock) are NEVER conflated;
  sections without an engine date say "evaluated HH:MM".
- Missing data renders as "—" + an explanatory caption (e.g. "no royalty data
  banked for this market"), never as a colored zero.
- Deltas render only from a complete 60-consecutive-day baseline; otherwise
  "delta hidden · incomplete 60-day history". Delta chips: red/green by
  improvement direction (spend up = red — operator-confirmed).
- Stale data (≥3 days) gets a caution pill, not silence.

## Components (use these, don't re-roll)
`StatCard` `AlertPill` `StatusBadge` `SectionHeader` `MoneyText/PercentText/
CountText` `FilterBar` `LoadableView` (only: All Markets, Profit, Bid report,
Demand feed) `ChartTooltip`. Status is never color-alone — badges keep text/symbol;
new components ship with accessibility labels.

## Addendum 2026-08-05 — Liquid Glass is OUT (decision recorded)

The 2026-07-11 spec above mandates `GlassEffectContainer` + `glassEffect` on
chrome and KPI cards. The implementation moved to the MerchDash-style flat
light cards (`.mdCard()`) instead, and the 14 `GlassEffectContainer` wrappers
that remained had no `.glassEffect` children — inert scaffolding costing a
layout pass each. Removed 2026-08-05 (zero visual change). The flat card
treatment is the current design of record; re-adopting glass is a deliberate
future decision, not a migration debt.

Appearance (updated 2026-08-14): the app no longer forces light. Every
`Theme.Colors.*` token now carries a light hex and a dark hex, resolved per
appearance by `Color(light:dark:)` (see `Design/ColorHex.swift`). The dark set
keeps the MerchDash identity — a cool near-black canvas, a one-step-lighter card
surface held apart by its hairline border, and the semantic + chart series
colors lifted one weight so they read on dark instead of muddying. The operator
chooses **System / Light / Dark** in Settings → Appearance (`AppAppearance`,
stored under `AppSettings.appearanceKey`); it feeds `.preferredColorScheme` at
the three scene roots (main window, Settings, menu-bar panel). Default is
System. The earlier "forced light is deliberate" note is superseded; light mode
is unchanged (the light hexes are the original fixed values).
