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
    heavy = bool(avg_vol) and v[i] is not None and v[i] >= 1.3 * avg_vol
    volume_note = " on normal volume, less reliable" if v[i] is not None and avg_vol else " (volume not reported, so its strength is unknown)"
    top, bottom = ta.prior_max(h, i, 20), ta.prior_min(l, i, 20)
    if c[i] > top:
        found.append(_pattern("20-day breakout", "bullish", "Closed above the 20-day high" + (" on heavy volume" if heavy else volume_note)))
    elif c[i] < bottom:
        found.append(_pattern("20-day breakdown", "bearish", "Closed below the 20-day low" + (" on heavy volume" if heavy else volume_note)))
    hi, lo = ind["hi252"][i], ind["lo252"][i]
    if hi and c[i] >= hi * 0.97:
        found.append(_pattern("Near 52-week high", "bullish", "Within 3% of the 52-week high"))
    if lo and c[i] <= lo * 1.03:
        found.append(_pattern("Near 52-week low", "bearish", "Within 3% of the 52-week low"))
    return found


GAP_PCT = 0.01             # an opening gap past the previous day's range of at least 1%
TIGHT_RANGE = 0.10         # the 20-day high-to-low range is under 10% of the price (on 70 real charts the median range was 15% and none was under 6%)
SURGE_RATIO = 2.0          # volume at least twice the 20-day average
SURGE_MOVE = 0.02          # on a move of at least 2%


def _three_candles(bars):
    """Morning/evening star, three white soldiers/black crows, harami, inside day: shapes over the last 2 or 3 candles."""
    o, h, l, c = bars["o"], bars["h"], bars["l"], bars["c"]
    i = len(c) - 1
    found = []
    if i < 3:
        return found
    body = lambda k: abs(c[k] - o[k])
    rng = lambda k: h[k] - l[k]
    up, down = (lambda k: c[k] > o[k]), (lambda k: c[k] < o[k])
    mid = lambda k: (o[k] + c[k]) / 2
    big = lambda k: rng(k) > 0 and body(k) >= 0.5 * rng(k)
    small = lambda k: rng(k) > 0 and body(k) <= 0.3 * rng(k)
    a, b, d = i - 2, i - 1, i
    if down(a) and big(a) and small(b) and max(o[b], c[b]) < c[a] and up(d) and c[d] > mid(a):
        found.append(_pattern("Morning star", "bullish", "Big red candle, a small one, then a green candle back above the middle of the first"))
    if up(a) and big(a) and small(b) and min(o[b], c[b]) > c[a] and down(d) and c[d] < mid(a):
        found.append(_pattern("Evening star", "bearish", "Big green candle, a small one, then a red candle back below the middle of the first"))
    if all(up(k) and big(k) for k in (a, b, d)) and c[a] < c[b] < c[d] and o[b] > o[a] and o[d] > o[b]:
        found.append(_pattern("Three white soldiers", "bullish", "Three green candles in a row, each closing higher"))
    if all(down(k) and big(k) for k in (a, b, d)) and c[a] > c[b] > c[d] and o[b] < o[a] and o[d] < o[b]:
        found.append(_pattern("Three black crows", "bearish", "Three red candles in a row, each closing lower"))
    inside = h[d] < h[b] and l[d] > l[b]
    if inside and down(b) and big(b) and up(d):
        found.append(_pattern("Bullish harami", "bullish", "A small green candle inside the previous big red one"))
    elif inside and up(b) and big(b) and down(d):
        found.append(_pattern("Bearish harami", "bearish", "A small red candle inside the previous big green one"))
    elif inside:
        found.append(_pattern("Inside day", "neutral", "The day stayed within the previous day's high and low"))
    return found


def _structure(bars, ind):
    """Gaps, a tight range, a run of higher or lower highs and lows, and a volume surge."""
    o, h, l, c, v = bars["o"], bars["h"], bars["l"], bars["c"], bars["v"]
    i = len(c) - 1
    found = []
    if i < 21:
        return found
    if l[i] > h[i - 1] and o[i] >= h[i - 1] * (1 + GAP_PCT):
        found.append(_pattern("Gap up", "bullish", "Opened above the previous day's high by at least 1%"))
    elif h[i] < l[i - 1] and o[i] <= l[i - 1] * (1 - GAP_PCT):
        found.append(_pattern("Gap down", "bearish", "Opened below the previous day's low by at least 1%"))
    top, bottom = max(h[i - 19:i + 1]), min(l[i - 19:i + 1])
    if c[i] > 0 and (top - bottom) / c[i] <= TIGHT_RANGE:
        found.append(_pattern("Tight range", "neutral", "The last 20 days stayed inside a range under 10% wide"))
    last = range(i - 3, i + 1)
    if all(h[k] > h[k - 1] and l[k] > l[k - 1] for k in last):
        found.append(_pattern("Higher highs and lows", "bullish", "Each of the last 4 days made a higher high and a higher low"))
    elif all(h[k] < h[k - 1] and l[k] < l[k - 1] for k in last):
        found.append(_pattern("Lower highs and lows", "bearish", "Each of the last 4 days made a lower high and a lower low"))
    avg = ind["avg_volume20"][i]
    if avg and v[i] is not None and v[i] >= SURGE_RATIO * avg and c[i - 1] > 0 and abs(c[i] / c[i - 1] - 1) >= SURGE_MOVE:
        up = c[i] > c[i - 1]
        found.append(_pattern("Volume surge", "bullish" if up else "bearish", f"Volume {v[i] / avg:.1f}x the 20-day average on a {'rise' if up else 'fall'} of {abs(c[i] / c[i - 1] - 1) * 100:.1f}%"))
    return found


def detect_patterns(bars, ind):
    if len(bars["c"]) < 30:
        return []
    return (_candles(bars, ind) + _three_candles(bars) + _crosses(ind, len(bars["c"]) - 1) + _breakouts(bars, ind) + _structure(bars, ind))
