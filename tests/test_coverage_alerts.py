"""Every-stock coverage, movers, index alerts, briefs and compact alert wording."""
import json
import unittest
from datetime import datetime, timezone

from engine import alerts, digest, sessions, wide
from engine.fetch import FetchError

NOW_IN_MORNING = datetime(2026, 11, 17, 3, 45, tzinfo=timezone.utc)      # 09:15 IST, a Tuesday


def q(price=100.0, chg=0.0, vol=1_000_000, avg=1_000_000, high=None, low=None, cap=1e11, state="REGULAR"):
    return {"price": price, "change_pct": chg, "volume": vol, "avg_volume": avg, "high52": high, "low52": low, "cap": cap, "state": state}


class UniverseTests(unittest.TestCase):
    SP = ('<table id="constituents"><tr><th>Symbol</th><th>Security</th><th>GICS Sector</th></tr>'
          '<tr><td><a>AAPL</a></td><td>Apple Inc.</td><td>Information Technology</td></tr><tr><td><a>BRK.B</a></td><td>Berkshire Hathaway</td><td>Financials</td></tr></table>')

    def test_sp500_table(self):
        self.assertEqual(wide.parse_sp500(self.SP), {"AAPL": {"name": "Apple Inc.", "sector": "Information Technology"}, "BRK-B": {"name": "Berkshire Hathaway", "sector": "Financials"}})
        self.assertEqual(wide.parse_sp500(""), {})

    def test_short_names_drop_the_legal_suffix(self):
        self.assertEqual(wide.short_name("Billionbrains Garage Ventures Limited"), "Billionbrains Garage Ventures")
        self.assertEqual(wide.short_name("Apple Inc."), "Apple")
        self.assertEqual(wide.short_name("The Tata Power Company Limited"), "The Tata Power")
        self.assertEqual(wide.short_name("Limited"), "Limited")

    def test_the_wide_list_leaves_out_the_curated_stocks(self):
        out = wide.build_universe({"TCS.NS": "Tata Consultancy Services Limited", "GROWW.NS": "Billionbrains Garage Ventures Limited"}, {"AAPL": {"name": "Apple Inc."}}, {"TCS.NS"})
        self.assertEqual([(s["symbol"], s["market"]) for s in out], [("GROWW.NS", "IN"), ("AAPL", "US")])

    def test_refresh_once_a_day_and_keep_the_old_list_if_a_source_fails(self):
        csv_text = "SYMBOL,NAME OF COMPANY, SERIES,DATE OF LISTING\nGROWW,Billionbrains Garage Ventures Limited, EQ,12-Nov-2025\n"
        calls = []

        def fetch(url):
            calls.append(url)
            return csv_text.encode() if "EQUITY_L" in url else self.SP.encode()

        wide_list, cache = wide.load_universe(None, NOW_IN_MORNING, set(), fetch, lambda s: None)
        self.assertEqual(len(wide_list), 3)
        wide.load_universe(cache, NOW_IN_MORNING.replace(hour=5), set(), fetch, lambda s: None)
        self.assertEqual(len(calls), 2, "a fresh list is not fetched again")

        def broken(url):
            raise FetchError("down")

        again, kept = wide.load_universe(cache, NOW_IN_MORNING.replace(day=19), set(), broken, lambda s: None)
        self.assertEqual(len(again), 3)
        self.assertEqual(kept, cache)

        def us_down(url):
            if "wikipedia" in url:
                raise FetchError("down")
            return csv_text.encode()

        mixed, _ = wide.load_universe(cache, NOW_IN_MORNING.replace(day=19), set(), us_down, lambda s: None)
        self.assertEqual(sorted({s["market"] for s in mixed}), ["IN", "US"])                 # the old US list is kept while the new Indian one is used
        _, partial_cache = wide.load_universe(cache, NOW_IN_MORNING.replace(day=19), set(), us_down, lambda s: None)
        self.assertEqual(partial_cache["fetched_at"], cache["fetched_at"], "a partial refresh is not stamped as done, so it is retried next run")


