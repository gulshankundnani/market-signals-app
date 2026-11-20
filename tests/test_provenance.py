import unittest
from datetime import datetime, timezone

from engine import provenance, statements

NOW = int(datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc).timestamp())      # Friday 14:30 IST, session running


def record(**over):
    base = {"symbol": "A.NS", "as_of": "2026-11-20", "confirmed_as_of": "2026-11-19", "provisional": True, "series_lag": False,
            "quality": {"status": "match", "last_date": "2026-11-19"}, "fundamentals": {"pe": 10, "most_recent_quarter": "2026-09-30"},
            "fundamentals_at": NOW - 3600, "dividend": {}, "news": {"fresh": [], "error": False}}
    base.update(over)
    return base


class ProvenanceTests(unittest.TestCase):
    def test_a_current_stock_has_a_source_and_time_for_every_group(self):
        out, ok = provenance.build(record(), NOW)
        self.assertTrue(ok)
        for group in ("price", "indicators", "end_of_day", "fundamentals", "dividends", "news"):
            self.assertIn("source", out[group])
        self.assertEqual(out["end_of_day"]["state"], "verified")
        self.assertEqual(out["fundamentals"]["state"], "fresh")
        self.assertEqual(out["fundamentals"]["as_of"], "2026-09-30")

    def test_a_series_missing_the_newest_session_is_stale_and_not_ok(self):
        after_close = int(datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc).timestamp())            # 17:30 IST: the 20th is final
        out, ok = provenance.build(record(as_of="2026-11-19", confirmed_as_of="2026-11-18", provisional=False), after_close)
        self.assertFalse(ok)
        self.assertEqual(out["price"]["state"], "stale")
        self.assertEqual(out["dividends"]["state"], "stale")

    def test_a_lagging_flag_or_an_official_dispute_makes_it_not_ok(self):
        self.assertFalse(provenance.build(record(series_lag=True), NOW)[1])
        self.assertFalse(provenance.build(record(quality={"status": "mismatch", "last_date": "x"}), NOW)[1])
        self.assertFalse(provenance.build(record(quality={"status": "behind", "last_date": "x"}), NOW)[1])
        self.assertFalse(provenance.build(record(live_check={"status": "mismatch", "diff_pct": 9.0, "as_of": "x"}), NOW)[1])

    def test_unverified_and_minor_are_not_failures(self):
        out, ok = provenance.build(record(quality=None), NOW)
        self.assertTrue(ok)
        self.assertEqual(out["end_of_day"]["state"], "unverified")

    def test_fundamentals_age(self):
        old = provenance.build(record(fundamentals_at=NOW - 20 * 86400), NOW)[0]["fundamentals"]
        self.assertEqual((old["state"], old["age_days"]), ("stale", 20))
        self.assertEqual(provenance.build(record(fundamentals_at=NOW - 8 * 86400), NOW)[0]["fundamentals"]["state"], "aging")
        self.assertEqual(provenance.build(record(fundamentals_at=None), NOW)[0]["fundamentals"]["state"], "unknown")
        self.assertEqual(provenance.build(record(fundamentals={}), NOW)[0]["fundamentals"]["state"], "unknown")

    def test_statements_state(self):
        fresh = {"latest_quarter": "2026-09-30", "fetched_at": "2026-11-19T09:00:00+00:00"}
        self.assertEqual(provenance.build(record(statements=fresh), NOW)[0]["statements"]["state"], "fresh")
        missing_quarter = {"latest_quarter": "2026-03-31", "fetched_at": "2026-11-19T09:00:00+00:00"}
        self.assertEqual(provenance.build(record(statements=missing_quarter), NOW)[0]["statements"]["state"], "stale")
        old_read = {"latest_quarter": "2026-09-30", "fetched_at": "2026-09-01T09:00:00+00:00"}
        self.assertEqual(provenance.build(record(statements=old_read), NOW)[0]["statements"]["state"], "stale")


def series(dates_values):
    return [{"date": d, "value": v} for d, v in dates_values]


