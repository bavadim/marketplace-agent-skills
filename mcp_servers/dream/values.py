"""Validate goal references and normalize sandbox money for RPC clients."""
import re
from urllib.parse import urlsplit, urlunsplit



def product_link(url):
    if url is None:
        return None
    if len(url) > 2048 or any(c.isspace() or ord(c) < 32 or c in '\\<>`' for c in url):
        raise ValueError('Некорректная ссылка на товар')
    u = urlsplit(url)
    if (u.scheme != 'https' or u.netloc not in ('www.ozon.ru', 'ozon.ru', 'www.aliexpress.com',
            'aliexpress.com', 'aliexpress.ru', 'www.aliexpress.ru')
            or not re.fullmatch(r'/(?:product|item)/[\w.%-]+/?', u.path, flags=re.ASCII)):
        raise ValueError('Нужна прямая HTTPS-ссылка на карточку поддерживаемого магазина')
    return urlunsplit((u.scheme, u.netloc, u.path, '', ''))


def normalize_links(links):
    if links is None:
        return []
    if not isinstance(links, list) or len(links) > 12 or any(not isinstance(url, str) for url in links):
        raise ValueError('Допустимо до 12 ссылок на товары')
    return list(dict.fromkeys(product_link(url) for url in links))


def rub_kopecks(item, *, round_up=False):
    if not isinstance(item, dict) or item.get('currency') != 'rub':
        return None
    try:
        values = (item.get('units', 0), item.get('nano', 0))
        if any(type(value) not in (str, int) for value in values):
            return None
        units, nano = (int(value) for value in values)
    except (ValueError, KeyError, TypeError):
        return None
    if abs(nano) >= 1_000_000_000 or units * nano < 0:
        return None
    nanos = units * 1_000_000_000 + nano
    return max(0, -(-nanos // 10_000_000) if round_up else nanos // 10_000_000)


def available_kopecks(money, blocked):
    if not isinstance(money, list) or any(not isinstance(x, dict) for x in money):
        return None
    amounts = [rub_kopecks(x) for x in money if x.get('currency') == 'rub']
    if len(amounts) > 1 or any(value is None for value in amounts):
        return None
    cash = amounts[0] if amounts and amounts[0] is not None else 0
    for group in blocked:
        if not isinstance(group, list) or any(not isinstance(x, dict) for x in group):
            return None
        for item in group:
            if item.get('currency') == 'rub':
                reserved = rub_kopecks(item, round_up=True)
                if reserved is None:
                    return None
                cash -= reserved
    return max(0, cash)
