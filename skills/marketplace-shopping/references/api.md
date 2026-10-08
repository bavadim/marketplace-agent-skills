# Unified API

## Calling

```sh
python3 scripts/shop.py call --request /absolute/private/request.json --output /absolute/private/result.json
python3 ../marketplace-orders/scripts/orders.py call --request /absolute/private/request.json --output /absolute/private/result.json
```

Every request is `{ "method": "METHOD", "params": {...} }`. `capabilities` lists the callable methods and required fields. Search limits are 1–100, image limits 1–32, variant traversal 0–24, orders 1–100 and list pages 1–20. Shopping and orders entrypoints expose their respective methods. Provider selection is optional and defaults to both.

Every response is `{schema_version:2, method, ok, status, observed_at, data}`. `ok=true` means useful data exists and includes partial responses; inspect `status`, `complete`, `warnings`, `sources` and `requires_cart_read`. Source errors are retained alongside successful provider results. Credentials/receipt values are excluded. JSON and receipt PNG outputs have mode 0600.

Examples below are separate requests:

```json
{"method":"search","params":{"queries":["HSS step drill 4 20 mm","сверло ступенчатое HSS 4 20 мм"],"limit":12}}
{"method":"product","params":{"ref":"REF_FROM_SEARCH","variant_limit":4}}
{"method":"images","params":{"ref":"EXACT_VARIANT_REF","path":"/absolute/private/images","limit":12}}
{"method":"quote","params":{"ref":"EXACT_VARIANT_REF","quantity":2,"city":"Смоленск"}}
{"method":"review","params":{"input":"/absolute/private/product.json","observations":"/absolute/private/review.json"}}
{"method":"compare","params":{"input":"/absolute/private/offers.json","quantity":8,"unit":"pcs","budget":5000,"deadline":"2026-11-01"}}
{"method":"cart.list","params":{}}
{"method":"cart.apply","params":{"operation_id":"STABLE_TASK_ID","items":[{"ref":"EXACT_VARIANT_REF","quantity":2,"max_unit_price":1000,"expected_options":{"Voltage":["24 V"]}}]}}
{"method":"cart.remove","params":{"ref":"EXACT_VARIANT_REF","operation_id":"STABLE_REMOVE_ID","expected_quantity":2}}
{"method":"orders.list","params":{"active_only":true,"max_orders":30,"max_pages":5}}
{"method":"orders.get","params":{"ref":"ORDER_REF_FROM_LIST"}}
{"method":"orders.schedule","params":{"days":7,"state":"/absolute/private/alerts.json"}}
{"method":"orders.schedule","params":{"input":"/absolute/private/orders.json","days":1}}
{"method":"orders.enrich","params":{"input":"/absolute/private/orders.json","max_products":10,"images_dir":"/absolute/private/measurement-images"}}
{"method":"orders.code","params":{"ref":"ORDER_REF_FROM_LIST","path":"/absolute/private/receipt.png"}}
{"method":"orders.ack","params":{"state":"/absolute/private/alerts.json","fingerprints":["FINGERPRINT_OF_SUCCESSFULLY_DELIVERED_ALERT"]}}
```

## Product

Both implementations return the same fields:

- Identity: `ref`, `provider`, `product_id`, `variant_id`, `url`, `title`, `observed_at`, `stage`, `variant_verified`.
- Money: `price.unit`, `currency`, `reference`, `conditional`. Decimal strings; unknown is null. Unit price means one sale pack, not one physical part.
- Delivery: `earliest`, `latest` (ISO dates), `cost`, `currency`, `destination`, `quantity` (sale packs), `method`, `verified`, `source_text`, `status`. Unknown cost is not zero; search labels are preliminary. Quotes are not checkout selections.
- Seller: name/id/url and `rating:{value,scale,count}`. `product_rating` is separate. Preserve percentages when scale is 100.
- Variant: `options` is always a mapping to arrays of displayed values. `variants[]` contains selectable refs and options; `variant_products[]` contains actually inspected alternatives; `variants_complete` concerns the bounded details traversal.
- Evidence: `specifications`, `description`, `images`, `evidence`, optional `image_evidence`, `review`, `warnings`, `available`, `stock`.

