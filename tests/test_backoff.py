import unittest
from datetime import datetime, timedelta, timezone

from engine import backoff


class BackoffTests(unittest.TestCase):
    def test_pauses_double_from_thirty_minutes_up_to_six_hours(self):
        self.assertEqual([backoff.pause(n) for n in range(1, 8)],
                         [timedelta(minutes=30), timedelta(hours=1), timedelta(hours=2), timedelta(hours=4), timedelta(hours=6), timedelta(hours=6), timedelta(hours=6)])
        self.assertEqual(backoff.pause(0), timedelta(minutes=30))

    def test_each_refusal_in_a_row_lengthens_the_pause_and_a_missing_state_starts_at_one(self):
        now = datetime(2026, 11, 17, 5, 0, tzinfo=timezone.utc)
        until, failures = backoff.refusal_state(now, None)
        self.assertEqual((until, failures), ("2026-11-17T05:30:00+00:00", 1))
        until, failures = backoff.refusal_state(now, {"failures": failures})
        self.assertEqual((until, failures), ("2026-11-17T06:00:00+00:00", 2))
        self.assertEqual(backoff.refusal_state(now, {"failures": "junk-free", "x": 1} if False else {"failures": None})[1], 1)


class OldPauseTests(unittest.TestCase):
    """A pause saved by the earlier fixed six-hour rule carries no failure count; the new policy does not honour it, so the feed is tried again at once."""
    NOW = datetime(2026, 11, 17, 5, 30, tzinfo=timezone.utc)
    OLD = {"blocked_until": "2026-11-17T11:00:00+00:00"}

    def good(self, url, **kw):
        import json
        return json.dumps({"data": []}).encode()

    def test_the_live_block_deal_feed(self):
        from engine import deals_live
        out = deals_live.fetch_block_deals(self.NOW, self.OLD, log=lambda s: None, getter=self.good)
        self.assertTrue(out["ok"])

    def test_the_official_feed(self):
        from engine import official
        out = official.fetch_official(self.NOW, {"AAA"}, self.OLD, getter=lambda url, **kw: b"[]", log=lambda s: None)
        self.assertTrue(out["ok"])

    def test_the_session_sources(self):
        from engine import sessions
        payload, state = sessions.Source("x", "u").fetch(self.NOW, self.OLD, lambda s: None, lambda url, **kw: b"{}")
        self.assertEqual((payload, state["ok"], state["failures"]), ({}, True, 0))


if __name__ == "__main__":
    unittest.main()
