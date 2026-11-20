#!/usr/bin/env python3
"""Read-only AliExpress HTTP probe with an explicitly authorized local Firefox export.

Only aliexpress.ru HTTPS destinations; cookie values and redirect queries never logged.
This does not import a session into CUA or claim JS rendering or authenticated cart access.
"""
import argparse
import datetime as dt
import http.cookiejar
import json
import os
from pathlib import Path
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser


def allowed(url):
    u = urllib.parse.urlsplit(url)
    return (u.scheme == "https" and not u.username and not u.password and u.port in (None, 443)
            and (u.hostname == "aliexpress.ru" or (u.hostname or "").endswith(".aliexpress.ru")))


class ScopedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self):
        self.hosts = []
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed(newurl):
            raise ValueError("Redirect outside authorized aliexpress.ru HTTPS scope")
        if len(self.hosts) >= 5:
            raise ValueError("Too many redirects")
        self.hosts.append(urllib.parse.urlsplit(newurl).hostname)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class PageSummary(HTMLParser):
    def __init__(self):
        super().__init__(); self.products = set(); self.title = []; self.in_title = False
    def handle_starttag(self, tag, attrs):
        if tag == "title": self.in_title = True
        if tag == "a":
            for key, value in attrs:
                if key == "href" and value:
                    u = urllib.parse.urlsplit(urllib.parse.urljoin("https://aliexpress.ru/", value))
                    if u.hostname == "aliexpress.ru" and re.fullmatch(r"/item/\d+\.html", u.path):
                        self.products.add("https://aliexpress.ru" + u.path)
    def handle_endtag(self, tag):
        if tag == "title": self.in_title = False
    def handle_data(self, data):
        if self.in_title: self.title.append(data)


def load_jar(path):
    path = Path(path)
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise ValueError("Session file must be private and not a symlink")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("domain_scope") != "aliexpress.ru" or data.get("origin_attributes") != "":
        raise ValueError("Only ordinary-context aliexpress.ru exports supported")
    policy = http.cookiejar.DefaultCookiePolicy(strict_ns_domain=http.cookiejar.DefaultCookiePolicy.DomainStrictNonDomain)
    jar = http.cookiejar.CookieJar(policy)
    for row in data["cookies"]:
        host = row["host"]; plain = host.lstrip(".")
        if plain != "aliexpress.ru" and not plain.endswith(".aliexpress.ru"):
            raise ValueError("Cookie outside authorized domain")
        if row.get("originAttributes", "") != "":
            raise ValueError("Container/partition cookie cannot be flattened into HTTP jar")
        for field in ("name", "value", "path", "host"):
            if any(c in str(row[field]) for c in ("\r", "\n", "\0")):
                raise ValueError("Invalid control characters in cookie")
        jar.set_cookie(http.cookiejar.Cookie(0, row["name"], row["value"], None, False,
            host, host.startswith("."), host.startswith("."), row["path"], True,
            bool(row["isSecure"]), int(row["expiry"]) if row.get("expiry") is not None else None,
            bool(row.get("isSession")) or row.get("expiry") is None, None, None,
            {"HTTPOnly": ""} if row["isHttpOnly"] else {}, False))
    return jar


def probe(session_path, query=None):
    url = "https://aliexpress.ru/"
    if query:
        url += "wholesale?" + urllib.parse.urlencode({"SearchText": query})
    redirect = ScopedRedirect()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(load_jar(session_path)), redirect,
        urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    request = urllib.request.Request(url, headers={"User-Agent": "AliExpress-BOM/1.0 (authorized local diagnostic)",
                                                 "Accept-Language": "ru-RU,ru;q=0.9", "Accept": "text/html"})
    try:
        response = opener.open(request, timeout=18)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        content = response.read(12 * 1024 * 1024 + 1)
        if len(content) > 12 * 1024 * 1024:
            raise ValueError("Response exceeds diagnostic size limit")
        final = urllib.parse.urlsplit(response.url)
        page = PageSummary(); page.feed(content.decode("utf-8", errors="replace"))
        return {"checked_at": dt.datetime.now(dt.timezone.utc).isoformat(), "http_status": response.status,
                "final_host": final.hostname, "final_path": final.path, "redirect_hosts": redirect.hosts,
                "response_bytes": len(content), "title": "".join(page.title)[:300],
                "product_link_count": len(page.products), "sample_product_urls": sorted(page.products)[:5],
                "cookie_values_logged": False, "login_verified": False, "javascript_rendering_verified": False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--session", required=True)
    p.add_argument("--query")
    p.add_argument("--report", required=True)
    a = p.parse_args()
    try:
        result = probe(a.session, a.query)
        Path(a.report).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
    except (ValueError, OSError, urllib.error.URLError) as error:
        # No exception strings: network libraries may include credential-bearing URLs.
        result = {"checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                  "status": "failed", "error_type": type(error).__name__,
                  "login_verified": False, "cookie_values_logged": False}
        Path(a.report).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        p.exit(2, f"Session probe failed ({type(error).__name__}); no credentials printed.\n")


if __name__ == "__main__": main()
