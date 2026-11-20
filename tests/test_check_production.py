import unittest
from datetime import datetime, timezone

from tools import check_production as cp

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


def event(**over):
    base = {"symbol": "AAA.NS", "market": "IN", "type": "earnings", "verification": "UNVERIFIED", "official_url": "https://www.nseindia.com/get-quotes/equity?symbol=AAA"}
    base.update(over)
    return base


def data(official=None, items=None, us=None, us_records=None, checks=None, generated="2026-11-16T11:50:00+00:00", blocked=None):
    ev = {"items": items if items is not None else [event()]}
    if official is not None:
        ev["official"] = official
    ipos = {"us": us_records or []}
    if us is not None:
        ipos["us_status"] = us
    if blocked:
        ipos["us_blocked_until"] = blocked
    return {"generated_at": generated, "events": ev, "ipos": ipos, "status": {"checks": checks or []}}


def by(rows):
    return {name: (status, detail) for name, status, detail in rows}


class CheckTests(unittest.TestCase):
    def test_an_old_deployment_without_the_new_blocks_is_unverified_not_passed(self):
        out = by(cp.check({"generated_at": "2026-11-16T11:50:00+00:00", "events": {"items": [event(official_url=None)]}, "ipos": {}}, NOW))
        self.assertEqual(out["NSE feed from Actions"][0], cp.UNVERIFIED)
        self.assertEqual(out["Official source URLs"][0], cp.UNVERIFIED)

    def test_a_working_feed_passes_only_if_confirmed_events_carry_the_official_record(self):
        good = data({"ok": True, "fetched_at": "2026-11-16T11:40:00+00:00", "confirmed": 1, "conflicts": 0, "unverified": 0}, [event(verification="CONFIRMED", official={"date": "2026-12-01"})])
        self.assertEqual(by(cp.check(good, NOW))["NSE feed from Actions"][0], cp.PASS)
        self.assertEqual(by(cp.check(good, NOW))["Event statuses"][0], cp.PASS)
        bad = data({"ok": True, "fetched_at": "x", "confirmed": 1, "conflicts": 0, "unverified": 0}, [event(verification="CONFIRMED")])
        self.assertEqual(by(cp.check(bad, NOW))["Event statuses"][0], cp.FAIL)

    def test_a_refused_feed_is_unverified_and_the_fallback_must_confirm_nothing(self):
        refused = {"ok": False, "error": "HTTP 403: the NSE refuses automated requests", "fetched_at": None}
        out = by(cp.check(data(refused), NOW))
        self.assertEqual((out["NSE feed from Actions"][0], out["Event statuses"][0]), (cp.UNVERIFIED, cp.PASS))
        cheating = by(cp.check(data(refused, [event(verification="CONFIRMED")]), NOW))
        self.assertEqual(cheating["Event statuses"][0], cp.FAIL)

    def test_freshness_and_service_worker(self):
        old = by(cp.check(data(generated="2026-11-16T08:00:00+00:00"), NOW))
        self.assertEqual(old["Data freshness"][0], cp.FAIL)
        sw = "const CACHE = 'signals-v33';"
        self.assertEqual(by(cp.check(data(), NOW, sw, "signals-v33"))["Service worker cache"][0], cp.PASS)
        self.assertEqual(by(cp.check(data(), NOW, sw, "signals-v34"))["Service worker cache"][0], cp.FAIL)
        self.assertEqual(by(cp.check(data(), NOW, sw, None))["Service worker cache"][0], cp.UNVERIFIED)


if __name__ == "__main__":
    unittest.main()