class QuoteTests(unittest.TestCase):
    def test_quotes_are_parsed_and_rows_without_a_price_skipped(self):
        payload = {"quoteResponse": {"result": [{"symbol": "A.NS", "regularMarketPrice": 10, "regularMarketChangePercent": 1.5, "averageDailyVolume3Month": 5, "marketState": "REGULAR"},
                                                {"symbol": "B.NS"}, {"regularMarketPrice": 3}, "junk"]}}
        out = wide.parse_quotes(payload)
        self.assertEqual(list(out), ["A.NS"])
        self.assertEqual((out["A.NS"]["change_pct"], out["A.NS"]["avg_volume"], out["A.NS"]["state"]), (1.5, 5.0, "REGULAR"))
        self.assertEqual(wide.parse_quotes(None), {})

    def test_batches_of_100_and_a_failed_batch_is_not_fatal(self):
        class Session:
            def __init__(self):
                self.calls = 0

            def get_json(self, url):
                self.calls += 1
                if self.calls == 2:
                    raise FetchError("429")
                return {"quoteResponse": {"result": [{"symbol": f"S{self.calls}", "regularMarketPrice": 5}]}}

        s = Session()
        logs = []
        out = wide.fetch_quotes(s, [f"X{n}" for n in range(250)], logs.append, pause=0)
        self.assertEqual((s.calls, sorted(out)), (3, ["S1", "S3"]))
        self.assertTrue(any("1 of 3 batches failed" in l for l in logs))


class MoverTests(unittest.TestCase):
    MARKETS = {"A.NS": "IN", "B.NS": "IN", "C.NS": "IN", "D.NS": "IN", "U": "US"}

    def events(self, quotes, open_markets=("IN",)):
        return wide.mover_events(quotes, self.MARKETS, set(open_markets))

    def test_a_big_move_a_volume_surge_and_a_52_week_high_are_each_found(self):
        out = self.events({"A.NS": q(chg=9.0), "B.NS": q(chg=3.0, vol=4_000_000), "C.NS": q(price=200.0, high=200.0), "D.NS": q(price=50.0, low=50.0)})
        tags = {e["symbol"]: e["tags"] for e in out}
        self.assertEqual(tags["A.NS"], ["+9.0%"])
        self.assertEqual(tags["B.NS"], ["volume 4.0x average"])
        self.assertEqual(tags["C.NS"], ["at 52-week high"])
        self.assertEqual(tags["D.NS"], ["at 52-week low"])

    def test_small_moves_and_stocks_that_hardly_trade_are_ignored(self):
        self.assertEqual(self.events({"A.NS": q(chg=7.9), "B.NS": q(chg=15.0, avg=1000), "C.NS": q(chg=15.0, price=0.5, avg=100_000), "D.NS": q(chg=15.0, avg=0)}), [])

    def test_penny_and_small_stocks_are_included_with_higher_bars_and_a_label(self):
        out = {e["symbol"]: e for e in self.events({"A.NS": q(chg=13.0, price=4.0, avg=400_000, cap=1e8), "B.NS": q(chg=9.0, price=4.0, avg=400_000, cap=1e8),
                                                    "C.NS": q(chg=6.0, price=4.0, avg=400_000, cap=1e8, vol=2_000_000), "D.NS": q(chg=12.5, price=300.0, avg=20_000, cap=1e9)})}
        self.assertEqual(out["A.NS"]["tags"], ["+13.0%"])
        self.assertTrue(out["A.NS"]["small"])
        self.assertNotIn("B.NS", out)                           # 9% is an ordinary day for a penny stock
        self.assertEqual(out["C.NS"]["tags"], ["volume 5.0x average"])
        self.assertTrue(out["D.NS"]["small"])                   # a thinly traded mid-priced stock needs the higher bar too
        self.assertFalse(self.events({"A.NS": q(chg=9.0)})[0]["small"])

    def test_a_small_stock_needs_real_turnover(self):
        self.assertEqual(self.events({"A.NS": q(chg=30.0, price=1.0, avg=20_000, cap=1e8)}), [])       # Rs 20,000 a day: a handful of trades

    def test_small_stocks_do_not_report_52_week_extremes(self):
        self.assertEqual(self.events({"A.NS": q(price=4.0, avg=400_000, cap=1e8, high=4.0, low=None)}), [])

    def test_a_volume_surge_needs_a_real_price_move(self):
        self.assertEqual(self.events({"A.NS": q(chg=0.5, vol=9_000_000)}), [])

    def test_a_closed_market_is_not_an_event(self):
        self.assertEqual(self.events({"A.NS": q(chg=12.0)}, open_markets=("US",)), [])
        self.assertEqual(self.events({"A.NS": q(chg=12.0, state="CLOSED")}), [])

    def test_the_strongest_comes_first(self):
        out = self.events({"A.NS": q(chg=9.0), "B.NS": q(chg=-14.0), "C.NS": q(chg=10.0, vol=4_000_000)})
        self.assertEqual([e["symbol"] for e in out], ["B.NS", "C.NS", "A.NS"])      # a 14% fall, then a 10% rise with a volume surge, then 9%

    def test_open_markets_need_a_few_running_quotes(self):
        quotes = {f"{c}.NS": q() for c in "ABCD"}
        self.assertEqual(wide.open_markets(quotes, {k: "IN" for k in quotes}), {"IN"})
        self.assertEqual(wide.open_markets({"A.NS": q()}, {"A.NS": "IN"}), set())

    def test_top_moves_among_liquid_stocks(self):
        quotes = {"A.NS": q(chg=5.0), "B.NS": q(chg=-6.0), "C.NS": q(chg=9.0, avg=10), "D.NS": q(price=300.0, high=300.0)}
        up, down, (highs, lows) = wide.top_moves(quotes, self.MARKETS, "IN", n=2)
        self.assertEqual(([r[0] for r in up], [r[0] for r in down], (highs, lows)), (["A.NS", "D.NS"], ["B.NS", "D.NS"], (1, 0)))


