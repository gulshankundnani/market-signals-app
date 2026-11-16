import json
import unittest
from urllib.parse import unquote

from engine import alerts, digest, signals


class TargetTests(unittest.TestCase):
    """Indicative stop and targets. With price 100 and ATR 2 the stop distance (R) is 4."""

    def levels(self, side="buy", price=100.0, atr=2.0, resistance=(), support=(), high=None, low=None):
        zones = {"resistance": [{"level": r, "touches": 2} for r in resistance], "support": [{"level": s, "touches": 2} for s in support]}
        return signals.target_levels(price, atr, zones, high, low, side)

    def test_buy_uses_the_nearest_resistance_in_range(self):
        out = self.levels(resistance=(112.0, 108.0))
        self.assertEqual(out["short"]["price"], 108.0)
        self.assertEqual(out["short"]["basis"], "resistance")
        self.assertAlmostEqual(out["short"]["pct"], 8.0)
        self.assertEqual(out["stop"]["price"], 96.0)
        self.assertAlmostEqual(out["stop"]["pct"], -4.0)

    def test_a_resistance_that_is_too_close_or_too_far_falls_back_to_volatility(self):
        for level in (102.0, 103.9, 116.1, 150.0):
            out = self.levels(resistance=(level,))
            self.assertEqual(out["short"]["basis"], "volatility", level)
            self.assertAlmostEqual(out["short"]["price"], 108.0)
        for level in (104.0, 116.0):
            self.assertEqual(self.levels(resistance=(level,))["short"]["basis"], "resistance", level)

    def test_levels_on_the_wrong_side_of_the_price_are_ignored(self):
        out = self.levels(resistance=(95.0, 99.0))
        self.assertEqual(out["short"]["basis"], "volatility")

    def test_long_term_buy_uses_the_52_week_high_when_far_enough(self):
        out = self.levels(high=130.0)
        self.assertEqual((out["long"]["price"], out["long"]["basis"]), (130.0, "52-week high"))
        near = self.levels(high=102.0)
        self.assertEqual(near["long"]["basis"], "volatility")
        self.assertAlmostEqual(near["long"]["price"], 112.0)
        self.assertEqual(self.levels(high=90.0)["long"]["basis"], "volatility")

    def test_sell_mirrors_buy(self):
        out = self.levels("sell", support=(92.0, 88.0), low=70.0)
        self.assertEqual((out["short"]["price"], out["short"]["basis"]), (92.0, "support"))
        self.assertEqual((out["long"]["price"], out["long"]["basis"]), (70.0, "52-week low"))
        self.assertEqual(out["stop"]["price"], 104.0)
        self.assertGreater(out["stop"]["pct"], 0)
        self.assertLess(out["short"]["pct"], 0)
        fallback = self.levels("sell")
        self.assertAlmostEqual(fallback["short"]["price"], 92.0)
        self.assertAlmostEqual(fallback["long"]["price"], 88.0)

    def test_targets_are_always_on_the_profitable_side(self):
        for side, sign in (("buy", 1), ("sell", -1)):
            out = self.levels(side, resistance=(108.0,), support=(92.0,), high=130.0, low=70.0)
            for horizon in ("short", "long"):
                self.assertGreater(sign * (out[horizon]["price"] - 100.0), 0, (side, horizon))
            self.assertLess(sign * (out["stop"]["price"] - 100.0), 0)

    def test_cannot_be_worked_out(self):
        self.assertIsNone(self.levels("hold"))
        self.assertIsNone(self.levels("sideways"))
        for atr in (None, 0, -1):
            self.assertIsNone(self.levels(atr=atr))
        for price in (None, 0, -5.0):
            self.assertIsNone(self.levels(price=price))
        self.assertIsNone(self.levels(price=3.0, atr=2.0), "a stop at or below zero is not offered")

    def test_junk_in_the_level_lists_is_ignored(self):
        junk = {"resistance": [None, "x", {"level": "high"}, {"level": True}, {"touches": 3}], "support": None}
        out = signals.target_levels(100.0, 2.0, junk, "n/a", None, "buy")
        self.assertEqual(out["short"]["basis"], "volatility")
        self.assertEqual(out["long"]["basis"], "volatility")
        self.assertIsNotNone(signals.target_levels(100.0, 2.0, None, None, None, "sell"))

    def test_result_is_json_safe(self):
        json.dumps(self.levels(resistance=(108.0,), high=130.0), allow_nan=False)


