"""Find newly listed stocks so they join the list automatically.

India: the NSE equity list carries each stock's listing date.
US: the Nasdaq Trader symbol directory has no dates, so new symbols are found by comparing
today's list with the previous run's list.
"""
import csv
import io
import re
from datetime import datetime, timedelta

from .fetch import FetchError, http_get

NSE_LIST_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
US_URLS = ("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
           "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt")
INDIA_WINDOW_DAYS = 90
KEEP_DAYS = 180
# No cap on the list: every NSE listing of the last INDIA_WINDOW_DAYS and every new US symbol is followed. (A cap of 40 once dropped Steamhouse.)
# One guard only: a US directory that suddenly shows this many "new" symbols is a broken download, not listings, so the run records nothing.
US_GLITCH_LIMIT = 200

_US_SYMBOL = re.compile(r"^[A-Z]{1,5}$")
_US_SKIP = re.compile(r"\b(warrants?|units?|rights?|acquisition (corp|co)|spac|preferred|notes?|trust|depositary shares)\b", re.I)


def india_new_listings(text, today, days=INDIA_WINDOW_DAYS):
    """Equity-series stocks listed within the last `days` days, newest first."""
    cutoff = today - timedelta(days=days)
    found = []
    for row in csv.DictReader(io.StringIO(text)):
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
        if row.get("SERIES") != "EQ" or not re.match(r"^[A-Z0-9&-]{1,20}$", row.get("SYMBOL", "")):
            continue
        try:
            listed = datetime.strptime(row["DATE OF LISTING"], "%d-%b-%Y").date()
        except (KeyError, ValueError):
            continue
        if cutoff <= listed <= today:
            found.append({"symbol": row["SYMBOL"] + ".NS", "name": row.get("NAME OF COMPANY", row["SYMBOL"]),
                          "market": "IN", "listed": listed.isoformat()})
    found.sort(key=lambda s: s["listed"], reverse=True)
    return found


# EQ is the normal series. BE (traded only for delivery, "trade for trade") and BZ (the exchange's surveillance list) are listed companies too, many of
# them penny stocks, so they are in the app as well; their series is shown. Leaving them out hid about 220 real NSE stocks from the search.
LISTED_SERIES = ("EQ", "BE", "BZ")


def _rows(text):
    for row in csv.DictReader(io.StringIO(text)):
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
        if row.get("SERIES") in LISTED_SERIES and re.match(r"^[A-Z0-9&-]{1,20}$", row.get("SYMBOL", "")) and row.get("NAME OF COMPANY"):
            yield row


def nse_company_names(text):
    """{"SYMBOL.NS": company name} for every listed stock in the NSE equity list (EQ, BE and BZ series)."""
    return {row["SYMBOL"] + ".NS": row["NAME OF COMPANY"] for row in _rows(text)}


SME_LIST_URL = "https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv"


def sme_listings(text):
    """{"SYMBOL.NS": company name} for the stocks on the NSE's SME platform (Emerge). Rights-entitlement lines (symbols and names ending in -RE or
    -RE1 and so on) are not companies and are left out."""
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
        symbol, name = row.get("SYMBOL", ""), row.get("NAME_OF_COMPANY", "")
        if symbol and name and re.match(r"^[A-Z0-9&-]{1,20}$", symbol) and not re.search(r"-RE\d*$", symbol) and not re.search(r"-RE\d*$", name):
            out[symbol + ".NS"] = name
    return out


def nse_series(text):
    """{"SYMBOL.NS": "BE" or "BZ"} for the stocks that are not in the normal EQ series."""
    return {row["SYMBOL"] + ".NS": row["SERIES"] for row in _rows(text) if row["SERIES"] != "EQ"}


def parse_us_directory(text, symbol_col, test_col, etf_col):
    """Common-stock-like symbols from a Nasdaq Trader pipe file. Returns {symbol: name}."""
    out = {}
    lines = [line for line in text.splitlines() if line and not line.startswith("File Creation Time")]
    if not lines:
        return out
    header = lines[0].split("|")
    try:
        s, t, e = header.index(symbol_col), header.index(test_col), header.index(etf_col)
    except ValueError:
        return out
    for line in lines[1:]:
        parts = line.split("|")
        if len(parts) <= max(s, t, e, 1):
            continue
        symbol, name = parts[s].strip(), parts[1].strip()
        if parts[t] != "N" or parts[e] != "N" or not _US_SYMBOL.match(symbol) or _US_SKIP.search(name):
            continue
        out[symbol] = name
    return out


def us_symbols(nasdaq_text, other_text):
    symbols = parse_us_directory(nasdaq_text, "Symbol", "Test Issue", "ETF")
    symbols.update(parse_us_directory(other_text, "ACT Symbol", "Test Issue", "ETF"))
    return symbols


def us_new_listings(current, previous_symbols, today):
    """Symbols not seen on the previous run. The first run only records the list."""
    if not previous_symbols:
        return []
    previous = set(previous_symbols)
    fresh = sorted(s for s in current if s not in previous)
    if len(fresh) > US_GLITCH_LIMIT:
        return []
    return [{"symbol": s, "name": current[s], "market": "US", "listed": today.isoformat()} for s in fresh]


def merge_registry(registry, found, today):
    """Add new finds to the registry, drop entries older than KEEP_DAYS, cap the size."""
    cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
    merged = {e["symbol"]: e for e in registry if e.get("listed", "") >= cutoff}
    for entry in found:
        merged.setdefault(entry["symbol"], entry)
    return sorted(merged.values(), key=lambda e: e["listed"], reverse=True)


def discover(state, today, fetch=http_get):
    """Returns (registry, us_symbol_list). Network failures keep the previous values."""
    registry = state.get("new_listings", [])
    us_list = state.get("us_symbols", [])
    found = []
    try:
        found += india_new_listings(fetch(NSE_LIST_URL).decode("utf-8", "replace"), today)
    except FetchError:
        pass
    try:
        current = us_symbols(*(fetch(url).decode("utf-8", "replace") for url in US_URLS))
        if current:
            found += us_new_listings(current, us_list, today)
            us_list = sorted(current)
    except FetchError:
        pass
    return merge_registry(registry, found, today), us_list
