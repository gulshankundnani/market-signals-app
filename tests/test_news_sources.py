import json
import unittest
from datetime import datetime, timedelta, timezone

from engine import news_sources as ns
from engine.fetch import FetchError

NOW = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)          # 15:30 IST

NSE = [{"an_dt": "20-Nov-2026 15:56:58", "attchmntText": "Blue Dart Express Limited has informed the Exchange regarding a press release.", "attchmntFile": "https://nsearchives.nseindia.in/corporate/x.pdf",
        "desc": "Press Release", "sm_name": "Blue Dart Express Limited", "symbol": "BLUEDART", "smIndustry": "Travel And Transport"},
       {"desc": "Bad row without a symbol"}, "junk"]
BSE = ("<?xml version='1.0'?><rss><channel><item><title>Triveni Engineering &amp; Industries Ltd (532356)</title><link>https://www.bseindia.com/a.pdf</link>"
       "<scripcode>532356</scripcode><description>Outcome of the Board Meeting held on Friday</description><pubDate>20-Nov-2026 15:58:08</pubDate></item>"
       "<item><title>Nothing Ltd</title><link></link><description></description><pubDate>bad date</pubDate></item></channel></rss>")
RSS = ("<?xml version='1.0'?><rss><channel><item><title>Infosys profit jumps 12% in second quarter</title><link>https://www.livemint.com/a</link>"
       "<pubDate>Fri, 20 Nov 2026 09:00:00 GMT</pubDate></item></channel></rss>")


class ParseTests(unittest.TestCase):
    def test_nse_rows_keep_headline_source_time_and_link(self):
        items = ns.parse_nse(NSE)
        self.assertEqual(len(items), 1)
        i = items[0]
        self.assertEqual((i["symbol"], i["kind"], i["source"], i["desc"]), ("BLUEDART.NS", "nse", "NSE", "Press Release"))
        self.assertTrue(i["link"].startswith("https://nsearchives") and i["ts"])
        self.assertEqual(ns.parse_nse(None), [])

    def test_bse_titles_lose_the_scrip_code_and_bad_rows_keep_no_time(self):
        items = ns.parse_bse(BSE)
        self.assertEqual(items[0]["name"], "Triveni Engineering & Industries Ltd")
        self.assertEqual((items[0]["kind"], items[0]["source"]), ("bse", "BSE"))
        self.assertIsNotNone(items[0]["ts"])
        self.assertIsNone(items[1]["ts"])
        self.assertEqual(ns.parse_bse(""), [])

    def test_media_and_newsdata(self):
        media = ns.parse_media(RSS, "Mint", NOW)
        self.assertEqual((media[0]["source"], media[0]["kind"]), ("Mint", "media"))
        nd = ns.parse_newsdata({"results": [{"title": "Reliance gains", "link": "https://z", "source_name": "Some Paper", "pubDate": "2026-11-20 08:00:00"}, {"nothing": 1}]})
        self.assertEqual(len(nd), 1)
        self.assertEqual(nd[0]["source"], "Some Paper via NewsData.io")
        self.assertEqual(ns.parse_newsdata(None), [])


class GuardTests(unittest.TestCase):
    def test_a_failure_is_contained_and_a_refusal_pauses_the_provider(self):
        def boom():
            raise FetchError("HTTP Error 403: Forbidden")

        items, status = ns._guard("X", None, NOW, boom, lambda s: None)
        self.assertEqual(items, [])
        self.assertFalse(status["ok"])
        self.assertIn("paused", status["error"])
        self.assertIsNotNone(status["blocked_until"])
        calls = []
        items2, status2 = ns._guard("X", {"blocked_until": status["blocked_until"], "failures": status["failures"]}, NOW + timedelta(minutes=5), lambda: calls.append(1), lambda s: None)
        self.assertEqual((items2, calls), ([], []), "no request while paused")

    def test_a_plain_failure_is_not_a_pause(self):
        def boom():
            raise FetchError("timed out")

        _, status = ns._guard("X", None, NOW, boom, lambda s: None)
        self.assertIsNone(status["blocked_until"])

    def test_a_recent_success_is_not_asked_again(self):
        previous = {"ok": True, "fetched_at": (NOW - timedelta(minutes=2)).isoformat(), "items": 5}
        items, status = ns._guard("X", previous, NOW, lambda: self.fail("must not fetch"), lambda s: None)
        self.assertIsNone(items)
        self.assertTrue(status["ok"])


