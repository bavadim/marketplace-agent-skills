"""Private, read-only marketplace MCP. Run the admin CLI only on the service host."""
import argparse
from contextvars import ContextVar
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
from threading import BoundedSemaphore
import time
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field
from starlette.responses import JSONResponse

REPO = Path(__file__).resolve().parents[1]
SHOP = REPO / 'skills/marketplace-shopping/scripts/shop.py'
ACCOUNTS = REPO / 'skills/marketplace-accounts/scripts/accounts.py'
READ = {'search': SHOP, 'product': SHOP, 'quote': SHOP, 'session.status': ACCOUNTS}
ARGUMENTS = {'search': {'query', 'providers', 'limit'}, 'product': {'ref', 'variant_limit'},
             'quote': {'ref', 'city', 'quantity'}, 'session_status': {'providers'}, 'authorize': set()}
ADMIN = {'session.login.start', 'session.login.status', 'session.login.finish',
         'session.login.cancel', 'session.validate', 'session.forget'}
SLOT = re.compile(r'[a-f0-9]{32}')
BEARER = re.compile(r'mk_[A-Za-z0-9_-]{43}')
REF = r'^(ozon:[0-9]{1,30}|aliexpress:[0-9]{1,30}(:[0-9]{1,30})?)$'
Provider = Literal['ozon', 'aliexpress']
Principal = ContextVar('marketplace_principal', default=None)


