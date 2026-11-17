"""Official NSE data confirms, contradicts or adds events; nothing is called confirmed without it."""
import json
import unittest
from datetime import datetime, timezone

from engine import events, official
from engine.fetch import FetchError
from tests.test_events import TODAY, rec

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
NO_DATES = {"earnings": [], "ex_dividend": None, "dividend_pay": None}


def action(symbol="AAA", subject="Interim Dividend - Rs 4 Per Share", ex="16-Dec-2026", record="17-Dec-2026"):
    return {"symbol": symbol, "comp": "Aaa Ltd", "subject": subject, "exDate": ex, "recDate": record}


def meeting(symbol="AAA", date="20-Jan-2027", purpose="Financial Results", desc="To consider the results for the quarter"):
    return {"bm_symbol": symbol, "bm_date": date, "bm_purpose": purpose, "bm_desc": desc, "bm_timestamp": "29-Oct-2026 16:10:39",
            "attachment": "https://nsearchives.nseindia.in/corporate/x.pdf"}


class ParseTests(unittest.TestCase):
    def test_action_subjects_are_classified(self):
        self.assertEqual(official.classify_action("Interim Dividend - Rs 4 Per Share"), ("dividend", 4.0, None))
        self.assertEqual(official.classify_action("Final Dividend - Rs 12.50 Per Share"), ("dividend", 12.5, None))
        self.assertEqual(official.classify_action("Bonus 1:1"), ("bonus", None, "1:1"))
        self.assertEqual(official.classify_action("Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 1/- Per Share"), ("split", None, "10 to 1 face value"))
        self.assertEqual(official.classify_action("Rights 1:5 @ Premium Rs 100/-")[0], "rights")
        self.assertEqual(official.classify_action("Annual General Meeting")[0], "other")
        self.assertEqual(official.classify_action(None)[0], "other")

    def test_dates(self):
        self.assertEqual(official.nse_date("16-Nov-2026"), "2026-11-16")
        for bad in ("-", "", None, "16/11/2026"):
            self.assertIsNone(official.nse_date(bad))

    def test_actions_and_meetings_keep_only_the_apps_symbols_and_valid_rows(self):
        out = official.parse_actions([action(), action("ZZZ"), action(ex="-"), {"symbol": "AAA"}, "junk"], {"AAA"})
        self.assertEqual(list(out), ["AAA"])
        self.assertEqual(len(out["AAA"]), 1)
        self.assertEqual((out["AAA"][0]["ex_date"], out["AAA"][0]["amount"]), ("2026-12-16", 4.0))
        meetings = official.parse_meetings([meeting(), meeting(purpose="Fund Raising", desc="To consider raising funds"), meeting("ZZZ"), meeting(date="-")], {"AAA"})
        self.assertEqual([m["results"] for m in meetings["AAA"]], [True, False])
        self.assertEqual(official.parse_actions(None, {"AAA"}), {})


class FetchTests(unittest.TestCase):
    def getter(self, answers):
        calls = []

        def get(url, **kw):
            calls.append(url)
            value = answers[len(calls) - 1] if len(calls) <= len(answers) else answers[-1]
            if isinstance(value, Exception):
                raise value
            return json.dumps(value).encode()

        return get, calls

    def test_a_good_answer_is_stored_with_its_time(self):
        get, calls = self.getter([[action()], [meeting()]])
        out = official.fetch_official(NOW, {"AAA"}, getter=get, log=lambda s: None)
        self.assertTrue(out["ok"])
        self.assertEqual(out["fetched_at"], "2026-11-16T12:00:00+00:00")
        self.assertEqual(len(calls), 2)
        self.assertEqual(out["actions"]["AAA"][0]["kind"], "dividend")

    def test_a_recent_answer_is_reused_without_asking_again(self):
        get, calls = self.getter([[action()], [meeting()]])
        first = official.fetch_official(NOW, {"AAA"}, getter=get, log=lambda s: None)
        again = official.fetch_official(NOW.replace(minute=30), {"AAA"}, first, getter=get, log=lambda s: None)
        self.assertTrue(again["reused"])
        self.assertEqual(len(calls), 2)
        later = official.fetch_official(NOW.replace(hour=14), {"AAA"}, first, getter=get, log=lambda s: None)
        self.assertEqual(len(calls), 4)
        self.assertNotIn("reused", later)

    def test_a_refusal_is_logged_starts_a_pause_and_keeps_the_last_good_data(self):
        get, calls = self.getter([[action()], [meeting()]])
        good = official.fetch_official(NOW, {"AAA"}, getter=get, log=lambda s: None)
        logs = []
        refused, calls2 = self.getter([FetchError("x: HTTP Error 403: Forbidden")])
        out = official.fetch_official(NOW.replace(hour=15), {"AAA"}, good, getter=refused, log=logs.append)
        self.assertFalse(out["ok"])
        self.assertIn("403", out["error"])
        self.assertEqual(out["blocked_until"], "2026-11-16T15:30:00+00:00")
        self.assertEqual(out["fetched_at"], "2026-11-16T12:00:00+00:00", "the time of the last SUCCESSFUL check is kept")
        self.assertEqual(len(calls2), 1)
        self.assertTrue(any("failed" in line for line in logs))

    def test_during_the_pause_the_nse_is_not_asked(self):
        get, calls = self.getter([AssertionError("must not be asked")])
        out = official.fetch_official(NOW, {"AAA"}, {"blocked_until": "2026-11-16T18:00:00+00:00", "failures": 5}, getter=get, log=lambda s: None)
        self.assertEqual(calls, [])
        self.assertFalse(out["ok"])
        self.assertEqual(out["blocked_until"], "2026-11-16T18:00:00+00:00")

    def test_other_failures_do_not_start_a_pause(self):
        get, _ = self.getter([FetchError("x: timed out")])
        out = official.fetch_official(NOW, {"AAA"}, getter=get, log=lambda s: None)
        self.assertFalse(out["ok"])
        self.assertIsNone(out["blocked_until"])

    def test_links(self):
        self.assertEqual(official.link_for("TCS.NS", "IN"), "https://www.nseindia.com/get-quotes/equity?symbol=TCS")
        self.assertIn("CIK=BRK.B", official.link_for("BRK-B", "US"))


