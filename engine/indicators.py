"""Technical indicators in plain Python.

Every series keeps the input length. None marks "not enough data yet".
"""
import math


def sma(values, n):
    out = [None] * len(values)
    if n <= 0 or len(values) < n:
        return out
    window = sum(values[:n])
    out[n - 1] = window / n
    for i in range(n, len(values)):
        window += values[i] - values[i - n]
        out[i] = window / n
    return out


def ema(values, n):
    out = [None] * len(values)
    if n <= 0 or len(values) < n:
        return out
    k = 2 / (n + 1)
    prev = sum(values[:n]) / n
    out[n - 1] = prev
    for i in range(n, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def _rsi_value(avg_gain, avg_loss):
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def rsi(values, n=14):
    """Wilder's RSI."""
    out = [None] * len(values)
    if len(values) <= n:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        change = values[i] - values[i - 1]
        gains += max(change, 0)
        losses += max(-change, 0)
    avg_gain, avg_loss = gains / n, losses / n
    out[n] = _rsi_value(avg_gain, avg_loss)
    for i in range(n + 1, len(values)):
        change = values[i] - values[i - 1]
        avg_gain = (avg_gain * (n - 1) + max(change, 0)) / n
        avg_loss = (avg_loss * (n - 1) + max(-change, 0)) / n
        out[i] = _rsi_value(avg_gain, avg_loss)
    return out


def macd(values, fast=12, slow=26, signal=9):
    """Returns (macd line, signal line, histogram)."""
    fast_ema, slow_ema = ema(values, fast), ema(values, slow)
    line = [None if a is None or b is None else a - b for a, b in zip(fast_ema, slow_ema)]
    signal_line = [None] * len(values)
    hist = [None] * len(values)
    first = next((i for i, v in enumerate(line) if v is not None), None)
    if first is not None:
        for offset, value in enumerate(ema(line[first:], signal)):
            if value is not None:
                signal_line[first + offset] = value
                hist[first + offset] = line[first + offset] - value
    return line, signal_line, hist


def atr(high, low, close, n=14):
    """Wilder's average true range."""
    out = [None] * len(close)
    if len(close) < n:
        return out
    true_range = [high[0] - low[0]]
    for i in range(1, len(close)):
        true_range.append(max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])))
    prev = sum(true_range[:n]) / n
    out[n - 1] = prev
    for i in range(n, len(close)):
        prev = (prev * (n - 1) + true_range[i]) / n
        out[i] = prev
    return out


def pct_returns(values):
    return [values[i] / values[i - 1] - 1 for i in range(1, len(values))]


def stdev(xs):
    if len(xs) < 2:
        return None
    mean = sum(xs) / len(xs)
    return math.sqrt(sum((x - mean) ** 2 for x in xs) / (len(xs) - 1))


def rolling_vol(values, n=30):
    """Annualised volatility of the last n daily returns."""
    out = [None] * len(values)
    returns = pct_returns(values)
    for i in range(n, len(values)):
        out[i] = stdev(returns[i - n:i]) * math.sqrt(252)
    return out


def rolling_vwap(high, low, close, volume, n=20):
    """Volume-weighted average price over the last n daily bars."""
    out = [None] * len(close)
    typical = [(h + l + c) / 3 for h, l, c in zip(high, low, close)]
    for i in range(n - 1, len(close)):
        vol = sum(volume[i - n + 1:i + 1])
        if vol > 0:
            out[i] = sum(t * v for t, v in zip(typical[i - n + 1:i + 1], volume[i - n + 1:i + 1])) / vol
    return out


def chaikin_money_flow(high, low, close, volume, n=20):
    """Chaikin Money Flow, -1 (selling pressure) to +1 (buying pressure).

    Each bar's volume is weighted by where the close sits inside the day's range.
    A volume-based proxy: free data has no real order book.
    """
    out = [None] * len(close)
    flow = []
    for h, l, c, v in zip(high, low, close, volume):
        multiplier = 0.0 if h == l else ((c - l) - (h - c)) / (h - l)
        flow.append(multiplier * v)
    for i in range(n - 1, len(close)):
        total_volume = sum(volume[i - n + 1:i + 1])
        if total_volume > 0:
            out[i] = sum(flow[i - n + 1:i + 1]) / total_volume
    return out


def up_volume_share(close, volume, n=20):
    """Share of the last n days' volume that traded on days the price rose (0..1)."""
    out = [None] * len(close)
    for i in range(n, len(close)):
        window = range(i - n + 1, i + 1)
        total = sum(volume[j] for j in window)
        if total > 0:
            out[i] = sum(volume[j] for j in window if close[j] > close[j - 1]) / total
    return out


def prior_max(values, i, n):
    """Highest value in the n bars before index i (excludes i)."""
    return max(values[max(0, i - n):i]) if i > 0 else None


def prior_min(values, i, n):
    return min(values[max(0, i - n):i]) if i > 0 else None


def fibonacci_levels(high, low, lookback=120):
    start = max(0, len(high) - lookback)
    highs, lows = high[start:], low[start:]
    top, bottom = max(highs), min(lows)
    if top <= bottom:
        return None
    uptrend = highs.index(top) > lows.index(bottom)
    span = top - bottom
    ratios = [0.236, 0.382, 0.5, 0.618, 0.786]
    levels = {str(r): (top - r * span) if uptrend else (bottom + r * span) for r in ratios}
    return {"high": top, "low": bottom, "uptrend": uptrend, "levels": levels}


def support_resistance(high, low, close, k=5, lookback=250, tolerance=0.015):
    """Nearest two support and resistance zones from clustered swing pivots."""
    n = len(close)
    pivots = []
    for i in range(max(k, n - lookback), n - k):
        if high[i] == max(high[i - k:i + k + 1]):
            pivots.append(high[i])
        if low[i] == min(low[i - k:i + k + 1]):
            pivots.append(low[i])
    clusters = []
    for pivot in sorted(pivots):
        if clusters and pivot / clusters[-1][-1] - 1 <= tolerance:
            clusters[-1].append(pivot)
        else:
            clusters.append([pivot])
    zones = [{"level": sum(c) / len(c), "touches": len(c)} for c in clusters]
    price = close[-1]
    supports = sorted((z for z in zones if z["level"] < price), key=lambda z: -z["level"])[:2]
    resistances = sorted((z for z in zones if z["level"] > price), key=lambda z: z["level"])[:2]
    return {"support": supports, "resistance": resistances}
