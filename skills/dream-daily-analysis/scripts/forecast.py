"""Calculate savings scenarios. Amounts are integer kopecks; no market data is used."""
import argparse
import json
import sys
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


def _self_test():
    import unittest

    class ForecastTests(unittest.TestCase):
        def test_baseline_rounds_up(self):
            self.assertEqual(forecast(10001, 5000, 2500)['baseline_months'], 3)

        def test_without_contributions(self):
            self.assertIsNone(forecast(10001, 5000, 0)['baseline_months'])
            self.assertEqual(forecast(10001, 10001, 0)['baseline_months'], 0)

        def test_negative_scenario_never_reaches_target_without_contributions(self):
            self.assertIsNone(forecast(10001, 5000, 0, (-10,))['scenarios'][0]['months'])

    suite = unittest.TestSuite()
    for case in (ForecastTests,):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(case))
    return 0 if unittest.TextTestRunner().run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        raise SystemExit(_self_test())
    main()
