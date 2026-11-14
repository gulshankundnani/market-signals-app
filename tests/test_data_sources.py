import unittest
from datetime import date

from engine import alerts, discover, exchange, ipo
from engine.fetch import FetchError

BHAV = """SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER
AAA, EQ, 13-Nov-2026, 10, 10, 11, 9, 10, 10, 10, 1000, 1.0, 50, 600, 60.00
BBB, BE, 13-Nov-2026, 10, 10, 11, 9, 10, 10, 10, 1000, 1.0, 50, 600, 60.00
CCC, EQ, 13-Nov-2026, 10, 10, 11, 9, 10, 10, 10, 1000, 1.0, 50, -, -
DDD, EQ, 13-Nov-2026, 10, 10, 11, 9, 10, 10, 10, 1000, 1.0, 50, 600, 160.00
"""

DEALS = """Date,Symbol,Security Name,Client Name,Buy/Sell,Quantity Traded,Trade Price / Wght. Avg. Price,Remarks
13-NOV-2026,AAA,AAA Ltd,Fund One,BUY,1000,10.5,-
13-NOV-2026,AAA,AAA Ltd,Fund Two,SELL,400,10.5,-
13-NOV-2026,ZZZ,ZZZ Ltd,Fund Three,BUY,10,5,-
13-NOV-2026,AAA,AAA Ltd,Bad Row,BUY,abc,10,-
13-NOV-2026,AAA,AAA Ltd,Bad Side,HOLD,10,10,-
"""

EQUITY_L = """SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE
NEWCO,New Co Limited,EQ,10-NOV-2026,10,1,INE000000001,10
OLDCO,Old Co Limited,EQ,06-OCT-2008,10,1,INE000000002,10
SMEIPO,Sme Co Limited,SM,10-NOV-2026,10,1,INE000000003,10
BADDATE,Bad Date Limited,EQ,not a date,10,1,INE000000004,10
FUTURE,Future Co Limited,EQ,01-DEC-2026,10,1,INE000000005,10
"""

NASDAQ_LISTED = """Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
GOOD|Good Corp. - Common Stock|Q|N|N|100|N|N
TESTY|Test Corp|Q|Y|N|100|N|N
FUNDX|Some ETF|G|N|N|100|Y|N
WARR|Foo Corp - Warrants|Q|N|N|100|N|N
SPAC|Bar Acquisition Corp. - Class A|Q|N|N|100|N|N
File Creation Time: 1113202608:00|||||||
"""
OTHER_LISTED = """ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
NYSEA|NYSE Alpha Inc. Common Stock|N|NYSEA|N|100|N|NYSEA
BRK.B|Berkshire|N|BRK.B|N|100|N|BRK.B
File Creation Time: 1113202608:00|||||||
"""


class BhavcopyTests(unittest.TestCase):
    def test_parse(self):
        day, rows = exchange.parse_bhavcopy(BHAV)
        self.assertEqual(day, "2026-11-13")
        self.assertEqual(list(rows), ["AAA"])
        self.assertEqual(rows["AAA"]["deliv_pct"], 60.0)
        self.assertEqual(rows["AAA"]["trades"], 50.0)

    def test_parse_garbage(self):
        self.assertEqual(exchange.parse_bhavcopy("<html>blocked</html>"), (None, {}))

    def test_sync_fetches_ten_weekdays_and_skips_weekends(self):
        calls = []

        def fetcher(day):
            calls.append(day)
            return day.strftime("%Y-%m-%d"), {"AAA": {"deliv_pct": 50.0, "trades": 10.0}, "OTHER": {"deliv_pct": 1.0, "trades": 1.0}}

        history = exchange.sync_delivery({}, date(2026, 11, 14), ["AAA"], fetcher)
        self.assertEqual(len(history), 10)
        self.assertTrue(all(d.weekday() < 5 for d in calls))
        self.assertTrue(all(list(v) == ["AAA"] for v in history.values()))

    def test_sync_is_incremental(self):
        full = {f"2026-11-{d:02d}": {"AAA": [50.0, 1.0]} for d in (2, 3, 4, 5, 6, 9, 10, 11, 12, 13)}
        calls = []
        exchange.sync_delivery(full, date(2026, 11, 14), ["AAA"], lambda day: calls.append(day))
        self.assertEqual(calls, [])

    def test_sync_survives_holidays_and_outages(self):
        self.assertEqual(exchange.sync_delivery({}, date(2026, 11, 14), ["AAA"], lambda day: None), {})

    def test_history_is_trimmed(self):
        old = {f"2026-01-{d:02d}": {"AAA": [1.0, 1.0]} for d in range(1, 29)}
        out = exchange.sync_delivery(old, date(2026, 11, 14), ["AAA"], lambda day: None)
        self.assertEqual(len(out), exchange.KEEP_DATES)

    def test_delivery_view(self):
        series = [("d1", 40.0), ("d2", 42.0), ("d3", 38.0), ("d4", 60.0)]
        self.assertEqual(exchange.delivery_view(series, 1.0)["tone"], "pos")
        self.assertEqual(exchange.delivery_view(series, -1.0)["tone"], "neg")
        low = [("d1", 50.0), ("d2", 50.0), ("d3", 50.0), ("d4", 30.0)]
        self.assertIn("Low delivery", exchange.delivery_view(low, 1.0)["text"])
        self.assertIsNone(exchange.delivery_view(series[:2], 1.0))
        self.assertIsNone(exchange.delivery_view([("a", 0.0), ("b", 0.0), ("c", 5.0)], 1.0))

    def test_deals(self):
        deals = exchange.parse_deals(DEALS, "bulk")
        self.assertEqual(len(deals), 3)
        summary = exchange.summarise_deals(deals, ["AAA"])
        self.assertEqual(list(summary), ["AAA"])
        self.assertAlmostEqual(summary["AAA"]["net_value"], 1000 * 10.5 - 400 * 10.5)
        self.assertEqual(summary["AAA"]["deals"][0]["client"], "Fund One")


