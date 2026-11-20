"""Ozon-only state persistence; optional real headless test uses synthetic pages."""
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]/'scripts'))
from ozon_browser import save_state, storage_state, private_bytes, group_summary, process_groups, navigation_error
from ozon_network import public_ip
from ozon_session import read_private
from ozon_display import profile_path


def session():
    return {'domain_scope':'ozon.ru', 'browser_profile':'synthetic-profile', 'cookies':[
        {'host':'.ozon.ru','name':'test_session','value':'SYNTHETIC_ONLY','path':'/',
         'expiry':None,'isSecure':True,'isHttpOnly':True,'sameSite':'Strict'}]}


class BrowserTests(unittest.TestCase):
    def test_dns_requires_explicit_browser_evidence_and_messages_are_redacted(self):
        dns = navigation_error(RuntimeError('net::ERR_NAME_NOT_RESOLVED at https://ozon.ru/?token=SECRET'))
        self.assertEqual(dns['reason'], 'dns_error')
        self.assertEqual(dns['network_error_code'], 'ERR_NAME_NOT_RESOLVED')
        self.assertNotIn('SECRET', str(dns))
        timeout = navigation_error(RuntimeError('Timeout https://ozon.ru/?token=SECRET'), timed_out=True)
        self.assertEqual(timeout['reason'], 'timeout')
        self.assertIsNone(timeout['network_error_code'])
        self.assertNotIn('SECRET', str(timeout))
        reset = navigation_error(RuntimeError('net::ERR_CONNECTION_RESET'))
        self.assertEqual(reset['reason'], 'network_error')
        self.assertEqual(navigation_error(RuntimeError('Unknown'))['reason'], 'navigation_error')

    def test_profile_isolated_per_import_but_stable_across_cookie_rotation(self):
        with tempfile.TemporaryDirectory() as tmp, patch('ozon_display.private_root',return_value=Path(tmp)):
            source=Path(tmp)/'session.json'
            first={'exported_at':'first','browser_profile':str(Path(tmp)/'desktop')}
            profile=profile_path(source,first)
            self.assertNotEqual(profile,Path(first['browser_profile']))
            self.assertEqual(profile,profile_path(source,{**first,'updated_at':'later'}))
            self.assertNotEqual(profile,profile_path(source,{'exported_at':'second'}))
            self.assertEqual(profile.stat().st_mode & 0o777,0o700)

    def test_launcher_group_alone_cannot_verify_network_service(self):
        browser = {'role':'browser','pid':123,'gids':[1002]*4}
        network = {'role':'network.mojom.NetworkService','pid':124,'gids':[1002]*4}
        self.assertEqual(group_summary([browser],1002)['status'],'unverified')
        self.assertEqual(group_summary([browser,network],1002)['status'],'verified')
        wrong = {**network,'gids':[1002,1002,1002,1000]}
        self.assertEqual(group_summary([browser,wrong],1002)['status'],'mismatch')
        self.assertEqual(group_summary([browser,{**network,'gids':None}],1002)['status'],'unverified')

    def test_ip_diagnostic_never_accepts_error_pages_as_addresses(self):
        self.assertEqual(public_ip('{"ip":"203.0.113.5"}'),'203.0.113.5')
        for text in ['<title>Blocked</title>','{"ip":"error"}','{}','null']:
            self.assertIsNone(public_ip(text))

    def test_expiry_and_browser_cookie_attributes_survive_round_trip(self):
        document = session()
        document['cookies'].append({**document['cookies'][0], 'name':'expired','expiry':time.time()-60})
        state = storage_state(document)
        self.assertEqual(len(state['cookies']), 1)
        cookie = state['cookies'][0]
        self.assertEqual(cookie['sameSite'], 'Strict')
        self.assertEqual(cookie['expires'], -1)
        self.assertTrue(cookie['secure'] and cookie['httpOnly'])
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)/'session.json'
            save_state(target, document, state)
            self.assertEqual(storage_state(read_private(target)), state)
            self.assertEqual(read_private(target)['browser_profile'], 'synthetic-profile')
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_rejects_unrelated_input_and_filters_third_party_browser_state(self):
        document = session()
        document['cookies'][0]['host'] = '.aliexpress.ru'
        with self.assertRaises(ValueError): storage_state(document)
        document = session()
        document['browser_storage'] = [{'origin':'https://ozon.ru.evil.test','localStorage':[]}]
        with self.assertRaises(ValueError): storage_state(document)
        document = session()
        state = storage_state(document)
        state['cookies'].append({**state['cookies'][0], 'domain':'.example.test'})
        state['origins'] = [{'origin':'https://www.ozon.ru','localStorage':[{'name':'test','value':'local'}]},
                            {'origin':'https://example.test','localStorage':[{'name':'other','value':'IGNORE'}]}]
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)/'session.json'
            save_state(target, document, state)
            saved = read_private(target)
            self.assertEqual(len(saved['cookies']), 1)
            self.assertEqual(len(saved['browser_storage']), 1)
            self.assertNotIn('IGNORE', target.read_text())

    def test_capture_refuses_symlink_and_restricts_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)/'capture.html'
            target.write_text('old'); target.chmod(0o644)
            private_bytes(target, b'new')
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            link = Path(tmp)/'link'
            link.symlink_to(target)
            with self.assertRaises(OSError): private_bytes(link, b'bad')
            self.assertEqual(target.read_bytes(), b'new')

    @unittest.skipUnless(os.environ.get('OZON_BROWSER_SMOKE') == '1', 'Opt-in real browser; all pages synthetic')
    def test_headless_reopens_rotated_cookie_and_local_storage_without_live_requests(self):
        from playwright.sync_api import sync_playwright
        with tempfile.TemporaryDirectory() as tmp, sync_playwright() as driver:
            options = {'headless':True, 'chromium_sandbox':True, 'args':['--no-proxy-server']}
            executable = shutil.which('chromium') or shutil.which('chromium-browser')
            options.update({'executable_path':executable} if executable else {'channel':'chromium'})
            browser = driver.chromium.launch(**options)
            try:
                document = session()
                target = Path(tmp)/'session.json'
                first = browser.new_context(storage_state=storage_state(document))
                first.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html', body='<title>Fixture</title><body>Fixture</body>'))
                page = first.new_page()
                self.assertEqual(process_groups(browser)['status'],'verified')
                page.goto('https://www.ozon.ru/')
                page.evaluate("localStorage.setItem('synthetic','persisted')")
                first.add_cookies([{**storage_state(document)['cookies'][0], 'value':'ROTATED_SYNTHETIC'}])
                save_state(target, document, first.storage_state(indexed_db=True))
                first.close()
                second = browser.new_context(storage_state=storage_state(read_private(target)))
                second.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html', body='<title>Fixture</title><body>Fixture</body>'))
                page = second.new_page()
                page.goto('https://www.ozon.ru/')
                self.assertEqual(page.evaluate("localStorage.getItem('synthetic')"), 'persisted')
                self.assertEqual(second.cookies()[0]['value'], 'ROTATED_SYNTHETIC')
                second.close()
            finally:
                browser.close()


if __name__ == '__main__': unittest.main()