class CollectTests(unittest.TestCase):
    def getter(self, broken=()):
        def get(url, **kw):
            if any(b in url for b in broken):
                raise FetchError("HTTP Error 403: Forbidden")
            if "corporate-announcements" in url:
                return json.dumps(NSE).encode()
            if "bseindia" in url:
                return BSE.encode()
            return RSS.encode()
        return get

    def test_every_provider_runs_and_one_failing_does_not_stop_the_rest(self):
        items, status = ns.collect(NOW, {}, self.getter(), lambda s: None, environ={})
        self.assertEqual({i["kind"] for i in items}, {"nse", "bse", "media"})
        self.assertTrue(all(s["ok"] for s in status.values()))
        self.assertNotIn("NewsData.io", status, "off without a key")
        items, status = ns.collect(NOW, {}, self.getter(broken=("bseindia", "business-standard")), lambda s: None, environ={})
        self.assertFalse(status["BSE announcements"]["ok"])
        self.assertFalse(status["Business Standard"]["ok"])
        self.assertTrue(status["NSE announcements"]["ok"] and status["Mint"]["ok"])
        self.assertIn("nse", {i["kind"] for i in items})

    def test_the_optional_provider_needs_a_key_from_the_environment_only(self):
        seen = []

        def get(url, **kw):
            seen.append(url)
            return json.dumps({"results": []}).encode() if "newsdata" in url else json.dumps(NSE).encode() if "corporate" in url else RSS.encode() if "xml" not in url or "bse" not in url else BSE.encode()

        _, status = ns.collect(NOW, {}, get, lambda s: None, environ={"NEWSDATA_API_KEY": "secret-key"})
        self.assertIn("NewsData.io", status)
        self.assertTrue(any("newsdata.io" in u for u in seen))


class ArchiveAndMappingTests(unittest.TestCase):
    def item(self, title, link, hours_old=1, source="Mint"):
        return {"title": title, "link": link, "source": source, "ts": NOW.timestamp() - hours_old * 3600, "kind": "media"}

    def test_the_archive_keeps_new_items_once_and_drops_old_ones(self):
        old = [self.item("a", "https://a", 1), self.item("old", "https://old", 200)]
        merged = ns.merge_archive(old, [self.item("a again", "https://a", 0.5), self.item("b", "https://b", 0.2)], NOW)
        self.assertEqual([i["link"] for i in merged], ["https://b", "https://a"])
        self.assertEqual(ns.merge_archive(None, [], NOW), [])

    def test_names_match_across_exchanges_and_headlines_find_their_company(self):
        index = ns.NameIndex({"INFY.NS": "Infosys Limited", "TRIVENI.NS": "Triveni Engineering & Industries Limited", "ICICIBANK.NS": "ICICI Bank Limited"})
        self.assertEqual(index.by_company("Triveni Engineering & Industries Ltd"), "TRIVENI.NS")
        self.assertIsNone(index.by_company("Unknown Co Ltd"))
        self.assertEqual(index.by_headline("Infosys profit jumps 12% in second quarter", "Mint"), ["INFY.NS"])
        self.assertEqual(index.by_headline("Markets close higher", "Mint"), [])

    def test_mapping_splits_stock_news_from_market_news(self):
        index = ns.NameIndex({"INFY.NS": "Infosys Limited", "TRIVENI.NS": "Triveni Engineering & Industries Limited"})
        items = ns.parse_nse(NSE) + ns.parse_bse(BSE)[:1] + ns.parse_media(RSS, "Mint", NOW) + [{"title": "Sensex rises", "kind": "media", "source": "Mint", "ts": None, "link": "", "symbol": None}]
        by_symbol, market = ns.map_items(items, index)
        self.assertEqual(set(by_symbol), {"BLUEDART.NS", "TRIVENI.NS", "INFY.NS"})
        self.assertEqual([m["title"] for m in market], ["Sensex rises"])

    def test_company_names_are_normalised(self):
        self.assertEqual(ns.norm_name("Triveni Engineering &amp; Industries Ltd."), "triveni engineering and industries")
        self.assertEqual(ns.norm_name("The Tata Power Company Limited"), "tata power")


class AmbiguousNameTests(unittest.TestCase):
    def test_two_listings_with_one_name_are_not_guessed(self):
        index = ns.NameIndex({"A.NS": "Acme Limited", "AACME.NS": "Acme Ltd", "B.NS": "Beta Limited"})
        self.assertIsNone(index.by_company("ACME LIMITED"))
        self.assertEqual(index.by_company("Beta Ltd."), "B.NS")
