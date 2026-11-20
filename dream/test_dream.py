import tempfile
import unittest
from pathlib import Path
from dream.core import Dream, preview


class Bank:
    def __init__(self):
        self.calls = []
        self.fail = False

    def call(self, method, body):
        self.calls.append((method, body))
        if self.fail:
            raise ValueError('bank timeout')
        return {'accountId': 'bank-account'} if method == 'OpenSandboxAccount' else {}


class DreamTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'dream.db'
        self.bank = Bank()
        self.dream = Dream(self.path, self.bank, 'alice')

    def test_forecast_no_date_or_dated_contributions(self):
        self.assertIsNone(preview(50000, 20000, 0)['baseline_months'])
        self.assertEqual(preview(50000, 20000, 10000)['baseline_months'], 3)

    def test_open_replay_and_owner_boundary(self):
        first = self.dream.create('goal-1', 'Куртка', 50000, 1000)
        self.assertEqual(first, self.dream.create('goal-1', 'Куртка', 50000, 1000))
        self.assertEqual(len(self.bank.calls), 1)
        bob = Dream(self.path, self.bank, 'bob')
        self.assertEqual(bob.list(), [])
        with self.assertRaises(ValueError):
            bob.status('goal-1')
        with self.assertRaises(ValueError):
            self.dream.create('goal-1', 'Другая цель', 50000, 1000)

    def test_funding_replay_and_ambiguous_timeout(self):
        self.dream.create('goal-1', 'Куртка', 50000, 1000)
        self.dream.fund('goal-1', 'fund-1', 12345)
        self.dream.fund('goal-1', 'fund-1', 12345)
        self.assertEqual(self.bank.calls[-1][1]['amount'], {'currency':'rub', 'units':'123', 'nano':450000000})
        self.bank.fail = True
        with self.assertRaises(ValueError):
            self.dream.fund('goal-1', 'fund-2', 1000)
        self.bank.fail = False
        with self.assertRaises(ValueError):
            self.dream.fund('goal-1', 'fund-3', 1000)
        self.assertEqual(len(self.bank.calls), 3)

    def test_readonly_never_opens(self):
        readonly = Dream(self.path, self.bank, 'alice', readonly=True)
        with self.assertRaises(ValueError):
            readonly.create('goal-1', 'Куртка', 50000, 1000)
        self.assertFalse(self.bank.calls)
