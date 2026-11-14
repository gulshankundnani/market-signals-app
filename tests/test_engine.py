import json
import math
import unittest
from datetime import datetime, timezone

from engine import backtest as bt
from engine import fetch, fundamentals, indicators as ta, news, patterns, risk, signals

# Wilder's RSI worked example (StockCharts), values from index 14 onward.
WILDER_CLOSES = [
    44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28,
    46.00, 46.03, 46.41, 46.22, 45.64, 46.21, 46.25, 45.71, 46.45, 45.78, 45.35, 44.03, 44.18, 44.22, 44.57,
    43.42, 42.66, 43.13,
]
WILDER_RSI = [70.53, 66.32, 66.55, 69.41, 66.36, 57.97, 62.93, 63.26, 56.06, 62.38, 54.71, 50.42, 39.99,
              41.46, 41.87, 45.46, 37.30, 33.08, 37.77]


def make_bars(closes, spread=0.01, volume=1000.0, open_prev_close=True):
    o = [closes[0]] + closes[:-1] if open_prev_close else list(closes)
    return {
        "t": list(range(len(closes))),
        "d": [str(i) for i in range(len(closes))],
        "o": o,
        "h": [max(a, b) * (1 + spread) for a, b in zip(o, closes)],
        "l": [min(a, b) * (1 - spread) for a, b in zip(o, closes)],
        "c": list(closes),
        "v": [volume] * len(closes),
    }


