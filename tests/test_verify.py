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
        self.assertIsNone(verify.compare(bars([("d9", 100.0)]), {"d5": 100.0}))
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
        s = verify.summarise({"A": None}, "IN")
        self.assertEqual((s["verified"], s["agreement_pct"], s["median_diff_pct"], s["as_of"]), (0, None, None, None))


class CrossCheckTests(unittest.TestCase):
    def test_each_market_uses_its_own_source_and_carried_or_unknown_stocks_are_skipped(self):
        records = [{"symbol": "AAA.NS", "market": "IN"}, {"symbol": "BBB.NS", "market": "IN"},
                   {"symbol": "OLD.NS", "market": "IN", "carried": True}, {"symbol": "ZZZ", "market": "US"}]
        all_bars = {s: bars([("d1", 100.0), ("d2", 100.0)]) for s in ("AAA.NS", "BBB.NS", "OLD.NS", "ZZZ")}
        india = {"d1": {"AAA": 100.0, "BBB": 150.0}, "d2": {"AAA": 100.0, "BBB": 150.0}}
        per_symbol, summary = verify.cross_check(records, all_bars, india)
        self.assertEqual(per_symbol["AAA.NS"]["status"], "match")
        self.assertEqual(per_symbol["BBB.NS"]["status"], "mismatch")
        self.assertNotIn("OLD.NS", per_symbol)
        self.assertNotIn("ZZZ", per_symbol)
        self.assertEqual((summary["IN"]["verified"], summary["IN"]["mismatch"]), (2, 1))
        self.assertNotIn("US", summary)

    def test_trusted_drops_only_the_disputed_stocks(self):
        per_symbol = {"A": {"status": "match"}, "B": {"status": "mismatch"}, "C": {"status": "minor"}}
        self.assertEqual(verify.trusted(["A", "B", "C", "D"], per_symbol), ["A", "C", "D"])




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


class BehindTests(unittest.TestCase):
    def test_a_series_older_than_the_official_file_is_behind(self):
        result = verify.compare(bars([("d1", 100.0), ("d2", 101.0)]), {"d1": 100.0, "d2": 101.0, "d3": 102.0})
        self.assertEqual((result["status"], result["official_last"], result["yahoo_last"]), ("behind", "d3", "d2"))

    def test_behind_series_have_no_alerts_and_no_calls(self):
        self.assertEqual(verify.trusted(["A", "B", "C"], {"A": {"status": "behind"}, "B": {"status": "match"}}), ["B", "C"])
        from engine import tracking
        item = {"kind": "signal", "symbol": "A", "horizon": "short", "side": "buy", "announced": "x"}
        self.assertEqual(tracking.make_calls([item], {"A": {"symbol": "A", "quality": {"status": "behind"}}}, 1), [])


class FrozenHistoryTests(unittest.TestCase):
    def test_a_full_history_still_picks_up_newer_sessions(self):
        from datetime import date
        from engine import exchange
        held = {f"2026-11-{d:02d}": {"A": 1.0} for d in (6, 9, 10, 11, 12, 13)}
        fetched = []
        def fetcher(day):
            fetched.append(day.isoformat())
            return (day.isoformat(), {"A": {"close": 2.0}}) if day.isoformat() in ("2026-11-16", "2026-11-17") else None
        out = exchange.sync_closes(held, date(2026, 11, 18), ["A"], fetcher=fetcher)
        self.assertIn("2026-11-17", out)
        self.assertIn("2026-11-16", out)
        self.assertNotIn("2026-11-05", fetched)


class UdiffTests(unittest.TestCase):
    TEXT = ("TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol\n"
            "2026-11-19,2026-11-19,CM,NSE,STK,1,INE1,TCS,EQ,2367.00,2385.60,2327.70,2342.70,2471096\n"
            "2026-11-19,2026-11-19,CM,NSE,STK,2,INE2,SGBJUN28,GB,1,1,1,13039.87,743\n"
            "2026-11-19,2026-11-19,CM,NSE,STK,3,INE3,BAD,EQ,1,1,1,,5\n")

    def test_only_eq_rows_with_a_close_are_read(self):
        from engine import exchange
        day, rows = exchange.parse_udiff(self.TEXT)
        self.assertEqual(day, "2026-11-19")
        self.assertEqual(list(rows), ["TCS"])
        self.assertEqual((rows["TCS"]["close"], rows["TCS"]["volume"]), (2342.70, 2471096.0))
        self.assertEqual(exchange.parse_udiff(""), (None, {}))

    def test_the_full_bhavcopy_is_the_fallback(self):
        from datetime import date
        from unittest import mock
        from engine import exchange
        with mock.patch.object(exchange, "fetch_udiff", return_value=None), mock.patch.object(exchange, "fetch_bhavcopy", return_value=("d", {"A": {}})) as full:
            self.assertEqual(exchange.fetch_eod(date(2026, 11, 19)), ("d", {"A": {}}))
            full.assert_called_once()
