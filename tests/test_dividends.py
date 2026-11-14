import json
import unittest

from engine import dividends as dv

DAY = 86400
NOW = 1_800_000_000


def paid(days_ago_and_amount):
    return [(NOW - d * DAY, amount) for d, amount in days_ago_and_amount]


class DividendTests(unittest.TestCase):
    def test_quarterly_payer(self):
        history = paid([(10, 1.0), (101, 1.0), (192, 1.0), (283, 1.0), (374, 1.0), (465, 1.0), (556, 1.0), (647, 1.0)])
        p = dv.dividend_profile(history, 100.0, NOW)
        self.assertEqual(p["frequency"], "Quarterly")
        self.assertEqual(p["payments_per_year"], 4)
        self.assertEqual(p["ttm_count"], 4)
        self.assertAlmostEqual(p["ttm_amount"], 4.0)
        self.assertAlmostEqual(p["ttm_yield"], 0.04)
        self.assertAlmostEqual(p["forward_amount"], 4.0)
        self.assertAlmostEqual(p["forward_yield"], 0.04)
        self.assertAlmostEqual(p["last_pct_of_price"], 0.01)
        self.assertFalse(p["overdue"])
        self.assertAlmostEqual(p["growth"], 0.0)

    def test_growth_between_years(self):
        history = paid([(10, 1.2), (101, 1.2), (192, 1.2), (283, 1.2), (374, 1.0), (465, 1.0), (556, 1.0), (647, 1.0)])
        p = dv.dividend_profile(history, 100.0, NOW)
        self.assertAlmostEqual(p["growth"], 0.2)
        self.assertAlmostEqual(p["ttm_amount"], 4.8)

    def test_special_dividend_is_excluded_from_recurring(self):
        history = paid([(10, 1.0), (101, 1.0), (192, 10.0), (283, 1.0), (374, 1.0), (465, 1.0), (556, 1.0)])
        p = dv.dividend_profile(history, 100.0, NOW)
        self.assertEqual(p["frequency"], "Quarterly")
        self.assertAlmostEqual(p["ttm_amount"], 13.0)
        self.assertAlmostEqual(p["forward_amount"], 4.0)
        self.assertEqual(p["special_count"], 1)
        self.assertTrue(any(h["special"] for h in p["history"]))

    def test_uneven_interim_and_final_is_not_special(self):
        history = paid([(60, 22.0), (240, 8.0), (425, 22.0), (605, 8.0)])
        p = dv.dividend_profile(history, 1000.0, NOW)
        self.assertEqual(p["special_count"], 0)
        self.assertEqual(p["frequency"], "Half-yearly")
        self.assertAlmostEqual(p["forward_amount"], 30.0)
        self.assertAlmostEqual(p["forward_yield"], 0.03)

    def test_yearly_payer(self):
        p = dv.dividend_profile(paid([(100, 5.0), (465, 4.5)]), 100.0, NOW)
        self.assertEqual(p["frequency"], "Yearly")
        self.assertAlmostEqual(p["forward_amount"], 5.0)
        self.assertAlmostEqual(p["growth"], 5.0 / 4.5 - 1)

    def test_monthly_payer(self):
        history = paid([(5 + 30 * i, 0.5) for i in range(14)])
        p = dv.dividend_profile(history, 100.0, NOW)
        self.assertEqual(p["frequency"], "Monthly")
        self.assertEqual(p["payments_per_year"], 12)
        self.assertAlmostEqual(p["forward_amount"], 6.0)

    def test_overdue_payment_flagged(self):
        history = paid([(200, 1.0), (291, 1.0), (382, 1.0), (473, 1.0)])
        p = dv.dividend_profile(history, 100.0, NOW)
        self.assertEqual(p["frequency"], "Quarterly")
        self.assertTrue(p["overdue"])

    def test_single_payment(self):
        p = dv.dividend_profile(paid([(100, 2.0)]), 100.0, NOW)
        self.assertEqual(p["frequency"], "Single payment")
        self.assertIsNone(p["payments_per_year"])
        self.assertIsNone(p["forward_amount"])
        self.assertIsNone(p["forward_yield"])
        self.assertAlmostEqual(p["ttm_yield"], 0.02)

    def test_payment_older_than_a_year_gives_zero_ttm(self):
        p = dv.dividend_profile(paid([(500, 2.0)]), 100.0, NOW)
        self.assertEqual(p["ttm_count"], 0)
        self.assertEqual(p["ttm_yield"], 0.0)

    def test_nothing_recent_or_bad_input(self):
        self.assertIsNone(dv.dividend_profile([], 100.0, NOW))
        self.assertIsNone(dv.dividend_profile(paid([(900, 2.0)]), 100.0, NOW))
        self.assertIsNone(dv.dividend_profile(paid([(10, 2.0)]), 0, NOW))
        self.assertIsNone(dv.dividend_profile(paid([(10, 2.0)]), None, NOW))
        self.assertIsNone(dv.dividend_profile(paid([(10, 0.0), (20, -1.0)]), 100.0, NOW))

    def test_future_dated_payment_is_ignored(self):
        self.assertIsNone(dv.dividend_profile([(NOW + 10 * DAY, 2.0)], 100.0, NOW))

    def test_history_is_newest_first_and_capped(self):
        history = paid([(10 + 91 * i, 1.0) for i in range(8)])
        p = dv.dividend_profile(history, 100.0, NOW)
        self.assertLessEqual(len(p["history"]), dv.HISTORY_LENGTH)
        dates = [h["date"] for h in p["history"]]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_output_is_json_safe(self):
        history = paid([(10, 1.0), (101, 1.0), (192, 1.0), (283, 1.0), (374, 1.0)])
        json.dumps(dv.dividend_profile(history, 100.0, NOW), allow_nan=False)


if __name__ == "__main__":
    unittest.main()
