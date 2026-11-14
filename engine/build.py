"""Build site/data.json (read by the web page) and site/state.json (kept for the next run).

Usage: python -m engine.build --out site [--prev SITE_URL_OR_DIR] [--limit N]
"""
import argparse
import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

from . import alerts, backtest, discover, dividends, exchange, fetch, fundamentals, ipo, ipo_gmp, news, patterns, risk, signals

ROOT = Path(__file__).resolve().parent.parent
WORKERS = 6
FUNDAMENTALS_MAX_AGE = 20 * 3600
STALE_DAYS = 5
SPARK_DAYS = 90
MAX_FAILURE_SHARE = 0.6
NEW_MAX_BARS = 250
TOO_NEW = "Too new for signals: about 60 trading days of history are needed"


def r(value, digits=4):
    """Round for compact JSON. NaN and infinity become None."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return round(value, digits) if isinstance(value, float) else value


def load_previous(source, name):
    """Read a JSON file from a URL or a folder. Missing or broken files give {}."""
    if not source:
        return {}
    try:
        if source.startswith("http"):
            return json.loads(fetch.http_get(source.rstrip("/") + "/" + name, retries=1))
        return json.loads((Path(source) / name).read_text(encoding="utf-8"))
    except (OSError, ValueError, fetch.FetchError):
        return {}


def signal_block(score, reasons):
    if score is None:
        return {"score": None, "label": "N/A", "reasons": reasons or [{"text": TOO_NEW, "tone": "neutral"}]}
    return {"score": score, "label": signals.label(score), "reasons": reasons}


def first_valid(fn, start, end):
    for i in range(start, end):
        if fn(i) is not None:
            return i
    return None


def run_backtests(ind, n):
    if n < 260:
        return None
    out = {}
    rules = {"short": lambda i: signals.short_score(ind, i)[0], "long": lambda i: signals.long_score(ind, i)[0]}
    for name, fn in rules.items():
        start = first_valid(fn, 0, n - backtest.MIN_DAYS)
        result = backtest.backtest(ind["bars"], fn, start) if start is not None else None
        out[name] = scrub(result)
    return out


def scrub(value):
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, float):
        return r(value, 5)
    return value


def analyse(entry, ctx):
    symbol, market = entry["symbol"], entry["market"]
    min_bars = 5 if entry.get("new") else fetch.MIN_BARS
    chart = fetch.fetch_chart(symbol, "5y", min_bars)
    bars = chart["bars"]
    close = bars["c"]
    n, i = len(close), len(close) - 1
    price = close[i]
    change = (price / close[i - 1] - 1) * 100 if n > 1 else None
    age_days = (ctx["today"] - date.fromisoformat(bars["d"][i])).days

    ticker_base = symbol.split(".")[0]
    try:
        items, news_error = news.fetch_news(f'"{entry["name"]}" (shares OR stock OR results)', market, ctx["now"]), False
    except fetch.FetchError:
        items, news_error = [], True
    news.mark_relevance(items, entry["name"], ticker_base)
    news_tone = news.news_score(items)

    ind = signals.build_indicators(bars)
    short_score, short_reasons = signals.short_score(ind, i, news=news_tone)
    long_score, long_reasons = signals.long_score(ind, i)
    pressure = signals.buy_sell_pressure(ind, i)
    dividend = dividends.dividend_profile(chart["dividends"], price, ctx["now_ts"])

    base = symbol[:-3] if symbol.endswith(".NS") else None
    delivery = exchange_view = None
    if base:
        series = [(d, v[base][0]) for d, v in sorted(ctx["delivery"].items()) if base in v]
        delivery = exchange.delivery_view(series, change)
        exchange_view = ctx["deals"].get(base)

    # A "new listing" with a long price history is an old stock the exchange re-labelled (series change).
    relabelled = bool(entry.get("new")) and n > NEW_MAX_BARS
    is_new = bool(entry.get("new")) and not relabelled
    record = {
        "symbol": symbol, "name": entry["name"], "market": market,
        "sector": "Other" if relabelled else entry["sector"],
        "tags": [] if relabelled else entry.get("tags", []), "new": is_new, "listed": entry.get("listed"),
        "currency": chart["meta"]["currency"], "exchange": chart["meta"]["exchange"],
        "price": r(price), "change_pct": r(change, 2), "as_of": bars["d"][i], "stale": age_days > STALE_DAYS,
        "bars": n,
        "short": signal_block(short_score, short_reasons),
        "long": signal_block(long_score, long_reasons),
        "pressure": {"label": pressure["label"], "money_flow": r(pressure["money_flow"]),
                     "up_volume_share": r(pressure["up_volume_share"])} if pressure else None,
        "delivery": scrub(delivery),
        "deals": scrub(exchange_view),
        "news": {"score": r(news_tone), "items": items[:6], "alerts": news.strong_alerts(items), "error": news_error},
        "dividend_yield": r(dividend["ttm_yield"], 5) if dividend else 0.0,
        "dividend": scrub(dividend),
        "spark": [r(v) for v in close[-SPARK_DAYS:]],
        "spark_sma50": [r(v) for v in ind["sma50"][-SPARK_DAYS:]],
    }
    if n >= 30:
        record["patterns"] = patterns.detect_patterns(bars, ind)
    if n >= 60:
        record["levels"] = scrub({**ta_levels(bars)})
        record["stop"] = scrub(signals.stop_loss(price, ind["atr"][i]))
        record["indicators"] = {k: r(ind[k][i]) for k in
                                ("rsi", "macd_hist", "sma20", "sma50", "sma200", "atr", "vwap20", "vol30",
                                 "cmf20", "upvol20", "momentum126", "hi252", "lo252")}
    if n > 60:
        record["risk"] = scrub(risk.risk_stats(close, dict(zip(bars["d"], close)), ctx["bench"].get(market)))
    record["backtest"] = run_backtests(ind, n)
    return record


def ta_levels(bars):
    from .indicators import fibonacci_levels, support_resistance
    return {"zones": support_resistance(bars["h"], bars["l"], bars["c"]),
            "fib": fibonacci_levels(bars["h"], bars["l"])}


def add_lists(records):
    """Give every stock the lists it belongs to: fixed ones and ones computed from the data."""
    labels = {"psu": "Government-backed", "megacap": "US mega cap", "new": "New listings"}
    for rec in records:
        lists = [rec["sector"]] + [labels.get(t, t) for t in rec["tags"]]
        ind, rsk, fund = rec.get("indicators") or {}, rec.get("risk") or {}, rec.get("fundamentals") or {}
        threshold = 0.03 if rec["market"] == "IN" else 0.025
        if (rec.get("dividend_yield") or 0) >= threshold and not (rec.get("dividend") or {}).get("overdue"):
            lists.append("High dividend")
        debt = fund.get("debt_to_equity")
        if debt is not None and debt < 0.5 and rec["sector"] not in ("Banks", "Financials"):
            lists.append("Low debt")
        if rsk.get("annual_vol") is not None and rsk["annual_vol"] < 0.25:
            lists.append("Low volatility")
        if (ind.get("momentum126") or 0) >= 0.15 and ind.get("sma200") and rec["price"] > ind["sma200"]:
            lists.append("Momentum leaders")
        if ind.get("rsi") is not None and ind["rsi"] < 30:
            lists.append("Oversold")
        if ind.get("hi252") and rec["price"] >= 0.95 * ind["hi252"]:
            lists.append("Near 52-week high")
        if (rec["long"]["score"] or 0) >= 50:
            lists.append("Strong long-term trend")
        if (rec.get("delivery") or {}).get("tone") == "pos":
            lists.append("Strong delivery buying")
        rec["lists"] = list(dict.fromkeys(lists))


def market_overview(config, now):
    def one(item):
        symbol, name, kind = item
        try:
            chart = fetch.fetch_chart(symbol, "1y", 30)
        except fetch.FetchError:
            return None
        close = chart["bars"]["c"]
        out = {"symbol": symbol, "name": name, "kind": kind, "price": r(close[-1]),
               "change_pct": r((close[-1] / close[-2] - 1) * 100, 2),
               "month_pct": r((close[-1] / close[-22] - 1) * 100, 2) if len(close) > 22 else None,
               "as_of": chart["bars"]["d"][-1]}
        if kind == "index" and len(close) >= 200:
            sma50, sma200 = sum(close[-50:]) / 50, sum(close[-200:]) / 200
            out["regime"] = "Uptrend" if close[-1] > sma50 and close[-1] > sma200 else \
                "Downtrend" if close[-1] < sma50 and close[-1] < sma200 else "Mixed"
        if kind == "fear":
            out["regime"] = "Calm" if close[-1] < 15 else "Normal" if close[-1] < 25 else "Fearful"
        return out

    with ThreadPoolExecutor(WORKERS) as pool:
        watch = [x for x in pool.map(one, config["market_watch"]) if x]

    headlines = {}
    for market, query in config["global_news_queries"]:
        try:
            for item in news.fetch_news(query, market, now, limit=5):
                headlines.setdefault(item["title"], {**item, "topic": query})
        except fetch.FetchError:
            continue
    ordered = sorted(headlines.values(), key=lambda i: (i["age_min"] is None, i["age_min"] or 0))[:20]
    return {"watch": watch, "news": ordered}


def fill_fundamentals(records, state, now_ts, session_factory):
    """Use cached fundamentals when fresh, otherwise fetch them (once a day per stock)."""
    cache = state.get("fundamentals", {})
    missing = []
    for rec in records:
        cached = cache.get(rec["symbol"])
        if cached and now_ts - cached.get("ts", 0) < FUNDAMENTALS_MAX_AGE:
            rec["fundamentals"] = cached["data"]
        else:
            missing.append(rec)
    if missing:
        session = session_factory()
        if session.start():
            for rec in missing:
                data = fundamentals.fetch_fundamentals(session, rec["symbol"])
                rec["fundamentals"] = data
                if data:
                    cache[rec["symbol"]] = {"ts": now_ts, "data": data}
                time.sleep(0.15)
        else:
            print("Fundamentals unavailable this run (Yahoo session failed)")
            for rec in missing:
                rec["fundamentals"] = cache.get(rec["symbol"], {}).get("data", {})
    live = {rec["symbol"] for rec in records}
    return {k: v for k, v in cache.items() if k in live}


def build_universe(config, registry, limit):
    stocks = [{"symbol": s, "name": nm, "market": m, "sector": sec, "tags": tags}
              for s, nm, m, sec, tags in config["stocks"]]
    known = {s["symbol"] for s in stocks}
    stocks += [{"symbol": e["symbol"], "name": e["name"], "market": e["market"], "sector": "New listings",
                "tags": ["new"], "new": True, "listed": e["listed"]} for e in registry if e["symbol"] not in known]
    if limit:
        picked = [s for s in stocks if s["market"] == "IN"][: limit // 2] + [s for s in stocks if s["market"] == "US"][: limit - limit // 2]
        new = [s for s in stocks if s.get("new")][:2]
        stocks = picked + [s for s in new if s not in picked]
    return stocks


def run(args):
    now = datetime.now(timezone.utc)
    today = now.date()
    config = json.loads((ROOT / "config" / "universe.json").read_text(encoding="utf-8"))
    alert_path = ROOT / "config" / "alerts.json"
    alert_cfg = json.loads(alert_path.read_text(encoding="utf-8")) if alert_path.exists() else {}
    prev_data = load_previous(args.prev, "data.json")
    state = load_previous(args.prev, "state.json")

    registry, us_list = discover.discover(state, today)
    universe = build_universe(config, registry, args.limit)
    print(f"{len(universe)} stocks ({sum(1 for s in universe if s.get('new'))} new listings)")

    in_bases = [s["symbol"][:-3] for s in universe if s["symbol"].endswith(".NS")]
    delivery = exchange.sync_delivery(state.get("delivery", {}), today, in_bases)
    deals = exchange.summarise_deals(exchange.fetch_deals(), in_bases)

    bench = {}
    for market, symbol in config["benchmarks"].items():
        try:
            chart = fetch.fetch_chart(symbol, "5y", 100)
            bench[market] = dict(zip(chart["bars"]["d"], chart["bars"]["c"]))
        except fetch.FetchError as err:
            print("benchmark failed:", err)

    ctx = {"now": now, "today": today, "now_ts": int(now.timestamp()), "delivery": delivery, "deals": deals, "bench": bench}

    def safe(entry):
        try:
            return analyse(entry, ctx)
        except fetch.FetchError as err:
            return {"symbol": entry["symbol"], "error": str(err)}

    with ThreadPoolExecutor(WORKERS) as pool:
        results = list(pool.map(safe, universe))
    records = sorted((x for x in results if "error" not in x), key=lambda x: x["symbol"])
    errors = [x for x in results if "error" in x]
    print(f"{len(records)} analysed, {len(errors)} failed")
    if not records or len(errors) / len(results) > MAX_FAILURE_SHARE:
        print("Too many failures, refusing to publish a broken data set", file=sys.stderr)
        return 1

    fund_cache = fill_fundamentals(records, state, ctx["now_ts"], fetch.YahooSession)
    add_lists(records)
    overview = market_overview(config, now)
    ipos = ipo.fetch_ipos(now)
    india, india_ok, india_at, ipo_cache = ipo_gmp.fetch_india(now, state.get("ipo_gmp"), fetch_news=news.fetch_news)
    ipos.update({"india": india, "india_ok": india_ok,
                 "india_source": {"name": "Investorgain", "url": ipo_gmp.URL, "fetched_at": india_at}})
    print(f"India IPOs: {len(india)} rows ({'fresh' if india_ok else 'unavailable or old'})")

    previous = {s["symbol"]: s for s in prev_data.get("stocks", [])}
    first_run = not prev_data
    watch = [s for s in alert_cfg.get("symbols", []) if s in {x["symbol"] for x in records}]
    lines, seen = alerts.build_messages(previous, {x["symbol"]: x for x in records}, watch,
                                        state.get("seen_alerts", []), first_run)
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if lines and token and chat:
        print("Telegram alert sent" if alerts.send_telegram(token, chat, "\n\n".join(lines)) else "Telegram alert failed")
    elif lines:
        print(f"{len(lines)} alerts ready but TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not set")

    data = {"version": 1, "generated_at": now.isoformat(timespec="seconds"), "market": overview, "ipos": ipos,
            "stocks": records, "errors": [{"symbol": e["symbol"], "error": e["error"]} for e in errors],
            "exchange_data": {"delivery_dates": sorted(delivery)[-3:], "note": "NSE delivery data and bulk/block deals. The exchange order book (bid/ask depth) is not published for free."}}
    new_state = {"seen_alerts": seen, "delivery": delivery, "us_symbols": us_list, "new_listings": registry,
                 "fundamentals": fund_cache, "ipo_gmp": ipo_cache}

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "data.json").write_text(json.dumps(data, allow_nan=False, separators=(",", ":")), encoding="utf-8")
    (out / "state.json").write_text(json.dumps(new_state, allow_nan=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {out / 'data.json'} ({(out / 'data.json').stat().st_size // 1024} KB)")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="site")
    parser.add_argument("--prev", default=os.environ.get("SITE_URL", ""), help="URL or folder holding the previous data.json/state.json")
    parser.add_argument("--limit", type=int, default=0, help="analyse only N stocks (testing)")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