class IndicatorTests(unittest.TestCase):
    def test_sma(self):
        self.assertEqual(ta.sma([1, 2, 3, 4, 5], 3), [None, None, 2, 3, 4])

    def test_sma_too_short(self):
        self.assertEqual(ta.sma([1, 2], 3), [None, None])

    def test_ema_constant_series(self):
        out = ta.ema([5.0] * 30, 10)
        self.assertTrue(all(v is None for v in out[:9]))
        self.assertTrue(all(abs(v - 5.0) < 1e-12 for v in out[9:]))

    def test_ema_known_step(self):
        out = ta.ema([1, 1, 1, 4], 3)
        self.assertAlmostEqual(out[3], 4 * 0.5 + 1 * 0.5)

    def test_rsi_matches_wilder_reference(self):
        out = ta.rsi(WILDER_CLOSES, 14)
        for offset, expected in enumerate(WILDER_RSI):
            self.assertAlmostEqual(out[14 + offset], expected, delta=0.6, msg=f"index {14 + offset}")

    def test_rsi_first_value_independent_formula(self):
        changes = [WILDER_CLOSES[i] - WILDER_CLOSES[i - 1] for i in range(1, 15)]
        avg_gain = sum(c for c in changes if c > 0) / 14
        avg_loss = sum(-c for c in changes if c < 0) / 14
        expected = 100 - 100 / (1 + avg_gain / avg_loss)
        self.assertAlmostEqual(ta.rsi(WILDER_CLOSES, 14)[14], expected, places=9)

    def test_rsi_bounds_and_extremes(self):
        rising = ta.rsi([float(i) for i in range(1, 40)], 14)
        falling = ta.rsi([float(i) for i in range(40, 1, -1)], 14)
        flat = ta.rsi([10.0] * 40, 14)
        self.assertEqual(rising[-1], 100.0)
        self.assertEqual(falling[-1], 0.0)
        self.assertEqual(flat[-1], 50.0)

    def test_macd_constant_is_zero(self):
        line, sig, hist = ta.macd([7.0] * 80)
        self.assertAlmostEqual(line[-1], 0.0)
        self.assertAlmostEqual(hist[-1], 0.0)

    def test_macd_uptrend_positive(self):
        line, sig, hist = ta.macd([float(i) for i in range(1, 120)])
        self.assertGreater(line[-1], 0)

    def test_atr_constant_range(self):
        out = ta.atr([12.0] * 30, [10.0] * 30, [11.0] * 30, 14)
        self.assertAlmostEqual(out[-1], 2.0)
        self.assertIsNone(out[12])

    def test_rolling_vol_flat_is_zero(self):
        self.assertAlmostEqual(ta.rolling_vol([10.0] * 50, 30)[-1], 0.0)

    def test_rolling_vol_known(self):
        values = [100.0]
        for i in range(40):
            values.append(values[-1] * (1.01 if i % 2 == 0 else 0.99))
        vol = ta.rolling_vol(values, 30)[-1]
        self.assertAlmostEqual(vol, 0.01 * math.sqrt(252), delta=0.01)

    def test_vwap_equal_volume_is_mean_typical(self):
        out = ta.rolling_vwap([12.0] * 25, [10.0] * 25, [11.0] * 25, [100.0] * 25, 20)
        self.assertAlmostEqual(out[-1], 11.0)

    def test_vwap_zero_volume_is_none(self):
        self.assertIsNone(ta.rolling_vwap([12.0] * 25, [10.0] * 25, [11.0] * 25, [0.0] * 25, 20)[-1])

    def test_cmf_close_at_high_is_one(self):
        out = ta.chaikin_money_flow([11.0] * 25, [9.0] * 25, [11.0] * 25, [100.0] * 25, 20)
        self.assertAlmostEqual(out[-1], 1.0)

    def test_cmf_close_at_low_is_minus_one(self):
        out = ta.chaikin_money_flow([11.0] * 25, [9.0] * 25, [9.0] * 25, [100.0] * 25, 20)
        self.assertAlmostEqual(out[-1], -1.0)

    def test_cmf_flat_bar_and_zero_volume(self):
        self.assertAlmostEqual(ta.chaikin_money_flow([10.0] * 25, [10.0] * 25, [10.0] * 25, [5.0] * 25)[-1], 0.0)
        self.assertIsNone(ta.chaikin_money_flow([11.0] * 25, [9.0] * 25, [10.0] * 25, [0.0] * 25)[-1])

    def test_up_volume_share(self):
        rising = ta.up_volume_share([float(i) for i in range(1, 40)], [10.0] * 39)
        falling = ta.up_volume_share([float(i) for i in range(40, 1, -1)], [10.0] * 39)
        self.assertEqual(rising[-1], 1.0)
        self.assertEqual(falling[-1], 0.0)

    def test_fibonacci_uptrend(self):
        high = [10.0, 12.0, 14.0, 20.0, 18.0]
        low = [9.0, 10.0, 12.0, 18.0, 16.0]
        fib = ta.fibonacci_levels(high, low)
        self.assertTrue(fib["uptrend"])
        self.assertAlmostEqual(fib["levels"]["0.5"], (20.0 + 9.0) / 2)

    def test_fibonacci_flat_is_none(self):
        self.assertIsNone(ta.fibonacci_levels([5.0] * 10, [5.0] * 10))

    def test_support_resistance_finds_zones(self):
        closes, high, low = [], [], []
        for i in range(120):
            base = 100 + 10 * math.sin(i / 6)
            closes.append(base)
            high.append(base + 0.5)
            low.append(base - 0.5)
        zones = ta.support_resistance(high, low, closes)
        price = closes[-1]
        for z in zones["support"]:
            self.assertLess(z["level"], price)
        for z in zones["resistance"]:
            self.assertGreater(z["level"], price)
        self.assertTrue(zones["support"] or zones["resistance"])


class RiskTests(unittest.TestCase):
    def test_percentile(self):
        self.assertEqual(risk.percentile([1, 2, 3, 4, 5], 0.5), 3)
        self.assertAlmostEqual(risk.percentile([1, 2, 3, 4], 0.5), 2.5)
        self.assertIsNone(risk.percentile([], 0.5))

    def test_max_drawdown(self):
        self.assertAlmostEqual(risk.max_drawdown([100, 50, 100]), -0.5)
        self.assertEqual(risk.max_drawdown([1, 2, 3]), 0.0)

    def test_risk_stats_short_history(self):
        self.assertIsNone(risk.risk_stats([100.0] * 30))

    def test_risk_stats_values(self):
        closes = [100 * (1.001 ** i) * (1 + (0.01 if i % 7 == 0 else 0)) for i in range(300)]
        stats = risk.risk_stats(closes)
        self.assertGreater(stats["annual_vol"], 0)
        self.assertGreaterEqual(stats["expected_shortfall95"], stats["var95"])
        self.assertLessEqual(stats["max_drawdown"], 0)

    def test_beta_of_double_exposure_is_two(self):
        bench = {str(i).zfill(4): 100 * (1 + 0.01 * math.sin(i)) for i in range(120)}
        dates = sorted(bench)
        stock = {}
        value = 100.0
        stock[dates[0]] = value
        for prev, cur in zip(dates, dates[1:]):
            value *= 1 + 2 * (bench[cur] / bench[prev] - 1)
            stock[cur] = value
        self.assertAlmostEqual(risk.beta(stock, bench), 2.0, delta=0.05)

    def test_beta_needs_overlap(self):
        self.assertIsNone(risk.beta({"a": 1.0}, {"b": 1.0}))


