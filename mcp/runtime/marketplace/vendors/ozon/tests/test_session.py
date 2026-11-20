"""Synthetic session isolation tests. Never reads a real browser profile."""
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('ozon_session', Path(__file__).parents[1]/'scripts/ozon_session.py')
session = importlib.util.module_from_spec(spec)
spec.loader.exec_module(session)


def row(host='.ozon.ru', partition='', expires=None):
    return {'host_key':host, 'name':'synthetic', 'value':'TEST_NOT_A_REAL_COOKIE',
        'encrypted_value':b'', 'path':'/', 'is_secure':1, 'is_httponly':1,
        'is_persistent':expires is not None, 'has_expires':expires is not None,
        'expires_utc':int(((expires or 0)+11644473600)*1000000), 'top_frame_site_key':partition,
        'samesite':2}


class SessionTests(unittest.TestCase):
    def test_ensure_reuses_session_without_decrypting_browser_again(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(session, 'scoped_rows', return_value=(24,[row()])):
            target=Path(tmp)/'session.json'
            session.export(Path(tmp)/'profile',target)
            with patch.object(session,'export',side_effect=AssertionError('Unexpected browser export')):
                result=session.prepare(target,'ensure')
            self.assertTrue(result['reused_saved_session'])
            self.assertEqual(result['cookie_count'],1)
            self.assertFalse(result['login_verified'])

    def test_server_cookie_rotation_stays_in_ozon_scope(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(session, 'scoped_rows', return_value=(24,[row()])):
            target,jar=Path(tmp)/'session.json',Path(tmp)/'cookies'
            session.export(Path(tmp)/'profile',target)
            session.write_jar(target,jar)
            text=jar.read_text().replace('TEST_NOT_A_REAL_COOKIE','ROTATED_SYNTHETIC')
            jar.write_text(text+'.example.test\tTRUE\t/\tTRUE\t0\tother\tIGNORE\n')
            session.persist_jar(target,jar)
            saved=json.loads(target.read_text())
            self.assertEqual(len(saved['cookies']),1)
            self.assertEqual(saved['cookies'][0]['value'],'ROTATED_SYNTHETIC')
            self.assertIsNone(saved['cookies'][0]['expiry'])
            self.assertEqual(saved['cookies'][0]['sameSite'],'Strict')
            self.assertNotIn('IGNORE',json.dumps(saved))
            self.assertEqual(saved['browser_profile'],str((Path(tmp)/'profile').resolve()))

    def test_refresh_uses_saved_profile_and_requires_uncontended_lock(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(session, 'scoped_rows', return_value=(24,[row()])):
            target=Path(tmp)/'session.json'
            session.export(Path(tmp)/'profile',target)
            with session.session_lock(target):
                with self.assertRaises(ValueError) as error:session.prepare(target,'refresh')
                self.assertEqual(error.exception.status,'session_busy')
            with patch.object(session,'export',return_value={'status':'imported'}) as exporter:
                session.prepare(target,'refresh')
                self.assertEqual(exporter.call_args.args[0],str((Path(tmp)/'profile').resolve()))

    def test_export_excludes_other_sites_expired_and_partitioned_cookies(self):
        data = [row(), row('.www.ozon.ru'), row('.aliexpress.ru'), row('.evilozon.ru'),
                row(partition='https://other.test'), row(expires=time.time()-60)]
        with tempfile.TemporaryDirectory() as tmp, patch.object(session, 'scoped_rows', return_value=(24,data)):
            target = Path(tmp)/'session.json'
            result = session.export(Path(tmp)/'profile', target)
            saved = json.loads(target.read_text())
            self.assertEqual(result['cookie_count'],2)
            self.assertEqual(saved['domain_scope'],'ozon.ru')
            self.assertFalse(saved['login_verified'])
            self.assertEqual(target.stat().st_mode & 0o777,0o600)
            self.assertNotIn('TEST_NOT_A_REAL_COOKIE',str(result))

    def test_jar_refuses_an_aliexpress_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp)/'session.json', Path(tmp)/'cookies.txt'
            session.atomic_write(source,{'domain_scope':'aliexpress.ru','cookies':[]})
            with self.assertRaises(ValueError): session.write_jar(source,target)
            self.assertFalse(target.exists())

    def test_jar_is_private_and_rejects_cookie_line_injection(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(session, 'scoped_rows', return_value=(24,[row()])):
            source,target=Path(tmp)/'session.json',Path(tmp)/'cookies.txt'
            session.export(Path(tmp)/'profile',source)
            session.write_jar(source,target)
            self.assertIn('#HttpOnly_.ozon.ru\tTRUE\t/\tTRUE\t0\t',target.read_text())
            self.assertEqual(target.stat().st_mode & 0o777,0o600)
            saved=json.loads(source.read_text());saved['cookies'][0]['value']='test\ninvalid'
            session.atomic_write(source,saved)
            with self.assertRaises(ValueError):session.write_jar(source,target)


if __name__ == '__main__':unittest.main()
