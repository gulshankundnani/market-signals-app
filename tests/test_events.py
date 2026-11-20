import time
import unittest
from datetime import datetime, timezone
from unittest import mock

from engine import build, events, fetch, fundamentals

TODAY = "2026-11-16"


def rec(symbol="AAA.NS", calendar=None, dividend=None, name="Aaa Ltd", market="IN"):
    return {"symbol": symbol, "name": name, "market": market, "currency": "INR",
            "fundamentals": {"events": calendar if calendar is not None else {}}, "dividend": dividend}


def ts(iso):
    return int(datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp())


class ParseEventsTests(unittest.TestCase):
    def test_dates_are_read_from_yahoos_calendar(self):
        result = {"calendarEvents": {"earnings": {"earningsDate": [{"raw": ts("2027-01-15")}, {"raw": ts("2027-01-19")}]},
                                     "exDividendDate": {"raw": ts("2026-12-01")}, "dividendDate": {"raw": ts("2026-12-15")}}}
        self.assertEqual(fundamentals.parse_events(result), {"earnings": ["2027-01-15", "2027-01-19"], "ex_dividend": "2026-12-01", "dividend_pay": "2026-12-15"})

    def test_missing_or_junk_values_give_empty_dates(self):
        empty = {"earnings": [], "ex_dividend": None, "dividend_pay": None}
        for junk in (None, {}, {"calendarEvents": None}, {"calendarEvents": {"earnings": None}},
                     {"calendarEvents": {"earnings": {"earningsDate": [{"raw": True}, {"raw": 0}, {}, None, "x"]}, "exDividendDate": {"raw": -5}}}):
            self.assertEqual(fundamentals.parse_events(junk), empty)

    def test_summary_carries_the_events_and_asks_yahoo_for_them(self):
        self.assertIn("calendarEvents", fundamentals.MODULES)
        out = fundamentals.parse_summary({"quoteSummary": {"result": [{"calendarEvents": {"exDividendDate": {"raw": ts("2026-12-01")}}}]}})
        self.assertEqual(out["events"]["ex_dividend"], "2026-12-01")


class SplitsTests(unittest.TestCase):
    def payload(self, splits):
        bars = [100.0 + i for i in range(80)]
        quote = {"open": bars, "high": bars, "low": bars, "close": bars, "volume": [1000] * 80}
        return {"chart": {"result": [{"meta": {"gmtoffset": 0}, "timestamp": [1_700_000_000 + i * 86400 for i in range(80)],
                                      "indicators": {"quote": [quote]}, "events": {"splits": splits}}]}}

    def test_splits_are_read_from_the_price_history(self):
        chart = fetch.clean_chart(self.payload({"1": {"date": 1_700_500_000, "numerator": 1, "denominator": 10, "splitRatio": "1:10"},
                                                "2": {"date": 1_690_000_000, "numerator": 2, "denominator": 1}}), "A")
        self.assertEqual(chart["splits"], [(1_690_000_000, 2.0, 1.0), (1_700_500_000, 1.0, 10.0)])

    def test_bad_split_rows_are_skipped(self):
        chart = fetch.clean_chart(self.payload({"1": {"date": 1, "numerator": 0, "denominator": 1}, "2": {"numerator": 1}, "3": "x", "4": None}), "A")
        self.assertEqual(chart["splits"], [])
        self.assertEqual(fetch.clean_chart(self.payload(None), "A")["splits"], [])


