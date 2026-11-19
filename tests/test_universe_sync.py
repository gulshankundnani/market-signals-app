import unittest
from datetime import datetime, timezone

from engine import universe_sync as us
from engine.fetch import FetchError

CSV = ("Company Name,Industry,Symbol,Series,ISIN Code\nHDFC Bank Ltd.,Financial Services,HDFCBANK,EQ,X\n"
       "Sun Pharmaceutical Industries Ltd.,Healthcare,SUNPHARMA,EQ,X\nNew Co Ltd.,Information Technology,NEWCO,EQ,X\n")
BASE = [["HDFCBANK.NS", "HDFC Bank", "IN", "Banks", ["psu"]]]


def at(h, m=0, day=19):                         # an IST wall-clock time, as UTC
    minutes = h * 60 + m - 330
    return datetime(2026, 11, day, minutes // 60, minutes % 60, tzinfo=timezone.utc) if minutes >= 0 else datetime(2026, 11, day - 1, (minutes + 1440) // 60, (minutes + 1440) % 60, tzinfo=timezone.utc)


class UniverseSyncTests(unittest.TestCase):
    def fetcher(self, calls):
        def get(url, **kw):
            calls.append(url)
            return CSV.encode()
        return get

    def test_nothing_before_ten_am_ist(self):
        calls = []
        stocks, state, added = us.sync(at(9, 30), {}, [list(s) for s in BASE], self.fetcher(calls), log=lambda *_: None)
        self.assertEqual((calls, added, len(stocks)), ([], [], 1))

    def test_adds_new_stocks_once_a_day_and_keeps_them(self):
        calls = []
        stocks, state, added = us.sync(at(10, 5), {}, [list(s) for s in BASE], self.fetcher(calls), log=lambda *_: None)
        self.assertEqual(added, ["SUNPHARMA.NS", "NEWCO.NS"])
        self.assertEqual(stocks[0], BASE[0])                      # existing entries are untouched
        again, state2, added2 = us.sync(at(11, 0), state, [list(s) for s in BASE], self.fetcher(calls), log=lambda *_: None)
        self.assertEqual(len(calls), 1)                           # not asked again the same day
        self.assertEqual([s[0] for s in again], ["HDFCBANK.NS", "SUNPHARMA.NS", "NEWCO.NS"])   # but still part of the list
        self.assertEqual(added2, [])

    def test_a_new_day_asks_again(self):
        calls = []
        _, state, _ = us.sync(at(10, 5), {}, [list(s) for s in BASE], self.fetcher(calls), log=lambda *_: None)
        us.sync(at(10, 5, day=20), state, [list(s) for s in BASE], self.fetcher(calls), log=lambda *_: None)
        self.assertEqual(len(calls), 2)

    def test_failure_is_retried_after_an_hour_not_every_run(self):
        calls = []

        def refused(url, **kw):
            calls.append(url)
            raise FetchError("HTTP 403")
        _, state, added = us.sync(at(10, 5), {}, [list(s) for s in BASE], refused, log=lambda *_: None)
        self.assertEqual((added, state.get("date")), ([], None))
        us.sync(at(10, 20), state, [list(s) for s in BASE], refused, log=lambda *_: None)
        self.assertEqual(len(calls), 1)
        us.sync(at(11, 10), state, [list(s) for s in BASE], refused, log=lambda *_: None)
        self.assertEqual(len(calls), 2)


class NewListingCapTests(unittest.TestCase):
    def test_every_listing_in_the_window_is_kept_not_only_the_newest_forty(self):
        from datetime import date
        from engine import discover
        rows = ["SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE"]
        rows += [f"S{n},Company {n},EQ,{(date(2026, 11, 19).toordinal() - n):d},1,1,X,1" for n in range(0)]
        for n in range(80):
            day = date.fromordinal(date(2026, 11, 18).toordinal() - n)
            rows.append(f"S{n},Company {n},EQ,{day.strftime('%d-%b-%Y').upper()},2,1,X,2")
        found = discover.india_new_listings("\n".join(rows), date(2026, 11, 19))
        self.assertEqual(len(found), 80)
