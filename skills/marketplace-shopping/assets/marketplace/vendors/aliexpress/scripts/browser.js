/* Paste this file into the initialized CUA REPL, then call AliBom methods.
 * Uses only documented tab APIs. No cookies, hidden state, fetch, CDP or payment.
 * Selectors supplied by the caller must come from a fresh page observation.
 */
var AliBom = (() => {
  const attempted = new Set();
  function productUrl(raw) {
    const u = new URL(raw, "https://aliexpress.ru");
    if (u.protocol !== "https:" || u.hostname !== "aliexpress.ru" || u.username || u.password)
      throw new Error("Expected an aliexpress.ru HTTPS product URL");
    const m = u.pathname.match(/^\/item\/(\d+)\.html$/);
    if (!m) throw new Error("Not a product URL");
    const sku = u.searchParams.get("sku_id");
    if (u.searchParams.getAll("sku_id").length > 1 || (sku !== null && !/^\d+$/.test(sku)))
      throw new Error("Ambiguous or invalid SKU in product URL");
    const canonical = `https://aliexpress.ru/item/${m[1]}.html`;
    return {url: canonical + (sku ? `?sku_id=${sku}` : ""), canonical_url: canonical,
      product_id: m[1], sku_id: sku};
  }

  async function observe(tab) {
    // Only rendered DOM data; no script contents, app stores or browser storage.
    const result = await tab.playwright.evaluate(() => {
      const visible = e => e.getClientRects().length > 0 &&
        getComputedStyle(e).visibility !== "hidden" && getComputedStyle(e).display !== "none";
      const text = document.body?.innerText || "";
      const links = [...document.querySelectorAll("a[href]")].filter(visible).map(a => ({
        url: a.href, text: (a.innerText || a.getAttribute("aria-label") || "").trim(),
        context: (a.parentElement?.innerText || "").slice(0, 1500)
      }));
      const images = [...document.images].filter(visible).map((im, index) => ({
        evidence_id: `image-${index + 1}`, url: im.currentSrc || im.src, alt: im.alt,
        width: im.naturalWidth, height: im.naturalHeight,
        loaded: im.complete && im.naturalWidth > 0,
        context: (im.parentElement?.innerText || "").slice(0, 400)
      }));
      return {title: document.title, heading: [...document.querySelectorAll("h1")]
        .filter(visible).map(e => e.innerText), text, links, images,
        resources: [...document.querySelectorAll("script[src],link[href]")].map(e => e.src || e.href),
        buttons: [...document.querySelectorAll("button,[role=button]")].filter(visible).map(e => ({
          text: (e.innerText || e.getAttribute("aria-label") || "").trim(),
          disabled: !!e.disabled || e.getAttribute("aria-disabled") === "true"
        }))};
    });
    const current = new URL(await tab.url());
    if (current.hostname !== "aliexpress.ru") throw new Error("Not on aliexpress.ru: inspect login/redirect separately");
    // Preserve the search query only; discard tracking and session parameters.
    const clean = new URL(current.origin + current.pathname);
    if (current.searchParams.has("SearchText")) clean.searchParams.set("SearchText", current.searchParams.get("SearchText"));
    if (/^\/item\/\d+\.html$/.test(current.pathname)) {
      const sku = productUrl(current.href).sku_id;
      if (sku) clean.searchParams.set("sku_id", sku);
    }
    result.url = clean.href;
    result.captured_at = new Date().toISOString();
    result.schema_version = 1;
    result.kind = "browser_observation";
    result.candidates = [];
    const seen = new Set();
    for (const link of result.links) {
      try {
        const p = productUrl(link.url);
        if (!seen.has(p.product_id)) {
          result.candidates.push({...p, title_raw: link.text, card_text_raw: link.context});
          seen.add(p.product_id);
        }
      } catch (_) {}
    }
    // Drop unrelated links and their query strings (including sign-in/session URLs).
    delete result.links;
    result.resource_hosts = [...new Set(result.resources.map(u => {try {return new URL(u).hostname;} catch (_) {return null;}}).filter(Boolean))];
    delete result.resources;
    const blocked = /подтвердите.{0,30}(вы|человек)|проверка безопасности|доступ ограничен|access denied|verify you are human/i.test(result.text);
    const empty = /ничего не найдено|товары не найдены|нет результатов/i.test(result.text);
    // An empty search may still show recommended products elsewhere on the page.
    result.search_status = blocked ? "blocked" : empty ? "empty" : result.candidates.length ? "results" : "not_ready";
    return result;
  }

  async function search(tab, query) {
    if (typeof query !== "string" || !query.trim()) throw new Error("Nonempty search query required");
    await tab.goto(`https://aliexpress.ru/wholesale?SearchText=${encodeURIComponent(query.trim())}`);
    await tab.playwright.domSnapshot();
    return observe(tab); // not_ready is not an empty catalogue; diagnose before retrying.
  }

  async function product(tab, url) {
    const p = productUrl(url);
    let current;
    try { current = productUrl(await tab.url()); } catch (_) {}
    // A generic link must not reset a choice already made on this product page.
    if (!current || current.product_id !== p.product_id || (p.sku_id && p.sku_id !== current.sku_id))
      await tab.goto(p.url);
    await tab.playwright.domSnapshot();
    const observation = await observe(tab);
    if (productUrl(observation.url).product_id !== p.product_id) throw new Error("Unexpected product redirect");
    return {...observation, product_id: p.product_id, requested_sku_id: p.sku_id,
      url_sku_id: productUrl(observation.url).sku_id,
      variant_verified: false, kind: "product_observation"}; // Verify visible selected options and price.
  }

  async function variantControls(tab, observedScopeSelector) {
    // Scope must come from the current product DOM; no guessed AliExpress CSS classes.
    const identity = productUrl(await tab.url());
    const scope = tab.playwright.locator(observedScopeSelector);
    if (await scope.count() !== 1 || !await scope.isVisible()) throw new Error("Variant scope must be unique and visible");
    const controls = await scope.evaluate(root => {
      const visible = el => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== "hidden";
      const selected = el => el.matches('input[type=radio],input[type=checkbox]') ? el.checked :
        el.hasAttribute('aria-checked') ? el.getAttribute('aria-checked') === 'true' :
        el.hasAttribute('aria-selected') ? el.getAttribute('aria-selected') === 'true' :
        el.hasAttribute('aria-pressed') ? el.getAttribute('aria-pressed') === 'true' : null;
      return {text:root.innerText,
        controls:[...root.querySelectorAll('button,select,input[type=radio],input[type=checkbox],[role=radio],[role=option],[role=button],[title]')]
          .filter(visible).map(el => ({tag:el.tagName,role:el.getAttribute('role'),
            text:(el.innerText || '').trim(),label:el.getAttribute('aria-label'),title:el.getAttribute('title'),
            classes:el.className,disabled:!!el.disabled || el.getAttribute('aria-disabled') === 'true',selected:selected(el),
            options:el.tagName === 'SELECT' ? [...el.options].map(o=>({label:o.label,value:o.value,selected:o.selected,disabled:o.disabled})) : null}))};
    });
    return {...identity,...controls,kind:'variant_controls',observed_at:new Date().toISOString(),
      complete_sku_catalog:false}; // Conditional controls are not a Cartesian catalogue.
  }

  async function chooseVariantOption(tab, {product_id, optionLocator, nativeLabel}) {
    if (productUrl(await tab.url()).product_id !== product_id) throw new Error('Wrong product page');
    if (await optionLocator.count() !== 1 || !await optionLocator.isVisible() || !await optionLocator.isEnabled())
      throw new Error('Option must be unique, visible and enabled');
    if (/^(в корзину|добавить в корзину|купить сейчас)$/i.test((await optionLocator.innerText()).trim()))
      throw new Error('This helper selects SKU options, not purchase buttons');
    if (nativeLabel !== undefined) await optionLocator.selectOption({label:nativeLabel});
    else await optionLocator.click();
    return {status:'selection_verification_required',product_id,snapshot:await tab.playwright.domSnapshot()};
  }

  async function addOne(tab, request) {
    // A guarded ONE-CLICK primitive, not an unattended selector-guessing loop.
    // Caller selects variant/quantity in UI and checks price and existing cart first.
    if (!request.authorized || request.variant_verified !== true || request.quantity_verified !== true)
      throw new Error("Need current authorization and verified selected variant/quantity");
    if (request.cart_baseline_verified !== true || request.price_verified !== true)
      throw new Error("Verify existing cart and current price before adding");
    if (request.operation_status !== "new" || attempted.has(request.operation_id))
      throw new Error("Do not retry an uncertain/completed add; inspect cart");
    const current = productUrl(await tab.url());
    if (current.product_id !== request.product_id) throw new Error("Product differs from plan");
    if (!Number.isInteger(request.quantity) || request.quantity < 1) throw new Error("Invalid purchase quantity");
    if (!request.sku_key || !request.operation_id) throw new Error("SKU key and operation ID required");
    if (!/^(в корзину|добавить в корзину)$/i.test(request.button_name || ""))
      throw new Error("Only the observed Add to cart button is allowed");
    const button = tab.playwright.getByRole("button", {name: request.button_name, exact: true});
    if (await button.count() !== 1 || !await button.isEnabled()) throw new Error("Cart button is ambiguous or disabled");
    attempted.add(request.operation_id); // Also persist an attempting record BEFORE this call.
    await button.click();
    const snapshot = await tab.playwright.domSnapshot();
    return {operation_id: request.operation_id, product_id: current.product_id,
      sku_key: request.sku_key, quantity: request.quantity,
      status: "verification_required", snapshot};
  }

  return {productUrl, observe, search, product, variantControls, chooseVariantOption, addOne};
})();
if (typeof module !== "undefined") module.exports = AliBom;
