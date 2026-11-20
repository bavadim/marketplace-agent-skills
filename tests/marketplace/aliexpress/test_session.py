#!/usr/bin/env python3
"""Credential-handling tests using synthetic cookies only; no network requests."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
import urllib.request
import firefox_session as exporter
import session_probe as probe


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name); self.profile = self.root / "profile"; self.profile.mkdir()
        self.con = sqlite3.connect(self.profile / "cookies.sqlite")
        self.con.execute('PRAGMA journal_mode=WAL')
        self.con.execute('PRAGMA locking_mode=EXCLUSIVE')
        self.con.execute('CREATE TABLE moz_cookies(name TEXT, value TEXT, host TEXT, path TEXT, expiry INTEGER, isSecure INTEGER, isHttpOnly INTEGER, originAttributes TEXT, sameSite INTEGER)')
        for name, host, expiry, origin in [('auth','.aliexpress.ru',time.time()+3600,''),
            ('onlyhost','aliexpress.ru',time.time()+3600,''),
            ('other','example.com',time.time()+3600,''),
            ('lookalike','aliexpress.ru.evil.test',time.time()+3600,''),
            ('container','.aliexpress.ru',time.time()+3600,'^userContextId=2'),
            ('expired','.aliexpress.ru',time.time()-1,'')]:
            self.con.execute('INSERT INTO moz_cookies VALUES (?,?,?,?,?,?,?,?,?)',
                (name,'SYNTHETIC-SECRET-'+name,host,'/',int(expiry),1,1,origin,1))
        self.con.commit(); self.output=self.root/'private'/'cookies.json'

    def tearDown(self):
        self.con.close(); self.tmp.cleanup()

    def test_locked_wal_snapshot_scope_and_source_unchanged(self):
        source_files = [self.profile/'cookies.sqlite', self.profile/'cookies.sqlite-wal']
        hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files]
        result=exporter.export(self.profile,self.output)
        self.assertEqual(result['cookie_count'],2)
        self.assertNotIn('SYNTHETIC-SECRET',json.dumps(result))
        self.assertEqual({r['name'] for r in json.loads(self.output.read_text())['cookies']},{'auth','onlyhost'})
        self.assertEqual(hashes,[hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files])
        self.assertEqual(self.output.stat().st_mode & 0o777,0o600)
        self.assertEqual(self.output.parent.stat().st_mode & 0o777,0o700)

    def test_existing_export_not_overwritten(self):
        exporter.export(self.profile,self.output); before=self.output.read_bytes()
        with self.assertRaises(FileExistsError):exporter.export(self.profile,self.output)
        self.assertEqual(before,self.output.read_bytes())

    def test_separate_container_not_flattened(self):
        result=exporter.export(self.profile,self.output,'^userContextId=2')
        self.assertEqual(result['cookie_count'],1)
        with self.assertRaises(ValueError):probe.load_jar(self.output)

    def test_cookie_host_scope_is_preserved(self):
        exporter.export(self.profile,self.output); jar=probe.load_jar(self.output)
        root=urllib.request.Request('https://aliexpress.ru/')
        login=urllib.request.Request('https://login.aliexpress.ru/')
        evil=urllib.request.Request('https://aliexpress.ru.evil.test/')
        for request in (root,login,evil):jar.add_cookie_header(request)
        self.assertIn('onlyhost=',root.get_header('Cookie'))
        self.assertNotIn('onlyhost=',login.get_header('Cookie'))
        self.assertIn('auth=',login.get_header('Cookie'))
        self.assertIsNone(evil.get_header('Cookie'))

    def test_http_rejects_external_and_insecure_destinations(self):
        for url in ['http://aliexpress.ru/','https://aliexpress.ru.evil.test/',
                    'https://user:pass@aliexpress.ru/','https://aliexpress.ru:8443/']:
            self.assertFalse(probe.allowed(url))
        self.assertTrue(probe.allowed('https://login.aliexpress.ru/'))
        with self.assertRaises(ValueError):probe.ScopedRedirect().redirect_request(None,None,302,'',{},'https://example.com/')

    def test_shared_permissions_are_rejected(self):
        exporter.export(self.profile,self.output); os.chmod(self.output,0o644)
        with self.assertRaises(ValueError):probe.load_jar(self.output)

    def test_only_canonical_product_links_reported(self):
        page=probe.PageSummary(); page.feed('<a href="/item/123.html?tracking=private">X</a><a href="https://evil.test/item/4.html">Y</a>')
        self.assertEqual(page.products,{'https://aliexpress.ru/item/123.html'})


if __name__ == '__main__': unittest.main(verbosity=2)