class DiscoverTests(unittest.TestCase):
    TODAY = date(2026, 11, 14)

    def test_india_new_listings(self):
        found = discover.india_new_listings(EQUITY_L, self.TODAY)
        self.assertEqual([f["symbol"] for f in found], ["NEWCO.NS"])
        self.assertEqual(found[0]["listed"], "2026-11-10")

    def test_us_directory_filters(self):
        symbols = discover.us_symbols(NASDAQ_LISTED, OTHER_LISTED)
        self.assertEqual(sorted(symbols), ["GOOD", "NYSEA"])

    def test_us_directory_bad_header(self):
        self.assertEqual(discover.parse_us_directory("nope|nada\nx|y", "Symbol", "Test Issue", "ETF"), {})
        self.assertEqual(discover.parse_us_directory("", "Symbol", "Test Issue", "ETF"), {})

    def test_us_first_run_only_records(self):
        self.assertEqual(discover.us_new_listings({"AAA": "A"}, [], self.TODAY), [])

    def test_us_new_symbols_detected(self):
        found = discover.us_new_listings({"AAA": "A Inc", "BBB": "B Inc"}, ["AAA"], self.TODAY)
        self.assertEqual([f["symbol"] for f in found], ["BBB"])

    def test_registry_expires_and_caps(self):
        old = {"symbol": "OLD", "name": "Old", "market": "US", "listed": "2025-01-01"}
        fresh = {"symbol": "NEW", "name": "New", "market": "US", "listed": "2026-11-01"}
        merged = discover.merge_registry([old, fresh], [fresh, {"symbol": "N2", "name": "N2", "market": "US", "listed": "2026-11-05"}], self.TODAY)
        self.assertEqual([e["symbol"] for e in merged], ["N2", "NEW"])

    def test_discover_keeps_state_when_offline(self):
        def offline(url, **kwargs):
            raise FetchError("down")

        state = {"new_listings": [{"symbol": "KEEP.NS", "name": "Keep", "market": "IN", "listed": "2026-11-01"}], "us_symbols": ["AAA"]}
        registry, us_list = discover.discover(state, self.TODAY, fetch=offline)
        self.assertEqual([e["symbol"] for e in registry], ["KEEP.NS"])
        self.assertEqual(us_list, ["AAA"])

    def test_discover_end_to_end(self):
        def fake(url, **kwargs):
            if "EQUITY_L" in url:
                return EQUITY_L.encode()
            return (NASDAQ_LISTED if "nasdaqlisted" in url else OTHER_LISTED).encode()

        registry, us_list = discover.discover({"us_symbols": ["GOOD"]}, self.TODAY, fetch=fake)
        self.assertEqual({e["symbol"] for e in registry}, {"NEWCO.NS", "NYSEA"})
        self.assertEqual(us_list, ["GOOD", "NYSEA"])


def record(name, short, long_, alert_items=()):
    return {"name": name, "price": 100.0, "currency": "INR", "short": {"label": short}, "long": {"label": long_},
            "news": {"alerts": list(alert_items)}}


