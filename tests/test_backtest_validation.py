"""Checks on how the backtest trades, charges costs and measures results, using small series worked out by hand."""
import math
import unittest

from engine import backtest as bt
from engine import costs


def bars_from(closes, opens=None):
    o = list(opens) if opens is not None else list(closes)
    return {"t": list(range(len(closes))), "d": [str(i) for i in range(len(closes))], "o": o,
            "h": [max(a, b) for a, b in zip(o, closes)], "l": [min(a, b) for a, b in zip(o, closes)], "c": list(closes), "v": [1000.0] * len(closes)}


class NoLookAheadTests(unittest.TestCase):
    def test_a_signal_on_one_close_trades_at_the_next_open(self):
        closes = [100.0] * 130
        opens = [100.0] * 130
        closes[80] = 160.0                       # the close that makes the rule bullish
        opens[81] = 150.0                        # the next open is where the trade must happen
        bars = bars_from(closes, opens)
        rule = lambda i: 25 if closes[i] > 150 else -5
        curve, trades, _, _ = bt._simulate(bars, rule, 0, 20, 0, 0.0, 0.0)
        entry_idx, exit_idx, ret = trades[0]
        self.assertEqual(entry_idx, 81)          # not 80: that bar's own close was already used to decide
        self.assertAlmostEqual(ret, opens[exit_idx] / 150.0 - 1)      # bought at the 150 open, not at the 160 close

    def test_the_rule_is_never_asked_about_the_bar_it_trades_on(self):
        n = 130
        bars = bars_from([100.0 + i * 0.1 for i in range(n)])
        asked = []

        def rule(i):
            asked.append(i)
            return 25

        bt._simulate(bars, rule, 0, 20, 0, 0.0, 0.0)
        self.assertEqual(max(asked), n - 2)      # the last bar's score would only decide a trade on a bar that does not exist
        self.assertEqual(asked, sorted(asked))


class CostTests(unittest.TestCase):
    def test_a_round_trip_at_flat_prices_loses_exactly_the_two_costs(self):
        n = 130
        bars = bars_from([100.0] * n)
        rule = lambda i: 25 if 10 <= i < 40 else -5
        entry, exit_ = 0.002, 0.003
        curve, trades, _, ended_open = bt._simulate(bars, rule, 0, 20, 0, entry, exit_)
        expected = (1 - exit_) / (1 + entry) - 1
        self.assertAlmostEqual(curve[-1] - 1, expected, places=12)
        self.assertAlmostEqual(trades[0][2], expected, places=12)
        self.assertFalse(ended_open)

    def test_an_open_position_at_the_end_is_valued_net_of_the_exit_cost(self):
        n = 130
        bars = bars_from([100.0] * n)
        entry, exit_ = 0.002, 0.003
        curve, trades, _, ended_open = bt._simulate(bars, lambda i: 25, 0, 20, 0, entry, exit_)
        self.assertTrue(ended_open)
        self.assertAlmostEqual(curve[-1] - 1, (1 - exit_) / (1 + entry) - 1, places=12)
        self.assertEqual(len(trades), 1)
        self.assertAlmostEqual(trades[0][2], (1 - exit_) / (1 + entry) - 1, places=12)

    def test_gross_return_is_what_is_left_without_costs_and_the_drag_is_the_difference(self):
        n = 130
        closes = [100.0 + i * 0.5 for i in range(n)]
        result = bt.backtest(bars_from(closes), lambda i: 25, 0, entry_cost=0.002, exit_cost=0.003)
        self.assertAlmostEqual(result["gross_return"], closes[-1] / closes[1] - 1, places=9)   # bought at bar 1's open, held to the end
        self.assertAlmostEqual(result["cost_drag"], result["gross_return"] - result["total_return"], places=12)
        self.assertGreater(result["cost_drag"], 0)
        self.assertTrue(result["open_at_end"])

    def test_the_single_flat_cost_still_applies_to_both_sides(self):
        closes = [100.0] * 130
        flat = bt.backtest(bars_from(closes), lambda i: 25, 0, cost=0.001)
        split = bt.backtest(bars_from(closes), lambda i: 25, 0, entry_cost=0.001, exit_cost=0.001)
        self.assertAlmostEqual(flat["total_return"], split["total_return"], places=12)


