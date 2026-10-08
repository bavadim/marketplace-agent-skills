#!/usr/bin/env python3
"""Check, refresh and retain an authorized AliExpress browser session.

No passwords, checkout, or cart writes. A new import replaces saved credentials only
after the account cart is read successfully. Output never includes cookie values.
"""
import argparse
import configparser
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

from ali_cart import CART_URL, CartAPI, save_json
from ali_http import Client, ReadFailure
from firefox_session import export
from session_store import (SessionError, atomic_write, default_session, managed_document,
                           private_parent, read_private, session_lock)


def profiles(browser="firefox"):
    if browser == "chromium":
        roots = [Path.home() / ".config/chromium", Path.home() / "snap/chromium/common/chromium"]
        found = []
        for root in roots:
            state = root / "Local State"
            if not state.is_file():
                continue
            info = json.loads(state.read_text()).get("profile", {}).get("info_cache", {})
            for name in info or ["Default"]:
                if Path(name).name != name:
                    continue
                path = root / name
                if ((path / "Cookies").is_file() or (path / "Network/Cookies").is_file()) and path.resolve() not in found:
                    found.append(path.resolve())
        return found
    roots = [Path.home() / ".mozilla/firefox", Path.home() / "snap/firefox/common/.mozilla/firefox",
             Path.home() / ".var/app/org.mozilla.firefox/.mozilla/firefox"]
    found = []
    for root in roots:
        config = configparser.ConfigParser()
        config.read(root / "profiles.ini")
        for section in config.sections():
            if not section.startswith("Profile") or not config.has_option(section, "Path"):
                continue
            value = config[section]
            path = root / value["Path"] if value.get("IsRelative") == "1" else Path(value["Path"])
            if (path / "cookies.sqlite").is_file() and path.resolve() not in found:
                found.append(path.resolve())
    return found


def check(session):
    if not Path(session).is_file():
        return {"status": "authentication_required", "authenticated": False}
    browser = read_private(session).get("browser", "firefox")
    try:
        with Client(session) as client:
            cart = CartAPI(client).read()
        return {"status": "ok", "authenticated": True, "cart_count": cart["count"],
                "cart_complete": cart["complete"], "cart_fingerprint": cart["fingerprint"],
                "session": str(Path(session).absolute()), "cart_url": CART_URL, "browser": browser}
    except ReadFailure as error:
        return {"status": error.status, "authenticated": False, "browser": browser}


def user_action(result, profile=None, browser=None):
    result = dict(result)
    browser = browser or result.get("browser", "firefox")
    label = "Chromium" if browser == "chromium" else "Firefox"
    result.update(login_url=CART_URL, browser=browser, cart_modified=False)
    if profile:
        result["profile"] = str(profile)
    if result["status"] in ("authentication_required", "requires_browser_verification"):
        result["next_action"] = "Log in and open the cart in this " + label + " profile, then run ali_session.py refresh --browser " + browser + ". Do not send passwords or cookies in chat."
    return result


def refresh(session, profile, browser="firefox"):
    session = Path(session)
    private_parent(session)
    with session_lock(session):
        with tempfile.TemporaryDirectory(prefix=".import-", dir=session.parent) as temp:
            candidate = Path(temp) / "session.json"
            if browser == "chromium":
                from chromium_session import export as chromium_export
                exported = chromium_export(profile, candidate)
            else:
                exported = export(profile, candidate, include_session_cookies=True)
            atomic_write(candidate, managed_document(read_private(candidate), profile, browser))
            result = check(candidate)
            if result["status"] != "ok":
                result.update(saved_session_replaced=False, imported_cookie_count=exported["cookie_count"],
                              imported_session_cookie_count=exported["session_cookie_count"])
                return user_action(result, profile, browser)
            atomic_write(session, read_private(candidate))
    result.update(session=str(session.absolute()), saved_session_replaced=True, reused_saved_session=False,
                  imported_cookie_count=exported["cookie_count"], imported_session_cookie_count=exported["session_cookie_count"],
                  cart_modified=False, browser=browser)
    return result


def ensure(session, profile=None, force_refresh=False, browser=None):
    saved = read_private(session) if Path(session).is_file() else {}
    browser = browser or saved.get("browser", "firefox")
    if not force_refresh:
        result = check(session)
        if result["status"] == "ok":
            result.update(reused_saved_session=True, cart_modified=False)
            return result
        if result["status"] != "authentication_required":
            return user_action(result, profile, browser)
    if profile is None and Path(session).is_file():
        saved_profile = (saved.get("browser_profile", saved.get("firefox_profile"))
                         if saved.get("browser", "firefox") == browser else None)
        profile = Path(saved_profile) if saved_profile else None
    if profile is None:
        choices = profiles(browser)
        if len(choices) != 1:
            return {"status": "browser_profile_required", "browser": browser, "profiles": [str(p) for p in choices],
                    "authenticated": False, "cart_modified": False}
        profile = choices[0]
    return refresh(session, Path(profile).resolve(), browser)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("ensure", "refresh", "check"))
    parser.add_argument("--session", type=Path, default=default_session())
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--browser", choices=("firefox", "chromium"), help="Import source; defaults to saved browser or Firefox")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = (user_action(check(args.session), args.profile, args.browser) if args.operation == "check" else
                  ensure(args.session, args.profile, args.operation == "refresh", args.browser))
    except (SessionError, ReadFailure) as error:
        result = {"status": error.status, "authenticated": False, "cart_modified": False}
    except (OSError, ValueError, ImportError, sqlite3.Error) as error:
        result = {"status": "session_import_failed", "error_type": type(error).__name__,
                  "authenticated": False, "cart_modified": False}
    save_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "ok" else 2


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from launcher import launch
    launch()
    raise SystemExit(main())
