import unittest
from datetime import date, datetime, timezone

from engine import event_news, events

PUB = date(2026, 11, 16)
NOW = datetime(2026, 11, 16, 6, 0, tzinfo=timezone.utc)
TODAY = "2026-11-16"
TITLE = "PSU dividend this week Nov 16-20: Cochin Shipyard, Power Finance Corp, SCI to Oil India - Check record dates"


def item(title, source="Livemint", age_min=60):
    return {"title": title, "source": source, "age_min": age_min, "link": "https://news.google.com/a"}


class RoundupExtractionTests(unittest.TestCase):
    def test_a_short_date_range_with_record_date_wording_is_a_roundup_event(self):
        e = event_news.roundup_event(TITLE, PUB)
        self.assertEqual((e["type"], e["date"], e["until"], e["actions"]), ("ex_dividend", "2026-11-16", "2026-11-20", ["dividend"]))
        mixed = event_news.roundup_event("Upcoming dividend, stock split, buyback record date next week, 16-20 Nov: MRF, SCI, PFC", PUB)
        self.assertEqual((mixed["type"], mixed["actions"]), ("corporate_action", ["buyback", "dividend", "split"]))
        self.assertEqual(event_news.roundup_event("Ex-dividend 16 to 20 November for Cochin", PUB)["until"], "2026-11-20")

    def test_headlines_without_a_range_or_without_action_wording_are_not_roundups(self):
        for title in ("Record Dates This Week: Cochin Shipyard, Page Industries among 12 stocks seeing corporate actions",
                      "Markets 16-20 Nov: Nifty outlook", "Dividend stocks November 2026: bonus, split, buyback dates",
                      "Sensex gains 16-20 points in early trade"):
            self.assertIsNone(event_news.roundup_event(title, PUB), title)

    def test_a_long_or_backwards_range_is_not_a_week(self):
        self.assertIsNone(event_news.week_range("Dividends Nov 1-28", PUB))
        self.assertIsNone(event_news.week_range("Dividends Nov 20-16", PUB))

    def test_a_prominent_roundup_naming_the_company_becomes_an_event_but_other_sources_and_other_companies_do_not(self):
        rec = {"symbol": "PFC.NS", "name": "Power Finance Corporation", "market": "IN"}
        found = event_news.from_headlines(rec, [item(TITLE)], NOW)
        self.assertEqual([(e["type"], e["date"], e["until"]) for e in found], [("ex_dividend", "2026-11-16", "2026-11-20")])
        self.assertEqual(found[0]["evidence"]["source"], "Livemint")
        self.assertEqual(event_news.from_headlines(rec, [item(TITLE, "Upstox")], NOW), [], "not a prominent outlet")
        other = {"symbol": "TITAN.NS", "name": "Titan Company", "market": "IN"}
        self.assertEqual(event_news.from_headlines(other, [item(TITLE)], NOW), [], "Titan is not in the roundup")
        ended = item(TITLE.replace("16-20", "2-6"))
        self.assertEqual(event_news.from_headlines(rec, [ended], NOW), [], "a range that has already ended is dropped")


def stock(calendar=None):
    return {"symbol": "PFC.NS", "name": "Power Finance Corporation", "market": "IN", "currency": "INR",
            "fundamentals": {"events": calendar or {}}, "dividend": None}


def roundup(kind="ex_dividend", start="2026-11-16", until="2026-11-20", actions=("dividend",)):
    return {"type": kind, "date": start, "until": until, "amount": None, "ratio": None, "label": "roundup", "actions": list(actions),
            "evidence": {"headline": TITLE, "source": "Livemint", "link": "https://x", "age_min": 30}}


class RoundupMergeTests(unittest.TestCase):
    def test_a_range_that_contains_the_yahoo_date_agrees_with_it(self):
        out = events.build([stock({"ex_dividend": "2026-11-17"})], TODAY, {"PFC.NS": [roundup()]})
        [e] = out["items"]
        self.assertEqual((e["date"], e["bases"], e["agree"]), ("2026-11-17", ["yahoo", "news"], True))
        self.assertEqual(out["news_check"], {"n": 1, "agree_pct": 100})

    def test_a_roundup_alone_adds_a_this_week_event_with_the_range_and_the_actions(self):
        out = events.build([stock()], TODAY, {"PFC.NS": [roundup("corporate_action", actions=("buyback", "dividend", "split"))]})
        [e] = out["items"]
        self.assertEqual((e["type"], e["title"], e["date"], e["until"], e["source"], e["bases"]),
                         ("corporate_action", "Corporate action (record or ex-date)", "2026-11-16", "2026-11-20", "News roundup", ["news"]))
        self.assertIn("buyback, dividend, split", e["detail"])
        self.assertIn("exact day is not given", e["detail"])

    def test_a_range_that_misses_the_yahoo_date_does_not_claim_agreement(self):
        out = events.build([stock({"ex_dividend": "2026-12-01"})], TODAY, {"PFC.NS": [roundup()]})
        self.assertEqual(sorted(e["bases"][0] for e in out["items"]), ["news", "yahoo"])
        self.assertTrue(all("agree" not in e for e in out["items"]))
        self.assertEqual(out["news_check"], {"n": 1, "agree_pct": 0})

    def test_a_range_a_day_off_still_counts_as_close_in_the_agreement_check_only_within_a_day(self):
        out = events.build([stock({"ex_dividend": "2026-11-21"})], TODAY, {"PFC.NS": [roundup()]})
        self.assertEqual(out["news_check"], {"n": 1, "agree_pct": 100}, "one day past the end of the range")


if __name__ == "__main__":
    unittest.main()
