"""Coverage of every listed stock, not only the curated list.

The curated list (config/universe.json) gets the full analysis: indicators, readings, backtests. Everything else gets what can be done
honestly for a thousand stocks every ten minutes:

  * the universe itself: every equity-series stock on the NSE (the NSE's own equity list) and the S&P 500 (Wikipedia's constituents
    table), refreshed once a day, previous list kept if a refresh fails;
  * live quotes in batches of 100 from Yahoo (price, move, volume, average volume, 52-week range): about 25 requests, a few seconds;
  * factual "mover" events from those quotes: a big move, a new 52-week high or low, a volume surge. No reading, no forecast;
  * headlines found stock by stock (each stock's own query, relevance-checked against its name), a rotating slice per run so the
    whole list is covered every few hours, with stocks that just moved first.
"""
import re
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from . import discover, news
from .fetch import FetchError, http_get

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbols}"
REFRESH_HOURS = 20
BATCH = 100
NEWS_PER_RUN = 50
NEWS_WORKERS = 6
MOVERS_PER_RUN = 8
SEEN_HOURS = 36
# Thresholds are plain statements of size, not judgements: nothing here says a move is good or bad.
THRESHOLDS = {"IN": {"move": 8.0, "min_price": 10.0, "min_avg_volume": 50_000, "min_cap": 5e9},
              "US": {"move": 6.0, "min_price": 5.0, "min_avg_volume": 300_000, "min_cap": 2e9}}
# Penny and small stocks (below the price, volume or size floors above) are included, with higher bars because they move a lot every day and a
# little volume can move them. They still need some real trading: the average daily turnover must reach the floor, so a stock that hardly
# trades cannot raise an alert with a handful of shares. They are labelled "small stock" and limited to MAX_SMALL_PER_RUN of the run's alerts.
SMALL = {"IN": {"move": 12.0, "volume_move": 5.0, "min_avg_volume": 10_000, "min_turnover": 5e5},
         "US": {"move": 10.0, "volume_move": 5.0, "min_avg_volume": 50_000, "min_turnover": 5e4}}
SMALL_VOLUME_RATIO = 4.0
MAX_SMALL_PER_RUN = 3
VOLUME_RATIO = 3.0
VOLUME_MOVE = 2.0
NEAR_EXTREME = 0.999
_SUFFIX = re.compile(r"\s*\b(limited|ltd\.?|inc\.?|incorporated|corporation|corp\.?|company|co\.?|plc|holdings?|the)\b\.?\s*$", re.I)


def _cells(row):
    return [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.S)]


def parse_sp500(html):
    """{ticker (Yahoo style): {"name", "sector"}} from Wikipedia's S&P 500 constituents table."""
    table = re.search(r'<table[^>]*id="constituents"[^>]*>(.*?)</table>', html or "", flags=re.S)
    out, header = {}, None
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table.group(1) if table else "", flags=re.S):
        cells = _cells(row)
        if header is None:
            if "Symbol" in cells and "Security" in cells:
                header = (cells.index("Symbol"), cells.index("Security"), cells.index("GICS Sector") if "GICS Sector" in cells else None)
            continue
        if len(cells) > max(i for i in header if i is not None) and cells[header[0]]:
            out[cells[header[0]].replace(".", "-")] = {"name": cells[header[1]], "sector": cells[header[2]] if header[2] is not None else None}
    return out


def short_name(name):
    """A company's name without its legal suffix, for a news query: 'Billionbrains Garage Ventures Limited' becomes 'Billionbrains Garage Ventures'."""
    previous = None
    name = original = (name or "").strip()
    while name != previous:
        previous, name = name, _SUFFIX.sub("", name).strip(" ,.")
    return name or (original or "")


def build_universe(nse_names, sp500, curated):
    """The wide list: [{"symbol", "name", "market"}] for every NSE and S&P 500 stock that is not in the curated list."""
    skip = set(curated)
    wide = [{"symbol": s, "name": n, "market": "IN"} for s, n in sorted(nse_names.items()) if s not in skip]
    wide += [{"symbol": s, "name": v["name"], "market": "US"} for s, v in sorted(sp500.items()) if s not in skip]
    return wide


