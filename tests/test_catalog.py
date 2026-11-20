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
        self.assertNotIn("short", rows["S4.NS"])                 # empty values are left out of the row
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


class RatioTests(unittest.TestCase):
    def test_summary_parses_ebitda_and_the_other_added_ratios(self):
        from engine import fundamentals
        payload = {"quoteSummary": {"result": [{
            "financialData": {"ebitda": {"raw": 5e9}, "ebitdaMargins": {"raw": 0.21}, "operatingMargins": {"raw": 0.17}, "freeCashflow": {"raw": 1e9},
                              "currentRatio": {"raw": 1.4}, "returnOnAssets": {"raw": 0.08}, "totalRevenue": {"raw": 2.4e10}},
            "defaultKeyStatistics": {"enterpriseToEbitda": {"raw": 11.5}, "trailingEps": {"raw": 42.0}, "bookValue": {"raw": 300.0}, "pegRatio": {"raw": 1.2}},
            "summaryDetail": {"priceToSalesTrailing12Months": {"raw": 3.1}}}]}}
        out = fundamentals.parse_summary(payload)
        self.assertEqual((out["ebitda"], out["ev_ebitda"], out["eps"], out["book_value"], out["peg"], out["price_to_sales"], out["current_ratio"], out["v"]),
                         (5e9, 11.5, 42.0, 300.0, 1.2, 3.1, 1.4, fundamentals.VERSION))
        self.assertIsNone(out["ev_revenue"])                      # missing stays missing, never zero

    def test_bulk_quote_carries_pe_eps_and_book_value(self):
        from engine import wide
        payload = {"quoteResponse": {"result": [{"symbol": "A.NS", "regularMarketPrice": 100.0, "trailingPE": 20.5, "epsTrailingTwelveMonths": 4.9,
                                                 "bookValue": 40.0, "priceToBook": 2.5, "trailingAnnualDividendYield": 0.0005, "dividendRate": 1.0}]}}
        q = wide.parse_quotes(payload)["A.NS"]
        self.assertEqual((q["pe"], q["eps"], q["book_value"], q["pb"], q["dividend_rate"]), (20.5, 4.9, 40.0, 2.5, 1.0))
        self.assertNotIn("dividend_yield", q)                      # Yahoo's trailing yield is wrong for many NSE stocks and is not used
        self.assertIsNone(q["forward_pe"])

    def test_index_row_has_quote_ratios_and_the_analysed_stocks_fundamentals(self):
        q = {"price": 100.0, "pe": 20.0, "eps": 5.0, "book_value": 40.0, "pb": 2.5, "dividend_yield": 0.01}
        record = {"short": {}, "long": {}, "fundamentals": {"ebitda": 5e9, "ev_ebitda": 11.5, "roe": 0.2, "sector": "X", "events": {}}}
        row = catalog.index_row({"symbol": "A.NS", "name": "A", "market": "IN"}, q, {"at": "t"}, record)
        self.assertEqual((row["pe"], row["eps"], row["book_value"]), (20.0, 5.0, 40.0))
        self.assertNotIn("fund", row)                                                         # the heavy fields are in index_extra.json
        self.assertEqual(catalog.extra_row(record)["fund"], {"ebitda": 5e9, "ev_ebitda": 11.5, "roe": 0.2})       # only the numeric ratios the table uses
        self.assertEqual(catalog.extra_row(None), {})

    def test_rotation_fetches_fundamentals_and_keeps_the_old_ones_when_yahoo_has_none(self):
        shard = catalog.shard_of("S0.NS")
        previous = {"S0.NS": {"symbol": "S0.NS", "fundamentals": {"market_cap": 1e9, "ebitda": 7.0}}}
        out = tempfile.mkdtemp()
        answers = {"S0.NS": {}, "S1.NS": {"market_cap": 2e9, "ebitda": 9.0}}
        catalog.update(NOW, {}, WIDE[:2], QUOTES, fake_analyse, {"bars": {}}, "prev", lambda prev, name: previous if name == f"detail/{shard}.json" else {}, out,
                       log=lambda *_: None, fundamentals_fn=lambda symbol: answers[symbol])
        got = {}
        for f in (Path(out) / "detail").glob("*.json"):
            got.update(json.loads(f.read_text()))
        self.assertEqual(got["S0.NS"]["fundamentals"]["ebitda"], 7.0)    # an empty answer never wipes an earlier one
        self.assertEqual(got["S1.NS"]["fundamentals"]["ebitda"], 9.0)


