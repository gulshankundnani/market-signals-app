import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from engine import catalog

NOW = datetime(2026, 11, 19, 6, 0, tzinfo=timezone.utc)
WIDE = [{"symbol": f"S{n}.NS", "name": f"Stock {n}", "market": "IN"} for n in range(5)]
QUOTES = {s["symbol"]: {"price": 100.0 + n, "change_pct": 1.0, "avg_volume": 1000.0 * (n + 1), "volume": 10.0, "cap": 1e9, "high52": 150.0, "low52": 50.0}
          for n, s in enumerate(WIDE)}


def fake_analyse(entry, ctx):
    if entry["symbol"] == "S4.NS":
        raise ValueError("only 3 usable bars")
    return {"symbol": entry["symbol"], "name": entry["name"], "short": {"label": "Buy", "score": 30}, "long": {"label": "Hold", "score": 5}}


class CatalogTests(unittest.TestCase):
    def run_update(self, index=None, loader=None, **kw):
        out = tempfile.mkdtemp()
        calls = []

        def counting(entry, ctx):
            calls.append(entry["symbol"])
            self.assertTrue(ctx["news_breaker"].open)             # no per-stock headline request for the wide list
            return fake_analyse(entry, ctx)
        result = catalog.update(NOW, index, WIDE, QUOTES, counting, {"bars": {}}, "prev", loader or (lambda prev, name: {}), out, log=lambda *_: None)
        return result, Path(out), calls

    def test_shard_is_stable_and_in_range(self):
        self.assertEqual(catalog.shard_of("RELIANCE.NS"), catalog.shard_of("RELIANCE.NS"))
        self.assertTrue(all(0 <= int(catalog.shard_of(s["symbol"])) < catalog.SHARDS for s in WIDE))

    def test_writes_an_index_row_for_every_quoted_stock_and_detail_only_for_analysed_ones(self):
        index, out, calls = self.run_update()
        rows = {r["symbol"]: r for r in json.loads((out / "index.json").read_text())["stocks"]}
        self.assertEqual(len(rows), 5)
        self.assertTrue(rows["S0.NS"]["analysed"])
        self.assertEqual(rows["S0.NS"]["short"], {"label": "Buy", "score": 30})
        self.assertFalse(rows["S4.NS"]["analysed"])               # too little history: the row shows the quote only
        self.assertIsNone(rows["S4.NS"]["short"])
        shard = json.loads((out / "detail" / f"{rows['S0.NS']['shard']}.json").read_text())
        self.assertIn("S0.NS", shard)
        self.assertEqual(index["S4.NS"]["ok"], False)

    def test_most_traded_first_then_not_asked_again_until_stale(self):
        chosen = catalog.pick(WIDE, QUOTES, {}, NOW, size=2)
        self.assertEqual([s["symbol"] for s in chosen], ["S4.NS", "S3.NS"])
        fresh = {s["symbol"]: {"at": (NOW - timedelta(hours=1)).isoformat(), "ok": True} for s in WIDE}
        self.assertEqual(catalog.pick(WIDE, QUOTES, fresh, NOW), [])
        old = {**fresh, "S2.NS": {"at": (NOW - timedelta(hours=30)).isoformat(), "ok": True}}
        self.assertEqual([s["symbol"] for s in catalog.pick(WIDE, QUOTES, old, NOW)], ["S2.NS"])

    def test_previous_shards_are_kept_and_a_lost_record_is_analysed_again(self):
        shard = catalog.shard_of("S1.NS")
        previous = {"S1.NS": {"symbol": "S1.NS", "short": {"label": "Sell", "score": -40}, "long": {}}}
        index = {s["symbol"]: {"at": (NOW - timedelta(hours=1)).isoformat(), "ok": True} for s in WIDE}
        result, out, calls = self.run_update(index, loader=lambda prev, name: previous if name == f"detail/{shard}.json" else {})
        self.assertEqual(sorted(calls), sorted(s["symbol"] for s in WIDE if s["symbol"] != "S1.NS"))     # S1 kept, the other records were missing
        kept = json.loads((out / "detail" / f"{shard}.json").read_text())
        self.assertEqual(kept["S1.NS"]["short"]["label"], "Sell")

    def test_stocks_that_left_the_list_are_dropped(self):
        shard = catalog.shard_of("GONE.NS")
        result, out, _ = self.run_update(loader=lambda prev, name: {"GONE.NS": {"symbol": "GONE.NS"}} if name == f"detail/{shard}.json" else {})
        path = out / "detail" / f"{shard}.json"
        self.assertTrue(not path.exists() or "GONE.NS" not in json.loads(path.read_text()))


