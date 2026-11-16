"""Backtest a score rule against buy-and-hold.

How it works, and what it cannot tell you:
  * The score at the close of bar j-1 decides the trade at the OPEN of bar j, so the rule never sees data from the bar it
    trades on. Indicators at bar j-1 only use bars up to j-1.
  * Long only: the rule is either holding the stock or in cash. Cash earns nothing.
  * Each entry and exit is charged its own cost (see costs.py: taxes, fees and an assumed slippage). A gross run with no
    costs is simulated alongside so the cost drag is visible.
  * A position still open at the end is valued at the last close less the exit cost, as if it were sold.
  * Prices are Yahoo's: adjusted for splits but NOT for dividends, so both the rule and buy-and-hold ignore dividend income.
  * The list of stocks is today's list, which leaves out companies that were delisted or fell out of the index
    (survivorship bias), and the rule's constants were chosen by people who had already seen part of this history.
  * The "recent 40%" segment is the last 40% of the tested period run with the same fixed rules. Nothing was fitted to the
    first 60%, but it is not an independent out-of-sample test, because the design of the rules was not blind to it.
"""
import math

from .indicators import stdev

MIN_DAYS = 60
FEW_TRADES = 8
TRADING_DAYS = 252


def _max_drawdown(curve):
    peak, worst = curve[0], 0.0
    for value in curve:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1)
    return worst


def _returns(curve):
    return [curve[i] / curve[i - 1] - 1 for i in range(1, len(curve))]


def _sharpe(curve):
    """Mean daily return over its standard deviation, annualised. The risk-free rate is taken as zero."""
    returns = _returns(curve)
    sd = stdev(returns)
    if not sd:
        return None
    return sum(returns) / len(returns) / sd * math.sqrt(TRADING_DAYS)


def _sortino(curve):
    """Like Sharpe, but only falls count as risk: downside deviation below zero."""
    returns = _returns(curve)
    if not returns:
        return None
    downside = math.sqrt(sum(min(r, 0.0) ** 2 for r in returns) / len(returns))
    if not downside:
        return None
    return sum(returns) / len(returns) / downside * math.sqrt(TRADING_DAYS)


def _cagr(total_return, days):
    """Compound annual growth rate from a total return over `days` trading days."""
    if days <= 0 or total_return <= -1:
        return None
    return (1 + total_return) ** (TRADING_DAYS / days) - 1


def _segment(curve, k0, buy_hold):
    base = curve[k0]
    rebased = [v / base for v in curve[k0:]]
    return {
        "total_return": rebased[-1] - 1,
        "buy_hold_return": buy_hold,
        "max_drawdown": _max_drawdown(rebased),
        "sharpe": _sharpe(rebased),
    }


def dividends_by_bar(bars, dividends, max_gap_days=5):
    """{bar index: cash dividend per share} from Yahoo's (unix time, amount) pairs, placed on the bar of the ex-dividend date.

    Yahoo's prices and dividend amounts are both adjusted for splits and bonus issues, so they are consistent with each
    other. A dividend with no trading bar within a few days of its date is ignored."""
    import bisect
    out = {}
    for ts, amount in dividends or []:
        k = bisect.bisect_left(bars["t"], ts)
        if k < len(bars["t"]) and bars["t"][k] - ts <= max_gap_days * 86400 and amount and amount > 0:
            out[k] = out.get(k, 0.0) + float(amount)
    return out


def _simulate(bars, score_fn, start, enter, leave, entry_cost, exit_cost, dividends=None, reinvest=True, trace=None):
    """One pass of the rule. Returns (equity curve, trades as (entry bar, exit bar, return), days held, ended open).

    dividends is {bar index: cash per share} or None for a price-only run. A holder at the previous close receives the
    dividend on the ex-date, including one who sells at that day's open; someone who buys on the ex-date does not. With
    reinvest the cash buys more shares at the ex-date close at no cost; without it the cash is kept. Dividend tax is ignored.
    trace, if a list, receives one True/False per day: whether the position was held through that day's close."""
    o, c = bars["o"], bars["c"]
    n = len(c)
    cash, units, entry_price, entry_idx, trade_dividends = 1.0, 0.0, None, None, 0.0
    curve, trades, days_in = [1.0], [], 0
    for j in range(start + 1, n):
        held_before = units > 0
        paid = dividends.get(j, 0.0) if dividends and held_before else 0.0
        score = score_fn(j - 1)           # only information up to the previous close
        if score is not None:
            if not held_before and score >= enter:
                units = cash / (o[j] * (1 + entry_cost))
                cash, entry_price, entry_idx, trade_dividends = 0.0, o[j] * (1 + entry_cost), j, 0.0
            elif held_before and score < leave:
                if paid:
                    cash += units * paid
                    trade_dividends += paid
                cash += units * o[j] * (1 - exit_cost)
                trades.append((entry_idx, j, (o[j] * (1 - exit_cost) + trade_dividends) / entry_price - 1))
                units, paid = 0.0, 0.0
        if units > 0:
            if paid:
                trade_dividends += paid
                if reinvest:
                    units += units * paid / c[j]
                else:
                    cash += units * paid
            days_in += 1
        curve.append(cash + units * c[j])
        if trace is not None:
            trace.append(units > 0)
    ended_open = units > 0
    if ended_open:
        curve[-1] = cash + units * c[-1] * (1 - exit_cost)        # valued as if sold at the last close
        trades.append((entry_idx, n - 1, (c[-1] * (1 - exit_cost) + trade_dividends) / entry_price - 1))
    return curve, trades, days_in, ended_open