class ReasonPartsTests(unittest.TestCase):
    def bars(self):
        closes = [100 * 1.004 ** i for i in range(300)]
        opens = [closes[0]] + closes[:-1]
        return {"t": list(range(300)), "d": [str(i) for i in range(300)], "o": opens, "c": closes,
                "h": [max(a, b) * 1.01 for a, b in zip(opens, closes)], "l": [min(a, b) * 0.99 for a, b in zip(opens, closes)],
                "v": [1000.0] * 300}

    def test_every_reason_carries_its_component_and_value(self):
        ind = signals.build_indicators(self.bars())
        for score, reasons, weights in (signals.short_score(ind, 299) + (signals.SHORT_WEIGHTS,),
                                        signals.long_score(ind, 299) + (signals.LONG_WEIGHTS,)):
            self.assertIsNotNone(score)
            self.assertTrue(reasons)
            keys = [r["key"] for r in reasons]
            self.assertEqual(len(keys), len(set(keys)), "a component must appear once")
            for r in reasons:
                self.assertIn(r["key"], weights)
                self.assertTrue(-1.0 <= r["value"] <= 1.0)
                self.assertIn(r["tone"], ("pos", "neg", "neutral"))
                self.assertTrue(r["text"])

    def test_tone_agrees_with_the_value(self):
        ind = signals.build_indicators(self.bars())
        for r in signals.short_score(ind, 299)[1] + signals.long_score(ind, 299)[1]:
            expected = "pos" if r["value"] > 0.15 else "neg" if r["value"] < -0.15 else "neutral"
            self.assertEqual(r["tone"], expected, r)

    def test_the_score_is_unchanged_by_recording_components(self):
        ind = signals.build_indicators(self.bars())
        score, reasons = signals.short_score(ind, 299)
        weights = signals.SHORT_WEIGHTS
        total = sum(weights[r["key"]] for r in reasons)
        rebuilt = sum(weights[r["key"]] * r["value"] for r in reasons) / total * 100
        self.assertAlmostEqual(rebuilt, score, delta=1.5, msg="values are rounded to 2 places, so the score matches to within rounding")

    def test_reasons_are_json_safe(self):
        ind = signals.build_indicators(self.bars())
        json.dumps(signals.short_score(ind, 299)[1], allow_nan=False)


def reasons():
    return [{"text": "Trend: price above 20-day average", "tone": "pos"}, {"text": "Momentum: MACD positive and rising", "tone": "pos"},
            {"text": "RSI 61 is in the normal range", "tone": "pos"}, {"text": "Volume is normal", "tone": "neutral"},
            {"text": "Volume pressure: selling pressure over 20 days", "tone": "neg"}, {"text": "News tone is positive", "tone": "pos"}]


def record(label="Buy", score=30, long_label="Hold", long_score=0, **overrides):
    rec = {"name": "Acme Ltd", "price": 100.0, "currency": "USD",
           "indicators": {"atr": 2.0, "hi252": 130.0, "lo252": 70.0},
           "levels": {"zones": {"resistance": [{"level": 108.0, "touches": 2}], "support": [{"level": 92.0, "touches": 2}]}},
           "short": {"label": label, "score": score, "reasons": reasons()},
           "long": {"label": long_label, "score": long_score, "reasons": reasons()},
           "news": {"fresh": []}}
    rec.update(overrides)
    return rec


def message(rec, announced="hold", new_side="buy", horizon="short"):
    return alerts.format_signal_alert(rec, "ACME", horizon, announced, new_side, rec[horizon])