def private_dir(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError('data directories must be private and owned by the service user')
    return path


class Grants:
    """Each grant owns a distinct browser slot, even for the same human on two clients."""

    def __init__(self, root):
        self.db = private_dir(root) / 'grants.sqlite3'
        fd = os.open(self.db, os.O_CREAT | os.O_NOFOLLOW | os.O_RDWR, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise RuntimeError('grant database must be a private regular file')
        finally:
            os.close(fd)
        self.sql('''CREATE TABLE IF NOT EXISTS grants (
          slot TEXT PRIMARY KEY, label TEXT NOT NULL, digest TEXT UNIQUE NOT NULL,
          expires_at INTEGER NOT NULL, revoked INTEGER NOT NULL DEFAULT 0)''')
        self.sql('CREATE TABLE IF NOT EXISTS viewer_keys (slot TEXT PRIMARY KEY, digest TEXT NOT NULL)')
        self.sql('''CREATE TABLE IF NOT EXISTS viewer_invites (
                    ticket TEXT PRIMARY KEY, slot TEXT NOT NULL, digest TEXT NOT NULL, expires_at INTEGER NOT NULL)''')

    def sql(self, statement, bindings=()):
        conn = sqlite3.connect(self.db, timeout=5)
        try:
            with conn:
                return conn.execute(statement, bindings).fetchone()
        finally:
            conn.close()

    def issue(self, label, expires_at=None):
        slot, key = secrets.token_hex(16), 'mk_' + secrets.token_urlsafe(32)
        expires_at = int(time.time()) + 30 * 86400 if expires_at is None else expires_at
        self.sql('INSERT INTO grants (slot,label,digest,expires_at) VALUES (?,?,?,?)',
                 (slot, label, hashlib.sha256(key.encode()).hexdigest(), expires_at))
        return slot, key

    def lookup(self, key):
        if not BEARER.fullmatch(key):
            return None
        row = self.sql('SELECT slot FROM grants WHERE digest=? AND revoked=0 AND expires_at>?',
                       (hashlib.sha256(key.encode()).hexdigest(), int(time.time())))
        return row[0] if row else None

    def active(self, slot):
        return self.sql('SELECT 1 FROM grants WHERE slot=? AND revoked=0 AND expires_at>?',
                        (slot, int(time.time()))) is not None

    def revoke(self, slot):
        return self.sql('UPDATE grants SET revoked=1 WHERE slot=? AND revoked=0 RETURNING slot',
                        (slot,)) is not None

    def set_viewer_key(self, slot):
        if not self.active(slot):
            raise ValueError('inactive grant')
        key = secrets.token_urlsafe(24)
        self.sql('INSERT OR REPLACE INTO viewer_keys VALUES (?,?)',
                 (slot, hashlib.sha256(key.encode()).hexdigest()))
        return key

    def verify_viewer(self, slot, key):
        row = self.sql('''SELECT v.digest FROM viewer_keys v JOIN grants g USING(slot)
                          WHERE slot=? AND revoked=0 AND expires_at>?''', (slot, int(time.time())))
        return bool(row and hmac.compare_digest(row[0], hashlib.sha256(key.encode()).hexdigest()))

    def make_viewer_invite(self, slot, ticket):
        if not self.active(slot) or not re.fullmatch(r'[A-Za-z0-9_-]{32}', ticket):
            raise ValueError('inactive grant or invalid ticket')
        key = secrets.token_urlsafe(24)
        now = int(time.time())
        self.sql('DELETE FROM viewer_invites WHERE expires_at <= ?', (now,))
        self.sql('INSERT OR REPLACE INTO viewer_invites VALUES (?,?,?,?)',
                 (ticket, slot, hashlib.sha256(key.encode()).hexdigest(), now + 600))
        return key

    def use_viewer_invite(self, slot, ticket, key):
        return self.sql('''DELETE FROM viewer_invites WHERE ticket=? AND slot=? AND digest=? AND expires_at>?
                         AND EXISTS (SELECT 1 FROM grants WHERE slot=? AND revoked=0 AND expires_at>?) RETURNING ticket''',
                        (ticket, slot, hashlib.sha256(key.encode()).hexdigest(), int(time.time()),
                         slot, int(time.time()))) is not None


class Engine:
    def __init__(self, root, scripts=None):
        self.root = private_dir(root)
        self.scripts = READ if scripts is None else scripts
        self.capacity = BoundedSemaphore(4)

    def environment(self, slot):
        if not SLOT.fullmatch(slot):
            raise ValueError('invalid slot')
        base = private_dir(private_dir(self.root / 'slots') / slot)
        env = {k: os.environ[k] for k in ('PATH', 'LANG', 'LC_ALL', 'PLAYWRIGHT_BROWSERS_PATH',
               'MARKETPLACE_CHROMIUM_BINARY') if k in os.environ}
        for var, directory in {'HOME': 'home', 'XDG_STATE_HOME': 'state', 'XDG_DATA_HOME': 'data',
                               'XDG_CACHE_HOME': 'cache', 'XDG_CONFIG_HOME': 'config', 'TMPDIR': 'tmp',
                               'MARKETPLACE_BROWSER_ROOT': 'profiles'}.items():
            env[var] = str(private_dir(base / directory))
        env.update(PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1')
        return env

    def call(self, slot, method, params, *, admin=False):
        script = ACCOUNTS if admin and method in ADMIN else self.scripts[method]
        failure = lambda status: {'schema_version': 2, 'method': method, 'ok': False, 'status': status, 'data': {}}
        if not self.capacity.acquire(blocking=False):
            return failure('service_busy')
        try:
            env = self.environment(slot)
            lock = Path(env['XDG_STATE_HOME']) / 'mcp.lock'
            fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'r+b') as guard:
                try:
                    fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return failure('session_busy')
                return self.run_worker(script, env, method, params, failure)
        finally:
            self.capacity.release()

    @staticmethod
    def run_worker(script, env, method, params, failure):
        with tempfile.TemporaryFile(dir=env['TMPDIR']) as output:
            with subprocess.Popen([sys.executable, str(script), 'call', '--request', '-'],
                                  stdin=subprocess.PIPE, stdout=output, stderr=subprocess.DEVNULL,
                                  env=env, cwd=env['HOME'], start_new_session=True, umask=0o077) as proc:
                try:
                    proc.communicate(json.dumps({'method': method, 'params': params}).encode(), timeout=85)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait()
                    return failure('worker_timeout')
            output.seek(0)
            try:
                raw = output.read(2_000_001)
                if len(raw) > 2_000_000:
                    return failure('worker_response_too_large')
                result = json.loads(raw)
                if not isinstance(result, dict) or result.get('method') != method or result.get('schema_version') != 2:
                    return failure('invalid_worker_response')
                return result
            except (UnicodeError, ValueError):
                return failure('invalid_worker_response')


class PrivateTransport:
    def __init__(self, app, grants, hosts):
        self.app, self.grants, self.hosts = app, grants, {host.encode('ascii') for host in hosts}

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = scope.get('headers', [])
        hosts = [value for name, value in headers if name == b'host']
        authorization = [value for name, value in headers if name == b'authorization']
        if len(hosts) != 1 or hosts[0] not in self.hosts:
            return await JSONResponse({'error': 'invalid_host'}, 421)(scope, receive, send)
        if any(name == b'origin' for name, _ in headers):
            return await JSONResponse({'error': 'browser_origin_not_allowed'}, 403)(scope, receive, send)
        key = authorization[0][7:].decode('ascii', errors='replace') if (
            len(authorization) == 1 and authorization[0].startswith(b'Bearer ')) else ''
        slot = self.grants.lookup(key)
        if not slot:
            return await JSONResponse({'error': 'unauthorized'}, 401, {'WWW-Authenticate': 'Bearer',
                'Cache-Control': 'no-store'})(scope, receive, send)
        token = Principal.set(slot)
        async def no_store(message):
            if message['type'] == 'http.response.start':
                message = {**message, 'headers': [*message.get('headers', []), (b'cache-control', b'no-store')]}
            await send(message)
        try:
            await self.app(scope, receive, no_store)
        finally:
            Principal.reset(token)


class CatalogServer(MCPServer):
    async def call_tool(self, name, arguments, context=None):
        if name not in ARGUMENTS or arguments.keys() - ARGUMENTS[name]:
            return CallToolResult(isError=True, content=[TextContent(type='text', text='Unknown tool or argument')])
        return await super().call_tool(name, arguments, context)


def create_app(grants, engine, hosts, viewer=None):
    server = CatalogServer('marketplace-catalog', log_level='CRITICAL')
    read_only = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)

    def call(method, params):
        slot = Principal.get()
        if not slot or not grants.active(slot):
            raise PermissionError('access required')
        if viewer and method != 'session.status' and viewer.busy(slot):
            return {'ok': False, 'status': 'authorization_in_progress'}
        params = {name: value for name, value in params.items() if value is not None}
        ozon = ((method == 'search' and 'ozon' in params.get('providers', ('ozon', 'aliexpress')))
                or (method in ('product', 'quote') and params['ref'].startswith('ozon:')))
        if viewer and ozon and not viewer.checked(slot):
            return {'ok': False, 'status': 'authorization_check_required', 'next_tool': 'authorize'}
        result = engine.call(slot, method, params)
        if method == 'session.status':
            rows = result.get('data', {}).get('sessions', [])
            result['data'] = {'sessions': [{k: row[k] for k in ('provider', 'status', 'authenticated',
                'metadata_only', 'last_verified_at', 'last_check_status') if k in row} for row in rows]}
        return result

    @server.tool(annotations=read_only, structured_output=True)
    def search(query: Annotated[str, Field(min_length=1, max_length=256)],
               providers: Annotated[list[Provider], Field(min_length=1, max_length=2)] | None = None,
               limit: Annotated[int, Field(ge=1, le=30)] = 12) -> dict[str, Any]:
        """Search the catalog. Prices are preliminary; check each provider's status."""
        return call('search', {'query': query, 'providers': providers, 'limit': limit})

    @server.tool(annotations=read_only, structured_output=True)
    def product(ref: Annotated[str, Field(pattern=REF)],
                variant_limit: Annotated[int, Field(ge=0, le=8)] = 0) -> dict[str, Any]:
        """Read a product or its variants. Use a marketplace ref from search, not a URL."""
        return call('product', {'ref': ref, 'variant_limit': variant_limit})

    @server.tool(annotations=read_only, structured_output=True)
    def quote(ref: Annotated[str, Field(pattern=REF)],
              city: Annotated[str, Field(min_length=1, max_length=120)],
              quantity: Annotated[int, Field(ge=1, le=100)] = 1) -> dict[str, Any]:
        """Read available price and delivery evidence without modifying a cart."""
        return call('quote', {'ref': ref, 'city': city, 'quantity': quantity})

    @server.tool(annotations=read_only, structured_output=True)
    def session_status(providers: Annotated[list[Provider], Field(min_length=1, max_length=2)] | None = None) -> dict[str, Any]:
        """Read saved connection metadata for this credential only; not a live login check."""
        return call('session.status', {'providers': providers})

    if viewer:
        @server.tool(structured_output=True)
        def authorize() -> dict[str, Any]:
            """Check Ozon access first. If the user must act, return a private viewer link."""
            slot = Principal.get()
            if not slot or not grants.active(slot):
                raise PermissionError('access required')
            return viewer.authorize(slot)

    app = server.streamable_http_app(stateless_http=True, json_response=True, max_request_body_size=32_768,
              transport_security=TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=[]))
    private = PrivateTransport(app, grants, hosts)
    return viewer.wrap(private) if viewer else private


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='action', required=True)
    subs.add_parser('serve')
    issue = subs.add_parser('issue'); issue.add_argument('label'); issue.add_argument('--days', type=int, default=30, choices=range(1, 91), metavar='1..90')
    revoke = subs.add_parser('revoke'); revoke.add_argument('slot')
    viewer_key = subs.add_parser('viewer-key'); viewer_key.add_argument('slot')
    invite = subs.add_parser('viewer-invite'); invite.add_argument('slot'); invite.add_argument('ticket')
    account = subs.add_parser('account'); account.add_argument('slot'); account.add_argument('operation', choices=sorted(ADMIN)); account.add_argument('--provider', choices=['ozon', 'aliexpress'], default='ozon'); account.add_argument('--handoff-id')
    args = parser.parse_args()
    root = Path(os.environ.get('MARKET_MCP_DATA', '/data'))
    if not root.is_absolute():
        parser.error('MARKET_MCP_DATA must be absolute')
    grants, engine = Grants(root), Engine(root)
    if args.action == 'serve':
        import uvicorn
        from viewer import Viewer
        hosts = os.environ.get('MARKET_MCP_HOSTS', '127.0.0.1:8000,localhost:8000').split(',')
        origin = os.environ.get('MARKET_MCP_VIEWER_ORIGIN')
        viewer = Viewer(grants, engine, origin, limit=int(os.environ.get('MARKET_MCP_VIEWER_LIMIT', '4'))) if origin else None
        uvicorn.run(create_app(grants, engine, hosts, viewer), host=os.environ.get('MARKET_MCP_BIND', '127.0.0.1'),
                    port=8000, workers=1, access_log=False, proxy_headers=False, ws_max_size=65536)
    elif args.action == 'issue':
        slot, key = grants.issue(args.label, int(time.time()) + args.days * 86400)
        print(json.dumps({'slot': slot, 'bearer': key}))
    elif args.action == 'revoke':
        if not grants.revoke(args.slot):
            parser.error('grant not found or already revoked')
        print(json.dumps({'revoked': True}))
    elif args.action == 'viewer-key':
        if not SLOT.fullmatch(args.slot) or not grants.active(args.slot):
            parser.error('unknown or inactive grant')
        print(json.dumps({'viewer_key': grants.set_viewer_key(args.slot)}))
    elif args.action == 'viewer-invite':
        try:
            print(json.dumps({'invite': grants.make_viewer_invite(args.slot, args.ticket)}))
        except ValueError as exc:
            parser.error(str(exc))
    else:
        if not SLOT.fullmatch(args.slot) or (not grants.active(args.slot) and args.operation not in {'session.forget', 'session.login.cancel'}):
            parser.error('unknown or inactive grant')
        if args.operation in {'session.login.status', 'session.login.finish', 'session.login.cancel'}:
            if not args.handoff_id:
                parser.error('--handoff-id is required')
            params = {'handoff_id': args.handoff_id}
        else:
            params = {'providers': [args.provider]}
        result = engine.call(args.slot, args.operation, params, admin=True)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get('ok') else 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
