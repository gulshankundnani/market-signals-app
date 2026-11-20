"""Historical financial statements and quarterly results, read automatically.

Source: Yahoo Finance's fundamentals time series (query1.finance.yahoo.com/ws/fundamentals-timeseries), the same cookie-and-crumb session the
fundamentals use. One request per stock returns every quarterly and annual figure asked for, each with its period end date and currency.
Tested on 2026-11-20 with TCS: 5 reported quarters and 4 fiscal years for revenue, operating income, EBITDA, net income and diluted EPS.

What is kept for a stock: the newest QUARTERS quarters and YEARS fiscal years of each figure, the currency, the time of the read, and a short
summary of the newest quarter (change on the same quarter a year before, and on the quarter before). A change is worked out only when the
comparison quarter really is there: Yahoo's list can skip a quarter (TCS had no September 2025 figure), and a growth rate across a gap would be
wrong. A missing figure stays missing, never zero.

When a stock is read again: never read -> now; newest quarter older than about 100 days -> every two days (results are due); otherwise weekly.
"""
import json
import time
from datetime import date, datetime, timedelta, timezone

from .fetch import FetchError, http_get

URL = ("https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{symbol}?symbol={symbol}&type={types}"
       "&merge=false&period1={start}&period2={end}")
QUARTERLY = {"revenue": "quarterlyTotalRevenue", "operating_income": "quarterlyOperatingIncome", "ebitda": "quarterlyEBITDA",
             "net_income": "quarterlyNetIncome", "eps": "quarterlyDilutedEPS"}
ANNUAL = {"revenue": "annualTotalRevenue", "operating_income": "annualOperatingIncome", "ebitda": "annualEBITDA", "net_income": "annualNetIncome",
          "eps": "annualDilutedEPS", "equity": "annualStockholdersEquity", "total_debt": "annualTotalDebt",
          "operating_cash_flow": "annualOperatingCashFlow", "free_cash_flow": "annualFreeCashFlow"}
QUARTERS = 8
YEARS = 6
VERSION = 1
WEEKLY = 7 * 86400
DUE_SOON = 2 * 86400
RESULTS_DUE_AFTER_DAYS = 100


