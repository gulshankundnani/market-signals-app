"""Every listed stock in the table, with its analysis loaded only when it is opened.

The curated list (config/universe.json) is in data.json with full records. Every other NSE and S&P 500 stock is covered here:

  * index.json: one small row per stock for the dashboard table (price, move, volume, size, 52-week range, and the two readings once the
    stock has been analysed). It comes from the bulk quotes of the run, so it is as fresh as the run and costs no extra requests.
  * detail/NN.json: the full analysis record of each stock, in 64 shards by a fixed hash of the symbol. The page fetches one shard only when a
    stock is opened. A run analyses PER_RUN stocks (never analysed first, most traded first, then the stalest), so the whole list is
    covered within hours and each stock is refreshed about once a day, which is how often completed daily candles change.
    The previous shards are read back from the published site, because every publish replaces the whole site.

A stock whose history is too short to analyse is retried after RETRY_HOURS; until then its row shows the quote only.
"""
import json
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

SHARDS = 64
PER_RUN = 60
REFRESH_HOURS = 20
RETRY_HOURS = 72
WORKERS = 6


class _NoNews:
    """A news breaker that is always open: stocks analysed here skip the per-stock headline request (the wide headline lookup covers them)."""
    open = True

    def succeeded(self):
        pass

    def failed(self):
        pass


def shard_of(symbol):
    return "%02d" % (zlib.crc32(symbol.encode("utf-8")) % SHARDS)


def _age_hours(stamp, now):
    try:
        return (now - datetime.fromisoformat(stamp)).total_seconds() / 3600
    except (TypeError, ValueError):
        return None


def pick(wide_list, quotes, index, now, size=PER_RUN):
    """The stocks to analyse this run: never analysed first (most traded first), then the stalest. Only stocks that have a quote."""
    due = []
    for stock in wide_list:
        q = quotes.get(stock["symbol"])
        if not q:
            continue
        entry = index.get(stock["symbol"])
        if entry:
            age = _age_hours(entry.get("at"), now)
            if age is not None and age < (REFRESH_HOURS if entry.get("ok") else RETRY_HOURS):
                continue
        turnover = (q.get("avg_volume") or 0) * q["price"]
        due.append(((1, entry.get("at") or "", 0) if entry else (0, "", -turnover), stock))
    due.sort(key=lambda row: row[0])
    return [stock for _, stock in due[:size]]


def load_shards(prev, loader):
    """{shard id: {symbol: record}} read back from the previous publish. A shard that does not exist yet is simply empty."""
    def one(number):
        name = "%02d" % number
        return name, (loader(prev, f"detail/{name}.json") if prev else {})
    with ThreadPoolExecutor(WORKERS) as pool:
        loaded = dict(pool.map(one, range(SHARDS)))
    return {k: (v if isinstance(v, dict) else {}) for k, v in loaded.items()}


def _round(value, digits):
    return round(value, digits) if isinstance(value, (int, float)) else None


NEAR = 0.05          # the same 5% as the "Near 52-week high / low" lists of the curated stocks


def quote_lists(q):
    """Lists a stock belongs to from its quote alone, so even a stock that is not analysed yet appears in them."""
    price, high, low = q["price"], q.get("high52"), q.get("low52")
    lists = []
    if high and price >= (1 - NEAR) * high:
        lists.append("Near 52-week high")
    if low and price <= (1 + NEAR) * low:
        lists.append("Near 52-week low")
    return lists


def index_row(stock, q, entry, record):
    short = (record or {}).get("short") or {}
    long_ = (record or {}).get("long") or {}
    return {"symbol": stock["symbol"], "name": stock["name"], "market": stock["market"], "sector": "Other",
            "currency": "INR" if stock["symbol"].endswith(".NS") else "USD",
            "price": round(q["price"], 4), "change_pct": _round(q.get("change_pct"), 2), "cap": _round(q.get("cap"), 0), "volume": _round(q.get("volume"), 0),
            "avg_volume": _round(q.get("avg_volume"), 0),
            "high52": _round(q.get("high52"), 2), "low52": _round(q.get("low52"), 2), "shard": shard_of(stock["symbol"]), "analysed": bool(record),
            "short": {"label": short.get("label"), "score": short.get("score")} if record else None,
            "long": {"label": long_.get("label"), "score": long_.get("score")} if record else None,
            "analysed_at": (entry or {}).get("at") if record else None,
            "lists": list(dict.fromkeys(quote_lists(q) + [n for n in (record or {}).get("lists", []) if n != "Other"]))}


def update(now, index, wide_list, quotes, analyse, ctx, prev, loader, out_dir, log=print, lists_fn=None):
    """Analyse this run's slice, rewrite the shards and the index. Returns the analysis index to keep in the saved state.

    analyse(entry, ctx) is the same function the curated list uses. One bad stock never stops the run."""
    wide_symbols = {s["symbol"] for s in wide_list}
    index = {s: e for s, e in (index or {}).items() if s in wide_symbols}
    shards = load_shards(prev, loader)
    for symbol in list(index):                           # an index entry whose record is gone (a shard that failed to load) is analysed again
        if index[symbol].get("ok") and symbol not in shards.get(shard_of(symbol), {}):
            del index[symbol]
    chosen = pick(wide_list, quotes, index, now)
    local = {**ctx, "bars": {}, "completed_bars": {}, "news_breaker": _NoNews()}

    def one(stock):
        entry = {"symbol": stock["symbol"], "name": stock["name"], "market": stock["market"], "sector": "Other", "tags": []}
        try:
            return stock["symbol"], analyse(entry, local), None
        except Exception as err:                         # a short history or a bad row is this stock's trouble only
            return stock["symbol"], None, str(err)[:100]

    with ThreadPoolExecutor(WORKERS) as pool:
        results = list(pool.map(one, chosen))
    stamp, done = now.isoformat(timespec="seconds"), 0
    for symbol, record, error in results:
        if record is not None:
            try:
                json.dumps(record, allow_nan=False)
            except ValueError:
                record, error = None, "unreadable numbers"
        if record is None:
            index[symbol] = {"at": stamp, "ok": False, "error": error}
            continue
        if lists_fn:
            lists_fn([record])                          # the lists (Oversold, Low debt and so on) the curated stocks get
        shards[shard_of(symbol)][symbol] = record
        index[symbol] = {"at": stamp, "ok": True}
        done += 1
    for records in shards.values():                      # drop records of stocks that left the list
        for symbol in [s for s in records if s not in wide_symbols]:
            del records[symbol]
    out = Path(out_dir)
    (out / "detail").mkdir(parents=True, exist_ok=True)
    for name, records in shards.items():
        if records:
            (out / "detail" / f"{name}.json").write_text(json.dumps(records, allow_nan=False, separators=(",", ":")), encoding="utf-8")
    all_records = {s: r for records in shards.values() for s, r in records.items()}
    rows = [index_row(s, quotes[s["symbol"]], index.get(s["symbol"]), all_records.get(s["symbol"])) for s in wide_list if s["symbol"] in quotes]
    payload = {"generated_at": stamp, "count": len(rows), "analysed": sum(1 for r in rows if r["analysed"]), "stocks": rows}
    (out / "index.json").write_text(json.dumps(payload, allow_nan=False, separators=(",", ":")), encoding="utf-8")
    log(f"Catalog: {len(rows)} stocks listed, {payload['analysed']} analysed; {done} analysed this run, {len(results) - done} without enough history")
    return index