class RotationTests(unittest.TestCase):
    LIST = [{"symbol": f"S{n}.NS", "name": f"Name {n}", "market": "IN"} for n in range(10)]

    def test_priority_first_then_the_next_slice_and_wrap_around(self):
        batch, cursor = wide.pick_news_batch(self.LIST, 8, ["S3.NS", "S99.NS"], size=4)
        self.assertEqual([s["symbol"] for s in batch], ["S3.NS", "S8.NS", "S9.NS", "S0.NS"])
        self.assertEqual(cursor, 1)
        batch2, cursor2 = wide.pick_news_batch(self.LIST, cursor, [], size=4)
        self.assertEqual([s["symbol"] for s in batch2], ["S1.NS", "S2.NS", "S3.NS", "S4.NS"])
        self.assertEqual(cursor2, 5)

    def test_the_whole_list_is_covered_in_a_cycle(self):
        seen, cursor = set(), 0
        for _ in range(3):
            batch, cursor = wide.pick_news_batch(self.LIST, cursor, [], size=4)
            seen |= {s["symbol"] for s in batch}
        self.assertEqual(len(seen), 10)
        self.assertEqual(wide.pick_news_batch([], 5, [], size=4), ([], 0))

    def test_each_stock_is_looked_up_by_its_own_query_and_only_relevant_fresh_headlines_kept(self):
        asked = []

        def fetcher(query, market, now):
            asked.append(query)
            title = "Billionbrains Garage Ventures posts record profit in the quarter" if "Billionbrains" in query else "Weather is nice today"
            return [{"title": title, "link": "https://x/1", "source": "Wire", "age_min": 20, "relevant": "Billionbrains" in query}]

        stocks = [{"symbol": "GROWW.NS", "name": "Billionbrains Garage Ventures Limited", "market": "IN"}, {"symbol": "XYZ.NS", "name": "Xyz Limited", "market": "IN"}]
        out = wide.wide_news(stocks, NOW_IN_MORNING, None, fetcher, workers=2)
        self.assertEqual(sorted(asked), ['"Billionbrains Garage Ventures" (shares OR stock OR results)', '"Xyz" (shares OR stock OR results)'])
        self.assertEqual(list(out), ["GROWW.NS"])

    def test_a_failing_source_opens_the_breaker_and_stops_asking(self):
        from engine.build import Breaker
        breaker = Breaker(2)
        calls = []

        def fetcher(query, market, now):
            calls.append(query)
            raise FetchError("blocked")

        wide.wide_news(self.LIST, NOW_IN_MORNING, breaker, fetcher, workers=1)
        self.assertEqual(len(calls), 2)