class SignalTests(unittest.TestCase):
    def scores(self, closes):
        bars = make_bars(closes)
        ind = signals.build_indicators(bars)
        i = len(closes) - 1
        return signals.short_score(ind, i)[0], signals.long_score(ind, i)[0]

    def test_label_boundaries(self):
        cases = {None: "N/A", 50: "Strong Buy", 49: "Buy", 20: "Buy", 19: "Hold", 0: "Hold", -19: "Hold",
                 -20: "Sell", -49: "Sell", -50: "Strong Sell"}
        for score, expected in cases.items():
            self.assertEqual(signals.label(score), expected, score)

    def test_uptrend_scores_bullish(self):
        closes = [100 * 1.004 ** i for i in range(300)]
        short, long_ = self.scores(closes)
        self.assertGreater(long_, 40)
        self.assertGreater(short, 0)

    def test_downtrend_scores_bearish(self):
        closes = [100 * 0.996 ** i for i in range(300)]
        short, long_ = self.scores(closes)
        self.assertLess(long_, -20)
        self.assertLess(short, 0)

    def test_scores_stay_in_range(self):
        for closes in ([100 * 1.02 ** i for i in range(300)], [100 * 0.98 ** i for i in range(300)]):
            for score in self.scores(closes):
                self.assertTrue(-100 <= score <= 100)

    def test_insufficient_history_is_none(self):
        bars = make_bars([100.0 + i for i in range(40)])
        ind = signals.build_indicators(bars)
        self.assertIsNone(signals.long_score(ind, 39)[0])
        self.assertIsNone(signals.short_score(ind, 39)[0])

    def test_news_changes_short_score_in_the_right_direction(self):
        closes = [100 + math.sin(i / 5) * 3 for i in range(120)]
        ind = signals.build_indicators(make_bars(closes))
        i = len(closes) - 1
        good = signals.short_score(ind, i, news=1.0)[0]
        bad = signals.short_score(ind, i, news=-1.0)[0]
        self.assertGreater(good, bad)

    def test_reasons_have_tone(self):
        ind = signals.build_indicators(make_bars([100 * 1.004 ** i for i in range(300)]))
        score, reasons = signals.long_score(ind, 299)
        self.assertTrue(reasons)
        self.assertTrue(all(r["tone"] in ("pos", "neg", "neutral") and r["text"] for r in reasons))

    def test_pressure_labels(self):
        buying = make_bars([100 * 1.003 ** i for i in range(60)])
        buying["h"] = [c * 1.01 for c in buying["c"]]
        buying["l"] = [c * 0.99 for c in buying["c"]]
        ind = signals.build_indicators(buying)
        self.assertEqual(signals.buy_sell_pressure(ind, 59)["label"], "Buying pressure")
        selling = make_bars([100 * 0.997 ** i for i in range(60)])
        selling["h"] = [c * 1.01 for c in selling["c"]]
        selling["l"] = [c * 0.99 for c in selling["c"]]
        ind = signals.build_indicators(selling)
        self.assertEqual(signals.buy_sell_pressure(ind, 59)["label"], "Selling pressure")

    def test_pressure_missing_without_volume(self):
        bars = make_bars([100.0 + i for i in range(60)], volume=0.0)
        self.assertIsNone(signals.buy_sell_pressure(signals.build_indicators(bars), 59))

    def test_stop_loss(self):
        stop = signals.stop_loss(100.0, 2.0)
        self.assertAlmostEqual(stop["price"], 96.0)
        self.assertAlmostEqual(stop["pct"], -4.0)
        self.assertIsNone(signals.stop_loss(100.0, None))
        self.assertIsNone(signals.stop_loss(1.0, 5.0))


