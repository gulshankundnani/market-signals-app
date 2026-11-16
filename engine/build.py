"""Build site/data.json (read by the web page) and site/state.json (kept for the next run).

Usage: python -m engine.build --out site [--prev SITE_URL_OR_DIR] [--limit N]
"""
import argparse
import json
import math
import os
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

from . import digest, market as market_view
from . import alerts, backtest, discover, dividends, exchange, fetch, fundamentals, ipo, ipo_gmp, ledger, news, patterns, risk, signals, tracking

ROOT = Path(__file__).resolve().parent.parent
WORKERS = 6
FUNDAMENTALS_MAX_AGE = 20 * 3600
STALE_DAYS = 5
SPARK_DAYS = 90
SPARK_MARKET = 30
MAX_FAILURE_SHARE = 0.6
NEW_MAX_BARS = 250
CARRY_MAX_AGE_DAYS = 4
NEWS_FAILURE_LIMIT = 12     # news lookups that fail in a row before the rest of the run skips them
TOO_NEW = "Too new for signals: about 60 trading days of history are needed"


class Breaker:
    """Stops asking a source that keeps failing, so one outage cannot make a whole run take twenty minutes.

    After `limit` failures in a row it is open and the caller skips the source for the rest of the run. Any success
    resets the count. Safe to share between the worker threads."""

    def __init__(self, limit):
        self.limit, self.count, self._lock = limit, 0, threading.Lock()

    @property
    def open(self):
        return self.count >= self.limit

    def failed(self):
        with self._lock:
            self.count += 1

    def succeeded(self):
        with self._lock:
            self.count = 0


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
    ctx.get("bars", {})[symbol] = bars
    close = bars["c"]
    n, i = len(close), len(close) - 1
    price = close[i]
    change = (price / close[i - 1] - 1) * 100 if n > 1 else None
    age_days = (ctx["today"] - date.fromisoformat(bars["d"][i])).days

    ticker_base = symbol.split(".")[0]
    breaker = ctx.get("news_breaker")
    if breaker is not None and breaker.open:
        items, news_error = [], True
    else:
        try:
            items, news_error = news.fetch_news(f'"{entry["name"]}" (shares OR stock OR results)', market, ctx["now"]), False
            if breaker is not None:
                breaker.succeeded()
        except fetch.FetchError:
            items, news_error = [], True
            if breaker is not None:
                breaker.failed()
    news.mark_relevance(items, entry["name"], ticker_base)

    ind = signals.build_indicators(bars)
    short_score, short_reasons = signals.short_score(ind, i)
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
        "news": {"items": items[:6], "fresh": news.fresh_headlines(items), "error": news_error},
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


def market_overview(config, now, raw=None):
    """Index, volatility and commodity cards plus world headlines. raw, if given, collects each symbol's bars."""
    def one(item):
        symbol, name, kind = item
        try:
            chart = fetch.fetch_chart(symbol, "2y", 30)
        except fetch.FetchError:
            return None
        close = chart["bars"]["c"]
        out = {"symbol": symbol, "name": name, "kind": kind, "price": r(close[-1]),
               "change_pct": r((close[-1] / close[-2] - 1) * 100, 2),
               "month_pct": r((close[-1] / close[-22] - 1) * 100, 2) if len(close) > 22 else None,
               "as_of": chart["bars"]["d"][-1], "spark": [r(v) for v in close[-SPARK_MARKET:]]}
        if raw is not None:
            raw[symbol] = chart["bars"]
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


def send_formatted(token, chat, text, reply_to=None):
    """Send a Telegram HTML message. If Telegram refuses the formatting, send the same message as plain text."""
    ok, message_id = alerts.post_telegram(token, chat, text, reply_to, markup=True)
    if not ok:
        ok, message_id = alerts.post_telegram(token, chat, alerts.to_plain(text)[: alerts.TELEGRAM_MAX_CHARS], reply_to)
    return ok, message_id