def load_universe(state, now, curated, fetcher=http_get, log=print):
    """(wide list, cache to keep). Refreshed once a day; the previous list is kept if a refresh fails, and an empty answer never replaces a list."""
    cache = state or {}
    stamp = cache.get("fetched_at")
    try:
        fresh = bool(stamp) and (now - datetime.fromisoformat(stamp)) < timedelta(hours=REFRESH_HOURS)
    except (TypeError, ValueError):
        fresh = False
    if fresh and cache.get("list"):
        return [s for s in cache["list"] if s["symbol"] not in set(curated)], cache
    nse, sp = {}, {}
    try:
        nse = discover.nse_company_names(fetcher(discover.NSE_LIST_URL).decode("utf-8", "replace"))
    except FetchError as err:
        log(f"Wide universe: the NSE equity list failed ({str(err)[-60:]})")
    try:
        sp = parse_sp500(fetcher(SP500_URL).decode("utf-8", "replace"))
    except FetchError as err:
        log(f"Wide universe: the S&P 500 list failed ({str(err)[-60:]})")
    previous = cache.get("list") or []
    wide = build_universe(nse, sp, curated) if (nse or sp) else []
    if not nse:                                      # keep the old Indian list rather than lose it
        wide = [s for s in previous if s["market"] == "IN"] + [s for s in wide if s["market"] == "US"]
    if not sp:
        wide = [s for s in wide if s["market"] == "IN"] + [s for s in previous if s["market"] == "US"]
    if not (nse or sp) or not wide:
        return [s for s in previous if s["symbol"] not in set(curated)], cache
    log(f"Wide universe: {sum(1 for s in wide if s['market'] == 'IN')} NSE and {sum(1 for s in wide if s['market'] == 'US')} US stocks beyond the curated list")
    complete = bool(nse) and bool(sp)
    # a partial refresh is used now but not stamped as done, so the missing source is asked for again on the next run
    return wide, {"fetched_at": now.isoformat(timespec="seconds") if complete else cache.get("fetched_at"), "list": wide}


