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

SOURCES = {"IN": "NSE end-of-day file"}
MATCH = 0.005        # closes within 0.5% count as matching
MISMATCH = 0.02      # more than 2% apart is a real disagreement
RECENT = 8           # completed sessions compared per stock
KEEP_DATES = 15
WORKERS = 4


def compare(bars, official, recent=RECENT):
    """How Yahoo's closes compare with an official {iso_date: close}. None when no date is shared.

    status "behind": the official source already has a completed session that Yahoo's series lacks (Yahoo's newest bar is older than the
    official file's newest date). A price from that series is a previous session's price, so nothing is alerted or recorded at it."""
    yahoo = dict(zip(bars["d"], bars["c"]))
    dates = sorted(d for d in official if official[d])
    behind = bool(dates) and bool(bars["d"]) and dates[-1] > bars["d"][-1]
    shared = [d for d in dates if d in yahoo][-recent:]
    if not shared:
        return {"status": "behind", "n": 0, "max_diff_pct": 0.0, "median_diff_pct": 0.0, "last_date": dates[-1], "official_last": dates[-1], "yahoo_last": bars["d"][-1]} if behind else None
    diffs = [abs(yahoo[d] / official[d] - 1) for d in shared]
    worst = max(diffs)
    status = "behind" if behind else "match" if worst <= MATCH else "minor" if worst <= MISMATCH else "mismatch"
    return {"status": status, "n": len(shared), "max_diff_pct": round(worst * 100, 3),
            "median_diff_pct": round(median(diffs) * 100, 3), "last_date": shared[-1], "official_last": dates[-1], "yahoo_last": bars["d"][-1]}


def summarise(results, market):
    """Accuracy figures for one source. results: {symbol: compare() result or None}."""
    verified = {s: r for s, r in results.items() if r}
    counts = {k: sum(1 for r in verified.values() if r["status"] == k) for k in ("match", "minor", "mismatch", "behind")}
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


def cross_check(records, bars_by_symbol, india_closes):
    """Returns ({symbol: {"source", ...compare result}}, {"IN": summary}).

    india_closes: {iso_date: {nse_symbol: close}}."""
    per_symbol, grouped = {}, {"IN": {}}
    for rec in records:
        symbol, bars = rec["symbol"], bars_by_symbol.get(rec["symbol"])
        if not bars or rec.get("carried") or not symbol.endswith(".NS"):
            continue
        base = symbol[:-3]
        official = {d: day[base] for d, day in india_closes.items() if base in day}
        result = compare(bars, official) if official else None
        grouped["IN"][symbol] = result
        if result:
            per_symbol[symbol] = {"source": SOURCES["IN"], **result}
    return per_symbol, {m: summarise(r, m) for m, r in grouped.items() if r}


def trusted(symbols, per_symbol):
    """The symbols whose prices the official source does not dispute (neither a mismatch nor a series that lags the official file).
    Unverified stocks are kept: no evidence either way."""
    return [s for s in symbols if (per_symbol.get(s) or {}).get("status") not in ("mismatch", "behind")]
