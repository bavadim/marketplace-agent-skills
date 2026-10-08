# Styling case API

## Invocation and dependencies

`python3 <skill>/scripts/style.py capabilities`

`python3 <skill>/scripts/style.py call --request /absolute/request.json --output /absolute/result.json`

Stdin accepts `--request -`. The result envelope is `{schema_version: 1, method, ok, observed_at, data}`; a failed operation has `ok: false`, `error`, and a nonzero exit status. An operation can succeed while its `data` contains failed validation or partial downloads; inspect both levels. Put every output outside the installed skill.

Core checks use Python's standard library. Public-page parsing requires `beautifulsoup4`; boards/image validation require `Pillow`. Use an existing interpreter with these libraries or the available workspace-dependency runtime. Report a missing dependency instead of silently installing system packages.

The optional `merchant.inspect` helper requires `patchright` or `playwright` and a Chromium executable/runtime. If unavailable in the current interpreter, it reuses an existing `${XDG_DATA_HOME:-~/.local/share}/ozon-bom/venv/bin/python` when present; `browser_python` can specify another prepared runtime. It installs nothing. It finds `chromium` or `chromium-browser` on PATH, supports an explicit `executable_path`, and otherwise uses the library's bundled browser. It always starts a temporary **headless** context; marketplace sessions remain in the marketplace engine.

Web operations retain the process environment and do not save browser credentials. Marketplace access uses its own existing runtime and sessions.

## Case file

Agent-maintained JSON; measurement and product facts must be sourced. Paths to photos and original garment images are absolute. Only user-provided data belongs in the local case, never in the reusable skill.

```json
{
  "request": {
    "text": "Three autumn outfits for work and weekends",
    "language": "en",
    "season": "autumn",
    "destination": "User's city",
    "categories": ["outerwear", "top", "bottom", "shoes"],
    "budget": {"amount": 50000, "currency": "RUB", "scope": "per_outfit"},
    "shoe_policy": "nominal",
    "providers": ["ozon", "aliexpress"]
  },
  "profile": {
    "measurements": [
      {"id": "chest", "value": 104, "unit": "cm", "basis": "body", "definition": "circumference at fullest chest", "source": "user", "confirmed": true},
      {"id": "inseam", "value": 84, "unit": "cm", "basis": "garment", "definition": "crotch seam to hem", "source": "user's fitting trousers"}
    ],
    "usual_sizes": {"top": "L", "shoes": [43, 44]},
    "photos": [{"role": "face", "path": "/case/face.png"}, {"role": "body", "path": "/case/body.png"}]
  },
  "criteria": [
    {"category": "bottom", "requirements": "Straight dark denim; verify inseam and hips", "queries": ["straight dark jeans L34"]}
  ],
  "items": {
    "jeans": {
      "name": "Brand / exact model", "category": "bottom",
      "url": "https://merchant.example/product/exact-variant",
      "variant": "blue, W34/L34", "sku": "variant-sku", "variant_verified": true,
      "selection_instructions": "Select W34 then L34 if the page resets",
      "price": {"ordinary": 6000, "currency": "RUB", "conditional": [{"amount": 5500, "condition": "named bank offer"}]},
      "stock": {"available": true, "scope": "online", "quantity": 2},
      "observed_at": "2026-10-08T10:00:00+00:00",
      "evidence": [{"path": "/case/exact-variant.json", "claim": "Selected size available"}],
      "delivery": {"verified": false, "destination": null, "earliest": null, "latest": null, "cost": null, "currency": "RUB"},
      "fit": {"status": "provisional", "note": "Try-on candidate", "unresolved": ["Exact garment hip circumference missing"]},
      "image_path": "/case/jeans.jpg",
      "visual_notes": "Straight dark blue denim, no distressing, full length"
    }
  },
  "outfits": [
    {"id": "urban", "title": "1. Elegant urban", "style_summary": "Charcoal structured coat with wine knit and straight dark denim", "note": "For cool dry days", "items": ["coat", "knit", "jeans", "boots"]}
  ]
}
```

The example illustrates shape, not a complete purchasable case. Supply three actual outfits and all their items. Shared purchases reuse the same item ID; distinct SKUs or sizes use distinct IDs. `quantity` defaults to 1. Supported categories: `top`, `shirt`, `outerwear`, `bottom`, `skirt`, `dress`, `shoes`; custom categories require agent-defined criteria. An outfit may override `required_categories` when a dress replaces a top and bottom. Optional `render_scene` controls shared framing.

## Operations

### `prepare`

`{"method":"prepare","params":{"case":"/case/case.json"}}`

Returns sourced `measurements`, `missing_measurements` with measuring methods, `missing_photos`, clarification `questions`, criteria and independent `search_jobs`. It does not invent style directions or queries. Fill those in the case and rerun. Measurements require positive finite values, `body`/`garment` basis, definition, source and `cm` (`weight`: `kg`). Do not put a nominal clothing size in this array.

### `marketplace.call`

`{"method":"marketplace.call","params":{"request":{"method":"search","params":{"query":"straight jeans L36","providers":["ozon"],"limit":12}}}}`

Finds a sibling/default installed `marketplace-shopping` and delegates its native JSON API. `skill_path` can specify another installed root. Read its `SKILL.md` and API reference before use. Allowed methods: `search`, `product`, `quote`, `images`, `review`, `compare`, `capabilities`. Returns the original marketplace envelope inside `data`; inspect its `ok/status` too. Cart writes are excluded. A missing skill returns `skill_unavailable`; use web search instead. Preserve marketplace `ref` values; use `offer.import` to map product/quote fields and retain source files. Do not promote incomplete data to verified facts.

### `offer.import`

