"""One owned headless Chromium runtime; credentials never leave private state."""
import contextlib
import datetime as dt
import fcntl
import grp
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
import uuid

ROOT = Path(__file__).resolve().parent
DRIVERS = threading.local()
SITES = {"ozon": {"domain": "ozon.ru", "url": "https://www.ozon.ru/cart", "label": "Ozon"},
         "aliexpress": {"domain": "aliexpress.ru", "url": "https://aliexpress.ru/cart", "label": "AliExpress"}}


class BrowserError(ValueError):
    def __init__(self, status):
        self.status = status
        super().__init__(status)


def timestamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def vendor_imports():
    for provider in SITES:
        path = str(ROOT / "vendors" / provider / "scripts")
        if path not in sys.path:
            sys.path.insert(0, path)


def state_root():
    root = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "marketplace-accounts"
    return private_directory(root)


def private_directory(path):
    path = Path(path).absolute()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if path.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise BrowserError("browser_directory_not_private")
    return path


def legacy_session(provider):
    if provider == "aliexpress" and os.environ.get("ALIEXPRESS_SESSION"):
        return Path(os.environ["ALIEXPRESS_SESSION"]).expanduser().absolute()
    return Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / (provider + "-bom/session.json")


def read_private(path):
    vendor_imports()
    from session_store import read_private as read
    return read(path)


def save_private(path, value):
    vendor_imports()
    from session_store import atomic_write
    atomic_write(path, value)


def in_scope(provider, host):
    domain = SITES[provider]["domain"]
    return isinstance(host, str) and (host.lstrip(".") == domain or host.endswith("." + domain))


def allowed(provider, url):
    try:
        parts = urlsplit(url)
        return (parts.scheme == "https" and in_scope(provider, parts.hostname) and
                parts.port in (None, 443) and not parts.username and not parts.password)
    except (ValueError, TypeError):
        return False


def metadata_path(provider):
    if provider not in SITES:
        raise BrowserError("invalid_provider")
    return private_directory(state_root() / provider) / "metadata.json"


def metadata(provider):
    path = metadata_path(provider)
    return read_private(path) if path.exists() else {}


def status(provider):
    info = metadata(provider)
    old = legacy_session(provider)
    return {"provider": provider, "status": "saved" if info or old.exists() else "login_required",
            "authenticated": info.get("authenticated"), "metadata_only": True,
            "last_verified_at": info.get("last_verified_at"), "last_check_status": info.get("last_check_status"),
            "browser": "chromium-headless", "login_url": SITES[provider]["url"],
            "profile_saved": bool(info.get("profile_id")), "legacy_session_available": old.exists()}


def cookies_from_document(provider, document):
    if document.get("domain_scope") != SITES[provider]["domain"]:
        raise BrowserError("session_scope_mismatch")
    cookies = []
    for row in document.get("cookies", []):
        if not in_scope(provider, row.get("host")):
            raise BrowserError("cookie_outside_scope")
        expiry = row.get("expiry")
        if expiry and expiry <= time.time():
            continue
        cookies.append({"name": row["name"], "value": row["value"], "domain": row["host"],
                        "path": row["path"], "expires": expiry or -1,
                        "secure": bool(row.get("isSecure")), "httpOnly": bool(row.get("isHttpOnly")),
                        "sameSite": row.get("sameSite", "Lax")})
    return cookies


def profile_base():
    override = os.environ.get("MARKETPLACE_BROWSER_ROOT")
    if override:
        return private_directory(Path(override).expanduser())
    # Snap Chromium cannot access arbitrary hidden home directories.
    snap = Path.home() / "snap/chromium/common"
    if snap.is_dir() and not os.environ.get("XDG_STATE_HOME"):
        return private_directory(snap / "marketplace-accounts")
    return private_directory(state_root() / "profiles")


def launch_options(executable=None, timeout_ms=30000):
    env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")
           and not k.lower().endswith("_proxy")}
    env["XDG_SESSION_TYPE"] = "x11"
    options = {"headless": True, "chromium_sandbox": True, "env": env,
               "args": ["--no-proxy-server", "--disable-blink-features=AutomationControlled", "--window-size=1440,1000"],
               "ignore_default_args": ["--enable-automation"], "locale": "ru-RU",
               "timezone_id": "Europe/Moscow", "viewport": {"width": 1440, "height": 1000},
               "accept_downloads": False, "timeout": timeout_ms}
    binary = executable or os.environ.get("MARKETPLACE_CHROMIUM_BINARY") or shutil.which("chromium") or shutil.which("chromium-browser")
    if binary:
        options["executable_path"] = str(binary)
        version = subprocess.run([str(binary), "--version"], capture_output=True, text=True, timeout=10)
        import re
        match = re.search(r"\b(\d+\.\d+\.\d+\.\d+)\b", version.stdout)
        if not match:
            raise BrowserError("browser_version_unavailable")
        options["user_agent"] = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/" +
                                 match.group(1) + " Safari/537.36")
    else:
        options["channel"] = "chromium"
    return options


