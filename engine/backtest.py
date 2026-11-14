"""Backtest a score rule against buy-and-hold.

The score at the close of bar j-1 decides the trade at the open of bar j, so the rule
never sees data from the bar it trades on. A cost is charged on every entry and exit.
"""
import math

from .indicators import stdev

MIN_DAYS = 60
FEW_TRADES = 8


def _max_drawdown(curve):
    peak, worst = curve[0], 0.0
    for value in curve:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1)
    return worst


def _sharpe(curve):
    returns = [curve[i] / curve[i - 1] - 1 for i in range(1, len(curve))]
    sd = stdev(returns)
    if not sd:
        return None
    return sum(returns) / len(returns) / sd * math.sqrt(252)


def _segment(curve, k0, buy_hold):
    base = curve[k0]
    rebased = [v / base for v in curve[k0:]]
    return {
        "total_return": rebased[-1] - 1,
        "buy_hold_return": buy_hold,
        "max_drawdown": _max_drawdown(rebased),
        "sharpe": _sharpe(rebased),
    }


def backtest(bars, score_fn, start, enter=20, leave=0, cost=0.001):
    """Long-only simulation. score_fn(i) returns a score or None. Returns None if too short."""
    o, c = bars["o"], bars["c"]
    n = len(c)
    if n - start < MIN_DAYS:
        return None

    equity, mark, entry_price, entry_idx = 1.0, None, None, None
    curve, trades, days_in = [1.0], [], 0
    for j in range(start + 1, n):
        score = score_fn(j - 1)
        if score is not None:
            if mark is None and score >= enter:
                equity /= 1 + cost
                mark, entry_price, entry_idx = o[j], o[j] * (1 + cost), j
            elif mark is not None and score < leave:
                equity *= o[j] / mark * (1 - cost)
                trades.append((entry_idx, j, o[j] * (1 - cost) / entry_price - 1))
                mark = None
        if mark is not None:
            equity *= c[j] / mark
            mark = c[j]
            days_in += 1
        curve.append(equity)
    if mark is not None:
        trades.append((entry_idx, n - 1, c[-1] / entry_price - 1))

    k0 = int(len(curve) * 0.6)
    idx0 = start + k0
    full = _segment(curve, 0, c[-1] / c[start] - 1)
    oos = _segment(curve, k0, c[-1] / c[idx0] - 1)
    oos["trades"] = sum(1 for t in trades if t[1] >= idx0)

    wins = [t[2] for t in trades if t[2] > 0]
    losses = [t[2] for t in trades if t[2] <= 0]
    result = dict(full)
    result.update({
        "tested_days": len(curve) - 1,
        "trades": len(trades),
        "win_rate": len(wins) / len(trades) if trades else None,
        "profit_factor": sum(wins) / -sum(losses) if losses and sum(losses) < 0 else None,
        "expectancy": sum(t[2] for t in trades) / len(trades) if trades else None,
        "exposure": days_in / (len(curve) - 1),
        "oos": oos,
        "beats_buy_hold": full["total_return"] > full["buy_hold_return"],
        "beats_buy_hold_oos": oos["total_return"] > oos["buy_hold_return"],
        "few_trades": len(trades) < FEW_TRADES,
    })
    return result
