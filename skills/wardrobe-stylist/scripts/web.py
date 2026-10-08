"""Public merchant evidence and original image downloads."""
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import urljoin, urlsplit
from urllib.request import build_opener, ProxyHandler, Request

from style import now, output_path, save


def http_url(value):
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise ValueError("Expected public http(s) URL without embedded credentials")
    return value



def read_url(url, max_bytes):
    http_url(url)
    opener = build_opener(ProxyHandler({}))
    request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"})
    with opener.open(request, timeout=35) as response:
        blob = response.read(max_bytes + 1)
        if len(blob) > max_bytes:
            raise ValueError("Response exceeds allowed size")
        return blob, response.headers, response.url, response.status


def fetch(params):
    from bs4 import BeautifulSoup
    url = http_url(params["url"])
    blob, headers, final, status = read_url(url, 8 * 1024 * 1024)
    text = blob.decode(headers.get_content_charset() or "utf-8", errors="replace")
    directory = output_path(Path(params["directory"]) / "placeholder").parent
    key = hashlib.sha256(url.encode()).hexdigest()[:12]
    raw = output_path(directory / f"page-{key}.html")
    raw.write_bytes(blob)
    soup = BeautifulSoup(text, "html.parser")
    structured = []
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            structured.append(json.loads(tag.string or tag.get_text()))
        except (ValueError, TypeError):
            pass
    images = []
    for tag in soup.select('meta[property="og:image"], meta[name="twitter:image"], img, a[href]'):
        for attr in ("content", "src", "data-src", "data-original", "href"):
            value = tag.get(attr)
            if not value:
                continue
            image_url = urljoin(final, value)
            path = urlsplit(image_url).path.lower()
            if (attr == "content" or path.endswith((".jpg", ".jpeg", ".png", ".webp", ".avif"))) and image_url.startswith(("http://", "https://")):
                if image_url not in images:
                    images.append(image_url)
    tables = [t.get_text(" | ", strip=True) for t in soup.select("table")]
    for tag in soup.select("script,style,noscript,nav,footer,header"):
        tag.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    evidence = {"url": url, "final_url": final, "http_status": status, "observed_at": now(),
                "html_path": str(raw), "sha256": hashlib.sha256(blob).hexdigest(),
                "text": main.get_text(" ", strip=True)[:params.get("text_limit", 16000)],
                "tables": tables, "json_ld": structured, "image_candidates": images[:80],
                "variant_verified": False, "stock_verified": False,
                "network_gid": os.getgid(), "requires_selected_variant_review": True}
    evidence["evidence_path"] = save(directory / f"page-{key}.json", evidence)
    return evidence