def buy_and_hold(bars, start, dividends=None):
    """Return of holding from the close of bar `start`; with dividends, they are reinvested at the ex-date close."""
    c = bars["c"]
    growth = 1.0
    for j in range(start + 1, len(c)):
        growth *= (c[j] + (dividends or {}).get(j, 0.0)) / c[j - 1]
    return growth - 1


def backtest(bars, score_fn, start, enter=20, leave=0, cost=0.001, entry_cost=None, exit_cost=None, dividends=None, reinvest=True):
    """Long-only simulation. score_fn(i) returns a score or None. Returns None if too short.

    cost is the charge on each side when entry_cost / exit_cost are not given (the older flat 0.1%).
    dividends ({bar index: cash per share}, see dividends_by_bar) adds a "total" block: the same rule and the same
    buy-and-hold comparison with dividends included. The headline figures stay price-only."""
    c = bars["c"]
    n = len(c)
    if n - start < MIN_DAYS:
        return None
    entry_cost = cost if entry_cost is None else entry_cost
    exit_cost = cost if exit_cost is None else exit_cost

    curve, trades, days_in, ended_open = _simulate(bars, score_fn, start, enter, leave, entry_cost, exit_cost)
    gross_curve = _simulate(bars, score_fn, start, enter, leave, 0.0, 0.0)[0]

    k0 = int(len(curve) * 0.6)
    idx0 = start + k0
    buy_hold = c[-1] / c[start] - 1
    full = _segment(curve, 0, buy_hold)
    oos = _segment(curve, k0, c[-1] / c[idx0] - 1)
    oos["trades"] = sum(1 for t in trades if t[1] >= idx0)

    wins = [t[2] for t in trades if t[2] > 0]
    losses = [t[2] for t in trades if t[2] <= 0]
    days = len(curve) - 1
    result = dict(full)
    result.update({
        "tested_days": days,
        "trades": len(trades),
        "win_rate": len(wins) / len(trades) if trades else None,
        "profit_factor": sum(wins) / -sum(losses) if losses and sum(losses) < 0 else None,
        "expectancy": sum(t[2] for t in trades) / len(trades) if trades else None,
        "exposure": days_in / days,
        "cagr": _cagr(full["total_return"], days),
        "buy_hold_cagr": _cagr(buy_hold, days),
        "sortino": _sortino(curve),
        "gross_return": gross_curve[-1] - 1,
        "cost_drag": gross_curve[-1] - curve[-1],
        "open_at_end": ended_open,
        "oos": oos,
        "total": _total_block(bars, score_fn, start, enter, leave, entry_cost, exit_cost, dividends, reinvest, k0) if dividends is not None else None,
        "beats_buy_hold": full["total_return"] > full["buy_hold_return"],
        "beats_buy_hold_oos": oos["total_return"] > oos["buy_hold_return"],
        "few_trades": len(trades) < FEW_TRADES,
    })
    return result


def _total_block(bars, score_fn, start, enter, leave, entry_cost, exit_cost, dividends, reinvest, k0):
    """The rule and buy-and-hold with dividends included, measured the same way as the price-only figures."""
    curve = _simulate(bars, score_fn, start, enter, leave, entry_cost, exit_cost, dividends, reinvest)[0]
    days = len(curve) - 1
    hold = buy_and_hold(bars, start, dividends)
    idx0 = start + k0
    hold_recent = buy_and_hold(bars, idx0, dividends)
    recent = _segment(curve, k0, hold_recent)
    full = _segment(curve, 0, hold)
    paid = sum(amount for k, amount in dividends.items() if k > start)
    return {"total_return": full["total_return"], "buy_hold_return": hold, "cagr": _cagr(full["total_return"], days),
            "buy_hold_cagr": _cagr(hold, days), "max_drawdown": full["max_drawdown"], "sharpe": full["sharpe"], "sortino": _sortino(curve),
            "beats_buy_hold": full["total_return"] > hold, "recent_total_return": recent["total_return"], "recent_buy_hold_return": hold_recent,
            "beats_buy_hold_recent": recent["total_return"] > hold_recent, "dividends_in_period": paid and round(paid / bars["c"][start], 5),
            "reinvested": reinvest}
