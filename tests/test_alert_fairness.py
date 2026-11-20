"""Alerts must not be dominated by a few heavily covered stocks, and block deals must be announced the moment the NSE lists them,
for any NSE stock."""
import json
import unittest
from datetime import datetime, timezone

from engine import alerts, deals_live, digest
from engine.fetch import FetchError
from tests.test_alert_messages import headline, record

NOW = 1_794_000_000
HOUR = 3600


def run(stocks, history=None, seen=None, now_ts=NOW):
    """One alert run over {symbol: [headlines]}. Returns (lines, items, history, seen)."""
    current = {s: record("Hold", 0, name=s.title(), news={"fresh": heads}) for s, heads in stocks.items()}
    items, hist = [], {} if history is None else history
    lines, new_seen = alerts.build_messages({}, current, sorted(current), seen or [], False, True, {}, items, hist, now_ts)
    return lines, items, hist, new_seen


class NewsFairnessTests(unittest.TestCase):
    def test_a_heavily_covered_stock_is_capped_per_day(self):
        history, seen = {}, []
        sent = 0
        topics = ["Nvidia earnings preview analysts expect record revenue", "Asian stocks slide as chip makers fall", "Regulators open probe into export licences",
                  "Chief executive sells shares under trading plan", "New data centre partnership announced with cloud provider"]
        for n, topic in enumerate(topics):
            _, items, history, seen = run({"NVDA": [headline(topic, n)]}, history, seen, NOW + n * 600)
            sent += len(items)
        self.assertEqual(sent, alerts.NEWS_PER_STOCK_DAY)

    def test_the_cap_resets_after_24_hours(self):
        history, seen = {}, []
        for n in range(2):
            _, _, history, seen = run({"NVDA": [headline(f"Distinct topic {n} alpha beta", n)]}, history, seen, NOW + n * 600)
        _, items, history, seen = run({"NVDA": [headline("Fresh topic gamma delta", 9)]}, history, seen, NOW + 25 * HOUR)
        self.assertEqual(len(items), 1)

    def test_the_same_story_from_another_outlet_is_not_sent_twice(self):
        history = {}
        _, first, history, seen = run({"AAA": [headline("Acme wins 500 crore order from railways", 1)]}, history)
        _, second, history, _ = run({"AAA": [headline("Acme wins Rs 500 crore railways order", 2)]}, history, seen, NOW + 600)
        self.assertEqual((len(first), len(second)), (1, 0))

    def test_a_different_story_for_the_same_stock_still_goes_out(self):
        history = {}
        _, first, history, seen = run({"AAA": [headline("Acme wins 500 crore order from railways", 1)]}, history)
        _, second, history, _ = run({"AAA": [headline("Acme chairman resigns after board dispute", 2)]}, history, seen, NOW + 600)
        self.assertEqual((len(first), len(second)), (1, 1))

    def test_stocks_alerted_least_go_first_when_not_everything_fits(self):
        history = {"BUSY": [{"ts": NOW - 100, "title": "earlier busy story"}]}
        stocks = {"BUSY": [headline("Busy has another brand new development", 1, age=1)]}
        for n in range(alerts.NEWS_PER_RUN):
            stocks[f"QUIET{n}"] = [headline(f"Quiet company number {n} announces something unique", n + 10, age=30 + n)]
        _, items, history, _ = run(stocks, history)
        sent = [i["symbol"] for i in items]
        self.assertEqual(len(sent), alerts.NEWS_PER_RUN)
        self.assertNotIn("BUSY", sent, "the stock with an alert already today waits behind stocks with none, even though its news is newest")

    def test_among_equals_the_newest_goes_first(self):
        stocks = {f"S{n}": [headline(f"Unique wording {chr(97 + n)} {chr(110 + n)} xyz{n}", n, age=10 + n)] for n in range(alerts.NEWS_PER_RUN + 3)}
        _, items, _, _ = run(stocks)
        ages = [i["age_min"] for i in items]
        self.assertEqual(ages, sorted(ages))
        self.assertEqual(max(ages), 10 + alerts.NEWS_PER_RUN - 1)

    def test_old_history_is_ignored_and_pruned(self):
        history = {"AAA": [{"ts": NOW - 30 * HOUR, "title": "old"}, {"ts": NOW - 30 * HOUR, "title": "older"}]}
        _, items, history, _ = run({"AAA": [headline("Brand new matter entirely different", 3)]}, history)
        self.assertEqual(len(items), 1)
        self.assertEqual(len(history["AAA"]), 1)

    def test_skipped_headlines_are_still_marked_seen(self):
        history = {"AAA": [{"ts": NOW, "title": "one"}, {"ts": NOW, "title": "two"}]}
        _, items, _, seen = run({"AAA": [headline("Third headline today for this stock", 5)]}, history)
        self.assertEqual(items, [])
        self.assertEqual(len(seen), 1)                 # it will not come back in a later run as if new

    def test_story_similarity(self):
        self.assertTrue(alerts.same_story("Acme wins 500 crore order", "Acme wins Rs 500 crore order today"))
        self.assertFalse(alerts.same_story("Acme wins 500 crore order", "Acme chairman resigns"))
        self.assertFalse(alerts.same_story("", "anything"))


