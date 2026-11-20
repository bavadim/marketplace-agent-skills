"""Authenticated noVNC lifecycle for a single MCP server process."""
import asyncio
from dataclasses import dataclass
import hmac
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit

import anyio
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocketDisconnect

HERE = Path(__file__).resolve().parent
TOKEN = re.compile(r'[A-Za-z0-9_-]{32}')
CLAIM = '''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Вход в Ozon</title><h1>Вход в Ozon</h1><p id="hint">Введи отдельный ключ просмотра, выданный при подключении. Не пароль от Ozon.</p><form method="post"><input type="password" name="key" required maxlength="32" autocomplete="off"><button>Открыть удалённый браузер</button></form><script src="/view/claim.js" defer></script></html>'''


@dataclass
class Job:
    slot: str
    ticket: str
    deadline: float
    lease: str = ''
    directory: Path | None = None
    supervisor: asyncio.Task | None = None
    socket_attached: bool = False
    ready: bool = False


class Viewer:
    def __init__(self, grants, engine, origin, assets=Path('/usr/share/novnc'), limit=4, ttl=600):
        parts = urlsplit(origin)
        if (parts.scheme != 'https' or not parts.hostname or parts.username is not None
                or parts.path or parts.query or parts.fragment or origin != f'https://{parts.netloc}'):
            raise ValueError('viewer origin must be an HTTPS origin without a path')
        if not 1 <= limit <= 10 or not 30 <= ttl <= 600:
            raise ValueError('viewer limit must be 1..10 and TTL 30..600 seconds')
        self.origin, self.host = origin, parts.netloc
        self.grants, self.engine, self.assets = grants, engine, assets
        self.limit, self.ttl = limit, ttl
        self.lock = threading.RLock()
        self.jobs = {}
        self.by_slot = {}
        self.checking = set()
        self.failures = {}
        self.checked_until = {}

    def drop(self, job):
        with self.lock:
            self.jobs.pop(job.ticket, None)
            if self.by_slot.get(job.slot) is job:
                self.by_slot.pop(job.slot)

    def busy(self, slot):
        with self.lock:
            job = self.by_slot.get(slot)
            return slot in self.checking or bool(job and job.lease)

    def authorize(self, slot):
        with self.lock:
            now = time.monotonic()
            self.checked_until = {k: v for k, v in self.checked_until.items() if v > now}
            self.checked_until.pop(slot, None)
            for job in list(self.jobs.values()):
                if not job.lease and job.deadline <= time.monotonic():
                    self.drop(job)
            if job := self.by_slot.get(slot):
                if job.deadline > time.monotonic() and self.available(job):
                    return self.link(job)
                return {'ok': False, 'status': 'authorization_in_progress'}
            if slot in self.checking:
                return {'ok': False, 'status': 'authorization_in_progress'}
            self.checking.add(slot)
        try:
            status = self.engine.call(slot, 'session.status', {'providers': ['ozon']})
            if not status.get('ok'):
                return {'ok': False, 'status': status.get('status', 'session_unverified')}
            rows = status.get('data', {}).get('sessions', [])
            if any(r.get('profile_saved') for r in rows):
                live = self.engine.call(slot, 'session.validate', {'providers': ['ozon']}, admin=True)
                rows = live.get('data', {}).get('sessions', [])
                if any(r.get('provider') == 'ozon' and r.get('authenticated') is True for r in rows):
                    with self.lock:
                        self.failures.pop(slot, None)
                        self.checked_until[slot] = time.monotonic() + 600
                    return {'ok': True, 'status': 'success'}
                if live.get('status') in ('session_busy', 'service_busy', 'worker_timeout', 'invalid_worker_response', 'browser_setup_required'):
                    return {'ok': False, 'status': live['status']}
            with self.lock:
                if len(self.jobs) >= 64:
                    return {'ok': False, 'status': 'viewer_busy'}
                job = Job(slot, secrets.token_urlsafe(24), time.monotonic() + self.ttl)
                self.jobs[job.ticket] = self.by_slot[slot] = job
                return self.link(job)
        finally:
            with self.lock:
                self.checking.discard(slot)

    def checked(self, slot):
        with self.lock:
            return self.checked_until.get(slot, 0) > time.monotonic()

    def link(self, job):
        result = {'ok': False, 'status': 'authorization_required', 'authorization_url': f'{self.origin}/view/{job.ticket}/', 'expires_in': int(max(0, job.deadline - time.monotonic()))}
        if previous := self.failures.get(job.slot):
            result['previous_attempt'] = previous
        return result

    def available(self, job):
        return (time.monotonic() < job.deadline and self.grants.active(job.slot)
                and not (job.directory and any((job.directory / name).exists() for name in ('sealed', 'result'))))

    def get(self, channel):
        if channel.headers.getlist('host') != [self.host]:
            return None
        ticket = channel.path_params['ticket']
        if not TOKEN.fullmatch(ticket):
            return None
        with self.lock:
            job = self.jobs.get(ticket)
            return job if job and self.available(job) else None

    def owns(self, channel, job):
        value = channel.cookies.get('market_view', '')
        return bool(job and job.lease and TOKEN.fullmatch(value) and hmac.compare_digest(job.lease, value))

    async def page(self, request: Request):
        job = self.get(request)
        if not job:
            return HTMLResponse('Ссылка истекла или вход завершён. Вернись к агенту.', status_code=410)
        if self.owns(request, job):
            return RedirectResponse('screen')
        if request.method == 'GET':
            if job.lease:
                return HTMLResponse('Ссылка уже использована.', status_code=410)
            return HTMLResponse(CLAIM)
        if request.headers.getlist('origin') != [self.origin]:
            return HTMLResponse('Forbidden', status_code=403)
        if request.headers.get('content-type', '').partition(';')[0] != 'application/x-www-form-urlencoded':
            return HTMLResponse('Unsupported form', status_code=415)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 512:
                return HTMLResponse('Request too large', status_code=413)
        key = parse_qs(body.decode('ascii', 'replace')).get('key', [''])[0]
        if not TOKEN.fullmatch(key):
            return HTMLResponse('Ключ просмотра не подходит.', status_code=403)
        with self.lock:
            if job.lease or not self.available(job):
                return HTMLResponse('Ссылка уже использована или истекла.', status_code=410)
            if sum(bool(x.lease) for x in self.jobs.values()) >= self.limit:
                return HTMLResponse('Все экраны заняты. Вернись назад и повтори через минуту.', status_code=503)
            if not (self.grants.verify_viewer(job.slot, key) or self.grants.use_viewer_invite(job.slot, job.ticket, key)):
                return HTMLResponse('Ключ просмотра не подходит.', status_code=403)
            job.lease = secrets.token_urlsafe(24)
            job.supervisor = asyncio.create_task(self.run(job))
        response = RedirectResponse('screen', status_code=303)
        response.set_cookie('market_view', job.lease, path=f'/view/{job.ticket}/', secure=True,
                            httponly=True, samesite='strict', max_age=int(max(1, job.deadline-time.monotonic())))
        return response

    async def screen(self, request: Request):
        return FileResponse(HERE / 'view.html') if self.owns(request, self.get(request)) else HTMLResponse('Вход завершён или ссылка недействительна.', status_code=410)

    async def status(self, request: Request):
        job = self.get(request)
        if not self.owns(request, job):
            return JSONResponse({'status': 'closed'}, status_code=410)
        return JSONResponse({'status': 'ready' if job and job.ready else 'starting'})

    async def check(self, request: Request):
        job = self.get(request)
        if request.headers.getlist('origin') != [self.origin] or not self.owns(request, job) or not job or not job.ready or not job.directory:
            return JSONResponse({'status': 'closed'}, status_code=410)
        (job.directory / 'check').touch(exist_ok=True)
        return JSONResponse({'status': 'checking'})

    async def run(self, job):
        proc = None
        result = 'viewer_failed'
        try:
            env = self.engine.environment(job.slot)
            # A short, private pathname is required for the RFB Unix socket.
            base = self.engine.root / 'v'
            base.mkdir(mode=0o700, exist_ok=True)
            job.directory = base / job.ticket
            job.directory.mkdir(mode=0o700)

            proc = await asyncio.create_subprocess_exec(sys.executable, str(HERE / 'viewer_worker.py'),
                str(job.directory), str(max(0, job.deadline-time.monotonic())), env=env, cwd=env['HOME'],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            while proc.returncode is None and self.active_job(job):
                if (job.directory / 'rfb').exists() and (job.directory / 'ready').exists():
                    job.ready = True
                await asyncio.sleep(.2)
            marker = job.directory / 'result'
            if marker.is_file():
                result = marker.read_text().strip()
                await asyncio.wait_for(proc.wait(), 15)
            elif not self.active_job(job):
                result = 'authorization_expired'
        except (OSError, ValueError, asyncio.TimeoutError):
            result = 'viewer_failed'
        finally:
            if proc and proc.returncode is None:
                try:
                    os.killpg(proc.pid, 15)
                    await asyncio.wait_for(proc.wait(), 8)
                except asyncio.TimeoutError:
                    try:
                        os.killpg(proc.pid, 9)
                    except ProcessLookupError:
                        pass
                    await proc.wait()
                except ProcessLookupError:
                    await proc.wait()
            if job.directory:
                shutil.rmtree(job.directory, ignore_errors=True)
            with self.lock:
                if result != 'connected':
                    self.failures.pop(job.slot, None)
                    self.failures[job.slot] = result if result in ('authorization_expired', 'profile_busy', 'viewer_failed') else 'viewer_failed'
                    if len(self.failures) > 256:
                        self.failures.pop(next(iter(self.failures)))
                else:
                    self.failures.pop(job.slot, None)
                self.drop(job)

    def active_job(self, job):
        return job.deadline > time.monotonic() and self.grants.active(job.slot)

    async def websocket(self, ws):
        job = self.get(ws)
        if ws.headers.getlist('origin') != [self.origin] or not self.owns(ws, job) or not job or not job.ready or not job.directory:
            await ws.close(code=1008)
            return
        with self.lock:
            occupied = job.socket_attached
            if not occupied:
                job.socket_attached = True
        if occupied:
            await ws.close(code=1008)
            return
        writer = None
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(job.directory / 'rfb'), 2)
            await ws.accept(subprotocol='binary' if 'binary' in ws.scope.get('subprotocols', ()) else None)
            async with anyio.create_task_group() as group:
                async def receive():
                    try:
                        while self.available(job):
                            payload = await ws.receive_bytes()
                            if not self.available(job):
                                break
                            writer.write(payload)
                            await writer.drain()
                    except (WebSocketDisconnect, OSError, KeyError):
                        pass
                    finally:
                        group.cancel_scope.cancel()
                async def send():
                    try:
                        while self.available(job) and (chunk := await reader.read(65536)):
                            if not self.available(job):
                                break
                            await ws.send_bytes(chunk)
                    except (WebSocketDisconnect, OSError):
                        pass
                    finally:
                        group.cancel_scope.cancel()
                async def revoke():
                    while self.available(job):
                        await anyio.sleep(.2)
                    group.cancel_scope.cancel()
                group.start_soon(receive)
                group.start_soon(send)
                group.start_soon(revoke)
        except (OSError, asyncio.TimeoutError, WebSocketDisconnect):
            pass
        finally:
            if writer:
                writer.close()
            with self.lock:
                job.socket_attached = False
            try:
                await ws.close()
            except (RuntimeError, WebSocketDisconnect, OSError):
                pass

    def wrap(self, private):
        routes = [Route('/view/claim.js', lambda req: FileResponse(HERE/'view-claim.js', media_type='text/javascript')),
                  Route('/view/{ticket}/', self.page, methods=['GET', 'POST']),
                  Route('/view/{ticket}/screen', self.screen), Route('/view/{ticket}/status', self.status),
                  Route('/view/{ticket}/check', self.check, methods=['POST']),
                  WebSocketRoute('/view/{ticket}/ws', self.websocket),
                  Route('/view-client.js', lambda req: FileResponse(HERE/'view-client.js', media_type='text/javascript'))]
        for path in ('core', 'vendor'):
            directory = self.assets / path
            if directory.is_dir():
                routes.append(Mount('/novnc/' + path, app=StaticFiles(directory=directory)))
        public = Starlette(routes=routes)

        async def app(scope, receive, send):
            if not scope.get('path', '').startswith(('/view/', '/novnc/', '/view-client.js')):
                return await private(scope, receive, send)
            async def secure(message):
                if message['type'] == 'http.response.start':
                    headers = [(b'cache-control', b'no-store'), (b'referrer-policy', b'no-referrer'),
                               (b'x-content-type-options', b'nosniff'), (b'x-frame-options', b'DENY'),
                               (b'content-security-policy', f"default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'self' wss://{self.host}; form-action 'self'; frame-ancestors 'none'".encode())]
                    message = {**message, 'headers': [*message.get('headers', []), *headers]}
                await send(message)
            await public(scope, receive, secure)
        return app
