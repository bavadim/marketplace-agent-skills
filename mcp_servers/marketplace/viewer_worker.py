"""One sandboxed Chromium and one private Xvnc for an enrolled Ozon user."""
import fcntl
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time

ENGINE = Path(__file__).resolve().parent / 'runtime/marketplace'
sys.path.insert(0, str(ENGINE))
from browser_runtime import ManagedBrowser, SITES


def mark(directory, name, value=''):
    temporary = directory / (name + '.new')
    temporary.write_text(value)
    os.replace(temporary, directory / name)


def run(directory, deadline):
    num = 100 + secrets.randbelow(30000)
    display = f':{num}'
    cookie = directory / 'xauth'
    cookie.touch(mode=0o600)
    auth_line = f'add {display} MIT-MAGIC-COOKIE-1 {secrets.token_hex(16)}\n'
    subprocess.run(['xauth', '-f', str(cookie)], input=auth_line, text=True, check=True, stdout=subprocess.DEVNULL)
    os.environ.update(DISPLAY=display, XAUTHORITY=str(cookie))
    rfb = directory / 'rfb'
    server = subprocess.Popen(['Xtigervnc', display, '-geometry', '880x920', '-depth', '24', '-auth', str(cookie),
                               '-nolisten', 'tcp', '-rfbport', '-1', '-rfbunixpath', str(rfb), '-rfbunixmode', '0600',
                               '-SecurityTypes', 'None', '-AcceptCutText=0', '-SendCutText=0', '-AcceptSetDesktopSize=0',
                               '-AllowOverride='], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        until = min(deadline, time.monotonic() + 8)
        while time.monotonic() < until and server.poll() is None:
            if rfb.exists() and Path(f'/tmp/.X11-unix/X{num}').is_socket():
                with socket.socket(socket.AF_UNIX) as s:
                    s.settimeout(1)
                    s.connect(str(rfb))
                    if s.recv(12).startswith(b'RFB '):
                        break
            time.sleep(.12)
        else:
            return 'viewer_failed'
        with ManagedBrowser('ozon', interactive_x11=True, timeout_ms=15000) as browser:
            browser.open()
            # Even on a navigation timeout, leave the actual page to its human operator.
            try:
                browser.page.goto(SITES['ozon']['url'], wait_until='commit', timeout=12000)
            except Exception:
                if browser.page.url in ('', 'about:blank'):
                    return 'viewer_failed'
            mark(directory, 'ready')
            while time.monotonic() < deadline and server.poll() is None:
                if browser.page.is_closed():
                    return 'viewer_failed'
                account = browser.page.locator('[data-widget="profileLogo"] a[href="/my/main"]')
                label = account.first.text_content(timeout=1500) if account.count() else ''
                requested = (directory / 'check').exists()
                if requested:
                    (directory / 'check').unlink(missing_ok=True)
                if requested or (label and label.strip() not in ('Войти', 'Личный кабинет')):
                    validate = browser.validate_login(navigate=False)
                    if validate.get('authenticated') is True:
                        mark(directory, 'sealed')  # Stop the viewer before flushing/closing Chromium.
                        break
                browser.page.wait_for_timeout(650)
            else:
                return 'authorization_expired'
        return 'connected'
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(4)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


def main():
    os.umask(0o077)
    def stop(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    directory = Path(sys.argv[1])
    deadline = time.monotonic() + min(float(sys.argv[2]), 600)
    lock = Path(os.environ['XDG_STATE_HOME']) / 'mcp.lock'
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+b') as guard:
        try:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            mark(directory, 'result', 'profile_busy')
            return
        try:
            result = run(directory, deadline)
        except Exception:
            result = 'viewer_failed'
        mark(directory, 'result', result)


if __name__ == '__main__':
    main()
