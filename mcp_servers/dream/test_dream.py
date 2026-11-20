import tempfile
import unittest
from pathlib import Path
from mcp_servers.dream.core import Dream
from mcp_servers.dream.values import available_kopecks, normalize_links


class ValueTests(unittest.TestCase):
    def test_links_are_sanitized(self):
        self.assertEqual(normalize_links(['https://www.ozon.ru/product/jacket-1/?track=123']),
                         ['https://www.ozon.ru/product/jacket-1/'])
        with self.assertRaises(ValueError):
            normalize_links(['https://www.ozon.ru.evil.example/product/1'])

    def test_blocked_cash_rounds_conservatively(self):
        cash = {'currency': 'rub', 'units': '500', 'nano': 0}
        blocked = {'currency': 'rub', 'units': '0', 'nano': 1}
        self.assertEqual(available_kopecks([cash], [[blocked]]), 49999)
        self.assertIsNone(available_kopecks(None, []))


class Bank:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.cash = 0
        self.verify_open = True
        self.credit = True

    def call(self, method, body):
        self.calls.append((method, body))
        if self.fail:
            raise ValueError('bank timeout')
        if method == 'OpenSandboxAccount':
            return {'accountId': 'bank-account'}
        if method == 'GetSandboxAccounts':
            return {'accounts': [{'id': 'bank-account', 'status': 'ACCOUNT_STATUS_OPEN'}] if self.verify_open else []}
        if method == 'SandboxPayIn':
            if self.credit:
                self.cash += int(body['amount']['units']) * 100 + body['amount']['nano'] // 10_000_000
            return {'balance': body['amount']}
        if method == 'GetSandboxPositions':
            return {'money': [{'currency': 'rub', 'units': str(self.cash // 100),
                               'nano': self.cash % 100 * 10_000_000}]}
        return {}


class DreamTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'dream.db'
        self.bank = Bank()
        self.dream = Dream(self.path, self.bank, 'alice')

    def test_status_returns_data_not_recommendations(self):
        self.dream.create('goal-1', 'Куртка', 50000, 1000)
        result = self.dream.status('goal-1')
        self.assertIn('observed_at', result)
        self.assertIn('available_kopecks', result)
        self.assertNotIn('forecast', result)
        self.assertNotIn('accountId', result)

    def test_open_replay_and_owner_boundary(self):
        first = self.dream.create('goal-1', 'Куртка', 50000, 1000)
        self.assertEqual(first, self.dream.create('goal-1', 'Куртка', 50000, 1000))
        self.assertEqual([name for name, _ in self.bank.calls], ['OpenSandboxAccount', 'GetSandboxAccounts'])
        self.assertTrue(first['account_verified'])
        bob = Dream(self.path, self.bank, 'bob')
        self.assertEqual(bob.list(), [])
        with self.assertRaises(ValueError):
            bob.status('goal-1')
        with self.assertRaises(ValueError):
            self.dream.create('goal-1', 'Другая цель', 50000, 1000)

    def test_funding_replay_and_ambiguous_timeout(self):
        self.dream.create('goal-1', 'Куртка', 50000, 1000)
        first = self.dream.fund('goal-1', 'fund-1', 12345)
        self.assertEqual(first['status'], 'funding_verified')
        self.assertEqual(first, self.dream.fund('goal-1', 'fund-1', 12345))
        payment = next(body for method, body in self.bank.calls if method == 'SandboxPayIn')
        self.assertEqual(payment['amount'], {'currency':'rub', 'units':'123', 'nano':450000000})
        self.bank.fail = True
        with self.assertRaises(ValueError):
            self.dream.fund('goal-1', 'fund-2', 1000)
        self.bank.fail = False
        with self.assertRaises(ValueError):
            self.dream.fund('goal-1', 'fund-3', 1000)
        self.assertEqual([name for name, _ in self.bank.calls].count('SandboxPayIn'), 1)

    def test_open_unverified_never_repeats(self):
        self.bank.verify_open = False
        for _ in range(2):
            with self.assertRaises(ValueError):
                self.dream.create('bad', 'Куртка', 50000, 1000)
        self.assertFalse(self.dream.list())
        self.assertEqual([m for m, _ in self.bank.calls].count('OpenSandboxAccount'), 1)

    def test_funding_unverified_never_repeats(self):
        self.dream.create('good', 'Куртка', 50000, 1000)
        self.bank.credit = False
        for op in ('one', 'two'):
            with self.assertRaises(ValueError):
                self.dream.fund('good', op, 12345)
        self.assertEqual(self.dream.list()[0]['contributed_kopecks'], 0)
        self.assertEqual([m for m, _ in self.bank.calls].count('SandboxPayIn'), 1)

    def test_readonly_never_opens(self):
        readonly = Dream(self.path, self.bank, 'alice', readonly=True)
        with self.assertRaises(ValueError):
            readonly.create('goal-1', 'Куртка', 50000, 1000)
        self.assertFalse(self.bank.calls)
