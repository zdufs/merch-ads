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

## Auto-targeting clause bids — the naming trap (2026-08-20)
Amazon's four auto clauses carry enum names that read backwards to most people:

| Amazon calls it | The enum |
|---|---|
| **close match** | `QUERY_HIGH_REL_MATCHES` |
| **loose match** | `QUERY_BROAD_REL_MATCHES` |
| substitutes | `ASIN_SUBSTITUTE_RELATED` |
| complements | `ASIN_ACCESSORY_RELATED` |

High relevance is the **close** one. Broad relevance is the **loose** one.

`lottery.EXPRESSION_TYPE` had those two swapped from the start, so every lottery
ad group launched paying the HIGH bid (`close-match`, $0.21 US) for loose match
and the LOW bid ($0.18) for close match. The optimizer never saw the swap — it
reads Amazon's own report labels — so US slowly corrected itself the expensive
way, while the newer EU markets sat at the wrong launch bids and spent most of
their money on loose match (DE: $97.48 loose vs $7.14 close, trailing 30).

Fixed in `lottery.py`; `lottery.EXPRESSION_NAME` is now the one enum→name map
and `inspect_lotto_bids.py` reads it. Guarded by `tests/clause_expression_tests.py`.
Live clauses already launched wrong are repaired by
`engine/rebid_clauses.py` (preview by default), which corrects ONLY clauses
still sitting at the old launch value — a bid the optimizer already moved came
from that clause's own sales data and is left alone.

## Per-market economics (operator-confirmed 2026-08-20/21)
Every royalty the engine prices with was read off the Merch dashboard and now
SHIPS in `products.py`: the US tee ladder, `PRODUCT_ECON` + `PRODUCT_PRICE` for
US products, and `MARKET_PRODUCT_ECON` for UK/DE/FR/IT/ES. Nothing is
extrapolated and nothing is an export median. Amazon fixes a maximum price per
product per market, so these are caps rather than estimates.

Editing them is the app's Product Royalty tab, which writes an override that
beats the shipped value. Removing a product is still a code change.

### Standard tee
Amazon fixes a MAXIMUM price per product per market, so these are caps rather
than estimates. Read off the Merch dashboard and entered in the app's Product
Royalty tab; `markets.TEE_PRICE` carries the same DE figure because
`derive_econ.py` uses it as the tee break-even denominator.

| Market | Price | Royalty | Break-even |
|---|---|---|---|
| US | $14.99 | $1.28 | 8.5% — but ACTED ON at the $19.99 floor (rank push, see above) |
| UK | £17.49 | £3.57 | 20.4% |
| DE | €17.99 | €2.70 | 15.0% |
| FR | €19.49 | €3.76 | 19.3% |
| ES | €19.49 | €3.98 | 20.4% |
| IT | €19.49 | €3.70 | 19.0% |

DE moved from €18.45 to €17.99, so its break-even rose from 14.6% to 15.0%.
Nothing else changed: the other five already matched what the engine held.