class SignalMessageTests(unittest.TestCase):
    def test_a_buy_alert_has_the_move_a_target_a_stop_and_the_reasons(self):
        lines = message(record("Strong Buy", 55)).split("\n")
        self.assertEqual(lines[0], "STRONGLY ALIGNED BULLISH: Acme Ltd (ACME)")
        self.assertEqual(lines[1], "Weeks view: Mixed -> Strongly aligned bullish (indicator alignment +55). Price 100.00 USD")
        self.assertEqual(lines[2], "Resistance reference 108.00 (+8.0%, nearest resistance). Volatility risk level 96.00 (-4.0%).")
        self.assertTrue(lines[3].startswith("Indicator readings: Trend: price above 20-day average; Momentum: MACD positive and rising; RSI 61"))

    def test_only_reasons_that_support_the_new_signal_are_listed(self):
        buy_why = message(record("Buy", 30)).split("\n")[-1]
        self.assertNotIn("selling pressure", buy_why)
        self.assertNotIn("Volume is normal", buy_why)
        self.assertEqual(buy_why.count(";"), alerts.MAX_REASONS - 1)
        sell_why = message(record("Sell", -30), "hold", "sell").split("\n")[-1]
        self.assertIn("selling pressure", sell_why)
        self.assertNotIn("Trend: price above", sell_why)

    def test_a_sell_alert_has_a_target_below_and_a_stop_above(self):
        text = message(record("Sell", -30), "hold", "sell")
        self.assertTrue(text.startswith("ALIGNED BEARISH:"))
        self.assertIn("Support reference 92.00 (-8.0%, nearest support). Volatility risk level 104.00 (+4.0%).", text)

    def test_a_signal_that_faded_to_hold_has_no_target(self):
        text = message(record("Hold", 5), "buy", "hold")
        self.assertIn("Weeks view: Bullish -> Mixed (indicator alignment +5)", text)
        self.assertIn("No reference level: the reading has faded to mixed.", text)
        self.assertNotIn("Target ", text.replace("No target", ""))
        why = text.split("\n")[-1]
        self.assertIn("selling pressure", why)
        self.assertIn("Volume is normal", why)
        self.assertNotIn("Trend: price above", why)

    def test_long_term_alerts_use_the_long_term_target(self):
        rec = record("Hold", 0, long_label="Buy", long_score=40)
        text = message(rec, "hold", "buy", "long")
        self.assertIn("Months-to-years view: Mixed -> Aligned bullish (indicator alignment +40)", text)
        self.assertIn("Resistance reference 130.00 (+30.0%, 52-week high)", text)

    def test_volatility_targets_say_how_they_were_worked_out(self):
        rec = record("Buy", 30)
        rec["levels"] = {"zones": {"resistance": [], "support": []}}
        rec["indicators"]["hi252"] = 101.0
        self.assertIn("Resistance reference 108.00 (+8.0%, volatility-based, 2x the risk distance)", message(rec))
        long_rec = dict(rec, long={"label": "Buy", "score": 40, "reasons": reasons()})
        self.assertIn("Resistance reference 112.00 (+12.0%, volatility-based, 3x the risk distance)", message(long_rec, "hold", "buy", "long"))

    def test_missing_price_history_means_no_target_but_still_an_alert(self):
        for overrides in ({"indicators": None}, {"indicators": {}}, {"levels": None}, {"indicators": {"atr": None}}):
            rec = record("Buy", 30, **overrides)
            if overrides == {"levels": None}:
                self.assertIn("Resistance reference ", message(rec))
                continue
            text = message(rec)
            self.assertNotIn("Target ", text, overrides)
            self.assertIn("Mixed -> Aligned bullish", text)
            self.assertIn("Indicator readings:", text)

    def test_missing_reasons_or_score_do_not_break_the_message(self):
        rec = record("Buy", 30)
        rec["short"] = {"label": "Buy"}
        text = message(rec)
        self.assertIn("Weeks view: Mixed -> Aligned bullish. Price 100.00 USD", text)
        self.assertNotIn("Indicator readings:", text)
        self.assertNotIn("(score", text)
        rec["short"] = {"label": "Buy", "score": True, "reasons": None}
        self.assertNotIn("(score", message(rec))

    def test_prices_without_a_currency_still_read_cleanly(self):
        text = message(record("Buy", 30, currency=None))
        self.assertIn("Price 100.00", text.split("\n")[1])
        self.assertFalse(text.split("\n")[1].endswith(" "))


def headline(title="Quarterly results", n=1, age=5):
    return {"title": title, "link": f"https://x/{n}", "source": "Wire", "age_min": age}