class BacktestTests(unittest.TestCase):
    def bars(self, n=300):
        closes = [100 * (1 + 0.002 * math.sin(i / 7) + 0.001) ** 1 for i in range(n)]
        walk = [100.0]
        for i in range(1, n):
            walk.append(walk[-1] * (1.001 + 0.01 * math.sin(i / 5)))
        return make_bars(walk)

    def test_always_long_without_costs_equals_buy_and_hold(self):
        bars = self.bars()
        result = bt.backtest(bars, lambda i: 100, start=50, cost=0.0)
        self.assertAlmostEqual(result["total_return"], result["buy_hold_return"], places=9)
        self.assertEqual(result["trades"], 1)
        self.assertAlmostEqual(result["exposure"], 1.0)

    def test_costs_reduce_return(self):
        bars = self.bars()
        free = bt.backtest(bars, lambda i: 100, start=50, cost=0.0)["total_return"]
        paid = bt.backtest(bars, lambda i: 100, start=50, cost=0.002)["total_return"]
        self.assertLess(paid, free)

    def test_never_long_stays_flat(self):
        result = bt.backtest(self.bars(), lambda i: -100, start=50)
        self.assertEqual(result["trades"], 0)
        self.assertAlmostEqual(result["total_return"], 0.0)
        self.assertEqual(result["max_drawdown"], 0.0)
        self.assertIsNone(result["win_rate"])

    def test_score_never_sees_the_last_bar(self):
        bars = self.bars()
        seen = []
        bt.backtest(bars, lambda i: seen.append(i) or 0, start=50)
        self.assertEqual(max(seen), len(bars["c"]) - 2)

    def test_too_short_returns_none(self):
        self.assertIsNone(bt.backtest(self.bars(80), lambda i: 100, start=50))

    def test_flags_and_oos_present(self):
        result = bt.backtest(self.bars(), lambda i: 100 if i % 40 < 20 else -100, start=50)
        self.assertTrue(result["few_trades"] or result["trades"] >= bt.FEW_TRADES)
        for key in ("total_return", "buy_hold_return", "max_drawdown", "sharpe", "trades"):
            self.assertIn(key, result["oos"])
        json.dumps(result, allow_nan=False)

    def test_known_trade_return(self):
        closes = [100.0] * 61 + [110.0] * 59
        bars = make_bars(closes)
        bars["o"] = closes
        result = bt.backtest(bars, lambda i: 100 if i == 59 else -100, start=50, cost=0.0)
        self.assertEqual(result["trades"], 1)
        self.assertAlmostEqual(result["expectancy"], 0.10, places=9)
        self.assertEqual(result["win_rate"], 1.0)


class PatternTests(unittest.TestCase):
    def detect(self, o, h, l, c):
        bars = {"t": [], "d": [], "o": o, "h": h, "l": l, "c": c, "v": [1000.0] * len(c)}
        return {p["name"] for p in patterns.detect_patterns(bars, signals.build_indicators(bars))}

    def flat_then(self, last_open, last_high, last_low, last_close, prev_open, prev_close):
        n = 40
        c = [100.0] * (n - 2) + [prev_close, last_close]
        o = [100.0] * (n - 2) + [prev_open, last_open]
        h = [101.0] * (n - 2) + [max(prev_open, prev_close) + 0.2, last_high]
        l = [99.0] * (n - 2) + [min(prev_open, prev_close) - 0.2, last_low]
        return o, h, l, c

    def test_bullish_engulfing(self):
        o, h, l, c = self.flat_then(98.5, 101.5, 98.3, 101.0, 100.0, 99.0)
        self.assertIn("Bullish engulfing", self.detect(o, h, l, c))

    def test_bearish_engulfing(self):
        o, h, l, c = self.flat_then(101.5, 101.7, 98.5, 99.0, 100.0, 101.0)
        self.assertIn("Bearish engulfing", self.detect(o, h, l, c))

    def test_doji(self):
        o, h, l, c = self.flat_then(100.0, 101.0, 99.0, 100.05, 100.0, 100.0)
        self.assertIn("Doji", self.detect(o, h, l, c))

    def test_breakout_detected(self):
        closes = [100.0 + (i % 3) * 0.1 for i in range(60)] + [105.0]
        bars = make_bars(closes)
        found = {p["name"] for p in patterns.detect_patterns(bars, signals.build_indicators(bars))}
        self.assertIn("20-day breakout", found)

    def test_short_history_returns_nothing(self):
        bars = make_bars([100.0] * 10)
        self.assertEqual(patterns.detect_patterns(bars, signals.build_indicators(bars)), [])

    def test_golden_cross_detected(self):
        closes = [100.0 - i * 0.2 for i in range(260)] + [48.0 + i * 3 for i in range(60)]
        bars = make_bars(closes)
        ind = signals.build_indicators(bars)
        found = []
        for end in range(len(closes) - 60, len(closes)):
            sub = {k: v[: end + 1] for k, v in bars.items()}
            found += [p["name"] for p in patterns.detect_patterns(sub, signals.build_indicators(sub))]
        self.assertIn("Golden cross", found)


