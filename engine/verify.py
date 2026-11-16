"""Cross-checking Yahoo's prices against independent sources, and measuring how well they agree.

India: the NSE's own end-of-day file (the official closing price of every stock).
United States: Cboe's public delayed-quote history, fetched once a day.

Only completed sessions are compared, close against close, on the dates both sources have. A stock whose prices
disagree with its official source is flagged and gets no new alerts until they agree again. The figures are published
on the Market tab, so the accuracy can be seen rather than assumed.
"""
from concurrent.futures import ThreadPoolExecutor
from statistics import median

from .fetch import FetchError, http_get

CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/charts/historical/{symbol}.json"
SOURCES = {"IN": "NSE end-of-day file", "US": "Cboe delayed-quote history"}
MATCH = 0.005        # closes within 0.5% count as matching
MISMATCH = 0.02      # more than 2% apart is a real disagreement
RECENT = 8           # completed sessions compared per stock
KEEP_DATES = 15
WORKERS = 4


def compare(bars, official, recent=RECENT):
    """How Yahoo's closes compare with an official {iso_date: close}. None when no date is shared."""
    yahoo = dict(zip(bars["d"], bars["c"]))
    shared = sorted(d for d in official if d in yahoo and official[d])[-recent:]
    if not shared:
        return None
    diffs = [abs(yahoo[d] / official[d] - 1) for d in shared]
    worst = max(diffs)
    status = "match" if worst <= MATCH else "minor" if worst <= MISMATCH else "mismatch"
    return {"status": status, "n": len(shared), "max_diff_pct": round(worst * 100, 3),
            "median_diff_pct": round(median(diffs) * 100, 3), "last_date": shared[-1]}


def summarise(results, market):
    """Accuracy figures for one source. results: {symbol: compare() result or None}."""
    verified = {s: r for s, r in results.items() if r}
    counts = {k: sum(1 for r in verified.values() if r["status"] == k) for k in ("match", "minor", "mismatch")}
    diffs = [r["max_diff_pct"] for r in verified.values()]
    medians = [r["median_diff_pct"] for r in verified.values()]
    worst = sorted(verified.items(), key=lambda kv: -kv[1]["max_diff_pct"])[:5]
    return {"market": market, "source": SOURCES[market], "stocks": len(results), "verified": len(verified),
            "unverified": len(results) - len(verified), **counts,
            "agreement_pct": round(100 * counts["match"] / len(verified), 1) if verified else None,
            "median_diff_pct": round(median(medians), 3) if medians else None,
            "max_diff_pct": round(max(diffs), 3) if diffs else None,
            "as_of": max((r["last_date"] for r in verified.values()), default=None),
            "worst": [{"symbol": s, "max_diff_pct": r["max_diff_pct"], "status": r["status"]} for s, r in worst if r["status"] != "match"]}


def cross_check(records, bars_by_symbol, india_closes, us_closes):
    """Returns ({symbol: {"source", ...compare result}}, {"IN": summary, "US": summary}).

    india_closes: {iso_date: {nse_symbol: close}}. us_closes: {symbol: {iso_date: close}}."""
    per_symbol, grouped = {}, {"IN": {}, "US": {}}
    for rec in records:
        symbol, bars = rec["symbol"], bars_by_symbol.get(rec["symbol"])
        if not bars or rec.get("carried"):
            continue
        if symbol.endswith(".NS"):
            base = symbol[:-3]
            official = {d: day[base] for d, day in india_closes.items() if base in day}
            market = "IN"
        elif rec.get("market") == "US":
            official, market = (us_closes or {}).get(symbol) or {}, "US"
        else:
            continue
        result = compare(bars, official) if official else None
        grouped[market][symbol] = result
        if result:
            per_symbol[symbol] = {"source": SOURCES[market], **result}
    return per_symbol, {m: summarise(r, m) for m, r in grouped.items() if r}


def trusted(symbols, per_symbol):
    """The symbols whose prices the official source does not dispute. Unverified stocks are kept: no evidence either way."""
    return [s for s in symbols if (per_symbol.get(s) or {}).get("status") != "mismatch"]


def parse_cboe_history(payload, keep=KEEP_DATES):
    """{iso_date: close} for the latest sessions in a Cboe historical-quotes document."""
    rows = ((payload or {}).get("data") or []) if isinstance(payload, dict) else []
    out = {}
    for row in rows:
        try:
            if row["date"] and float(row["close"]) > 0:
                out[str(row["date"])] = float(row["close"])
        except (KeyError, TypeError, ValueError):
            continue
    return dict(list(out.items())[-keep:])


def _cboe_symbol(symbol):
    return symbol.replace("-", ".").upper()


class RateLimited(Exception):
    """The source asked us to slow down. The round stops at once and carries on in a later run."""


def fetch_cboe_history(symbol):
    import json
    try:
        return parse_cboe_history(json.loads(http_get(CBOE_URL.format(symbol=_cboe_symbol(symbol)), timeout=30, retries=1)))
    except FetchError as err:
        if "429" in str(err):
            raise RateLimited(str(err))
        return {}
    except (ValueError, UnicodeDecodeError):
        return {}


def sync_us(cache, today_iso, symbols, fetcher=fetch_cboe_history, per_run=12, pause=2.0, sleep=None):
    """Cboe history for the US stocks. cache: {"day", "closes": {symbol: {date: close}}, "no_data": [...], "refreshed": [...]}.

    Each stock is refreshed once per UTC day, and gently: one request at a time with a pause, at most `per_run` stocks
    per run, and the round stops at the first rate-limit answer. Stocks not reached yet are picked up in the next
    run, and until then their earlier history is still used, so the check never has gaps and never hammers the source.
    A symbol the source has no data for is remembered and shows as unverified."""
    import time
    sleep = sleep or time.sleep
    cache = cache if isinstance(cache, dict) else {}
    closes = dict(cache.get("closes") or {})
    no_data = set(cache.get("no_data") or [])
    refreshed = set(cache.get("refreshed") or []) if cache.get("day") == today_iso else set()
    todo = [s for s in symbols if s not in refreshed]
    if not todo:
        return cache
    done = 0
    for symbol in todo[:per_run]:
        if done:
            sleep(pause)
        try:
            history = fetcher(symbol)
        except RateLimited:
            break
        done += 1
        refreshed.add(symbol)
        if history:
            closes[symbol] = history
            no_data.discard(symbol)
        elif symbol not in closes:
            no_data.add(symbol)
    if not done:
        return cache
    return {"day": today_iso, "closes": closes, "no_data": sorted(no_data), "refreshed": sorted(refreshed)}