class MetricTests(unittest.TestCase):
    def setUp(self):
        n = 130
        closes, opens = [100.0] * n, [100.0] * n
        for k in range(12, 62):
            closes[k] = opens[k] = 110.0
        for k in range(62, n):
            closes[k] = opens[k] = 104.5
        self.bars = bars_from(closes, opens)
        self.rule = lambda i: 25 if (10 <= i < 40 or 60 <= i < 90) else -5

    def test_win_rate_profit_factor_expectancy_exposure_and_trade_count(self):
        result = bt.backtest(self.bars, self.rule, 0, cost=0.0)
        self.assertEqual(result["trades"], 2)
        self.assertEqual(result["win_rate"], 0.5)
        self.assertAlmostEqual(result["profit_factor"], 0.10 / 0.05, places=9)       # +10% and -5%
        self.assertAlmostEqual(result["expectancy"], 0.025, places=9)
        self.assertAlmostEqual(result["exposure"], 60 / 129, places=9)
        self.assertAlmostEqual(result["total_return"], 1.10 * (104.5 / 110.0) - 1, places=9)
        self.assertEqual(result["gross_return"], result["total_return"])

    def test_buy_and_hold_comparison_is_the_unfiltered_price_move(self):
        result = bt.backtest(self.bars, self.rule, 0, cost=0.0)
        self.assertAlmostEqual(result["buy_hold_return"], 104.5 / 100.0 - 1, places=12)
        self.assertAlmostEqual(result["buy_hold_cagr"], 1.045 ** (252 / 129) - 1, places=12)

    def test_cagr(self):
        self.assertAlmostEqual(bt._cagr(1.0, 252), 1.0)                  # +100% in exactly one year
        self.assertAlmostEqual(bt._cagr(0.21, 126), 1.21 ** 2 - 1)       # half a year at +21% compounds to +46.41%
        self.assertIsNone(bt._cagr(-1.0, 100))
        self.assertIsNone(bt._cagr(0.1, 0))

    def test_sortino_and_sharpe_on_a_known_curve(self):
        returns = [0.10, -0.05, 0.02, -0.01]
        curve = [1.0]
        for r in returns:
            curve.append(curve[-1] * (1 + r))
        mean = sum(returns) / 4
        downside = math.sqrt((0.05 ** 2 + 0.01 ** 2) / 4)
        self.assertAlmostEqual(bt._sortino(curve), mean / downside * math.sqrt(252), places=9)
        sd = math.sqrt(sum((r - mean) ** 2 for r in returns) / 3)                # sample standard deviation
        self.assertAlmostEqual(bt._sharpe(curve), mean / sd * math.sqrt(252), places=9)
        self.assertIsNone(bt._sortino([1.0, 1.01, 1.02]))                       # no falls: undefined, not infinite

    def test_max_drawdown(self):
        self.assertAlmostEqual(bt._max_drawdown([1.0, 2.0, 1.0, 3.0]), -0.5)
        self.assertEqual(bt._max_drawdown([1.0, 1.1, 1.2]), 0.0)

    def test_too_short_a_history_returns_nothing(self):
        self.assertIsNone(bt.backtest(bars_from([100.0] * 30), lambda i: 25, 0))


class CostModelTests(unittest.TestCase):
    def test_india_sides_follow_the_documented_rates(self):
        s = costs.settings("IN")
        taxable = s["brokerage"] + s["exchange_txn"] + s["sebi"]
        gst = 0.18 * taxable
        entry, exit_ = costs.sides("IN")
        self.assertAlmostEqual(entry, 0.001 + 0.0000297 + 0.000001 + 0.00015 + gst + 0.0005, places=12)
        self.assertAlmostEqual(exit_, 0.001 + 0.0000297 + 0.000001 + 0.00015 + gst + 0.0005, places=12)
        self.assertGreater(entry + exit_, 0.002)           # dearer than the old flat 0.1% a side once taxes are counted

    def test_stamp_duty_is_charged_on_the_buy_side_only_and_dp_on_the_sell_side_only(self):
        a, b = costs.sides("IN", {"IN": {"stamp_buy": 0.01}})
        a0, b0 = costs.sides("IN")
        self.assertGreater(a, a0)
        self.assertEqual(b, b0)
        a, b = costs.sides("IN", {"IN": {"dp_sell": 0.01}})
        self.assertEqual(a, a0)
        self.assertGreater(b, b0)

    def test_gst_applies_to_brokerage_and_exchange_fees_not_to_taxes(self):
        plain, _ = costs.sides("IN")
        with_broker, _ = costs.sides("IN", {"IN": {"brokerage": 0.001}})
        self.assertAlmostEqual(with_broker - plain, 0.001 * 1.18, places=12)

    def test_us_sides(self):
        entry, exit_ = costs.sides("US")
        self.assertAlmostEqual(entry, 0.0005)
        self.assertAlmostEqual(exit_, 0.0005 + 0.00003)

    def test_bad_overrides_are_ignored(self):
        base = costs.sides("IN")
        for bad in ({"IN": {"stt_buy": -1}}, {"IN": {"stt_buy": "x"}}, {"IN": {"nonsense": 5}}, {"IN": {"stt_buy": True}}, {"US": {"slippage": 1}}, None, {}):
            self.assertEqual(costs.sides("IN", bad), base)

    def test_description_lists_both_markets_with_their_round_trip(self):
        d = costs.describe()
        self.assertEqual(set(d), {"IN", "US"})
        self.assertAlmostEqual(d["IN"]["round_trip_pct"], round(sum(costs.sides("IN")) * 100, 4))