class AlertTests(unittest.TestCase):
    def test_side(self):
        self.assertEqual([alerts.side(x) for x in ("Strong Buy", "Buy", "Hold", "N/A", "Sell", "Strong Sell")],
                         ["buy", "buy", "hold", "hold", "sell", "sell"])

    def test_flip_alert(self):
        lines, _ = alerts.build_messages({"A": record("A Ltd", "Hold", "Hold")}, {"A": record("A Ltd", "Buy", "Hold")}, ["A"], [], False)
        self.assertEqual(len(lines), 1)
        self.assertIn("Hold -> Buy", lines[0])

    def test_no_alert_within_same_side(self):
        lines, _ = alerts.build_messages({"A": record("A", "Buy", "Hold")}, {"A": record("A", "Strong Buy", "Hold")}, ["A"], [], False)
        self.assertEqual(lines, [])

    def test_na_to_signal_is_not_a_flip(self):
        lines, _ = alerts.build_messages({"A": record("A", "N/A", "N/A")}, {"A": record("A", "Buy", "Buy")}, ["A"], [], False)
        self.assertEqual(lines, [])

    def test_first_run_is_silent_but_records_news(self):
        item = {"title": "Fraud probe", "link": "https://x/1", "source": "S", "strong": "neg", "age_min": 5}
        lines, seen = alerts.build_messages({}, {"A": record("A", "Buy", "Buy", [item])}, ["A"], [], True)
        self.assertEqual(lines, [])
        self.assertEqual(len(seen), 1)

    def test_news_alert_once(self):
        item = {"title": "Fraud probe", "link": "https://x/1", "source": "S", "strong": "neg", "age_min": 5}
        current = {"A": record("A Ltd", "Buy", "Buy", [item])}
        lines, seen = alerts.build_messages(current, current, ["A"], [], False)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("Negative news"))
        again, _ = alerts.build_messages(current, current, ["A"], seen, False)
        self.assertEqual(again, [])

    def test_unwatched_and_missing_symbols_ignored(self):
        lines, _ = alerts.build_messages({}, {"A": record("A", "Buy", "Buy")}, ["B"], [], False)
        self.assertEqual(lines, [])

    def test_caps_message_count(self):
        prev = {f"S{i}": record(f"S{i}", "Hold", "Hold") for i in range(30)}
        cur = {f"S{i}": record(f"S{i}", "Buy", "Hold") for i in range(30)}
        lines, _ = alerts.build_messages(prev, cur, list(cur), [], False)
        self.assertEqual(len(lines), alerts.MAX_LINES)


class IpoTests(unittest.TestCase):
    def test_price_range(self):
        self.assertEqual(ipo._price_range("10.00-12.00"), (10.0, 12.0))
        self.assertEqual(ipo._price_range("$10.00"), (10.0, 10.0))
        self.assertEqual(ipo._price_range("$10.00 - $12.00"), (10.0, 12.0))
        self.assertEqual(ipo._price_range(""), (None, None))
        self.assertEqual(ipo._price_range(None), (None, None))
        self.assertEqual(ipo._price_range("TBD"), (None, None))

    def test_parse_calendar(self):
        payload = {"data": {
            "priced": {"rows": [{"proposedTickerSymbol": "NARA", "companyName": "Narragansett", "proposedSharePrice": "10.00",
                                 "sharesOffered": "9,098,800", "pricedDate": "11/02/2026", "dollarValueOfSharesOffered": "$90,988,000"}]},
            "upcoming": {"upcomingTable": {"rows": [{"proposedTickerSymbol": "UPC", "companyName": "Upcoming Inc", "proposedSharePrice": "14.00-16.00",
                                                     "sharesOffered": "5,000,000", "expectedPriceDate": "11/20/2026"}, {"companyName": ""}]}},
            "filed": {"rows": None},
        }}
        deals = ipo.parse_calendar(payload)
        self.assertEqual([d["status"] for d in deals], ["upcoming", "priced"])
        self.assertEqual(deals[0]["price_low"], 14.0)
        self.assertEqual(deals[0]["price_high"], 16.0)
        self.assertEqual(deals[1]["shares"], 9098800.0)
        self.assertEqual(deals[1]["amount"], 90988000.0)

    def test_parse_calendar_junk(self):
        self.assertEqual(ipo.parse_calendar(None), [])
        self.assertEqual(ipo.parse_calendar({"data": None}), [])


if __name__ == "__main__":
    unittest.main()
