"""Risk-adjusted report of the two fixed rules against buy-and-hold, by market and by market regime.

    python tools/strategy_report.py                 # whole universe, writes docs/strategy-report.md and .json
    python tools/strategy_report.py --limit 20      # a quick trial

For every stock with at least 260 daily bars and each rule (weeks view, months-to-years view) it runs the same backtest the app
publishes (next-open trades, assumed costs per market, dividends included for both the rule and buy-and-hold), then measures:
total return, CAGR, maximum drawdown, downside deviation, Sharpe, Sortino, Calmar, exposure, average holding period and trades.

Regimes come from the market index (Nifty 50 for India, S&P 500 for the US), judged from the PREVIOUS close so nothing looks
ahead: bull = index above a rising 200-day average, bear = below a falling one, sideways = anything else. Nothing here tunes a
threshold; it only measures what the fixed rules did. Survivorship and inclusion bias remain (today's stock list).
"""
import argparse
import json
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import backtest as bt, costs, fetch, indicators as ta, signals          # noqa: E402

BENCH = {"IN": "^NSEI", "US": "^GSPC"}
DAYS = 252


def returns(curve):
    return [curve[i] / curve[i - 1] - 1 for i in range(1, len(curve))]


def stats(curve):
    """Risk and return measures of an equity curve that starts at 1.0 and has one point per trading day."""
    r = returns(curve)
    days = len(r)
    total = curve[-1] - 1
    cagr = bt._cagr(total, days)
    dd = bt._max_drawdown(curve)
    sd = ta.stdev(r)
    downside = math.sqrt(sum(min(x, 0.0) ** 2 for x in r) / days) * math.sqrt(DAYS) if days else None
    return {"total": total, "cagr": cagr, "max_drawdown": dd, "volatility": sd * math.sqrt(DAYS) if sd else None, "downside_dev": downside,
            "sharpe": bt._sharpe(curve), "sortino": bt._sortino(curve), "calmar": (cagr / abs(dd)) if cagr is not None and dd < 0 else None}


def hold_curve(bars, start, dividends):
    c = bars["c"]
    curve = [1.0]
    for j in range(start + 1, len(c)):
        curve.append(curve[-1] * (c[j] + dividends.get(j, 0.0)) / c[j - 1])
    return curve


def regimes(bench_bars):
    """{date: 'bull'|'bear'|'sideways'} for each day, judged from the previous close."""
    c, d = bench_bars["c"], bench_bars["d"]
    sma = ta.sma(c, 200)
    out = {}
    for i in range(1, len(c)):
        k = i - 1
        if k < 220 or sma[k] is None or sma[k - 20] is None:
            continue
        out[d[i]] = "bull" if c[k] > sma[k] and sma[k] > sma[k - 20] else "bear" if c[k] < sma[k] and sma[k] < sma[k - 20] else "sideways"
    return out


def annualised(rs):
    """Average daily return times 252. Compounding a short stretch of days up to a year would blow small samples up, so the
    arithmetic average is used for the regime comparison."""
    return (sum(rs) / len(rs) * DAYS) if rs else None


