#!/usr/bin/env python3
"""Authorized Linux Chromium cookie export, strictly limited to aliexpress.ru.

Reads a stable SQLite/WAL copy. Uses only Chromium's own Secret Service item for
v11 decryption in memory. Never exports the key, passwords, history or other sites.
"""
import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time

from firefox_session import acquire, private_write
from session_store import private_parent


def storage_password():
    import gi
    gi.require_version("Secret", "1")
    from gi.repository import Gio, Secret
    schema = Secret.Schema.new("chrome_libsecret_os_crypt_password_v2", Secret.SchemaFlags.NONE,
                               {"application": Secret.SchemaAttributeType.STRING})
    cancel = Gio.Cancellable()
    timer = threading.Timer(20, cancel.cancel)
    timer.daemon = True
    timer.start()
    try:
        value = Secret.password_lookup_sync(schema, {"application": "chromium"}, cancel)
        if value is None:
            raise ValueError("chromium_keyring_item_unavailable")
        return value.encode("utf-8")
    except Exception as error:
        raise ValueError("chromium_keyring_unavailable") from error
    finally:
        timer.cancel()


def decrypt(encrypted, host, version, password):
    from Cryptodome.Cipher import AES
    prefix = encrypted[:3]
    if prefix == b"v10":
        password = b"peanuts"
    elif prefix != b"v11" or password is None:
        raise ValueError("unsupported_chromium_cookie_encryption")
    key = hashlib.pbkdf2_hmac("sha1", password, b"saltysalt", 1, dklen=16)
    payload = AES.new(key, AES.MODE_CBC, iv=b" " * 16).decrypt(encrypted[3:])
    pad = payload[-1] if payload else 0
    if not 1 <= pad <= 16 or payload[-pad:] != bytes([pad]) * pad:
        raise ValueError("chromium_cookie_decryption_failed")
    payload = payload[:-pad]
    if version >= 24:
        if payload[:32] != hashlib.sha256(host.encode("utf-8")).digest():
            raise ValueError("chromium_cookie_host_digest_mismatch")
        payload = payload[32:]
    return payload.decode("utf-8")


def scoped_rows(profile):
    profile = Path(profile)
    database = profile / "Network/Cookies" if (profile / "Network/Cookies").is_file() else profile / "Cookies"
    copies = acquire(database.parent, database.name)
    with tempfile.TemporaryDirectory(prefix="ali-chromium-") as tmp:
        root = Path(tmp)
        for name, value in copies.items():
            path = root / name
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(value)
        del copies
        with contextlib.closing(sqlite3.connect(root / "Cookies")) as con:
            con.row_factory = sqlite3.Row
            if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("inconsistent_chromium_cookie_snapshot")
            version = int(con.execute("SELECT value FROM meta WHERE key='version'").fetchone()[0])
            fields = {r[1] for r in con.execute("PRAGMA table_info(cookies)")}
            required = {"host_key", "name", "value", "encrypted_value", "path", "expires_utc",
                        "is_secure", "is_httponly", "is_persistent", "has_expires"}
            if not required <= fields:
                raise ValueError("unsupported_chromium_cookie_schema")
            columns = sorted(required | ({"top_frame_site_key"} & fields))
            rows = [dict(r) for r in con.execute("SELECT " + ",".join(columns) +
                      " FROM cookies WHERE host_key=? OR host_key LIKE ?", ("aliexpress.ru", "%.aliexpress.ru"))]
    return version, rows


def export(profile, output):
    version, rows = scoped_rows(profile)
    eligible = []
    for row in rows:
        if row.get("top_frame_site_key"):
            continue  # Do not flatten partitioned cookies into the top-level session.
        persistent = bool(row["is_persistent"] and row["has_expires"])
        expiry = int(row["expires_utc"]) // 1000000 - 11644473600 if persistent else None
        if expiry is not None and expiry <= time.time():
            continue
        eligible.append((row, expiry))
    password = (storage_password() if any(r["encrypted_value"].startswith(b"v11") for r, _ in eligible) else None)
    cookies = []
    for row, expiry in eligible:
        value = decrypt(row["encrypted_value"], row["host_key"], version, password) if row["encrypted_value"] else row["value"]
        cookies.append({"host": row["host_key"], "name": row["name"], "value": value, "path": row["path"],
                        "expiry": expiry, "isSession": expiry is None, "isSecure": bool(row["is_secure"]),
                        "isHttpOnly": bool(row["is_httponly"]), "originAttributes": ""})
    if not cookies:
        raise ValueError("no_aliexpress_cookies_in_chromium_profile")
    document = {"schema_version": 1, "domain_scope": "aliexpress.ru", "origin_attributes": "",
                "source": "Linux Chromium Cookies + WAL; scoped decryption", "cookies": cookies,
                "exported_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    output = Path(output).absolute()
    private_parent(output)
    private_write(output, json.dumps(document, ensure_ascii=False, indent=2) + "\n")
    return {"output": str(output), "cookie_count": len(cookies), "session_cookie_count": sum(c["isSession"] for c in cookies),
            "http_only_count": sum(c["isHttpOnly"] for c in cookies), "login_verified": False, "permissions": "0600"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(export(args.profile, args.output), ensure_ascii=False))
    except Exception as error:
        parser.exit(2, "Chromium export failed (" + type(error).__name__ + "); no credentials printed.\n")


if __name__ == "__main__":
    main()
