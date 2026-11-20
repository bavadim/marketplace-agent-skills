import tempfile
from pathlib import Path
import unittest
from urllib.parse import urlsplit

from starlette.testclient import TestClient

from market_mcp import Grants, create_app
from viewer import Viewer


class Worker:
    def __init__(self):
        self.saved = set()
        self.calls = []

    def call(self, slot, method, params, **kwargs):
        self.calls.append((slot, method))
        if method == 'session.forget':
            self.saved.discard(slot)
        return {'ok': True, 'data': {'sessions': [{'provider': 'ozon',
                'profile_saved': slot in self.saved, 'authenticated': slot in self.saved}]}}


class GatewayIdentityTest(unittest.TestCase):
    def test_authenticated_gateway_provisions_on_first_authorize_and_recreates_only_own_session(self):
        origin = 'https://viewer.example'
        key = 'a' * 64
        with tempfile.TemporaryDirectory() as tmp:
            grants = Grants(Path(tmp))
            engine = Worker()
            viewer = Viewer(grants, engine, origin)
            with TestClient(create_app(grants, engine, ['viewer.example'], viewer, gateway_key=key),
                            base_url=origin) as client:
                def rpc(user, method, params=None, auth: str | None = key):
                    headers = {'Accept': 'application/json, text/event-stream',
                               'MCP-Protocol-Version': '2025-11-25', 'X-Marketplace-Identity': user}
                    if auth is not None:
                        headers['Authorization'] = 'Bearer ' + auth
                    return client.post('/mcp', headers=headers, json={
                        'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}})

                def tool(user, name, **args):
                    return rpc(user, 'tools/call', {'name': name, 'arguments': args}).json()['result']

                self.assertEqual(rpc('telegram:42', 'tools/list', auth=None).status_code, 401)
                self.assertEqual(rpc('telegram:42', 'tools/list', auth='b' * 64).status_code, 401)
                self.assertEqual(rpc('', 'tools/list').status_code, 401)
                self.assertEqual(rpc('telegram:42', 'tools/list').status_code, 200)
                self.assertEqual(engine.calls, [])
                self.assertTrue(tool('telegram:42', 'authorize', marketplace='ozon', user_id='99')['isError'])
                alice = tool('telegram:42', 'authorize', marketplace='ozon')['structuredContent']
                bob = tool('telegram:99', 'authorize', marketplace='ozon')['structuredContent']
                self.assertNotEqual(urlsplit(alice['authorization_url']).path, urlsplit(bob['authorization_url']).path)
                a = viewer.jobs[urlsplit(alice['authorization_url']).path.split('/')[2]].slot
                b = viewer.jobs[urlsplit(bob['authorization_url']).path.split('/')[2]].slot
                self.assertNotEqual(a, b)
                with viewer.lock:
                    viewer.drop(viewer.by_slot[a])
                engine.saved.update((a, b))
                self.assertTrue(tool('telegram:42', 'search', marketplace='ozon', query='coat')['structuredContent']['ok'])
                self.assertEqual(engine.calls[-1], (a, 'search'))
                reset = tool('telegram:42', 'authorize', marketplace='ozon')['structuredContent']
                self.assertNotEqual(urlsplit(alice['authorization_url']).path, urlsplit(reset['authorization_url']).path)
                self.assertEqual(viewer.by_slot[a].slot, a)
                self.assertNotIn(a, engine.saved)
                self.assertIn(b, engine.saved)
                self.assertEqual(client.get(urlsplit(alice['authorization_url']).path).status_code, 410)
                self.assertEqual(client.get(urlsplit(bob['authorization_url']).path).status_code, 200)
            self.assertEqual(Grants(Path(tmp)).identity_slot('telegram:42'), a)
