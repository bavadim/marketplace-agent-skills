"""Private, reusable AliExpress HTTP sessions; cookie values are never diagnostics."""
from contextlib import contextmanager
import datetime as dt
import fcntl
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import stat
import tempfile
import time
import uuid


class SessionError(ValueError):
    def __init__(self, status):
        self.status = status
        super().__init__(status)


def default_session():
    override = os.environ.get("ALIEXPRESS_SESSION")
    if override:
        return Path(override).expanduser().absolute()
    return Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "aliexpress-bom/session.json"


def read_private(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "r", encoding="utf-8") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
            raise SessionError("session_permissions_invalid")
        return json.load(stream)


def private_parent(path):
    parent = Path(path).absolute().parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if parent.is_symlink() or parent.stat().st_mode & 0o077 or parent.stat().st_uid != os.getuid():
        raise SessionError("session_directory_not_private")


@contextmanager
def session_lock(path):
    path = Path(path)
    private_parent(path)
    fd = os.open(str(path) + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        if os.fstat(fd).st_mode & 0o077:
            raise SessionError("session_permissions_invalid")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise SessionError("session_busy") from error
        yield
    finally:
        os.close(fd)


def atomic_write(path, document):
    path = Path(path)
    private_parent(path)
    if path.is_symlink():
        raise SessionError("session_permissions_invalid")
    fd, temporary = tempfile.mkstemp(prefix=".session-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def binding(path):
    """Stable through server cookie rotation, new on an explicit browser import.

    Legacy immutable exports retain their existing journal identity. Never reuse a
    journal across a newly imported login whose account identity is not established.
    """
    document = read_private(path)
    session_id = document.get("session_id") if document.get("managed_session") is True else None
    if session_id:
        return hashlib.sha256(("managed-session:" + str(uuid.UUID(session_id))).encode()).hexdigest()
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def managed_document(document, profile=None, browser="firefox"):
    result = dict(document)
    result.update(managed_session=True, session_id=str(uuid.uuid4()),
                  login_verified=False, last_verified_at=None)
    if profile:
        result.update(browser=browser, browser_profile=str(Path(profile).resolve()))
        if browser == "firefox":
            result["firefox_profile"] = result["browser_profile"]
    return result


def cookie_rows(jar_path):
    jar = http.cookiejar.MozillaCookieJar(str(jar_path))
    jar.load(ignore_discard=True, ignore_expires=True)
    rows = []
    for cookie in jar:
        host = cookie.domain
        if not (host.lstrip(".") == "aliexpress.ru" or host.endswith(".aliexpress.ru")):
            continue
        # curl encodes session cookies as expiry=0; Python's loader leaves it 0.
        expiry = cookie.expires or None
        if expiry is not None and expiry <= time.time():
            continue
        rows.append({"name": cookie.name, "value": cookie.value, "host": host,
                     "path": cookie.path, "expiry": expiry, "isSession": expiry is None,
                     "isSecure": bool(cookie.secure), "isHttpOnly": cookie.has_nonstandard_attr("HTTPOnly") or cookie.has_nonstandard_attr("HttpOnly"),
                     "originAttributes": ""})
    return rows


class ManagedSession:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = session_lock(self.path)
        self.lock.__enter__()
        try:
            self.document = read_private(self.path)
            if self.document.get("managed_session") is not True:
                raise SessionError("not_managed_session")
            self.binding = binding(self.path)
        except BaseException:
            self.close()
            raise

    def save(self, jar_path):
        self.document["cookies"] = cookie_rows(jar_path)
        self.document["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        atomic_write(self.path, self.document)

    def auth_observed(self, state):
        self.document["login_verified"] = state is True
        self.document["last_auth_check_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        if state is True:
            self.document["last_verified_at"] = self.document["last_auth_check_at"]
        atomic_write(self.path, self.document)

    def close(self):
        if self.lock:
            self.lock.__exit__(None, None, None)
            self.lock = None
