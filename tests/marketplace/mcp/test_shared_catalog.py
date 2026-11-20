import asyncio
import fcntl
import io
from pathlib import Path
import tarfile
import tempfile
import time
import unittest
from urllib.parse import urlsplit

from starlette.testclient import TestClient

from market_mcp import Engine, Grants, SHARED_OZON_SLOT, create_app
from viewer import Viewer


class Worker:
    status = 'ok'

    def __init__(self):
        self.calls = []

    def call(self, slot, method, params, **_):
        self.calls.append((slot, method))
        assert method == 'search', method
        return {'ok': self.status == 'ok', 'status': self.status,
                'data': {'products': [], 'sources': [{'provider': 'ozon', 'status': self.status}]}}


class SharedCatalogTest(unittest.TestCase):
    def test_guest_catalog_and_operator_only_handoff_use_same_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            grants = Grants(Path(tmp))
            worker = Worker()
            viewer = Viewer(grants, worker, 'https://viewer.example')
            key = 'a' * 64
            app = create_app(grants, worker, ['viewer.example'], viewer,
                             gateway_key=key, shared_ozon=True, search_interval=0)
            with TestClient(app, base_url='https://viewer.example') as client:
                def search(user):
                    reply = client.post('/mcp', headers={'Authorization': f'Bearer {key}',
                        'X-Marketplace-Identity': user, 'Accept': 'application/json, text/event-stream',
                        'MCP-Protocol-Version': '2025-11-25'}, json={'jsonrpc': '2.0', 'id': 1,
                        'method': 'tools/call', 'params': {'name': 'search', 'arguments': {
                            'marketplace': 'ozon', 'query': 'test'}}})
                    return reply.json()['result']['structuredContent']
                self.assertTrue(search('alice')['ok'])  # Empty successful catalog is not a challenge.
                self.assertTrue(search('bob')['ok'])
                self.assertEqual(worker.calls[0], worker.calls[1])
                self.assertIsNone(grants.identity_slot('alice'))
                worker.status = 'browser_navigation_error'
                self.assertEqual(search('default')['status'], 'browser_navigation_error')
                worker.status = 'browser_challenge'
                self.assertEqual(search('alice')['status'], 'operator_verification_required')
                first = search('default')
                self.assertEqual(first['status'], 'catalog_verification_required')
                self.assertEqual(first['authorization_url'], search('default')['authorization_url'])
                job = viewer.jobs[urlsplit(first['authorization_url']).path.split('/')[2]]
                self.assertEqual(job.slot, worker.calls[0][0])

    def test_queue_serializes_and_spaces_search_starts(self):
        from market_mcp import CatalogQueue

        async def exercise():
            queue = CatalogQueue(.08)
            events = []

            def work(i):
                events.append(('start', i, time.monotonic()))
                time.sleep(.025)
                events.append(('end', i, time.monotonic()))
                return i

            self.assertEqual(await asyncio.gather(*(queue.run(lambda i=i: work(i), search=True)
                                                     for i in range(3))), [0, 1, 2])
            self.assertEqual([(name, i) for name, i, _ in events],
                             [('start', 0), ('end', 0), ('start', 1), ('end', 1), ('start', 2), ('end', 2)])
            self.assertGreaterEqual(events[2][2]-events[0][2], .075)
            self.assertGreaterEqual(events[4][2]-events[2][2], .075)

        asyncio.run(exercise())

    def test_export_is_scoped_and_refuses_active_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = Engine(root)
            env = engine.environment(SHARED_OZON_SLOT)
            state = Path(env['XDG_STATE_HOME'])
            metadata = state / 'marketplace-accounts/ozon/metadata.json'
            metadata.parent.mkdir(parents=True)
            metadata.write_text('{}')
            (Path(env['MARKETPLACE_BROWSER_ROOT']) / 'fixture').write_bytes(b'profile')
            (root / 'gateway.key').write_bytes(b'not part of profile')
            out = io.BytesIO()
            with (state / 'mcp.lock').open('wb') as guard:
                fcntl.flock(guard, fcntl.LOCK_EX)
                with self.assertRaisesRegex(ValueError, 'in use'):
                    engine.export_shared_ozon(out)
            engine.export_shared_ozon(out)
            out.seek(0)
            with tarfile.open(fileobj=out) as archive:
                self.assertEqual(set(archive.getnames()), {
                    'profiles', 'profiles/fixture', 'state/marketplace-accounts/ozon/metadata.json'})
