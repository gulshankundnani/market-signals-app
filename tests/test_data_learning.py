import unittest
from datetime import datetime, timedelta, timezone

from engine import data_learning as dl

T0 = datetime(2026, 11, 20, 4, 0, tzinfo=timezone.utc)


def rec(symbol, **over):
    return {"symbol": symbol, **over}


def run(state, n, records_fn, sources=None, start=0):
    quarantined = {}
    for k in range(n):
        state, quarantined = dl.observe(state, T0 + timedelta(minutes=10 * (start + k)), records_fn(k), sources or {})
    return state, quarantined


class IncidentTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(dl.incidents_of(rec("A")), [])
        self.assertEqual(dl.incidents_of(rec("A", series_lag=True)), ["lag"])
        self.assertEqual(dl.incidents_of(rec("A", quality={"status": "behind"})), ["behind"])
        self.assertEqual(dl.incidents_of(rec("A", quality={"status": "mismatch"})), ["mismatch"])
        self.assertEqual(dl.incidents_of(rec("A", live_check={"status": "mismatch"})), ["live_mismatch"])
        self.assertEqual(dl.incidents_of(rec("A", provenance_ok=False)), ["stale"])
        self.assertEqual(dl.incidents_of(rec("A", provenance_ok=False, series_lag=True)), ["lag"], "the cause, not a second incident for the same fault")
        self.assertEqual(dl.incidents_of(rec("A", carried=True)), ["carried"])


class QuarantineTests(unittest.TestCase):
    def test_one_bad_run_does_not_quarantine(self):
        state, q = run(None, 1, lambda k: [rec("A", series_lag=True), rec("B")])
        self.assertEqual(q, {})

    def test_a_repeat_offender_is_quarantined_then_released_after_clean_runs(self):
        state, q = run(None, 3, lambda k: [rec("A", series_lag=True), rec("B")])
        self.assertIn("A", q)
        self.assertNotIn("B", q)
        self.assertEqual(q["A"]["reasons"], ["price series missing the newest session"])
        state, q = run(state, 2, lambda k: [rec("A"), rec("B")], start=3)
        self.assertIn("A", q, "two clean runs are not enough")
        state, q = run(state, 1, lambda k: [rec("A"), rec("B")], start=5)
        self.assertNotIn("A", q)

    def test_clean_stocks_take_no_space_and_forgotten_ones_are_dropped(self):
        state, _ = run(None, 3, lambda k: [rec(f"S{n}") for n in range(50)])
        self.assertEqual(state["symbols"], {})
        state, _ = run(None, 1, lambda k: [rec("A", series_lag=True)])
        state, _ = run(state, 6, lambda k: [rec("A")], start=1)
        self.assertNotIn("A", state["symbols"])


class SourceAndHoursTests(unittest.TestCase):
    def test_reliability_and_degraded(self):
        state, _ = run(None, 10, lambda k: [rec("A")], {"Good": (True, ""), "Bad": (False, "HTTP 403")})
        summary = dl.summary(state, {})
        by = {s["name"]: s for s in summary["sources"]}
        self.assertEqual((by["Good"]["state"], by["Good"]["reliability"]), ("ok", 1.0))
        self.assertEqual((by["Bad"]["state"], by["Bad"]["reliability"]), ("degraded", 0.0))
        self.assertEqual(by["Bad"]["detail"], "HTTP 403")

    def test_late_sources_can_be_added_in_the_same_run(self):
        state, _ = run(None, 1, lambda k: [rec("A")], {"Early": (True, "")})
        state = dl.fold_sources(state, T0, {"Late": (False, "down")})
        self.assertEqual({s["name"] for s in dl.summary(state, {})["sources"]}, {"Early", "Late"})

    def test_the_hours_when_stocks_lag_are_learned(self):
        state = None
        for k in range(8):                                  # 09:30 IST (04:00 UTC): most stocks lagging
            state, _ = dl.observe(state, T0 + timedelta(minutes=k), [rec(f"S{n}", series_lag=n < 6) for n in range(10)], {})
        for k in range(8):                                  # 14:30 IST: none lagging
            state, _ = dl.observe(state, T0 + timedelta(hours=5, minutes=k), [rec(f"S{n}") for n in range(10)], {})
        self.assertEqual(dl.lag_hours(state), [9])

    def test_summary_of_nothing_is_none(self):
        self.assertIsNone(dl.summary(None, {}))
