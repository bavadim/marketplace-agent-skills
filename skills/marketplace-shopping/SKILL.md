---
name: marketplace-shopping
description: Find and compare tools, manufacturing parts and other products across Ozon and AliExpress. Review exact variants and image specifications, compare price and delivery, offer the best verified choice or alternatives, and fill reviewed carts through one provider-neutral JSON API.
---

# Marketplace shopping

## Interface

Run `python3 scripts/shop.py capabilities` or `python3 scripts/shop.py call --request REQUEST.json --output RESULT.json` from this skill directory. Use absolute request/result paths outside the skill. Stdin is supported with `--request -`.

All normal reads use **both marketplaces**. Pass returned `ref` values unchanged; the provider factory routes them. Do not ask the user which marketplace is active. The implementations and browser/session mechanics are bundled in `assets/marketplace/`.

For account setup/renewal, use `marketplace-accounts` when installed, or the same `session.validate` and `session.login.*` methods here. Give the user the local login link; both sites use owned headless Chromium. Reuse saved profiles and preserve the shopping task while waiting for login.

## Workflow

1. **Define the part.** Record purpose, quantity/unit, required fit and kit contents. For tools and production parts, check the relevant dimensions/tolerances, thread/pitch, mounting, material, voltage/current and connectors. Add budget, destination and deadline when given; do not invent requirements.
2. **Search both.** Call `search` with `query` or a short `queries` array. Search prices are provisional. Keep useful candidates from either provider; read `sources` if coverage is partial.
3. **Inspect exact variants.** Call `product` for shortlisted refs. Inspect `variants` and call the ref of the required variant, even if the default variant is unsuitable. Verify `variant_verified`, `options`, stock, specifications, kit contents, seller rating and product reviews. Optional `variant_limit` reads other linked variants. Do not equate a product rating with a seller rating.
4. **Read image evidence when needed.** Call `images` for specifications or contents missing/ambiguous in text. View the returned tiles. Record the image/evidence ID and its actual claim; OCR is a reading aid. Check that evidence belongs to the selected variant.
5. **Review and quote.** Call `review` with the saved product and observations. State compatibility, pack size, unit, kit contents, evidence and unresolved required facts. Call `quote` with the exact ref, city and **purchase-unit quantity**, calculated as ceil(required units / pack size). Preserve unknown delivery cost/date/address; verify the account destination. A quote does not select a checkout shipping method.
6. **Compare.** Put reviewed products in one `products` array and call `compare` with quantity/unit and optional budget/deadline. It computes packs, cost and price outliers. Its balanced default prefers faster confirmed delivery within 20% of the cheapest confirmed total. Adjust policy only to reflect the user’s priorities. Refresh and re-review an altered SKU; do not copy another variant’s technical facts.
7. **Present or fill the cart.** Present `best_verified` with its evidence, or 2–3 `offer_choices` explaining cost/time tradeoffs and missing data. For an authorized cart task, call `cart.apply` with the chosen exact refs and price caps, then verify its result and return every affected cart link. Stop before checkout/payment.

## Cart semantics

`cart.apply` defaults to `mode="ensure"`: preserve an existing larger quantity and add missing units. `cart.set` changes the absolute quantity; zero removes that exact variant; `cart.remove` is its deletion shortcut. Use an explicit stable `operation_id` and reuse it after interruption. The script checks price/options and preserves other rows; the plan is journaled and can be partial. A changed plan requires a new operation ID.

For `requires_cart_read`, refresh `cart.list` before another write. Do not delete unrelated items for rollback. Developing/testing the skill is not authorization to alter a real cart.

## Output

Show the exact part/kit, quantity, purchase-unit price, known total with delivery, delivery window, seller rating and reason for the choice. Conditional bank prices and crossed-out reference prices are separate fields. Do not call an offer the overall cheapest/fastest when required comparison data is missing. A current-price outlier requires an explanation; it does not invalidate all variants of its listing.

## References

Read [api.md](references/api.md) for request/model examples, review evidence and comparison policy. Read [access.md](references/access.md) only for login, browser, network or CAPTCHA problems. Existing orders and delivery lists use the separate `marketplace-orders` skill with the same engine.