class MoverAlertTests(unittest.TestCase):
    EVENTS = [{"symbol": "AVT.NS", "tags": ["+9.6%", "volume 3.2x average"], "move": 9.6, "price": 105.03, "magnitude": 11.6},
              {"symbol": "FIL.NS", "tags": ["+9.2%"], "move": 9.2, "price": 113.0, "magnitude": 9.2}]

    def test_alerts_are_plain_and_use_the_short_company_name(self):
        lines, items, _ = alerts.mover_alerts(self.EVENTS, {"AVT.NS": "AVT Natural Products Limited"}, {}, 1_794_000_000)
        self.assertEqual(lines[0], "Mover, AVT Natural Products (AVT.NS): +9.6%, volume 3.2x average; price ₹105.03")
        self.assertEqual((items[0]["kind"], items[1]["name"]), ("mover", "FIL.NS"))
        for word in ("bullish", "bearish", "buy", "sell", "strong"):
            self.assertNotIn(word, " ".join(lines).lower())

    def test_each_kind_of_event_is_announced_once_per_stock_per_day_and_the_run_is_capped(self):
        _, _, seen = alerts.mover_alerts(self.EVENTS, {}, {}, 1_794_000_000)
        again, _, _ = alerts.mover_alerts(self.EVENTS, {}, seen, 1_794_000_600)
        self.assertEqual(again, [])
        grown = [{**self.EVENTS[1], "tags": ["+9.2%", "at 52-week high"]}]
        more, _, _ = alerts.mover_alerts(grown, {}, seen, 1_794_000_900)
        self.assertEqual(len(more), 1)                      # a new kind of event on the same stock
        many = [{"symbol": f"S{n}.NS", "tags": ["+9.0%"], "move": 9.0, "price": 10.0, "magnitude": 9.0} for n in range(20)]
        capped, _, seen2 = alerts.mover_alerts(many, {}, {}, 1_794_000_000, limit=8)
        self.assertEqual((len(capped), len(seen2)), (8, 8))
        tomorrow, _, _ = alerts.mover_alerts(self.EVENTS, {}, seen, 1_794_000_000 + 25 * 3600)
        self.assertEqual(len(tomorrow), 2)

    def test_small_stocks_are_labelled_and_limited_to_their_share_of_a_run(self):
        events = [{"symbol": f"P{n}.NS", "tags": ["+14.0%"], "move": 14.0, "price": 4.0, "magnitude": 14.0 - n * 0.1, "small": True} for n in range(6)]
        events.append({"symbol": "BIG.NS", "tags": ["+9.0%"], "move": 9.0, "price": 500.0, "magnitude": 9.0, "small": False})
        lines, items, _ = alerts.mover_alerts(events, {}, {}, 1_794_000_000, limit=8)
        self.assertEqual(sum(1 for i in items if i["small"]), 3)
        self.assertTrue(any(l.startswith("Mover, BIG.NS") for l in lines), "an ordinary mover is not crowded out by small stocks")
        self.assertTrue(lines[0].startswith("Small-stock mover, P0.NS"))

    def test_ordering_helper_places_movers_after_readings_and_deals_before_headlines(self):
        lines = ["R", "D", "N"]
        items = [{"kind": "signal"}, {"kind": "deal"}, {"kind": "news"}]
        lines, items = alerts.insert_after(lines, items, ("signal", "deal"), ["M"], [{"kind": "mover"}])
        self.assertEqual(lines, ["R", "D", "M", "N"])
        lines, items = alerts.insert_after(lines, items, (), ["B"], [{"kind": "brief"}])
        self.assertEqual(lines[0], "B")


