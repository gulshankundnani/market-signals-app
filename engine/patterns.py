"""Chart pattern and candlestick detection on the latest bars."""
from . import indicators as ta


def _pattern(name, bias, note):
    return {"name": name, "bias": bias, "note": note}


def _candles(bars, ind):
    o, h, l, c = bars["o"], bars["h"], bars["l"], bars["c"]
    i = len(c) - 1
    found = []
    rng = h[i] - l[i]
    if rng <= 0:
        return found
    body = abs(c[i] - o[i])
    upper = h[i] - max(o[i], c[i])
    lower = min(o[i], c[i]) - l[i]
    sma20_prev = ind["sma20"][i - 1] if i > 0 else None
    after_fall = sma20_prev is not None and c[i - 1] < sma20_prev
    after_rise = sma20_prev is not None and c[i - 1] > sma20_prev

    if body <= 0.1 * rng:
        found.append(_pattern("Doji", "neutral", "Open and close nearly equal, the market is undecided"))
    elif lower >= 2 * body and upper <= 0.3 * body + 0.05 * rng and body <= 0.4 * rng and after_fall:
        found.append(_pattern("Hammer", "bullish", "Long lower wick after a fall, buyers stepped in"))
    elif upper >= 2 * body and lower <= 0.3 * body + 0.05 * rng and body <= 0.4 * rng and after_rise:
        found.append(_pattern("Shooting star", "bearish", "Long upper wick after a rise, sellers pushed back"))

    if i >= 1:
        prev_body = abs(c[i - 1] - o[i - 1])
        if c[i - 1] < o[i - 1] and c[i] > o[i] and o[i] <= c[i - 1] and c[i] >= o[i - 1] and body > prev_body:
            found.append(_pattern("Bullish engulfing", "bullish", "A green candle fully covers the previous red one"))
        if c[i - 1] > o[i - 1] and c[i] < o[i] and o[i] >= c[i - 1] and c[i] <= o[i - 1] and body > prev_body:
            found.append(_pattern("Bearish engulfing", "bearish", "A red candle fully covers the previous green one"))
    return found


def _crosses(ind, i, window=10):
    found = []
    sma50, sma200 = ind["sma50"], ind["sma200"]
    for j in range(max(1, i - window + 1), i + 1):
        if None in (sma50[j], sma200[j], sma50[j - 1], sma200[j - 1]):
            continue
        if sma50[j - 1] <= sma200[j - 1] and sma50[j] > sma200[j]:
            found.append(_pattern("Golden cross", "bullish", "50-day average crossed above the 200-day in the last 10 days"))
        if sma50[j - 1] >= sma200[j - 1] and sma50[j] < sma200[j]:
            found.append(_pattern("Death cross", "bearish", "50-day average crossed below the 200-day in the last 10 days"))
    hist = ind["macd_hist"]
    for j in range(max(1, i - 2), i + 1):
        if hist[j] is None or hist[j - 1] is None:
            continue
        if hist[j - 1] <= 0 < hist[j]:
            found.append(_pattern("MACD bullish cross", "bullish", "MACD crossed above its signal line in the last 3 days"))
        if hist[j - 1] >= 0 > hist[j]:
            found.append(_pattern("MACD bearish cross", "bearish", "MACD crossed below its signal line in the last 3 days"))
    return found


def _breakouts(bars, ind):
    c, h, l, v = bars["c"], bars["h"], bars["l"], bars["v"]
    i = len(c) - 1
    found = []
    if i < 21:
        return found
    avg_vol = ind["avg_volume20"][i]
    heavy = bool(avg_vol) and v[i] >= 1.3 * avg_vol
    top, bottom = ta.prior_max(h, i, 20), ta.prior_min(l, i, 20)
    if c[i] > top:
        found.append(_pattern("20-day breakout", "bullish", "Closed above the 20-day high" + (" on heavy volume" if heavy else " on normal volume, less reliable")))
    elif c[i] < bottom:
        found.append(_pattern("20-day breakdown", "bearish", "Closed below the 20-day low" + (" on heavy volume" if heavy else " on normal volume, less reliable")))
    hi, lo = ind["hi252"][i], ind["lo252"][i]
    if hi and c[i] >= hi * 0.97:
        found.append(_pattern("Near 52-week high", "bullish", "Within 3% of the 52-week high"))
    if lo and c[i] <= lo * 1.03:
        found.append(_pattern("Near 52-week low", "bearish", "Within 3% of the 52-week low"))
    return found


def detect_patterns(bars, ind):
    if len(bars["c"]) < 30:
        return []
    return _candles(bars, ind) + _crosses(ind, len(bars["c"]) - 1) + _breakouts(bars, ind)