class StockEventsTests(unittest.TestCase):
    def kinds(self, record):
        return [(e["type"], e["date"], e["confidence"]) for e in events.stock_events(record, TODAY)]

    def test_future_results_and_dividend_dates_become_events_with_their_source(self):
        record = rec(calendar={"earnings": ["2026-11-20"], "ex_dividend": "2026-12-01", "dividend_pay": "2026-12-15"},
                     dividend={"last_amount": 5.0})
        found = events.stock_events(record, TODAY)
        self.assertEqual([(e["type"], e["date"]) for e in found], [("earnings", "2026-11-20"), ("ex_dividend", "2026-12-01"), ("dividend_payment", "2026-12-15")])
        self.assertTrue(all(e["source"] == "Yahoo Finance" and e["confidence"] == "unconfirmed" for e in found))
        self.assertEqual(found[1]["amount"], 5.0)
        self.assertEqual((found[0]["symbol"], found[0]["name"], found[0]["market"]), ("AAA.NS", "Aaa Ltd", "IN"))

    def test_past_dates_are_never_shown(self):
        record = rec(calendar={"earnings": ["2026-10-08"], "ex_dividend": "2026-11-03", "dividend_pay": "2026-11-15"})
        self.assertEqual(events.stock_events(record, TODAY), [])

    def test_today_counts_as_upcoming(self):
        self.assertEqual(self.kinds(rec(calendar={"earnings": [TODAY]})), [("earnings", TODAY, "unconfirmed")])

    def test_a_results_window_is_shown_from_its_start_with_its_end(self):
        [e] = events.stock_events(rec(calendar={"earnings": ["2026-11-18", "2026-11-22"]}), TODAY)
        self.assertEqual((e["date"], e["until"]), ("2026-11-18", "2026-11-22"))
        self.assertIn("window from 2026-11-18 to 2026-11-22", e["detail"])
        [e] = events.stock_events(rec(calendar={"earnings": ["2026-11-10", "2026-11-20"]}), TODAY)
        self.assertEqual((e["date"], e["until"]), (TODAY, "2026-11-20"), "a window that has already begun is shown from today")

    def test_dates_beyond_the_horizon_are_left_out(self):
        self.assertEqual(events.stock_events(rec(calendar={"earnings": ["2027-06-01"], "ex_dividend": "2027-06-01"}), TODAY), [])

    def test_with_no_ex_dividend_date_the_next_one_is_estimated_from_the_payment_pattern(self):
        quarterly = {"payments_per_year": 4, "frequency": "Quarterly", "last_date": "2026-09-20", "last_amount": 2.0, "overdue": False}
        found = events.stock_events(rec(dividend=quarterly), TODAY)
        self.assertEqual([(e["type"], e["date"], e["confidence"], e["source"]) for e in found],
                         [("ex_dividend", "2026-12-20", "estimated", events.ESTIMATE_SOURCE)])
        self.assertIn("Quarterly payer", found[0]["detail"])

    def test_estimates_are_not_made_for_overdue_far_off_or_irregular_payers(self):
        base = {"payments_per_year": 4, "frequency": "Quarterly", "last_date": "2026-09-20", "last_amount": 2.0, "overdue": False}
        self.assertEqual(events.stock_events(rec(dividend={**base, "overdue": True}), TODAY), [])
        self.assertEqual(events.stock_events(rec(dividend={**base, "payments_per_year": 1, "last_date": "2026-09-20"}), TODAY), [], "next one is far off")
        self.assertEqual(events.stock_events(rec(dividend={**base, "payments_per_year": None}), TODAY), [])
        self.assertEqual(events.stock_events(rec(dividend={**base, "last_date": "2026-01-01"}), TODAY), [], "estimate already in the past")

    def test_the_estimate_uses_the_same_week_a_year_after_an_earlier_payment(self):
        history = [{"date": "2025-12-12", "amount": 0.2}, {"date": "2026-03-13", "amount": 0.2}, {"date": "2026-05-08", "amount": 0.2},
                   {"date": "2026-08-21", "amount": 0.2}]
        profile = {"payments_per_year": 4, "frequency": "Quarterly", "last_date": "2026-08-21", "last_amount": 0.2, "overdue": False, "history": history}
        self.assertEqual(events.estimate_ex_dividend(profile, TODAY), "2026-12-11", "not 2026-11-20, three months after the last payment")
        found = events.stock_events(rec(dividend=profile), TODAY)
        self.assertEqual([(e["date"], e["confidence"]) for e in found], [("2026-12-11", "estimated")])

    def test_special_payments_are_not_used_as_the_pattern_and_there_is_a_fallback_without_history(self):
        profile = {"payments_per_year": 1, "frequency": "Yearly", "last_date": "2026-03-01", "last_amount": 1.0, "overdue": False,
                   "history": [{"date": "2025-12-05", "amount": 9.0, "special": True}]}
        self.assertEqual(events.estimate_ex_dividend(profile, TODAY), "2027-03-01", "the special payment is ignored; the gap rule applies")
        self.assertIsNone(events.estimate_ex_dividend({}, TODAY))
        self.assertIsNone(events.estimate_ex_dividend({"last_date": "2026-03-01"}, TODAY))

    def test_the_estimate_is_checked_against_dates_yahoo_does_give(self):
        history = [{"date": "2025-12-05", "amount": 1.0}, {"date": "2026-03-06", "amount": 1.0}]
        profile = {"payments_per_year": 4, "frequency": "Quarterly", "last_date": "2026-09-05", "last_amount": 1.0, "overdue": False, "history": history}
        close = rec("A", {"ex_dividend": "2026-12-07"}, dividend=profile)
        far = rec("B", {"ex_dividend": "2027-01-20"}, dividend=profile)
        check = events.estimate_check([close, far, rec("C", {}, dividend=profile)], TODAY)
        self.assertEqual(check["n"], 2)
        self.assertEqual(check["median_abs_days"], (3 + 47) / 2)
        self.assertEqual(check["within_7_days_pct"], 50)
        self.assertIsNone(events.estimate_check([rec("C", {}, dividend=profile)], TODAY))

    def test_a_real_ex_dividend_date_replaces_the_estimate(self):
        quarterly = {"payments_per_year": 4, "frequency": "Quarterly", "last_date": "2026-09-20", "last_amount": 2.0, "overdue": False}
        found = events.stock_events(rec(calendar={"ex_dividend": "2026-12-03"}, dividend=quarterly), TODAY)
        self.assertEqual([(e["date"], e["confidence"]) for e in found], [("2026-12-03", "unconfirmed")])

    def test_stocks_without_any_data_give_nothing_and_junk_is_ignored(self):
        self.assertEqual(events.stock_events({"symbol": "X", "name": "X", "market": "US"}, TODAY), [])
        junk = rec(calendar={"earnings": [None, 5, "2026-11-20"], "ex_dividend": 7, "dividend_pay": ["x"]})
        self.assertEqual(self.kinds(junk), [("earnings", "2026-11-20", "unconfirmed")])


