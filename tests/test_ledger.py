import json
import shutil
import tempfile
import unittest
from pathlib import Path

from engine import ledger


def day(n):
    """A date n days after 2026-11-02 (all inside November and December 2026)."""
    from datetime import date, timedelta
    return (date(2026, 11, 2) + timedelta(days=n)).isoformat()


def make_bars(rows, start=1):
    """rows: [(open, high, low, close)] on consecutive days starting `start` days after the call date."""
    return {"d": [day(start + i) for i in range(len(rows))], "o": [r[0] for r in rows], "h": [r[1] for r in rows],
            "l": [r[2] for r in rows], "c": [r[3] for r in rows]}


def call(side="buy", horizon="short", price=100.0, stop=96.0, target=108.0, **extra):
    if side == "sell":
        stop, target = (104.0, 92.0) if (stop, target) == (96.0, 108.0) else (stop, target)
    base = {"type": "call", "id": f"{side[0]}{horizon[0]}{price:g}"[:12], "ts": 1_793_000_000, "date": day(0), "symbol": "AAA",
            "name": "Aaa Ltd", "market": "US", "sector": "Tech", "horizon": horizon, "from": "hold", "side": side,
            "label": "Buy" if side == "buy" else "Sell", "score": 40 if side == "buy" else -40, "price": price, "currency": "USD",
            "stop": stop, "target": target, "target_basis": "volatility", "expiry_days": 5, "parts": {}, "model": ledger.MODEL}
    base.update(extra)
    return base


class MakeCallTests(unittest.TestCase):
    def record(self, **overrides):
        rec = {"name": "Acme", "market": "US", "sector": "Tech", "price": 100.0, "currency": "USD",
               "indicators": {"atr": 2.0, "hi252": 130.0, "lo252": 70.0},
               "levels": {"zones": {"resistance": [{"level": 108.0, "touches": 2}], "support": [{"level": 92.0, "touches": 2}]}},
               "short": {"label": "Buy", "score": 40, "reasons": [{"text": "t", "tone": "pos", "key": "trend", "value": 1.0},
                                                                   {"text": "m", "tone": "pos", "key": "macd", "value": 0.5}]},
               "long": {"label": "Strong Buy", "score": 70, "reasons": []}}
        rec.update(overrides)
        return rec

    def test_a_buy_call_has_everything_needed_to_check_it_later(self):
        c = ledger.make_call(self.record(), "ACME", "short", "buy", "hold", 1_793_000_000)
        self.assertEqual((c["side"], c["horizon"], c["from"], c["symbol"]), ("buy", "short", "hold", "ACME"))
        self.assertEqual((c["price"], c["stop"], c["target"]), (100.0, 96.0, 108.0))
        self.assertEqual(c["target_basis"], "resistance")
        self.assertEqual(c["parts"], {"trend": 1.0, "macd": 0.5})
        self.assertEqual(c["expiry_days"], ledger.EXPIRY_DAYS["short"])
        self.assertEqual((c["label"], c["score"], c["date"]), ("Buy", 40, "2026-10-26"))
        json.dumps(c, allow_nan=False)

    def test_the_id_is_stable_and_separates_horizons_days_and_sides(self):
        a = ledger.make_call(self.record(), "ACME", "short", "buy", "hold", 1_793_000_000)
        again = ledger.make_call(self.record(), "ACME", "short", "buy", "hold", 1_793_000_500)
        self.assertEqual(a["id"], again["id"])
        other_horizon = ledger.make_call(self.record(), "ACME", "long", "buy", "hold", 1_793_000_000)
        next_day = ledger.make_call(self.record(), "ACME", "short", "buy", "hold", 1_793_000_000 + 86400)
        sell = ledger.make_call(self.record(), "ACME", "short", "sell", "hold", 1_793_000_000)
        self.assertEqual(len({a["id"], other_horizon["id"], next_day["id"], sell["id"]}), 4)

    def test_a_sell_call_has_the_stop_above_and_target_below(self):
        c = ledger.make_call(self.record(), "ACME", "short", "sell", "hold", 1_793_000_000)
        self.assertGreater(c["stop"], c["price"])
        self.assertLess(c["target"], c["price"])

    def test_no_call_without_a_side_or_levels(self):
        self.assertIsNone(ledger.make_call(self.record(), "ACME", "short", "hold", "buy", 1_793_000_000))
        self.assertIsNone(ledger.make_call(self.record(indicators={}), "ACME", "short", "buy", "hold", 1_793_000_000))
        self.assertIsNone(ledger.make_call(self.record(indicators=None), "ACME", "short", "buy", "hold", 1_793_000_000))

    def test_reasons_from_older_data_without_keys_are_skipped(self):
        rec = self.record()
        rec["short"]["reasons"] = [{"text": "old", "tone": "pos"}, None, {"key": "x"}]
        self.assertEqual(ledger.make_call(rec, "ACME", "short", "buy", "hold", 1_793_000_000)["parts"], {})


