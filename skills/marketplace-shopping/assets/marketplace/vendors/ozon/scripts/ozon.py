#!/usr/bin/env python3
"""Ozon search, product extraction and cart CRUD in owned headless Chromium.

Reports contain no cookies, challenge tokens or raw headers. Cart commands stop before checkout and payment.
"""
import argparse
import contextlib
import grp
import hashlib
import importlib.util
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import urljoin, urlsplit, urlunsplit


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.title = ''
        self.in_title = False
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == 'title': self.in_title = True

    def handle_endtag(self, tag):
        if tag == 'title': self.in_title = False

    def handle_data(self, text):
        if self.in_title: self.title += text


def allowed(url):
    try:
        u = urlsplit(url)
        return (u.scheme == 'https' and u.hostname in ('ozon.ru', 'www.ozon.ru')
                and u.port in (None, 443) and not u.username and not u.password)
    except ValueError:
        return False


def safe_url(url):
    u = urlsplit(url)
    if u.scheme not in ('https','http'):
        return 'about:blank' if url == 'about:blank' else u.scheme+':'
    return urlunsplit((u.scheme, u.netloc, u.path, '', ''))


def classify(status, html):
    title = Page(html).title.strip()
    if status == 429: return 'rate_limited', title
    if re.search(r'нет\s+соединения|доступ\s+ограничен|access denied', title, re.I):
        return 'access_blocked', title
    if re.search(r'captcha|Antibot Challenge|капч|подтвердите.{0,30}не робот', title, re.I):
        return 'http_challenge', title
    if 200 <= status < 300 and title: return 'ok', title
    return ('http_error' if not 200 <= status < 300 else 'unsupported_page'), title



