import json
import random
import unittest
from datetime import datetime, timezone

from engine import learning


def sample(di, key, up, h=20, i=None):
    """One replay sample whose finest key is `key` (the coarser ones are fixed), with a +5% or -5% outcome at horizon h."""
    i = learning.START if i is None else i
    return {"date": f"d{di:04d}", "di": di, "symbol": f"S{di % 7}", "keys": [key, "x|y|z", "x|z", "ALL"], "ret": {h: 0.05 if up else -0.05}, "i": i}


def synthetic(strong, n_dates=400, per_date=8, seed=3):
    """Samples over many dates. With strong=True, key A goes up 85% of the time and key B 15%; otherwise both are fair coins."""
    rng = random.Random(seed)
    out = []
    for di in range(n_dates):
        for _ in range(per_date):
            key = rng.choice(("A", "B"))
            p = (0.85 if key == "A" else 0.15) if strong else 0.5
            out.append(sample(di, key, rng.random() < p))
    return out


class FactorTests(unittest.TestCase):
    def ind(self, **over):
        base = {"sma50": [95.0], "sma200": [90.0], "rsi": [62.0], "macd_hist": [0.4], "hi252": [120.0], "lo252": [70.0], "atr": [2.0], "momentum126": [0.1]}
        base.update({k: [v] for k, v in over.items()})
        return base

    def test_buckets(self):
        f = learning.factors(self.ind(), [100.0], 0)
        self.assertEqual([f[k][0] for k in ("trend", "rsi", "macd", "position", "volatility", "momentum")], ["T2", "R3", "M1", "P1", "V1", "Q1"])
        low = learning.factors(self.ind(rsi=25.0, macd_hist=-1.0, sma50=110.0, sma200=120.0), [100.0], 0)
        self.assertEqual([low[k][0] for k in ("trend", "rsi", "macd")], ["T0", "R0", "M0"])

    def test_missing_inputs_give_none(self):
        self.assertIsNone(learning.factors(self.ind(rsi=None), [100.0], 0))
        self.assertIsNone(learning.factors(self.ind(hi252=70.0), [100.0], 0))

    def test_keys_go_from_fine_to_coarse(self):
        keys = learning.keys_of(learning.factors(self.ind(), [100.0], 0))
        self.assertEqual(keys, ["T2|R3|M1|P1", "T2|R3|M1", "T2|M1", "ALL"])


class TableTests(unittest.TestCase):
    def test_small_groups_are_left_out_and_lookup_backs_off(self):
        samples = [sample(d, "RARE" if d < 5 else "COMMON", True) for d in range(200)]
        table = learning.build_table(samples, 20)
        self.assertNotIn("RARE", table)
        entry = learning.lookup(table, ["RARE", "x|y|z", "x|z", "ALL"])
        self.assertEqual(entry["level"], "L2")
        self.assertEqual(entry["key"], "x|y|z")
        self.assertEqual(learning.lookup({}, ["a", "b", "c", "ALL"]), None)

    def test_frequency_and_range(self):
        table = learning.build_table(synthetic(True), 20)
        a = table["A"]
        self.assertAlmostEqual(a["p_up"], 0.85, delta=0.04)
        self.assertLess(a["ci"][0], a["p_up"])
        self.assertGreater(a["ci"][1], a["p_up"])
        self.assertAlmostEqual(table["ALL"]["p_up"], 0.5, delta=0.05)
        self.assertEqual((a["median"], a["p10"]), (0.05, -0.05))


class WalkForwardTests(unittest.TestCase):
    DATES = [f"d{d:04d}" for d in range(400)]

    def test_a_real_pattern_is_an_edge_on_later_data(self):
        v = learning.walk_forward(synthetic(True), self.DATES, 20)
        self.assertTrue(v["edge"], v)
        self.assertGreater(v["skill"], 0.1)

    def test_coin_flips_are_not_an_edge(self):
        v = learning.walk_forward(synthetic(False), self.DATES, 20)
        self.assertFalse(v["edge"], v)

    def test_too_little_data_is_not_an_edge(self):
        v = learning.walk_forward(synthetic(True, n_dates=10, per_date=3), self.DATES[:10], 20)
        self.assertFalse(v["edge"])
        self.assertIn("not enough", v["reason"])

    def test_the_training_side_never_sees_outcomes_after_the_split(self):
        # a pattern that exists only in the last 40% of dates cannot be learned from the first 60%
        late = [sample(d, "A", d >= 240) for d in range(400) for _ in range(8)]
        v = learning.walk_forward(late, self.DATES, 20)
        self.assertFalse(v["edge"])


