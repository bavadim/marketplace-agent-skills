import unittest

from forecast import forecast


class ForecastTests(unittest.TestCase):
    def test_baseline_rounds_up(self):
        self.assertEqual(forecast(10001, 5000, 2500)['baseline_months'], 3)

    def test_without_contributions(self):
        self.assertIsNone(forecast(10001, 5000, 0)['baseline_months'])
        self.assertEqual(forecast(10001, 10001, 0)['baseline_months'], 0)

    def test_negative_scenario_never_reaches_target_without_contributions(self):
        self.assertIsNone(forecast(10001, 5000, 0, (-10,))['scenarios'][0]['months'])
