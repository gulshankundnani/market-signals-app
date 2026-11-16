import unittest

from engine import build, market


def bars(closes, spread=0.01, date_start=0, last_date=None):
    n = len(closes)
    dates = [f"d{date_start + i:04d}" for i in range(n)]
    if last_date:
        dates[-1] = last_date
    return {"d": dates, "c": list(closes), "h": [c * (1 + spread) for c in closes], "l": [c * (1 - spread) for c in closes]}


def rising(n=260, start=100.0, step=0.5):
    return [start + step * i for i in range(n)]


def falling(n=260, start=300.0, step=0.5):
    return [start - step * i for i in range(n)]


class AdxTests(unittest.TestCase):
    def test_a_steady_climb_is_a_strong_up_trend(self):
        b = bars(rising())
        value, plus, minus = market.adx(b["h"], b["l"], b["c"])
        self.assertGreater(value, 40)
        self.assertGreater(plus, minus)

    def test_a_steady_fall_points_down(self):
        b = bars(falling())
        value, plus, minus = market.adx(b["h"], b["l"], b["c"])
        self.assertGreater(value, 40)
        self.assertGreater(minus, plus)

    def test_a_flat_zigzag_has_a_weak_trend(self):
        closes = [100 + (1 if i % 2 else -1) for i in range(120)]
        b = bars(closes, spread=0.02)
        self.assertLess(market.adx(b["h"], b["l"], b["c"])[0], 20)

    def test_too_little_history_or_no_range_gives_none(self):
        b = bars(rising(20))
        self.assertIsNone(market.adx(b["h"], b["l"], b["c"]))
        flat = bars([100.0] * 60, spread=0)
        self.assertIsNone(market.adx(flat["h"], flat["l"], flat["c"]))


class RegimeTests(unittest.TestCase):
    def test_a_clean_up_trend_with_calm_vix_and_good_breadth_is_strongly_bullish(self):
        out = market.regime(bars(rising()), [14.0] * 10, 80, 100)
        self.assertEqual(out["score"], 7)
        self.assertEqual(out["label"], "Strongly bullish conditions")
        self.assertEqual(len(out["checks"]), 7)
        self.assertTrue(all(c["score"] in (-1, 0, 1) for c in out["checks"]))

    def test_a_clean_down_trend_is_strongly_bearish(self):
        out = market.regime(bars(falling()), [30.0] * 10, 20, 100)
        self.assertEqual(out["score"], -7)
        self.assertEqual(out["label"], "Strongly bearish conditions")

    def test_missing_vix_and_breadth_count_as_zero_and_say_so(self):
        out = market.regime(bars(rising()), None, None, 0)
        by_key = {c["key"]: c for c in out["checks"]}
        self.assertEqual((by_key["vix"]["score"], by_key["breadth"]["score"]), (0, 0))
        self.assertIn("unavailable", by_key["vix"]["detail"])
        self.assertEqual(out["score"], 5)

    def test_a_vix_jump_is_negative_even_when_the_level_is_modest(self):
        out = market.regime(bars(rising()), [12.0] * 6 + [14.5], 70, 50)
        vix = next(c for c in out["checks"] if c["key"] == "vix")
        self.assertEqual(vix["score"], -1)

    def test_too_short_an_index_history_gives_none(self):
        self.assertIsNone(market.regime(bars(rising(100)), [14] * 10, 50, 10))
        self.assertIsNone(market.regime(None, None, None, 0))

    def test_label_boundaries(self):
        labels = {7: "Strongly bullish conditions", 5: "Strongly bullish conditions", 4: "Bullish conditions",
                  2: "Bullish conditions", 1: "Sideways", -1: "Sideways", -2: "Bearish conditions",
                  -4: "Bearish conditions", -5: "Strongly bearish conditions"}
        for score, label in labels.items():
            self.assertEqual(market.regime_label(score), label, score)


def stock(n=210, step=0.5, last_date=None, start=100.0):
    return bars([start + step * i for i in range(n)], last_date=last_date)


