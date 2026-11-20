import unittest

from engine import digest, evidence


def block(rule, hold, beat, days=1186, trades=45):
    return {"total": {"total_return": rule, "buy_hold_return": hold, "beats_buy_hold": beat}, "tested_days": days, "trades": trades, "few_trades": False}


class EvidenceTests(unittest.TestCase):
    def test_a_stocks_own_result_is_taken_from_the_backtest_total(self):
        e = evidence.stock_evidence({"short": block(-0.197, 0.238, False), "long": block(0.5, 0.4, True, days=1260)})
        self.assertEqual(e["short"], {"rule": -0.197, "hold": 0.238, "beat": False, "years": 4.7, "trades": 45, "few_trades": False})
        self.assertTrue(e["long"]["beat"])
        self.assertEqual(evidence.stock_evidence({}), {})
        self.assertEqual(evidence.stock_evidence({"short": {"total": None}}), {})

    def test_the_whole_list_summary_counts_stocks_where_the_rule_beat_holding(self):
        records = [{"market": "IN", "evidence": {"short": {"rule": r, "hold": h, "beat": r > h, "years": 4.0}}} for r, h in ((0.1, 0.5), (0.9, 0.5), (0.0, 0.3), (-0.1, 0.2))]
        records.append({"market": "US", "evidence": {}})
        out = evidence.summarise(records)
        self.assertEqual(out["IN"]["short"]["n"], 4)
        self.assertEqual((out["IN"]["short"]["beat"], out["IN"]["short"]["beat_pct"]), (1, 25))
        self.assertAlmostEqual(out["IN"]["short"]["median_rule"], 0.05)
        self.assertNotIn("short", out["US"])

    def test_alert_sentence_says_which_way_it_went_without_a_verdict_on_the_stock(self):
        e = evidence.stock_evidence({"short": block(-0.197, 0.238, False)})
        text = evidence.evidence_text(e, "short")
        self.assertIn("rule -20% vs buy-and-hold +24%", text)
        self.assertIn("lagged holding", text)
        self.assertIsNone(evidence.evidence_text(e, "long"))

    def test_thin_and_short_history_flags(self):
        self.assertEqual(evidence.data_flags(100.0, 5000, "IN", 400), ["thin"])          # Rs 5 lakh a day
        self.assertEqual(evidence.data_flags(100.0, 500_000, "IN", 120), ["short_history"])
        self.assertEqual(evidence.data_flags(100.0, 500_000, "IN", 400), [])
        self.assertEqual(evidence.data_flags(100.0, None, "IN", 400), [])                # unknown volume is not "thin"

    def test_a_reading_card_carries_the_evidence_and_the_thin_warning(self):
        item = {"symbol": "A.NS", "name": "A", "currency": "INR", "price": 100.0, "horizon": "short", "side": "buy", "announced": "hold", "label": "Buy", "score": 30,
                "reasons": [], "evidence": "Past test on this stock (4.7 yrs, after costs): rule -20% vs buy-and-hold +24%, so it lagged holding", "flags": ["thin"]}
        card = digest.reading_card(item, None)
        self.assertIn("lagged holding", card)
        self.assertIn("Thinly traded", card)
        plain = digest.reading_card({**item, "evidence": None, "flags": []}, None)
        self.assertNotIn("Past test", plain)


if __name__ == "__main__":
    unittest.main()