class StatusWithoutComparisonsTests(unittest.TestCase):
    def test_price_check_status_survives_a_run_with_nothing_verified(self):
        from engine import build
        accuracy = {"IN": {"match": 0, "verified": 0, "source": "NSE bhavcopy", "median_diff_pct": None, "mismatch": 0, "unverified": 5, "agreement_pct": None}}
        status = build.status_block({"stocks": []}, [], [], [], {}, {}, {}, {}, {}, [], None, None, accuracy)
        check = next(c for c in status["checks"] if c["name"] == "Price cross-check")
        self.assertIn("nothing to compare yet", check["detail"])


class FundamentalsCatchUpTests(unittest.TestCase):
    def test_stocks_analysed_before_the_new_fields_are_picked_again_soon(self):
        recent = (NOW - timedelta(hours=1)).isoformat()
        index = {s["symbol"]: {"at": recent, "ok": True, "fv": 1} for s in WIDE}
        self.assertEqual(catalog.pick(WIDE, QUOTES, index, NOW), [])
        self.assertEqual(len(catalog.pick(WIDE, QUOTES, index, NOW, fund_version=2)), 5)
        index["S0.NS"]["fv"] = 2
        self.assertNotIn("S0.NS", [s["symbol"] for s in catalog.pick(WIDE, QUOTES, index, NOW, fund_version=2)])


class TechnicalFieldTests(unittest.TestCase):
    def test_index_row_carries_the_technicals_the_filters_use(self):
        q = {"price": 100.0}
        record = {"short": {}, "long": {}, "indicators": {"rsi": 28.123456, "macd_hist": -0.5, "sma200": 90.0, "hi252": 150.0, "junk": 1, "sma50": None},
                  "pressure": {"label": "Buying pressure"}, "patterns": [{"name": "Hammer", "bias": "bullish", "note": "x"}]}
        row = catalog.extra_row(record)
        self.assertEqual(row["ind"], {"rsi": 28.123, "macd_hist": -0.5, "sma200": 90.0})      # only the fields the filters use, none that are missing
        self.assertEqual((row["pressure"], row["patterns"]), ("Buying pressure", [["Hammer", "bullish"]]))
        plain = catalog.index_row({"symbol": "B.NS", "name": "B", "market": "IN"}, q, None, None)
        self.assertTrue(all(k not in plain for k in ("ind", "pressure", "patterns", "fund")))


class KeepRowsTests(unittest.TestCase):
    def test_a_stock_with_no_quote_this_run_keeps_its_previous_row(self):
        out = tempfile.mkdtemp()
        old_row = {"symbol": "S3.NS", "name": "Stock 3", "market": "IN", "price": 7.0, "analysed": False}
        loader = lambda prev, name: {"stocks": [old_row]} if name == "index.json" else {}
        quotes = {k: v for k, v in QUOTES.items() if k != "S3.NS"}
        catalog.update(NOW, {}, WIDE, quotes, fake_analyse, {"bars": {}}, "prev", loader, out, log=lambda *_: None)
        rows = {r["symbol"]: r for r in json.loads((Path(out) / "index.json").read_text())["stocks"]}
        self.assertEqual(rows["S3.NS"]["price"], 7.0)
        self.assertEqual(len(rows), 5)


class ExtraFileTests(unittest.TestCase):
    def test_the_extra_file_holds_the_technicals_of_analysed_stocks_only(self):
        out = tempfile.mkdtemp()

        def analyse(entry, ctx):
            if entry["symbol"] == "S4.NS":
                raise ValueError("short")
            return {"symbol": entry["symbol"], "name": entry["name"], "short": {"label": "Buy", "score": 1}, "long": {}, "indicators": {"rsi": 40.0},
                    "fundamentals": {"ebitda": 5.0}, "pressure": {"label": "Buying pressure"}, "patterns": []}
        catalog.update(NOW, {}, WIDE, QUOTES, analyse, {"bars": {}}, "prev", lambda prev, name: {}, out, log=lambda *_: None)
        extra = json.loads((Path(out) / "index_extra.json").read_text())["stocks"]
        self.assertEqual(extra["S0.NS"]["ind"], {"rsi": 40.0})
        self.assertEqual(extra["S0.NS"]["fund"], {"ebitda": 5.0})
        self.assertNotIn("S4.NS", extra)
        rows = {r["symbol"]: r for r in json.loads((Path(out) / "index.json").read_text())["stocks"]}
        self.assertNotIn("ind", rows["S0.NS"])