def status_block(config, records, errors, carried, overview, conditions, ipos, delivery, track_record, pending, token, chat):
    """Plain checks of how healthy this run was, for the status panel: [{"name", "state": ok|warn|info, "detail"}]."""
    checks = []

    def add(name, state, detail):
        checks.append({"name": name, "state": state, "detail": detail})

    failed = len(errors)
    add("Stock prices", "warn" if failed else "ok",
        f"{len(records)} stocks loaded" + (f", {failed} failed to load" if failed else "") + (f", {len(carried)} kept from the previous run" if carried else ""))
    news_down = sum(1 for x in records if (x.get("news") or {}).get("error"))
    add("News", "warn" if records and news_down / len(records) > 0.2 else "ok",
        "Headlines loaded" if not news_down else f"Headlines missing for {news_down} stocks" + (" (the news source is not answering)" if news_down == len(records) else ""))
    wanted, got = len(config.get("market_watch", [])), len((overview or {}).get("watch", []))
    add("Index and market data", "ok" if got >= wanted else "warn", f"{got} of {wanted} market series loaded")
    missing = [m for m, c in (conditions or {}).items() if not c.get("regime")]
    add("Market conditions", "warn" if missing else "ok",
        "Worked out for " + ", ".join(sorted(conditions or {})) if not missing else "Not available for " + ", ".join(sorted(missing)))
    latest = max(delivery) if delivery else None
    add("NSE delivery data", "ok" if latest else "info", f"Latest day {latest}" if latest else "Not loaded")
    add("India IPOs", "ok" if (ipos or {}).get("india_ok") else "warn",
        "Fresh from the source" if (ipos or {}).get("india_ok") else "Source unavailable, showing older data")
    if track_record is None:
        add("Track record", "info", "Not set up")
    else:
        waiting = len(pending)
        add("Track record", "warn" if waiting else "ok",
            f"{track_record.get('calls_total', 0)} readings recorded" + (f", {waiting} waiting to be saved" if waiting else ""))
    add("Telegram alerts", "ok" if token and chat else "info", "Connected" if token and chat else "Not set up")
    overall = "warn" if any(c["state"] == "warn" for c in checks) else "ok"
    return {"overall": overall, "checks": checks}


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


US_IPO_MAX_AGE_DAYS = 7


def carry_us_ipos(ipos, previous, now):
    """Stamp a fresh US IPO list with its time. If the calendar failed to load, keep the last list that did
    (up to a week old) and mark it, so the page does not lose it because of one refused request."""
    if ipos.get("us_ok") and ipos.get("us"):
        ipos["us_fetched_at"] = now.isoformat(timespec="seconds")
        return ipos
    previous = previous or {}
    old, stamp = previous.get("us"), previous.get("us_fetched_at")
    try:
        age_days = (now - datetime.fromisoformat(stamp)).days if stamp else None
    except (TypeError, ValueError):
        age_days = None
    if not ipos.get("us_ok") and old and age_days is not None and 0 <= age_days <= US_IPO_MAX_AGE_DAYS:
        ipos["us"], ipos["us_carried"], ipos["us_fetched_at"] = old, True, stamp
    return ipos


def carry_forward(errors, previous, today):
    """Keep last run's record for a stock that failed to load this time, so one bad answer from Yahoo does not
    make it vanish from the page. Only records with a recent price date are kept. Returns (carried, still_failed)."""
    carried, failed = [], []
    for error in errors:
        old = previous.get(error["symbol"])
        try:
            age = (today - date.fromisoformat(old["as_of"])).days if old else None
        except (KeyError, TypeError, ValueError):
            age = None
        if age is not None and 0 <= age <= CARRY_MAX_AGE_DAYS:
            # Its headlines and their ages are from an earlier run, so they must not look current.
            carried.append({**old, "carried": True, "news": {"items": [], "fresh": [], "error": True}})
        else:
            failed.append(error)
    return carried, failed


