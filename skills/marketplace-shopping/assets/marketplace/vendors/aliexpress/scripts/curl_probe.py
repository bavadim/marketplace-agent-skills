#!/usr/bin/env python3
"""Bounded curl GET of AliExpress search; client proxies disabled by default.

Uses existing authorized Firefox export if supplied. Does not alter Firefox/router.
Only aliexpress.ru HTTPS redirects, no credential output, no cart mutation.
"""
import argparse
import datetime as dt
import http.cookiejar
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.parse
from session_probe import allowed, load_jar, PageSummary


def command(url, jar, headers, body, mode="direct", http="auto", ipv="auto", timeout=18):
    if not allowed(url):
        raise ValueError("Destination outside AliExpress HTTPS scope")
    args = ["curl", "-q", "--proto", "=https", "--globoff", "--silent", "--show-error",
            "--connect-timeout", "8", "--max-time", str(timeout), "--max-filesize", "12582912",
            "--cookie", str(jar), "--cookie-jar", str(jar), "--dump-header", str(headers),
            "--output", str(body), "--write-out", "%{http_code}\t%{time_total}\t%{http_version}\t%{remote_ip}\t%{proxy_used}\t%{ssl_verify_result}"]
    if mode == "direct": args += ["--noproxy", "*", "--proxy", ""]
    elif mode != "environment": raise ValueError("Unknown network mode")
    if http == "1.1": args += ["--http1.1"]
    elif http != "auto": raise ValueError("Unknown HTTP mode")
    if ipv == "4": args += ["--ipv4"]
    elif ipv == "6": args += ["--ipv6"]
    elif ipv != "auto": raise ValueError("Unknown IP mode")
    return args + [url]


def environment(mode):
    env = dict(os.environ)
    if mode == "direct":
        for key in list(env):
            if key.lower() in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}:
                del env[key]
    return env


def header_fields(text):
    fields = {}
    for line in text.splitlines():
        if line.startswith("HTTP/"): fields = {}
        elif ":" in line:
            key, value = line.split(":", 1); fields[key.strip().lower()] = value.strip()
    return fields


def probe(query, session=None, mode="direct", http="auto", ipv="auto", follow=False):
    result = {"checked_at": dt.datetime.now(dt.timezone.utc).isoformat(), "transport": "curl",
              "network_mode": mode, "requested_http": http, "requested_ip": ipv,
              "cookies_supplied": session is not None, "login_verified": False, "hops": []}
    url = "https://aliexpress.ru/wholesale?" + urllib.parse.urlencode({"SearchText": query})
    deadline = time.monotonic() + 35
    with tempfile.TemporaryDirectory(prefix="ali-curl-") as directory:
        root = Path(directory); jar = root / "cookies.txt"; headers = root / "headers"; body = root / "body"
        for path in (jar, headers, body):
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600); os.close(descriptor)
        cookies = http_cookie_jar(session)
        cookies.save(str(jar), ignore_discard=True, ignore_expires=False)
        for _ in range(6):
            remaining = min(18, max(1, int(deadline-time.monotonic())))
            headers.write_bytes(b""); body.write_bytes(b"")
            run = subprocess.run(command(url, jar, headers, body, mode, http, ipv, remaining),
                                 capture_output=True, text=True, env=environment(mode), timeout=remaining+4)
            parts = run.stdout.strip().split("\t")
            if len(parts) != 6:
                result.update(status="transport_error", curl_exit=run.returncode)
                break
            fields = header_fields(headers.read_text(errors="replace"))
            html = body.read_text(errors="replace")
            page = PageSummary(); page.feed(html)
            hop = {"host": urllib.parse.urlsplit(url).hostname, "http_status": int(parts[0]),
                   "seconds": float(parts[1]), "http_version": parts[2], "remote_ip": parts[3],
                   "proxy_used": parts[4] == "1", "tls_verify_result": int(parts[5]),
                   "curl_exit": run.returncode, "response_bytes": body.stat().st_size,
                   "complete_transfer": run.returncode == 0, "html_closing_tag": "</html>" in html.lower(),
                   "title": "".join(page.title)[:300], "product_link_count": len(page.products),
                   "sample_product_urls": sorted(page.products)[:5],
                   "content_type": fields.get("content-type"), "transfer_encoding": fields.get("transfer-encoding"),
                   "content_length": fields.get("content-length"), "content_encoding": fields.get("content-encoding")}
            result["hops"].append(hop)
            if run.returncode:
                result["status"] = "timeout" if run.returncode == 28 else "transport_error"; break
            if hop["http_status"] in (301, 302, 303, 307, 308):
                if not follow:
                    result["status"] = "redirect"; break
                next_url = urllib.parse.urljoin(url, fields.get("location", ""))
                if not allowed(next_url) or not fields.get("location"):
                    result["status"] = "redirect_outside_scope"; break
                if time.monotonic() >= deadline:
                    result["status"] = "deadline"; break
                url = next_url
            else:
                result["status"] = "http_ok" if 200 <= hop["http_status"] < 300 else "http_error"; break
        else:
            result["status"] = "too_many_redirects"
    return result


class CurlCookieJar(http.cookiejar.MozillaCookieJar):
    def save(self, filename=None, ignore_discard=False, ignore_expires=False):
        super().save(filename, ignore_discard, ignore_expires)
        path = Path(filename or self.filename)
        lines = []
        for line in path.read_text().splitlines():
            fields = line.split("\t")
            if len(fields) == 7 and fields[4] == "":
                fields[4] = "0"  # curl requires numeric expiry for session cookies.
                line = "\t".join(fields)
            lines.append(line)
        path.write_text("\n".join(lines) + "\n")


def http_cookie_jar(session):
    jar = CurlCookieJar()
    if session:
        for cookie in load_jar(session):
            if any(ch in item for item in (cookie.name, cookie.value, cookie.path, cookie.domain) for ch in ("\t", "\r", "\n", "\0")):
                raise ValueError("Cookie contains invalid Netscape-file characters")
            jar.set_cookie(cookie)
    return jar


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--query", required=True); p.add_argument("--session")
    p.add_argument("--network-mode", choices=("direct", "environment"), default="direct")
    p.add_argument("--http", choices=("auto", "1.1"), default="auto")
    p.add_argument("--ip", choices=("auto", "4", "6"), default="auto")
    p.add_argument("--follow", action="store_true"); p.add_argument("--report", required=True)
    a = p.parse_args()
    try:
        result = probe(a.query, a.session, a.network_mode, a.http, a.ip, a.follow)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        result = {"status": "failed", "error_type": type(error).__name__, "login_verified": False}
    Path(a.report).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__": main()