class BuildItemsTests(unittest.TestCase):
    SIDES = {"hold": "hold"}

    @staticmethod
    def run_items(current, seen=(), **kwargs):
        sides = {s: {"short": "hold", "long": "hold"} for s in current}
        items = []
        lines, seen_after = alerts.build_messages({}, current, list(current), list(seen), False, sides=sides, items=items, **kwargs)
        return lines, items, seen_after

    def test_readings_come_before_headlines_and_items_line_up_with_lines(self):
        current = {"A": record("Buy", 30, news={"fresh": [headline()]}), "B": record("Buy", 30)}
        lines, items, _ = self.run_items(current)
        self.assertEqual(len(lines), len(items))
        self.assertEqual([(i["symbol"], i["kind"]) for i in items], [("A", "signal"), ("B", "signal"), ("A", "news")])
        self.assertTrue(lines[2].startswith("News, "))
        self.assertNotRegex(lines[2], "Positive|Negative")

    def test_a_reading_item_carries_what_the_card_needs(self):
        _, items, _ = self.run_items({"A": record("Buy", 30)})
        item = items[0]
        self.assertEqual((item["name"], item["label"], item["score"], item["price"], item["side"], item["announced"]),
                         ("Acme Ltd", "Buy", 30, 100.0, "buy", "hold"))
        self.assertEqual(item["levels"]["kind"], "Resistance reference")
        self.assertTrue(item["reasons"])

    def test_a_headline_item_carries_its_source_and_article_link(self):
        _, items, _ = self.run_items({"A": record("Hold", 0, news={"fresh": [headline("Plant opened", 7, 64)]})})
        self.assertEqual(items, [{"symbol": "A", "kind": "news", "name": "Acme Ltd", "title": "Plant opened",
                                  "source": "Wire", "link": "https://x/7", "age_min": 64}])

    def test_one_headline_per_stock_and_a_cap_per_run_and_the_rest_are_marked_seen(self):
        two = {"fresh": [headline("First", 1), headline("Second", 2)]}
        lines, items, seen = self.run_items({"A": record("Hold", 0, news=two)})
        self.assertEqual([i["title"] for i in items], ["First"])
        self.assertEqual(len(seen), 2, "the second headline is recorded so it is not reported later")
        many = {f"S{n}": record("Hold", 0, news={"fresh": [headline(f"Title {n}", n)]}) for n in range(alerts.NEWS_PER_RUN + 4)}
        _, items, _ = self.run_items(many)
        self.assertEqual(len(items), alerts.NEWS_PER_RUN)

    def test_news_never_changes_a_reading(self):
        base = self.run_items({"A": record("Buy", 30)})[0]
        with_news = self.run_items({"A": record("Buy", 30, news={"fresh": [headline("Fraud probe opened")]})})[0]
        self.assertEqual(base[0], with_news[0])

    def test_signal_alerts_in_a_run_carry_targets(self):
        lines, _ = alerts.build_messages({}, {"A": record("Buy", 30)}, ["A"], [], False, sides={"A": {"short": "hold", "long": "hold"}})
        self.assertIn("Resistance reference 108.00", lines[0])
        self.assertIn("Indicator readings:", lines[0])


