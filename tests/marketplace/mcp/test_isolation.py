import concurrent.futures
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

from market_mcp import Grants, Engine, create_app


class IsolationTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.grants = Grants(self.root)
        self.a, self.a_key = self.grants.issue('hermes-alice')
        self.b, self.b_key = self.grants.issue('hermes-bob')
        self.worker = self.root / 'probe.py'
        self.worker.write_text('''import json, os, pathlib, sys
req = json.load(sys.stdin)
marker = pathlib.Path(os.environ['XDG_STATE_HOME']) / 'marker'
print(json.dumps({'schema_version': 2, 'method': req['method'], 'ok': True,
 'status': 'ok', 'data': {'products': [{'title': marker.read_text()}]}}))
''')
        self.engine = Engine(self.root, scripts={'search': self.worker})
        for slot, name in ((self.a, 'alice'), (self.b, 'bob')):
            path = Path(self.engine.environment(slot)['XDG_STATE_HOME'])
            (path / 'marker').write_text(name)
        self.client = TestClient(create_app(self.grants, self.engine, ['127.0.0.1:8000']),
                                 base_url='http://127.0.0.1:8000')
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def rpc(self, key, method, params=None, **extra_headers):
        headers = {'Accept': 'application/json, text/event-stream',
                   'MCP-Protocol-Version': '2025-11-25', **extra_headers}
        if key:
            headers['Authorization'] = f'Bearer {key}'
        return self.client.post('/mcp', headers=headers, json={
            'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}})

    def search(self, key, **args):
        return self.rpc(key, 'tools/call', {'name': 'search', 'arguments': {'query': 'drill', **args}})

    def test_authentication_precedes_discovery_and_forged_headers_do_not_help(self):
        self.assertEqual(self.rpc(None, 'tools/list', **{'X-User-Id': self.a}).status_code, 401)
        self.assertEqual(self.rpc('wrong', 'tools/list').status_code, 401)
        self.assertEqual(self.rpc(self.a_key, 'tools/list', Host='other.example').status_code, 421)

    def test_only_read_tools_and_no_identity_selector(self):
        out = self.rpc(self.a_key, 'tools/list').json()['result']['tools']
        self.assertEqual({tool['name'] for tool in out}, {'search', 'product', 'quote', 'session_status'})
        for tool in out:
            self.assertFalse({'user_id', 'tenant', 'profile', 'path'} & set(tool['inputSchema']['properties']))
        response = self.search(self.b_key, tenant=self.a).json()
        self.assertTrue('error' in response or response['result'].get('isError'), response)
        response = self.rpc(self.a_key, 'tools/call', {'name': 'product', 'arguments': {'ref': 'https://example.com'}}).json()
        self.assertTrue('error' in response or response['result'].get('isError'), response)

    def test_marketplace_is_required_and_routes_to_one_provider(self):
        tools = self.rpc(self.a_key, 'tools/list').json()['result']['tools']
        for tool in tools:
            schema = tool['inputSchema']
            self.assertIn('marketplace', schema['required'])
            self.assertEqual(set(schema['properties']['marketplace']['enum']), {'ozon', 'aliexpress'})
            self.assertIn('aliexpress.ru', schema['properties']['marketplace']['description'])
        with patch.object(self.engine, 'call', return_value={'ok': True}) as worker:
            for marketplace in ('ozon', 'aliexpress'):
                result = self.search(self.a_key, marketplace=marketplace).json()
                self.assertTrue(result['result']['structuredContent']['ok'])
                worker.assert_called_with(self.a, 'search', {'query': 'drill', 'providers': [marketplace], 'limit': 12})
            worker.reset_mock()
            for arguments in ({'query': 'drill'}, {'query': 'drill', 'marketplace': 'both'},
                              {'query': 'drill', 'marketplace': ['ozon', 'aliexpress']}):
                result = self.rpc(self.a_key, 'tools/call', {'name': 'search', 'arguments': arguments}).json()
                self.assertTrue('error' in result or result['result'].get('isError'), result)
            for name, arguments in (('product', {}), ('quote', {'city': 'Kazan'})):
                result = self.rpc(self.a_key, 'tools/call', {'name': name, 'arguments': {
                    'marketplace': 'ozon', 'ref': 'aliexpress:123:456', **arguments}}).json()
                self.assertEqual(result['result']['structuredContent']['status'], 'marketplace_mismatch')
            worker.assert_not_called()

    def test_concurrent_tool_calls_use_the_right_browser_state(self):
        jobs = [(self.a_key, 'alice'), (self.b_key, 'bob')]
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            for _ in range(5):
                results = list(pool.map(lambda job: self.search(job[0], marketplace='ozon').json(), jobs))
                for response, (_, expected) in zip(results, jobs):
                    data = response['result']['structuredContent']['data']
                    self.assertEqual(data['products'][0]['title'], expected)
        self.assertNotEqual(self.engine.environment(self.a)['MARKETPLACE_BROWSER_ROOT'],
                            self.engine.environment(self.b)['MARKETPLACE_BROWSER_ROOT'])

    def test_revocation_persistence_and_expiry(self):
        self.grants.revoke(self.a)
        self.assertIsNone(Grants(self.root).lookup(self.a_key))
        self.assertEqual(self.rpc(self.a_key, 'tools/list').status_code, 401)
        self.assertEqual(self.rpc(self.b_key, 'tools/list').status_code, 200)
        _, expired = self.grants.issue('expired', expires_at=int(time.time()) - 1)
        self.assertEqual(self.rpc(expired, 'tools/list').status_code, 401)

    def test_private_roots_and_secrets_not_in_worker_env(self):
        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': 'secret'}):
            env = self.engine.environment(self.a)
        self.assertNotIn('TELEGRAM_BOT_TOKEN', env)
        for name in ('HOME', 'XDG_STATE_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'MARKETPLACE_BROWSER_ROOT', 'TMPDIR'):
            target = Path(env[name]); self.assertTrue(target.is_relative_to(self.root))
            self.assertEqual(target.stat().st_mode & 0o777, 0o700)


if __name__ == '__main__':
    unittest.main()