class IndexTests(unittest.TestCase):
    PAYLOAD = {"data": [{"index": "NIFTY BANK", "last": "62,197.25", "percentChange": 1.04, "yearHigh": 62200, "yearLow": 50000, "advances": "9", "declines": "3"},
                        {"index": "INDIA VIX", "last": 22.1, "percentChange": 4.0, "yearHigh": 30, "yearLow": 10}, {"index": "NIFTY 50", "last": 25000, "percentChange": 0.2},
                        {"index": "SOME OTHER", "last": 1, "percentChange": 9}, {"last": 5}, "junk"]}

    def test_indices_are_parsed(self):
        out = sessions.parse_indices(self.PAYLOAD)
        self.assertEqual(out["NIFTY BANK"]["last"], 62197.25)
        self.assertEqual(sessions.parse_indices(None), {})

    def test_events_a_move_a_52_week_high_and_a_volatility_level(self):
        events = {e["name"]: dict(e["tags"]) for e in sessions.index_events(sessions.parse_indices(self.PAYLOAD), [], india_open=True)}
        self.assertEqual(events["NIFTY BANK"], {"move1": "+1.0%", "high52": "at 52-week high"})
        self.assertEqual(events["INDIA VIX"], {"level": "above 20"})
        self.assertNotIn("NIFTY 50", events)
        self.assertNotIn("SOME OTHER", events)

    def test_indian_indexes_are_only_alerted_while_the_market_is_open_and_never_twice(self):
        indices = sessions.parse_indices(self.PAYLOAD)
        self.assertEqual(sessions.index_events(indices, [], india_open=False), [])
        watch = [{"symbol": "^GSPC", "name": "S&P 500", "price": 7400.0, "change_pct": -1.9, "as_of": "2026-11-16"},
                 {"symbol": "^NSEBANK", "name": "Bank Nifty", "price": 62190.0, "change_pct": 1.0, "as_of": "2026-11-17"},
                 {"symbol": "^VIX", "name": "US VIX", "price": 22.9, "change_pct": 5.0, "as_of": "2026-11-16"}]
        events = sessions.index_events(indices, watch, india_open=False)
        self.assertEqual(sorted(e["name"] for e in events), ["S&P 500", "US VIX"])      # the Indian index comes from the NSE feed only
        lines, items, seen = sessions.index_alerts(events, {}, 1_794_000_000)
        self.assertEqual(len(lines), 2)
        self.assertIn("Index, S&P 500: -1.9%", lines[0])
        again, _, _ = sessions.index_alerts(events, seen, 1_794_000_000 + 20 * 3600)
        self.assertEqual(again, [], "the same session's move is not announced again the next day")

    def test_a_move_that_grows_to_a_higher_tier_is_announced_again(self):
        first = sessions.index_events({"NIFTY BANK": {"last": 1.0, "pct": 1.2}}, [], True)
        _, _, seen = sessions.index_alerts(first, {}, 1_794_000_000)
        second = sessions.index_events({"NIFTY BANK": {"last": 1.0, "pct": 2.3}}, [], True)
        lines, _, _ = sessions.index_alerts(second, seen, 1_794_000_600)
        self.assertEqual(len(lines), 1)
        self.assertIn("+2.3%", lines[0])


