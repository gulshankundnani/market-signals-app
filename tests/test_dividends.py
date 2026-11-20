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


class RealPatternTests(unittest.TestCase):
    """Payment patterns of real companies that the first version got wrong (dates as days before NOW)."""

    def profile(self, days_and_amounts, price=1000.0):
        return dv.dividend_profile(paid(days_and_amounts), price, NOW)

    def test_a_big_yearly_final_dividend_is_regular_not_special(self):
        # interim 1.0 and 0.5, final 6.5 and 5.0 (GAIL-like): the final repeats every year
        p = self.profile([(80, 0.5), (290, 5.0), (470, 1.0), (640, 6.5)])
        self.assertEqual(p["special_count"], 0)
        self.assertEqual((p["frequency"], p["payments_per_year"]), ("Half-yearly", 2))
        self.assertAlmostEqual(p["forward_amount"], 5.5)
        self.assertAlmostEqual(p["ttm_amount"], 5.5)

    def test_uneven_interim_and_final_pay_twice_a_year_not_quarterly(self):
        # two payments a year, 87 and 281 days apart (Bajaj Holdings-like): the median gap would say quarterly
        p = self.profile([(60, 65.0), (143, 130.0), (425, 65.0), (510, 28.0)])
        self.assertEqual((p["frequency"], p["payments_per_year"], p["rhythm"]), ("Half-yearly", 2, "uneven"))
        self.assertAlmostEqual(p["forward_amount"], 195.0)       # the last two payments, not four spanning two years

    def test_one_dividend_paid_in_two_parts_counts_once(self):
        # 11.0 then 2.5 four weeks later, then 13.0 a year later (HDFC Bank-like)
        p = self.profile([(150, 13.0), (500, 2.5), (528, 11.0)])
        self.assertEqual((p["frequency"], p["payments_per_year"]), ("Yearly", 1))
        self.assertAlmostEqual(p["forward_amount"], 13.0)

    def test_a_raised_dividend_that_stays_raised_is_not_special(self):
        history = [(88, 0.25), (178, 0.25), (270, 0.01), (365 + 20, 0.01), (365 + 110, 0.01), (365 + 200, 0.01)]
        p = self.profile(history, price=100.0)
        self.assertEqual(p["special_count"], 0)

    def test_a_lone_one_off_is_still_special_and_kept_out_of_the_recurring_figure(self):
        p = self.profile([(30, 1.0), (120, 1.0), (210, 9.0), (300, 1.0), (390, 1.0), (480, 1.0)])
        self.assertEqual(p["special_count"], 1)
        self.assertAlmostEqual(p["ttm_amount"], 12.0)
        self.assertAlmostEqual(p["ttm_regular_amount"], 3.0)
        self.assertAlmostEqual(p["ttm_regular_yield"], 0.003)

    def test_the_payments_behind_the_figures_are_listed(self):
        p = self.profile([(30, 1.0), (120, 1.0), (210, 1.0), (300, 1.0)], price=100.0)
        self.assertEqual(len(p["ttm_payments"]), 4)
        self.assertAlmostEqual(sum(x["amount"] for x in p["ttm_payments"]), p["ttm_amount"])
        self.assertEqual(len(p["forward_payments"]), 4)


class CrossCheckTests(unittest.TestCase):
    def profile(self):
        return dv.dividend_profile(paid([(30, 1.0), (120, 1.0), (210, 1.0), (300, 1.0)]), 100.0, NOW)

    def test_agrees_when_within_fifteen_percent(self):
        out = dv.cross_check(self.profile(), 100.0, 4.4)
        self.assertEqual(out["status"], "agrees")
        self.assertAlmostEqual(out["yield"], 0.044)

    def test_differs_when_far_apart_and_says_why(self):
        out = dv.cross_check(self.profile(), 100.0, 8.0)
        self.assertEqual(out["status"], "differs")
        self.assertIn("actually made", out["note"])

    def test_yahoo_rate_but_no_payment_here(self):
        self.assertEqual(dv.cross_check(None, 100.0, 2.0)["status"], "not_paid")

    def test_no_yahoo_rate_means_no_check(self):
        self.assertIsNone(dv.cross_check(self.profile(), 100.0, None))
        self.assertIsNone(dv.cross_check(self.profile(), 100.0, 0))
