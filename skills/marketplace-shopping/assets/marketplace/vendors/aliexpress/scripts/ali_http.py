#!/usr/bin/env python3
"""Read AliExpress search/products and image assets.

No network configuration changes, CAPTCHA solving, cart writes or checkout.
Search contract observed in SearchContextWidget/0.80.3 on 2026-10-07.
"""
import argparse
import datetime as dt
from decimal import Decimal, InvalidOperation
import grp
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import sys
import tempfile
from urllib.parse import urlencode, urljoin, urlsplit

from curl_probe import command, environment, header_fields, http_cookie_jar
from session_probe import allowed
from session_store import ManagedSession, SessionError, default_session, read_private

ORIGIN = "https://aliexpress.ru"
SEARCH_API = ORIGIN + "/aer-webapi/v1/search"
CDN_DOMAINS = ("aliexpress-media.com", "alicdn.com", "aestatic.net")


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def clean_text(value, limit=2000):
    return value.strip()[:limit] if isinstance(value, str) else None


def image_allowed(url):
    try:
        u = urlsplit(url)
        return (u.scheme == "https" and not u.username and not u.password
                and u.port in (None, 443) and bool(u.hostname)
                and any(u.hostname == d or u.hostname.endswith("." + d) for d in CDN_DOMAINS))
    except (ValueError, TypeError):
        return False


def image_url(url):
    """Upgrade observed legacy CDN URLs; never expand the allowed host scope."""
    if isinstance(url, str):
        url = ('https:' + url) if url.startswith('//') else re.sub(r'^http://', 'https://', url)
    return url if image_allowed(url) else None


def product_identity(url):
    u = urlsplit(url)
    m = re.fullmatch(r"/item/(\d+)\.html", u.path)
    if not allowed(url) or u.hostname != "aliexpress.ru" or not m:
        raise ValueError("Expected https://aliexpress.ru/item/<id>.html")
    return m[1], ORIGIN + u.path


def challenge(body):
    # Do not flag normal pages merely because their JS imports a captcha library.
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    return bool(re.search(r'["\']action["\']\s*:\s*["\'](?:captcha|punish)["\']', text[:100000])
                or (len(text) < 20000 and "x5referer" in text and "window.location.replace" in text))


class ReadFailure(Exception):
    def __init__(self, status, reason, http_status=None):
        self.status, self.reason, self.http_status = status, reason, http_status
        super().__init__(reason)


