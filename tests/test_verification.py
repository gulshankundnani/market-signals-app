import unittest

from engine import verification as v

REF = {"source": "NSE end-of-day file (bhavcopy)", "dates": ["2026-11-12", "2026-11-13"], "closes": {"2026-11-12": 100.0, "2026-11-13": 102.0},
       "fetched_at": "2026-11-16T11:00:00+00:00"}


def rec(**over):
    base = {"symbol": "AAA.NS", "exchange": "NSE", "as_of": "2026-11-13", "price": 102.0, "previous_close": 100.0, "provisional": False,
            "generated_at": "2026-11-16T10:00:00+00:00"}
    base.update(over)
    return base


class PriceTests(unittest.TestCase):
    def test_a_matching_price_on_the_same_session_passes(self):
        row = v.verify_price(rec(), REF)
        self.assertEqual((row["status"], row["difference"], row["difference_pct"], row["reference_price"]), (v.PASSED, 0.0, 0.0, 102.0))
        self.assertEqual((row["trading_date"], row["exchange"], row["source"]), ("2026-11-13", "NSE", REF["source"]))

    def test_a_difference_over_the_tolerance_fails_and_is_reported(self):
        row = v.verify_price(rec(price=104.0), REF)
        self.assertEqual(row["status"], v.FAILED)
        self.assertAlmostEqual(row["difference"], 2.0)
        self.assertAlmostEqual(row["difference_pct"], 1.9608, places=4)

    def test_the_tolerance_edge(self):
        self.assertEqual(v.compare_value(100.5, 100.0)[0], v.PASSED)
        self.assertEqual(v.compare_value(100.51, 100.0)[0], v.FAILED)

    def test_a_session_without_a_published_reference_is_unverified_not_failed_or_passed(self):
        row = v.verify_price(rec(as_of="2026-11-16", price=105.0), REF)
        self.assertEqual(row["status"], v.UNVERIFIED)
        self.assertIsNone(row["reference_price"])
        self.assertIn("not published", row["reason"])

    def test_sessions_are_never_mixed(self):
        # the app's 16 Nov price must not be compared with the 13 Nov reference, however close they are
        row = v.verify_price(rec(as_of="2026-11-16", price=102.0), REF)
        self.assertEqual(row["status"], v.UNVERIFIED)
        self.assertEqual(row["trading_date"], "2026-11-16")

    def test_a_provisional_price_is_never_verified_against_an_end_of_day_figure(self):
        row = v.verify_price(rec(provisional=True), REF)
        self.assertEqual(row["status"], v.UNVERIFIED)
        self.assertIn("provisional", row["reason"])

    def test_the_previous_session_is_checked_on_its_own_date(self):
        row = v.verify_price(rec(), REF, "previous")
        self.assertEqual((row["trading_date"], row["app_price"], row["reference_price"], row["status"]), ("2026-11-12", 100.0, 100.0, v.PASSED))
        provisional = v.verify_price(rec(provisional=True, as_of="2026-11-16", previous_close=102.0), {**REF, "dates": ["2026-11-12", "2026-11-13"]}, "previous")
        self.assertEqual((provisional["trading_date"], provisional["status"]), ("2026-11-13", v.PASSED))   # completed session, verified separately

    def test_missing_inputs_never_pass(self):
        for app, ref in ((None, 1.0), (1.0, None), (1.0, 0), ("x", 1.0), (True, 1.0)):
            self.assertEqual(v.compare_value(app, ref)[0], v.UNVERIFIED)
        self.assertEqual(v.verify_price(rec(previous_close=None), REF, "previous")["status"], v.UNVERIFIED)
        self.assertEqual(v.verify_price(rec(), {**REF, "dates": ["2026-11-13"]}, "previous")["status"], v.UNVERIFIED)


class VolumeTests(unittest.TestCase):
    def test_identical_volumes_pass_with_a_ratio_of_one(self):
        row = v.verify_volume("A", "2026-11-13", 1000.0, 1000.0, "NSE")
        self.assertEqual((row["status"], row["ratio"]), (v.PASSED, 1.0))

    def test_any_difference_in_a_completed_session_fails(self):
        row = v.verify_volume("A", "2026-11-13", 900.0, 1000.0, "NSE")
        self.assertEqual((row["status"], row["ratio"]), (v.FAILED, 0.9))

    def test_unpublished_missing_and_provisional_volumes_are_unverified(self):
        self.assertEqual(v.verify_volume("A", "d", 1000.0, None, "NSE")["status"], v.UNVERIFIED)
        missing = v.verify_volume("A", "d", None, 1000.0, "NSE")
        self.assertEqual(missing["status"], v.UNVERIFIED)
        self.assertIn("missing, not zero", missing["reason"])
        self.assertEqual(v.verify_volume("A", "d", 1000.0, 1000.0, "NSE", provisional=True)["status"], v.UNVERIFIED)

    def test_a_genuine_zero_volume_is_a_value_not_a_gap(self):
        self.assertEqual(v.verify_volume("A", "d", 0.0, 0.0, "NSE")["status"], v.PASSED)
        self.assertEqual(v.verify_volume("A", "d", 0.0, 500.0, "NSE")["status"], v.FAILED)


class SummaryTests(unittest.TestCase):
    def test_counts_and_largest_difference(self):
        rows = [v.verify_price(rec(), REF), v.verify_price(rec(price=104.0), REF), v.verify_price(rec(as_of="2026-11-16"), REF)]
        out = v.summarise(rows)
        self.assertEqual((out[v.PASSED], out[v.FAILED], out[v.UNVERIFIED]), (1, 1, 1))
        self.assertAlmostEqual(out["largest_difference_pct"], 1.9608, places=4)


if __name__ == "__main__":
    unittest.main()
