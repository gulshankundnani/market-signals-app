import datetime
import unittest

from engine import exchange, verify


def bars(pairs):
    return {"d": [d for d, _ in pairs], "c": [c for _, c in pairs]}


class CompareTests(unittest.TestCase):
    def test_matching_closes_are_a_match(self):
        result = verify.compare(bars([("d1", 100.0), ("d2", 101.0), ("d3", 102.0)]), {"d1": 100.0, "d2": 101.2, "d3": 102.0})
        self.assertEqual((result["status"], result["n"], result["last_date"]), ("match", 3, "d3"))
        self.assertAlmostEqual(result["max_diff_pct"], 0.198, places=2)

    def test_small_and_large_differences_are_told_apart(self):
        minor = verify.compare(bars([("d1", 101.0)]), {"d1": 100.0})
        major = verify.compare(bars([("d1", 110.0)]), {"d1": 100.0})
        self.assertEqual((minor["status"], major["status"]), ("minor", "mismatch"))

    def test_one_bad_day_among_good_ones_is_flagged(self):
        result = verify.compare(bars([("d1", 100.0), ("d2", 150.0), ("d3", 100.0)]), {"d1": 100.0, "d2": 100.0, "d3": 100.0})
        self.assertEqual(result["status"], "mismatch")
        self.assertEqual(result["max_diff_pct"], 50.0)

    def test_only_dates_both_sources_have_are_compared_and_none_means_unverified(self):
        result = verify.compare(bars([("d1", 100.0), ("d9", 1.0)]), {"d1": 100.0, "d2": 5.0})
        self.assertEqual((result["n"], result["status"]), (1, "match"))
        self.assertIsNone(verify.compare(bars([("d1", 100.0)]), {"d5": 100.0}))
        self.assertIsNone(verify.compare(bars([("d1", 100.0)]), {}))

    def test_only_the_latest_sessions_are_compared(self):
        old = [(f"d{n:02d}", 100.0) for n in range(20)]
        official = {d: 100.0 for d, _ in old}
        official["d00"] = 1.0                      # far in the past: outside the window
        self.assertEqual(verify.compare(bars(old), official, recent=5)["status"], "match")

    def test_a_zero_official_close_is_ignored_not_divided_by(self):
        self.assertIsNone(verify.compare(bars([("d1", 100.0)]), {"d1": 0}))


class SummariseTests(unittest.TestCase):
    def test_counts_agreement_and_the_worst_stocks(self):
        results = {"A": {"status": "match", "max_diff_pct": 0.0, "median_diff_pct": 0.0, "last_date": "d2"},
                   "B": {"status": "match", "max_diff_pct": 0.1, "median_diff_pct": 0.05, "last_date": "d2"},
                   "C": {"status": "mismatch", "max_diff_pct": 9.0, "median_diff_pct": 4.0, "last_date": "d1"},
                   "D": None}
        s = verify.summarise(results, "IN")
        self.assertEqual((s["stocks"], s["verified"], s["unverified"], s["match"], s["mismatch"]), (4, 3, 1, 2, 1))
        self.assertEqual(s["agreement_pct"], 66.7)
        self.assertEqual((s["as_of"], s["max_diff_pct"]), ("d2", 9.0))
        self.assertEqual(s["worst"], [{"symbol": "C", "max_diff_pct": 9.0, "status": "mismatch"}])
        self.assertEqual(s["source"], "NSE end-of-day file")

    def test_nothing_verified_gives_no_percentages(self):
        s = verify.summarise({"A": None}, "US")
        self.assertEqual((s["verified"], s["agreement_pct"], s["median_diff_pct"], s["as_of"]), (0, None, None, None))


