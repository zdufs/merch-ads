# Ads Bidding Rules

> The operator's strategy directive, translated into rules the tool enforces. Pairs with royalty-reference.md.
> Status: **FINAL — all values confirmed 2026-06-15.**

## Scope (v1)
- **US marketplace only** in v1 (one advertising profile). The engine has run all six
  Merch marketplaces since 2026-06 — see `multi-market.md`.
- **Automatic campaigns, one ASIN per ad group.** No manual-keyword campaigns yet.
  - No manual keywords → spend control on a bad search term = **negative keyword**.
  - Bid control = the **4 auto targeting groups** (close match, loose match, complements, substitutes), per ad group.

## Two optimization models

**Model A — Standard tee only (growth / CVR-first):**
- Optimize for **conversion rate**; push targeting-group bids **up** where CVR is strong.
- **ACOS ceiling = 30%.** Below it, keep scaling; above it, pull bids back.
- Accepts running slightly above break-even (tee BE 24.5%→28.9%) to drive volume + organic rank.

**Model B — Everything else (break-even discipline):**
- Drive achieved ACOS toward each product's **break-even** (table below).
- Tight stop-losses; cut non-converters fast.

## Advertised products (US only) & targets
| Product | Royalty | Model | Target / ceiling ACOS |
|---|---|---|---|
| Standard tee | $4.89 ($19.99) → $6.36 ($21.99) | A | ≤ 30% |
| Sweatshirt | $8.10 | B | 22.5% (break-even) |
| Pullover hoodie | $7.90 | B | 21.9% (break-even) |
| Zip hoodie | $7.23 | B | 19.5% (break-even) |
| Baseball hat | $2.80 | B | 14.0% (break-even) |
| Trucker hat | $2.80 | B | 14.0% (break-even) |

## Stop-loss rules (both fire on 0 orders)

**1. Bad search term → negative exact keyword** (threshold = royalty × 0.5)
| Product | Negate term after spend of |
|---|---|
| Standard tee | $2.45 → $3.18 (at $21.99) |
| Sweatshirt | $4.05 |
| Pullover hoodie | $3.95 |
| Zip hoodie | $3.62 |
| Baseball / Trucker hat | $1.40 |

**2. Whole ad group (ASIN) → pause** (threshold = royalty × 0.5, EXCEPT tee)
| Product | Pause ad group after spend of |
|---|---|
| Sweatshirt | $4.05 |
| Pullover hoodie | $3.95 |
| Zip hoodie | $3.62 |
| Baseball / Trucker hat | $1.40 |
| **Standard tee** | **$5 flat** (0 sales). Once it gets a sale, governed by the 30% ACOS ceiling instead. |

## Other rules
- **Bid moves** are gradual (capped per run) so nothing swings wildly.
- **Search-term harvesting (YES):** converting search terms → promote to a manual **exact-match** campaign + negate in the auto campaign. (Later phase — requires creating manual campaigns.)
- **Budget:** shift toward ad groups at/under target ACOS that are budget-capped; trim losers.

## DO-NOT-advertise (everything else)
Premium tri-blend, V-neck, Tank top, Performance T-shirt, Long sleeve, CC Heavyweight,
Performance Polo, Performance Quarter-Zip, CC Crop Top, Throw Pillow, Tote Bag,
Performance Hoodie, CC Sweatshirt, CC Crop Sweatshirt, iPhone case, Ceramic Mug,
Sport Sun Visor, PopSockets, Tumbler, Water bottle.
