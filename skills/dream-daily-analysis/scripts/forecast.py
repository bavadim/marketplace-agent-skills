"""Calculate savings scenarios. Amounts are integer kopecks; no market data is used."""
import argparse
import json
from decimal import Decimal


def forecast(target: int, available: int, monthly: int, scenarios: tuple[int, ...] = ()) -> dict:
    if not all(type(v) is int and 0 <= v <= 100_000_000_000 for v in (target, available, monthly)) or not target:
        raise ValueError('Ожидаются целые суммы в копейках и положительная цель')
    if any(type(rate) is not int or not -99 <= rate <= 100 for rate in scenarios):
        raise ValueError('Годовая ставка сценария: целый процент от −99 до 100')
    gap = max(0, target - available)
    baseline = (gap + monthly - 1) // monthly if gap and monthly else (None if gap else 0)
    results = []
    for annual_percent in scenarios:
        multiplier = (1 + Decimal(annual_percent) / 100) ** (Decimal(1) / 12)
        amount = Decimal(available)
        months = 0
        while amount < target and months < 120:
            amount = amount * multiplier + monthly
            months += 1
        results.append({'annual_percent': annual_percent, 'months': months if amount >= target else None,
                        'evaluated_months': months, 'amount_kopecks': int(amount)})
    return {'gap_kopecks': gap, 'baseline_months': baseline, 'scenarios': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', type=int)
    parser.add_argument('available', type=int)
    parser.add_argument('monthly', type=int)
    parser.add_argument('--annual-percent', action='append', type=int, default=[])
    args = parser.parse_args()
    try:
        result = forecast(args.target, args.available, args.monthly, tuple(args.annual_percent))
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
