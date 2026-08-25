# Merch Royalty & Break-Even Reference

> Source: live Merch dashboard figures + official royalty doc (resource/201858580), 2026-06-15. Tier: **Plus**.
> Used by the ads tool to set per-product, per-marketplace break-even and target ACOS.

## Tier mechanics (critical)

- **US store:** Plus = **2× Creator rate** (Premium = 2.16×). This account is on Plus.
- **Non-US (UK/DE/FR/IT/ES/JP): NOT multiplied** — listed royalty is actual, any tier.
- Royalty = offer price − tax − Amazon costs. US price excludes tax (added at checkout); EU/JP price includes tax.
- **Break-even ACOS = royalty ÷ price.** Profit requires target ACOS *below* break-even.

## ⚠️ Strategy implication
US margins are ~2× UK/EU margins on the same product (Plus bonus is US-only).
→ Ads tool must use **per-marketplace** ACOS targets: more aggressive in US, tighter in UK/EU.
A flat cross-market target loses money in Europe.

## Scope
- Marketplaces sold: **US, UK, DE, FR, IT, ES** (no Japan).
- Products NOT sold: raglan / baseball tee.

## Pricing strategy notes
- **Standard tee:** launch $19.99, raise to $21.99 after 10 sales (US). UK at £17.49.

## Product royalties & break-even (Plus tier)

### Standard T-Shirt
> ⚠️ US figures re-confirmed 2026-07-12 off the live dashboard — Amazon RAISED
> royalties since the 2026-06-15 capture. The engine now derives US tee
> break-even from each design's live list price (products.US_TEE_ROYALTY_CENTS,
> model 2026-07-12); this table is the authoritative source of those constants.
| Market | Price | Royalty | Break-even ACOS |
|---|---|---|---|
| US | $19.99 | $5.28 (extrapolated) | 26.4% |
| US | $20.99 | $6.08 (extrapolated) | 29.0% |
| US | $21.99 | $6.88 (confirmed 2026-07-12) | 31.3% |
| US | $22.99 | $7.67 (confirmed) | 33.4% |
| US | $23.99 | $8.47 (confirmed) | 35.3% |
| US | $24.99 | $9.27 (confirmed) | 37.1% |
| UK | £17.49 | £3.57 | 20.4% |
| DE | €19.99 | €3.96 | 19.8% |
| FR | €19.99 | €4.09 | 20.5% |
| IT | €19.99 | €4.02 | 20.1% |
| ES | €19.99 | €4.29 | 21.5% |

### Premium Tri-Blend T-Shirt
| Market | Price | Royalty | Break-even ACOS |
|---|---|---|---|
| US | $23.99 | $6.52 | 27.2% |
| UK | £21.99 | £3.11 | 14.1% |
| DE | €21.99 | €3.14 | 14.3% |
| FR | €21.99 | €3.11 | 14.1% |
| IT | €21.99 | €3.06 | 13.9% |
| ES | €21.99 | €3.09 | 14.1% |

### Comfort Colors Heavyweight T-Shirt
| Market | Price | Royalty | Break-even ACOS |
|---|---|---|---|
| US | $24.99 | $5.10 | 20.4% |
| UK | £21.99 | £3.11 | 14.1% |
| DE | €24.99 | €3.57 | 14.3% |
| FR | €24.99 | €3.54 | 14.2% |
| IT | €24.99 | €3.48 | 13.9% |
| ES | €24.99 | €3.51 | 14.0% |

### V-Neck T-Shirt
| Market | Price | Royalty | Break-even ACOS |
|---|---|---|---|
| US | $21.99 | $5.09 | 23.1% |
| UK | £17.99 | £3.30 | 18.3% |
| DE | €18.99 | €3.39 | 17.9% |
| FR | €19.99 | €3.54 | 17.7% |
| IT | €19.99 | €3.48 | 17.4% |
| ES | €18.99 | €3.11 | 16.4% |

### Tank Top
| Market | Price | Royalty | Break-even ACOS |
|---|---|---|---|
| US | $21.99 | $5.12 | 23.3% |
| UK | £17.99 | £2.91 | 16.2% |
| DE | €19.99 | €3.48 | 17.4% |
| FR | €18.99 | €3.47 | 18.3% |
| IT | €18.99 | €3.42 | 18.0% |
| ES | €18.99 | €3.39 | 17.9% |

### Performance T-Shirt  (US-only product)
| Market | Price | Royalty | Break-even ACOS |
|---|---|---|---|
| US | $21.99 | $5.30 | 24.1% |

### Long Sleeve T-Shirt
| Market | Price | Royalty | Break-even ACOS |
|---|---|---|---|
| US | $24.99 | $5.24 | 21.0% |
| UK | £21.99 | £3.46 | 15.7% |
| DE | €22.99 | €3.13 | 13.6% |
| FR | €20.99 | €3.15 | 15.0% |
| IT | €20.99 | €3.10 | 14.8% |
| ES | €20.99 | €3.10 | 14.8% |

### Sweatshirt
| Market | Price | Royalty | Break-even ACOS |
|---|---|---|---|
| US | $35.99 | $8.10 | 22.5% |
| UK | £31.99 | £5.04 | 15.8% |
| DE | €34.99 | €5.23 | 14.9% |
| FR | €31.99 | €5.82 | 18.2% |
| IT | €31.99 | €5.46 | 17.1% |
| ES | €31.99 | €5.49 | 17.2% |

