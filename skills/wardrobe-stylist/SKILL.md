---
name: wardrobe-stylist
description: "Personal clothing and wardrobe styling. Use when the user wants to dress better, find or change their clothing style, refresh their wardrobe, choose clothes that suit them, or assemble outfits for a season, occasion, trip or everyday wear. Recognize requests such as 'хочу приодеться', 'подбери мне стиль', 'подбери одежду', 'что мне носить', 'собери образ' and 'обнови гардероб'. Start even without photos or measurements; collect relevant details, use marketplace skills before other retailers, verify exact sizes and offers, and deliver coordinated outfits, shopping links and personal try-on renders as appropriate to the request. Not for interior, graphic or interface design."
---

# Wardrobe stylist

For a full outfit selection, produce **three distinct, coordinated outfits within the user's brief**, each with a render of the user and a table of actual shopping offers. Use the user's language. Instructions are in English; scripts support Russian and English reports.

## Activation and scope

Apply to personal clothing/style requests, including short colloquial requests to dress better or refresh a wardrobe. The user need not name this skill, provide photos, know their measurements or explicitly ask to shop before you begin.

Match the deliverable to the request. For a full selection, follow the six stages below. For focused style advice, an existing-wardrobe combination, a sizing check or links for an already chosen outfit, use only the relevant stages; do not expand it into new shopping or mandatory image generation.

Keep case files in a new workspace directory outside this skill. Reuse confirmed facts from the conversation; later corrections supersede earlier values. Do not embed personal profiles or photos in the installed skill.

## Interface

Run `python3 <skill>/scripts/style.py capabilities` to discover operations. Run `python3 <skill>/scripts/style.py call --request REQUEST.json --output RESULT.json` with absolute paths. The JSON envelope is `{ "method": "prepare", "params": { ... } }`. Read [api.md](references/api.md) before the first scripted operation.

Scripts handle measurement tables, gaps and instructions, search jobs, dimension comparisons, public-page evidence, reference downloads and boards, validation, totals, render requests and reports. The agent decides styles, interprets uncertain evidence, selects products, and invokes the available native image generator.

## 1. Intake → a usable brief and a measurement table

**Goal:** establish what to dress for and which dimensions actually matter.

- Record requested styles, season/weather, occasions, garment categories, destination, delivery deadline if relevant, and budget with currency and scope (`per_outfit` alternatives or one `capsule`). If scope changes the choices, clarify it; until then show separate outfit and shared-capsule totals.
- Preserve fit preferences, existing pieces, unwanted colors/materials, nominal sizes and explicit policies such as selecting shoes by label alone. Do not turn one user's preferences into defaults.
- Record each measurement with its basis (**body or garment**), definition, source, unit and uncertainty. A surprising confirmed value stays unchanged; ask about the measurement method rather than silently correcting it. Weight and sizes do not yield circumference measurements.
- Run `prepare`. Show its measurement table, then request only missing facts relevant to the selected garment categories. Use [measurements.md](references/measurements.md) and the returned measuring instructions. Ask for photos through normal conversation, not a text-only input tool.
- Photos: face plus an unobstructed full-body view; side view helps when available. Explain how to take them if absent or unusable. Mirror selfies may be usable with noted perspective uncertainty. Do not infer exact measurements from photos.

**Exit:** a sourced measurement table and explicit gaps. Start provisional search while awaiting optional details; missing identity photos block personalized rendering, not garment research.

## 2. Search criteria → three style directions and category queries

**Goal:** translate the brief and proportions into searchable, measurable constraints.

- Describe three different style directions that all satisfy the brief, with occasion/weather, palette, silhouette and layering. Differences must concern outfit design, not merely three names or shirt colors.
- Create criteria for each category: shape, material, color, relevant measured lengths/widths, layering allowance, budget allocation, delivery and return/try-on needs. Body-to-garment ease is a design choice, not a universal fixed number.
- Add concise queries to `criteria` and run `prepare` again. Its `search_jobs` separate garment categories so a shared result limit cannot silently omit later queries.
- If the user asks about this season's trends, browse current sources and distinguish trends from the user's preferences. Do not bake dated fashion claims into the skill.

**Exit:** three compatible style concepts, a criteria table, and per-category queries.

## 3. Search and verify → exact, purchasable candidates

**Goal:** find things that can actually be ordered in the required variant.