def probe(url, capture=None, session=None):
    if not allowed(url): raise ValueError('Only HTTPS ozon.ru and www.ozon.ru are supported')
    env = {k:v for k,v in os.environ.items() if k.lower() not in
           ('http_proxy', 'https_proxy', 'all_proxy', 'no_proxy')}
    with contextlib.ExitStack() as locks, tempfile.TemporaryDirectory(prefix='ozon-probe-') as tmp:
        root = Path(tmp)
        headers, body, jar = (root / name for name in ('headers', 'body', 'cookies'))
        jar.touch(mode=0o600)
        if session:
            from ozon_session import write_jar, persist_jar, session_lock
            locks.enter_context(session_lock(session))
            write_jar(session, jar)
        for redirects in range(4):
            args = ['curl', '-q', '--proxy', '', '--noproxy', '*', '--globoff',
                    '--proto', '=https', '--silent', '--show-error', '--compressed',
                    '--connect-timeout', '8', '--max-time', '20', '--max-filesize', '16777216',
                    '--cookie', str(jar), '--cookie-jar', str(jar),
                    '--dump-header', str(headers), '--output', str(body),
                    '--write-out', '%{http_code}', url]
            result = subprocess.run(args, capture_output=True, text=True, env=env, timeout=25)
            if result.returncode:
                reason = {5:'dns_error',6:'dns_error',28:'timeout'}.get(result.returncode,'network_error')
                return {'status':'transport_error', 'reason':reason,
                        'curl_exit':result.returncode, 'url':safe_url(url)}
            if session: persist_jar(session, jar)
            status = int(result.stdout)
            if status in (301, 302, 303, 307, 308):
                locations = re.findall(r'^location:\s*(.+)$', headers.read_text(errors='replace'), re.I|re.M)
                target = urljoin(url, locations[-1].strip()) if locations else ''
                if not allowed(target): return {'status':'outside_scope_redirect', 'http_status':status}
                url = target
                continue
            raw = body.read_bytes()
            state, title = classify(status, raw.decode('utf-8', 'replace'))
            if capture:
                capture.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(capture, os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, 'wb') as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(raw)
            return {'status':state, 'http_status':status, 'title':title[:200], 'url':safe_url(url),
                    'client':'curl', 'browser_status':'not_observed',
                    'bytes':len(raw), 'sha256':hashlib.sha256(raw).hexdigest(),
                    'authenticated':None if session else False, 'cookies_supplied':bool(session), 'cart_modified':False}
        return {'status':'redirect_limit'}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command',required=True)
    read = sub.add_parser('probe', help='Bounded HTTP access check')
    read.add_argument('--url', default='https://www.ozon.ru/')
    read.add_argument('--capture', type=Path, help='Private local HTML for parser development; do not publish')
    read.add_argument('--anonymous',action='store_true',help='Explicitly omit the saved session')
    headless = sub.add_parser('browse', help='Launch owned headless Chromium with the saved Ozon session')
    headless.add_argument('--url', default='https://www.ozon.ru/')
    headless.add_argument('--capture', type=Path, help='Private rendered HTML')
    headless.add_argument('--screenshot', type=Path, help='Private screenshot of the visible page')
    headless.add_argument('--executable', type=Path, help='Chromium binary; default installed Chromium, then Playwright')
    headless.add_argument('--anonymous', action='store_true')
    headless.add_argument('--mode',choices=['native'],default='native')
    search = sub.add_parser('search',help='Search using the observed Ozon search form')
    search.add_argument('query')
    search.add_argument('--url',default='https://www.ozon.ru/')
    search.add_argument('--capture',type=Path)
    search.add_argument('--screenshot',type=Path)
    search.add_argument('--executable',type=Path)
    search.add_argument('--anonymous',action='store_true')
    search.add_argument('--mode',choices=['native'],default='native')
    search.add_argument('--limit',type=int,default=24)
    product = sub.add_parser('product',help='Read an exact product, its variants, details and image evidence')
    product.add_argument('url')
    product.add_argument('--variant-sku')
    product.add_argument('--variants-limit',type=int,default=0,help='Inspect up to N linked variant pages')
    product.add_argument('--images-dir',type=Path)
    product.add_argument('--image-limit',type=int,default=12)
    cart = sub.add_parser('cart',help='Read cart or set an exact SKU quantity; no checkout')
    cart.add_argument('action',choices=['list','add','set','remove','apply'])
    cart.add_argument('--plan',type=Path,help='Reviewed BOM selections: items with sku/url/quantity/max_price')
    cart.add_argument('--url',help='Reviewed product variant URL, required for add')
    cart.add_argument('--sku')
    cart.add_argument('--quantity',type=int,default=1,help='Absolute desired quantity, not increment')
    cart.add_argument('--expect-quantity',type=int,help='Stop if the current quantity differs')
    cart.add_argument('--max-price',type=float,help='Maximum regular unit price in rubles for add')
    for command in (product,cart):
        command.add_argument('--capture',type=Path)
        command.add_argument('--screenshot',type=Path)
        command.add_argument('--executable',type=Path)
        command.add_argument('--mode',choices=['native'],default='native')
        command.set_defaults(anonymous=False)
    connect = sub.add_parser('session',help='Reuse, refresh or inspect the authorized Chromium session')
    connect.add_argument('action', choices=['ensure','refresh','status'])
    connect.add_argument('--profile',type=Path,help='Authorized Chromium profile; refresh reuses the saved path')
    connect.add_argument('--client', choices=['headless','http','none'], default='headless',
                         help='Access check after session preparation; default headless')
    network = sub.add_parser('network',help='Compare curl and owned headless routes, without user cookies')
    network.add_argument('--executable',type=Path)
    for command in (read,headless,search,product,cart,connect,network):
        command.add_argument('--output',required=True,type=Path)
        command.add_argument('--session',type=Path,help='Private Ozon session; default ~/.local/state/ozon-bom/session.json')
    args = parser.parse_args()
    if args.command=='cart':
        if args.action=='apply' and not args.plan:parser.error('cart apply requires --plan')
        if args.action=='add' and not args.url:parser.error('cart add requires --url for the reviewed variant')
        if args.action in ('set','remove') and (not args.sku or not args.sku.isdigit()):parser.error('cart set/remove requires numeric --sku')
        if args.quantity<1:parser.error('--quantity must be positive; use remove to delete')
        if args.max_price is not None and (not math.isfinite(args.max_price) or args.max_price<=0):parser.error('--max-price must be positive and finite')
        if args.action!='add':args.url='https://www.ozon.ru/cart'
    if args.command=='product' and (not 0<=args.variants_limit<=24 or not 1<=args.image_limit<=32):
        parser.error('variants-limit must be 0..24; image-limit must be 1..32')
    if args.command=='search' and not 1<=args.limit<=100:parser.error('limit must be 1..100')
    browser_commands=('browse','search','product','cart')
    wants_browser = args.command in (*browser_commands,'network') or (args.command == 'session' and
                     args.action != 'status' and args.client == 'headless')
    driver_module = 'playwright' if args.command == 'network' else 'patchright'
    if wants_browser and importlib.util.find_spec(driver_module) is None:
        from setup_browser import runtime_python
        runtime = runtime_python()
        if runtime.is_file() and str(runtime) != sys.executable:
            os.execv(str(runtime), [str(runtime), str(Path(__file__).resolve()), *sys.argv[1:]])
    try:
        from ozon_session import default_session, prepare
        path = args.session or default_session()
        if args.command == 'network':
            from ozon_network import check
            result = check(args.executable)
        elif args.command == 'session':
            result = prepare(path,args.action,args.profile)
            if args.action != 'status' and args.client != 'none' and result['status'] in ('saved','imported'):
                if args.client == 'headless':
                    from ozon_browser import browse
                    access = browse('https://www.ozon.ru/',session=path)
                else:
                    access = probe('https://www.ozon.ru/',session=path)
                result = {**result,**access,'session_saved':True}
        else:
            session = None if args.anonymous else path if path.exists() else None
            if args.session and not args.session.exists() and not args.anonymous:
                raise ValueError('Explicit session file is missing')
            if args.command in browser_commands:
                from ozon_browser import browse
                callback=None
                if args.command in ('search','product','cart'):
                    from ozon_ops import operation
                    callback=operation(args)
                if args.command=='cart' and not session:raise ValueError('cart_session_required')
                result = browse(args.url,session,args.capture,args.screenshot,args.executable,
                                mode=args.mode,query=args.query if args.command == 'search' else None,
                                operation=callback)
            else:
                result = probe(args.url,args.capture,session)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        result = {'status':getattr(error,'status','failed'), 'error_type':type(error).__name__}
    result['effective_group'] = grp.getgrgid(os.getegid()).gr_name
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    os.chmod(args.output, 0o600)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] in ('ok','saved','imported') else 2


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from launcher import launch
    launch()
    sys.exit(main())
