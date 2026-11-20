"""T-Invest sandbox REST only; no redirects or production configuration."""
import json
import ssl
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

BASE = 'https://sandbox-invest-public-api.tbank.ru/rest/tinkoff.public.invest.api.contract.v1.'
SANDBOX = frozenset(('GetSandboxAccounts', 'OpenSandboxAccount', 'CloseSandboxAccount', 'SandboxPayIn',
    'GetSandboxPortfolio', 'GetSandboxWithdrawLimits', 'PostSandboxOrder', 'GetSandboxOrderState'))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Sandbox:
    def __init__(self, token, ca, *, readonly=False):
        self._token = token
        self._readonly = readonly
        self._http = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context(cafile=ca)))

    def call(self, method, body):
        if self._readonly and not method.startswith('Get'):
            raise ValueError('Только чтение')
        if not self._token:
            raise ValueError('Тестовый токен не настроен на сервере')
        if method in SANDBOX:
            service = 'SandboxService'
        elif method == 'GetLastPrices':
            service = 'MarketDataService'
        else:
            raise ValueError('Метод недоступен')
        req = Request(f'{BASE}{service}/{method}', json.dumps(body).encode(),
                      {'Authorization': 'Bearer ' + self._token, 'Content-Type': 'application/json'}, method='POST')
        try:
            with self._http.open(req, timeout=12) as response:
                data = response.read(2_000_001)
            if len(data) > 2_000_000:
                raise ValueError('Слишком большой ответ песочницы')
            result = json.loads(data)
            if not isinstance(result, dict):
                raise ValueError('Некорректный ответ песочницы')
            return result
        except HTTPError as error:
            raise ValueError(f'Песочница {method}: HTTP {error.code}') from None
        except (URLError, TimeoutError, UnicodeError, json.JSONDecodeError):
            raise ValueError(f'Песочница {method}: нет подтверждённого ответа') from None
