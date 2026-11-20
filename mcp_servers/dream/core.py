"""Owner-scoped goals and a conservative journal for sandbox mutations."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from mcp_servers.dream.values import available_kopecks, normalize_links, rub_kopecks


def money(kopecks):
    if type(kopecks) is not int or not 0 < kopecks <= 100_000_000_000:
        raise ValueError('Укажи положительную сумму в копейках')
    return {'currency': 'rub', 'units': str(kopecks // 100), 'nano': kopecks % 100 * 10_000_000}


class Dream:
    def __init__(self, path, bank, owner, readonly=False):
        self.path, self.bank, self.owner, self.readonly = path, bank, owner, readonly
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS goals(owner TEXT, id TEXT, payload TEXT, PRIMARY KEY(owner,id))')
            db.execute('CREATE TABLE IF NOT EXISTS ops(owner TEXT, id TEXT, goal TEXT, intent TEXT, result TEXT, PRIMARY KEY(owner,id))')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def connection(self):
        self.accounts()
        return {'mode': 'sandbox', 'connected': True}

    def accounts(self, bank=None):
        response = (self.bank if bank is None else bank).call('GetSandboxAccounts', {})
        accounts = response.get('accounts', [])
        if not isinstance(accounts, list) or any(not isinstance(a, dict) for a in accounts):
            raise ValueError('Некорректный список счетов песочницы')
        return {a['id'] for a in accounts if a.get('status') == 'ACCOUNT_STATUS_OPEN'
                and isinstance(a.get('id'), str) and a['id']}

    def check_replacement(self, bank):
        accounts = self.accounts(bank)
        with self.db() as db:
            goals = [json.loads(row[0]) for row in db.execute(
                'SELECT payload FROM goals WHERE owner=?', (self.owner,))]
        if any(g['accountId'] not in accounts for g in goals if not g['closed']):
            raise ValueError('Новый токен не видит открытые счета этого профиля')

    def cash(self, args):
        items = self.bank.call('GetSandboxPositions', args).get('money', [])
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise ValueError('Не удалось сверить рублёвый баланс')
        rubles = [rub_kopecks(item) for item in items if item.get('currency') == 'rub']
        if len(rubles) > 1 or any(item is None for item in rubles):
            raise ValueError('Не удалось сверить рублёвый баланс')
        return sum(item for item in rubles if item is not None)

    def list(self):
        with self.db() as db:
            return [self.public(json.loads(row[0])) for row in db.execute(
                'SELECT payload FROM goals WHERE owner=? ORDER BY id', (self.owner,))]

    @staticmethod
    def public(record):
        return {key: value for key, value in record.items() if key != 'accountId'}

    def record(self, db, goal):
        row = db.execute('SELECT payload FROM goals WHERE owner=? AND id=?', (self.owner, goal)).fetchone()
        if row is None:
            raise ValueError('Цель не найдена')
        return json.loads(row[0])

    def status(self, goal):
        with self.db() as db:
            record = self.record(db, goal)
        result = self.public(record)
        if not record['closed']:
            args = {'accountId': record['accountId']}
            portfolio = self.bank.call('GetSandboxPortfolio', args)
            result['portfolio'] = {k: v for k, v in portfolio.items() if k != 'accountId'}
            limits = self.bank.call('GetSandboxWithdrawLimits', args)
            result['withdrawable_virtual'] = limits.get('money')
            result['blocked_virtual'] = [limits.get(key, []) for key in ('blocked', 'blockedGuarantee')]
            result['available_kopecks'] = available_kopecks(result['withdrawable_virtual'], result['blocked_virtual'])
            result['portfolio_kopecks'] = rub_kopecks(portfolio.get('totalAmountPortfolio'))
            result['observed_at'] = datetime.now(timezone.utc).isoformat()
        return result

    def mutate(self, goal, op, kind, params):
        if self.readonly:
            raise ValueError('Только чтение')
        if not all(isinstance(v, str) and 0 < len(v) <= 128 for v in (goal, op)):
            raise ValueError('Некорректный идентификатор')
        intent = json.dumps([goal, kind, params], sort_keys=True)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            prev = db.execute('SELECT intent,result FROM ops WHERE owner=? AND id=?', (self.owner, op)).fetchone()
            if prev:
                if prev[0] != intent or prev[1] is None:
                    raise ValueError('Конфликт операции или неизвестный результат; требуется сверка оператором')
                return json.loads(prev[1])
            if db.execute('SELECT 1 FROM ops WHERE owner=? AND goal=? AND result IS NULL', (self.owner, goal)).fetchone():
                raise ValueError('Есть неподтверждённая операция; требуется сверка оператором')
            if kind == 'open':
                if db.execute('SELECT 1 FROM goals WHERE owner=? AND id=?', (self.owner, goal)).fetchone():
                    raise ValueError('Цель уже существует')
                record = {'id': goal, **params, 'closed': False, 'contributed_kopecks': 0}
            else:
                record = self.record(db, goal)
                if record['closed']:
                    raise ValueError('Цель закрыта')
            db.execute('INSERT INTO ops VALUES(?,?,?,?,NULL)', (self.owner, op, goal, intent))
        if kind == 'open':
            record['accountId'] = self.bank.call('OpenSandboxAccount', {'name': record['title']}).get('accountId')
            if record['accountId'] not in self.accounts():
                raise ValueError('Открытие ещё не подтверждено; повторно счёт не открываем')
            record['account_verified'] = True
            result = self.public(record)
        else:
            args = {'accountId': record['accountId']}
            if kind == 'fund':
                before = self.cash(args)
                response = self.bank.call('SandboxPayIn', {**args, 'amount': money(params['kopecks'])})
                after = self.cash(args)
                if after - before != params['kopecks'] or rub_kopecks(response.get('balance')) != after:
                    raise ValueError('Взнос ещё не подтверждён; повторно не начисляем')
                record['contributed_kopecks'] += params['kopecks']
                result = {'status': 'funding_verified', 'balance': response.get('balance'),
                          'goal_id': goal}
            elif kind == 'order':
                response = self.bank.call('PostSandboxOrder', {**args, **params, 'orderId': str(uuid4())})
                result = {k: v for k, v in response.items() if k != 'accountId'}
            else:
                self.bank.call('CloseSandboxAccount', args)
                record['closed'] = True
                result = {'status': 'closed'}
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO goals VALUES(?,?,?)', (self.owner, goal, json.dumps(record)))
            db.execute('UPDATE ops SET result=? WHERE owner=? AND id=?', (json.dumps(result), self.owner, op))
        return result

    def create(self, goal, title, target, monthly, purchase_urls=None):
        money(target)
        if type(monthly) is not int or monthly < 0:
            raise ValueError('Взнос должен быть неотрицательной суммой в копейках')
        if monthly:
            money(monthly)
        if not 0 < len(title.strip()) <= 80:
            raise ValueError('Название цели: 1–80 символов')
        params = {'title': title, 'target_kopecks': target, 'monthly_kopecks': monthly}
        links = normalize_links(purchase_urls)
        if links:
            params['purchase_urls'] = links
        return self.mutate(goal, 'create:' + goal, 'open', params)

    def fund(self, goal, op, kopecks):
        money(kopecks)
        return self.mutate(goal, 'fund:' + op, 'fund', {'kopecks': kopecks})

    def order(self, goal, op, instrument, side, lots, limit):
        if not instrument or len(instrument) > 100 or side not in ('buy','sell') or not 1 <= lots <= 1000:
            raise ValueError('Некорректные параметры заявки')
        price = money(limit)
        price.pop('currency')
        return self.mutate(goal, 'order:' + op, 'order', {'instrumentId': instrument, 'quantity': str(lots),
            'direction': 'ORDER_DIRECTION_' + side.upper(), 'orderType': 'ORDER_TYPE_LIMIT',
            'priceType': 'PRICE_TYPE_CURRENCY', 'confirmMarginTrade': False, 'price': price})

    def close(self, goal, op):
        return self.mutate(goal, 'close:' + op, 'close', {})

    def order_state(self, goal, order):
        with self.db() as db:
            account = self.record(db, goal)['accountId']
        result = self.bank.call('GetSandboxOrderState', {'accountId': account, 'orderId': order})
        return {k: v for k, v in result.items() if k != 'accountId'}