class OutlookTests(unittest.TestCase):
    def learned(self):
        table = {"ALL": {"n": 500, "dates": 100, "p_up": 0.55, "ci": [0.5, 0.6], "median": 0.01, "p10": -0.08, "p90": 0.09},
                 "T2|R3|M1|P1": {"n": 300, "dates": 90, "p_up": 0.6, "ci": [0.54, 0.66], "median": 0.02, "p10": -0.07, "p90": 0.1}}
        return {"v": learning.VERSION, "generated_at": "2026-11-20T05:00:00+00:00", "span": ["2021-11-22", "2026-11-20"], "universe": 200,
                "horizons": {"20": {"table": table, "validation": {"edge": False, "skill": 0.002}}}}

    def record(self):
        return {"symbol": "A.NS", "price": 100.0, "indicators": {"sma50": 95.0, "sma200": 90.0, "rsi": 62.0, "macd_hist": 0.4, "hi252": 120.0, "lo252": 70.0, "atr": 2.0, "momentum126": 0.1}}

    def test_outlook_describes_the_factors_and_the_past_frequency(self):
        out = learning.outlook(self.record(), self.learned())
        h = out["horizons"]["20"]
        self.assertEqual((h["p_up"], h["level"], h["edge"], h["base"]), (0.6, "L3", False, 0.55))
        self.assertEqual(len(out["factors"]), 6)

    def test_nothing_learned_or_no_indicators_gives_none(self):
        self.assertIsNone(learning.outlook(self.record(), None))
        self.assertIsNone(learning.outlook({"symbol": "A", "price": 1.0}, self.learned()))

    def test_load_falls_back_to_the_previous_copy(self):
        good = self.learned()

        def failing(url, **kw):
            raise OSError("down")

        self.assertEqual(learning.load("o/r", good, failing), good)
        self.assertIsNone(learning.load("o/r", None, failing))
        self.assertEqual(learning.load("o/r", {"v": 99}, lambda u, **k: json.dumps(good).encode()), good)
        self.assertIsNone(learning.load("", {"v": 99}, failing))


class ForecastTests(unittest.TestCase):
    BARS = {"d": [f"2026-01-{d:02d}" for d in range(1, 31)], "c": [100.0 + d for d in range(30)]}

    def test_forecasts_resolve_only_when_the_horizon_has_passed(self):
        due = {"id": "a", "symbol": "A", "date": "2026-01-05", "horizon": 20, "p_up": 0.6}
        early = {"id": "b", "symbol": "A", "date": "2026-01-25", "horizon": 20, "p_up": 0.6}
        unknown = {"id": "c", "symbol": "Z", "date": "2026-01-05", "horizon": 20, "p_up": 0.6}
        done = learning.resolve_forecasts([due, early, unknown], {"A": self.BARS})
        self.assertEqual([(f["id"], y) for f, y in done], [("a", 1.0)])

    def test_scorecard_scores_against_the_base_rate(self):
        resolved = [({"p_up": 0.7}, 1.0), ({"p_up": 0.7}, 1.0), ({"p_up": 0.7}, 0.0), ({"p_up": 0.3}, 0.0)]
        card = learning.scorecard(resolved, 0.5)
        self.assertEqual(card["n"], 4)
        self.assertLess(card["brier"], card["brier_base"])
        self.assertEqual([b["from_pct"] for b in card["bands"]], [30, 70])
        self.assertEqual(learning.scorecard([], 0.5), {"n": 0})

    def test_make_forecasts_logs_one_per_stock_per_day(self):
        records = [{"symbol": "A.NS", "price": 10.0, "outlook": {"horizons": {"20": {"p_up": 0.6, "level": "L2", "edge": False}}}}, {"symbol": "B.NS", "price": 5.0, "outlook": None}]
        rows = learning.make_forecasts(records, {}, "2026-11-20")
        self.assertEqual([r["id"] for r in rows], ["A.NS|2026-11-20|20"])
