"""Synthetic Chromium cookie fixtures; never reads browser/keyring credentials."""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from Cryptodome.Cipher import AES

import chromium_session as chromium
import ali_session
import session_store


def encrypted(value, host=".aliexpress.ru", password=b"SYNTHETIC-KEY", version=24):
    payload = (hashlib.sha256(host.encode()).digest() if version >= 24 else b"") + value.encode()
    pad = 16 - len(payload) % 16
    key = hashlib.pbkdf2_hmac("sha1", password, b"saltysalt", 1, dklen=16)
    return b"v11" + AES.new(key, AES.MODE_CBC, iv=b" " * 16).encrypt(payload + bytes([pad]) * pad)


class ChromiumSessionTests(unittest.TestCase):
    def test_v24_decryption_checks_host_digest_and_key(self):
        value = encrypted("SYNTHETIC-COOKIE")
        self.assertEqual(chromium.decrypt(value, ".aliexpress.ru", 24, b"SYNTHETIC-KEY"), "SYNTHETIC-COOKIE")
        with self.assertRaises(ValueError):
            chromium.decrypt(value, "aliexpress.ru", 24, b"SYNTHETIC-KEY")
        with self.assertRaises(ValueError):
            chromium.decrypt(value, ".aliexpress.ru", 24, b"WRONG-KEY")
        with self.assertRaises(ValueError):
            chromium.decrypt(b"v20" + value[3:], ".aliexpress.ru", 24, b"SYNTHETIC-KEY")

    def test_export_filters_before_decryption_and_keeps_session_cookie(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = root / "Default"
            profile.mkdir()
            con = sqlite3.connect(profile / "Cookies")
            self.addCleanup(con.close)
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("CREATE TABLE meta (key TEXT, value TEXT)")
            con.execute("INSERT INTO meta VALUES ('version','24')")
            con.execute("CREATE TABLE cookies(host_key TEXT,name TEXT,value TEXT,encrypted_value BLOB,path TEXT,expires_utc INTEGER,is_secure INTEGER,is_httponly INTEGER,is_persistent INTEGER,has_expires INTEGER,top_frame_site_key TEXT)")
            now_chrome = int((time.time() + 11644473600) * 1000000)
            rows = [(".aliexpress.ru", "session", "", encrypted("SYNTHETIC-COOKIE"), "/", 0, 1, 1, 0, 0, ""),
                    ("elsewhere.test", "foreign", "", b"INVALID", "/", 0, 1, 1, 0, 0, ""),
                    ("aliexpress.ru.evil.test", "lookalike", "", b"INVALID", "/", 0, 1, 1, 0, 0, ""),
                    (".aliexpress.ru", "partition", "", b"INVALID", "/", 0, 1, 1, 0, 0, "https://example.test"),
                    (".aliexpress.ru", "expired", "", b"INVALID", "/", now_chrome-1000000, 1, 1, 1, 1, "")]
            con.executemany("INSERT INTO cookies VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
            con.commit()
            before = hashlib.sha256((profile / "Cookies-wal").read_bytes()).digest()
            output = root / "private/session.json"
            with patch("chromium_session.storage_password", return_value=b"SYNTHETIC-KEY") as keyring:
                result = chromium.export(profile, output)
            keyring.assert_called_once_with()
            self.assertEqual(result["cookie_count"], 1)
            self.assertNotIn("SYNTHETIC", json.dumps(result))
            saved = json.loads(output.read_text())
            self.assertEqual(saved["cookies"][0]["name"], "session")
            self.assertIsNone(saved["cookies"][0]["expiry"])
            self.assertNotIn("SYNTHETIC-KEY", output.read_text())
            self.assertEqual(before, hashlib.sha256((profile / "Cookies-wal").read_bytes()).digest())
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_saved_chromium_profile_is_used_when_refreshing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = root / "Default"
            path = root / "private/session.json"
            session_store.atomic_write(path, session_store.managed_document({"cookies": []}, profile, "chromium"))
            with patch("ali_session.refresh", return_value={"status": "ok"}) as refresh:
                ali_session.ensure(path, force_refresh=True)
            refresh.assert_called_once_with(path, profile.resolve(), "chromium")
            self.assertEqual(ali_session.user_action({"status": "authentication_required", "browser": "chromium"})["browser"], "chromium")


if __name__ == "__main__":
    unittest.main()
