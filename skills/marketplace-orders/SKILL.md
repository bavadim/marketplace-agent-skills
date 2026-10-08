---
name: marketplace-orders
description: Return one combined order and shipment list from Ozon and AliExpress, including status, delivery date, pickup address and storage deadline. Build daily/weekly delivery lists, extract sourced measurements and export actual pickup-code images through the shared provider-neutral API.
---

# Marketplace orders

## Interface

Run `python3 scripts/orders.py capabilities` or `python3 scripts/orders.py call --request REQUEST.json --output RESULT.json` from this skill directory. Use absolute private paths; stdin is supported with `--request -`.

The runtime is bundled in the sibling `marketplace-shopping/assets/marketplace/`. Install both skills together. No standalone provider skill is required.

Account setup uses `marketplace-accounts` when installed or the shared `session.validate`/`session.login.*` methods here. Give the user the local login link and resume the order task after verification. Both providers use owned headless Chromium.

## Workflow

1. **Refresh both sources.** Call `orders.list`. It reads both marketplaces by default and returns one normalized `items` list, plus nested `orders` and per-provider `sources`. Do not ask which marketplace is active. A source failure is not an empty account; report partial coverage.
2. **Show the combined list.** Use one row per shipment: items/quantity, status, expected window or storage deadline, address and order link. Sort/group by status, date or address as useful. Split orders can contain received, ready and travelling shipments simultaneously. Preserve an unresolved order as an incomplete row.
3. **Build a day/week list.** Call `orders.schedule` with `days=1` or `days=7`. Without `input`, it refreshes both sources; with a saved combined `input`, it works offline. Return ready pickups and expected deliveries, grouped by address. Date ranges are uncertainty windows, not repeated deliveries. A stale snapshot needs refreshing before an actionable reminder.
4. **Obtain measurements when requested.** Call `orders.enrich` on the combined snapshot. It reads each unique product once, attaches parsed weight/dimensions and returns `measurement_review_queue`. Use image/description reasoning only for unresolved values. Distinguish product dimensions/weight from package measurements; current listing claims can differ from the purchased kit. Keep source, units and uncertainty.
5. **Export a receipt card when requested.** Call `orders.code` with the returned order `ref` and an absolute PNG `path`. Return the actual site artifact and validity metadata. Ozon currently exposes a daily numeric receipt card, not a barcode image. Refresh it immediately before forwarding. Send only to the user’s explicitly chosen recipient/channel; receipt values never belong in the normal order list.

## Alerts

`orders.schedule` returns alerts for ready shipments and approaching/expired storage deadlines. An inclusive deadline is still valid on that date; an expired deadline does not prove a return. Unknown deadlines remain unknown.

Pass a private `state` file to suppress previously delivered alerts. After successful message delivery, call `orders.ack` with its fingerprints. Building a list never acknowledges delivery. Ordinary ready alerts repeat only when their state/deadline changes; urgent reminders can recur the next day.

For requested automatic monitoring, use the app automation tool with the user’s schedule/timezone/channel. Refresh sources on each run; notify only on new actionable status, urgency or a failure requiring action. No route planning, geocoding or background service is part of this skill.

## Coverage and access

Ozon live coverage includes grouped ready orders, split shipments, list pagination, addresses/deadlines and receipt-image export. AliExpress live coverage includes an authenticated empty active list; nonempty list fields are adapted from its public frontend contract. AliExpress shipment details and receipt export remain unavailable and return an explicit partial/unsupported result. Uniform formatting does not imply equal provider coverage.

For login/access failures read [the shared access instructions](../marketplace-shopping/references/access.md). API requests, models and partial results are documented in [the shared API reference](../marketplace-shopping/references/api.md).
