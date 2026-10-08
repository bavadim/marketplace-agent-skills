"""Opt-in real headless Chromium tests. Every marketplace response is synthetic."""
import base64
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

RUNTIME = Path(__file__).resolve().parents[2] / 'marketplace-shopping/assets/marketplace'
sys.path.insert(0, str(RUNTIME))
from browser_runtime import ManagedBrowser, SITES, legacy_session, read_private
from login_server import LoginApplication, handler


def page_fixture(provider, signed=False):
    marker = '<script id="__AER_DATA__">{"loginStatusV2":"' + ('LOGGED_IN' if signed else 'ANONYMOUS') + '"}</script>' if provider == 'aliexpress' else '<div data-widget="profileLogo"><a href="/my/main">Войти</a></div>'
    return '''<html><head><title>Synthetic marketplace login</title></head><body>''' + marker + '''
      <input id="phone" style="position:absolute;left:50px;top:120px;width:240px;height:40px" aria-label="Synthetic input">
      <button id="signin" style="position:absolute;left:50px;top:200px;width:240px;height:40px">Synthetic sign in</button>
      <script>
      function show(){if(document.cookie.includes('synthetic=connected')){
        const aer=document.querySelector('#__AER_DATA__');if(aer)aer.textContent=JSON.stringify({loginStatusV2:'LOGGED_IN'});
        const account=document.querySelector('[data-widget="profileLogo"] a');if(account)account.textContent='Synthetic account';
      }}
      document.querySelector('#signin').onclick=()=>{document.cookie='synthetic=connected;path=/;secure;samesite=Lax';localStorage.setItem('synthetic','persisted');show()};show();
      </script></body></html>'''