def forget(provider):
    """Remove only the owned active profile/snapshot, with existing locks held."""
    vendor_imports()
    from session_store import session_lock
    session = legacy_session(provider)
    with session_lock(session):
        info = metadata(provider)
        profile = Path(info['profile_path']) if info.get('profile_path') else None
        if profile:
            import re
            if not re.fullmatch(provider + r'-[0-9a-f]{32}|profile-[0-9a-f]{16}', profile.name):
                raise BrowserError('unsupported_owned_profile')
            private_directory(profile)
            fd = os.open(str(profile) + '.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error: raise BrowserError('session_busy') from error
                shutil.rmtree(profile)
            finally: os.close(fd)
        if session.exists(): session.unlink()
        path = metadata_path(provider)
        if path.exists(): path.unlink()


def verify_groups(browser):
    vendor_imports()
    from ozon_browser import process_groups
    result = process_groups(browser)
    if result["status"] == "mismatch":
        raise BrowserError("browser_group_mismatch")
    return result


@contextlib.contextmanager
def owned_driver(factory):
    """Several provider contexts share one synchronous driver in their thread."""
    record = getattr(DRIVERS, 'record', None)
    if record is None:
        manager = factory()
        record = {'manager': manager, 'driver': manager.__enter__(), 'leases': 0}
        DRIVERS.record = record
    record['leases'] += 1
    try:
        yield record['driver']
    finally:
        record['leases'] -= 1
        if not record['leases']:
            del DRIVERS.record
            record['manager'].__exit__(None, None, None)


class ManagedBrowser:
    """Lazy context, one stable profile per provider, exclusive profile/session locks."""
    def __init__(self, provider, session=None, use_saved_session=True, executable=None, timeout_ms=30000):
        if provider not in SITES:
            raise BrowserError("invalid_provider")
        self.provider = provider
        self.session = Path(session) if session else legacy_session(provider) if use_saved_session else None
        self.canonical = use_saved_session and (session is None or Path(session).absolute() == legacy_session(provider).absolute())
        self.executable, self.timeout_ms = executable, timeout_ms
        self.stack = contextlib.ExitStack()
        self.driver = self._context = self.page = None
        self.document, self.info = {}, {}
        self.group_check = None
        self.profile = None

    def __enter__(self):
        return self

    @property
    def context(self):
        if self._context is None:
            self.open()
        return self._context

    def open(self):
        if self._context:
            return self
        vendor_imports()
        from session_store import session_lock
        try:
            from patchright.sync_api import sync_playwright
        except ImportError as error:
            raise BrowserError("browser_setup_required") from error
        try:
            if self.session:
                self.stack.enter_context(session_lock(self.session))
                if self.session.exists():
                    self.document = read_private(self.session)
            self.info = metadata(self.provider) if self.canonical else {}
            old_generation = self.document.get("session_id") or self.document.get("exported_at")
            generation = self.info.get("import_generation")
            seed = bool(self.document) and (not self.info.get("profile_id") or old_generation != generation)
            profile_id = self.info.get("profile_id")
            if not profile_id or seed and self.info.get("profile_id"):
                profile_id = uuid.uuid4().hex
            if not self.canonical:
                profile_id = "temporary-" + uuid.uuid4().hex
            profile = profile_base() / (self.provider + "-" + profile_id)
            # Adopt the previously used Ozon profile on the first migration.
            if self.canonical and not self.info and self.provider == "ozon" and self.document and self.session and not os.environ.get("XDG_STATE_HOME"):
                from ozon_display import profile_path
                profile = profile_path(self.session, self.document)
            elif self.info.get("profile_path") and not seed:
                profile = Path(self.info["profile_path"])
            private_directory(profile)
            self.profile = profile
            lock = os.open(str(profile) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            self.stack.callback(os.close, lock)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise BrowserError("session_busy") from error
            self.driver = self.stack.enter_context(owned_driver(sync_playwright))
            self._context = self.driver.chromium.launch_persistent_context(str(profile), **launch_options(self.executable, self.timeout_ms))
            self.group_check = verify_groups(self._context.browser)
            if seed:
                self._context.clear_cookies()
                self._context.add_cookies(cookies_from_document(self.provider, self.document))
                origins = {row["origin"]: row.get("localStorage", []) for row in self.document.get("browser_storage", [])
                           if allowed(self.provider, row.get("origin"))}
                if origins:
                    self._context.add_init_script("const seed=" + json.dumps(origins) + "; for(const r of seed[location.origin]||[]) localStorage.setItem(r.name,r.value);")
            elif self.document:
                # Chromium can discard session cookies on clean shutdown. Restore
                # only absent session cookies from the last synchronized snapshot.
                present = {(row['domain'], row['name'], row['path']) for row in self._context.cookies()}
                session_cookies = [row for row in cookies_from_document(self.provider, self.document)
                                   if row['expires'] == -1 and (row['domain'], row['name'], row['path']) not in present]
                if session_cookies: self._context.add_cookies(session_cookies)
            self.page = self._context.pages[0] if self._context.pages else self._context.new_page()
            self.info.update(profile_id=profile_id, profile_path=str(profile), import_generation=old_generation,
                             browser="chromium-headless", provider=self.provider)
            if self.canonical:
                save_private(metadata_path(self.provider), self.info)
            return self
        except BaseException:
            self.close(save=False)
            raise

    def observe_auth(self, authenticated, check_status=None):
        if authenticated is True or authenticated is False:
            self.info.update(authenticated=authenticated, last_verified_at=timestamp())
            self.document.update(login_verified=authenticated, last_verified_at=self.info["last_verified_at"])
        if check_status:
            self.info["last_check_status"] = check_status
        if self.canonical:
            save_private(metadata_path(self.provider), self.info)

    def save(self):
        if not self._context:
            return
        state = self._context.storage_state(indexed_db=True)
        cookies = [{"host": row["domain"], "name": row["name"], "value": row["value"], "path": row["path"],
                    "expiry": row["expires"] if row.get("expires", -1) > 0 else None,
                    "isSecure": row["secure"], "isHttpOnly": row["httpOnly"], "sameSite": row["sameSite"]}
                   for row in state["cookies"] if in_scope(self.provider, row["domain"])]
        origins = [row for row in state.get("origins", []) if allowed(self.provider, row.get("origin"))]
        self.document.update(schema_version=2, domain_scope=SITES[self.provider]["domain"], cookies=cookies,
                             browser_storage=origins, browser="chromium-headless", managed_session=True,
                             session_id=self.document.get("session_id") or str(uuid.uuid4()), updated_at=timestamp())
        self.info["import_generation"] = self.document["session_id"]
        if self.session:
            save_private(self.session, self.document)
        if self.canonical:
            save_private(metadata_path(self.provider), self.info)

    def validate_login(self, navigate=True):
        self.open()
        page = self.page
        try:
            if navigate:
                page.goto(SITES[self.provider]["url"], wait_until="domcontentloaded", timeout=self.timeout_ms)
                page.wait_for_timeout(2000)
            if not allowed(self.provider, page.url):
                raise BrowserError("outside_scope_redirect")
            if self.provider == "aliexpress":
                from ali_http import PageData, login_state, challenge
                html = page.content()
                if challenge(html):
                    raise BrowserError("browser_challenge")
                authenticated = login_state(PageData(html).aer)
            else:
                from ozon import classify
                page_status, _ = classify(200, page.content())
                if page_status != "ok":
                    raise BrowserError("browser_challenge" if page_status == "http_challenge" else page_status)
                account = page.locator('[data-widget="profileLogo"] a[href="/my/main"]')
                label = account.first.inner_text().strip() if account.count() else ""
                authenticated = True if label and label not in ("Войти", "Личный кабинет") else False if label == "Войти" else None
            result = "ok" if authenticated is True else "login_required" if authenticated is False else "login_unverified"
            self.observe_auth(authenticated, result)
            self.save()
            return {"provider": self.provider, "status": result, "authenticated": authenticated,
                    "metadata_only": False, "browser": "chromium-headless", "profile_saved": True,
                    "last_verified_at": self.info.get("last_verified_at")}
        except Exception as error:
            from ozon_browser import navigation_error
            result = getattr(error, "status", None) or navigation_error(error)["reason"]
            self.observe_auth(None, result)
            return {"provider": self.provider, "status": result, "authenticated": None,
                    "metadata_only": False, "browser": "chromium-headless", "error_type": type(error).__name__}

    def close(self, save=True):
        try:
            if self._context and save:
                self.save()
        finally:
            try:
                if self._context:
                    self._context.close()
            finally:
                self._context = None
                self.stack.close()
                if self.profile and not self.canonical:
                    shutil.rmtree(self.profile, ignore_errors=True)

    def __exit__(self, *args):
        self.close()