class StatementsTests(unittest.TestCase):
    def payload(self):
        def block(kind, rows):
            return {"meta": {"type": [kind]}, kind: [{"asOfDate": d, "currencyCode": "INR", "reportedValue": {"raw": v}} for d, v in rows]}
        return {"timeseries": {"result": [
            block("quarterlyTotalRevenue", [("2025-09-30", 100.0), ("2025-12-31", 110.0), ("2026-03-31", 120.0), ("2026-06-30", 125.0), ("2026-09-30", 130.0)]),
            block("quarterlyNetIncome", [("2025-09-30", 10.0), ("2026-09-30", 13.0), ("2026-06-30", None)]),
            block("annualTotalRevenue", [("2025-03-31", 400.0), ("2026-03-31", 450.0)]),
            {"meta": {"type": ["somethingElse"]}, "somethingElse": []}]}}

    def test_parse_orders_oldest_first_and_skips_empty_values(self):
        data, currency = statements.parse(self.payload())
        self.assertEqual(currency, "INR")
        self.assertEqual([r["date"] for r in data["quarterly"]["revenue"]], ["2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30"])
        self.assertEqual(len(data["quarterly"]["net_income"]), 2)
        self.assertEqual(data["annual"]["revenue"][-1]["value"], 450.0)

    def test_the_summary_compares_only_quarters_that_are_really_there(self):
        data, _ = statements.parse(self.payload())
        s = statements.summarise(data["quarterly"])
        self.assertEqual(s["latest_quarter"], "2026-09-30")
        self.assertEqual(s["yoy"]["revenue"], 30.0)
        self.assertEqual(s["qoq"]["revenue"], 4.0)
        self.assertEqual(s["yoy"]["net_income"], 30.0)
        self.assertNotIn("net_income", s["qoq"])            # the June quarter had no profit figure
        self.assertEqual(s["net_margin_pct"], 10.0)

    def test_a_gap_gives_no_growth_rate_not_a_wrong_one(self):
        s = statements.summarise({"revenue": series([("2025-06-30", 90.0), ("2026-06-30", 100.0), ("2026-09-30", 110.0)])})
        self.assertEqual(s["qoq"]["revenue"], 10.0)
        self.assertEqual(s["yoy"], {})
        self.assertTrue(s["gaps"])

    def test_nothing_in_nothing_out(self):
        self.assertEqual(statements.summarise({}), {})
        self.assertEqual(statements.parse(None), ({"quarterly": {}, "annual": {}}, None))

    def test_due_rules(self):
        now = NOW
        fresh = {"ts": now - 86400, "data": {"v": statements.VERSION, "latest_quarter": "2026-09-30"}}
        self.assertFalse(statements.due(fresh, now))
        self.assertTrue(statements.due({**fresh, "ts": now - 8 * 86400}, now))
        self.assertTrue(statements.due(None, now))
        waiting = {"ts": now - 3 * 86400, "data": {"v": statements.VERSION, "latest_quarter": "2026-06-01"}}      # results are due: look every two days
        self.assertTrue(statements.due(waiting, now))

    def test_sync_reads_only_what_is_due_and_keeps_old_figures_when_a_read_fails(self):
        records = [{"symbol": "A.NS"}, {"symbol": "B.NS"}]
        cache = {"B.NS": {"ts": NOW - 10 * 86400, "data": {"v": statements.VERSION, "latest_quarter": "2026-09-30", "marker": "old"}}}

        class Session:
            def start(self):
                return True

        def fetcher(session, symbol, ts):
            return {"v": statements.VERSION, "latest_quarter": "2026-09-30", "marker": "new"} if symbol == "A.NS" else {}

        out = statements.sync(records, cache, NOW, Session, fetcher=fetcher, sleep=lambda s: None)
        self.assertEqual(records[0]["statements"]["marker"], "new")
        self.assertEqual(records[1]["statements"]["marker"], "old")
        self.assertIn("A.NS", out)