- Check available skills. If `marketplace-shopping` or another applicable marketplace skill exists, read it and use its scripted search/product/quote/images API first. Follow any user preference for a platform. This skill's `marketplace.call` forwards read operations to a discovered `marketplace-shopping`; do not duplicate its session/browser code.
- Search each category separately. Shortlist and inspect **the required size, height/length, color and SKU**, including other variants in the listing. Search snippets, related-product panels and the default variant are not stock evidence.
- If suitable marketplace offers are missing, search the web for relevant retailers, including tall/petite/specialist stores when measurements justify it. `merchant.fetch` extracts public HTML, JSON-LD offers, product text, tables and image candidates. For dynamic variants use `merchant.inspect`: first read the page, then provide precise size/color selectors and read the selected offer. Both operations collect evidence, not automatic proof of stock. They respect the configured network group; the browser helper always runs headless in a temporary context.
- Use `offer.import` to map native marketplace products/quotes into case items. For direct shops, record variant-specific ordinary price, conditional prices separately, available stock, stock scope (online/local-store), delivery destination/date/cost, return terms, ratings with their type, and evidence timestamps/paths. Physical-store stock does not prove shipping to the destination.
- Run `fit.check` using comparable dimensions. Keep unknowns explicit. Reject known incompatible dimensions; reserve uncertain pieces as **try-on candidates**. Nominal W/L/RU/XXL or height labels do not override a manufacturer's measurements. Alteration can fix excess length; it cannot fix insufficient width or length.
- Inspect image-only composition/specifications/size charts visually when text is insufficient. Preserve the evidence and distinguish a generic category chart from a chart for the exact model. OCR is a reading aid; conflicting claims stay unresolved.

**Exit:** sourced candidates for every outfit slot. Replace unavailable variants before rendering. If no purchasable piece can be found, explain the gap and label any preview a concept instead of inventing a shopping offer.

## 4. Assemble and recheck → three priced outfits

**Goal:** choose cohesive combinations with honest fit and purchase status.

- Assign item IDs and exact variants to three outfits. Describe why each combination works for this person and request, including weather limitations. Reuse common pieces when useful.
- Refresh shortlisted stock and quotes after substitutions and immediately before the final report. Any changed SKU needs its own dimensions, price, stock and image evidence. Selected-size prices can differ substantially.
- Run `outfits.check`. Resolve structural/stock/known-fit errors. Display remaining fit and delivery uncertainties. Totals use ordinary prices; missing shipping is **unknown**, not zero. Compute each alternative and the union of shared items counted once; compare the applicable total with the budget.

**Exit:** three validated outfit plans with dated offers, exact variant links or selection instructions, totals and unresolved facts.

## 5. References and renders → three images of the user

**Goal:** visualize the selected garments without changing the person's identity or implying a fit guarantee.

- Use product images already returned by the marketplace API; otherwise `media.download` accepts merchant image URLs with their source page. Keep these downloaded shopping references local; do not use downloads to bypass restrictions on displaying remote media.
- Run `render.prepare` per outfit. It assembles labeled contact sheets from original garment photos and returns a native image-generation request. These sheets are supporting references; they are not try-on renders.
- Use the available image-generation skill/tool. If `imagegen` is available, read its instructions. Inspect local identity images and boards before generation. The current helper uses at most **five** reference paths: identity photos plus board(s), and optionally the first approved render as an anchor.
- Generate one image per style. Preserve face, age, hair, skin texture and natural body proportions. Ignore retailer models' identities. Keep garment color, texture, construction and actual known length; a short coat must not become a knee-length coat. Do not add slimming, physique changes or accessories absent from the plan.
- Inspect all three outputs for identity and garment fidelity. A common anchor keeps framing, pose and lighting comparable. Fix material mistakes with targeted edits; do not claim a render verifies sizing. Record actual prompts and tool mode with `render.attach`, copying returned local outputs into the case directory without overwriting earlier versions.
- If native generation is unavailable, report the limitation and deliver the shopping tables/reference boards. Do not substitute collages for requested try-on images or silently switch to a paid API.

**Exit:** three inspected renders, or an explicit image-generation limitation with the remaining deliverables completed.

## 6. Deliver → measurement table, images and shopping tables

Run `report` to generate a Markdown report and a local HTML gallery. Both include the measurement table, search criteria, three renders, and a table under each style: **item, exact variant, price, delivery, fit status and purchase link**. Include each outfit's known goods total and shared-capsule total, currency, timestamp and missing delivery costs. Show the gallery and renders where supported; provide clickable absolute artifact paths.

Explain the recommendation and only the uncertainties that change a buying decision. Separate approximate styling visualization from measured fit. A styling request does not authorize cart writes, checkout or merchant messages; use the marketplace skill for a separately authorized cart task.

## Conditional references

- [measurements.md](references/measurements.md): measurement/photo collection and definitions.
- [api.md](references/api.md): JSON contracts and runnable operations.
- [evidence-and-fit.md](references/evidence-and-fit.md): variant, stock, pricing and dimension pitfalls observed during real shopping.