class BuildTests(unittest.TestCase):
    def test_all_stocks_are_listed_soonest_first_with_counts(self):
        records = [rec("B.NS", {"earnings": ["2026-12-01"], "ex_dividend": "2026-11-20"}, name="Bbb"), rec("A.NS", {"earnings": ["2026-11-20"]}, name="Aaa")]
        out = events.build(records, TODAY)
        self.assertEqual([(e["date"], e["type"], e["name"]) for e in out["items"]],
                         [("2026-11-20", "earnings", "Aaa"), ("2026-11-20", "ex_dividend", "Bbb"), ("2026-12-01", "earnings", "Bbb")])
        self.assertEqual(out["counts"], {"earnings": 2, "ex_dividend": 1})
        self.assertEqual(out["as_of"], TODAY)
        empty = events.build([], TODAY)
        self.assertEqual({k: empty[k] for k in ("as_of", "items", "announcements", "counts", "estimate_check", "news_check")},
                         {"as_of": TODAY, "items": [], "announcements": [], "counts": {}, "estimate_check": None, "news_check": None})
        self.assertEqual((empty["official"]["ok"], empty["official"]["confirmed"], empty["official"]["unverified"]), (False, 0, 0))


class CachedFundamentalsTests(unittest.TestCase):
    def test_a_cached_record_without_events_is_fetched_again_but_a_complete_one_is_reused(self):
        now = int(time.time())
        records = [{"symbol": "OLD"}, {"symbol": "NEW"}]
        state = {"fundamentals": {"OLD": {"ts": now, "data": {"pe": 10}}, "NEW": {"ts": now, "data": {"pe": 11, "events": {"earnings": []}, "v": fundamentals.VERSION}}}}
        session = mock.MagicMock()
        session.start.return_value = True
        with mock.patch.object(fundamentals, "fetch_fundamentals", return_value={"pe": 12, "events": {"earnings": ["2026-12-01"]}}) as fetched, \
                mock.patch.object(build.time, "sleep"):
            cache = build.fill_fundamentals(records, state, now, lambda: session)
        self.assertEqual([c.args[1] for c in fetched.call_args_list], ["OLD"])
        self.assertEqual(records[0]["fundamentals"]["events"]["earnings"], ["2026-12-01"])
        self.assertEqual(records[1]["fundamentals"]["pe"], 11)
        self.assertIn("events", cache["OLD"]["data"])


if __name__ == "__main__":
    unittest.main()
