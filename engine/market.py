"""Market-level views worked out from data the build already has: market conditions, breadth and sector moves.

Nothing here is about one stock. Breadth and sectors are computed from the stocks in our own list, not the whole
exchange, and the page says so.
"""
from statistics import mean

from . import indicators

REGIME_LABELS = ((5, "Strongly bullish conditions"), (2, "Bullish conditions"), (-1, "Sideways"), (-4, "Bearish conditions"))
STRONG_BEARISH = "Strongly bearish conditions"
RETURN_THRESHOLD = 2.0     # percent over 20 sessions
ADX_TRENDING = 25
BREADTH_HIGH, BREADTH_LOW = 60, 40
VIX_CALM, VIX_FEAR, VIX_JUMP = 15, 22, 15
MIN_INDEX_BARS = 200
MIN_BREADTH_STOCKS = 10
SECTOR_WINDOWS = (("d1", 1), ("d5", 5), ("d20", 20))


def regime_label(score):
    for floor, label in REGIME_LABELS:
        if score >= floor:
            return label
    return STRONG_BEARISH


def adx(high, low, close, n=14):
    """Wilder's ADX and directional indicators at the last bar: (adx, plus_di, minus_di), or None if too short."""
    if len(close) < 2 * n + 1:
        return None
    tr, plus, minus = [], [], []
    for i in range(1, len(close)):
        up, down = high[i] - high[i - 1], low[i - 1] - low[i]
        plus.append(up if up > down and up > 0 else 0.0)
        minus.append(down if down > up and down > 0 else 0.0)
        tr.append(max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])))
    smooth_tr, smooth_plus, smooth_minus = sum(tr[:n]), sum(plus[:n]), sum(minus[:n])
    dx, pdi, mdi = [], 0.0, 0.0
    for i in range(n, len(tr) + 1):
        if i > n:
            smooth_tr += tr[i - 1] - smooth_tr / n
            smooth_plus += plus[i - 1] - smooth_plus / n
            smooth_minus += minus[i - 1] - smooth_minus / n
        if smooth_tr <= 0:
            return None
        pdi, mdi = 100 * smooth_plus / smooth_tr, 100 * smooth_minus / smooth_tr
        total = pdi + mdi
        dx.append(100 * abs(pdi - mdi) / total if total else 0.0)
    if len(dx) < n:
        return None
    value = mean(dx[:n])
    for x in dx[n:]:
        value = (value * (n - 1) + x) / n
    return value, pdi, mdi


def _check(key, label, score, detail):
    return {"key": key, "label": label, "score": score, "detail": detail}


def _position(name, close, level, pct):
    side = "above" if close > level else "below"
    return f"Close {close:,.0f} is {side} {name} {level:,.0f} ({pct:+.1f}%)"


def regime(index_bars, vix_close, breadth_above50, universe_size):
    """Seven plain checks, each +1, 0 or -1, added up (-7 to +7). Returns None without enough index history."""
    close = (index_bars or {}).get("c") or []
    if len(close) < MIN_INDEX_BARS:
        return None
    last = close[-1]
    e20, e50, e200 = (indicators.ema(close, n)[-1] for n in (20, 50, 200))
    checks = []
    for name, level, key in (("EMA-20", e20, "ema20"), ("EMA-50", e50, "ema50")):
        pct = (last / level - 1) * 100
        checks.append(_check(key, f"Index vs {name}", 1 if last > level else -1, _position(name, last, level, pct)))
    cross = 1 if e50 > e200 else -1
    checks.append(_check("cross", "EMA-50 vs EMA-200", cross,
                         f"EMA-50 {e50:,.0f} is {'above' if cross > 0 else 'below'} EMA-200 {e200:,.0f}"))
    move = (last / close[-21] - 1) * 100
    checks.append(_check("ret20", "20-day return", 1 if move >= RETURN_THRESHOLD else -1 if move <= -RETURN_THRESHOLD else 0,
                         f"{move:+.1f}% over 20 sessions (threshold {RETURN_THRESHOLD:g}%)"))
    trend = adx(index_bars["h"], index_bars["l"], close)
    if trend is None:
        checks.append(_check("adx", "Trend strength (ADX)", 0, "Not enough history"))
    else:
        value, plus, minus = trend
        score = 0 if value < ADX_TRENDING else 1 if plus > minus else -1
        word = "trending" if value >= ADX_TRENDING else "weak trend"
        checks.append(_check("adx", "Trend strength (ADX)", score,
                             f"ADX {value:.0f} ({word}); +DI {plus:.0f} against -DI {minus:.0f}"))
    if breadth_above50 is None:
        checks.append(_check("breadth", "Breadth (% above 50-day average)", 0, "Not enough stocks to measure"))
    else:
        score = 1 if breadth_above50 >= BREADTH_HIGH else -1 if breadth_above50 <= BREADTH_LOW else 0
        checks.append(_check("breadth", "Breadth (% above 50-day average)", score,
                             f"{breadth_above50:.0f}% of {universe_size} stocks in our list are above their 50-day average"))
    if vix_close and len(vix_close) > 6:
        level, change = vix_close[-1], (vix_close[-1] / vix_close[-6] - 1) * 100
        score = -1 if level > VIX_FEAR or change >= VIX_JUMP else 1 if level < VIX_CALM and change <= 10 else 0
        checks.append(_check("vix", "Volatility index", score, f"VIX {level:.2f} ({change:+.1f}% over 5 sessions)"))
    else:
        checks.append(_check("vix", "Volatility index", 0, "Volatility index unavailable"))
    total = sum(c["score"] for c in checks)
    return {"score": total, "label": regime_label(total), "checks": checks, "as_of": (index_bars.get("d") or [None])[-1]}


