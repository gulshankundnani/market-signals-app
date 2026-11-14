import contextlib
import io
import json
import unittest
import urllib.error
from datetime import date
from unittest.mock import patch

from engine import alerts, build, discover, exchange, ipo
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


class AlertLogTests(unittest.TestCase):
    NOW = 1_800_000_000

    def test_log_adds_new_entries_with_stable_ids(self):
        log, added = alerts.update_log([], ["A: Hold -> Buy", "B: Buy -> Sell"], self.NOW)
        self.assertEqual([e["text"] for e in log], ["A: Hold -> Buy", "B: Buy -> Sell"])
        self.assertEqual(len(added), 2)
        self.assertEqual(len({e["id"] for e in log}), 2)
        again, added_again = alerts.update_log(log, ["A: Hold -> Buy"], self.NOW + 60)
        self.assertEqual(added_again, [])
        self.assertEqual(len(again), 2)

    def test_same_text_on_another_day_is_a_new_alert(self):
        log, _ = alerts.update_log([], ["A: Hold -> Buy"], self.NOW)
        _, added = alerts.update_log(log, ["A: Hold -> Buy"], self.NOW + 86400)
        self.assertEqual(len(added), 1)

    def test_old_entries_are_dropped_and_the_log_is_capped(self):
        old = {"id": "old", "ts": self.NOW - 4 * 86400, "text": "old"}
        log, _ = alerts.update_log([old], [], self.NOW)
        self.assertEqual(log, [])
        lines = [f"alert {i}" for i in range(alerts.LOG_LIMIT + 10)]
        log, added = alerts.update_log([], lines, self.NOW)
        self.assertEqual(len(log), alerts.LOG_LIMIT)
        self.assertEqual(log[-1]["text"], lines[-1])
        self.assertEqual(len(added), len(lines))

    def test_log_survives_junk_in_saved_state(self):
        log, added = alerts.update_log([None, "x", {"id": "k", "ts": self.NOW, "text": "kept"}], ["new"], self.NOW)
        self.assertEqual([e["text"] for e in log], ["kept", "new"])
        self.assertEqual(len(added), 1)

    def test_log_is_json_safe(self):
        import json
        log, _ = alerts.update_log([], ["TCS (TCS.NS) short-term signal: Hold -> Buy. Price 2407.50 INR"], self.NOW)
        json.dumps(log, allow_nan=False)



class FakeResponse:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TelegramTests(unittest.TestCase):
    URLOPEN = "engine.alerts.urllib.request.urlopen"

    def test_sends_the_text_to_the_chat(self):
        with patch(self.URLOPEN, return_value=FakeResponse()) as opened:
            self.assertTrue(alerts.send_telegram("123:token", "-1001", "TCS: Hold -> Buy"))
        request = opened.call_args[0][0]
        self.assertEqual(request.full_url, "https://api.telegram.org/bot123:token/sendMessage")
        body = json.loads(request.data)
        self.assertEqual((body["chat_id"], body["text"]), ("-1001", "TCS: Hold -> Buy"))
        self.assertEqual(request.get_header("Content-type"), "application/json")

    def test_the_token_is_never_in_the_message_body(self):
        with patch(self.URLOPEN, return_value=FakeResponse()) as opened:
            alerts.send_telegram("123:secret-token", "1", "hello")
        self.assertNotIn(b"secret-token", opened.call_args[0][0].data)

    def test_long_messages_are_cut_to_telegrams_limit(self):
        with patch(self.URLOPEN, return_value=FakeResponse()) as opened:
            alerts.send_telegram("t", "1", "x" * 9000)
        self.assertEqual(len(json.loads(opened.call_args[0][0].data)["text"]), alerts.TELEGRAM_MAX_CHARS)

    def test_failures_return_false_instead_of_raising(self):
        for error in (urllib.error.URLError("down"), TimeoutError(), ConnectionError()):
            with patch(self.URLOPEN, side_effect=error):
                self.assertFalse(alerts.send_telegram("t", "1", "x"))
        with patch(self.URLOPEN, return_value=FakeResponse(500)):
            self.assertFalse(alerts.send_telegram("t", "1", "x"))

    def test_test_alert_needs_both_secrets_and_a_delivered_message(self):
        quiet = contextlib.redirect_stdout(io.StringIO())
        with quiet, patch.dict("os.environ", {}, clear=True):
            self.assertEqual(build.send_test_alert(), 1)
        with quiet, patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "t"}, clear=True):
            self.assertEqual(build.send_test_alert(), 1)
        both = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1"}
        with quiet, patch.dict("os.environ", both, clear=True), patch("engine.build.alerts.send_telegram", return_value=True) as sent:
            self.assertEqual(build.send_test_alert(), 0)
        sent.assert_called_once()
        with quiet, patch.dict("os.environ", both, clear=True), patch("engine.build.alerts.send_telegram", return_value=False):
            self.assertEqual(build.send_test_alert(), 1)

    def test_test_alert_flag_skips_the_full_build(self):
        with patch("engine.build.send_test_alert", return_value=0) as test_alert, patch("engine.build.run") as full_run:
            self.assertEqual(build.main(["--test-alert"]), 0)
        test_alert.assert_called_once()
        full_run.assert_not_called()


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