class BreadthTests(unittest.TestCase):
    def group(self, ups=12, downs=3, flats=0):
        g = {}
        for i in range(ups):
            g[f"U{i}"] = stock(step=0.5)
        for i in range(downs):
            g[f"D{i}"] = stock(step=-0.2, start=300)
        for i in range(flats):
            g[f"F{i}"] = bars([100.0] * 210)
        return g

    def test_counts_and_shares(self):
        out = market.breadth(self.group(12, 3, 1))
        self.assertEqual((out["n"], out["advancing"], out["declining"], out["unchanged"]), (16, 12, 3, 1))
        self.assertEqual(out["above50_pct"], 75.0)
        self.assertEqual(out["new_highs"], 12)
        self.assertEqual(out["new_lows"], 3)

    def test_stocks_that_did_not_trade_on_the_latest_session_are_left_out(self):
        g = self.group(12, 3)
        g["OLD"] = bars([100.0 + i for i in range(210)], last_date="d0000")
        self.assertEqual(market.breadth(g)["n"], 15)

    def test_too_few_stocks_gives_none(self):
        self.assertIsNone(market.breadth(self.group(3, 2)))
        self.assertIsNone(market.breadth({}))

    def test_short_histories_do_not_count_toward_the_200_day_share(self):
        g = self.group(12, 3)
        for i in range(5):
            g[f"N{i}"] = stock(n=60, last_date="d0209")
        out = market.breadth(g)
        self.assertEqual(out["n"], 20)
        self.assertEqual(out["above200_pct"], 80.0)


class SectorTests(unittest.TestCase):
    def test_equal_weighted_moves_per_sector_strongest_first(self):
        group = {"A": bars([100] * 25 + [110]), "B": bars([100] * 25 + [104]), "C": bars([100] * 25 + [97])}
        info = {"A": {"sector": "Tech"}, "B": {"sector": "Tech"}, "C": {"sector": "Banks"}}
        rows = market.sector_moves(group, info)
        self.assertEqual([r["sector"] for r in rows], ["Tech", "Banks"])
        self.assertEqual(rows[0]["d1"], 7.0)
        self.assertEqual(rows[0]["n"], 2)
        self.assertEqual(rows[1]["d20"], -3.0)

    def test_a_stock_with_no_sector_is_other_and_empty_groups_are_empty(self):
        rows = market.sector_moves({"A": bars([100] * 25 + [101])}, {})
        self.assertEqual(rows[0]["sector"], "Other")
        self.assertEqual(market.sector_moves({}, {}), [])


class AnalyseTests(unittest.TestCase):
    def test_each_market_gets_its_own_view_and_a_missing_index_is_not_a_crash(self):
        stocks = {f"I{i}": stock() for i in range(12)}
        stocks.update({f"U{i}": stock(step=-0.2, start=300) for i in range(12)})
        info = {**{f"I{i}": {"market": "IN", "sector": "Banks"} for i in range(12)},
                **{f"U{i}": {"market": "US", "sector": "Tech"} for i in range(12)}}
        raw = {"^IN": bars(rising()), "^INVIX": bars([14.0] * 30)}
        config = {"IN": {"index": "^IN", "vix": "^INVIX", "name": "Nifty 50"}, "US": {"index": "^US", "vix": "^USVIX", "name": "S&P 500"}}
        out = market.analyse(config, raw, stocks, info)
        self.assertEqual(out["IN"]["regime"]["label"], "Strongly bullish conditions")
        self.assertEqual(out["IN"]["breadth"]["n"], 12)
        self.assertEqual(out["IN"]["sectors"][0]["sector"], "Banks")
        self.assertIsNone(out["US"]["regime"])
        self.assertEqual(out["US"]["breadth"]["advancing"], 0)
        self.assertEqual(market.analyse({}, {}, {}, {}), {})