### Pullover Hoodie
| Market | Price | Royalty | Break-even ACOS |
|---|---|---|---|
| US | $35.99 | $7.90 | 21.9% |
| UK | £32.99 | £4.71 | 14.3% |
| DE | €35.99 | €4.43 | 12.3% |
| FR | €33.99 | €5.59 | 16.4% |
| IT | €33.99 | €5.23 | 15.4% |
| ES | €33.99 | €5.51 | 16.2% |

### Zip Hoodie
| Market | Price | Royalty | Break-even ACOS |
|---|---|---|---|
| US | $36.99 | $7.23 | 19.5% |
| UK | £32.99 | £6.07 | 18.4% |
| DE | €35.99 | €5.91 | 16.4% |
| FR | €33.99 | €5.52 | 16.2% |
| IT | €33.99 | €5.17 | 15.2% |
| ES | €33.99 | €5.44 | 16.0% |

### Comfort Colors Crop Top  (US computed from doc; non-US TBD)
| Market | Price | Royalty | Break-even ACOS |
|---|---|---|---|
| US | $24.99 | $5.94 | 23.8% |

### PopSockets  ⚠️ very thin margin
| Market | Price | Royalty | Break-even ACOS |
|---|---|---|---|
| US | $14.99 | $2.10 | 14.0% |
| UK | £11.99 | £1.40 | 11.7% |
| DE | €12.99 | €1.53 | 11.8% |
| FR | €14.99 | €1.75 | 11.7% |
| IT | €14.99 | €1.72 | 11.5% |
| ES | €14.49 | €1.68 | 11.6% |

### iPhone Case  ⚠️ thin margin
| Market | Price | Royalty | Break-even ACOS |
|---|---|---|---|
| US | $17.99 | $2.89 | 16.1% |
| UK | £15.99 | £2.00 | 12.5% |
| DE | €16.99 | €2.14 | 12.6% |
| FR | €17.99 | €2.25 | 12.5% |
| IT | €17.99 | €2.21 | 12.3% |
| ES | €17.99 | €2.23 | 12.4% |

### Tumbler  ⚠️ very thin margin
| Market | Price | Royalty | Break-even ACOS |
|---|---|---|---|
| US | $26.99 | $3.78 | 14.0% |
| UK | £18.20 | £2.12 | 11.6% |
| DE | €20.57 | €2.42 | 11.8% |
| FR | €20.57 | €2.40 | 11.7% |
| IT | €20.57 | €2.36 | 11.5% |
| ES | €20.57 | €2.38 | 11.6% |

### Water Bottle  ⚠️ very thin margin
| Market | Price | Royalty | Break-even ACOS |
|---|---|---|---|
| US | $28.99 | $4.06 | 14.0% |
| UK | £18.20 | £2.12 | 11.6% |
| DE | €20.57 | €2.42 | 11.8% |
| FR | €20.57 | €2.40 | 11.7% |
| IT | €20.57 | €2.36 | 11.5% |
| ES | €20.57 | €2.38 | 11.6% |

### Performance line (all US-only)
| Product | Price | Royalty (Plus, 2×) | Break-even ACOS |
|---|---|---|---|
| Performance T-Shirt | $21.99 | $5.30 | 24.1% |
| Performance Polo | $23.99 | $5.12 | 21.3% |
| Performance Quarter-Zip | $24.99 | $5.10 | 20.4% |
| Performance Hoodie | $41.99 | $6.27 | 14.9% |

### Comfort Colors sweatshirts (US-only) — thin margin
| Product | Price | Royalty (Plus, 2×) | Break-even ACOS |
|---|---|---|---|
| CC Sweatshirt | $44.99 | $6.89 | 15.3% |
| CC Crop Sweatshirt | $44.99 | $6.89 | 15.3% |

### Other US-only products
| Product | Price | Royalty (Plus, 2×) | Break-even ACOS | Note |
|---|---|---|---|---|
| Comfort Colors Crop Top | $24.99 | $5.94 | 23.8% | decent |
| Tote Bag | $21.99 | $5.36 | 24.4% | decent |
| Throw Pillow | $23.99 | $5.32 | 22.2% | decent |
| Ceramic Mug | $16.99 | $2.54 | 15.0% | thin (+$4 if 2-sided) |
| Baseball Hat | $19.99 | $2.80 | 14.0% | thin |
| Trucker Hat | $19.99 | $2.80 | 14.0% | thin |
| Sport Sun Visor | $17.99 | $2.52 | 14.0% | thin |

<!-- All catalog products captured. US royalty = 2 × Creator-rate (Plus tier). Non-US not multiplied. -->

## Ad-targeting buckets (by break-even ACOS)
- **Healthy (advertise actively, BE ≥ 20%):** all apparel — standard/premium/CC/v-neck/tank/performance tees, long sleeve, sweatshirt, hoodies; plus tote bag, crop top, throw pillow (US).
- **Thin (minimal/no ads, BE 11–16%):** PopSockets, phone cases, tumbler, water bottle, mug, hats, sun visor, CC sweatshirts.
- **Per-market rule:** US margins ~2× EU (Plus bonus is US-only) → use more aggressive ACOS targets in US, tighter in UK/DE/FR/IT/ES.