LIVE = {"data": [
    {"session": "Session 1", "symbol": "GROWW", "series": "BL", "lastPrice": 219, "previousClose": 224.92, "pchange": -2.63, "totalTradedVolume": 87000000,
     "totalTradedValue": 19053000000, "lastUpdateTime": "17-Nov-2026 08:49:55"},
    {"session": "Session 1", "symbol": "ALKEM", "series": "BL", "lastPrice": 5050, "previousClose": 5136, "pchange": -1.67, "totalTradedVolume": 597825,
     "totalTradedValue": 3019016250, "lastUpdateTime": "17-Nov-2026 09:00:07"},
    {"session": "Session 1", "symbol": "OLD", "series": "BL", "lastPrice": 10, "previousClose": 10, "pchange": 0, "totalTradedVolume": 1000,
     "totalTradedValue": 10000, "lastUpdateTime": "13-Nov-2026 09:00:07"},
    {"symbol": "", "lastPrice": 1, "totalTradedVolume": 1}, {"symbol": "BAD", "lastPrice": 0, "totalTradedVolume": 5}, "junk"]}
TODAY = "2026-11-17"


class DealAlertTests(unittest.TestCase):
    def deals(self):
        return deals_live.parse_live(LIVE)

    def test_the_live_feed_is_parsed_and_junk_rows_skipped(self):
        out = self.deals()
        self.assertEqual([d["symbol"] for d in out], ["GROWW", "ALKEM", "OLD"])
        self.assertEqual((out[0]["qty"], out[0]["price"], out[0]["date"], out[0]["time"]), (87000000, 219.0, "2026-11-17", "08:49"))
        self.assertEqual(deals_live.parse_live(None), [])
        self.assertEqual(deals_live.parse_live({"data": None}), [])

    def test_an_untracked_stock_is_announced_with_its_size_and_window(self):
        lines, items, seen = alerts.deal_alerts(self.deals(), [], {}, NOW, TODAY)
        self.assertEqual(len(lines), 2)
        first = lines[0]                                   # largest first
        self.assertIn("GROWW (GROWW.NS)", first)
        self.assertIn("87,000,000 shares at ₹219.00", first)
        self.assertIn("₹1,905 crore", first)
        self.assertIn("morning window", first)
        self.assertIn("-2.63% against the previous close", first)
        self.assertEqual(items[0]["kind"], "deal")
        self.assertEqual(items[0]["symbol"], "GROWW.NS")
        self.assertEqual(len(seen), 3)

    def test_a_tracked_stock_uses_its_company_name(self):
        lines, _, _ = alerts.deal_alerts(self.deals(), [], {"ALKEM.NS": "Alkem Laboratories"}, NOW, TODAY)
        self.assertTrue(any("Alkem Laboratories (ALKEM.NS)" in l for l in lines))

    def test_a_deal_is_announced_once(self):
        _, _, seen = alerts.deal_alerts(self.deals(), [], {}, NOW, TODAY)
        lines, items, seen2 = alerts.deal_alerts(self.deals(), seen, {}, NOW + 600, TODAY)
        self.assertEqual((lines, items), ([], []))
        self.assertEqual(seen2, seen)

    def test_a_new_deal_in_a_later_run_is_announced(self):
        _, _, seen = alerts.deal_alerts(self.deals(), [], {}, NOW, TODAY)
        more = {"data": LIVE["data"] + [{"session": "Session 2", "symbol": "NEWCO", "lastPrice": 100, "previousClose": 99, "pchange": 1.0, "totalTradedVolume": 5000000,
                                         "totalTradedValue": 500000000, "lastUpdateTime": "17-Nov-2026 14:10:00"}]}
        lines, _, _ = alerts.deal_alerts(deals_live.parse_live(more), seen, {}, NOW + 600, TODAY)
        self.assertEqual(len(lines), 1)
        self.assertIn("afternoon window", lines[0])

    def test_yesterdays_deals_are_never_announced_as_new(self):
        lines, _, seen = alerts.deal_alerts(self.deals(), [], {}, NOW, "2026-11-18")
        self.assertEqual(lines, [])
        self.assertEqual(len(seen), 3)

    def test_the_alert_describes_the_deal_without_judging_it(self):
        text = " ".join(alerts.deal_alerts(self.deals(), [], {}, NOW, TODAY)[0]).lower()
        for word in ("bullish", "bearish", "positive", "negative", "bad", "good", "buy", "sell", "strong"):
            self.assertNotIn(word, text)

    def test_at_most_a_limited_number_are_announced_in_one_run(self):
        many = {"data": [{"session": "Session 1", "symbol": f"S{n}", "lastPrice": 10, "totalTradedVolume": 1000 + n, "totalTradedValue": 10000 + n,
                          "lastUpdateTime": "17-Nov-2026 09:00:00"} for n in range(25)]}
        lines, _, seen = alerts.deal_alerts(deals_live.parse_live(many), [], {}, NOW, TODAY, limit=10)
        self.assertEqual(len(lines), 10)
        self.assertEqual(len(seen), 25)

    def test_order_in_the_digest_is_readings_then_deals_then_headlines(self):
        lines, items = ["R1", "H1", "H2"], [{"kind": "signal"}, {"kind": "news"}, {"kind": "news"}]
        merged_lines, merged_items = alerts.merge_deals(lines, items, ["D1", "D2"], [{"kind": "deal"}, {"kind": "deal"}])
        self.assertEqual(merged_lines, ["R1", "D1", "D2", "H1", "H2"])
        self.assertEqual([i["kind"] for i in merged_items], ["signal", "deal", "deal", "news", "news"])


