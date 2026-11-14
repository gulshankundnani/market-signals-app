"""Risk statistics from daily closes."""
import math

from .indicators import pct_returns, stdev

TRADING_DAYS = 252


def percentile(sorted_values, p):
    """Linear-interpolated percentile, p between 0 and 1."""
    if not sorted_values:
        return None
    pos = p * (len(sorted_values) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def max_drawdown(closes):
    peak, worst = closes[0], 0.0
    for value in closes:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1)
    return worst


def beta(stock_by_date, bench_by_date):
    """Beta of daily returns on the dates both series share."""
    dates = sorted(set(stock_by_date) & set(bench_by_date))[-(TRADING_DAYS + 1):]
    if len(dates) < 60:
        return None
    s = pct_returns([stock_by_date[d] for d in dates])
    b = pct_returns([bench_by_date[d] for d in dates])
    var_b = stdev(b)
    if not var_b:
        return None
    mean_s, mean_b = sum(s) / len(s), sum(b) / len(b)
    cov = sum((x - mean_s) * (y - mean_b) for x, y in zip(s, b)) / (len(s) - 1)
    return cov / var_b ** 2


def risk_stats(closes, stock_by_date=None, bench_by_date=None):
    """One-year risk figures. Losses are returned as positive fractions (VaR, shortfall)."""
    recent = closes[-(TRADING_DAYS + 1):]
    if len(recent) < 60:
        return None
    returns = pct_returns(recent)
    ordered = sorted(returns)
    cutoff = percentile(ordered, 0.05)
    tail = [r for r in ordered if r <= cutoff]
    return {
        "annual_vol": stdev(returns) * math.sqrt(TRADING_DAYS),
        "var95": -cutoff,
        "expected_shortfall95": -sum(tail) / len(tail),
        "max_drawdown": max_drawdown(recent),
        "beta": beta(stock_by_date, bench_by_date) if stock_by_date and bench_by_date else None,
    }