def _num(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value == value else None


def parse(payload):
    """({"quarterly": {name: [{"date", "value"}]}, "annual": {...}}, currency) from one time-series answer. Oldest first, newest last."""
    wanted = {**{v: ("quarterly", k) for k, v in QUARTERLY.items()}, **{v: ("annual", k) for k, v in ANNUAL.items()}}
    out, currency = {"quarterly": {}, "annual": {}}, None
    results = (((payload or {}).get("timeseries") or {}).get("result")) if isinstance(payload, dict) else None
    for item in results if isinstance(results, list) else []:
        types = ((item or {}).get("meta") or {}).get("type") or []
        kind = types[0] if types else None
        if kind not in wanted:
            continue
        period, name = wanted[kind]
        rows = []
        for entry in item.get(kind) or []:
            if not isinstance(entry, dict):
                continue
            value = _num(((entry.get("reportedValue") or {}).get("raw")))
            when = entry.get("asOfDate")
            if value is None or not isinstance(when, str):
                continue
            currency = currency or entry.get("currencyCode")
            rows.append({"date": when, "value": value})
        rows.sort(key=lambda r: r["date"])
        if rows:
            out[period][name] = rows[-(QUARTERS if period == "quarterly" else YEARS):]
    return out, currency


def _find(rows, target, tolerance_days):
    """The row whose date is within tolerance_days of the target date, or None."""
    best = None
    for row in rows:
        gap = abs((date.fromisoformat(row["date"]) - target).days)
        if gap <= tolerance_days and (best is None or gap < best[0]):
            best = (gap, row)
    return best[1] if best else None


def _change(now_value, then_value):
    if now_value is None or then_value is None or then_value == 0:
        return None
    base = abs(then_value)
    return round((now_value - then_value) / base * 100, 1)


def summarise(quarterly):
    """The newest quarter and how it compares: {"latest_quarter", "yoy": {name: pct}, "qoq": {name: pct}, "margin_pct", "gaps"}. Empty when there is no quarter."""
    newest = max((rows[-1]["date"] for rows in quarterly.values() if rows), default=None)
    if not newest:
        return {}
    end = date.fromisoformat(newest)
    year_ago = date(end.year - 1, end.month, min(end.day, 28)) if end.month != 2 else date(end.year - 1, 2, 28)
    yoy, qoq, gaps = {}, {}, []
    for name, rows in quarterly.items():
        now_row = _find(rows, end, 10)
        if not now_row:
            continue
        before = _find(rows, year_ago, 15)
        prior = _find(rows, end - timedelta(days=91), 15)
        yoy_change, qoq_change = _change(now_row["value"], before["value"] if before else None), _change(now_row["value"], prior["value"] if prior else None)
        if yoy_change is not None:
            yoy[name] = yoy_change
        if qoq_change is not None:
            qoq[name] = qoq_change
        if before is None:
            gaps.append(f"{name}: no figure for the quarter a year before")
    revenue, profit = _find(quarterly.get("revenue", []), end, 10), _find(quarterly.get("net_income", []), end, 10)
    margin = round(profit["value"] / revenue["value"] * 100, 1) if revenue and profit and revenue["value"] else None
    return {"latest_quarter": newest, "yoy": yoy, "qoq": qoq, "net_margin_pct": margin, "gaps": gaps}


def fetch_statements(session, symbol, now_ts=None, getter=http_get):
    """One stock's statements, or {} when Yahoo has none or does not answer (never an exception, never zeros)."""
    now_ts = int(now_ts or time.time())
    types = ",".join(list(QUARTERLY.values()) + list(ANNUAL.values()))
    url = URL.format(symbol=symbol, types=types, start=1577836800, end=now_ts)
    try:
        payload = json.loads(getter(url, opener=session.opener, retries=1, timeout=25))
    except (FetchError, ValueError):
        return {}
    data, currency = parse(payload)
    if not data["quarterly"] and not data["annual"]:
        return {}
    summary = summarise(data["quarterly"])
    return {"v": VERSION, "currency": currency, "quarterly": data["quarterly"], "annual": data["annual"], "latest_quarter": summary.get("latest_quarter"),
            "summary": summary, "fetched_at": datetime.fromtimestamp(now_ts, timezone.utc).isoformat(timespec="seconds"),
            "source": "Yahoo Finance fundamentals time series"}


def due(entry, now_ts):
    """Whether a stock's statements should be read again. entry: the cached {"ts", "data"} or None."""
    if not entry or (entry.get("data") or {}).get("v") != VERSION:
        return True
    age = now_ts - entry.get("ts", 0)
    newest = (entry.get("data") or {}).get("latest_quarter")
    try:
        quarter_age = (datetime.fromtimestamp(now_ts, timezone.utc).date() - date.fromisoformat(newest)).days if newest else None
    except ValueError:
        quarter_age = None
    if quarter_age is None or quarter_age > RESULTS_DUE_AFTER_DAYS:
        return age >= DUE_SOON            # results are or may be due: look often
    return age >= WEEKLY


def due_record(statements, now_ts):
    """Whether a stock record's attached statements dict is due for a new read (the same rule as due(), for a record rather than the cache)."""
    if not statements:
        return True
    try:
        ts = int(datetime.fromisoformat(statements["fetched_at"]).timestamp())
    except (KeyError, TypeError, ValueError):
        return True
    return due({"ts": ts, "data": statements}, now_ts)


def sync(records, cache, now_ts, session_factory, per_run=60, fetcher=fetch_statements, sleep=time.sleep):
    """Read the statements that are due, at most per_run stocks a run (oldest first), and attach record["statements"] to every record.

    cache: {symbol: {"ts", "data"}} from the previous run. Returns the cache to keep. A failed read keeps the earlier statements, so
    a stock never loses its figures because Yahoo had a bad moment."""
    cache = dict(cache or {})
    todo = sorted((r["symbol"] for r in records if due(cache.get(r["symbol"]), now_ts)), key=lambda s: (cache.get(s) or {}).get("ts", 0))[:per_run]
    if todo:
        session = session_factory()
        if session.start():
            for symbol in todo:
                data = fetcher(session, symbol, now_ts)
                if data:
                    cache[symbol] = {"ts": now_ts, "data": data}
                elif symbol in cache:
                    cache[symbol] = {**cache[symbol], "ts": now_ts - WEEKLY + DUE_SOON}      # try again in two days
                sleep(0.15)
    for rec in records:
        entry = cache.get(rec["symbol"])
        if entry and entry.get("data"):
            rec["statements"] = entry["data"]
    live = {r["symbol"] for r in records}
    return {s: v for s, v in cache.items() if s in live}