A ref is opaque to the caller. The factory supports current refs and site product URLs, preserving the AliExpress sku_id. Use returned variant refs for exact selection; a product ref without a variant cannot be written to the cart.

## Review and comparison

An observations file contains only the technical judgment; it cannot replace observed price/SKU/delivery:

```json
{
  "ref":"EXACT_VARIANT_REF",
  "complete":true,
  "compatible":true,
  "pack_size":5,
  "unit":"pcs",
  "package_contents":["5 matching parts"],
  "facts":{"thread":"M6 x 1.0","material":"steel"},
  "evidence":[{"source":"OBSERVED_URL_OR_TILE_PATH","text":"Actual displayed marking supporting the selected variant","reviewed":true}],
  "unresolved":[]
}
```

Add an evidence ID where available. `complete=true` follows actual inspection of required facts and kit contents. A seller image supports a seller claim, not an independent measurement. An outlier can be explained with `price_explained=true` and the supporting evidence.

The compare input is `{"products":[REVIEWED_PRODUCT,...]}`. It calculates packs with ceil(requested quantity / pack_size), goods cost and total only when shipping quantity/currency/cost match. Required unknown facts, stale observations, insufficient stock, budget/deadline violations and unexplained current-price outliers cannot win. Seller rating thresholds apply only when set by policy; unknown ratings are flagged.

Policy keys: `fast_delivery_premium` (0.20), `high_price_factor` (10), `max_age_hours` (12), optional `min_seller_rating_5`. Median outlier detection requires at least three independent sellers and uses physical-unit prices of compatible variants. Reference/conditional prices never replace the ordinary current price.

Results include `recommended`, `decision`, `cheapest_verified`, `fastest_verified`, `balanced_verified`, `offers` and `comparison_complete`. `best_verified` chooses the earliest upper delivery date within the configured premium of the cheapest verified total. Missing comparison coverage produces `offer_choices`, not a proven winner. Re-quote the computed purchase quantity if the original quote was for another count.

## Carts

Both providers return `items` with ref/provider, quantity, price.unit, line_total, selected, availability and displayed variant text, plus completeness and cart links. Callers use refs, not platform row IDs.

`cart.set` changes an absolute quantity, including zero. `cart.remove` is the same verified deletion without a price-cap parameter. `cart.apply` defaults to `mode="ensure"` and preserves an existing larger count; `mode="set"` applies exact quantities. Duplicate refs are rejected: aggregate the chosen pack quantities before making a plan. Price caps use ordinary RUB purchase-unit prices; options are compared with the reviewed values. Other rows and selection flags are checked.

Mutation calls require a stable `operation_id`. A private persistent journal binds it to the exact plan and records pending/completed steps. Reusing it with a different plan fails. An interrupted/uncertain result may already have changed the cart: read it before further writes. The multi-provider plan is not atomic; no checkout/payment or automatic rollback is performed.

## Orders

`orders.list` returns normalized nested `orders` and one combined flat `items` list. Each flat row has ref, row_ref, provider, order_id, order_url, observed_at, shipment_id, status/status_text, delivery_text/method, address, expected, pickup_until, pickup_until_inclusive, items, complete and missing. Namespace IDs include the provider. An unresolved order remains a row with null shipment/address fields.

Status values: processing, in_transit, out_for_delivery, ready_for_pickup, delivered, cancelled, returned, awaiting_payment, unknown. One order can span several statuses/deadlines. `active_only=false` combines active and historical orders within max_orders/max_pages; coverage bounds remain explicit.

Schedules return ready_for_pickup, expected, by_day, undated, unresolved_orders, pickup_addresses, alerts/pending_alerts, completeness and per-provider sources. Europe/Moscow is the current date timezone. Historical snapshots do not become fresh when enriched. Receipt values are excluded; orders.code returns a private image path and validity metadata.

Measurements have kg/cm values, product/package scope, source text/URL, confidence, completeness and review evidence. No invented parcel dimensions or unknown zero weights. `orders.enrich` reads at most max_products unique listings, and retains unresolved/limit cases in its review queue.
