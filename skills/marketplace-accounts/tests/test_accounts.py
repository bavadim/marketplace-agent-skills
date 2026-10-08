"""Account lifecycle/privacy tests; synthetic state only, no real site requests."""
import contextlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

RUNTIME = Path(__file__).resolve().parents[2] / 'marketplace-shopping/assets/marketplace'
sys.path.insert(0, str(RUNTIME))
import browser_runtime as runtime
import accounts
from api import validate
from login_server import authorize, LoginApplication


class FakePage:
    url = 'https://aliexpress.ru/cart'
    error = None
    def goto(self, *args, **kwargs):
        if self.error: raise self.error
    def wait_for_timeout(self, *args): pass
    def content(self): return '<script id="__AER_DATA__">{"loginStatusV2":"LOGGED_IN"}</script>'


class FakeContext:
    def __init__(self, path, registry):
        self.registry, self.path = registry, path
        self.state = json.loads(json.dumps(registry.get(path, {'cookies': [], 'origins': []})))
        self.pages, self.browser = [FakePage()], object()
        self.cleared = False
    def clear_cookies(self): self.cleared = True; self.state['cookies'] = []
    def add_cookies(self, cookies): self.state['cookies'] = cookies
    def add_init_script(self, script): pass
    def cookies(self): return self.state['cookies']
    def storage_state(self, **kwargs): return self.state
    def close(self): self.registry[self.path] = self.state


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {'XDG_STATE_HOME': self.temp.name, 'MARKETPLACE_BROWSER_ROOT': self.temp.name + '/profiles'}, clear=True).start()
        self.registry, self.contexts = {}, []
        def launch(path, **kwargs):
            ctx = FakeContext(path, self.registry); self.contexts.append(ctx); return ctx
        fake_driver = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))
        patch('patchright.sync_api.sync_playwright', side_effect=lambda: contextlib.nullcontext(fake_driver)).start()
        patch.object(runtime, 'launch_options', return_value={'headless': True}).start()
        patch.object(runtime, 'verify_groups', return_value={'status': 'verified'}).start()

    def seed(self):
        runtime.save_private(runtime.legacy_session('aliexpress'), {'domain_scope': 'aliexpress.ru', 'exported_at': 'synthetic-import',
            'cookies': [{'host': '.aliexpress.ru', 'name': 'synthetic', 'value': 'SYNTHETIC_ONLY', 'path': '/',
                         'expiry': None, 'isSecure': True, 'isHttpOnly': True}]})

    def test_profile_and_rotated_cookies_survive_new_client_without_reimport(self):
        self.seed()
        with runtime.ManagedBrowser('aliexpress') as first:
            first.context
            profile = first.info['profile_id']
            self.assertTrue(first.context.cleared)
            first.context.state['cookies'][0]['value'] = 'ROTATED_SYNTHETIC'
            first.context.state['origins'] = [{'origin': 'https://aliexpress.ru', 'localStorage': [{'name': 'test', 'value': 'SYNTHETIC_ONLY'}]}]
        with runtime.ManagedBrowser('aliexpress') as second:
            self.assertEqual(second.context.state['cookies'][0]['value'], 'ROTATED_SYNTHETIC')
            self.assertEqual(second.info['profile_id'], profile)
            self.assertFalse(second.context.cleared)
            self.assertTrue(second.context.state['origins'])

    def test_cookie_count_and_http_success_do_not_prove_login(self):
        self.seed()
        report = runtime.status('aliexpress')
        self.assertIsNone(report['authenticated'])
        self.assertTrue(report['metadata_only'])
        self.assertNotIn('SYNTHETIC_ONLY', json.dumps(report))

    def test_lifecycle_checks_preserve_auth_on_network_failure(self):
        with runtime.ManagedBrowser('aliexpress') as browser:
            self.assertTrue(browser.validate_login()['authenticated'])
            browser.page.error = RuntimeError('net::ERR_NAME_NOT_RESOLVED PRIVATE_URL')
            result = browser.validate_login()
            self.assertEqual(result['status'], 'dns_error')
            self.assertIsNone(result['authenticated'])
            self.assertTrue(runtime.metadata('aliexpress')['authenticated'])
            self.assertNotIn('PRIVATE_URL', json.dumps(result))

    def test_unrecognized_auth_is_not_anonymous_or_success(self):
        with runtime.ManagedBrowser('aliexpress') as browser:
            browser.open()
            browser.page.content = lambda: '<title>Unrecognized fixture</title>'
            report = browser.validate_login()
            self.assertEqual(report['status'], 'login_unverified')
            self.assertIsNone(report['authenticated'])

    def test_exclusive_session_lease_prevents_concurrent_browser(self):
        with runtime.ManagedBrowser('aliexpress') as first:
            first.context
            with self.assertRaisesRegex(ValueError, 'session_busy'):
                with runtime.ManagedBrowser('aliexpress') as second:
                    second.context

    def test_two_provider_profiles_share_driver_and_keep_separate_contexts(self):
        with runtime.ManagedBrowser('aliexpress') as first:
            first.context
            with runtime.ManagedBrowser('ozon') as second:
                second.context
                self.assertIs(first.driver, second.driver)
                self.assertIsNot(first.context, second.context)
                self.assertEqual(runtime.DRIVERS.record['leases'], 2)
            self.assertEqual(runtime.DRIVERS.record['leases'], 1)
        self.assertFalse(hasattr(runtime.DRIVERS, 'record'))

    def test_failed_launch_does_not_replace_snapshot(self):
        self.seed()
        old = runtime.legacy_session('aliexpress').read_bytes()
        with patch.object(runtime, 'verify_groups', side_effect=runtime.BrowserError('browser_group_mismatch')):
            with self.assertRaises(runtime.BrowserError):
                with runtime.ManagedBrowser('aliexpress') as browser: browser.context
        self.assertEqual(runtime.legacy_session('aliexpress').read_bytes(), old)

    def test_snapshot_filters_foreign_storage_and_preserves_private_modes(self):
        with runtime.ManagedBrowser('aliexpress') as browser:
            browser.context.state['cookies'] = [{'domain': '.evil.test', 'name': 'foreign', 'value': 'OTHER_SITE', 'path': '/',
                                                'expires': -1, 'secure': True, 'httpOnly': True, 'sameSite': 'Lax'}]
            browser.context.state['origins'] = [{'origin': 'https://evil.test', 'localStorage': []}]
        path = runtime.legacy_session('aliexpress')
        saved = runtime.read_private(path)
        self.assertEqual(saved['cookies'], [])
        self.assertEqual(saved['browser_storage'], [])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)

    def test_forget_removes_owned_profile_and_snapshot(self):
        with runtime.ManagedBrowser('aliexpress') as browser:
            browser.context
            profile = browser.profile
        runtime.forget('aliexpress')
        self.assertFalse(profile.exists())
        self.assertFalse(runtime.legacy_session('aliexpress').exists())
        self.assertEqual(runtime.status('aliexpress')['status'], 'login_required')

    def test_cross_site_cookie_import_and_shared_state_are_rejected(self):
        with self.assertRaises(runtime.BrowserError):
            runtime.cookies_from_document('ozon', {'domain_scope': 'aliexpress.ru', 'cookies': []})
        path = Path(self.temp.name) / 'shared'
        path.mkdir(mode=0o755)
        with self.assertRaises(runtime.BrowserError): runtime.private_directory(path)

    def test_handoff_auth_rejects_missing_token_wrong_host_and_cross_origin(self):
        base = 'http://127.0.0.1:1234'
        good = {'Host': '127.0.0.1:1234', 'Origin': base, 'Authorization': 'Bearer SYNTHETIC_ONLY'}
        self.assertTrue(authorize(good, 'SYNTHETIC_ONLY', base))
        for replacement in ({'Host': 'evil.test'}, {'Origin': 'https://evil.test'}, {'Authorization': ''}):
            self.assertFalse(authorize({**good, **replacement}, 'SYNTHETIC_ONLY', base))

    def test_handoff_ids_and_ttl_are_validated_before_any_browser(self):
        for params in ({'ttl_seconds': 1}, {'ttl_seconds': True}, {'providers': ['unknown']}):
            with self.assertRaises(ValueError): validate({'method': 'session.login.start', 'params': params}, 'accounts')
        with self.assertRaises(ValueError): accounts.descriptor('../outside')

    def test_partial_login_does_not_close_other_provider_handoff(self):
        app = LoginApplication({'providers': ['ozon', 'aliexpress'], 'handoff_id': 'synthetic', 'expires_at': 'synthetic'})
        def check(provider):
            app.results[provider] = {'provider': provider, 'status': 'ok' if provider == 'ozon' else 'login_required',
                                     'authenticated': provider == 'ozon'}
            return app.results[provider]
        with patch.object(app, 'verify', side_effect=check):
            result = app.finish()
        self.assertEqual(result['status'], 'partial')
        self.assertFalse(app.stopped)

    def test_completed_handoff_retains_verification_without_credential_token(self):
        doc = {'providers': ['ozon'], 'handoff_id': 'synthetic', 'expires_at': 'synthetic',
               'status': 'closed', 'token': 'PRIVATE_HANDOFF_TOKEN'}
        app = LoginApplication(doc)
        app.results['ozon'] = {'provider': 'ozon', 'status': 'ok', 'authenticated': True}
        with patch.object(app, 'verify'):
            self.assertTrue(app.finish()['complete'])
        doc['sessions'] = list(app.results.values())
        result = accounts.public_handoff(doc)
        self.assertTrue(result['complete'])
        self.assertTrue(result['sessions'][0]['authenticated'])
        self.assertNotIn('PRIVATE_HANDOFF_TOKEN', json.dumps(result))


if __name__ == '__main__': unittest.main()
