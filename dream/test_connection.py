import tempfile
import unittest
from pathlib import Path

from mcp import Client
from mcp.types import ElicitResult

from dream.bank import CredentialRequired
from dream.core import Dream
from dream.credentials import TokenFile
from dream.server import build


class Bank:
    def __init__(self, credential, unavailable=False):
        self.credential = credential
        self.unavailable = unavailable

    def call(self, method, body):
        if self.unavailable:
            raise ValueError('timeout')
        if self.credential() != 'synthetic-marker':
            raise CredentialRequired('rejected')
        return {'accounts': []}


class ConnectionTest(unittest.IsolatedAsyncioTestCase):
    async def test_form_validates_before_save_and_only_runs_when_needed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            store = TokenFile(path / 'secret' / 'token')
            dream = Dream(path / 'db', Bank(store.read), 'owner')
            answers = iter([ElicitResult(action='decline'),
                            ElicitResult(action='accept', content={'token': 'synthetic-wrong'}),
                            ElicitResult(action='accept', content={'token': 'synthetic-marker'})])
            requests = []

            async def answer(context, params):
                requests.append(params)
                return next(answers)

            server = build(dream, token_store=store, bank_factory=lambda token: Bank(lambda: token))
            async with Client(server, mode='legacy', elicitation_callback=answer) as client:
                for expected in ('not_confirmed', 'token_rejected', 'connected', None):
                    result = await client.call_tool('begin_connection')
                    self.assertFalse(result.is_error)
                    self.assertNotIn('synthetic-', result.model_dump_json())
                    self.assertEqual(result.structured_content.get('status'), expected)
                    if expected in ('not_confirmed', 'token_rejected'):
                        self.assertIsNone(store.read())
            self.assertEqual(len(requests), 3)
            self.assertEqual(store.read(), 'synthetic-marker')
            self.assertEqual(store.path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(b'synthetic-marker', (path / 'db').read_bytes())

    async def test_network_failure_does_not_request_token(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            store = TokenFile(path / 'secret' / 'token')
            dream = Dream(path / 'db', Bank(store.read, unavailable=True), 'owner')
            requests = []

            async def decline(context, params):
                requests.append(params)
                return ElicitResult(action='decline')

            async with Client(build(dream, token_store=store, bank_factory=lambda _: None),
                              mode='legacy', elicitation_callback=decline) as client:
                result = await client.call_tool('begin_connection')
                self.assertFalse(result.structured_content['connected'])
            self.assertFalse(requests)
