# PRODUCT.md

## What this is
MerchAds — a native macOS (SwiftUI) control room for a Merch-on-demand Amazon Ads
operation across 6 markets. Single operator, daily use. The Python engine in this
folder is the brain (nightly automation); the app is the window and the control point.

## Register
product — design serves the task. macOS-native, restrained, information-first.
The reference tools are Xcode's reports navigator, Linear, and Numbers: dense tables,
system typography, semantic color only where it carries state.

## Users & scene
One operator, at a desk on a Mac, checking the morning run over coffee and making
surgical interventions (pause this, approve that, raise a price). Light mode or dark
per system setting — always follow the system appearance.

## Design system
- System fonts (SF Pro; SF Rounded for large numerals). No custom fonts.
- System semantic colors only. Meaning-carrying accents: green = money earned / healthy,
  red = losing money / danger, orange = warning / needs attention, yellow = data still
  settling. Never decorative color.
- ACOS coloring convention (used everywhere): > 30% red, > 22% orange, else default;
  green only when explicitly "profitable".
- Money is always in the market's own currency; percentages from fractions (0.18 = 18%).
- Tables are the primary surface: resizable, reorderable, sorted, exportable.
- "Settling" concept: the freshest ~2 days under-report sales (30-day attribution) —
  always flagged, never hidden.

## Voice
Plain, specific, no marketing tone. Labels say what a thing is ("Freeze all writes
(KILL)"), captions explain consequences in one sentence.
