import tempfile
import unittest
from pathlib import Path
from mcp_servers.dream.core import Dream, preview
from mcp_servers.dream.proposals import offer_savings_goal, suggestions, render


class ProposalTests(unittest.TestCase):
    def test_offer_is_readonly_and_uses_same_calculation_as_digest(self):
        offer = offer_savings_goal('Лук', 50000, 20000, 10000)
        self.assertEqual(offer['forecast']['baseline_months'], 3)
        self.assertIn('открыть', offer['message'])
        self.assertEqual(offer['status'], 'offer_only')

    def test_daily_prioritizes_cash_and_excludes_archived(self):
        rows = [{'id':'a','title':'Куртка','target_kopecks':50000,'monthly_kopecks':10000,
                 'withdrawable_virtual':[{'currency':'rub','units':'500','nano':0}]},
                {'id':'b','title':'Обувь','target_kopecks':50000,'monthly_kopecks':0,
                 'portfolio':{'totalAmountPortfolio':{'currency':'rub','units':'600','nano':0}},
                 'withdrawable_virtual':[{'currency':'rub','units':'100','nano':0}]},
                {'id':'c','title':'Закрытая','closed':True},
                {'id':'d','title':'Нет данных','target_kopecks':50000,'monthly_kopecks':0}]
        result = suggestions(rows)
        self.assertEqual([r['action'] for r in result], ['review_withdraw_and_purchase','review_sale','wait_for_data'])
        self.assertEqual(result[0]['forecast']['baseline_months'], 0)
        self.assertIn('вывод не выполнен', render(result))
        self.assertNotIn('Закрытая', render(result))

    def test_blocked_cash_does_not_trigger_purchase_and_link_is_sanitized(self):
        offer = offer_savings_goal('Лук', 50000, purchase_url='https://www.ozon.ru/product/jacket-1/?track=123')
        self.assertEqual(offer['purchase_urls'], ['https://www.ozon.ru/product/jacket-1/'])
        with self.assertRaises(ValueError):
            offer_savings_goal('Лук', 50000, purchase_url='https://www.ozon.ru.evil.example/product/1')
        cash = {'currency': 'rub', 'units': '500', 'nano': 0}
        blocked = {'currency': 'rub', 'units': '0', 'nano': 1}
        row = {'id': 'x', 'title': 'Лук', 'target_kopecks': 50000, 'monthly_kopecks': 0,
               'withdrawable_virtual': [cash], 'blocked_virtual': [[blocked]]}
        result = suggestions([row])[0]
        self.assertEqual(result['cash_kopecks'], 49999)
        self.assertEqual(result['action'], 'continue_saving')

    def test_bad_bank_data_does_not_imply_ready(self):
        r = {'id':'x','title':'Цель','target_kopecks':1,'monthly_kopecks':0,
             'withdrawable_virtual':[{'currency':'usd','units':'500','nano':0}]}
        self.assertEqual(suggestions([r])[0]['action'], 'wait_for_data')


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

    def test_forecast_no_date_or_dated_contributions(self):
        self.assertIsNone(preview(50000, 20000, 0)['baseline_months'])
        self.assertEqual(preview(50000, 20000, 10000)['baseline_months'], 3)

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
