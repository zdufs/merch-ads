# Demand Feed — Context Pack for Codex

Supplementary context for building the MerchPirate "Demand Feed" intake. Pairs with
`merchpirate-codex-brief.md` (the task) and `demand_feed.schema.json` (the formal contract).

## File facts
- **Path (Mac):** `~/Biznis/ClaudeCode/POD/Ads/outputs/demand_feed.json`
- **Cadence:** regenerated every ~2 days by a launchd job. Each file overlaps the last → **dedup by `term` (seeds) and `asin` (proven) is required**.
- **Current volume:** ~58 keyword_seeds + ~60 proven_sellers. Expect it to grow as more designs sell. Build for hundreds, not tens.
- **Encoding:** UTF-8. Terms/titles may contain non-Latin text (e.g. Japanese), emoji, apostrophes — handle as plain unicode strings.

## `product_type` — full known value set (Merch export vocabulary)
Map each to MerchPirate's internal blank/product. Most volume is `standard_tshirt`. Unknown → default `standard_tshirt`.

| product_type | human label | typical use |
|---|---|---|
| standard_tshirt | Standard Tee | bulk of seeds & sellers |
| premium_tshirt | Premium/Tri-Blend Tee | |
| oversized_tshirt | Oversized Tee | |
| performance_tshirt | Performance Tee (US-only) | |
| vneck | V-Neck | |
| tank_top | Tank Top | |
| long_sleeve | Long Sleeve | |
| raglan | Raglan / Baseball Tee | not actively sold |
| comfort_colors_heavyweight | Comfort Colors Heavyweight Tee | |
| standard_sweatshirt | Sweatshirt | |
| standard_pullover_hoodie | Pullover Hoodie | |
| zip_hoodie | Zip Hoodie | |
| quarter_zip | Quarter Zip (US-only) | |
| polo | Performance Polo (US-only) | |
| performance_hoodie | Performance Hoodie (US-only) | |
| comfort_colors_sweatshirt / comfort_colors_crop_sweatshirt | CC Sweatshirts | |
| crop_top | Crop Top | |
| tote_bag | Tote Bag | |
| throw_pillow | Throw Pillow | |
| printed_baseball_hat | Baseball Hat | |
| printed_trucker_hat | Trucker Hat | |
| sport_sun_visor | Sun Visor | |
| pop_socket | PopSocket | |
| phone_case_apple_iphone / phone_case_samsung_galaxy | Phone Case | |
| tumbler / water_bottle / mug | Drinkware | |

Currently present in the live feed: `standard_tshirt` (dominant), `standard_sweatshirt`, `printed_trucker_hat`,
`crop_top`, `standard_pullover_hoodie`, `tank_top`, `vneck`, `water_bottle`.

## `niche` field
Free-text source ad-campaign name (e.g. `Lotto 2`, `Baseball - AUTO`, `Retro Name Vault Trucker Hats 3`,
`4th of July`, `Lotto Sweatshirts`). Context/grouping only — do not parse it for product type (use `product_type`).

## Value ranges (current data, for sanity checks / UI)
- `orders`: 2–9 (≥2 is the inclusion threshold).
- `acos`: 0.00–0.26.
- `royalty_last30`: ~$12–$285.
- `sales_last30`: small integers.

## Routing summary
- **keyword_seeds → NEW design.** Use `term` as the design intent/prompt-source; `product_type` as the blank.
  Let existing prompt-gen (Claude Opus 4.8) → image → vectorize → bg-removal → upscale → listing run normally.
- **proven_sellers → VARIATION.** Use existing prompt-source recovery on `asin` to spin fresh variants of a winner.

## Hard rules
1. Idempotent: never re-create a design for a `term`/`asin` already imported.
2. Respect Blacklist + Uploaded archive (skip those).
3. Seeds only — they pass through normal QA/Tracking + manual upload gate. Never auto-upload.
4. IP: upstream filtering is best-effort. Keep human trademark review before upload regardless.
5. Read the JSON only; never touch the ads tool's database or code.

## Validation
Validate incoming files against `demand_feed.schema.json` (JSON Schema draft-07) and check
`schema == "merchads.demand_feed/v1"`. On mismatch or missing file, fail with a clear message and import nothing.