class LinkAndDigestTests(unittest.TestCase):
    SITE = "https://user.github.io/market-signals-app/"

    def test_links_open_the_stock_analysis(self):
        self.assertEqual(alerts.stock_link(self.SITE, "AAPL"), "https://user.github.io/market-signals-app/#stock=AAPL&section=signals")
        self.assertEqual(alerts.stock_link(self.SITE.rstrip("/"), "AAPL", "news"), "https://user.github.io/market-signals-app/#stock=AAPL&section=news")

    def test_symbols_with_special_characters_survive_the_round_trip(self):
        for symbol in ("M&M.NS", "BAJAJ-AUTO.NS", "BRK-B", "HDFCBANK.NS", "A B", "50%", "A#B", "A?B=C"):
            link = alerts.stock_link(self.SITE, symbol)
            encoded = link.split("#stock=")[1].split("&section=")[0]
            self.assertEqual(unquote(encoded), symbol)
            self.assertNotRegex(encoded, r"[&#? =]")

    ITEMS_LINKS = None

    @staticmethod
    def reading(n=1, side="sell"):
        return {"kind": "signal", "symbol": f"S{n}.NS", "name": f"Stock {n}", "horizon": "short", "side": side, "announced": "hold",
                "label": "Sell" if side == "sell" else "Buy", "score": -27 if side == "sell" else 31, "price": 398.8, "currency": "INR",
                "levels": {"kind": "Support reference", "price": 364.95, "pct": -8.5, "basis": "nearest support", "risk_price": 415.73, "risk_pct": 4.2},
                "reasons": ["Trend: price below 20-day average", "Volume pressure: selling pressure over 20 days"]}

    @staticmethod
    def story(n=1, link="https://news.google.com/a"):
        return {"kind": "news", "symbol": f"N{n}", "name": f"Name {n}", "title": f"Headline number {n}", "source": "Livemint",
                "link": link, "age_min": 75}

    def test_a_reading_card_is_a_bulleted_list_with_its_figures_and_link(self):
        card = digest.reading_card(self.reading(), self.SITE + "#stock=S1.NS")
        for expected in ("Stock 1 (S1.NS)", "Aligned bearish \u00b7 Weeks view", "\u2022 Change: Mixed \u2192 Aligned bearish (alignment -27)",
                         "\u2022 Price: \u20b9398.80", "\u2022 Support reference: \u20b9364.95 (-8.5%, nearest support)",
                         "\u2022 Volatility risk level: \u20b9415.73 (+4.2%)", "\u2022 Indicator readings:",
                         "   \u2013 Trend: price below 20-day average", "\u2022 Open analysis: " + self.SITE + "#stock=S1.NS"):
            self.assertIn(expected, card)

    def test_a_reading_that_faded_says_there_is_no_reference_level(self):
        item = {**self.reading(), "side": "hold", "label": "Hold", "levels": None, "announced": "sell", "score": 2}
        card = digest.reading_card(item, None)
        self.assertIn("\u2022 No reference level: the reading has faded to mixed", card)
        self.assertNotIn("Open analysis", card)

    def test_a_news_card_has_the_headline_source_age_and_both_links_and_no_tone(self):
        card = digest.news_card(self.story(), self.SITE + "#stock=N1&section=news")
        for expected in ("Name 1 (N1)", "\u2022 Headline: Headline number 1", "\u2022 Source: Livemint \u00b7 75 min ago",
                         "\u2022 Read article: https://news.google.com/a", "\u2022 Open analysis: " + self.SITE):
            self.assertIn(expected, card)
        self.assertNotRegex(card, "(?i)positive|negative|bullish|bearish")

    def test_a_news_card_without_an_article_link_or_source_still_reads_cleanly(self):
        card = digest.news_card({**self.story(), "link": "", "source": "", "age_min": None}, None)
        self.assertIn("Source: unknown \u00b7 time unknown", card)
        self.assertNotIn("Read article", card)

    def test_the_digest_has_a_header_with_counts_sections_and_one_footer(self):
        items = [self.reading(1), self.reading(2, "buy"), self.story(1)]
        links = [self.SITE + f"#{n}" for n in range(3)]
        [(text, start, end)] = digest.render(items, links, 1_794_700_000)
        self.assertEqual((start, end), (0, 3))
        self.assertTrue(text.startswith("\U0001F4CA Market Signals \u00b7 Sun 15 Nov, 05:16 IST\n2 reading changes \u00b7 1 headline"))
        self.assertLess(text.index("READINGS"), text.index("NEWS"))
        self.assertEqual(text.count("READINGS"), 1)
        self.assertEqual(text.count("\U0001F534"), 1)
        self.assertEqual(text.count("\U0001F7E2"), 1)
        self.assertTrue(text.endswith(alerts.NOTE + chr(10) + alerts.ADVISORY))
        self.assertEqual(text.count(alerts.NOTE), 1)

    def test_nothing_to_send_gives_no_message(self):
        self.assertEqual(digest.render([], [], 1_794_700_000), [])

    def test_a_busy_run_is_split_into_messages_within_telegrams_limit_with_no_gaps(self):
        items = [self.reading(n) for n in range(30)]
        links = [self.SITE + f"#stock=S{n}" for n in range(30)]
        messages = digest.render(items, links, 1_794_700_000)
        self.assertGreater(len(messages), 1)
        self.assertLessEqual(len(messages), digest.MAX_MESSAGES)
        for text, start, end in messages:
            self.assertLessEqual(len(text), alerts.TELEGRAM_MAX_CHARS)
            self.assertTrue(text.endswith(alerts.ADVISORY))
        self.assertEqual(messages[0][1], 0)
        for (_, _, end), (_, start, _) in zip(messages, messages[1:]):
            self.assertEqual(end, start)
        self.assertIn(f"({len(messages)}/{len(messages)})", messages[-1][0].split("\n")[0])

    def test_alerts_beyond_the_last_message_are_counted_not_dropped(self):
        items = [self.reading(n) for n in range(60)]
        messages = digest.render(items, [None] * 60, 1_794_700_000)
        sent = messages[-1][2]
        self.assertLess(sent, 60)
        self.assertIn(f"+{60 - sent} more alerts. Open the app to see them.", messages[-1][0])

    def test_headlines_that_follow_readings_get_their_own_section_in_the_message_that_carries_them(self):
        items = [self.reading(1), self.story(1), self.story(2)]
        [(text, _, _)] = digest.render(items, [None] * 3, 1_794_700_000)
        self.assertEqual(text.count("NEWS"), 1)
        self.assertIn("2 headlines", text)

    def test_one_very_long_headline_is_shortened(self):
        card = digest.news_card({**self.story(), "title": "word " * 200}, None)
        self.assertLessEqual(len(card.split("\n")[1]), digest.MAX_TITLE + 30)
        self.assertIn("\u2026", card)


