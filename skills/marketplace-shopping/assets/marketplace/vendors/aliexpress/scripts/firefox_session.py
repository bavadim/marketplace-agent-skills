#!/usr/bin/env python3
"""Export only aliexpress.ru Firefox cookies locally after explicit user authorization.

Never prints cookie values. Does not write to the Firefox profile. Requires a stable
copy of cookies.sqlite and its WAL; refuses if either changes during acquisition.
"""
import argparse
import collections
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time


def private_write(path, content):
    path = Path(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(content)


def acquire(profile, database="cookies.sqlite"):
    paths = [profile / database, profile / (database + "-wal")]
    def signature():
        return [(p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns) if p.exists() else None for p in paths]
    before = signature()
    if before[0] is None:
        raise ValueError("cookies.sqlite not found")
    copies = {p.name: p.read_bytes() for p, state in zip(paths, before) if state is not None}
    if before != signature():
        raise ValueError("Cookie database changed while reading; close Firefox and retry")
    # Compare a second read as well; never accept an in-flight WAL/checkpoint.
    for p in paths:
        if p.name in copies and hashlib.sha256(p.read_bytes()).digest() != hashlib.sha256(copies[p.name]).digest():
            raise ValueError("Cookie database changed while reading; close Firefox and retry")
    if before != signature():
        raise ValueError("Cookie database changed while reading; close Firefox and retry")
    return copies


def scoped_rows(profile):
    copies = acquire(profile)
    with tempfile.TemporaryDirectory(prefix="ali-firefox-") as tmp:
        root = Path(tmp)
        for name, data in copies.items():
            fd = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
        del copies
        # SQLite can replay committed WAL pages in the private copy, never the source.
        with contextlib.closing(sqlite3.connect(root / "cookies.sqlite", timeout=3)) as con:
            con.row_factory = sqlite3.Row
            if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Inconsistent snapshot; close Firefox and retry")
            columns = {r[1] for r in con.execute("PRAGMA table_info(moz_cookies)")}
            required = {"name", "value", "host", "path", "expiry", "isSecure", "isHttpOnly", "originAttributes"}
            if not required <= columns:
                raise ValueError("Unsupported Firefox cookie schema")
            wanted = sorted(required | ({"sameSite"} & columns))
            # Exact suffix boundary excludes lookalikes and unrelated domains.
            sql = "SELECT " + ",".join(wanted) + " FROM moz_cookies WHERE host = ? OR host LIKE ?"
            rows = [dict(row) for row in con.execute(sql, ("aliexpress.ru", "%.aliexpress.ru"))]
    return rows


def session_rows(profile):
    """Read only scoped cookie records from Firefox's current session snapshot.

    Never export windows, history, form data, or cookies for other domains. Previous
    recovery/upgrade snapshots are deliberately excluded: they can resurrect logout.
    """
    candidates = [profile / "sessionstore-backups" / "recovery.jsonlz4", profile / "sessionstore.jsonlz4"]
    candidates = [p for p in candidates if p.is_file() and not p.is_symlink()]
    if not candidates:
        return []
    path = max(candidates, key=lambda p: p.stat().st_mtime_ns)
    before = path.stat()
    if before.st_size > 64 * 1024 * 1024:
        raise ValueError("Oversized Firefox session snapshot")
    raw = path.read_bytes()
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError("Firefox session snapshot changed; retry after it settles")
    if len(raw) < 12 or raw[:8] != b"mozLz40\0" or int.from_bytes(raw[8:12], "little") > 64 * 1024 * 1024:
        raise ValueError("Unsupported or oversized Firefox session snapshot")
    try:
        import lz4.block
    except ImportError as error:
        raise ValueError("Reading Firefox session cookies requires Python lz4") from error
    try:
        document = json.loads(lz4.block.decompress(raw[8:]))
    except (ValueError, lz4.block.LZ4BlockError) as error:
        raise ValueError("Unreadable Firefox session snapshot") from error
    rows = []
    for cookie in document.get("cookies", []):
        host = cookie.get("host", "")
        if not (host.lstrip(".") == "aliexpress.ru" or host.endswith(".aliexpress.ru")):
            continue
        attrs = cookie.get("originAttributes", {})
        # Do not flatten containers, partitions or private-window sessions.
        if not isinstance(attrs, dict) or any(v not in ("", 0, False, None) for v in attrs.values()) or cookie.get("isPartitioned"):
            continue
        rows.append({"name": cookie.get("name", ""), "value": cookie["value"], "host": host,
                     "path": cookie.get("path") or "/", "expiry": cookie.get("expiry"),
                     "isSecure": bool(cookie.get("secure")), "isHttpOnly": bool(cookie.get("httponly")),
                     "originAttributes": "", "isSession": True})
    return rows


def export(profile, output, origin_attributes="", include_session_cookies=False):
    rows = scoped_rows(profile)
    now = int(time.time())
    eligible = [r for r in rows if r["originAttributes"] == origin_attributes and r["expiry"] > now]
    session = []
    if include_session_cookies:
        if origin_attributes:
            raise ValueError("Session snapshot import supports only the ordinary Firefox context")
        session = [r for r in session_rows(profile) if r["expiry"] is None or r["expiry"] > now]
        merged = {(r["host"], r["path"], r["name"]): r for r in eligible}
        merged.update({(r["host"], r["path"], r["name"]): r for r in session})
        eligible = list(merged.values())
    if not eligible:
        scopes = dict(collections.Counter(r["originAttributes"] for r in rows if r["expiry"] > now))
        raise ValueError("No unexpired aliexpress.ru cookies in selected context; context counts: " + json.dumps(scopes))
    # Keep original Firefox attributes, without guessing cross-browser SameSite mappings.
    document = {"schema_version": 1, "domain_scope": "aliexpress.ru", "source": "Firefox cookies.sqlite + WAL" + (" + current session cookies" if include_session_cookies else ""),
                "exported_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "origin_attributes": origin_attributes, "cookies": eligible,
                "note": "Local credential file. Cookies not flushed by Firefox may be absent. Cookie presence does not prove login."}
    output = Path(output).absolute()
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if output.parent.is_symlink():
        raise ValueError("Refusing a symlink output directory")
    if output.parent.stat().st_mode & 0o077:
        raise ValueError("Output directory must be private (chmod 700)")
    private_write(output, json.dumps(document, ensure_ascii=False, indent=2) + "\n")
    return {"output": str(output), "cookie_count": len(eligible),
            "hosts": dict(collections.Counter(r["host"] for r in eligible)),
            "http_only_count": sum(bool(r["isHttpOnly"]) for r in eligible), "session_cookie_count": len(session),
            "origin_attributes": origin_attributes, "permissions": "0600", "login_verified": False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--profile", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--origin-attributes", default="", help="Explicit Firefox container/partition; default ordinary context")
    p.add_argument("--include-session-cookies", action="store_true", help="Include only AliExpress ordinary-context cookies from Firefox's current session snapshot")
    a = p.parse_args()
    try:
        print(json.dumps(export(a.profile.resolve(), a.output, a.origin_attributes, a.include_session_cookies), ensure_ascii=False))
    except (OSError, ValueError, sqlite3.Error) as error:
        p.exit(2, f"Export failed: {error}\n")


if __name__ == "__main__":
    main()