def one(symbol, market, regime_map, overrides):
    chart = fetch.fetch_chart(symbol, "5y")
    bars = chart["bars"]
    if len(bars["c"]) < 260:
        return []
    ind = signals.build_indicators(bars)
    n = len(bars["c"])
    dividends = bt.dividends_by_bar(bars, chart["dividends"])
    entry, exit_ = costs.sides(market, overrides)
    out = []
    for horizon, fn in (("short", lambda i: signals.short_score(ind, i)[0]), ("long", lambda i: signals.long_score(ind, i)[0])):
        scores = [fn(i) for i in range(n)]
        start = next((i for i in range(n - bt.MIN_DAYS) if scores[i] is not None), None)
        if start is None:
            continue
        trace = []
        curve, trades, days_in, _ = bt._simulate(bars, lambda i: scores[i], start, 20, 0, entry, exit_, dividends, True, trace)
        hold = hold_curve(bars, start, dividends)
        price_curve = bt._simulate(bars, lambda i: scores[i], start, 20, 0, entry, exit_)[0]
        price_hold = hold_curve(bars, start, {})
        s, h = stats(curve), stats(hold)
        by_regime = {}
        sr, hr = returns(curve), returns(hold)
        for name in ("bull", "bear", "sideways"):
            idx = [k for k in range(len(sr)) if regime_map.get(bars["d"][start + 1 + k]) == name]
            by_regime[name] = {"days": len(idx), "rule": annualised([sr[k] for k in idx]), "hold": annualised([hr[k] for k in idx]),
                               "exposure": (sum(1 for k in idx if trace[k]) / len(idx)) if idx else None}
        holding = [t[1] - t[0] for t in trades]
        out.append({"symbol": symbol, "market": market, "rule": horizon, "days": len(sr), "rule_stats": s, "hold_stats": h,
                    "price_only": {"rule": price_curve[-1] - 1, "hold": price_hold[-1] - 1},
                    "exposure": days_in / len(sr), "trades": len(trades), "avg_holding_days": (sum(holding) / len(holding)) if holding else None,
                    "regimes": by_regime})
    return out


def agg(rows, key):
    xs = [key(r) for r in rows]
    xs = [x for x in xs if x is not None]
    return median(xs) if xs else None


def summarise(rows):
    n = len(rows)
    out = {"stocks": n}
    out["median_total_rule"] = agg(rows, lambda r: r["rule_stats"]["total"])
    out["median_total_hold"] = agg(rows, lambda r: r["hold_stats"]["total"])
    out["beats_hold_total_return"] = sum(1 for r in rows if r["rule_stats"]["total"] > r["hold_stats"]["total"])
    out["beats_hold_price_only"] = sum(1 for r in rows if r["price_only"]["rule"] > r["price_only"]["hold"])
    for name, better in (("max_drawdown", lambda a, b: a > b), ("downside_dev", lambda a, b: a < b), ("sharpe", lambda a, b: a > b),
                         ("sortino", lambda a, b: a > b), ("calmar", lambda a, b: a > b), ("volatility", lambda a, b: a < b)):
        pairs = [(r["rule_stats"][name], r["hold_stats"][name]) for r in rows if r["rule_stats"][name] is not None and r["hold_stats"][name] is not None]
        out[name] = {"rule_median": median(a for a, _ in pairs) if pairs else None, "hold_median": median(b for _, b in pairs) if pairs else None,
                     "rule_better_in": sum(1 for a, b in pairs if better(a, b)), "of": len(pairs)}
    out["median_exposure"] = agg(rows, lambda r: r["exposure"])
    out["median_trades"] = agg(rows, lambda r: r["trades"])
    out["median_avg_holding_days"] = agg(rows, lambda r: r["avg_holding_days"])
    out["regimes"] = {}
    for name in ("bull", "bear", "sideways"):
        sel = [r["regimes"][name] for r in rows if r["regimes"][name]["days"] >= 60]
        out["regimes"][name] = {"stock_rule_pairs": len(sel), "median_days": median(x["days"] for x in sel) if sel else None,
                                "median_rule_annualised": median(x["rule"] for x in sel) if sel else None,
                                "median_hold_annualised": median(x["hold"] for x in sel) if sel else None,
                                "rule_better_in": sum(1 for x in sel if x["rule"] > x["hold"]),
                                "median_exposure": median(x["exposure"] for x in sel) if sel else None}
    return out


def pct(x, d=1):
    return "n/a" if x is None else f"{x * 100:.{d}f}%"


def num(x, d=2):
    return "n/a" if x is None else f"{x:.{d}f}"


