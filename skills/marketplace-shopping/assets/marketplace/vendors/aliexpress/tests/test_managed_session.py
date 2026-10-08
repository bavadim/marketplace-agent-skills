"""Session lifecycle regressions with synthetic credentials and local fixtures only."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import ali_http
import ali_session
from ali_cart import CartAPI
from curl_probe import http_cookie_jar
import firefox_session
import session_store as store


def document(value="SYNTHETIC-INITIAL"):
    return {"domain_scope": "aliexpress.ru", "origin_attributes": "", "cookies": [
        {"host": ".aliexpress.ru", "path": "/", "name": "session_test", "value": value,
         "expiry": None, "isSession": True, "isHttpOnly": True, "isSecure": True,
         "originAttributes": ""}]}


class ManagedSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "private/session.json"
        store.atomic_write(self.path, store.managed_document(document()))

    def test_curl_session_expiry_zero_and_httponly_survive_roundtrip(self):
        jar = self.root / "jar.txt"
        http_cookie_jar(self.path).save(str(jar), ignore_discard=True, ignore_expires=False)
        line = next(x for x in jar.read_text().splitlines() if "\t" in x)
        self.assertEqual(line.split("\t")[4], "0")
        self.assertTrue(line.startswith("#HttpOnly_"))
        row = store.cookie_rows(jar)[0]
        self.assertTrue(row["isHttpOnly"])
        self.assertIsNone(row["expiry"])
        self.assertTrue(row["isSession"])

    def test_new_client_reuses_saved_rotated_credentials_and_stable_journal_binding(self):
        initial = store.binding(self.path)
        seen = []
        def network(args, **kwargs):
            jar = Path(args[args.index("--cookie") + 1])
            seen.append(store.cookie_rows(jar)[0]["value"])
            jar.write_text("# Netscape HTTP Cookie File\n#HttpOnly_.aliexpress.ru\tTRUE\t/\tTRUE\t0\tsession_test\tSYNTHETIC-ROTATED\n")
            if args[-1] == "https://aliexpress.ru/cart":
                data = b'<script id="__AER_DATA__">{"widgets":[{"props":{"isLoggedIn":true,"loginStatus":"SIGNED_IN"}}]}</script>'
            else:
                data = json.dumps({"data": {"items": [], "summary": {"totalItemsCount": 0}, "itemsSummary": {
                    "page": {"currentPage": 1, "totalPages": 1}, "selectedItemsIds": [], "selectAll": False, "sortType": 2}}}).encode()
            Path(args[args.index("--dump-header") + 1]).write_text("HTTP/2 200\r\n\r\n")
            Path(args[args.index("--output") + 1]).write_bytes(data)
            return subprocess.CompletedProcess(args, 0, "200\t0.1\t2\t192.0.2.1\t0\t0", "")
        with patch("ali_http.default_session", return_value=self.path), patch("ali_http.subprocess.run", network):
            for _ in range(2):
                with ali_http.LegacyHTTPClient() as client:
                    result = CartAPI(client).read()
                    self.assertTrue(result["authenticated"])
                    self.assertEqual(result["count"], 0)
        self.assertEqual(seen, ["SYNTHETIC-INITIAL", "SYNTHETIC-ROTATED", "SYNTHETIC-ROTATED", "SYNTHETIC-ROTATED"])
        self.assertEqual(store.binding(self.path), initial)
        self.assertTrue(store.read_private(self.path)["login_verified"])
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)

    def test_expired_deleted_and_foreign_cookies_not_restored(self):
        jar = self.root / "jar.txt"
        jar.write_text("# Netscape HTTP Cookie File\n.aliexpress.ru\tTRUE\t/\tTRUE\t1\told\tSYNTHETIC\n.example.com\tTRUE\t/\tTRUE\t0\tforeign\tSYNTHETIC\n")
        with store.session_lock(self.path):
            saved = store.read_private(self.path)
            saved["cookies"] = store.cookie_rows(jar)
            store.atomic_write(self.path, saved)
        self.assertEqual(store.read_private(self.path)["cookies"], [])

    def test_simultaneous_clients_fail_without_overwriting_session(self):
        with ali_http.LegacyHTTPClient(self.path):
            with self.assertRaises(ali_http.ReadFailure) as failure:
                ali_http.LegacyHTTPClient(self.path)
            self.assertEqual(failure.exception.status, "session_busy")

    def test_reimport_gets_new_journal_binding(self):
        initial = store.binding(self.path)
        store.atomic_write(self.path, store.managed_document(document()))
        self.assertNotEqual(store.binding(self.path), initial)

    def test_invalid_login_import_does_not_replace_saved_session(self):
        original = self.path.read_bytes()
        def export(profile, output, **kwargs):
            store.atomic_write(output, document("SYNTHETIC-GUEST"))
            return {"cookie_count": 1, "session_cookie_count": 1}
        with patch("ali_session.export", export), patch("ali_session.check", return_value={"status": "authentication_required", "authenticated": False}):
            result = ali_session.refresh(self.path, self.root / "profile")
        self.assertFalse(result["saved_session_replaced"])
        self.assertIn("next_action", result)
        self.assertNotIn("SYNTHETIC", json.dumps(result))
        self.assertEqual(self.path.read_bytes(), original)

    def test_valid_import_is_promoted_and_ensure_reuses_it_without_export(self):
        def export(profile, output, **kwargs):
            store.atomic_write(output, document("SYNTHETIC-NEW"))
            return {"cookie_count": 1, "session_cookie_count": 1}
        ok = {"status": "ok", "authenticated": True, "cart_count": 3, "cart_complete": True}
        with patch("ali_session.export", export), patch("ali_session.check", return_value=ok):
            result = ali_session.refresh(self.path, self.root / "profile")
        self.assertTrue(result["saved_session_replaced"])
        self.assertEqual(store.read_private(self.path)["cookies"][0]["value"], "SYNTHETIC-NEW")
        with patch("ali_session.check", return_value=dict(ok)), patch("ali_session.export", side_effect=AssertionError("must reuse")):
            self.assertTrue(ali_session.ensure(self.path)["reused_saved_session"])

    def test_symlink_and_shared_session_rejected(self):
        link = self.root / "link.json"
        link.symlink_to(self.path)
        with self.assertRaises(OSError):
            store.read_private(link)
        os.chmod(self.path, 0o644)
        with self.assertRaises(store.SessionError):
            store.read_private(self.path)

    def test_firefox_snapshot_exports_only_current_scoped_session_cookies(self):
        import lz4.block
        profile = self.root / "profile"
        (profile / "sessionstore-backups").mkdir(parents=True)
        fixture = {"windows": [{"tabs": [{"PRIVATE_HISTORY": "DO_NOT_EXPORT"}]}], "cookies": [
            {"host": ".aliexpress.ru", "name": "session", "value": "SYNTHETIC", "path": "/", "secure": True, "httponly": True},
            {"host": "elsewhere.test", "value": "OTHER_SITE"},
            {"host": "aliexpress.ru.evil.test", "value": "LOOKALIKE"},
            {"host": ".aliexpress.ru", "value": "CONTAINER", "originAttributes": {"userContextId": 2}},
            {"host": ".aliexpress.ru", "value": "PARTITION", "isPartitioned": True}]}
        raw = b"mozLz40\0" + lz4.block.compress(json.dumps(fixture).encode())
        (profile / "sessionstore-backups/recovery.jsonlz4").write_bytes(raw)
        rows = firefox_session.session_rows(profile)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "session")
        self.assertIsNone(rows[0]["expiry"])
        self.assertTrue(rows[0]["isHttpOnly"])
        self.assertNotIn("PRIVATE_HISTORY", json.dumps(rows))
        (profile / "sessionstore-backups/recovery.jsonlz4").rename(profile / "sessionstore-backups/previous.jsonlz4")
        self.assertEqual(firefox_session.session_rows(profile), [])


if __name__ == "__main__":
    unittest.main()
