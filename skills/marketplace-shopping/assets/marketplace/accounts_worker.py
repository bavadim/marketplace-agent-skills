"""Isolated headless account checks; inherits the entrypoint environment."""
import argparse
import os
from pathlib import Path
import sys

from accounts import runtime_python, selected
from browser_runtime import ManagedBrowser, BrowserError, ROOT, legacy_session, metadata_path, private_directory, read_private, save_private, state_root


def prepare_process():
    script = str(Path(sys.argv[0]).resolve())
    python = runtime_python()
    if python != sys.executable:
        os.execv(python, [python, script, *sys.argv[1:]])


def execute(method, params):
    sessions = []
    for provider in selected(params):
        try:
            if method == "session.forget":
                from browser_runtime import forget
                forget(provider)
                sessions.append({"provider": provider, "status": "forgotten", "authenticated": False,
                                 "server_session_revoked": False})
            else:
                with ManagedBrowser(provider) as browser:
                    sessions.append(browser.validate_login())
        except Exception as error:
            sessions.append({"provider": provider, "status": getattr(error, "status", "browser_error"),
                             "authenticated": None, "error_type": type(error).__name__})
    good = [s for s in sessions if s["status"] in ("ok", "forgotten")]
    return {"status": "ok" if len(good) == len(sessions) else "partial" if good else
            "login_required" if all(s["status"] == "login_required" for s in sessions) else "accounts_unavailable",
            "sessions": sessions, "complete": len(good) == len(sessions), "headless": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        request = read_private(args.request)
        if request['method'] != 'session.forget': prepare_process()
        result = execute(request["method"], request["params"])
    except Exception as error:
        result = {"status": getattr(error, "status", "browser_error"), "error_type": type(error).__name__, "complete": False}
    save_private(args.output, result)


if __name__ == '__main__':
    from launcher import launch
    launch()
    main()