def markdown(report):
    lines = [f"# Strategy report (generated {report['generated_at']})", "",
             "Fixed rules, never tuned, tested against buy-and-hold on up to 5 years of Yahoo daily prices, dividends included for both, "
             "assumed trading costs (see `engine/costs.py`). Today's stock list: survivorship bias is NOT removed (see README). "
             "Past results do not predict future results.", ""]
    for market in ("IN", "US"):
        for rule in ("short", "long"):
            s = report["summary"].get(f"{market}/{rule}")
            if not s:
                continue
            lines += [f"## {'NSE (India)' if market == 'IN' else 'United States'}: {'weeks view' if rule == 'short' else 'months-to-years view'} rule ({s['stocks']} stocks)", "",
                      f"* Beat buy-and-hold on total return (with dividends): **{s['beats_hold_total_return']} of {s['stocks']}** (price only: {s['beats_hold_price_only']}). "
                      f"Median total return: rule {pct(s['median_total_rule'])}, buy-and-hold {pct(s['median_total_hold'])}.",
                      f"* Median time invested {pct(s['median_exposure'], 0)}, median {num(s['median_trades'], 0)} trades, median holding {num(s['median_avg_holding_days'], 0)} days.", "",
                      "| Measure | Rule (median) | Buy-and-hold (median) | Rule better in |", "|---|---|---|---|"]
            for name, label, fmt in (("max_drawdown", "Maximum drawdown", pct), ("volatility", "Volatility (annual)", pct), ("downside_dev", "Downside deviation (annual)", pct),
                                     ("sharpe", "Sharpe", num), ("sortino", "Sortino", num), ("calmar", "Calmar", num)):
                m = s[name]
                lines.append(f"| {label} | {fmt(m['rule_median'])} | {fmt(m['hold_median'])} | {m['rule_better_in']} of {m['of']} |")
            lines += ["", "| Regime (index) | Stock-rule pairs | Median days in regime | Rule: average daily return x 252 | Buy-and-hold: same | Rule better in | Rule invested |", "|---|---|---|---|---|---|---|"]
            for name in ("bull", "bear", "sideways"):
                g = s["regimes"][name]
                lines.append(f"| {name} | {g['stock_rule_pairs']} | {num(g['median_days'], 0)} | {pct(g['median_rule_annualised'])} | {pct(g['median_hold_annualised'])} | {g['rule_better_in']} of {g['stock_rule_pairs']} | {pct(g['median_exposure'], 0)} |")
            lines.append("")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=0, help="only the first N stocks of each market")
    parser.add_argument("--out", default=str(ROOT / "docs" / "strategy-report"))
    args = parser.parse_args(argv)
    universe = json.loads((ROOT / "config" / "universe.json").read_text(encoding="utf-8"))
    cost_path = ROOT / "config" / "costs.json"
    overrides = json.loads(cost_path.read_text(encoding="utf-8")) if cost_path.exists() else {}
    maps = {m: regimes(fetch.fetch_chart(sym, "5y", 250)["bars"]) for m, sym in BENCH.items()}
    jobs = []
    for market in ("IN", "US"):
        stocks = [s for s in universe["stocks"] if s[2] == market]
        jobs += [(s[0], market) for s in (stocks[:args.limit] if args.limit else stocks)]
    rows, failed = [], []

    def work(job):
        try:
            time.sleep(0.2)
            return one(job[0], job[1], maps[job[1]], overrides)
        except fetch.FetchError as err:
            failed.append((job[0], str(err)[-80:]))
            return []

    with ThreadPoolExecutor(4) as pool:
        for part in pool.map(work, jobs):
            rows += part
    summary = {}
    for market in ("IN", "US"):
        for rule in ("short", "long"):
            sel = [r for r in rows if r["market"] == market and r["rule"] == rule]
            if sel:
                summary[f"{market}/{rule}"] = summarise(sel)
    report = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "stocks_failed": failed, "summary": summary,
              "regime_definition": "index above a rising 200-day average = bull; below a falling one = bear; otherwise sideways; judged from the previous close"}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(str(args.out) + ".json").write_text(json.dumps({**report, "rows": rows}, indent=1), encoding="utf-8")
    Path(str(args.out) + ".md").write_text(markdown(report), encoding="utf-8")
    print(markdown(report))
    print("failed:", failed)


if __name__ == "__main__":
    main()
