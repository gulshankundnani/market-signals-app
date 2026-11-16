import unittest
from datetime import date, datetime, timedelta, timezone

from engine import build, event_news, events, fetch

PUB = date(2026, 11, 16)
NOW = datetime(2026, 11, 16, 6, 0, tzinfo=timezone.utc)


def types(title, pub=PUB):
    return [(e["type"], e["date"], e["amount"], e["ratio"]) for e in event_news.extract(title, pub)]


class ExtractTests(unittest.TestCase):
    def test_an_ex_dividend_date_and_amount_in_a_headline(self):
        self.assertEqual(types("Microsoft Goes Ex-Dividend Nov 19. Here's What Backs the $0.98 Payout"), [("ex_dividend", "2026-11-19", 0.98, None)])
        self.assertEqual(types("Cochin Shipyard to go ex-dividend on 16 November, interim dividend of Rs 3 per share"), [("ex_dividend", "2026-11-16", 3.0, None)])

    def test_the_record_date_counts_as_the_ex_date_and_today_and_tomorrow_are_understood(self):
        self.assertEqual(types("Infosys dividend 2026: Record date tomorrow | Know dividend payout")[0][:2], ("ex_dividend", "2026-11-17"))
        self.assertEqual(types("PFC dividend: record date today")[0][:2], ("ex_dividend", "2026-11-16"))
        self.assertEqual(types("ABC declares dividend of $0.52 per share, holders of record November 28")[0][:2], ("ex_dividend", "2026-11-28"))

    def test_an_announced_amount_without_a_date_is_kept_as_an_undated_announcement(self):
        self.assertEqual(types("Infosys dividend alert! IT major announces Rs 26 per share dividend. Check record date"), [("ex_dividend", None, 26.0, None)])

    def test_splits_and_bonus_issues_need_a_precise_phrase_and_a_ratio_or_a_date(self):
        self.assertEqual(types("XYZ Ltd announces stock split in the ratio of 1:5; record date November 25"), [("split", "2026-11-25", None, "1:5")])
        self.assertEqual(types("ABC announces bonus issue 1:1, record date Dec 5"), [("bonus", "2026-12-05", None, "1:1")])
        self.assertEqual(types("Company X sub-division of shares: face value of Rs 10 to Rs 2, ex-date 3 December"), [("split", "2026-12-03", None, "face value 10 to 2")])
        self.assertEqual(types("DEF approves stock split, details awaited"), [], "no ratio and no date: nothing to say")

    def test_results_and_board_meeting_dates_and_payment_dates(self):
        self.assertEqual(types("Company X to announce Q2 results on November 18")[0][:2], ("earnings", "2026-11-18"))
        [event] = event_news.extract("ABC Ltd board meeting on 20 Nov 2026 to consider Q2 results and dividend", PUB)
        self.assertEqual((event["date"], event["label"]), ("2026-11-20", "board meeting"))
        self.assertEqual(types("Company declares quarterly dividend of $0.52 per share payable December 12")[0][:3], ("dividend_payment", "2026-12-12", 0.52))

    def test_other_uses_of_the_same_words_are_not_events(self):
        for headline in ("Apple Just Updated Old iPad Split View Feature in iPadOS 27.2", "ITC shares gain modestly after Q2 results; analysts split on outlook",
                         "Apple's AirPods 4 Drop Below $90 With Your Choice of 5 Free Bonus Offers", "Microsoft outage disrupts Kern County email, postpones hospital board meeting",
                         "Wallstreetbets Favorites Split As Microsoft Works Through Outage", "Binance Will Support the Microsoft Corporation (MSFT) Cash Dividend Distribution via",
                         "ITC sets board meeting date to declare Q2 results 2026", "Stock split rumours swirl around the tech sector"):
            self.assertEqual(types(headline), [], headline)

    def test_a_date_that_starts_a_range_is_not_a_date(self):
        self.assertEqual(types("PSU dividend this week Nov 16-20: record date for several stocks"), [])
        self.assertEqual(types("Ex-dividend 16 to 20 November for Cochin"), [])

    def test_years_are_resolved_from_the_headline_date(self):
        self.assertEqual(types("Ex-dividend date January 5 for ABC, dividend of $1 per share")[0][:2], ("ex_dividend", "2027-01-05"))
        self.assertEqual(types("Ex-dividend Dec 3, 2026 for ABC, dividend of $1")[0][:2], ("ex_dividend", "2026-12-03"))
        self.assertEqual(types("Ex-dividend Nov 10 for ABC, dividend of $1")[0][1], "2026-11-10", "a recent date stays in this year, as a past date")
        self.assertEqual(types("Ex-dividend Feb 30 for ABC, dividend of $1")[0][1], None, "an impossible date is no date")


