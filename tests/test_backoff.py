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


if __name__ == "__main__":
    unittest.main()
