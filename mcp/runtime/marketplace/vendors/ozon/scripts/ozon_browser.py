"""Owned headless Chromium with private, Ozon-scoped browser state.

No connection to the desktop browser, credential flags or open debugging port.
Both providers use the shared native headless runtime. Cookie import is optional.
"""
import contextlib
import datetime as dt
import os
from pathlib import Path
import shutil
import subprocess
import re
import time
from urllib.parse import urlsplit

from ozon import allowed, classify, safe_url
from ozon_session import atomic_write, in_scope, read_private, session_lock


def launch_options(executable=None, timeout_ms=30000):
    import sys
    runtime = Path(__file__).resolve().parents[3]
    if str(runtime) not in sys.path: sys.path.insert(0, str(runtime))
    from browser_runtime import launch_options as shared_options
    return shared_options(executable, timeout_ms)


def background_options(driver, executable=None, timeout_ms=30000, mode='native'):
    if mode != 'native': raise ValueError('unsupported_browser_mode')
    return launch_options(executable, timeout_ms)


def group_summary(processes, expected_gid):
    needed = {'browser','network.mojom.NetworkService'}
    rows = [row for row in processes if row['role'] in needed]
    status = 'unverified'
    if any(row.get('gids') and (row['gids'][1] != expected_gid or row['gids'][3] != expected_gid)
           for row in rows):
        status = 'mismatch'
    elif needed <= {row['role'] for row in rows} and all(row.get('gids') for row in rows):
        status = 'verified'
    return {'status':status, 'expected_gid':expected_gid,
            'gid_order':['real','effective','saved','filesystem'], 'processes':rows}


def process_groups(browser):
    """Check the owned browser and its network service, not just the launcher."""
    rows = []
    cdp = browser.new_browser_cdp_session()
    try:
        for item in cdp.send('SystemInfo.getProcessInfo')['processInfo']:
            if item['type'] not in ('browser','network.mojom.NetworkService'):
                continue
            row = {'role':item['type'],'pid':int(item['id']),'gids':None}
            try:
                fields = dict(line.split(':',1) for line in
                              Path('/proc',str(row['pid']),'status').read_text().splitlines() if ':' in line)
                gids = [int(value) for value in fields['Gid'].split()]
                if int(fields['Uid'].split()[0]) == os.getuid() and len(gids) == 4:
                    row['gids'] = gids
            except (OSError,KeyError,ValueError):
                pass
            rows.append(row)
    finally:
        cdp.detach()
    return group_summary(rows, os.getegid())


def scoped_origin(origin):
    try:
        parsed = urlsplit(origin)
        return (parsed.scheme == 'https' and in_scope(parsed.hostname)
                and parsed.port in (None, 443) and not parsed.username and not parsed.password)
    except (ValueError, TypeError):
        return False


def storage_state(document):
    """Translate the saved session without importing unrelated browser data."""
    if document.get('domain_scope') != 'ozon.ru':
        raise ValueError('session_scope_mismatch')
    cookies = []
    for row in document['cookies']:
        if not in_scope(row['host']):
            raise ValueError('cookie_outside_scope')
        expiry = row.get('expiry')
        if expiry and expiry <= time.time():
            continue
        cookie = dict(name=row['name'], value=row['value'], domain=row['host'],
                      path=row['path'], expires=expiry or -1,
                      httpOnly=bool(row.get('isHttpOnly')), secure=bool(row.get('isSecure')),
                      sameSite=row.get('sameSite', 'Lax'))
        cookies.append(cookie)
    origins = document.get('browser_storage', [])
    if any(not scoped_origin(item.get('origin')) for item in origins):
        raise ValueError('storage_outside_scope')
    return {'cookies': cookies, 'origins': origins}