class DealCardTests(unittest.TestCase):
    def test_the_digest_renders_a_deal_card_with_its_own_section_and_count(self):
        _, items, _ = alerts.deal_alerts(deals_live.parse_live(LIVE), [], {}, NOW, TODAY)
        message = digest.render(items, [None] * len(items), NOW)[0][0]
        self.assertIn("BLOCK DEALS", message)
        self.assertIn("2 block deals", message)
        self.assertIn("GROWW (GROWW.NS)", message)
        self.assertIn("Buyer and seller are published after the close", message)
        self.assertIn("nseindia.com/market-data/large-deals", message)
        self.assertNotIn("NEWS", message)


class LiveFeedTests(unittest.TestCase):
    NOWDT = datetime(2026, 11, 17, 4, 0, tzinfo=timezone.utc)

    def getter(self, value):
        calls = []

        def get(url, **kw):
            calls.append(url)
            if isinstance(value, Exception):
                raise value
            return json.dumps(value).encode()

        return get, calls

    def test_a_good_answer(self):
        get, calls = self.getter(LIVE)
        out = deals_live.fetch_block_deals(self.NOWDT, getter=get, log=lambda s: None)
        self.assertTrue(out["ok"])
        self.assertEqual(len(out["deals"]), 3)
        self.assertEqual(calls, [deals_live.URL])

    def test_a_refusal_is_logged_and_the_nse_is_left_alone_for_six_hours(self):
        logs = []
        get, calls = self.getter(FetchError("x: HTTP Error 403: Forbidden"))
        out = deals_live.fetch_block_deals(self.NOWDT, getter=get, log=logs.append)
        self.assertFalse(out["ok"])
        self.assertEqual(out["blocked_until"], "2026-11-17T04:30:00+00:00")
        self.assertTrue(any("failed" in l and "403" in l for l in logs))
        get2, calls2 = self.getter(AssertionError("must not be asked"))
        again = deals_live.fetch_block_deals(self.NOWDT.replace(minute=10), out, getter=get2, log=lambda s: None)
        self.assertEqual(calls2, [])
        self.assertEqual(again["blocked_until"], "2026-11-17T04:30:00+00:00")

    def test_other_failures_do_not_start_a_pause(self):
        get, _ = self.getter(FetchError("x: timed out"))
        out = deals_live.fetch_block_deals(self.NOWDT, getter=get, log=lambda s: None)
        self.assertEqual((out["ok"], out["blocked_until"]), (False, None))

    def test_today_in_india(self):
        self.assertEqual(deals_live.today_ist(datetime(2026, 11, 16, 20, 0, tzinfo=timezone.utc)), "2026-11-17")   # 01:30 IST the next day
        self.assertEqual(deals_live.today_ist(self.NOWDT), "2026-11-17")


class CompanyNameTests(unittest.TestCase):
    def test_names_come_from_the_nse_equity_list(self):
        from engine import discover
        text = "SYMBOL,NAME OF COMPANY, SERIES,DATE OF LISTING\nGROWW,Billionbrains Garage Ventures Limited, EQ,12-Nov-2025\nXYZ,Not Equity Ltd, BE,01-Jan-2020\nBAD SYMBOL!,Bad, EQ,01-Jan-2020\n"
        self.assertEqual(discover.nse_company_names(text), {"GROWW.NS": "Billionbrains Garage Ventures Limited", "XYZ.NS": "Not Equity Ltd"})   # BE and BZ are listed stocks too
        self.assertEqual(discover.nse_series(text), {"XYZ.NS": "BE"})
        self.assertEqual(discover.nse_company_names(""), {})


if __name__ == "__main__":
    unittest.main()
