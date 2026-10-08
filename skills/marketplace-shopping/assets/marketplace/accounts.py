"""Account API orchestration, private login handoffs and detached browser workers."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from urllib.parse import urlsplit
from urllib.request import build_opener, ProxyHandler, Request
import uuid

from browser_runtime import ROOT, SITES, BrowserError, metadata, metadata_path, private_directory, read_private, save_private, state_root, status


def selected(params):
    providers = params.get("providers", list(SITES))
    if not isinstance(providers, list) or not providers or len(set(providers)) != len(providers) or any(p not in SITES for p in providers):
        raise BrowserError("invalid_providers")
    return providers


def descriptor(handoff_id):
    try:
        if str(uuid.UUID(handoff_id)) != handoff_id:
            raise ValueError
    except (ValueError, TypeError):
        raise BrowserError("invalid_handoff_id")
    return private_directory(state_root() / "login") / (handoff_id + ".json")


def runtime_python():
    from importlib.util import find_spec
    if find_spec("patchright"):
        return sys.executable
    path = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "ozon-bom/venv/bin/python"
    if not path.is_file():
        raise BrowserError("browser_setup_required")
    return str(path)


def public_handoff(doc):
    return {"status": doc["status"], "handoff_id": doc["handoff_id"], "providers": doc["providers"],
            "handoff_url": doc.get("url"), "expires_at": doc["expires_at"],
            "headless": True, "reason": doc.get("reason"), "error_type": doc.get("error_type"),
            "sessions": doc.get("sessions", []), "complete": doc.get("complete", False),
            "next_action": "Open the local link, select each marketplace and log in. Then verify each account."}


def start(params):
    providers = selected(params)
    python = runtime_python()
    directory = private_directory(state_root() / "login")
    for path in directory.glob("*.json"):
        doc = read_private(path)
        if doc.get("status") == "ready" and doc.get("deadline", 0) > time.time():
            try:
                call_handoff(doc, "status")
            except Exception:
                continue
            if set(providers) <= set(doc["providers"]):
                return public_handoff(doc)
            raise BrowserError("login_handoff_busy")
    handoff_id = str(uuid.uuid4())
    path = descriptor(handoff_id)
    ttl = params.get("ttl_seconds", 1800)
    if type(ttl) is not int or not 60 <= ttl <= 3600:
        raise BrowserError("invalid_ttl_seconds")
    from datetime import datetime, timezone
    deadline = time.time() + ttl
    save_private(path, {"handoff_id": handoff_id, "status": "starting", "providers": providers,
                        "token": secrets.token_urlsafe(32), "deadline": deadline,
                        "expires_at": datetime.fromtimestamp(deadline, timezone.utc).isoformat()})
    subprocess.Popen([python, str(ROOT / "login_server.py"), "--handoff-id", handoff_id],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, close_fds=True)
    for _ in range(100):
        doc = read_private(path)
        if doc["status"] != "starting":
            return public_handoff(doc)
        time.sleep(0.1)
    return {"status": "starting", "handoff_id": handoff_id, "providers": providers, "headless": True}


def call_handoff(doc, action):
    base = doc.get("base_url", "")
    parsed = urlsplit(base)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.username or parsed.password:
        raise BrowserError("invalid_handoff_url")
    if doc.get("deadline", 0) <= time.time():
        raise BrowserError("login_handoff_expired")
    request = Request(base + "/api/" + action, data=b"{}" if action != "status" else None,
                      headers={"Authorization": "Bearer " + doc["token"], "Origin": base, "Content-Type": "application/json"})
    with build_opener(ProxyHandler({})).open(request, timeout=120) as response:
        return json.loads(response.read(1024 * 1024))


def dispatch(method, params):
    if method == "session.login.start":
        return start(params)
    if method.startswith("session.login."):
        doc = read_private(descriptor(params["handoff_id"]))
        if doc["status"] != "ready":
            return public_handoff(doc)
        return call_handoff(doc, method.rsplit(".", 1)[1])
    if method == "session.status":
        return {"status": "ok", "sessions": [status(p) for p in selected(params)], "metadata_only": True}
    from providers.transport import run_json
    import tempfile
    with tempfile.TemporaryDirectory(prefix="account-request-") as temporary:
        request = Path(temporary) / "request.json"
        save_private(request, {"method": method, "params": params})
        return run_json(ROOT / "accounts_worker.py", ["--request", str(request)])
