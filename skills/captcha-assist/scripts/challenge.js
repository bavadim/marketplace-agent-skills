/* Paste into the documented browser REPL. Read-only inspection and local state;
 * actual challenge reasoning/actions remain with the agent and supported UI APIs. */
var CaptchaAssist = (() => {
  function safeUrl(raw) {
    const u = new URL(raw);
    if (u.protocol !== 'https:' || u.username || u.password) throw Error('Expected HTTPS page');
    const clean = new URL(u.origin + u.pathname);
    const sku = u.searchParams.get('sku_id');
    if (sku && /^\d+$/.test(sku)) clean.searchParams.set('sku_id', sku);
    return clean.href;
  }
  function redact(text) {
    return String(text || '').replace(/https?:\/\/[^\s<>"']+/g, raw => {
      try { return safeUrl(raw); } catch (_) { return '[URL]'; }
    });
  }
  function classify(rawUrl, page) {
    const u = new URL(rawUrl), text = page.text || '';
    const route = /_____tmd_____|\/captcha(?:\/|$)|\/challenge(?:\/|$)/i.test(u.pathname);
    const network = /ERR_(?:CONNECTION|NAME|PROXY|TIMED|NETWORK)|не удается получить доступ к сайту/i.test(text);
    const challenge = /verify.{0,30}(human|you)|подтвердите.{0,30}(вы|человек)|проверка безопасности|captcha|сдвиньте.{0,30}(ползунок|вправо)|drag.{0,30}slider/i.test(text);
    const frame = (page.frames || []).some(f => /captcha|challenge|verify|recaptcha|hcaptcha/i.test(f.url || '') || /captcha|проверка/i.test(f.title || ''));
    const status = network ? 'network_error' : !text.trim() && route && !frame ? 'challenge_not_loaded'
      : challenge || route && text.trim() ? 'challenge_visible' : frame ? 'challenge_frame'
      : !text.trim() ? 'page_not_loaded' : 'content_visible';
    return {status, url: safeUrl(rawUrl), text_excerpt: redact(text).slice(0, 1800),
      controls: (page.controls || []).map(c => ({...c,label:redact(c.label)})), frames: (page.frames || []).map(f => ({title: redact(f.title),
        url: (() => {try {return safeUrl(f.url);} catch (_) {return null;}})()})),
      observed_at: new Date().toISOString()};
  }
  async function inspect(tab) {
    const current = await tab.url();
    // Browser network-error data URLs contain internal HTML, not a challenge URL to save.
    if (!current || !current.startsWith('https:')) return {status:'network_or_navigation_error', url:null};
    const page = await tab.playwright.evaluate(() => {
      const visible = el => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
      return {text: document.body?.innerText || '',
        controls: [...document.querySelectorAll('button,input,[role=button],[role=checkbox]')].filter(visible).map(el => ({
          tag:el.tagName, type:el.getAttribute('type'), role:el.getAttribute('role'),
          label:el.getAttribute('aria-label') || el.innerText || '', disabled:!!el.disabled})),
        frames:[...document.querySelectorAll('iframe')].filter(visible).map(el => ({url:el.src,title:el.title}))};
    });
    return classify(current, page);
  }
  function begin({target_url, browser_id, tab_id, operation_id, max_attempts=2}) {
    if (![browser_id,tab_id,operation_id].every(v => typeof v === 'string' && v.length > 0))
      throw Error('Current browser, tab and operation IDs required');
    if (!Number.isInteger(max_attempts) || max_attempts < 1 || max_attempts > 2) throw Error('Attempt limit must be 1 or 2');
    return {schema_version:1,target_url:safeUrl(target_url),browser_id,tab_id,operation_id,
      attempts:0,max_attempts,status:'inspection_required',created_at:new Date().toISOString()};
  }
  function reserveAttempt(state, observation) {
    if (state.status === 'resumed') throw Error('Challenge already completed');
    if (!['challenge_visible','challenge_frame'].includes(observation.status)) throw Error('No visible challenge to solve');
    if (new URL(state.target_url).origin !== new URL(observation.url).origin) throw Error('Challenge changed origin; inspect authorization before continuing');
    if (state.attempts >= state.max_attempts) throw Error('Attempt limit reached; hand off instead of retrying');
    return {...state, attempts:state.attempts+1, status:'attempting', observed_at:observation.observed_at};
  }
  async function verify(tab, state, readyLocator) {
    if (String(tab.id) !== state.tab_id) throw Error('Use the same browser tab');
    const observation = await inspect(tab);
    const a = new URL(state.target_url), b = observation.url ? new URL(observation.url) : null;
    const target = b && a.origin === b.origin && a.pathname === b.pathname;
    const visible = target && observation.status === 'content_visible' && await readyLocator.count() === 1 && await readyLocator.isVisible();
    return {...state,status:visible ? 'resumed' : 'verification_required',
      verified_at:new Date().toISOString(),observation};
  }
  return {safeUrl,classify,begin,inspect,reserveAttempt,verify};
})();
if (typeof module !== 'undefined') module.exports = CaptchaAssist;
