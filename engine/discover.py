"""Find newly listed stocks so they join the list automatically.

India: the NSE equity list carries each stock's listing date.
"""
import csv
import io
import re
from datetime import datetime, timedelta

from .fetch import FetchError, http_get

NSE_LIST_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
INDIA_WINDOW_DAYS = 90
KEEP_DAYS = 180
# No cap on the list: every NSE listing of the last INDIA_WINDOW_DAYS is followed. (A cap of 40 once dropped Steamhouse.)


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


def listing_dates(text, sme_format=False):
    """{"SYMBOL.NS": "YYYY-MM-DD"} from the NSE list's date of listing (main list "17-SEP-2026", SME list "16-Nov-26")."""
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
        raw = row.get("DATE_OF_LISTING" if sme_format else "DATE OF LISTING", "")
        for fmt in ("%d-%b-%Y", "%d-%b-%y"):
            try:
                out[row.get("SYMBOL", "") + ".NS"] = datetime.strptime(raw.title(), fmt).date().isoformat()
                break
            except ValueError:
                continue
    return out


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





def merge_registry(registry, found, today):
    """Add new finds to the registry, drop entries older than KEEP_DAYS, cap the size."""
    cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
    merged = {e["symbol"]: e for e in registry if e.get("listed", "") >= cutoff}
    for entry in found:
        merged.setdefault(entry["symbol"], entry)
    return sorted(merged.values(), key=lambda e: e["listed"], reverse=True)


def discover(state, today, fetch=http_get):
    """Returns the registry of new NSE listings. Network failures keep the previous values."""
    registry = [e for e in state.get("new_listings", []) if e.get("market", "IN") == "IN"]       # US entries saved by earlier versions are dropped
    found = []
    try:
        found += india_new_listings(fetch(NSE_LIST_URL).decode("utf-8", "replace"), today)
    except FetchError:
        pass
    return merge_registry(registry, found, today)