class PageData(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.aer = None
        self.jsonld = []
        self.kind = None
        self.parts = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            a = dict(attrs)
            self.kind = "aer" if a.get("id") == "__AER_DATA__" else (
                "jsonld" if a.get("type") == "application/ld+json" else None)
            self.parts = []

    def handle_data(self, data):
        if self.kind:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.kind:
            try:
                parsed = json.loads("".join(self.parts))
                if self.kind == "aer":
                    self.aer = parsed
                else:
                    self.jsonld.append(parsed)
            except ValueError:
                pass
            self.kind = None


def login_state(aer):
    for node in walk(aer):
        # Only booleans/statuses, never account identifiers or profile fields.
        if "loginStatusV2" in node:
            state = node["loginStatusV2"]
            if state in ("LOGGED_IN", "ANONYMOUS"):
                return state == "LOGGED_IN"
            # AUTO_LOGIN still requires the browser flow; UNKNOWN is not a login.
            return None
        if "loginStatus" in node:
            if node.get("isLoggedIn") is True:
                return True
            if node.get("isLoggedIn") is False:
                return False
    return None


class LegacyHTTPClient:
    def __init__(self, session=None, use_saved_session=True):
        if session is None and use_saved_session and default_session().exists():
            session = default_session()
        self.session_path = Path(session) if session else None
        self.managed = None
        self.tmp = tempfile.TemporaryDirectory(prefix="ali-http-")
        self.root = Path(self.tmp.name)
        self.jar = self.root / "cookies.txt"
        self.jar.touch(mode=0o600)
        try:
            if session:
                if not Path(session).exists():
                    raise SessionError("authentication_required")
                if read_private(session).get("managed_session") is True:
                    self.managed = ManagedSession(session)
            http_cookie_jar(session).save(str(self.jar), ignore_discard=True, ignore_expires=False)
        except BaseException as error:
            self.__exit__()
            if isinstance(error, SessionError):
                raise ReadFailure(error.status, "Saved session unavailable; use ali_session.py ensure") from error
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if self.managed:
            self.managed.close()
        self.tmp.cleanup()

    def auth_observed(self, state):
        if self.managed:
            self.managed.auth_observed(state)

    def fetch(self, url, payload=None, referer=None, asset=False, method=None, aer_url=None):
        if aer_url is not None:
            if '\r' in aer_url or '\n' in aer_url: raise ValueError('Invalid aer-url header')
            product_identity(aer_url)
            if asset or urlsplit(url).hostname != 'aliexpress.ru' or urlsplit(url).path != '/widget':
                raise ValueError('aer-url is only supported for product widget reads')
        method = method or ("POST" if payload is not None else "GET")
        if method not in ("GET", "POST") or (method == "GET" and payload is not None):
            raise ValueError("Only GET and POST are supported")
        for _ in range(5):
            if not (image_allowed(url) if asset else allowed(url)):
                raise ReadFailure("outside_scope", "Destination outside allowed HTTPS scope")
            header = self.root / "headers"
            body = self.root / "body"
            for p in (header, body):
                p.touch(mode=0o600)
                p.write_bytes(b"")
            if asset:
                # CDN requests never receive the user's AliExpress cookie jar.
                args = ["curl", "-q", "--proto", "=https", "--globoff", "--silent", "--show-error",
                        "--connect-timeout", "8", "--max-time", "20", "--max-filesize", "12582912",
                        "--noproxy", "*", "--proxy", "", "--dump-header", str(header), "--output", str(body),
                        "--write-out", "%{http_code}\t%{time_total}\t%{http_version}\t%{remote_ip}\t%{proxy_used}\t%{ssl_verify_result}"]
            else:
                args = command(url, self.jar, header, body, timeout=20)[:-1]
            args += ["--compressed"]
            if aer_url is not None:
                args += ['--header', 'aer-url: ' + aer_url]
            if payload is not None:
                request = self.root / "request.json"
                request.touch(mode=0o600)
                request.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                args += ["--header", "Content-Type: application/json", "--header", "Origin: " + ORIGIN,
                         "--data-binary", "@" + str(request)]
            elif method == "POST":
                args += ["--request", "POST", "--header", "Content-Type: application/json",
                         "--header", "Origin: " + ORIGIN]
            if referer and not asset:
                args += ["--referer", referer]
            run = subprocess.run(args + [url], capture_output=True, text=True,
                                 env=environment("direct"), timeout=25)
            if self.managed and not asset:
                self.managed.save(self.jar)
            if run.returncode:
                raise ReadFailure("transport_error", "curl_exit_" + str(run.returncode))
            fields = run.stdout.strip().split("\t")
            if len(fields) != 6 or not fields[0].isdigit():
                raise ReadFailure("transport_error", "Unexpected curl metadata")
            status = int(fields[0])
            headers = header_fields(header.read_text(errors="replace"))
            data = body.read_bytes()
            if challenge(data):
                raise ReadFailure("requires_browser_verification", "captcha", status)
            if status in (301, 302, 303, 307, 308):
                location = headers.get("location")
                if not location or method == "POST":
                    raise ReadFailure("redirect", "Unexpected redirect; stopped", status)
                url = urljoin(url, location)
                continue
            if status == 429:
                raise ReadFailure("rate_limited", "HTTP 429; stopped without retry", status)
            if not 200 <= status < 300:
                raise ReadFailure("http_error", "HTTP request failed", status)
            return data, {"http_status": status, "seconds": float(fields[1]), "http_version": fields[2],
                          "curl_proxy_used": fields[4] == "1", "tls_verify_result": int(fields[5]),
                          "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        raise ReadFailure("redirect", "Too many redirects")


class Client:
    """Site requests run inside shared headless Chromium, including cart APIs."""
    def __init__(self, session=None, use_saved_session=True):
        runtime = Path(__file__).resolve().parents[3]
        if str(runtime) not in sys.path: sys.path.insert(0, str(runtime))
        from browser_runtime import ManagedBrowser
        self.browser = ManagedBrowser('aliexpress', session, use_saved_session)

    def __enter__(self): return self

    def __exit__(self, *args): self.browser.close()

    def auth_observed(self, state): self.browser.observe_auth(state)

    def fetch(self, url, payload=None, referer=None, asset=False, method=None, aer_url=None):
        import base64,time
        method = method or ('POST' if payload is not None else 'GET')
        if method not in ('GET','POST') or method=='GET' and payload is not None:
            raise ValueError('Only GET and POST are supported')
        if not (image_allowed(url) if asset else allowed(url)):
            raise ReadFailure('outside_scope','Destination outside allowed HTTPS scope')
        if referer and not allowed(referer): raise ReadFailure('outside_scope','Referer outside allowed HTTPS scope')
        if aer_url is not None:
            product_identity(aer_url)
            if asset or urlsplit(url).path!='/widget': raise ValueError('aer-url is only supported for product widget reads')
        started=time.monotonic()
        try:
            context=self.browser.context;page=self.browser.page
            def scoped_navigation(route):
                if route.request.is_navigation_request() and route.request.frame==page.main_frame and not allowed(route.request.url): route.abort()
                else: route.fallback()
            if not getattr(self,'_routed',False):
                page.route('**/*',scoped_navigation);self._routed=True
            if asset:
                # An empty context prevents account credentials reaching image hosts.
                isolated=context.browser.new_context()
                try:
                    image_page=isolated.new_page()
                    image_page.route('**/*',lambda route: route.fallback() if image_allowed(route.request.url) else route.abort())
                    response=image_page.goto(url,wait_until='load',timeout=20000)
                    if not response or not image_allowed(response.url): raise ReadFailure('outside_scope','Asset navigation failed')
                    code,data=response.status,response.body()
                finally: isolated.close()
            elif method=='GET' and not urlsplit(url).path.startswith(('/aer-','/widget')):
                response=page.goto(url,wait_until='domcontentloaded',timeout=30000);page.wait_for_timeout(1200)
                if not allowed(page.url): raise ReadFailure('outside_scope','Navigation left the site')
                code=response.status if response else 0;data=page.content().encode('utf-8')
            else:
                if not allowed(page.url):
                    page.goto(referer or ORIGIN,wait_until='domcontentloaded',timeout=30000);page.wait_for_timeout(1000)
                if challenge(page.content()): raise ReadFailure('requires_browser_verification','captcha')
                headers={'Content-Type':'application/json'}
                if aer_url: headers['aer-url']=aer_url
                result=page.evaluate('''async ({url,method,payload,headers,referer}) => {
                  const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),20000);
                  try {const response=await fetch(url,{method,headers,credentials:'include',redirect:'manual',signal:controller.signal,
                    referrer:referer||undefined,body:payload===null?undefined:JSON.stringify(payload)});
                    if(response.type==='opaqueredirect')return {redirect:true};
                    const bytes=new Uint8Array(await response.arrayBuffer());if(bytes.length>12582912)return {oversized:true};
                    let text='';for(let i=0;i<bytes.length;i+=8192)text+=String.fromCharCode(...bytes.subarray(i,i+8192));
                    return {status:response.status,data:btoa(text)};
                  }finally{clearTimeout(timer);}
                }''',{'url':url,'method':method,'payload':payload,'headers':headers,'referer':referer})
                if result.get('redirect'): raise ReadFailure('redirect','API redirect stopped without replay')
                if result.get('oversized'): raise ReadFailure('transport_error','response_too_large')
                code,data=result['status'],base64.b64decode(result['data'])
            if len(data)>12582912: raise ReadFailure('transport_error','response_too_large')
            if not asset and challenge(data): raise ReadFailure('requires_browser_verification','captcha',code)
            if code==429: raise ReadFailure('rate_limited','HTTP 429; stopped without retry',code)
            if not 200<=code<300: raise ReadFailure('http_error','Browser request failed',code)
            return data,{'http_status':code,'seconds':time.monotonic()-started,'client':'chromium-headless','headless':True,
                         'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
        except ReadFailure: raise
        except Exception as error:
            raise ReadFailure(getattr(error,'status','browser_error'),'Chromium request failed') from error


def gallery(snippet):
    urls = []
    for node in walk(snippet.get("presentations", [])):
        g = node.get("gallery")
        if isinstance(g, dict):
            for url in g.get("images", []):
                if image_allowed(url) and url not in urls:
                    urls.append(url)
    return [{"url": url, "role": "search_gallery", "vision_status": "pending"} for url in urls]


def price_value(value):
    try:
        amount = Decimal(str(value))
        return format(amount, "f") if amount.is_finite() and amount >= 0 else None
    except InvalidOperation:
        return None


def parse_search(payload, query, search_url, stamp, requested_page=1):
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("productsFeed"), dict):
        raise ReadFailure("unsupported_response", "Missing data.productsFeed")
    feed = data["productsFeed"]
    entries = feed.get("productsV2")
    if not isinstance(entries, list):
        raise ReadFailure("unsupported_response", "Search feed schema changed; expected productsV2")
    products, skipped = [], 0
    for entry in entries:
        try:
            snippet = entry["snippetContainer"]
            item = snippet["itemData"]
            props, pdp = item["properties"], item["pdpInfo"]
            info = pdp["preloadedData"]
            pid, canonical = product_identity(pdp["url"])
            if str(props["id"]) != pid or not clean_text(info.get("title")):
                raise ValueError("Missing title / product ID mismatch")
            price = info.get("price") or {}
            store = info.get("store") or {}
            store_url = store.get("url")
            if not isinstance(store_url, str) or not allowed(store_url):
                store_url = None
            else:
                store_url = ORIGIN + urlsplit(store_url).path
            sku = str(props.get("preselectSkuId") or "")
            # Drop affiliate/tracking query parameters, keep only the observed SKU.
            listing_url = canonical + ("?" + urlencode({"sku_id": sku}) if sku.isdigit() else "")
            images = gallery(snippet)
            products.append({"schema_version": 1, "product_id": pid, "url": canonical,
                "listing_url": listing_url, "observed_at": stamp, "title": info["title"],
                "status": "needs_review", "detail_status": "not_requested", "facts": {},
                "listing": {"price": {"value": price_value(price.get("value")),
                    "currency": clean_text(price.get("currency")), "display": clean_text(price.get("displayPrice")),
                    "selected_variant_verified": False}, "rating": clean_text(info.get("rating")),
                    "sales_display": clean_text(info.get("salesCount")),
                    "store": {"name": clean_text(store.get("name")), "url": store_url},
                    "preselected_sku_id": sku if sku.isdigit() else None},
                "evidence": [{"id": "search-1", "method": "platform_search", "url": search_url,
                    "endpoint": SEARCH_API, "text": info["title"] + " | " + (clean_text(price.get("displayPrice")) or "")}],
                "images": images,
                "unresolved": ["selected variant", "price per purchase unit", "units in one pack",
                               "delivery", "product description", "image specifications"]})
        except (KeyError, TypeError, ValueError):
            skipped += 1
    if entries and not products:
        raise ReadFailure("unsupported_response", "No readable product snippets in nonempty feed")
    if not entries and feed.get("products"):
        raise ReadFailure("unsupported_response", "Legacy products feed requires a new parser")
    notices = []
    for error in data.get("errors") or []:
        if isinstance(error, dict):
            notices.append({"code": clean_text(error.get("error")), "level": error.get("level")})
    pagination = data.get("pagination") or {}
    total = pagination.get("totalPages")
    if not entries and (notices or (isinstance(total, (int, float)) and total > 0)):
        raise ReadFailure("incomplete_response", "Empty feed with service errors or nonempty pagination")
    status = "partial" if skipped or any(n["level"] not in (0, None) for n in notices) else ("ok" if products else "empty")
    return {"schema_version": 1, "kind": "search", "observed_at": stamp, "query": query,
            "url": search_url, "status": status, "requested_page": requested_page,
            "pagination": {"current_page": pagination.get("currentPage"), "total_pages": total},
            "count": len(products), "skipped_entries": skipped, "service_notices": notices, "products": products}


def search(client, query, page):
    search_url = ORIGIN + "/wholesale?" + urlencode({"SearchText": query, "page": page})
    raw, page_meta = client.fetch(search_url)
    page_data = PageData(raw.decode("utf-8", "replace"))
    if not isinstance(page_data.aer, dict):
        raise ReadFailure("unsupported_response", "Search page missing __AER_DATA__")
    payload = {"catId": "", "searchText": query, "page": page, "g": "y", "searchInfo": "",
               "mainFilters": "", "storeIds": [], "pgChildren": [], "aeBrainIds": [], "source": "direct"}
    raw, api_meta = client.fetch(SEARCH_API, payload, search_url)
    try:
        parsed = json.loads(raw)
    except ValueError:
        raise ReadFailure("unsupported_response", "Search API returned non-JSON")
    if not isinstance(parsed, dict):
        raise ReadFailure("unsupported_response", "Search API returned unexpected JSON")
    result = parse_search(parsed, query, search_url, now(), page)
    result.update(authenticated=login_state(page_data.aer), transport={"page": page_meta, "search_api": api_meta})
    return result


def input_cards(data):
    """Accept a product, single search or batch_search shortlist, without losing images."""
    if not isinstance(data, dict):
        raise ValueError("Expected a product, search result or batch shortlist")
    if data.get("kind") == "search":
        return data.get("products", [])
    if isinstance(data.get("rows"), list):
        # The same public listing can appear in several BOM rows/queries.
        unique = {}
        for row in data["rows"]:
            for card in row.get("candidates", []):
                unique.setdefault(card["product_id"], card)
        return list(unique.values())
    return [data]


def product(client, url, seed=None):
    pid, canonical = product_identity(url)
    if seed:
        candidates = input_cards(seed)
        card = next((json.loads(json.dumps(x)) for x in candidates if x.get("product_id") == pid), None)
    else:
        card = None
    if card is None:
        card = {"schema_version": 1, "product_id": pid, "url": canonical, "title": None,
                "facts": {}, "evidence": [], "images": [], "unresolved": ["product detail", "selected variant"]}
    card.update(kind="product", status="needs_review", detail_observed_at=now())
    card.setdefault("observed_at", card["detail_observed_at"])
    try:
        raw, meta = client.fetch(url)
    except ReadFailure as e:
        card.update(detail_status=e.status, detail_error={"reason": e.reason, "http_status": e.http_status})
        return card
    card["transport"] = meta
    page = PageData(raw.decode("utf-8", "replace"))
    card["authenticated"] = login_state(page.aer)
    if any('GlowAsyncProductContextWidget/' in str(n.get('widgetId', '')) for n in walk(page.aer)):
        from pdp import read_product
        try:
            return read_product(client, url, page, card)
        except ReadFailure as e:
            card.update(detail_status=e.status, detail_error={'reason':e.reason,'http_status':e.http_status})
            return card
    products = [n for n in walk(page.jsonld) if n.get("@type") == "Product"
                or (isinstance(n.get("@type"), list) and "Product" in n["@type"])]
    if len(products) != 1:
        card["detail_status"] = "unsupported_page"
        card["detail_error"] = {"reason": "No unique schema.org Product; needs browser observation"}
        return card
    p = products[0]
    # Static structured data is a listing, not a confirmed live SKU selection.
    card["structured_listing"] = {k: clean_text(p.get(k), 12000) for k in ("name", "description", "sku", "mpn")}
    card["title"] = clean_text(p.get("name")) or card.get("title")
    card["detail_status"] = "structured_data_only"
    card["evidence"].append({"id": "jsonld-1", "method": "structured_data", "url": canonical,
                             "text": card["structured_listing"]["description"] or card["title"]})
    images = p.get("image") or []
    if isinstance(images, str):
        images = [images]
    for image in images if isinstance(images, list) else []:
        image = image.get("url") if isinstance(image, dict) else image
        if image_allowed(image) and image not in {i["url"] for i in card["images"]}:
            card["images"].append({"url": image, "role": "structured_listing", "vision_status": "pending"})
    return card


def parse_store(body, store_id, transport=None):
    """Read public store facts only; exclude account, regional and tracking props."""
    if not re.fullmatch(r"\d{1,30}", str(store_id)):
        raise ValueError("Numeric store ID required")
    url = ORIGIN + "/store/" + str(store_id)
    page = PageData(body.decode("utf-8", "replace"))
    matches = [node.get("props", {}) for node in walk(page.aer)
               if str(node.get("widgetId", "")).startswith("bx/SnowShopContext/")]
    if len(matches) != 1 or matches[0].get("shopUrl") != url:
        raise ReadFailure("unsupported_response", "Store identity or public context missing")
    props = matches[0]
    stats = props.get("sellerStats") or {}
    rating = stats.get("sellerRating")
    if isinstance(rating, bool) or not isinstance(rating, (int, float)) or not 0 <= rating <= 100:
        rating = None
    return {"schema_version": 1, "kind": "store", "status": "ok", "observed_at": now(),
            "seller": {"id": str(store_id), "name": clean_text(props.get("name")), "url": url,
                       "rating_kind": "seller", "rating": rating, "scale": 100,
                       "review_count": None},
            "store_statistics": {key: stats.get(key) for key in
                                 ("productsRating", "reviewsCount", "deliveredOrdersCount",
                                  "productsCount", "averageShippingTimeDays")},
            "notes": ["reviewsCount is a store product-review count, not a verified seller-rating denominator",
                      "averageShippingTimeDays is not a destination-specific delivery quote"],
            "source": {"url": url, "method": "structured_data", "widget": "SnowShopContext",
                       "sha256": hashlib.sha256(body).hexdigest()}, "transport": transport or {}}


def store(client, store_id):
    if not re.fullmatch(r"\d{1,30}", str(store_id)):
        raise ValueError("Numeric store ID required")
    body, meta = client.fetch(ORIGIN + "/store/" + str(store_id))
    return parse_store(body, store_id, meta)


def download_images(client, input_data, pid, limit, output, ocr=None):
    from vision import prepare
    cards = input_cards(input_data)
    cards = [c for c in cards if not pid or c.get("product_id") == pid]
    if len(cards) != 1:
        raise ValueError("Choose one product with --product-id")
    card = cards[0]
    if not re.fullmatch(r"\d+", str(card.get("product_id", ""))):
        raise ValueError("Product ID must contain only digits")
    by_url = {}
    for entry in card.get('images', []):
        url = image_url(entry.get('url'))
        if not url: continue
        previous = by_url.setdefault(url, {**entry, 'url':url, 'roles':[], 'sku_ids':[]})
        previous['roles'] = list(dict.fromkeys(previous['roles'] + entry.get('roles', [entry.get('role')])) )
        previous['sku_ids'] = list(dict.fromkeys(previous['sku_ids'] + entry.get('sku_ids', [])))
    entries = list(by_url.values())[:limit]
    result = {"schema_version": 1, "kind": "images", "product_id": card["product_id"],
              "observed_at": now(), "status": "ok", "images": [], "vision_status": "pending",
              "available_count":len(by_url), "truncated":len(by_url)>limit}
    asset_root = output.parent / (output.stem + "-assets")
    asset_root.mkdir(parents=True, exist_ok=True)
    for index, entry in enumerate(entries, 1):
        url = entry["url"]
        try:
            cache_key = hashlib.sha256(url.encode()).hexdigest()
            cached = asset_root / (cache_key + '.json')
            attribution = {k:entry.get(k) for k in ('url', 'role', 'roles', 'sku_ids')}
            if cached.exists():
                try: old = read_json(cached)
                except (ValueError, OSError): old = {}
                if not isinstance(old, dict): old = {}
                source = Path(old.get('source', ''))
                if source.is_file() and hashlib.sha256(source.read_bytes()).hexdigest() == old.get('sha256') and all(Path(t['path']).is_file() for t in old.get('tiles', [])) and old.get('ocr_language') == ocr:
                    result['images'].append({**old, **attribution, 'cache_hit':True}); continue
            body, meta = client.fetch(url, asset=True)
            path = asset_root / (cache_key + '.image')
            path.write_bytes(body)
            manifest = prepare(path, asset_root / "tiles", language=ocr)
            manifest.update(**attribution, transport=meta)
            manifest['ocr_language'] = ocr
            cached.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
            result["images"].append(manifest)
        except (ReadFailure, OSError, ValueError, RuntimeError) as e:
            result["status"] = "partial"
            result["images"].append({"url": url, "status": getattr(e, "status", "image_error"),
                                      "error_type": type(e).__name__})
            if isinstance(e, ReadFailure) and e.status in ("rate_limited", "requires_browser_verification"):
                break
    if not entries:
        result["status"] = "no_images"
    return result



def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    subs = p.add_subparsers(dest="operation", required=True)
    s = subs.add_parser("search")
    s.add_argument("--query", required=True)
    s.add_argument("--page", type=int, default=1)
    c = subs.add_parser("product")
    c.add_argument("--url", required=True)
    c.add_argument("--from-search")
    st = subs.add_parser("store")
    st.add_argument("--store-id", required=True)
    i = subs.add_parser("images")
    i.add_argument("--input", required=True)
    i.add_argument("--product-id")
    i.add_argument("--limit", type=int, default=30)
    i.add_argument("--ocr", help="Optional installed Tesseract language, e.g. eng")
    for sub in (s, c, i, st):
        sub.add_argument("--session", help="Managed AliExpress session (optional)")
        sub.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    if a.operation == "search" and (a.page < 1 or not a.query.strip()):
        p.error("Nonempty query and page >= 1 required")
    if a.operation == "images" and not 1 <= a.limit <= 100:
        p.error("Image limit must be 1..100")
    try:
        with Client(a.session) as client:
            if a.operation == "search":
                result = search(client, a.query, a.page)
            elif a.operation == "product":
                result = product(client, a.url, read_json(a.from_search) if a.from_search else None)
            elif a.operation == "store":
                result = store(client, a.store_id)
            else:
                result = download_images(client, read_json(a.input), a.product_id, a.limit, a.output, a.ocr)
    except ReadFailure as e:
        result = {"status": e.status, "reason": e.reason, "http_status": e.http_status}
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as e:
        # Do not echo exception messages containing raw payloads or secret file contents.
        result = {"status": "failed", "error_type": type(e).__name__}
    result["network"] = {"effective_gid": os.getegid(), "effective_group": grp.getgrgid(os.getegid()).gr_name,
                         "client_proxy": "disabled", "cookies_supplied": bool(a.session or default_session().exists()),
                         "login_verified": result.get("authenticated") is True}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(a.output, 0o600)
    print(json.dumps({"output": str(a.output.resolve()), "status": result["status"],
                      "detail_status": result.get("detail_status"), "count": result.get("count"),
                      "effective_group": result["network"]["effective_group"]}, ensure_ascii=False))
    success = result["status"] in ("ok", "empty") or result.get("detail_status") in ("ok", "structured_data_only")
    return 0 if success else 2


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from launcher import launch
    launch()
    sys.exit(main())