class DividendTests(unittest.TestCase):
    """Total-return mode: dividends on the ex-date, for the rule and for buy-and-hold alike."""

    def bars(self, pre=100.0, post=98.0, after=None, n=130, ex=50):
        closes = [pre] * ex + [post] * (n - ex)
        if after is not None:
            for k in range(ex + 1, n):
                closes[k] = after
        return bars_from(closes)

    def test_holding_through_the_ex_date_loses_the_price_drop_in_price_mode_but_not_in_total_mode(self):
        bars = self.bars()
        price_only = bt._simulate(bars, lambda i: 25, 0, 20, 0, 0.0, 0.0)[0][-1]
        total = bt._simulate(bars, lambda i: 25, 0, 20, 0, 0.0, 0.0, {50: 2.0})[0][-1]
        self.assertAlmostEqual(price_only, 0.98, places=12)
        self.assertAlmostEqual(total, 1.0, places=12)                 # (98 + 2) / 100: the dividend made up the drop
        self.assertAlmostEqual(bt.buy_and_hold(bars, 0, {50: 2.0}), 0.0, places=12)
        self.assertAlmostEqual(bt.buy_and_hold(bars, 0), -0.02, places=12)

    def test_buying_on_the_ex_date_does_not_earn_the_dividend(self):
        bars = self.bars()
        rule = lambda i: 25 if i >= 49 else None       # decided at the close of bar 49, so the buy is at bar 50's open: the ex-date
        curve = bt._simulate(bars, rule, 0, 20, 0, 0.0, 0.0, {50: 2.0})[0]
        self.assertAlmostEqual(curve[-1], 1.0, places=12)             # bought at the 98 open of the ex-date and nothing moved: no dividend
        self.assertNotAlmostEqual(curve[-1], 1 + 2 / 98.0, places=3)  # an entitled holder would have had 1.0204

    def test_selling_at_the_ex_date_open_still_earns_the_dividend(self):
        n = 130
        bars = bars_from([100.0] * n)
        rule = lambda i: 25 if i < 49 else -5          # still held at the close of bar 49, sold at bar 50's open
        curve, trades, _, _ = bt._simulate(bars, rule, 0, 20, 0, 0.0, 0.0, {50: 2.0})
        self.assertAlmostEqual(curve[-1], 1.02, places=12)
        self.assertAlmostEqual(trades[0][2], 0.02, places=12)

    def test_reinvested_and_kept_dividends_differ_exactly_as_the_arithmetic_says(self):
        bars = self.bars(after=196.0)
        reinvested = bt._simulate(bars, lambda i: 25, 0, 20, 0, 0.0, 0.0, {50: 2.0}, reinvest=True)[0][-1]
        kept = bt._simulate(bars, lambda i: 25, 0, 20, 0, 0.0, 0.0, {50: 2.0}, reinvest=False)[0][-1]
        # one share bought at 100; at the ex-date it pays 2 while the price is 98
        self.assertAlmostEqual(reinvested, (1 + 2 / 98.0) * 1.96, places=12)      # the 2 buys 2/98 more share, all worth 196 later
        self.assertAlmostEqual(kept, 1.96 + 0.02, places=12)                       # the 2 stays as cash
        self.assertGreater(reinvested, kept)

    def test_a_dividend_when_not_invested_is_ignored(self):
        bars = self.bars()
        curve = bt._simulate(bars, lambda i: -5, 0, 20, 0, 0.0, 0.0, {50: 2.0})[0]
        self.assertAlmostEqual(curve[-1], 1.0, places=12)

    def test_the_total_block_is_added_only_when_dividends_are_given_and_uses_the_same_rule_and_costs(self):
        closes = [100.0 + 0.1 * i for i in range(130)]
        bars = bars_from(closes)
        plain = bt.backtest(bars, lambda i: 25, 0, entry_cost=0.002, exit_cost=0.003)
        self.assertIsNone(plain["total"])
        with_div = bt.backtest(bars, lambda i: 25, 0, entry_cost=0.002, exit_cost=0.003, dividends={40: 1.0, 90: 1.0})
        self.assertEqual(with_div["total_return"], plain["total_return"])         # headline figures stay price-only
        total = with_div["total"]
        self.assertGreater(total["total_return"], plain["total_return"])
        self.assertGreater(total["buy_hold_return"], plain["buy_hold_return"])
        self.assertAlmostEqual(total["buy_hold_return"], bt.buy_and_hold(bars, 0, {40: 1.0, 90: 1.0}), places=12)
        self.assertTrue(total["reinvested"])

    def test_with_no_dividends_the_total_block_equals_the_price_figures(self):
        closes = [100.0 + (i % 9) for i in range(130)]
        res = bt.backtest(bars_from(closes), lambda i: 25 if (i // 15) % 2 == 0 else -5, 0, cost=0.001, dividends={})
        self.assertAlmostEqual(res["total"]["total_return"], res["total_return"], places=12)
        self.assertAlmostEqual(res["total"]["buy_hold_return"], res["buy_hold_return"], places=12)

    def test_dividends_are_placed_on_the_ex_date_bar(self):
        bars = {"t": [1000 + 86400 * i for i in range(10)]}
        self.assertEqual(bt.dividends_by_bar(bars, [(1000 + 86400 * 4, 1.5)]), {4: 1.5})
        self.assertEqual(bt.dividends_by_bar(bars, [(1000 + 86400 * 4 - 3600, 1.5)]), {4: 1.5})          # a time slightly before the bar
        self.assertEqual(bt.dividends_by_bar(bars, [(1000 + 86400 * 4, 1.0), (1000 + 86400 * 4, 0.5)]), {4: 1.5})
        self.assertEqual(bt.dividends_by_bar(bars, [(1000 + 86400 * 40, 1.0)]), {})                        # after the last bar
        self.assertEqual(bt.dividends_by_bar(bars, [(1000 + 86400 * 4, 0.0), (1000 + 86400 * 5, -1.0), (1000 + 86400 * 6, None)]), {})
        self.assertEqual(bt.dividends_by_bar(bars, None), {})


class MembershipTests(unittest.TestCase):
    TABLE = """<table id="constituents"><tbody>
    <tr><th>Symbol</th><th>Security</th><th>Date added</th></tr>
    <tr><td><a>AAPL</a></td><td>Apple</td><td>1982-11-30</td></tr>
    <tr><td><a>BRK.B</a></td><td>Berkshire</td><td>2010-02-16</td></tr>
    <tr><td><a>NEW</a></td><td>Newco</td><td>2025-03-24 <sup>[1]</sup></td></tr>
    <tr><td><a>ODD</a></td><td>Odd</td><td>unknown</td></tr>
    <tr><td>BAD</td></tr></tbody></table>"""

    def test_the_constituents_table_is_parsed_and_odd_rows_skipped(self):
        from engine import membership
        out = membership.parse_sp500_added(self.TABLE)
        self.assertEqual(out, {"AAPL": "1982-11-30", "BRK-B": "2010-02-16"})      # the dotted ticker is written the way Yahoo writes it
        self.assertEqual(membership.parse_sp500_added(""), {})
        self.assertEqual(membership.parse_sp500_added("<table>no id</table>"), {})

    def test_the_first_eligible_bar(self):
        from engine import membership
        dates = ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"]
        self.assertEqual(membership.first_eligible_index(dates, None), 0)
        self.assertEqual(membership.first_eligible_index(dates, "2026-01-05"), 1)
        self.assertEqual(membership.first_eligible_index(dates, "2026-01-04"), 1)      # joined on a non-trading day: the next bar
        self.assertEqual(membership.first_eligible_index(dates, "2025-01-01"), 0)
        self.assertEqual(membership.first_eligible_index(dates, "2027-01-01"), 4)      # not yet a member: no eligible bars

    def test_lookup(self):
        from engine import membership
        data = {"US": {"AAPL": {"from": "1982-11-30"}}, "IN": {}}
        self.assertEqual(membership.lookup(data, "US", "AAPL"), "1982-11-30")
        self.assertIsNone(membership.lookup(data, "US", "XYZ"))
        self.assertIsNone(membership.lookup(data, "IN", "TCS.NS"))
        self.assertIsNone(membership.lookup(None, "US", "AAPL"))

    def test_a_backtest_started_after_the_join_date_never_trades_before_it(self):
        n = 200
        closes = [100.0 + i for i in range(n)]
        rule = lambda i: 25
        full = bt._simulate(bars_from(closes), rule, 0, 20, 0, 0.0, 0.0)[1][0]
        late = bt._simulate(bars_from(closes), rule, 120, 20, 0, 0.0, 0.0)[1][0]
        self.assertEqual((full[0], late[0]), (1, 121))


if __name__ == "__main__":
    unittest.main()