class CatalogListTests(unittest.TestCase):
    def test_quote_lists_work_without_any_analysis(self):
        self.assertEqual(catalog.quote_lists({"price": 148.0, "high52": 150.0, "low52": 50.0}), ["Near 52-week high"])
        self.assertEqual(catalog.quote_lists({"price": 52.0, "high52": 150.0, "low52": 50.0}), ["Near 52-week low"])
        self.assertEqual(catalog.quote_lists({"price": 100.0, "high52": 150.0, "low52": 50.0}), [])
        self.assertEqual(catalog.quote_lists({"price": 100.0}), [])

    def test_analysed_stocks_get_the_curated_lists_too(self):
        def with_lists(records):
            for r in records:
                r["lists"] = ["Other", "Oversold"]
        out = tempfile.mkdtemp()
        catalog.update(NOW, {}, WIDE[:1], QUOTES, fake_analyse, {"bars": {}}, "prev", lambda prev, name: {}, out, log=lambda *_: None, lists_fn=with_lists)
        row = json.loads((Path(out) / "index.json").read_text())["stocks"][0]
        self.assertEqual(row["lists"], ["Oversold"])              # the sector placeholder "Other" is not a list


class LiteRecordTests(unittest.TestCase):
    FULL = {"symbol": "A.NS", "name": "A", "price": 10.0, "spark": [1, 2, 3, 4], "spark_sma50": [1, 2], "live": {"short": {}}, "levels": {"zones": []},
            "news": {"items": [{"title": "x"}] * 6, "fresh": [{"title": "x"}], "error": False},
            "dividend": {"frequency": "Yearly", "history": [1, 2, 3], "forward_yield": 0.02},
            "backtest": {"short": {"beats_buy_hold_oos": True, "trades": [1] * 50}, "long": {"beats_buy_hold_oos": False}, "note": "x"},
            "short": {"label": "Buy", "score": 30}}

    def test_lite_keeps_what_the_dashboard_needs_and_drops_the_rest(self):
        lite = catalog.lite(self.FULL)
        self.assertEqual(lite["spark"], [3, 4])
        self.assertNotIn("live", lite)
        self.assertNotIn("levels", lite)
        self.assertEqual(lite["news"], {"fresh": [{"title": "x"}], "items": [], "error": False})
        self.assertEqual(lite["backtest"], {"short": {"beats_buy_hold_oos": True}, "long": {"beats_buy_hold_oos": False}})
        self.assertNotIn("history", lite["dividend"])
        self.assertEqual(lite["dividend"]["forward_yield"], 0.02)
        self.assertEqual((lite["short"], lite["shard"], lite["lite"]), (self.FULL["short"], catalog.shard_of("A.NS"), True))
        self.assertEqual(len(json.dumps(lite)) < len(json.dumps(self.FULL)), True)

    def test_full_records_of_curated_stocks_are_stored_for_the_page_but_slim_ones_never_replace_them(self):
        out = tempfile.mkdtemp()
        full = {"symbol": "KEEP.NS", "name": "Keep", "price": 1.0, "short": {"label": "Buy", "score": 1}, "long": {}}
        catalog.update(NOW, {}, [], {}, fake_analyse, {"bars": {}}, "prev", lambda prev, name: {}, out, log=lambda *_: None, curated_records=[full])
        shard = json.loads((Path(out) / "detail" / f"{catalog.shard_of('KEEP.NS')}.json").read_text())
        self.assertIn("KEEP.NS", shard)
        out2 = tempfile.mkdtemp()
        previous = {"KEEP.NS": full}
        shard_name = f"detail/{catalog.shard_of('KEEP.NS')}.json"
        catalog.update(NOW, {}, [], {}, fake_analyse, {"bars": {}}, "prev", lambda prev, name: previous if name == shard_name else {}, out2, log=lambda *_: None,
                       curated_records=[{**catalog.lite(full), "carried": True}])
        kept = json.loads((Path(out2) / "detail" / f"{catalog.shard_of('KEEP.NS')}.json").read_text())
        self.assertNotIn("lite", kept["KEEP.NS"])
