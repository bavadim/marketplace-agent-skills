"""Deterministic, read-only decisions for the official sandbox."""
import re
from urllib.parse import urlsplit, urlunsplit

from dream.core import preview


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


def offer_savings_goal(title, target_kopecks, initial_kopecks=0, monthly_kopecks=0, purchase_url=None,
                       purchase_urls=None):
    if purchase_urls is not None and (not isinstance(purchase_urls, list) or len(purchase_urls) > 12):
        raise ValueError('Допустимо до 12 ссылок на товары')
    links = list(dict.fromkeys(link for value in [purchase_url, *(purchase_urls or [])]
                              if (link := product_link(value)) is not None))
    if len(links) > 12:
        raise ValueError('Допустимо до 12 ссылок на товары')
    if not 0 < len(title.strip()) <= 80:
        raise ValueError('Название цели: 1–80 символов')
    return {'status': 'offer_only', 'mode': 'sandbox', 'title': title,
            'target_kopecks': target_kopecks, 'purchase_urls': links,
            'forecast': preview(target_kopecks, initial_kopecks, monthly_kopecks),
            'message': 'Хочешь открыть тестовую копилку на эту покупку? Реальных списаний нет.'}


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


def suggestions(rows):
    result = []
    for row in rows:
        if row.get('closed'):
            continue
        target = row['target_kopecks']
        withdrawal = row.get('withdrawable_virtual')
        amounts = [rub_kopecks(x) for x in withdrawal if isinstance(x, dict) and x.get('currency') == 'rub'] if isinstance(withdrawal, list) else []
        cash = amounts[0] if len(amounts) == 1 and row.get('live_status') != 'unavailable' else None
        if cash is not None:
            for group in row.get('blocked_virtual', []):
                if not isinstance(group, list) or any(not isinstance(item, dict) for item in group):
                    cash = None
                    break
                reserved = [rub_kopecks(item, round_up=True) for item in group if item.get('currency') == 'rub']
                if any(amount is None for amount in reserved):
                    cash = None
                    break
                cash = max(0, cash - sum(amount for amount in reserved if amount is not None))
        portfolio_data = row.get('portfolio')
        portfolio = rub_kopecks(portfolio_data.get('totalAmountPortfolio')) if isinstance(portfolio_data, dict) else None
        if cash is None:
            action = 'wait_for_data'
        elif cash >= target:
            action = 'review_withdraw_and_purchase'
        elif portfolio is not None and portfolio >= target:
            action = 'review_sale'
        else:
            action = 'continue_saving'
        result.append({'goal_id': row['id'], 'title': row['title'], 'target_kopecks': target,
                       'cash_kopecks': cash, 'portfolio_kopecks': portfolio, 'action': action,
                       'purchase_urls': row.get('purchase_urls') or ([row['purchase_url']] if row.get('purchase_url') else []),
                       'forecast': preview(target, cash, row['monthly_kopecks']) if cash is not None else None})
    return result


def render(rows):
    def rub(value):
        return f'{value // 100:,}'.replace(',', ' ') + f',{value % 100:02d} ₽'

    lines = ['Инвестируй в мечту · песочница'] if rows else []
    for row in rows:
        title = ' '.join(row['title'].split())
        lines.append(f'\n{title} — цель {rub(row["target_kopecks"])} (сохранённая цена)')
        cash = row['cash_kopecks']
        if cash is not None:
            lines.append(f'Виртуально доступно: {rub(cash)}.')
        if row['action'] == 'review_withdraw_and_purchase':
            lines.append('По сохранённой цене хватает. Проверь текущую цену и наличие перед покупкой. '
                         'В песочнице вывод не выполнен: виртуальные деньги нельзя вывести. '
                         'Если хочешь купить за реальные деньги, открой магазин и оплати сам.')
            lines.extend(row['purchase_urls'])
        elif row['action'] == 'review_sale':
            lines.append('Оценка портфеля достигает цели, но доступных денег не хватает. '
                         'Можно запросить расчёт продажи; заявка не выставлена.')
        elif row['action'] == 'wait_for_data':
            lines.append('Нет подтверждённого рублёвого остатка; готовность не определяю.')
        else:
            lines.append(f'До цели не хватает {rub(row["target_kopecks"] - cash)}.')
            months = row['forecast']['baseline_months']
            lines.append(f'Без доходности: {months} мес. при заданных взносах.' if months is not None
                         else 'Без новых взносов и доходности даты нет.')
    return '\n'.join(lines)


def daily_proposal(dream):
    rows = []
    for goal in dream.list():
        if not goal['closed']:
            try:
                rows.append(dream.status(goal['id']))
            except ValueError:
                rows.append({**goal, 'live_status': 'unavailable'})
    goals = suggestions(rows)
    return {'mode': 'sandbox', 'goals': goals, 'message': render(goals)}