class EvaluateCallTests(unittest.TestCase):
    def test_target_hit_fills_at_the_target(self):
        out = ledger.evaluate_call(call(), make_bars([(100, 103, 99, 102), (102, 105, 101, 104), (104, 109, 103, 108)]))
        self.assertEqual((out["outcome"], out["exit_price"], out["days_held"]), ("target", 108.0, 3))
        self.assertAlmostEqual(out["return_pct"], 8.0)
        self.assertEqual(out["resolved_date"], day(3))
        self.assertEqual(out["id"], call()["id"])

    def test_stop_hit_fills_at_the_stop(self):
        out = ledger.evaluate_call(call(), make_bars([(100, 101, 97, 98), (98, 99, 95, 96)]))
        self.assertEqual((out["outcome"], out["exit_price"], out["days_held"]), ("stop", 96.0, 2))
        self.assertAlmostEqual(out["return_pct"], -4.0)

    def test_a_bar_touching_both_counts_as_a_stop(self):
        out = ledger.evaluate_call(call(), make_bars([(100, 110, 95, 105)]))
        self.assertEqual(out["outcome"], "stop")

    def test_gaps_fill_at_the_open(self):
        up = ledger.evaluate_call(call(), make_bars([(112, 115, 111, 114)]))
        self.assertEqual((up["outcome"], up["exit_price"]), ("target", 112.0))
        down = ledger.evaluate_call(call(), make_bars([(90, 92, 88, 91)]))
        self.assertEqual((down["outcome"], down["exit_price"]), ("stop", 90.0))
        self.assertAlmostEqual(down["return_pct"], -10.0)

    def test_the_announcement_day_is_ignored(self):
        bars = make_bars([(100, 120, 90, 100), (100, 101, 99, 100)], start=0)
        self.assertEqual(bars["d"][0], day(0))
        out = ledger.evaluate_call(call(expiry_days=1), bars)
        self.assertEqual(out["outcome"], "expired")
        self.assertEqual(out["resolved_date"], day(1))

    def test_a_call_expires_at_the_close_after_its_horizon(self):
        rows = [(100, 102, 98, 101)] * 5 + [(101, 130, 90, 101)]
        out = ledger.evaluate_call(call(expiry_days=5), make_bars(rows))
        self.assertEqual((out["outcome"], out["days_held"], out["exit_price"]), ("expired", 5, 101.0))
        self.assertAlmostEqual(out["return_pct"], 1.0)

    def test_a_call_with_too_little_history_stays_open(self):
        self.assertIsNone(ledger.evaluate_call(call(expiry_days=5), make_bars([(100, 102, 98, 101)] * 3)))
        self.assertIsNone(ledger.evaluate_call(call(), {"d": [], "o": [], "h": [], "l": [], "c": []}))
        self.assertIsNone(ledger.evaluate_call(call(), make_bars([(100, 101, 99, 100)], start=-3)))

    def test_a_sell_call_wins_when_the_price_falls(self):
        out = ledger.evaluate_call(call("sell"), make_bars([(100, 101, 98, 99), (99, 100, 91, 92)]))
        self.assertEqual((out["outcome"], out["exit_price"]), ("target", 92.0))
        self.assertAlmostEqual(out["return_pct"], 8.0)

    def test_a_sell_call_loses_when_the_price_rises(self):
        out = ledger.evaluate_call(call("sell"), make_bars([(100, 105, 99, 104)]))
        self.assertEqual((out["outcome"], out["exit_price"]), ("stop", 104.0))
        self.assertAlmostEqual(out["return_pct"], -4.0)

    def test_sell_gaps_and_ties(self):
        up = ledger.evaluate_call(call("sell"), make_bars([(110, 112, 109, 111)]))
        self.assertEqual((up["outcome"], up["exit_price"]), ("stop", 110.0))
        down = ledger.evaluate_call(call("sell"), make_bars([(88, 90, 86, 89)]))
        self.assertEqual((down["outcome"], down["exit_price"]), ("target", 88.0))
        both = ledger.evaluate_call(call("sell"), make_bars([(100, 105, 90, 100)]))
        self.assertEqual(both["outcome"], "stop")

    def test_best_and_worst_moves_are_recorded(self):
        out = ledger.evaluate_call(call(), make_bars([(100, 105, 98, 101), (101, 109, 100, 108)]))
        self.assertAlmostEqual(out["max_gain_pct"], 9.0)
        self.assertAlmostEqual(out["max_loss_pct"], -2.0)

    def test_outcomes_are_json_safe(self):
        out = ledger.evaluate_call(call(), make_bars([(100, 109, 99, 108)]))
        json.dumps(out, allow_nan=False)