def _group_by_market(bars_by_symbol, info):
    groups = {}
    for symbol, bars in bars_by_symbol.items():
        market = (info.get(symbol) or {}).get("market")
        if market and bars and len(bars.get("c") or []) > 20:
            groups.setdefault(market, {})[symbol] = bars
    return groups


def _latest_session(group):
    dates = [b["d"][-1] for b in group.values()]
    return max(dates)


def breadth(group):
    """Advancers, decliners, and the share of stocks above their averages, on the latest session most stocks share."""
    if not group:
        return None
    session = _latest_session(group)
    current = {s: b["c"] for s, b in group.items() if b["d"][-1] == session}
    if len(current) < MIN_BREADTH_STOCKS:
        return None
    up = sum(1 for c in current.values() if c[-1] > c[-2])
    down = sum(1 for c in current.values() if c[-1] < c[-2])
    out = {"n": len(current), "as_of": session, "advancing": up, "declining": down, "unchanged": len(current) - up - down}
    for window in (20, 50, 200):
        eligible = [c for c in current.values() if len(c) >= window]
        out[f"above{window}_pct"] = round(100 * sum(1 for c in eligible if c[-1] > mean(c[-window:])) / len(eligible), 1) if eligible else None
    out["new_highs"] = sum(1 for c in current.values() if len(c) > 21 and c[-1] > max(c[-21:-1]))
    out["new_lows"] = sum(1 for c in current.values() if len(c) > 21 and c[-1] < min(c[-21:-1]))
    return out


def sector_moves(group, info):
    """Average move per sector over 1, 5 and 20 sessions (every stock counts the same), strongest day first."""
    session = _latest_session(group) if group else None
    sectors = {}
    for symbol, bars in group.items():
        if bars["d"][-1] != session:
            continue
        close = bars["c"]
        sector = (info.get(symbol) or {}).get("sector") or "Other"
        moves = sectors.setdefault(sector, {key: [] for key, _ in SECTOR_WINDOWS})
        for key, days in SECTOR_WINDOWS:
            if len(close) > days:
                moves[key].append((close[-1] / close[-1 - days] - 1) * 100)
    rows = []
    for sector, moves in sectors.items():
        row = {"sector": sector, "n": max((len(v) for v in moves.values()), default=0)}
        for key, _ in SECTOR_WINDOWS:
            row[key] = round(mean(moves[key]), 2) if moves[key] else None
        rows.append(row)
    return sorted(rows, key=lambda r: (r["d1"] is None, -(r["d1"] or 0), r["sector"]))


def analyse(regime_config, raw_index_bars, stock_bars, info):
    """{market: {"regime", "breadth", "sectors", "universe"}} for every market in regime_config.

    raw_index_bars: {symbol: bars} for the index and volatility symbols. stock_bars: {symbol: bars}.
    info: {symbol: {"market", "sector"}}.
    """
    groups = _group_by_market(stock_bars, info)
    out = {}
    for market, symbols in (regime_config or {}).items():
        group = groups.get(market, {})
        measured = breadth(group)
        vix = (raw_index_bars.get(symbols.get("vix")) or {}).get("c")
        out[market] = {
            "name": symbols.get("name"),
            "regime": regime(raw_index_bars.get(symbols.get("index")), vix, measured and measured["above50_pct"], len(group)),
            "breadth": measured,
            "sectors": sector_moves(group, info) if measured else [],
            "universe": len(group),
        }
    return out