@unittest.skipUnless(os.environ.get('MARKETPLACE_BROWSER_SMOKE') == '1', 'Opt-in headless Chromium; synthetic responses only')
class NativeTests(unittest.TestCase):
    def setUp(self):
        from browser_runtime import vendor_imports
        vendor_imports()
        self.temp = tempfile.TemporaryDirectory(prefix='accounts-native-')
        self.addCleanup(self.temp.cleanup)
        self.environment = patch.dict(os.environ, {'XDG_STATE_HOME': self.temp.name, 'MARKETPLACE_BROWSER_ROOT': self.temp.name + '/profiles'})
        self.environment.start(); self.addCleanup(self.environment.stop)
        os.environ.pop('ISPROXYGO', None)

    def test_profile_preserves_cookies_local_storage_and_indexeddb(self):
        def fulfill(route): route.fulfill(status=200, content_type='text/html', body=page_fixture('aliexpress'))
        with ManagedBrowser('aliexpress') as first:
            first.context.route('**/*', fulfill)
            first.page.goto(SITES['aliexpress']['url'])
            first.page.locator('#signin').click()
            first.page.evaluate('''async () => {await new Promise((resolve,reject)=>{const r=indexedDB.open('synthetic',1);
              r.onupgradeneeded=()=>r.result.createObjectStore('data');r.onerror=reject;r.onsuccess=()=>{const db=r.result;
              const t=db.transaction('data','readwrite');t.objectStore('data').put('SYNTHETIC_ONLY','key');t.oncomplete=()=>{db.close();resolve()}}})}''')
            original_profile = first.info['profile_id']
            self.assertTrue(first.validate_login()['authenticated'])
        with ManagedBrowser('aliexpress') as second:
            second.context.route('**/*', fulfill)
            second.page.goto(SITES['aliexpress']['url'])
            self.assertEqual(second.info['profile_id'], original_profile)
            self.assertEqual(second.page.evaluate("localStorage.getItem('synthetic')"), 'persisted')
            self.assertIn('synthetic=connected', second.page.evaluate('document.cookie'))
            value = second.page.evaluate('''async () => await new Promise((resolve,reject)=>{const r=indexedDB.open('synthetic',1);r.onerror=reject;
              r.onsuccess=()=>{const db=r.result;const q=db.transaction('data').objectStore('data').get('key');q.onsuccess=()=>{db.close();resolve(q.result)}}})''')
            self.assertEqual(value, 'SYNTHETIC_ONLY')

    def test_aliexpress_cart_read_uses_chromium_api_requests(self):
        from ali_http import Client
        from ali_cart import CartAPI
        calls = []
        with Client() as client:
            def fulfill(route):
                request = route.request
                calls.append((request.method, request.url))
                if '/aer-jsonapi/' in request.url:
                    data = {'data': {'items': [], 'summary': {'totalItemsCount': 0}, 'itemsSummary': {
                        'page': {'currentPage': 1, 'totalPages': 1}, 'selectedItemsIds': [], 'selectAll': False, 'sortType': 2}}}
                    route.fulfill(status=200, content_type='application/json', body=json.dumps(data))
                else:
                    route.fulfill(status=200, content_type='text/html', body=page_fixture('aliexpress', True))
            client.browser.context.route('**/*', fulfill)
            result = CartAPI(client).read()
        self.assertTrue(result['authenticated'])
        self.assertEqual(result['count'], 0)
        self.assertTrue(any(method == 'POST' and '/cart/hot/get' in url for method, url in calls))
        self.assertTrue(read_private(legacy_session('aliexpress'))['login_verified'])

    def test_local_login_page_controls_native_tabs_and_verifies_both_sites(self):
        addresses, stop = queue.Queue(), threading.Event()
        failures = []
        token = 'SYNTHETIC_LOCAL_HANDOFF'
        doc = {'providers': ['ozon', 'aliexpress'], 'handoff_id': 'synthetic', 'token': token,
               'expires_at': '2099-01-01T00:00:00+00:00'}
        class SyntheticApplication(LoginApplication):
            events = []
            def browser(self, provider):
                if provider not in self.doc['providers']: raise ValueError('invalid_provider')
                if provider not in self.browsers:
                    browser = ManagedBrowser(provider)
                    browser.context.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html', body=page_fixture(provider)))
                    browser.page.goto(SITES[provider]['url'])
                    self.browsers[provider] = browser
                return self.browsers[provider]
            def input(self, provider, event):
                result = super().input(provider, event)
                page = self.browsers[provider].page
                self.events.append({'op': event['op'], 'x': event.get('x'), 'y': event.get('y'),
                                    'focus': page.evaluate('() => document.activeElement.tagName'),
                                    'cookie_present': page.evaluate("() => document.cookie.includes('synthetic=connected')")})
                return result
        def serve():
            app = SyntheticApplication(doc)
            try:
                with HTTPServer(('127.0.0.1', 0), BaseHTTPRequestHandler) as server:
                    base = 'http://127.0.0.1:' + str(server.server_port)
                    server.timeout = 0.1; server.RequestHandlerClass = handler(app, base); addresses.put(base)
                    while not app.stopped and not stop.is_set():
                        server.handle_request()
                        for browser in app.browsers.values(): browser.page.wait_for_timeout(5)
            except Exception as error:
                failures.append(type(error).__name__ + ': ' + str(error))
            finally: app.close()
        thread = threading.Thread(target=serve, daemon=True); thread.start()
        try:
            base = addresses.get(timeout=10)
            with ManagedBrowser('aliexpress', use_saved_session=False) as viewer:
                page = viewer.context.new_page()
                page.goto(base + '/#' + token)
                for provider in doc['providers']:
                    page.get_by_role('button', name=SITES[provider]['label'], exact=True).click()
                    try:
                        page.wait_for_function("provider => document.querySelector('#frame').naturalWidth > 0 && document.querySelector('#frame').dataset.provider === provider", arg=provider, timeout=15000)
                    except Exception:
                        page.screenshot(path='/tmp/synthetic-login-viewer-debug.png', full_page=True)
                        self.fail(json.dumps({'provider': provider, 'status': page.locator('#status').inner_text(),
                                              'failures': failures, 'events': SyntheticApplication.events,
                                              'src': page.locator('#frame').get_attribute('src')}, ensure_ascii=False))
                    def remote_click(x, y):
                        box = page.locator('#frame').bounding_box()
                        page.mouse.click(box['x'] + box['width']*x/1440, box['y'] + box['height']*y/1000)
                        page.evaluate('() => queue', isolated_context=False)
                    remote_click(130, 145)
                    page.keyboard.type('SYNTHETIC_ONLY')
                    page.evaluate('() => queue', isolated_context=False)
                    remote_click(130, 225)
                    page.get_by_role('button', name='Проверить вход', exact=True).click()
                    try:
                        page.wait_for_function("() => document.querySelector('#status').textContent.includes('вход подтверждён') && !document.querySelector('#verify').disabled", timeout=10000)
                    except Exception:
                        page.screenshot(path='/tmp/synthetic-login-viewer-debug.png', full_page=True)
                        self.fail(json.dumps({'provider': provider, 'status': page.locator('#status').inner_text(),
                                              'events': SyntheticApplication.events}, ensure_ascii=False))
                page.get_by_role('button', name='Завершить подключение', exact=True).click()
                page.wait_for_function("() => document.querySelector('#status').textContent.includes('Аккаунты подключены')")
                output = os.environ.get('MARKETPLACE_VIEWER_SCREENSHOT')
                if output: page.screenshot(path=output, full_page=True)
            for provider in doc['providers']:
                self.assertTrue(read_private(legacy_session(provider))['login_verified'])
        finally:
            stop.set(); thread.join(timeout=15)
        self.assertFalse(thread.is_alive())


if __name__ == '__main__': unittest.main()
