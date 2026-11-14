"""Rule-based Buy/Hold/Sell scores.

Each component scores between -1 (bearish) and +1 (bullish). The final score is the
weighted average of the components that have data, scaled to -100..+100.
The rules are fixed. Nothing is fitted to the history, so the backtest is honest.
"""
from . import indicators as ta

SHORT_WEIGHTS = {"trend": 30, "macd": 25, "rsi": 20, "volume": 5, "pressure": 15, "news": 15}
LONG_WEIGHTS = {"trend200": 35, "cross": 25, "momentum": 20, "position": 10, "volatility": 10}

BUY, SELL = 20, -20
STRONG = 50


def clamp(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def label(score):
    if score is None:
        return "N/A"
    if score >= STRONG:
        return "Strong Buy"
    if score >= BUY:
        return "Buy"
    if score <= -STRONG:
        return "Strong Sell"
    if score <= SELL:
        return "Sell"
    return "Hold"


def build_indicators(bars):
    close, high, low, volume = bars["c"], bars["h"], bars["l"], bars["v"]
    macd_line, macd_signal, macd_hist = ta.macd(close)
    n = len(close)
    momentum = [None] * n
    for i in range(126, n):
        momentum[i] = close[i] / close[i - 126] - 1
    hi252 = [None] * n
    lo252 = [None] * n
    for i in range(251, n):
        hi252[i] = max(high[i - 251:i + 1])
        lo252[i] = min(low[i - 251:i + 1])
    return {
        "sma20": ta.sma(close, 20),
        "sma50": ta.sma(close, 50),
        "sma200": ta.sma(close, 200),
        "rsi": ta.rsi(close, 14),
        "macd": macd_line,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,
        "atr": ta.atr(high, low, close, 14),
        "vol30": ta.rolling_vol(close, 30),
        "vwap20": ta.rolling_vwap(high, low, close, volume, 20),
        "avg_volume20": ta.sma(volume, 20),
        "cmf20": ta.chaikin_money_flow(high, low, close, volume, 20),
        "upvol20": ta.up_volume_share(close, volume, 20),
        "momentum126": momentum,
        "hi252": hi252,
        "lo252": lo252,
        "bars": bars,
    }


def _combine(components, weights, min_weight_share=0.5):
    """components: {name: (value, text) or None}. Returns (score, reasons) or (None, [])."""
    used = {k: v for k, v in components.items() if v is not None}
    total = sum(weights[k] for k in used)
    if total < min_weight_share * sum(weights.values()):
        return None, []
    score = sum(weights[k] * used[k][0] for k in used) / total * 100
    reasons = []
    for k, (value, text) in used.items():
        tone = "pos" if value > 0.15 else "neg" if value < -0.15 else "neutral"
        reasons.append({"text": text, "tone": tone})
    return round(score), reasons


def buy_sell_pressure(ind, i):
    """Volume-based buying or selling pressure. Returns None when volume data is missing."""
    cmf, share = ind["cmf20"][i], ind["upvol20"][i]
    if cmf is None or share is None:
        return None
    value = (clamp(cmf / 0.25) + clamp((share - 0.5) / 0.2)) / 2
    name = "Buying pressure" if value > 0.15 else "Selling pressure" if value < -0.15 else "Balanced"
    return {"value": value, "label": name, "money_flow": cmf, "up_volume_share": share}


def short_score(ind, i, news=None):
    """Swing view (days to weeks). news is a -1..1 sentiment score or None."""
    bars = ind["bars"]
    close, volume = bars["c"], bars["v"]
    c = {}

    sma20, sma50 = ind["sma20"][i], ind["sma50"][i]
    if sma20 is not None and sma50 is not None:
        value = (0.5 if close[i] > sma20 else -0.5) + (0.5 if sma20 > sma50 else -0.5)
        parts = []
        parts.append("price above 20-day average" if close[i] > sma20 else "price below 20-day average")
        parts.append("20-day above 50-day" if sma20 > sma50 else "20-day below 50-day")
        c["trend"] = (value, "Trend: " + ", ".join(parts))

    hist, prev_hist = ind["macd_hist"][i], ind["macd_hist"][i - 1] if i > 0 else None
    if hist is not None and prev_hist is not None:
        rising = hist > prev_hist
        if hist > 0:
            value = 1.0 if rising else 0.5
            text = "MACD positive and " + ("rising" if rising else "fading")
        else:
            value = -0.5 if rising else -1.0
            text = "MACD negative and " + ("recovering" if rising else "falling")
        c["macd"] = (value, "Momentum: " + text)

    rsi = ind["rsi"][i]
    if rsi is not None:
        if rsi >= 70:
            value, text = -0.5, f"RSI {rsi:.0f} is overbought, pullback risk"
        elif rsi <= 30:
            value, text = 0.5, f"RSI {rsi:.0f} is oversold, bounce possible"
        else:
            value, text = (rsi - 50) / 40, f"RSI {rsi:.0f} is in the normal range"
        c["rsi"] = (value, text)

    avg_volume = ind["avg_volume20"][i]
    if avg_volume and i > 0:
        spike = volume[i] > 1.5 * avg_volume
        if spike and close[i] > close[i - 1]:
            c["volume"] = (1.0, "Volume spike on a rising day")
        elif spike and close[i] < close[i - 1]:
            c["volume"] = (-1.0, "Volume spike on a falling day")
        else:
            c["volume"] = (0.0, "Volume is normal")

    pressure = buy_sell_pressure(ind, i)
    if pressure is not None:
        c["pressure"] = (pressure["value"], f"Volume pressure: {pressure['label'].lower()} over 20 days")

    if news is not None:
        text = "News tone is positive" if news > 0.15 else "News tone is negative" if news < -0.15 else "News tone is neutral"
        c["news"] = (clamp(news), text)

    return _combine(c, SHORT_WEIGHTS, min_weight_share=0.65)


def long_score(ind, i):
    """Investor view (months to years)."""
    close = ind["bars"]["c"]
    c = {}

    sma50, sma200 = ind["sma50"][i], ind["sma200"][i]
    if sma200 is not None:
        above = close[i] > sma200
        c["trend200"] = (1.0 if above else -1.0, "Price is " + ("above" if above else "below") + " the 200-day average")
        if sma50 is not None:
            golden = sma50 > sma200
            c["cross"] = (1.0 if golden else -1.0, "50-day is " + ("above" if golden else "below") + " the 200-day (" + ("golden" if golden else "death") + " cross zone)")

    mom = ind["momentum126"][i]
    if mom is not None:
        c["momentum"] = (clamp(mom / 0.3), f"6-month return is {mom * 100:+.1f}%")

    hi, lo = ind["hi252"][i], ind["lo252"][i]
    if hi is not None and hi > lo:
        position = (close[i] - lo) / (hi - lo)
        c["position"] = (clamp(2 * position - 1), f"Trading at {position * 100:.0f}% of its 52-week range")

    vol = ind["vol30"][i]
    if vol is not None:
        value = 0.5 if vol <= 0.20 else 0.0 if vol <= 0.40 else -0.5
        c["volatility"] = (value, f"Volatility {vol * 100:.0f}% a year")

    return _combine(c, LONG_WEIGHTS)


def stop_loss(price, atr_value, multiple=2.0):
    """Protective stop below price. Returns None without ATR data."""
    if not atr_value:
        return None
    stop = price - multiple * atr_value
    if stop <= 0:
        return None
    return {"price": stop, "pct": (stop / price - 1) * 100, "multiple": multiple}