class StatusTests(unittest.TestCase):
    CONFIG = {"market_watch": [1, 2, 3]}

    def status(self, **over):
        args = dict(config=self.CONFIG, records=[{"news": {}}, {"news": {}}], errors=[], carried=[],
                    overview={"watch": [1, 2, 3]}, conditions={"IN": {"regime": {"score": 1}}, "US": {"regime": {"score": 0}}},
                    ipos={"india_ok": True}, delivery={"2026-11-13": {}}, track_record={"calls_total": 4}, pending=[],
                    token="T", chat="C")
        args.update(over)
        return build.status_block(**args)

    def states(self, out):
        return {c["name"]: c["state"] for c in out["checks"]}

    def test_a_healthy_run_is_all_ok(self):
        out = self.status()
        self.assertEqual(out["overall"], "ok")
        self.assertEqual(set(self.states(out).values()), {"ok"})

    def test_each_problem_is_flagged_on_its_own_line(self):
        out = self.status(errors=[{"symbol": "X"}], overview={"watch": [1]}, conditions={"IN": {"regime": None}},
                          ipos={"india_ok": False}, pending=[{}, {}], records=[{"news": {"error": True}}, {"news": {}}])
        states = self.states(out)
        for name in ("Stock prices", "News", "Index and market data", "Market conditions", "India IPOs", "Track record"):
            self.assertEqual(states[name], "warn", name)
        self.assertEqual(out["overall"], "warn")
        self.assertIn("2 waiting", next(c["detail"] for c in out["checks"] if c["name"] == "Track record"))

    def test_recent_listings_kept_from_the_last_run_do_not_raise_a_warning_but_other_stocks_do(self):
        new_only = self.status(carried=[{"new": True}, {"new": True}])
        prices = next(c for c in new_only["checks"] if c["name"] == "Stock prices")
        self.assertEqual(prices["state"], "ok")
        self.assertIn("2 recent listings without enough history", prices["detail"])
        mixed = self.status(carried=[{"new": True}, {"new": False}])
        prices = next(c for c in mixed["checks"] if c["name"] == "Stock prices")
        self.assertEqual(prices["state"], "warn")
        self.assertIn("1 kept from the previous run", prices["detail"])

    def test_a_new_listing_that_fails_to_load_is_information_but_a_real_failure_is_a_warning(self):
        new_only = self.status(errors=[{"symbol": "NEW", "new": True}])
        prices = next(c for c in new_only["checks"] if c["name"] == "Stock prices")
        self.assertEqual(prices["state"], "ok")
        self.assertIn("1 recent listing without enough history", prices["detail"])
        self.assertNotIn("failed to load", prices["detail"])
        real = self.status(errors=[{"symbol": "NEW", "new": True}, {"symbol": "OLD", "error": "404"}])
        prices = next(c for c in real["checks"] if c["name"] == "Stock prices")
        self.assertEqual(prices["state"], "warn")
        self.assertIn("1 failed to load", prices["detail"])

    def test_optional_pieces_are_info_not_warnings(self):
        out = self.status(track_record=None, token=None, chat=None, delivery={})
        states = self.states(out)
        self.assertEqual((states["Track record"], states["Telegram alerts"], states["NSE delivery data"]), ("info", "info", "info"))
        self.assertEqual(out["overall"], "ok")

    def test_it_does_not_reveal_secrets(self):
        self.assertNotIn("T", {c["detail"] for c in self.status(token="SECRET", chat="CHAT")["checks"]})
        self.assertNotIn("SECRET", str(self.status(token="SECRET", chat="CHAT")))


class BreakerTests(unittest.TestCase):
    def test_it_opens_after_enough_failures_in_a_row_and_a_success_resets_it(self):
        breaker = build.Breaker(3)
        for _ in range(2):
            breaker.failed()
        self.assertFalse(breaker.open)
        breaker.succeeded()
        for _ in range(2):
            breaker.failed()
        self.assertFalse(breaker.open, "a success in between starts the count again")
        breaker.failed()
        self.assertTrue(breaker.open)

    def test_a_failing_news_source_is_asked_only_a_few_times_not_once_per_stock(self):
        from unittest import mock
        from engine import fetch, news
        entry = {"symbol": "AAA", "name": "Aaa Ltd", "market": "IN", "sector": "IT", "tags": []}
        closes = [100.0 + i * 0.1 for i in range(300)]
        chart = {"bars": {"t": list(range(300)), "d": [f"2026-01-{1 + i % 28:02d}" for i in range(300)], "o": closes, "h": closes,
                          "l": closes, "c": closes, "v": [1000.0] * 300}, "dividends": [], "meta": {"currency": "INR", "exchange": "NSE"}}
        ctx = {"today": __import__("datetime").date(2026, 11, 16), "now": __import__("datetime").datetime(2026, 11, 16), "now_ts": 1,
               "delivery": {}, "deals": {}, "bench": {}, "bars": {}, "news_breaker": build.Breaker(3)}
        with mock.patch.object(fetch, "fetch_chart", return_value=chart),                 mock.patch.object(news, "fetch_news", side_effect=fetch.FetchError("blocked")) as asked:
            records = [build.analyse(entry, ctx) for _ in range(10)]
        self.assertEqual(asked.call_count, 3)
        self.assertTrue(all(r["news"]["error"] and r["news"]["fresh"] == [] for r in records))


if __name__ == "__main__":
    unittest.main()