class ExcessReturnTests(unittest.TestCase):
    def test_a_buy_that_beat_the_market(self):
        bench = {day(0): 1000.0, day(1): 1010.0, day(3): 1020.0}
        out = {"resolved_date": day(3), "return_pct": 8.0}
        self.assertAlmostEqual(ledger.excess_return(call(), out, bench), 6.0)

    def test_a_sell_is_measured_against_a_falling_market(self):
        bench = {day(0): 1000.0, day(2): 950.0}
        out = {"resolved_date": day(2), "return_pct": 8.0}
        self.assertAlmostEqual(ledger.excess_return(call("sell"), out, bench), 3.0)

    def test_the_last_close_on_or_before_a_date_is_used(self):
        bench = {day(0): 1000.0, day(4): 1040.0}
        out = {"resolved_date": day(6), "return_pct": 10.0}
        self.assertAlmostEqual(ledger.excess_return(call(), out, bench), 6.0)

    def test_no_benchmark_or_no_overlap_gives_none(self):
        out = {"resolved_date": day(3), "return_pct": 8.0}
        self.assertIsNone(ledger.excess_return(call(), out, None))
        self.assertIsNone(ledger.excess_return(call(), out, {}))
        self.assertIsNone(ledger.excess_return(call(), out, {day(5): 1000.0}))


class LedgerStorageTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def test_save_and_load_round_trip_into_monthly_files(self):
        led = ledger.Ledger(self.dir)
        nov, dec = call(), call(price=101.0, date="2026-12-03")
        self.assertTrue(led.add_call(nov))
        self.assertTrue(led.add_call(dec))
        self.assertEqual(led.save(), 2)
        self.assertEqual(sorted(p.name for p in self.dir.glob("*.jsonl")), ["calls-2026-11.jsonl", "calls-2026-12.jsonl"])
        again = ledger.Ledger(self.dir).load()
        self.assertEqual(again.calls, {nov["id"]: nov, dec["id"]: dec})

    def test_duplicates_and_orphan_outcomes_are_refused(self):
        led = ledger.Ledger(self.dir)
        self.assertTrue(led.add_call(call()))
        self.assertFalse(led.add_call(call()))
        self.assertFalse(led.add_outcome({"id": "nope", "resolved_date": day(2)}))
        out = ledger.evaluate_call(call(), make_bars([(100, 109, 99, 108)]))
        self.assertTrue(led.add_outcome(out))
        self.assertFalse(led.add_outcome(out))
        self.assertEqual(led.save(), 2)
        self.assertEqual(led.save(), 0, "nothing new to write the second time")

    def test_history_is_only_ever_appended(self):
        led = ledger.Ledger(self.dir)
        led.add_call(call())
        led.save()
        path = self.dir / "calls-2026-11.jsonl"
        before = path.read_bytes()
        led2 = ledger.Ledger(self.dir).load()
        led2.add_call(call(price=101.0))
        led2.save()
        after = path.read_bytes()
        self.assertTrue(after.startswith(before), "earlier lines must be untouched")
        self.assertEqual(after.count(b"\n"), 2)

    def test_files_are_one_valid_json_object_per_line_with_stable_keys(self):
        led = ledger.Ledger(self.dir)
        led.add_call(call(parts={"b": 1.0, "a": 0.5}))
        led.save()
        lines = (self.dir / "calls-2026-11.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        parsed = json.loads(lines[0])
        self.assertEqual(list(parsed), sorted(parsed))
        self.assertEqual(list(parsed["parts"]), ["a", "b"])
        self.assertNotIn("NaN", lines[0])

    def test_damaged_lines_are_skipped_and_counted_not_fatal(self):
        (self.dir / "calls-2026-11.jsonl").write_text('{"id":"ok1","date":"2026-11-02","type":"call"}\nnot json\n\n{"no_id":1}\n[1,2]\n', encoding="utf-8")
        led = ledger.Ledger(self.dir).load()
        self.assertEqual(list(led.calls), ["ok1"])
        self.assertEqual(led.skipped_lines, 3)

    def test_loading_an_empty_or_missing_folder_is_fine(self):
        self.assertEqual(ledger.Ledger(self.dir / "nothing-here").load().calls, {})
        self.assertEqual(ledger.Ledger(self.dir).load().calls, {})

    def test_open_calls_are_those_without_an_outcome(self):
        led = ledger.Ledger(self.dir)
        led.add_call(call())
        led.add_call(call(price=101.0))
        led.add_outcome(ledger.evaluate_call(call(), make_bars([(100, 109, 99, 108)])))
        self.assertEqual([c["price"] for c in led.open_calls()], [101.0])

    def test_resolve_open_uses_the_bars_and_the_benchmark_and_is_repeatable(self):
        led = ledger.Ledger(self.dir)
        led.add_call(call())
        led.add_call(call(price=50.0, stop=48.0, target=54.0, symbol="ZZZ"))
        bars = {"AAA": make_bars([(100, 103, 99, 102), (102, 109, 101, 108)])}
        bench = {"US": {day(0): 1000.0, day(2): 1020.0}}
        resolved = led.resolve_open(bars, bench)
        self.assertEqual(len(resolved), 1)
        c, o = resolved[0]
        self.assertEqual((c["symbol"], o["outcome"]), ("AAA", "target"))
        self.assertAlmostEqual(o["excess_pct"], 6.0)
        self.assertEqual(led.resolve_open(bars, bench), [], "a resolved call is not resolved twice")
        self.assertEqual([c["symbol"] for c in led.open_calls()], ["ZZZ"])

    def test_the_ledger_survives_a_reload_between_runs(self):
        led = ledger.Ledger(self.dir)
        led.add_call(call())
        led.save()
        later = ledger.Ledger(self.dir).load()
        later.resolve_open({"AAA": make_bars([(100, 109, 99, 108)])}, {})
        later.save()
        final = ledger.Ledger(self.dir).load()
        self.assertEqual(len(final.outcomes), 1)
        self.assertEqual(final.open_calls(), [])


class StatisticsTests(unittest.TestCase):
    def test_wilson_interval(self):
        self.assertIsNone(ledger.wilson(0, 0))
        low, high = ledger.wilson(50, 100)
        self.assertAlmostEqual(low, 40.4, delta=0.2)
        self.assertAlmostEqual(high, 59.6, delta=0.2)
        self.assertEqual(ledger.wilson(0, 10)[0], 0.0)
        self.assertEqual(ledger.wilson(10, 10)[1], 100.0)
        wide, narrow = ledger.wilson(5, 10), ledger.wilson(500, 1000)
        self.assertGreater(wide[1] - wide[0], narrow[1] - narrow[0], "less data means a wider interval")

    def rows(self, specs):
        out = []
        for i, (kind, ret, days) in enumerate(specs):
            out.append({"call": call(price=100 + i, side="buy"), "outcome": {"outcome": kind, "return_pct": ret, "days_held": days, "excess_pct": ret - 1}})
        return out

    def test_group_figures(self):
        stats = ledger.group_stats(self.rows([("target", 8.0, 4), ("target", 8.0, 6), ("stop", -4.0, 2), ("expired", 1.0, 20)]))
        self.assertEqual(stats["n"], 4)
        self.assertEqual((stats["target_pct"], stats["stop_pct"], stats["expired_pct"]), (50.0, 25.0, 25.0))
        self.assertEqual(stats["win_pct"], 75.0)
        self.assertAlmostEqual(stats["avg_return"], 3.25)
        self.assertAlmostEqual(stats["median_return"], 4.5)
        self.assertAlmostEqual(stats["avg_excess"], 2.25)
        self.assertAlmostEqual(stats["avg_days"], 8.0)
        self.assertAlmostEqual(stats["profit_factor"], 17.0 / 4.0)
        self.assertFalse(stats["reliable"])

    def test_small_groups_are_marked_unreliable_and_large_ones_reliable(self):
        self.assertFalse(ledger.group_stats(self.rows([("target", 1.0, 1)] * (ledger.MIN_SAMPLE - 1)))["reliable"])
        self.assertTrue(ledger.group_stats(self.rows([("target", 1.0, 1)] * ledger.MIN_SAMPLE))["reliable"])

    def test_an_empty_group_has_no_percentages(self):
        stats = ledger.group_stats([])
        self.assertEqual(stats["n"], 0)
        for key in ("target_pct", "stop_pct", "win_pct", "avg_return", "median_return", "profit_factor", "target_ci"):
            self.assertIsNone(stats[key], key)

    def test_profit_factor_needs_a_loss(self):
        self.assertIsNone(ledger.group_stats(self.rows([("target", 5.0, 1)]))["profit_factor"])

    def test_score_bands(self):
        self.assertEqual([ledger.band_of(s) for s in (25, 39, 40, 59, 60, 100, -30, -75)], ["25-39", "25-39", "40-59", "40-59", "60+", "60+", "25-39", "60+"])
        for junk in (None, 10, True, "x", float("nan")):
            self.assertIsNone(ledger.band_of(junk), junk)


def resolved_row(kind, ret, parts=None, side="buy", horizon="short", score=40, market="US", date="2026-11-02", resolved="2026-11-20", n=[0]):
    n[0] += 1
    c = call(side, horizon, price=100.0 + n[0], score=score, market=market, date=date, id=f"id{n[0]}", parts=parts or {})
    return c, {"type": "outcome", "id": c["id"], "resolved_date": resolved, "outcome": kind, "exit_price": 100.0, "return_pct": ret, "days_held": 5, "excess_pct": 0.0}


class SummaryTests(unittest.TestCase):
    NOW = 1_795_000_000  # 2026-11-18

    def ledger_with(self, rows, open_calls=()):
        led = ledger.Ledger(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, led.directory, ignore_errors=True)
        for c, o in rows:
            led.add_call(c)
            led.add_outcome(o)
        for c in open_calls:
            led.add_call(c)
        return led

    def test_an_empty_ledger_summarises_without_errors(self):
        s = ledger.summarise(ledger.Ledger(tempfile.mkdtemp()), self.NOW)
        self.assertEqual((s["calls_total"], s["open"], s["resolved"]), (0, 0, 0))
        self.assertEqual(s["recent"], [])
        self.assertIn("noise", s["suggestions"][0]["text"])
        json.dumps(s, allow_nan=False)

    def test_calls_from_an_earlier_rule_version_are_kept_but_not_counted(self):
        old_target, old_stop, new_row = resolved_row("target", 8.0), resolved_row("stop", -4.0), resolved_row("target", 6.0)
        old_target[0]["model"] = old_stop[0]["model"] = "v1"
        old_open = call(price=61.0, id="old_open")
        old_open["model"] = "v1"
        led = self.ledger_with([old_target, old_stop, new_row], [old_open, call(price=62.0, id="new_open")])
        s = ledger.summarise(led, self.NOW)
        self.assertEqual((s["calls_total"], s["older_model_calls"], s["resolved"], s["open"]), (2, 3, 1, 1))
        self.assertEqual(s["overall"]["n"], 1)
        self.assertEqual(s["overall"]["target_pct"], 100.0)           # the two v1 outcomes (one target, one stop) did not leak in
        self.assertEqual(len(led.calls), 5, "nothing is removed from the record")

    def test_counts_and_recent_calls(self):
        rows = [resolved_row("target", 8.0, resolved=f"2026-11-{10 + i:02d}") for i in range(30)]
        s = ledger.summarise(self.ledger_with(rows, [call(price=77.0, id="open1")]), self.NOW)
        self.assertEqual((s["calls_total"], s["open"], s["resolved"]), (31, 1, 30))
        self.assertEqual(len(s["recent"]), ledger.RECENT)
        dates = [r["resolved_date"] for r in s["recent"]]
        self.assertEqual(dates, sorted(dates, reverse=True), "newest first")
        self.assertEqual(s["open_calls"][0]["price"], 77.0)
        self.assertTrue(s["overall"]["reliable"])
        self.assertEqual(s["overall"]["target_pct"], 100.0)

    def test_groups_by_horizon_side_band_and_market(self):
        rows = [resolved_row("target", 8.0, side="buy", horizon="short", score=30), resolved_row("stop", -4.0, side="sell", horizon="long", score=-70, market="IN")]
        s = ledger.summarise(self.ledger_with(rows), self.NOW)
        self.assertEqual(s["by_group"]["Weeks view, bullish"]["n"], 1)
        self.assertEqual(s["by_group"]["Months-to-years view, bearish"]["n"], 1)
        self.assertEqual(s["by_group"]["Weeks view, bearish"]["n"], 0)
        self.assertIn("Weeks view, bullish / alignment 25-39", s["by_score_band"])
        self.assertIn("Months-to-years view, bearish / alignment 60+", s["by_score_band"])
        self.assertEqual(set(s["by_market"]), {"IN", "US"})

    def test_the_last_90_days_only_counts_recent_outcomes(self):
        rows = [resolved_row("target", 8.0, resolved="2026-11-10"), resolved_row("stop", -4.0, resolved="2026-05-01")]
        s = ledger.summarise(self.ledger_with(rows), self.NOW)
        self.assertEqual((s["overall"]["n"], s["last_90_days"]["n"]), (2, 1))

    def test_a_losing_record_is_called_out_once_there_is_enough_data(self):
        rows = [resolved_row("stop", -4.0) for _ in range(ledger.MIN_SAMPLE)]
        texts = " ".join(n["text"] for n in ledger.summarise(self.ledger_with(rows), self.NOW)["suggestions"])
        self.assertIn("moved 4.0% against the readings", texts)
        self.assertIn("reached the volatility risk level first", texts)

    def test_calls_that_never_resolve_suggest_targets_are_too_far(self):
        rows = [resolved_row("expired", 0.5) for _ in range(ledger.MIN_SAMPLE)]
        texts = " ".join(n["text"] for n in ledger.summarise(self.ledger_with(rows), self.NOW)["suggestions"])
        self.assertIn("reference levels may be too far", texts)

    def test_a_component_that_does_not_help_is_flagged(self):
        rows = []
        for i in range(ledger.MIN_SAMPLE):
            supporting = i % 2 == 0
            rows.append(resolved_row("target" if not supporting else "stop", -3.0 if supporting else 6.0, parts={"rsi": 0.9 if supporting else -0.5}))
        s = ledger.summarise(self.ledger_with(rows), self.NOW)
        texts = " ".join(n["text"] for n in s["suggestions"])
        self.assertIn("indicator 'rsi' is not helping", texts)
        item = next(i for i in s["components"] if i["component"] == "rsi")
        self.assertTrue(item["comparable"])

    def test_no_suggestions_beyond_the_sample_warning_while_data_is_thin(self):
        rows = [resolved_row("stop", -4.0, parts={"rsi": 0.9}) for _ in range(ledger.MIN_SAMPLE - 1)]
        suggestions = ledger.summarise(self.ledger_with(rows), self.NOW)["suggestions"]
        self.assertEqual(len(suggestions), 1)
        self.assertIn("Only", suggestions[0]["text"])

    def test_the_summary_never_contains_invalid_numbers(self):
        rows = [resolved_row("target", 8.0) for _ in range(3)] + [resolved_row("stop", -4.0) for _ in range(3)]
        raw = json.dumps(ledger.summarise(self.ledger_with(rows), self.NOW), allow_nan=False)
        self.assertNotIn("NaN", raw)


if __name__ == "__main__":
    unittest.main()


class VoidReferenceTests(unittest.TestCase):
    def bars(self, rows):
        return {"d": [r[0] for r in rows], "o": [r[1] for r in rows], "h": [r[2] for r in rows], "l": [r[3] for r in rows], "c": [r[4] for r in rows]}

    def call(self, price):
        return {"id": "x", "date": "2026-11-16", "price": price, "stop": price * 0.95, "target": price * 1.1, "side": "buy", "horizon": "short", "expiry_days": 5}

    def test_stale_reference_is_void_not_a_stop(self):
        bars = self.bars([("2026-11-16", 272, 273, 260, 262), ("2026-11-17", 262, 268, 259, 259)])
        out = ledger.evaluate_call(self.call(287.4), bars)
        self.assertEqual(out["outcome"], "void")
        self.assertIsNone(out["return_pct"])

    def test_traded_reference_is_judged_as_before(self):
        bars = self.bars([("2026-11-16", 100, 103, 98, 101), ("2026-11-17", 100, 101, 90, 91)])
        self.assertEqual(ledger.evaluate_call(self.call(100), bars)["outcome"], "stop")

    def test_void_is_not_counted_in_the_summary(self):
        led = ledger.Ledger(tempfile.mkdtemp())
        c = {**self.call(287.4), "symbol": "A", "model": ledger.MODEL, "ts": 1, "market": "IN"}
        led.calls["x"] = c
        led.add_outcome({"type": "outcome", "id": "x", "resolved_date": "2026-11-17", "outcome": "void", "return_pct": None, "days_held": 0})
        s = ledger.summarise(led, 1794854298)
        self.assertEqual((s["resolved"], s["void"], s["open"]), (0, 1, 0))
