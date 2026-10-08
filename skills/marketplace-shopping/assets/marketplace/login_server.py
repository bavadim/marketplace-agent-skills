"""Loopback-only human handoff to owned headless tabs. No login form logging."""
import argparse
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
from pathlib import Path
import time
from urllib.parse import urlsplit

from accounts import descriptor
from accounts_worker import prepare_process
from browser_runtime import ManagedBrowser, SITES, allowed, read_private, save_private, status


def authorize(headers, token, base):
    if headers.get("Host") != urlsplit(base).netloc:
        return False
    if headers.get("Origin") not in (None, base):
        return False
    return hmac.compare_digest(headers.get("Authorization", "").encode(), ("Bearer " + token).encode())


class LoginApplication:
    def __init__(self, doc):
        self.doc = doc
        self.browsers = {}
        self.results = {p: status(p) for p in doc["providers"]}
        self.stopped = False

    def browser(self, provider):
        if provider not in self.doc["providers"]:
            raise ValueError("invalid_provider")
        if provider not in self.browsers:
            browser = ManagedBrowser(provider)
            browser.open()
            self.browsers[provider] = browser
            browser.context.on("page", lambda page: setattr(browser, "page", page))
            def route_navigation(route):
                if route.request.is_navigation_request() and route.request.frame == route.request.frame.page.main_frame and not allowed(provider, route.request.url):
                    route.abort()
                else:
                    route.fallback()
            browser.context.route("**/*", route_navigation)
            try:
                browser.page.goto(SITES[provider]["url"], wait_until="domcontentloaded", timeout=30000)
            except Exception as error:
                self.results[provider] = {"provider": provider, "status": "navigation_pending", "authenticated": None,
                                          "error_type": type(error).__name__}
        return self.browsers[provider]

    def view_status(self):
        return {"status": "ready", "handoff_id": self.doc["handoff_id"], "providers": self.doc["providers"],
                "sessions": list(self.results.values()), "expires_at": self.doc["expires_at"], "headless": True}

    def input(self, provider, event):
        page = self.browser(provider).page
        op = event.get("op")
        if op in ("move", "down", "up", "wheel"):
            x, y = event.get("x", 0), event.get("y", 0)
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (x, y)) or not (0 <= x <= 1440 and 0 <= y <= 1000):
                raise ValueError("invalid_coordinates")
            page.mouse.move(x, y)
            if op in ("down", "up"):
                getattr(page.mouse, op)(button="left")
            elif op == "wheel":
                delta = event.get("delta", 0)
                if not isinstance(delta, (int, float)) or isinstance(delta, bool) or not math.isfinite(delta):
                    raise ValueError("invalid_scroll")
                page.mouse.wheel(0, max(-3000, min(3000, delta)))
        elif op == "text":
            text = event.get("text")
            if not isinstance(text, str) or len(text) > 4096:
                raise ValueError("invalid_text")
            page.keyboard.insert_text(text)
        elif op in ("key_down", "key_up"):
            key = event.get("key")
            permitted = {"Enter", "Tab", "Backspace", "Delete", "Escape", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown",
                         "Home", "End", "PageUp", "PageDown", "Control", "Shift", "Alt", "Meta", "Space"}
            if not isinstance(key, str) or key not in permitted and not (len(key) == 1 and key.isascii()):
                raise ValueError("invalid_key")
            getattr(page.keyboard, "down" if op == "key_down" else "up")(key)
        else:
            raise ValueError("invalid_input_operation")
        return {"status": "ok"}

    def verify(self, provider):
        browser = self.browser(provider)
        self.results[provider] = browser.validate_login()
        return self.results[provider]

    def finish(self):
        for provider in self.doc["providers"]:
            self.verify(provider)
        complete = all(r.get("authenticated") is True for r in self.results.values())
        result = {"status": "ok" if complete else "partial", "complete": complete,
                  "sessions": list(self.results.values()), "headless": True}
        if complete:
            self.doc['complete'] = True
            self.stopped = True
        return result

    def close(self):
        for browser in self.browsers.values():
            try:
                browser.close()
            except Exception:
                pass


def handler(application, base):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code, body, content_type="application/json"):
            blob = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(blob)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' blob:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            try:
                self.wfile.write(blob)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            if self.headers.get("Host") != urlsplit(base).netloc:
                return self.send(403, {"status": "forbidden"})
            path = urlsplit(self.path).path
            if path in ("/", "/viewer.js", "/viewer.css"):
                name = "login.html" if path == "/" else path[1:]
                mime = "text/html; charset=utf-8" if path == "/" else "text/javascript" if path.endswith(".js") else "text/css"
                return self.send(200, Path(__file__).with_name("login_ui").joinpath(name).read_bytes(), mime)
            if not authorize(self.headers, application.doc["token"], base):
                return self.send(403, {"status": "forbidden"})
            try:
                if path == "/api/status":
                    return self.send(200, application.view_status())
                if path.startswith("/api/frame/"):
                    provider = path.rsplit("/", 1)[1]
                    page = application.browser(provider).page
                    return self.send(200, page.screenshot(type="jpeg", quality=75, timeout=5000), "image/jpeg")
                return self.send(404, {"status": "not_found"})
            except Exception as error:
                return self.send(503, {"status": getattr(error, "status", "browser_error"), "error_type": type(error).__name__})

        def do_POST(self):
            if not authorize(self.headers, application.doc["token"], base) or self.headers.get("Origin") != base:
                return self.send(403, {"status": "forbidden"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= 20000:
                    return self.send(413, {"status": "request_too_large"})
                params = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(params, dict):
                    raise ValueError("invalid_request")
                path = urlsplit(self.path).path
                if path == "/api/input":
                    result = application.input(params["provider"], params["event"])
                elif path == "/api/verify":
                    result = application.verify(params["provider"])
                elif path == "/api/finish":
                    result = application.finish()
                elif path == "/api/cancel":
                    application.stopped = True
                    result = {"status": "cancelled", "sessions": list(application.results.values())}
                else:
                    return self.send(404, {"status": "not_found"})
                return self.send(200, result)
            except Exception as error:
                return self.send(400, {"status": getattr(error, "status", "operation_error"), "error_type": type(error).__name__})
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--handoff-id", required=True)
    args = parser.parse_args()
    path = descriptor(args.handoff_id)
    doc = read_private(path)
    application = None
    try:
        prepare_process()
        application = LoginApplication(doc)
        with HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler) as server:
            server.timeout = 0.2
            base = "http://127.0.0.1:" + str(server.server_port)
            server.RequestHandlerClass = handler(application, base)
            doc.update(status="ready", base_url=base, url=base + "/#" + doc["token"])
            save_private(path, doc)
            while not application.stopped and time.time() < doc["deadline"]:
                server.handle_request()
                for browser in application.browsers.values():
                    if not browser.page.is_closed(): browser.page.wait_for_timeout(5)
            doc.update(status="closed" if application.stopped else "expired")
    except Exception as error:
        doc.update(status="login_server_error", reason=getattr(error, "status", None), error_type=type(error).__name__)
    finally:
        if application:
            application.close()
            doc['sessions'] = list(application.results.values())
        doc.pop("token", None)
        doc.pop("url", None)
        save_private(path, doc)


if __name__ == '__main__':
    from launcher import launch
    launch()
    main()