class ProminentSourceTests(unittest.TestCase):
    def test_major_outlets_pass_and_others_do_not(self):
        for source in ("CNBC TV18", "The Economic Times", "Livemint", "Mint", "Moneycontrol.com", "Business Standard", "Reuters", "ET Now", "Bloomberg"):
            self.assertTrue(event_news.is_prominent(source), source)
        for source in ("Upstox", "Analytics Insight", "News9live", "TradingView", "MarketBeat", "Binance", "Minty Fresh Blog", "IndiaIPO", "", None):
            self.assertFalse(event_news.is_prominent(source), source)


def item(title, source="CNBC TV18", age_min=60, link="https://news.google.com/a"):
    return {"title": title, "source": source, "age_min": age_min, "link": link}


REC = {"symbol": "MSFT", "name": "Microsoft", "market": "US"}


class FromHeadlinesTests(unittest.TestCase):
    def test_only_prominent_relevant_recent_future_headlines_become_events_with_their_evidence(self):
        items = [item("Microsoft Goes Ex-Dividend Nov 19. Here's What Backs the $0.98 Payout", "Yahoo Finance", 2880),
                 item("Microsoft Goes Ex-Dividend Nov 19 says blog", "Analytics Insight"),
                 item("Dividend Roundup: Southwest Airlines, Cisco, Microsoft ex-dividend Nov 19", "Reuters"),
                 item("Microsoft Goes Ex-Dividend Nov 10", "Reuters"),
                 item("Microsoft Goes Ex-Dividend Nov 19", "Reuters", 60 * 24 * 40)]
        found = event_news.from_headlines(REC, items, NOW)
        self.assertEqual([(e["type"], e["date"]) for e in found], [("ex_dividend", "2026-11-19")])
        self.assertEqual(found[0]["evidence"], {"headline": "Microsoft Goes Ex-Dividend Nov 19. Here's What Backs the $0.98 Payout",
                                                "source": "Yahoo Finance", "link": "https://news.google.com/a", "age_min": 2880})

    def test_the_date_is_resolved_from_when_the_headline_was_published(self):
        old = item("Microsoft ex-dividend January 5, dividend of $0.98 per share", "Reuters", 60 * 24 * 20)
        self.assertEqual(event_news.from_headlines(REC, [old], NOW)[0]["date"], "2027-01-05")

    def test_undated_announcements_are_kept_and_the_list_is_capped_with_dated_ones_first(self):
        items = [item(f"Microsoft announces dividend of ${n}.00 per share", "Reuters", 10 * n) for n in range(1, 10)]
        items.append(item("Microsoft ex-dividend Dec 1, dividend of $0.98", "Reuters", 5000))
        found = event_news.from_headlines(REC, items, NOW)
        self.assertEqual(len(found), event_news.KEEP_PER_STOCK)
        self.assertEqual(found[0]["date"], "2026-12-01")

    def test_headlines_without_an_age_are_ignored(self):
        self.assertEqual(event_news.from_headlines(REC, [{"title": "Microsoft ex-dividend Nov 19", "source": "Reuters"}], NOW), [])


class SyncTests(unittest.TestCase):
    RECORDS = [{"symbol": s, "name": f"Company {s}", "market": "US"} for s in ("AAA", "BBB", "CCC", "DDD")]

    def test_each_stock_is_looked_up_once_a_day_in_batches_and_gently(self):
        asked, pauses = [], []

        def fetcher(rec, now):
            asked.append(rec["symbol"])
            return []

        first = event_news.sync(self.RECORDS, {}, NOW, fetcher, per_run=3, sleep=pauses.append)
        self.assertEqual(asked, ["AAA", "BBB", "CCC"])
        self.assertEqual(len(pauses), 2)
        second = event_news.sync(self.RECORDS, first, NOW, fetcher, per_run=3, sleep=pauses.append)
        self.assertEqual(asked[3:], ["DDD"])
        third = event_news.sync(self.RECORDS, second, NOW, fetcher, per_run=3, sleep=pauses.append)
        self.assertEqual((len(asked), third["day"]), (4, "2026-11-16"), "all done for the day: no more lookups")
        tomorrow = event_news.sync(self.RECORDS, third, NOW + timedelta(days=1), fetcher, per_run=3, sleep=pauses.append)
        self.assertEqual(asked[4:], ["AAA", "BBB", "CCC"], "a new day starts the round again")
        self.assertIn("DDD", tomorrow["stocks"], "yesterday's result is kept until it is refreshed")

    def test_the_breaker_stops_the_round_and_stocks_that_left_the_list_are_dropped(self):
        breaker = build.Breaker(2)

        def failing(rec, now):
            raise fetch.FetchError("blocked")

        cache = {"day": "2026-11-15", "stocks": {"GONE": {"day": "2026-11-15", "events": [{"type": "split"}]}}}
        out = event_news.sync(self.RECORDS, cache, NOW, failing, per_run=4, breaker=breaker, sleep=lambda s: None)
        self.assertTrue(breaker.open)
        self.assertEqual(out["stocks"], {}, "nothing stored for failures; the removed stock is gone")

    def test_junk_in_the_saved_copy_is_ignored(self):
        for junk in (None, "x", [], {"stocks": None}, {"stocks": {"AAA": "x", "BBB": None}}):
            out = event_news.sync(self.RECORDS[:1], junk, NOW, lambda r, n: [], sleep=lambda s: None)
            self.assertEqual(list(out["stocks"]), ["AAA"])