def save_state(path, document, state):
    cookies = []
    for row in state['cookies']:
        if not in_scope(row['domain']):
            continue
        expiry = row.get('expires', -1)
        if expiry > 0 and expiry <= time.time():
            continue
        cookies.append(dict(host=row['domain'], name=row['name'], value=row['value'],
                            path=row['path'], expiry=expiry if expiry > 0 else None,
                            isSecure=row['secure'], isHttpOnly=row['httpOnly'],
                            sameSite=row['sameSite']))
    origins = [item for item in state.get('origins', []) if scoped_origin(item.get('origin'))]
    atomic_write(path, {**document, 'cookies': cookies, 'browser_storage': origins,
                       'updated_at': dt.datetime.now(dt.timezone.utc).isoformat()})


def private_bytes(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(content)


def navigation_error(error, timed_out=False):
    """Retain only Chromium's error code; raw driver messages may contain secrets."""
    match = re.search(r'\bnet::(ERR_[A-Z0-9_]+)\b', str(error))
    code = match.group(1) if match else None
    reason = 'navigation_error'
    if code in ('ERR_NAME_NOT_RESOLVED', 'ERR_NAME_RESOLUTION_FAILED') or (code and code.startswith('ERR_DNS_')):
        reason = 'dns_error'
    elif timed_out or code in ('ERR_TIMED_OUT', 'ERR_CONNECTION_TIMED_OUT'):
        reason = 'timeout'
    elif code:
        reason = 'network_error'
    return {'status': 'browser_navigation_error', 'reason': reason,
            'error_type': type(error).__name__, 'network_error_code': code}


def browse(url, session=None, capture=None, screenshot=None, executable=None, timeout_ms=30000,
           mode='native', query=None, operation=None):
    if not allowed(url):
        raise ValueError('Only HTTPS ozon.ru and www.ozon.ru are supported')
    if mode != 'native':
        return {'status': 'unsupported_browser_mode', 'headless': True, 'required_mode': 'native'}
    try:
        from patchright.sync_api import sync_playwright, Error, TimeoutError as BrowserTimeout
    except ImportError:
        return {'status': 'browser_dependency_missing', 'client': 'chromium-headless'}
    result = {'client': 'chromium-background', 'headless': mode == 'native',
              'display_mode': mode, 'driver':'patchright',
              'cookies_supplied': bool(session), 'authenticated': None if session else False,
              'cart_modified': False, 'url': safe_url(url)}
    with contextlib.ExitStack() as stack:
        document, state = None, None
        if session:
            document = read_private(session)
            state = storage_state(document)
        try:
            import sys
            runtime = Path(__file__).resolve().parents[3]
            if str(runtime) not in sys.path: sys.path.insert(0, str(runtime))
            from browser_runtime import ManagedBrowser
            with ManagedBrowser('ozon',session,use_saved_session=bool(session),executable=executable,timeout_ms=timeout_ms) as managed:
                context = managed.context
                browser = context.browser
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    result['process_group_check'] = process_groups(browser)
                    if result['process_group_check']['status'] == 'mismatch':
                        return {**result,'status':'browser_group_mismatch'}
                    outside_scope = []

                    def route_request(route):
                        request = route.request
                        if (request.is_navigation_request() and request.frame == page.main_frame
                                and not allowed(request.url)):
                            outside_scope.append(True)
                            route.abort()
                        else:
                            # Patchright has its own loader routes; preserve that handler chain.
                            route.fallback()

                    page.route('**/*', route_request)
                    stage = 'navigate'
                    try:
                        latest = {}
                        def document_response(response):
                            if response.request.is_navigation_request() and response.request.frame == page.main_frame:
                                latest['response'] = response
                        page.on('response',document_response)

                        def settled():
                            # A JS challenge may redirect after load; follow only this navigation.
                            deadline = time.monotonic()+20
                            while time.monotonic() < deadline:
                                title = page.title()
                                if not re.search(r'Antibot Challenge|Just a moment',title,re.I):
                                    break
                                page.wait_for_timeout(500)
                            try:
                                page.wait_for_load_state('load',timeout=5000)
                            except BrowserTimeout:
                                pass

                        page.goto(url, wait_until='domcontentloaded', timeout=timeout_ms)
                        if not allowed(page.url):
                            return {**result,'status':'outside_scope_redirect','url':safe_url(page.url)}
                        stage = 'settle_initial_page'
                        settled()
                        if not allowed(page.url):
                            return {**result,'status':'outside_scope_redirect','url':safe_url(page.url)}
                        if query:
                            if re.search(r'Challenge|Captcha|нет\s+соединения',page.title(),re.I):
                                result['query_not_submitted'] = True
                            else:
                                stage = 'search_input'
                                field = page.locator('input[name="text"][placeholder="Искать на Ozon"]')
                                field.fill(query)
                                stage = 'search_submit'
                                field.press('Enter')
                                stage = 'search_navigation'
                                page.wait_for_url(lambda value: urlsplit(value).path.rstrip('/') == '/search',timeout=timeout_ms)
                                page.wait_for_load_state('domcontentloaded',timeout=timeout_ms)
                                settled()
                                if not allowed(page.url):
                                    return {**result,'status':'outside_scope_redirect','url':safe_url(page.url)}
                            result['query'] = query
                        stage = 'inspect_page'
                        response = latest.get('response')
                        html = page.content()
                        code = response.status if response else 0
                        status, title = classify(code, html)
                        if status == 'http_challenge':
                            status = 'browser_challenge'
                        if status == 'ok' and operation:
                            stage = 'operation'
                            if getattr(operation,'mutates_cart',False):
                                result.update(cart_modified=None,requires_cart_read=True)
                            result.update(operation(page))
                            if result.get('operation_status')=='ok':result.pop('requires_cart_read',None)
                            if not allowed(page.url):
                                return {**result,'status':'outside_scope_redirect','url':safe_url(page.url)}
                            html = page.content()
                            response = latest.get('response')
                            code = response.status if response else 0
                            status, title = classify(code, html)
                            if result.get('operation_status') not in (None,'ok'):
                                status = result['operation_status']
                        text = page.locator('body').inner_text(timeout=5000)
                        if status == 'ok' and not text.strip():
                            status = 'unsupported_page'
                        result.update(status=status, http_status=code, title=title[:200],
                                      url=safe_url(page.url), browser_status=status,
                                      product_link_count=page.locator('a[href*="/product/"]').count(),
                                      text_chars=len(text), browser_version=browser.version)
                        account = page.locator('[data-widget="profileLogo"] a[href="/my/main"]')
                        if account.count() and account.first.inner_text().strip() not in ('','Войти','Личный кабинет'):
                            result['authenticated'] = True
                        if query and status == 'ok' and 'products' not in result:
                            links = page.locator('a[href*="/product/"]').evaluate_all(
                                '(nodes) => nodes.map(a => ({url:a.href,text:a.innerText.trim()}))')
                            products = {}
                            for link in links:
                                canonical = safe_url(link['url'])
                                if not allowed(canonical): continue
                                row = products.setdefault(canonical,{'url':canonical,'labels':[]})
                                if link['text'] and link['text'] not in row['labels']:
                                    row['labels'].append(link['text'])
                            result['products'] = list(products.values())
                        try:
                            result['server_address'] = response.server_addr() if response else None
                        except Error:
                            result['server_address'] = None
                        if capture:
                            private_bytes(capture, html.encode('utf-8'))
                        if screenshot:
                            private_bytes(screenshot, page.screenshot(full_page=False))
                    except (Error, BrowserTimeout) as error:
                        # Raw Playwright messages may include URLs, cookies or page content.
                        result.update(navigation_error(error, isinstance(error, BrowserTimeout)), stage=stage)
                        if outside_scope:
                            result['status'] = 'outside_scope_redirect'
                        try:
                            result.update(title=page.title()[:200],url=safe_url(page.url))
                            if capture: private_bytes(capture,page.content().encode('utf-8'))
                            if screenshot: private_bytes(screenshot,page.screenshot(timeout=5000))
                        except Error:
                            result['error_capture_failed'] = True
                    finally:
                        if session:
                            managed.observe_auth(result.get('authenticated'))
                            managed.save()
                            result['session_saved'] = True
                finally:
                    pass
        except (Error, BrowserTimeout) as error:
            result.update(status='browser_launch_or_state_error', error_type=type(error).__name__)
    return result
