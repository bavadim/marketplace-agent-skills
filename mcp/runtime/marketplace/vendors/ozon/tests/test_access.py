"""Offline checks for the Ozon access probe; no live network or account."""
import importlib.util
import json
import sys
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('ozon', Path(__file__).parents[1]/'scripts/ozon.py')
ozon = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ozon)


class AccessTests(unittest.TestCase):
    def test_authenticated_probe_persists_server_cookies_without_claiming_login_on_captcha(self):
        scripts=str(Path(__file__).parents[1]/'scripts')
        with patch.object(sys,'path',[scripts,*sys.path]):
            import ozon_session
        def run(args, **kwargs):
            jar=Path(args[args.index('--cookie-jar')+1])
            jar.write_text(jar.read_text().replace('TEST_COOKIE','ROTATED_TEST_COOKIE'))
            Path(args[args.index('--output')+1]).write_text('<title>Antibot Captcha</title>')
            self.assertNotIn('TEST_COOKIE',str(args))
            return subprocess.CompletedProcess(args,0,'403','')
        with tempfile.TemporaryDirectory() as tmp:
            saved=Path(tmp)/'session.json'
            ozon_session.atomic_write(saved,{'domain_scope':'ozon.ru','cookies':[
                {'host':'.ozon.ru','name':'synthetic','value':'TEST_COOKIE','path':'/',
                 'expiry':None,'isSecure':True,'isHttpOnly':True}]})
            with patch.object(ozon.subprocess,'run',side_effect=run):
                result=ozon.probe('https://www.ozon.ru/',session=saved)
            self.assertEqual(result['status'],'http_challenge')
            self.assertEqual(result['client'],'curl')
            self.assertEqual(result['browser_status'],'not_observed')
            self.assertIsNone(result['authenticated'])
            self.assertTrue(result['cookies_supplied'])
            self.assertNotIn('TEST_COOKIE',str(result))
            self.assertEqual(json.loads(saved.read_text())['cookies'][0]['value'],'ROTATED_TEST_COOKIE')

    def test_http_success_does_not_hide_an_access_error(self):
        self.assertEqual(ozon.classify(200, '<title>Похоже, нет соединения</title>')[0], 'access_blocked')
        self.assertEqual(ozon.classify(429, '<title>Ozon</title>')[0], 'rate_limited')
        self.assertEqual(ozon.classify(200, '<title>CAPTCHA</title>')[0], 'http_challenge')

    def test_redirect_cannot_leave_ozon_or_disable_tls(self):
        for target in ['https://www.ozon.ru.evil.test/', 'http://www.ozon.ru/', 'https://user@www.ozon.ru/']:
            self.assertFalse(ozon.allowed(target))
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            Path(args[args.index('--dump-header')+1]).write_text('HTTP/2 302\r\nLocation: https://example.com/?token=SECRET\r\n')
            return subprocess.CompletedProcess(args, 0, '302', '')
        with patch.object(ozon.subprocess, 'run', side_effect=run):
            result = ozon.probe('https://www.ozon.ru/')
        self.assertEqual(result['status'], 'outside_scope_redirect')
        self.assertEqual(len(calls), 1)
        self.assertNotIn('SECRET', str(result))

    def test_transport_failure_does_not_retry_or_echo_secrets(self):
        with patch.object(ozon.subprocess, 'run', return_value=subprocess.CompletedProcess([], 28, '', 'SECRET')) as run:
            result = ozon.probe('https://www.ozon.ru/?token=SECRET')
        self.assertEqual(result['status'], 'transport_error')
        self.assertEqual(result['reason'], 'timeout')
        self.assertEqual(run.call_count, 1)
        self.assertNotIn('SECRET', str(result))

    def test_curl_dns_error_is_distinct_from_timeout(self):
        with patch.object(ozon.subprocess, 'run', return_value=subprocess.CompletedProcess([], 6, '', 'SECRET')):
            result = ozon.probe('https://www.ozon.ru/')
        self.assertEqual(result['status'], 'transport_error')
        self.assertEqual(result['reason'], 'dns_error')
        self.assertNotIn('SECRET', str(result))

    def test_diagnostic_url_drops_inline_documents(self):
        self.assertEqual(ozon.safe_url('data:text/html,SECRET'),'data:')
        self.assertEqual(ozon.safe_url('about:blank'),'about:blank')

    def test_capture_is_private_and_proxy_environment_is_cleared(self):
        def run(args, **kwargs):
            self.assertNotIn('HTTPS_PROXY', kwargs['env'])
            self.assertEqual(args[args.index('--proxy')+1], '')
            Path(args[args.index('--output')+1]).write_text('<title>Ozon</title>')
            return subprocess.CompletedProcess(args, 0, '200', '')
        with tempfile.TemporaryDirectory() as tmp, patch.dict(ozon.os.environ, {'HTTPS_PROXY':'SECRET'}):
            capture = Path(tmp)/'page.html'
            capture.write_text('old')
            capture.chmod(0o644)
            with patch.object(ozon.subprocess, 'run', side_effect=run):
                result = ozon.probe('https://www.ozon.ru/', capture)
            self.assertEqual(capture.stat().st_mode & 0o777, 0o600)
            self.assertFalse(result['authenticated'])
            self.assertFalse(result['cart_modified'])


if __name__ == '__main__':
    unittest.main()