class CrossCheckTests(unittest.TestCase):
    def test_each_market_uses_its_own_source_and_carried_or_unknown_stocks_are_skipped(self):
        records = [{"symbol": "AAA.NS", "market": "IN"}, {"symbol": "BBB.NS", "market": "IN"}, {"symbol": "UUU", "market": "US"},
                   {"symbol": "OLD.NS", "market": "IN", "carried": True}, {"symbol": "ZZZ", "market": "US"}]
        all_bars = {s: bars([("d1", 100.0), ("d2", 100.0)]) for s in ("AAA.NS", "BBB.NS", "UUU", "OLD.NS", "ZZZ")}
        india = {"d1": {"AAA": 100.0, "BBB": 150.0}, "d2": {"AAA": 100.0, "BBB": 150.0}}
        us = {"UUU": {"d1": 100.0, "d2": 100.0}}
        per_symbol, summary = verify.cross_check(records, all_bars, india, us)
        self.assertEqual(per_symbol["AAA.NS"]["status"], "match")
        self.assertEqual(per_symbol["BBB.NS"]["status"], "mismatch")
        self.assertEqual(per_symbol["UUU"]["source"], "Cboe delayed-quote history")
        self.assertNotIn("OLD.NS", per_symbol)
        self.assertNotIn("ZZZ", per_symbol)
        self.assertEqual((summary["IN"]["verified"], summary["IN"]["mismatch"]), (2, 1))
        self.assertEqual((summary["US"]["stocks"], summary["US"]["verified"], summary["US"]["unverified"]), (2, 1, 1))

    def test_trusted_drops_only_the_disputed_stocks(self):
        per_symbol = {"A": {"status": "match"}, "B": {"status": "mismatch"}, "C": {"status": "minor"}}
        self.assertEqual(verify.trusted(["A", "B", "C", "D"], per_symbol), ["A", "C", "D"])


class CboeTests(unittest.TestCase):
    def test_parsing_keeps_the_latest_sessions_and_skips_bad_rows(self):
        rows = [{"date": f"2026-11-{n:02d}", "close": 100 + n} for n in range(1, 21)]
        rows += [{"date": "2026-11-21", "close": 0}, {"date": "2026-11-22"}, {"close": 5}, {"date": "2026-11-23", "close": "x"}]
        out = verify.parse_cboe_history({"data": rows}, keep=24)
        self.assertEqual(len(out), 20)
        self.assertEqual(out["2026-11-20"], 120.0)
        self.assertEqual(verify.parse_cboe_history({"data": rows}, keep=10), {f"2026-11-{n:02d}": 100.0 + n for n in range(11, 21)})
        for junk in (None, {}, {"data": None}, [], "x"):
            self.assertEqual(verify.parse_cboe_history(junk), {})

    def test_the_symbol_form_matches_cboes(self):
        self.assertEqual(verify._cboe_symbol("BRK-B"), "BRK.B")
        self.assertEqual(verify._cboe_symbol("aapl"), "AAPL")

    def test_each_stock_is_fetched_once_a_day_gently_and_in_batches(self):
        calls, pauses = [], []

        def fetcher(symbol):
            calls.append(symbol)
            return {"2026-11-12": 1.0} if symbol != "BAD" else {}

        symbols = ["AAA", "BAD", "CCC", "DDD", "EEE"]
        first = verify.sync_us({}, "2026-11-16", symbols, fetcher, per_run=3, sleep=pauses.append)
        self.assertEqual(calls, ["AAA", "BAD", "CCC"], "at most per_run stocks in one run")
        self.assertEqual(len(pauses), 2, "a pause between requests, none before the first")
        self.assertEqual(sorted(first["closes"]), ["AAA", "CCC"])
        self.assertEqual(first["no_data"], ["BAD"])
        second = verify.sync_us(first, "2026-11-16", symbols, fetcher, per_run=3, sleep=pauses.append)
        self.assertEqual(calls[3:], ["DDD", "EEE"], "the next run picks up the rest")
        self.assertEqual(sorted(second["closes"]), ["AAA", "CCC", "DDD", "EEE"])
        again = verify.sync_us(second, "2026-11-16", symbols, fetcher, sleep=pauses.append)
        self.assertEqual((again, len(calls)), (second, 5), "nothing more to fetch the same day")

    def test_a_rate_limit_stops_the_round_at_once_and_keeps_what_was_gathered(self):
        calls = []

        def fetcher(symbol):
            calls.append(symbol)
            if symbol == "CCC":
                raise verify.RateLimited("429")
            return {"2026-11-12": 1.0}

        out = verify.sync_us({}, "2026-11-16", ["AAA", "BBB", "CCC", "DDD"], fetcher, sleep=lambda s: None)
        self.assertEqual(calls, ["AAA", "BBB", "CCC"], "no request after the limit")
        self.assertEqual(sorted(out["closes"]), ["AAA", "BBB"])
        self.assertNotIn("CCC", out["refreshed"])
        nothing = verify.sync_us({}, "2026-11-16", ["AAA"], lambda s: (_ for _ in ()).throw(verify.RateLimited("429")), sleep=lambda s: None)
        self.assertEqual(nothing, {})

    def test_yesterdays_history_is_used_until_a_stock_is_refreshed(self):
        old = {"day": "2026-11-15", "closes": {"AAA": {"2026-11-12": 1.0}}, "no_data": [], "refreshed": ["AAA"]}
        out = verify.sync_us(old, "2026-11-16", ["AAA"], lambda s: (_ for _ in ()).throw(verify.RateLimited("429")), sleep=lambda s: None)
        self.assertEqual(out, old, "rate limited before anything new: the old copy stays, so there is no gap")
        out = verify.sync_us(old, "2026-11-16", ["AAA"], lambda s: {"2026-11-13": 2.0}, sleep=lambda s: None)
        self.assertEqual(out["closes"]["AAA"], {"2026-11-13": 2.0})
        self.assertEqual(out["day"], "2026-11-16")

    def test_junk_in_the_saved_copy_is_ignored(self):
        for junk in (None, "x", [], {"closes": None, "no_data": None, "refreshed": None}):
            out = verify.sync_us(junk, "2026-11-16", ["AAA"], lambda s: {"d": 1.0}, sleep=lambda s: None)
            self.assertEqual(out["closes"], {"AAA": {"d": 1.0}})


