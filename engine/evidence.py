"""How well the two fixed rules have actually worked, shown next to every reading so a reading is never read as a promise.

Two levels, both from the backtests the engine already runs (next-open execution, assumed costs, dividends included for the rule and for buy-and-hold):

  * a stock's own result: the rule's total return over the test against buy-and-hold on the same stock and days;
  * the whole list: on how many tested stocks the rule beat buy-and-hold, and the median return of each.

Nothing here changes a reading or a threshold. It is evidence shown beside the reading. The test uses today's stock list, so it is NOT free of survivorship bias.
"""
from statistics import median

# A stock whose average daily turnover is below this is thinly traded: a few trades move its price, so its indicators say little. Read as "unreliable", not "avoid".
THIN_TURNOVER = {"IN": 1_000_000, "US": 100_000}
MIN_BARS_LONG = 250
HORIZONS = ("short", "long")


def stock_evidence(backtest):
    """{"short": {...}, "long": {...}}: the rule's result on this stock for each horizon, or {} when it could not be tested."""
    out = {}
    for horizon in HORIZONS:
        block = (backtest or {}).get(horizon)
        total = (block or {}).get("total") if isinstance(block, dict) else None
        if not isinstance(total, dict) or total.get("total_return") is None or total.get("buy_hold_return") is None:
            continue
        out[horizon] = {"rule": total["total_return"], "hold": total["buy_hold_return"], "beat": bool(total.get("beats_buy_hold")),
                        "years": round((block.get("tested_days") or 0) / 252, 1), "trades": block.get("trades"), "few_trades": bool(block.get("few_trades"))}
    return out


def data_flags(price, avg_volume, market, bars):
    """Short names for what limits how far a reading can be trusted: "thin" (small turnover) and "short_history" (under a year of prices)."""
    flags = []
    if price and avg_volume is not None and price * avg_volume < THIN_TURNOVER.get(market, 0):
        flags.append("thin")
    if bars is not None and bars < MIN_BARS_LONG:
        flags.append("short_history")
    return flags


def summarise(records):
    """{market: {horizon: {"n", "beat", "beat_pct", "median_rule", "median_hold"}}} over the records' own results."""
    out = {}
    for market in sorted({r["market"] for r in records}):
        out[market] = {}
        for horizon in HORIZONS:
            rows = [r["evidence"][horizon] for r in records if r["market"] == market and horizon in (r.get("evidence") or {})]
            if not rows:
                continue
            beat = sum(1 for x in rows if x["beat"])
            out[market][horizon] = {"n": len(rows), "beat": beat, "beat_pct": round(beat / len(rows) * 100),
                                    "median_rule": round(median(x["rule"] for x in rows), 3), "median_hold": round(median(x["hold"] for x in rows), 3),
                                    "median_years": round(median(x["years"] for x in rows), 1)}
    return out


def evidence_text(evidence, horizon):
    """One plain sentence for an alert: the rule against buy-and-hold on this stock, or None."""
    x = (evidence or {}).get(horizon)
    if not x:
        return None
    verdict = "beat" if x["beat"] else "lagged"
    return f"Past test on this stock ({x['years']:g} yrs, after costs): rule {x['rule'] * 100:+.0f}% vs buy-and-hold {x['hold'] * 100:+.0f}%, so it {verdict} holding"