def _num(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def parse_quotes(payload):
    """{symbol: compact quote} from a Yahoo quote answer. Rows without a symbol or a price are skipped."""
    out = {}
    rows = (((payload or {}).get("quoteResponse") or {}).get("result")) if isinstance(payload, dict) else None
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not row.get("symbol"):
            continue
        price = _num(row.get("regularMarketPrice"))
        if not price or price <= 0:
            continue
        out[row["symbol"]] = {"price": price, "change_pct": _num(row.get("regularMarketChangePercent")), "volume": _num(row.get("regularMarketVolume")),
                              "avg_volume": _num(row.get("averageDailyVolume3Month")), "high52": _num(row.get("fiftyTwoWeekHigh")),
                              "low52": _num(row.get("fiftyTwoWeekLow")), "cap": _num(row.get("marketCap")), "state": row.get("marketState"),
                              "time": _num(row.get("regularMarketTime"))}
    return out


def fetch_quotes(session, symbols, log=print, pause=0.15):
    """Live quotes for any number of symbols, 100 per request. A failed batch is skipped, never fatal."""
    out, failed = {}, 0
    for start in range(0, len(symbols), BATCH):
        chunk = symbols[start:start + BATCH]
        try:
            out.update(parse_quotes(session.get_json(QUOTE_URL.format(symbols=urllib.parse.quote(",".join(chunk))))))
        except (FetchError, ValueError):
            failed += 1
        time.sleep(pause)
    if failed:
        log(f"Quotes: {failed} of {-(-len(symbols) // BATCH)} batches failed")
    return out


def mover_events(quotes, market_of, open_markets):
    """Factual events from live quotes: a big move, a volume surge, a new 52-week high or low.

    quotes: {symbol: quote}. market_of: {symbol: "IN"|"US"}. open_markets: markets whose session is running (a quote outside the session
    repeats the last close and is not an event). Returns [{"symbol", "tags": [...], "move", "price", "magnitude"}], strongest first."""
    events = []
    for symbol, q in quotes.items():
        market = market_of.get(symbol)
        if market not in open_markets or q.get("state") not in (None, "REGULAR"):
            continue
        t, s = THRESHOLDS[market], SMALL[market]
        price, move, avg = q["price"], q.get("change_pct"), q.get("avg_volume")
        main = bool(avg) and price >= t["min_price"] and avg >= t["min_avg_volume"] and (q.get("cap") or 0) >= t["min_cap"]
        if not main and not (avg and avg >= s["min_avg_volume"] and price * avg >= s["min_turnover"]):
            continue                                     # not even the small-stock floor of real trading
        tags = []
        if main:
            if move is not None and abs(move) >= t["move"]:
                tags.append(f"{move:+.1f}%")
            if q.get("volume") and q["volume"] >= VOLUME_RATIO * avg and move is not None and abs(move) >= VOLUME_MOVE:
                tags.append(f"volume {q['volume'] / avg:.1f}x average")
            if q.get("high52") and price >= q["high52"] * NEAR_EXTREME:
                tags.append("at 52-week high")
            elif q.get("low52") and price <= q["low52"] / NEAR_EXTREME:
                tags.append("at 52-week low")
        else:
            if move is not None and abs(move) >= s["move"]:
                tags.append(f"{move:+.1f}%")
            if q.get("volume") and q["volume"] >= SMALL_VOLUME_RATIO * avg and move is not None and abs(move) >= s["volume_move"]:
                tags.append(f"volume {q['volume'] / avg:.1f}x average")
        if tags:
            events.append({"symbol": symbol, "tags": tags, "move": move, "price": price, "small": not main,
                           "magnitude": abs(move or 0) + 2 * (len(tags) - 1)})
    return sorted(events, key=lambda e: -e["magnitude"])


def open_markets(quotes, market_of):
    """Markets that have at least a few quotes in a running regular session."""
    count = {}
    for symbol, q in quotes.items():
        if q.get("state") == "REGULAR":
            count[market_of.get(symbol)] = count.get(market_of.get(symbol), 0) + 1
    return {m for m, n in count.items() if m and n >= 3}


def pick_news_batch(wide, cursor, priority, size=NEWS_PER_RUN):
    """(stocks to look up now, new cursor). Stocks that just moved come first, then the next slice of the list in order."""
    order = {s["symbol"]: s for s in wide}
    chosen = [order[s] for s in priority if s in order][:size]
    taken = {s["symbol"] for s in chosen}
    count = len(wide)
    index = cursor % count if count else 0
    advanced = 0
    while count and len(chosen) < size and advanced < count:
        stock = wide[(index + advanced) % count]
        advanced += 1
        if stock["symbol"] not in taken:
            chosen.append(stock)
            taken.add(stock["symbol"])
    return chosen, (index + advanced) % count if count else 0


def wide_news(stocks, now, breaker, fetcher=news.fetch_news, workers=NEWS_WORKERS):
    """{symbol: {"name", "news": {"fresh": [...]}}}: each stock's own query, kept only if the headline is clearly about the company."""
    out, lock = {}, threading.Lock()

    def one(stock):
        if breaker is not None and breaker.open:
            return
        try:
            items = fetcher(f'"{short_name(stock["name"])}" (shares OR stock OR results)', stock["market"], now)
            if breaker is not None:
                breaker.succeeded()
        except FetchError:
            if breaker is not None:
                breaker.failed()
            return
        news.mark_relevance(items, stock["name"], stock["symbol"].split(".")[0])
        fresh = news.fresh_headlines(items)
        if fresh:
            with lock:
                out[stock["symbol"]] = {"name": stock["name"], "news": {"fresh": fresh}}

    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(one, stocks))
    return out


def top_moves(quotes, market_of, market, n=5):
    """(gainers, losers, (stocks at a 52-week high, at a low)) among the liquid stocks of one market, from live or closing quotes.
    gainers and losers are [(symbol, percent, price)]."""
    th = THRESHOLDS[market]
    rows, highs, lows = [], 0, 0
    for symbol, q in quotes.items():
        if market_of.get(symbol) != market or q.get("change_pct") is None:
            continue
        if q["price"] < th["min_price"] or not q.get("avg_volume") or q["avg_volume"] < th["min_avg_volume"] or (q.get("cap") or 0) < th["min_cap"]:
            continue
        rows.append((symbol, q["change_pct"], q["price"]))
        if q.get("high52") and q["price"] >= q["high52"] * NEAR_EXTREME:
            highs += 1
        elif q.get("low52") and q["price"] <= q["low52"] / NEAR_EXTREME:
            lows += 1
    rows.sort(key=lambda r: -r[1])
    return rows[:n], sorted(rows, key=lambda r: r[1])[:n], (highs, lows)