class LogSymbolTests(unittest.TestCase):
    NOW = 1_800_000_000

    def test_entries_remember_their_stock(self):
        log, added = alerts.update_log([], ["a", "b", "c"], self.NOW, ["AAPL", None, "TCS.NS"])
        self.assertEqual([e.get("symbol") for e in log], ["AAPL", None, "TCS.NS"])
        self.assertEqual(len(added), 3)

    def test_symbols_are_optional_and_may_be_shorter_than_the_lines(self):
        log, _ = alerts.update_log([], ["a", "b"], self.NOW)
        self.assertTrue(all("symbol" not in e for e in log))
        log, _ = alerts.update_log([], ["a", "b"], self.NOW, ["AAPL"])
        self.assertEqual([e.get("symbol") for e in log], ["AAPL", None])

    def test_log_with_symbols_is_json_safe(self):
        log, _ = alerts.update_log([], [message(record("Buy", 30))], self.NOW, ["ACME"])
        json.dumps(log, allow_nan=False)


class AdvisoryTests(unittest.TestCase):
    def test_every_digest_and_outcome_message_carries_the_advisory_and_stays_in_limits(self):
        items = [LinkAndDigestTests.reading(n) for n in range(40)]
        for text, _, _ in digest.render(items, [f"https://s/#stock={n}" for n in range(40)], 1_794_700_000):
            self.assertTrue(text.endswith(alerts.ADVISORY))
            self.assertLessEqual(len(text), alerts.TELEGRAM_MAX_CHARS)
        reply = alerts.format_outcome({"symbol": "A", "name": "A", "horizon": "short", "side": "buy", "price": 100.0, "currency": "USD"},
                                      {"outcome": "target", "exit_price": 108.0, "resolved_date": "2026-01-01", "days_held": 3})
        self.assertTrue(reply.endswith(alerts.ADVISORY))


class DropIrrelevantTests(unittest.TestCase):
    RECORDS = {"TRENT.NS": {"name": "Trent"}, "PFC.NS": {"name": "Power Finance Corporation"}, "AAPL": {"name": "Apple"}}

    @staticmethod
    def entry(text, symbol):
        return {"id": text[:8], "symbol": symbol, "text": text, "ts": 1}

    def test_wrong_news_alerts_are_removed_and_right_ones_stay(self):
        wrong = self.entry("News, Trent (TRENT.NS): The Gary Trent Jr. Investigation Could DESTROY the Franchise (Yahoo Sports, 14 h ago)", "TRENT.NS")
        roundup = self.entry("News, Power Finance Corporation (PFC.NS): Upcoming dividend, buyback record date: MRF, SCI, PFC, PTC (Livemint, 3 h ago)", "PFC.NS")
        broker = self.entry("News, Apple (AAPL): Ecolab (ECL) Shares Rise After Apple Upgrade (Wire, 5 min ago)", "AAPL")
        right = self.entry("News, Apple (AAPL): Apple (AAPL) shares surge after record profit (Reuters, 12 min ago)", "AAPL")
        signal = self.entry("ALIGNED BULLISH: Apple (AAPL)" + chr(10) + "Weeks view: Mixed -> Aligned bullish. Price 100.00 USD", "AAPL")
        unknown = self.entry("News, Ghost Corp (GHOST): Ghost files results (Wire, 5 min ago)", "GHOST")
        kept = alerts.drop_irrelevant([wrong, roundup, broker, right, signal, unknown], self.RECORDS)
        self.assertEqual(kept, [right, signal, unknown])

    def test_old_entries_that_guessed_good_or_bad_news_are_removed(self):
        positive = self.entry("Positive news, Apple (AAPL): Apple (AAPL) shares surge after record profit (Reuters, 12 min ago)", "AAPL")
        negative = self.entry("Negative news, Apple (AAPL): Apple (AAPL) faces probe (Reuters, 12 min ago)", "AAPL")
        self.assertEqual(alerts.drop_irrelevant([positive, negative], self.RECORDS), [])

    def test_junk_in_the_log_is_ignored(self):
        self.assertEqual(alerts.drop_irrelevant(["x", None, {"text": 5}, {}], self.RECORDS), ["x", None, {"text": 5}, {}])
        self.assertEqual(alerts.drop_irrelevant([], self.RECORDS), [])


if __name__ == "__main__":
    unittest.main()
