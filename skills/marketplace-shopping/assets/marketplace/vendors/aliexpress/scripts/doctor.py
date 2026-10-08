#!/usr/bin/env python3
"""Bounded, unauthenticated connectivity probes. Never prints redirect tokens/cookies."""
import argparse
import concurrent.futures
import datetime as dt
import json
import subprocess
from urllib.parse import urlsplit

DEFAULT_URLS = [
    "https://aliexpress.ru/",
    "https://login.aliexpress.ru/",
    "https://st.aestatic.net/mixer/ssr/1/aer-assets/login-api-interceptor.0.1.6.js?cache=0",
    "https://g.alicdn.com/secdev/sufei_data/3.9.14/index.js",
    "https://assets.aliexpress-media.com/g/AWSC/et/1.83.41/et_f.js",
]


def check(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Expected an HTTPS URL without credentials")
    # Intentionally no -L: login redirects contain ephemeral session data.
    result = subprocess.run(
        ["curl", "--proto", "=https", "--connect-timeout", "8", "--max-time", "18",
         "--silent", "--show-error", "--output", "/dev/null", "--write-out",
         "%{http_code}\t%{time_total}\t%{ssl_verify_result}", url],
        capture_output=True, text=True, timeout=22,
    )
    fields = result.stdout.strip().split("\t")
    code = int(fields[0]) if fields and fields[0].isdigit() else 0
    failure = {5: "proxy_dns_failed", 6: "dns_failed", 7: "connect_failed",
               28: "timeout", 35: "tls_failed", 60: "certificate_failed"}
    status = failure.get(result.returncode, "transport_failed") if result.returncode else (
        "http_ok" if 200 <= code < 300 else "redirect_reachable" if 300 <= code < 400
        else "http_blocked" if code in (403, 429) else "http_error")
    return {"host": parsed.hostname, "http_status": code, "status": status,
            "seconds": float(fields[1]) if len(fields) > 1 else None,
            "tls_verified": result.returncode == 0 and len(fields) > 2 and fields[2] == "0",
            "curl_exit": result.returncode}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--urls", type=argparse.FileType("r"), help="JSON array of observed public asset URLs")
    parser.add_argument("--output", help="Write report to JSON instead of stdout")
    args = parser.parse_args()
    urls = json.load(args.urls) if args.urls else DEFAULT_URLS
    if not isinstance(urls, list) or not 1 <= len(urls) <= 30 or not all(isinstance(u, str) for u in urls):
        parser.error("Expected 1..30 URLs")
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        probes = list(pool.map(check, urls))
    report = {"checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
              "authenticated": False, "browser_functionality_verified": False,
              "probes": probes,
              "note": "HTTP reachability does not verify search, login, images or cart. Sandbox DNS may differ from host/browser."}
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        from pathlib import Path
        Path(args.output).write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")


if __name__ == "__main__":
    main()