class FetchTests(unittest.TestCase):
    def payload(self, **overrides):
        base = {
            "chart": {"result": [{
                "meta": {"currency": "INR", "gmtoffset": 19800, "longName": "Test Ltd", "regularMarketTime": 5},
                "timestamp": list(range(1_700_000_000, 1_700_000_000 + 86400 * 70, 86400)),
                "indicators": {"quote": [{
                    "open": [10.0] * 70, "high": [11.0] * 70, "low": [9.0] * 70,
                    "close": [10.5] * 70, "volume": [1000] * 70,
                }]},
                "events": {"dividends": {"1": {"date": 1_700_000_000, "amount": 2.0}}},
            }], "error": None}
        }
        base["chart"]["result"][0].update(overrides)
        return base

    def test_clean_chart_ok(self):
        out = fetch.clean_chart(self.payload(), "T")
        self.assertEqual(len(out["bars"]["c"]), 70)
        self.assertEqual(out["meta"]["name"], "Test Ltd")
        self.assertEqual(len(out["dividends"]), 1)

    def test_drops_null_and_invalid_bars(self):
        p = self.payload()
        quote = p["chart"]["result"][0]["indicators"]["quote"][0]
        quote["close"][3] = None
        quote["low"][4] = 12.0
        quote["open"][5] = 0
        out = fetch.clean_chart(p, "T")
        self.assertEqual(len(out["bars"]["c"]), 67)

    def test_null_volume_becomes_zero(self):
        p = self.payload()
        p["chart"]["result"][0]["indicators"]["quote"][0]["volume"][0] = None
        self.assertEqual(fetch.clean_chart(p, "T")["bars"]["v"][0], 0.0)

    def test_errors(self):
        with self.assertRaises(fetch.FetchError):
            fetch.clean_chart({"chart": {"result": None, "error": {"description": "Not Found"}}}, "T")
        with self.assertRaises(fetch.FetchError):
            fetch.clean_chart(None, "T")
        with self.assertRaises(fetch.FetchError):
            fetch.clean_chart(self.payload(timestamp=[1, 2, 3]), "T")
        short = self.payload()
        for key in ("open", "high", "low", "close", "volume"):
            short["chart"]["result"][0]["indicators"]["quote"][0][key] = [10.0] * 5
        short["chart"]["result"][0]["timestamp"] = list(range(5))
        with self.assertRaises(fetch.FetchError):
            fetch.clean_chart(short, "T")


class FundamentalsTests(unittest.TestCase):
    def test_parse(self):
        payload = {"quoteSummary": {"result": [{
            "summaryDetail": {"trailingPE": {"raw": 25.5}, "payoutRatio": {"raw": 0.4}},
            "defaultKeyStatistics": {"priceToBook": {"raw": 3.0}},
            "financialData": {"returnOnEquity": {"raw": 0.2}, "debtToEquity": {"raw": 35.0}},
            "assetProfile": {"sector": "Technology"},
            "price": {"marketCap": {"raw": 1_000_000}},
        }]}}
        out = fundamentals.parse_summary(payload)
        self.assertEqual(out["pe"], 25.5)
        self.assertAlmostEqual(out["debt_to_equity"], 0.35)
        self.assertEqual(out["sector"], "Technology")
        self.assertIsNone(out["forward_pe"])
        self.assertEqual(out["market_cap"], 1_000_000)

    def test_parse_empty_and_junk(self):
        self.assertEqual(fundamentals.parse_summary(None), {})
        self.assertEqual(fundamentals.parse_summary({"quoteSummary": {"result": None}}), {})
        out = fundamentals.parse_summary({"quoteSummary": {"result": [{"summaryDetail": {"trailingPE": {}}}]}})
        self.assertIsNone(out["pe"])


RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<item><title>Acme shares surge after record profit - Example Wire</title><link>https://example.com/a</link>
<pubDate>Sat, 14 Nov 2026 06:00:00 GMT</pubDate><source url="https://example.com">Example Wire</source></item>
<item><title>Acme faces fraud probe, stock plunges - Daily News</title><link>https://example.com/b</link>
<pubDate>Sat, 14 Nov 2026 09:30:00 GMT</pubDate><source url="https://example.com">Daily News</source></item>
<item><title>Bank of Acme opens new branch</title><link>javascript:alert(1)</link>
<pubDate>not a date</pubDate></item>
</channel></rss>"""


class NewsTests(unittest.TestCase):
    NOW = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)

    def test_parse_rss(self):
        items = news.parse_rss(RSS, self.NOW)
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]["title"], "Acme faces fraud probe, stock plunges")
        self.assertEqual(items[0]["age_min"], 30)
        self.assertEqual(items[-1]["age_min"], None)
        self.assertEqual(items[-1]["link"], "")

    def test_sentiment(self):
        self.assertEqual(news.headline_sentiment("Company shares surge after record profit")[1], "pos")
        score, strong = news.headline_sentiment("Fraud probe: stock plunges")
        self.assertEqual(strong, "neg")
        self.assertLess(score, 0)

    def test_bank_is_not_ban(self):
        self.assertEqual(news.headline_sentiment("State Bank opens new branch"), (0.0, None))

    def test_neutral(self):
        self.assertEqual(news.headline_sentiment("Company schedules annual meeting"), (0.0, None))

    def test_score_recency_weighted(self):
        items = news.parse_rss(RSS, self.NOW)
        score = news.news_score(items)
        self.assertLess(score, 0)
        self.assertTrue(-1 <= score <= 1)

    def test_score_none_without_recent_news(self):
        old = [{"age_min": 60 * 100, "sentiment": 1.0}, {"age_min": None, "sentiment": 1.0}]
        self.assertIsNone(news.news_score(old))
        self.assertIsNone(news.news_score([]))

    def test_strong_alerts(self):
        items = news.parse_rss(RSS, self.NOW)
        alerts = news.strong_alerts(items)
        self.assertEqual([a["strong"] for a in alerts], ["neg", "pos"])

    def test_relevance_requires_the_company_name(self):
        rel = news.is_relevant
        self.assertTrue(rel("Apple unveils new iPhone", "Apple"))
        self.assertFalse(rel("3 Favorite Buffett Stocks Hit Record Highs As Nvidia Sinks", "Apple"))
        self.assertTrue(rel("State Bank of India raises lending rate", "State Bank of India", "SBIN"))
        self.assertFalse(rel("Online fraud drains woman's account; bank ordered to pay", "State Bank of India", "SBIN"))
        self.assertFalse(rel("India's balance of payments surplus soars", "Bank of Baroda", "BANKBARODA"))
        self.assertTrue(rel("Baroda shares rally after results", "Bank of Baroda", "BANKBARODA"))
        self.assertTrue(rel("TCS wins large deal", "Tata Consultancy Services", "TCS"))
        self.assertFalse(rel("Tata Motors shares fall", "Tata Steel", "TATASTEEL"))
        self.assertTrue(rel("ITC announces dividend", "ITC", "ITC"))
        self.assertFalse(rel("Marketing campaign launches", "ITC", "ITC"))

    def test_irrelevant_headlines_do_not_alert_or_score(self):
        items = news.parse_rss(RSS, self.NOW)
        news.mark_relevance(items, "Somebody Else Corp", "SOME")
        self.assertTrue(all(i["relevant"] is False and i["strong"] is None for i in items))
        self.assertIsNone(news.news_score(items))
        self.assertEqual(news.strong_alerts(items), [])
        items = news.mark_relevance(news.parse_rss(RSS, self.NOW), "Acme", "ACME")
        self.assertTrue(news.news_score(items) is not None)
        self.assertTrue(news.strong_alerts(items))

    def test_rejects_dtd(self):
        with self.assertRaises(fetch.FetchError):
            news.parse_rss('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><rss/>', self.NOW)

    def test_rejects_garbage(self):
        with self.assertRaises(fetch.FetchError):
            news.parse_rss("<html>not rss", self.NOW)


if __name__ == "__main__":
    unittest.main()