class BriefTests(unittest.TestCase):
    def test_windows_open_once_per_day_on_the_right_weekdays(self):
        self.assertEqual(sessions.due_windows(NOW_IN_MORNING, {}, ("IN",)), ["in_preopen"])
        self.assertEqual(sessions.due_windows(NOW_IN_MORNING, {"in_preopen": "2026-11-17"}, ("IN",)), [])
        close = datetime(2026, 11, 17, 11, 0, tzinfo=timezone.utc)                    # 16:30 IST
        self.assertEqual(sessions.due_windows(close, {}, ("IN",)), ["in_postclose"])
        evening = datetime(2026, 11, 17, 14, 0, tzinfo=timezone.utc)                  # 19:30 IST
        self.assertEqual(sessions.due_windows(evening, {"in_postclose": "2026-11-17"}, ("IN",)), ["in_flows"])
        saturday = datetime(2026, 11, 21, 3, 45, tzinfo=timezone.utc)
        self.assertEqual(sessions.due_windows(saturday, {}, ("IN",)), [])
        us = datetime(2026, 11, 17, 22, 0, tzinfo=timezone.utc)                       # 03:30 IST Wednesday
        self.assertEqual(sessions.due_windows(us, {}, ("US",)), ["us_close"])
        sunday_ist = datetime(2026, 11, 22, 22, 0, tzinfo=timezone.utc)               # Monday 03:30 IST: the US was shut on Sunday
        self.assertEqual(sessions.due_windows(sunday_ist, {}, ("US",)), [])

    def test_preopen_parsing_keeps_equity_series_with_enough_turnover(self):
        def row(symbol, pct, price, turnover, series="EQ"):
            return {"metadata": {"symbol": symbol, "pChange": pct, "lastPrice": price, "totalTurnover": turnover, "series": series}}

        payload = {"data": [row("AAA", 5.0, 100, 5e7), row("BBB", -4.0, 50, 5e7), row("CCC", 20.0, 9, 1000), row("DDD", 9.0, 9, 5e7, "ST")], "advances": 900, "declines": 800}
        up, down, adv, dec = sessions.parse_preopen(payload)
        self.assertEqual(([r[0] for r in up], [r[0] for r in down], adv, dec), (["AAA.NS", "BBB.NS"], ["BBB.NS", "AAA.NS"], 900, 800))

    def test_preopen_brief_content(self):
        pre = ([("AAA.NS", 5.0, 100)], [("BBB.NS", -4.0, 50)], 900, 800)
        lines = sessions.preopen_brief(pre, [("S&P 500", -1.9)], {"date": "16-Nov-2026", "FII/FPI": 4864.68, "DII": -3519.05}, ["Acme (earnings)"])
        self.assertEqual(lines[0], "Pre-open indicative: up AAA +5.0%")
        self.assertIn("Overnight: S&P 500 -1.9%", lines)
        self.assertIn("Previous day net flows (16-Nov-2026): FII/FPI +4,865 cr, DII -3,519 cr", lines)
        self.assertIn("Today: Acme (earnings)", lines)
        self.assertEqual(sessions.preopen_brief(None, [], {}, [])[0], "Pre-open data not available this run")

    def test_flows_and_deals_are_parsed_and_the_postclose_brief_is_assembled(self):
        flows = sessions.parse_flows([{"category": "DII", "date": "16-Nov-2026", "netValue": "-3519.05"}, {"category": "FII/FPI", "date": "16-Nov-2026", "netValue": "4864.68"}])
        self.assertEqual(flows, {"DII": -3519.05, "FII/FPI": 4864.68, "date": "16-Nov-2026"})
        deals = sessions.parse_deals({"BULK_DEALS_DATA": [{"date": "16-Nov-2026", "symbol": "AAA", "qty": "435000", "watp": "92.2", "buySell": "BUY", "clientName": "INDRA AGENCIES"},
                                                          {"date": "13-Nov-2026", "symbol": "OLD", "qty": "1", "watp": "1"}], "BLOCK_DEALS_DATA": []}, "16-Nov-2026")
        self.assertEqual(len(deals), 1)
        indices = {"NIFTY 50": {"last": 25000, "pct": 0.4, "advances": 30, "declines": 20}, "NIFTY BANK": {"last": 1, "pct": 1.0}, "INDIA VIX": {"last": 15.1, "pct": -3.9}}
        lines = sessions.postclose_brief(indices, [("AAA.NS", 9.0, 10)], [("BBB.NS", -8.0, 10)], (5, 2), flows, "16-Nov-2026", deals, [])
        text = " | ".join(lines)
        for part in ("Closes: N50 +0.4%, NBANK +1.0%", "Breadth: 30 advancing, 20 declining", "India VIX 15.1 (-3.9%)", "Top gainers: AAA +9.0%", "At 52-week high: 5, at 52-week low: 2",
                     "Net flows today: FII/FPI +4,865 cr, DII -3,519 cr", "Bulk deal: Indra Agencies bought 435,000 AAA at ₹92.20"):
            self.assertIn(part, text)

    def test_flows_for_another_day_are_not_reported_as_todays(self):
        lines = sessions.postclose_brief({"NIFTY 50": {"last": 1, "pct": 0.1}}, [], [], None, {"date": "13-Nov-2026", "DII": 1.0}, "16-Nov-2026", [], [])
        self.assertNotIn("Net flows", " ".join(lines))

    def test_a_refused_source_is_left_alone_for_six_hours(self):
        source = sessions.Source("NSE test", "https://example/api")
        calls = []

        def refuse(url, **kw):
            calls.append(url)
            raise FetchError("x: HTTP Error 403: Forbidden")

        payload, state = source.fetch(NOW_IN_MORNING, None, lambda s: None, refuse)
        self.assertIsNone(payload)
        self.assertEqual(state["blocked_until"], "2026-11-17T04:15:00+00:00")
        source.fetch(NOW_IN_MORNING.replace(minute=50), state, lambda s: None, refuse)
        self.assertEqual(len(calls), 1)
        ok, good = source.fetch(NOW_IN_MORNING.replace(hour=10), state, lambda s: None, lambda url, **kw: json.dumps({"a": 1}).encode())
        self.assertEqual((ok, good["ok"]), ({"a": 1}, True))


