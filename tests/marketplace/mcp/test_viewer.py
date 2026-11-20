import asyncio
import tempfile
from pathlib import Path
import unittest
from urllib.parse import parse_qs, urlsplit

from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from market_mcp import Engine, Grants, create_app
from viewer import Viewer


class ViewerTest(unittest.TestCase):
    def test_grant_has_separate_viewer_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            grants = Grants(Path(tmp))
            alice, bearer = grants.issue('alice')
            bob, _ = grants.issue('bob')
            key = grants.set_viewer_key(alice)
            self.assertTrue(grants.verify_viewer(alice, key))
            self.assertFalse(grants.verify_viewer(bob, key))
            self.assertFalse(grants.verify_viewer(alice, bearer))
            grants.revoke(alice)
            self.assertFalse(grants.verify_viewer(alice, key))

    def test_public_origin_rejects_insecure_or_path(self):
        for origin in ('http://example.org', 'https://example.org/foo', 'https://user@example.org', 'https://example.org/?a=1'):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                Viewer(None, None, origin)

    def test_invite_is_bound_to_slot_and_ticket_and_consumed_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            grants = Grants(Path(tmp))
            alice, _ = grants.issue('alice')
            bob, _ = grants.issue('bob')
            ticket = 'A' * 32
            key = grants.make_viewer_invite(alice, ticket)
            self.assertFalse(grants.verify_viewer(alice, key))
            self.assertFalse(grants.use_viewer_invite(alice, 'B' * 32, key))
            self.assertFalse(grants.use_viewer_invite(bob, ticket, key))
            self.assertTrue(grants.use_viewer_invite(alice, ticket, key))
            self.assertFalse(grants.use_viewer_invite(alice, ticket, key))

    def test_two_viewers_have_distinct_leases_and_revoke_existing_socket(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            grants = Grants(root)
            alice, a_bearer = grants.issue('alice')
            bob, b_bearer = grants.issue('bob')


            class FakeEngine(Engine):
                connected = set()
                def call(self, slot, method, params, **kwargs):
                    return {'ok': True, 'data': {'sessions': [{'provider': 'ozon',
                        'profile_saved': slot in self.connected, 'authenticated': slot in self.connected}]}}

            class LocalSocketViewer(Viewer):
                async def run(self, job):
                    job.directory = root / job.ticket
                    job.directory.mkdir()

                    async def handle(reader, writer):
                        writer.write(job.slot.encode())
                        await writer.drain()
                        await reader.read()
                        writer.close()

                    server = await asyncio.start_unix_server(handle, path=job.directory / 'rfb')
                    job.ready = True
                    try:
                        while self.active_job(job) and not (job.directory / 'check').exists():
                            await asyncio.sleep(.025)
                        if (job.directory / 'check').exists():
                            (job.directory / 'sealed').touch()
                            self.engine.connected.add(job.slot)
                    finally:
                        server.close()
                        await server.wait_closed()
                        self.drop(job)

            origin = 'https://demo.example'
            engine = FakeEngine(root)
            viewer = LocalSocketViewer(grants, engine, origin)
            with TestClient(create_app(grants, engine, ['demo.example'], viewer), base_url=origin) as client:
                def authorize(bearer):
                    headers = {'Authorization': f'Bearer {bearer}', 'Accept': 'application/json, text/event-stream',
                               'MCP-Protocol-Version': '2025-11-25'}
                    req = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                           'params': {'name': 'authorize', 'arguments': {'marketplace': 'ozon'}}}
                    return client.post('/mcp', json=req, headers=headers).json()['result']['structuredContent']
                a_url = urlsplit(authorize(a_bearer)['authorization_url'])
                b_url = urlsplit(authorize(b_bearer)['authorization_url'])
                a, b = a_url.path, b_url.path
                a_invite = parse_qs(a_url.fragment)['invite'][0]
                b_invite = parse_qs(b_url.fragment)['invite'][0]
                self.assertNotEqual(a, b)
                self.assertEqual(client.get(a).headers['referrer-policy'], 'strict-origin')
                self.assertNotIn('img-src data:', client.get(a).headers['content-security-policy'])
                self.assertFalse(viewer.checked(alice))
                replacement = urlsplit(authorize(a_bearer)['authorization_url'])
                self.assertEqual(replacement.path, a)
                invitation = parse_qs(replacement.fragment)['invite'][0]
                headers = {'Origin': origin}

                self.assertEqual(client.post(a, data={'key': b_invite}, headers=headers).status_code, 403)
                self.assertEqual(client.post(a, data={'key': a_invite}, headers=headers).status_code, 403)
                self.assertEqual(client.post(a, data={'key': invitation}, headers={'Origin': 'https://other.example'}).status_code, 403)
                self.assertEqual(client.get('/view/claim.js').status_code, 200)
                for path, key in ((a, invitation), (b, b_invite)):
                    self.assertEqual(client.post(path, data={'key': key}, headers=headers, follow_redirects=False).status_code, 303)
                    for _ in range(20):
                        if client.get(path+'status').json()['status'] == 'ready':
                            break
                    else:
                        self.fail('viewer did not start')
                self.assertEqual(authorize(a_bearer), {'ok': False, 'status': 'authorization_in_progress'})
                replay = client.build_request('POST', a, data={'key': invitation}, headers=headers)
                replay.headers.pop('cookie', None)
                self.assertEqual(client.send(replay).status_code, 410)
                ws_origin = 'wss://demo.example'
                self.assertIn('img-src data:', client.get(a+'screen').headers['content-security-policy'])
                with client.websocket_connect(ws_origin+a+'ws', headers=headers) as aws, client.websocket_connect(ws_origin+b+'ws', headers=headers) as bws:
                    self.assertEqual(aws.receive_bytes(), alice.encode())
                    self.assertEqual(bws.receive_bytes(), bob.encode())
                    self.assertEqual(client.post(a+'check', headers=headers).status_code, 200)
                    with self.assertRaises(WebSocketDisconnect):
                        aws.receive_bytes()
                    self.assertEqual(client.get(a+'screen').status_code, 410)
                    self.assertEqual(authorize(a_bearer), {'ok': True, 'status': 'success'})
                    self.assertTrue(viewer.checked(alice))
                    self.assertFalse(viewer.checked(bob))
                    self.assertEqual(client.get(b+'status').json()['status'], 'ready')
                    grants.revoke(bob)
                    with self.assertRaises(WebSocketDisconnect):
                        bws.receive_bytes()
                    self.assertEqual(client.get(b+'screen').status_code, 410)