def inspect_merchant(params):
    """A temporary headless context for direct-shop variant selectors, never a desktop profile."""
    try:
        from patchright.sync_api import sync_playwright
    except ImportError:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            runtime_python = Path(params.get("browser_python") or
                                  (Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "ozon-bom/venv/bin/python"))
            if not runtime_python.is_file() or str(runtime_python.absolute()) == str(Path(sys.executable).absolute()):
                raise ImportError("merchant.inspect needs an existing patchright/playwright runtime; set browser_python")
            worker = subprocess.run([str(runtime_python), str(Path(__file__).with_name("style.py")), "call", "--request", "-"],
                                    input=json.dumps({"method": "merchant.inspect", "params": params}),
                                    text=True, capture_output=True, timeout=180)
            try:
                answer = json.loads(worker.stdout)
            except json.JSONDecodeError as e:
                raise ValueError("Browser runtime returned no JSON") from e
            if not answer.get("ok"):
                raise ValueError(answer.get("error", {}).get("message", "Browser runtime failed"))
            return answer["data"]
    url = http_url(params["url"])
    directory = output_path(Path(params["directory"]) / "placeholder").parent
    key = hashlib.sha256((url + json.dumps(params.get("actions", []), sort_keys=True)).encode()).hexdigest()[:12]
    env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY") and not k.lower().endswith("_proxy")}
    executable = params.get("executable_path") or shutil.which("chromium") or shutil.which("chromium-browser")
    forbidden = re.compile(r"add.?to.?cart|checkout|payment|buy.?now|в.?корзин|оформить|оплатить|купить|отправить|submit", re.I)
    log = []
    with sync_playwright() as runtime:
        options = {"headless": True, "chromium_sandbox": True, "args": ["--no-proxy-server"], "env": env}
        if executable:
            options["executable_path"] = executable
        browser = runtime.chromium.launch(**options)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1100})
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.locator("body").wait_for(timeout=15000)
            for action in params.get("actions", []):
                if action.get("op") not in ("click_variant", "select_variant", "wait_for"):
                    raise ValueError("Only variant selection and element waits are supported")
                locator = page.locator(action["selector"])
                if locator.count() != 1:
                    raise ValueError("Variant selector must identify exactly one element")
                label = " ".join([locator.inner_text(), locator.get_attribute("aria-label") or "", locator.get_attribute("name") or ""])
                if action["op"] != "wait_for" and (forbidden.search(label) or locator.get_attribute("type") == "submit"):
                    raise ValueError("Merchant inspection cannot activate cart, checkout or submission controls")
                expected = action.get("expected_text")
                if expected and expected.strip() != locator.inner_text().strip():
                    raise ValueError("Variant label changed; inspect the current page before selecting")
                if action["op"] == "click_variant":
                    locator.click(timeout=10000)
                elif action["op"] == "select_variant":
                    locator.select_option(value=action["value"], timeout=10000)
                else:
                    locator.wait_for(state=action.get("state", "visible"), timeout=10000)
                log.append({**action, "observed_label": label})
            extracts = {}
            for name, selector in params.get("extract", {}).items():
                locators = page.locator(selector)
                extracts[name] = [{"text": l.inner_text(timeout=5000), "value": l.get_attribute("value"),
                                   "aria_selected": l.get_attribute("aria-selected"), "disabled": l.get_attribute("disabled"),
                                   "class": l.get_attribute("class")} for l in locators.all()[:30]]
            main = page.locator(params.get("text_selector", "body")).first.inner_text(timeout=10000)
            challenge = bool(re.search(r"captcha|подтвердите.{0,30}(человек|робот)|доступ ограничен|проверка безопасности", main, re.I))
            screenshot = output_path(directory / f"browser-{key}.png")
            html_path = output_path(directory / f"browser-{key}.html")
            page.screenshot(path=str(screenshot), full_page=False)
            html_path.write_text(page.content(), encoding="utf-8")
            evidence = {"status": "access_challenge" if challenge else "observed", "url": url,
                        "final_url": page.url, "observed_at": now(), "network_gid": os.getgid(),
                        "actions": log, "extracts": extracts, "text": main[:params.get("text_limit", 16000)],
                        "screenshot": str(screenshot), "html_path": str(html_path), "headless": True,
                        "variant_verified": False, "requires_selected_variant_review": True}
            evidence["evidence_path"] = save(directory / f"browser-{key}.json", evidence)
            return evidence
        finally:
            browser.close()


def download(params):
    from PIL import Image
    directory = output_path(Path(params["directory"]) / "placeholder").parent
    results = []
    for asset in params["assets"]:
        try:
            url = http_url(asset["url"])
            source = http_url(asset["source_page"])
            blob, headers, final, status = read_url(url, 20 * 1024 * 1024)
            with Image.open(BytesIO(blob)) as image:
                image.verify()
                fmt = image.format.lower()
            digest = hashlib.sha256(blob).hexdigest()
            path = output_path(directory / f"image-{digest[:16]}.{fmt}")
            if not path.exists():
                path.write_bytes(blob)
            results.append({"item_id": asset["item_id"], "ok": True, "url": url,
                            "source_page": source, "final_url": final,
                            "path": str(path), "sha256": digest, "observed_at": now(),
                            "network_gid": os.getgid()})
        except (OSError, ValueError, KeyError) as e:
            results.append({"item_id": asset.get("item_id"), "ok": False, "error": str(e)})
    return {"assets": results, "manifest": save(directory / "image-manifest.json", results),
            "complete": all(a["ok"] for a in results)}