`{"method":"offer.import","params":{"input":"/case/product.json","quote":"/case/quote.json","id":"jeans","category":"bottom","image_path":"/case/jeans.jpg"}}`

Maps successful native marketplace product/quote/review evidence to `data.item`, preserving options, ordinary and conditional prices, stock, delivery, seller/product ratings, images/specifications and source paths. Add it under `case.items[data.item_id]`. Optional `quote` must have the same exact `ref`; changed-SKU quotes are rejected. Search snippets cannot be imported. Ambiguous option lists stay unverified. Agent-supplied `fit` and `visual_notes` may be included after their evidence review; default fit remains provisional. `image_path` is a visually selected actual garment image, not an automatically guessed chart tile.

### `merchant.fetch`

`{"method":"merchant.fetch","params":{"url":"https://merchant.example/product","directory":"/case/evidence"}}`

Saves original public HTML plus structured evidence: trimmed main text, tables, JSON-LD, image candidates, timestamps/hash and network GID. It does **not** infer selected-variant availability. Use browser evidence for dynamic variants where necessary. Login/CAPTCHA errors remain access problems; do not parse a challenge page as a product. `text_limit` defaults to 16000; choose a larger value only when needed.

### `merchant.inspect`

`{"method":"merchant.inspect","params":{"url":"https://merchant.example/product","directory":"/case/browser-evidence","actions":[{"op":"click_variant","selector":"button[data-size='56']","expected_text":"56"}],"extract":{"selected_size":"[aria-selected='true']","offer":".product-price"}}}`

First call without actions and inspect the saved HTML/screenshot to find exact selectors. Then select the required variant and extract its visible details. Supported operations: `click_variant`, `select_variant` (with `value`), `wait_for` (optional `state`). Selectors must identify one element; expected labels prevent stale selections. Cart/checkout/submission controls are rejected. `extract` maps field names to selectors and returns text plus selection/disabled attributes. Optional `text_selector` limits the page text. Returns screenshot, HTML, evidence JSON, network GID and `headless:true`; challenge pages return `access_challenge` and require access help, not offer inference. Missing libraries/browser are explicit errors. The agent must interpret availability and evidence for the chosen variant.

### `fit.check`

```json
{"method":"fit.check","params":{"comparisons":[
  {"id":"coat_chest", "target":{"value":104,"unit":"cm","kind":"circumference","basis":"body","definition":"chest at underarms","source":"user"}, "offer":{"value":59,"unit":"cm","kind":"flat_width","basis":"garment","definition":"chest at underarms","source":"selected model size chart","evidence_scope":"exact_variant"}, "ease_cm":14, "tolerance_minus_cm":2, "tolerance_plus_cm":2}
]}}
```

Flat width is doubled explicitly. Matching units/endpoints/kinds/sources are required; body-to-garment comparisons require agent-chosen `ease_cm`. Tolerances default to zero. `estimated`, generic charts or incompatible definitions yield `unknown`; approximate measurements yield `provisional`. Result `candidate` means only the supplied comparisons pass, never guaranteed fit. Missing critical dimensions belong in the item's `fit.unresolved`. Keep known `mismatch` even if other checks pass. Save the comparison result as evidence.

### `outfits.check`

`{"method":"outfits.check","params":{"case":"/case/case.json","max_age_hours":24}}`

Checks three distinct style descriptions, IDs, categories, exact variant verification, dated online stock, known fit mismatches, and applicable goods budgets. Computes per-outfit and shared-item totals by currency. Unknown prices and delivery stay unknown. `as_of` is for reproducible replay only; normal work uses current UTC. Old/future observations request refresh. A passed check still requires visual style judgment and cannot establish measured fit or delivery from absent evidence.

### `media.download`

`{"method":"media.download","params":{"directory":"/case/references","assets":[{"item_id":"coat","url":"https://merchant.example/coat.jpg","source_page":"https://merchant.example/product"}]}}`

Downloads and validates original image files, records URLs, hashes and paths; partial failures remain explicit. Select images belonging to the actual variant, not logos, unrelated products or a default color. For marketplace image evidence, use its existing `images` operation and returned local paths instead.

### `render.prepare`

`{"method":"render.prepare","params":{"case":"/case/case.json","outfit_id":"urban","directory":"/case/render-inputs"}}`

Requires valid outfit plans, face and body files, and `image_path` for each garment. Creates original-image reference sheets and saves a tool request containing `prompt`, `referenced_image_paths` (maximum five) and `transparent_background:false`. It **does not call the generator**. Inspect the images, then pass the saved fields to native `image_gen`. One request per outfit. Optional `anchor` points to the first inspected render; optional `allow_concept:true` explicitly permits a provisional concept when shopping validation fails. At most 12 visible pieces per helper request.

### `render.attach`

`{"method":"render.attach","params":{"case":"/case/case.json","outfit_id":"urban","source_path":"/actual/generated/image.png","request_path":"/case/render-inputs/urban-request.json","visual_review":{"identity":true,"garments":true,"framing":true,"notes":[]}}}`

After inspecting the actual output, copies it to the case's `renders/` with versioning and updates the outfit with actual request, mode, time and review. Save the request actually sent if you edited a generated prompt. Never use invented file paths or assert visual checks you did not perform. `directory` optionally changes the output folder; `mode` defaults to `built-in image_gen`.

### `report`

`{"method":"report","params":{"case":"/case/case.json","directory":"/case/output"}}`

Builds `report.md`, `gallery.html`, `validation.json`. Default rejects invalid shopping plans; `allow_incomplete:true` creates an explicitly provisional report. Missing or unreviewed renders are listed and prevent `complete:true`. Open/inspect the report and all images before delivering; the scripts do not assess visual fidelity. Local HTML is a convenience gallery, not a replacement for renders. Scripts create no paid API requests, cart changes or outbound messages.