class NseClosesTests(unittest.TestCase):
    CSV = ("SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER\n"
           "AAA, EQ, 13-Nov-2026, 10.0, 10.0, 11.0, 9.0, 10.5, 10.4, 10.2, 1000, 1.0, 10, 500, 50.0\n"
           "BBB, EQ, 13-Nov-2026, 20.0, 20.0, 21.0, 19.0, 20.5, 20.4, 20.2, 1000, 1.0, 10, 500, 50.0\n"
           "CCC, BE, 13-Nov-2026, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 10, 1.0, 1, -, -\n")

    def test_the_closing_price_is_read_from_the_file(self):
        iso, rows = exchange.parse_bhavcopy(self.CSV)
        self.assertEqual(iso, "2026-11-13")
        self.assertEqual(rows["AAA"]["close"], 10.4)
        self.assertNotIn("CCC", rows)

    def test_only_missing_recent_days_are_fetched_and_only_tracked_symbols_kept(self):
        fetched = []

        def fetcher(day):
            fetched.append(day)
            if day.weekday() >= 5:
                return None
            iso = day.strftime("%Y-%m-%d")
            return iso, {"AAA": {"close": 10.0 + day.day}, "ZZZ": {"close": 1.0}, "NOCLOSE": {"close": None}}

        today = datetime.date(2026, 11, 16)
        history = exchange.sync_closes({}, today, ["AAA", "NOCLOSE"], fetcher, want=3)
        self.assertEqual(sorted(history), ["2026-11-12", "2026-11-13", "2026-11-16"])
        self.assertEqual(history["2026-11-13"], {"AAA": 23.0})
        count = len(fetched)
        history = exchange.sync_closes(history, today, ["AAA"], fetcher, want=3)
        self.assertEqual(len(fetched), count, "nothing new to fetch")

    def test_junk_in_the_saved_history_is_ignored(self):
        out = exchange.sync_closes({"2026-11-13": "x", "2026-11-12": {"AAA": 1.0}}, datetime.date(2026, 11, 16), ["AAA"], lambda d: None, want=1)
        self.assertEqual(out, {"2026-11-12": {"AAA": 1.0}})


if __name__ == "__main__":
    unittest.main()