def track(args, state, prev_data, calls, ctx, now_ts, token, chat, link_for_symbol):
    """Record new calls, check open ones against fresh prices and reply to those that hit target or stop.

    Returns (track_record summary, calls still to be saved, reply queue, notified ids, [(symbol, text)] sent).
    Any failure leaves things to be retried next run; it never stops the site from publishing.
    """
    previous_record = prev_data.get("track_record")
    pending = [c for c in state.get("ledger_pending", []) if isinstance(c, dict) and "id" in c]
    pending += [c for _, c in calls if c["id"] not in {p["id"] for p in pending}]
    pending = pending[-tracking.PENDING_LIMIT:]
    queue = [i for i in state.get("reply_queue", []) if isinstance(i, str)]
    notified = [i for i in state.get("notified_outcomes", []) if isinstance(i, str)]

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    remote = args.ledger_remote or (f"https://github.com/{repo}.git" if repo and not args.ledger_dir else "")
    if not (args.ledger_dir or remote):
        return previous_record, pending, queue, notified, []
    directory = args.ledger_dir or str(Path(tempfile.mkdtemp()) / "ledger")
    git_token = os.environ.get("GITHUB_TOKEN") or None
    led = tracking.open_ledger(directory, remote, git_token)
    if led is None:
        return previous_record, pending, queue, notified, []

    try:
        for call in pending:
            led.add_call(call)
        resolved = led.resolve_open(ctx.get("bars", {}), ctx["bench"])
        queue = queue + [o["id"] for _, o in resolved]
        led.save()
        stored = tracking.publish_ledger(directory, f"Ledger update {datetime.fromtimestamp(now_ts, timezone.utc):%Y-%m-%d %H:%M}", remote, git_token)
        print(f"Ledger: {len(calls)} new calls, {len(resolved)} resolved, {len(led.open_calls())} open" + ("" if stored else " (not saved yet)"))
    except Exception as err:
        print("Ledger update failed, will retry next run:", err)
        return previous_record, pending, queue, notified, []

    sent_lines = []
    if token and chat:
        def send(text, reply_to):
            return send_formatted(token, chat, text, reply_to)[0]
        sent, queue, texts = tracking.send_replies(
            queue, led, notified, send, lambda c: link_for_symbol(c["symbol"]))
        notified = (notified + sent)[-tracking.NOTIFIED_LIMIT:]
        sent_lines = [(led.calls[i]["symbol"], t) for i, t in zip(sent, texts)]
        if sent:
            print(f"Replied to {len(sent)} calls that reached target or stop")
    else:
        queue = []
    if stored:
        pending = []
    summary = ledger.summarise(led, now_ts)
    if repo:
        summary["data_url"] = f"https://github.com/{repo}/tree/data"
    return summary, pending, queue, notified, sent_lines


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

    ctx = {"now": now, "today": today, "now_ts": int(now.timestamp()), "delivery": delivery, "deals": deals, "bench": bench,
           "bars": {}, "news_breaker": Breaker(NEWS_FAILURE_LIMIT)}

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

    previous = {s["symbol"]: s for s in prev_data.get("stocks", [])}
    carried, errors = carry_forward(errors, previous, today)
    if carried:
        print(f"{len(carried)} stocks kept from the previous run after a failed load: {', '.join(c['symbol'] for c in carried)}")
    records = sorted(records + carried, key=lambda x: x["symbol"])

    fund_cache = fill_fundamentals(records, state, ctx["now_ts"], fetch.YahooSession)
    news.keep_stock_specific(records)
    add_lists(records)
    raw_index = {}
    overview = market_overview(config, now, raw_index)
    info = {x["symbol"]: {"market": x["market"], "sector": x["sector"]} for x in records if not x.get("new") and x["sector"] != "Other"}
    conditions = market_view.analyse(config.get("regime"), raw_index, ctx["bars"], info)
    ipos = carry_us_ipos(ipo.fetch_ipos(now), prev_data.get("ipos"), now)
    india, india_ok, india_at, ipo_cache = ipo_gmp.fetch_india(now, state.get("ipo_gmp"), fetch_news=news.fetch_news)
    ipos.update({"india": india, "india_ok": india_ok,
                 "india_source": {"name": "Investorgain", "url": ipo_gmp.URL, "fetched_at": india_at}})
    print(f"India IPOs: {len(india)} rows ({'fresh' if india_ok else 'unavailable or old'})")

    first_run = not prev_data
    watch = alerts.watched_symbols(alert_cfg.get("symbols", []), {x["symbol"] for x in records})
    news_on = bool(alert_cfg.get("news", True))     # headlines in the alerts; no tone is ever worked out from them
    sides = {s: dict(v) for s, v in state.get("alert_sides", {}).items() if isinstance(v, dict)}
    items = []
    lines, seen = alerts.build_messages(previous, {x["symbol"]: x for x in records}, watch,
                                        state.get("seen_alerts", []), first_run, news_on, sides, items)
    now_ts = int(now.timestamp())
    by_symbol = {x["symbol"]: x for x in records}
    alert_log, added = alerts.update_log(alerts.drop_irrelevant(state.get("alert_log", []), by_symbol), lines, now_ts, [i["symbol"] for i in items])
    print(f"Alerts: {len(added)} new, {len(watch)} stocks watched")
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    site_url = args.prev if args.prev.startswith("http") else ""

    def link_for_symbol(symbol, section="signals"):
        return alerts.stock_link(site_url, symbol, section) if site_url else None

    calls = tracking.make_calls(items, {x["symbol"]: x for x in records}, now_ts)
    if lines and token and chat:
        links = [link_for_symbol(i["symbol"], "news" if i["kind"] == "news" else "signals") for i in items]
        for message, start, end in digest.render(items, links, now_ts):
            ok, message_id = send_formatted(token, chat, message)
            print(f"Telegram alert {'sent' if ok else 'failed'} ({end - start} alerts)")
            if ok:
                tracking.attach_message(calls, end, message_id, start)
    elif lines:
        print("New alerts are in the app. Telegram is not set up (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID).")

    track_record, pending, queue, notified, outcome_lines = track(args, state, prev_data, calls, ctx, now_ts, token, chat, link_for_symbol)
    if outcome_lines:
        alert_log, _ = alerts.update_log(alert_log, [text for _, text in outcome_lines], now_ts, [sym for sym, _ in outcome_lines])
    status = status_block(config, records, errors, carried, overview, conditions, ipos, delivery, track_record, pending, token, chat)
    data = {"version": 1, "generated_at": now.isoformat(timespec="seconds"), "market": overview, "conditions": conditions,
            "status": status, "ipos": ipos,
            "stocks": records, "errors": [{"symbol": e["symbol"], "error": e["error"]} for e in errors],
            "alerts": alert_log, "track_record": track_record,
            "exchange_data": {"delivery_dates": sorted(delivery)[-3:], "note": "NSE delivery data and bulk/block deals. The exchange order book (bid/ask depth) is not published for free."}}
    new_state = {"seen_alerts": seen, "delivery": delivery, "us_symbols": us_list, "new_listings": registry,
                 "fundamentals": fund_cache, "ipo_gmp": ipo_cache, "alert_log": alert_log,
                 "alert_sides": sides, "ledger_pending": pending, "reply_queue": queue, "notified_outcomes": notified}

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "data.json").write_text(json.dumps(data, allow_nan=False, separators=(",", ":")), encoding="utf-8")
    (out / "state.json").write_text(json.dumps(new_state, allow_nan=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {out / 'data.json'} ({(out / 'data.json').stat().st_size // 1024} KB)")
    return 0


def send_test_alert():
    """Send one test message to Telegram. Returns 0 only if it was delivered."""
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        print("Telegram is not set up. Add the TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID secrets.")
        return 1
    ok = alerts.send_telegram(token, chat, "Test alert from Stock Signals. If you can read this, alerts are working.")
    print("Telegram test alert sent" if ok else "Telegram test alert FAILED")
    return 0 if ok else 1


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-alert", action="store_true", help="send a test message to Telegram and exit")
    parser.add_argument("--out", default="site")
    parser.add_argument("--prev", default=os.environ.get("SITE_URL", ""), help="URL or folder holding the previous data.json/state.json")
    parser.add_argument("--ledger-dir", default="", help="keep the track-record ledger in this folder (no git); for testing")
    parser.add_argument("--ledger-remote", default="", help="git remote holding the data branch (default: this GitHub repository)")
    parser.add_argument("--limit", type=int, default=0, help="analyse only N stocks (testing)")
    args = parser.parse_args(argv)
    return send_test_alert() if args.test_alert else run(args)


if __name__ == "__main__":
    sys.exit(main())
