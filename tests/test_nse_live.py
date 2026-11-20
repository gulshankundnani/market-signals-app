import json
import unittest
from datetime import datetime, timedelta, timezone

from engine import nse_live
from engine.fetch import FetchError

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 11, 20, 9, 22, tzinfo=timezone.utc)          # 14:52 IST


def answer(rows, stamp="20-Nov-2026 14:50:22"):
    block = {"timestamp": stamp, "data": rows}
    return json.dumps({"NIFTY": block, "allSec": block, "legends": []}).encode()


def row(symbol, ltp, prev=100.0, pct=1.0):
    return {"symbol": symbol, "ltp": ltp, "prev_price": prev, "perChange": pct}


class ParseTests(unittest.TestCase):
    def test_prices_and_the_newest_timestamp(self):
        prices, when = nse_live.parse(json.loads(answer([row("TCS", 2400.5), row("BAD", None), row("ZERO", 0), {"ltp": 5}])))
        self.assertEqual(list(prices), ["TCS.NS"])
        self.assertEqual(prices["TCS.NS"]["price"], 2400.5)
        self.assertEqual(when, datetime(2026, 11, 20, 14, 50, 22, tzinfo=IST))

    def test_junk_gives_nothing(self):
        self.assertEqual(nse_live.parse(None), ({}, None))
        self.assertEqual(nse_live.parse({"NIFTY": {"data": "x"}}), ({}, None))


class FetchTests(unittest.TestCase):
    def test_two_requests_one_per_list(self):
        urls = []

        def getter(url, **kw):
            urls.append(url)
            return answer([row("TCS", 2400.0)])

        out = nse_live.fetch(NOW, None, getter, lambda s: None, clock=lambda: NOW)
        self.assertTrue(out["ok"])
        self.assertEqual(len(urls), 2)
        self.assertEqual(out["as_of"], "2026-11-20T14:50:22+05:30")

    def test_a_refusal_pauses_the_source_and_is_never_retried_around(self):
        def refused(url, **kw):
            raise FetchError("HTTP Error 403: Forbidden")

        out = nse_live.fetch(NOW, None, refused, lambda s: None)
        self.assertFalse(out["ok"])
        self.assertEqual(out["prices"], {})
        self.assertIsNotNone(out["blocked_until"])
        calls = []
        again = nse_live.fetch(NOW + timedelta(minutes=5), {"blocked_until": out["blocked_until"], "failures": out["failures"]}, lambda u, **k: calls.append(u), lambda s: None)
        self.assertEqual(calls, [], "no request while the pause lasts")
        self.assertFalse(again["ok"])


class UsableTests(unittest.TestCase):
    def live(self, as_of, fetched_at="2026-11-20T09:22:00+00:00"):
        return {"ok": True, "as_of": as_of, "fetched_at": fetched_at, "prices": {"TCS.NS": {"price": 1.0}}}

    def test_only_a_current_snapshot_counts(self):
        self.assertTrue(nse_live.usable(self.live("2026-11-20T14:50:22+05:30"), NOW))
        self.assertEqual(nse_live.usable(self.live("2026-11-20T11:00:00+05:30"), NOW), {})
        self.assertEqual(nse_live.usable({"ok": False}, NOW), {})
        self.assertEqual(nse_live.usable(None, NOW), {})

    def test_a_snapshot_read_minutes_after_the_run_started_is_still_current(self):
        started = datetime(2026, 11, 20, 9, 26, tzinfo=timezone.utc)                    # the run's `now`
        read_at = "2026-11-20T09:29:50+00:00"                                           # the snapshot is read about 4 minutes later
        self.assertTrue(nse_live.usable(self.live("2026-11-20T14:59:45+05:30", read_at), started))

    def test_an_old_read_is_not_used(self):
        self.assertEqual(nse_live.usable(self.live("2026-11-20T14:50:22+05:30", "2026-11-20T09:00:00+00:00"), NOW + timedelta(minutes=30)), {})


class CompareTests(unittest.TestCase):
    def test_status_bands(self):
        self.assertEqual(nse_live.status_of(100.5, 100.0)[0], "match")
        self.assertEqual(nse_live.status_of(102.0, 100.0)[0], "minor")
        self.assertEqual(nse_live.status_of(110.0, 100.0)[0], "mismatch")
        self.assertEqual(nse_live.status_of(None, 100.0), (None, None))
        self.assertEqual(nse_live.status_of(0.16, 0.17)[0], "match", "a one-tick difference on a penny stock is not a disagreement")
        self.assertEqual(nse_live.status_of(0.20, 0.30)[0], "mismatch")

    def test_records_get_a_live_check_and_the_summary_counts_them(self):
        records = [{"symbol": "A.NS", "price": 100.2}, {"symbol": "B.NS", "price": 150.0}, {"symbol": "C.NS", "price": 10.0}, {"symbol": "D.NS", "price": 5.0, "carried": True}]
        prices = {"A.NS": {"price": 100.0}, "B.NS": {"price": 100.0}, "D.NS": {"price": 5.0}}
        summary = nse_live.check_records(records, prices, "x")
        self.assertEqual((summary["compared"], summary["match"], summary["mismatch"]), (2, 1, 1))
        self.assertEqual(records[1]["live_check"]["status"], "mismatch")
        self.assertNotIn("live_check", records[2])
        self.assertNotIn("live_check", records[3])
        self.assertIsNone(nse_live.check_records([{"symbol": "Z.NS", "price": 1.0}], {}, "x"))

    def test_disputed_movers_are_the_ones_more_than_three_percent_off(self):
        quotes = {"A.NS": {"price": 110.0}, "B.NS": {"price": 100.5}, "C.NS": {"price": 50.0}}
        self.assertEqual(nse_live.disputed(quotes, {"A.NS": {"price": 100.0}, "B.NS": {"price": 100.0}}), {"A.NS"})
