"""One sandboxed Chromium and one private Xvnc for an enrolled Ozon user."""
import fcntl
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time

ENGINE = Path(__file__).resolve().parent / 'runtime/marketplace'
sys.path.insert(0, str(ENGINE))
from browser_runtime import SITES, metadata, metadata_path, private_directory, profile_base, save_private


def mark(directory, name, value=''):
    temporary = directory / (name + '.new')
    temporary.write_text(value)
    os.replace(temporary, directory / name)


def stop_browser(browser):
    if browser.poll() is None:
        # Chromium treats SIGTERM as desktop session shutdown, not normal browser exit.
        browser.send_signal(signal.SIGINT)
        try:
            browser.wait(5)
        except subprocess.TimeoutExpired:
            browser.kill()
            browser.wait()
            return False
    return browser.returncode == 0


def manual_chromium(directory, deadline, display_server, url):
    info = metadata('ozon')
    saved_id = info.get('profile_id') or ''
    profile_id = saved_id if re.fullmatch(r'[a-f0-9]{32}', saved_id) else secrets.token_hex(16)
    profile = private_directory(profile_base() / ('ozon-' + profile_id))
    fd = os.open(str(profile) + '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+b') as guard:
        try:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 'profile_busy'
        binary = os.environ.get('MARKETPLACE_CHROMIUM_BINARY') or shutil.which('chromium')
        if not binary:
            return 'viewer_failed'
        browser = subprocess.Popen([binary, '--ozone-platform=x11', '--disable-gpu', '--no-proxy-server',
            '--no-first-run', '--no-default-browser-check', '--window-position=0,0', '--window-size=880,920',
            '--lang=ru-RU', '--password-store=basic', '--restore-last-session', f'--user-data-dir={profile}', url],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        committed = False
        try:
            mark(directory, 'ready')
            while time.monotonic() < deadline and display_server.poll() is None:
                if (directory / 'check').exists():
                    mark(directory, 'sealed')
                    if not stop_browser(browser):
                        return 'viewer_failed'
                    save_private(metadata_path('ozon'), {**info, 'profile_id': profile_id, 'profile_path': str(profile),
                        'browser': 'chromium-desktop', 'manual_handoff': True, 'authenticated': None,
                        'last_verified_at': None, 'last_check_status': 'manual_handoff'})
                    committed = True
                    return 'handed_off'
                if browser.poll() is not None:
                    return 'viewer_failed'
                time.sleep(.2)
            return 'authorization_expired'
        finally:
            stop_browser(browser)
            if not committed and profile_id != saved_id:
                shutil.rmtree(profile, ignore_errors=True)


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
        return manual_chromium(directory, deadline, server, SITES['ozon']['url'])
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