class MergeTests(unittest.TestCase):
    TODAY = "2026-11-16"

    def stock(self, calendar=None, dividend=None):
        return {"symbol": "MSFT", "name": "Microsoft", "market": "US", "currency": "USD", "fundamentals": {"events": calendar or {}}, "dividend": dividend}

    def news_event(self, kind="ex_dividend", day="2026-11-19", amount=0.98, ratio=None):
        return {"type": kind, "date": day, "amount": amount, "ratio": ratio, "label": "ex-date",
                "evidence": {"headline": "Microsoft Goes Ex-Dividend Nov 19", "source": "Yahoo Finance", "link": "https://x", "age_min": 60}}

    def test_a_news_date_that_agrees_with_yahoo_marks_the_event_as_agreed_by_two_sources(self):
        out = events.build([self.stock({"ex_dividend": "2026-11-19"})], self.TODAY, {"MSFT": [self.news_event()]})
        [e] = out["items"]
        self.assertEqual((e["bases"], e["agree"], e["amount"], e["announced_amount"]), (["yahoo", "news"], True, 0.98, 0.98))
        self.assertEqual(e["evidence"]["source"], "Yahoo Finance")
        self.assertEqual(out["news_check"], {"n": 1, "agree_pct": 100})

    def test_a_news_date_close_to_an_estimate_also_agrees(self):
        history = [{"date": "2025-11-20", "amount": 0.9}]
        profile = {"payments_per_year": 4, "frequency": "Quarterly", "last_date": "2026-08-20", "last_amount": 0.9, "overdue": False, "history": history}
        out = events.build([self.stock(dividend=profile)], self.TODAY, {"MSFT": [self.news_event(day="2026-11-20")]})
        [e] = out["items"]
        self.assertEqual((e["date"], e["bases"], e["agree"]), ("2026-11-19", ["estimate", "news"], True), "within two days of the estimate")

    def test_a_news_date_that_differs_from_yahoo_is_shown_separately_and_flagged(self):
        out = events.build([self.stock({"ex_dividend": "2026-11-27"})], self.TODAY, {"MSFT": [self.news_event(day="2026-11-19")]})
        self.assertEqual([(e["date"], e["bases"], e.get("conflict", False)) for e in out["items"]],
                         [("2026-11-19", ["news"], True), ("2026-11-27", ["yahoo"], False)])
        self.assertIn("Differs from the Yahoo Finance date of 2026-11-27", out["items"][0]["detail"])
        self.assertEqual(out["news_check"], {"n": 1, "agree_pct": 0})

    def test_events_only_the_news_knows_about_are_added_with_their_evidence(self):
        out = events.build([self.stock()], self.TODAY, {"MSFT": [self.news_event("split", "2026-12-02", None, "1:5")]})
        [e] = out["items"]
        self.assertEqual((e["type"], e["title"], e["bases"], e["confidence"], e["ratio"]), ("split", "Stock split", ["news"], "news", "1:5"))
        self.assertIn("ratio 1:5", e["detail"])

    def test_past_and_far_off_news_dates_are_left_out_and_undated_ones_become_announcements(self):
        undated = {"type": "bonus", "date": None, "amount": None, "ratio": "1:1", "label": "bonus", "evidence": {"headline": "h", "source": "Reuters", "link": "", "age_min": 5}}
        out = events.build([self.stock()], self.TODAY, {"MSFT": [self.news_event(day="2026-11-10"), self.news_event(day="2027-09-01"), undated]})
        self.assertEqual(out["items"], [])
        self.assertEqual([(a["type"], a["ratio"], a["title"]) for a in out["announcements"]], [("bonus", "1:1", "Bonus issue announced")])

    def test_an_announced_dividend_amount_is_attached_to_the_stocks_dividend_event(self):
        announced = {"type": "ex_dividend", "date": None, "amount": 4.1, "ratio": None, "label": "announced",
                     "evidence": {"headline": "PFC announces dividend of Rs 4.10 per share", "source": "CNBC TV18", "link": "", "age_min": 9000}}
        out = events.build([self.stock({"ex_dividend": "2026-11-17"}, {"last_amount": 3.9})], self.TODAY, {"MSFT": [announced]})
        [e] = out["items"]
        self.assertEqual((e["amount"], e["announced_amount"]), (3.9, 4.1))
        self.assertEqual(len(out["announcements"]), 1)

    def test_no_news_changes_nothing(self):
        out = events.build([self.stock({"ex_dividend": "2026-11-19"})], self.TODAY)
        self.assertEqual((out["items"][0]["bases"], "agree" in out["items"][0], out["news_check"], out["announcements"]), (["yahoo"], False, None, []))


if __name__ == "__main__":
    unittest.main()