class CompactTests(unittest.TestCase):
    CASES = {
        "Trend: price above 20-day average, 20-day above 50-day": "Trend: P>20D, 20D>50D",
        "Trend: price below 20-day average, 20-day about level with 50-day": "Trend: P<20D, 20D≈50D",
        "Momentum: MACD positive and rising": "Momentum: MACD + rising",
        "Momentum: MACD negative and falling": "Momentum: MACD − falling",
        "RSI 61 is in the normal range": "RSI 61 normal",
        "RSI 74 is overbought, pullback risk": "RSI 74 overbought",
        "Volume spike on a rising day": "Vol spike, up day",
        "Volume pressure: selling pressure over 20 days": "Vol pressure: selling (20D)",
        "Price is above the 200-day average": "P>200D",
        "50-day is below the 200-day (bearish alignment, no recent crossover)": "50D<200D (bearish alignment)",
        "50-day crossed above the 200-day 9 sessions ago (golden cross)": "50D crossed above 200D 9 sessions ago (golden cross)",
        "6-month return is +18.7%": "6M return +18.7%",
        "Trading at 96% of its 52-week range": "52W range: 96%",
        "Volatility 23% a year": "Volatility 23%/yr",
        "Something no rule knows about": "Something no rule knows about",
    }

    def test_every_rule(self):
        for before, after in self.CASES.items():
            self.assertEqual(alerts.compact(before), after, before)

    def test_it_is_shorter_overall_and_never_lengthens_a_line(self):
        for before in self.CASES:
            self.assertLessEqual(len(alerts.compact(before)), len(before))
        self.assertLess(sum(len(alerts.compact(b)) for b in self.CASES), 0.8 * sum(len(b) for b in self.CASES))
        self.assertEqual(alerts.compact(None), "")

    def test_the_reading_card_uses_the_short_form_and_the_message_carries_a_key(self):
        item = {"kind": "signal", "symbol": "A", "name": "Acme", "horizon": "short", "side": "buy", "announced": "hold", "label": "Buy", "score": 40, "price": 100.0,
                "currency": "USD", "levels": None, "reasons": ["Trend: price above 20-day average, 20-day above 50-day", "RSI 61 is in the normal range"]}
        message = digest.render([item], [None], 1_794_000_000)[0][0]
        self.assertIn("Trend: P&gt;20D, 20D&gt;50D", message)
        self.assertIn("Key: D=day", message)
        self.assertTrue(message.endswith(alerts.ADVISORY))


class DigestCardTests(unittest.TestCase):
    def test_movers_indexes_and_briefs_have_their_own_sections_and_counts(self):
        items = [{"kind": "brief", "symbol": "", "name": "India post-close brief", "lines": ["Closes: N50 +0.4%"]},
                 {"kind": "index", "symbol": "", "name": "NIFTY BANK", "tags": ["+1.0%"], "last": 62197.25},
                 {"kind": "mover", "symbol": "AVT.NS", "name": "AVT", "tags": ["+9.6%"], "price": 105.0, "currency": "INR", "move": 9.6},
                 {"kind": "news", "symbol": "A", "name": "Acme", "title": "Acme wins order", "source": "Wire", "link": "https://x", "age_min": 5}]
        message = digest.render(items, [None] * 4, 1_794_000_000)[0][0]
        for part in ("MARKET BRIEF", "INDEXES", "MOVERS", "NEWS", "2 market updates", "1 mover", "1 headline", "Closes: N50 +0.4%", "Level: 62,197.25", "+9.6%"):
            self.assertIn(part, message)
        self.assertLess(message.index("MARKET BRIEF"), message.index("INDEXES"))
        self.assertLess(message.index("INDEXES"), message.index("MOVERS"))
        self.assertLess(message.index("MOVERS"), message.index("NEWS"))


if __name__ == "__main__":
    unittest.main()