def feed(actions=(), meetings=(), ok=True, error=None):
    return {"ok": ok, "fetched_at": "2026-11-16T12:00:00+00:00", "checked_at": "2026-11-16T12:00:00+00:00", "error": error,
            "actions": official.parse_actions(list(actions), {"AAA", "BBB", "AAPL"}), "meetings": official.parse_meetings(list(meetings), {"AAA", "BBB", "AAPL"})}


class VerificationTests(unittest.TestCase):
    CAL = {"earnings": ["2027-01-20"], "ex_dividend": "2026-12-16", "dividend_pay": None}

    def build(self, official_feed, record=None):
        return events.build([record or rec(calendar=self.CAL)], TODAY, official=official_feed)

    def find(self, out, kind):
        return next(e for e in out["items"] if e["type"] == kind)

    def test_a_date_the_nse_agrees_with_is_confirmed_with_the_official_figures(self):
        out = self.build(feed([action()], [meeting()]))
        div, res = self.find(out, "ex_dividend"), self.find(out, "earnings")
        self.assertEqual((div["verification"], res["verification"]), ("CONFIRMED", "CONFIRMED"))
        self.assertEqual(div["official"]["amount"], 4.0)
        self.assertIn("nse", div["bases"])
        self.assertEqual(div["last_checked"], "2026-11-16")
        self.assertEqual(out["official"]["confirmed"], 2)

    def test_a_different_official_date_is_a_conflict_and_shows_the_official_date(self):
        out = self.build(feed([action(ex="23-Dec-2026")], [meeting(date="27-Jan-2027")]))
        div, res = self.find(out, "ex_dividend"), self.find(out, "earnings")
        self.assertEqual((div["verification"], res["verification"]), ("CONFLICT", "CONFLICT"))
        self.assertEqual((div["official"]["date"], res["official"]["date"]), ("2026-12-23", "2027-01-27"))
        self.assertEqual(out["official"]["conflicts"], 2)

    def test_nothing_announced_means_unverified_with_a_reason_and_a_link(self):
        out = self.build(feed())
        for e in out["items"]:
            self.assertEqual(e["verification"], "UNVERIFIED")
            self.assertTrue(e["verification_note"])
            self.assertEqual(e["official_url"], "https://www.nseindia.com/get-quotes/equity?symbol=AAA")
        self.assertEqual(out["official"]["confirmed"], 0)

    def test_when_the_feed_is_unreachable_everything_is_unverified_and_says_why(self):
        out = self.build({"ok": False, "fetched_at": None, "checked_at": "2026-11-16T12:00:00+00:00", "error": "HTTP 403: the NSE refuses automated requests", "actions": {}, "meetings": {}})
        self.assertTrue(all(e["verification"] == "UNVERIFIED" for e in out["items"]))
        self.assertIn("403", out["items"][0]["verification_note"])
        self.assertIsNone(out["items"][0]["last_checked"])
        self.assertFalse(out["official"]["ok"])

    def test_without_any_official_data_nothing_is_confirmed(self):
        out = events.build([rec(calendar=self.CAL)], TODAY)
        self.assertTrue(all(e["verification"] == "UNVERIFIED" for e in out["items"]))

    def test_us_events_are_never_confirmed_and_link_to_the_filings(self):
        out = events.build([rec("AAPL", calendar=self.CAL, market="US")], TODAY, official=feed([action("AAPL")]))
        for e in out["items"]:
            self.assertEqual(e["verification"], "UNVERIFIED")
            self.assertIn("sec.gov", e["official_url"])
            self.assertIsNone(e["last_checked"], "no official source was consulted, so there is no check date")
            self.assertIn("No official machine-readable feed", e["verification_note"])

    def test_official_events_that_yahoo_missed_are_added_as_confirmed(self):
        out = self.build(feed([action(subject="Bonus 1:1", ex="10-Dec-2026"), action(subject="Interim Dividend - Rs 2 Per Share", ex="11-Dec-2026")], [meeting()]), rec(calendar=NO_DATES))
        self.assertEqual(sorted(e["type"] for e in out["items"]), ["bonus", "earnings", "ex_dividend"])
        for e in out["items"]:
            self.assertEqual((e["verification"], e["confidence"]), ("CONFIRMED", "official"))
        self.assertEqual(self.find(out, "bonus")["ratio"], "1:1")
        self.assertEqual(self.find(out, "ex_dividend")["amount"], 2.0)

    def test_past_and_out_of_range_official_actions_are_ignored(self):
        out = self.build(feed([action(ex="01-Nov-2026"), action(ex="01-Dec-2030")]), rec(calendar=NO_DATES))
        self.assertEqual(out["items"], [])

    def test_announced_without_a_date_items_stay_unverified(self):
        news = {"AAA.NS": [{"type": "split", "date": None, "evidence": {"title": "t", "source": "s", "link": "https://x"}, "ratio": "1:2"}]}
        out = events.build([rec(calendar=self.CAL)], TODAY, news, official=feed())
        self.assertEqual(out["announcements"][0]["verification"], "UNVERIFIED")
        self.assertIn("nseindia.com", out["announcements"][0]["official_url"])


if __name__ == "__main__":
    unittest.main()
