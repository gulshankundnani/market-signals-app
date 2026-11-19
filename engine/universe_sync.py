"""Keep the curated stock list up to date by itself: once a day, from 10:00 IST, every current Nifty 200 stock the app does not yet
analyse is added (full readings and backtests), taking its name and sector from the NSE's own Nifty 200 list.

Existing entries and their tags are never changed or removed (a stock that leaves the index keeps its history). The added stocks are kept in
the saved state and merged into the list at every run, so config/universe.json is never rewritten by the automatic run. A failed download is
not counted as done: it is tried again after RETRY_MINUTES. A refusal (HTTP 401/403) is never worked around; the same retry spacing applies.
The same functions serve `python tools/update_universe.py`, which writes the file by hand.
"""
import csv
import io
from datetime import datetime, timedelta, timezone

from . import wide
from .fetch import FetchError, http_get

IST = timezone(timedelta(hours=5, minutes=30))
URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv"
START_MINUTE = 10 * 60          # the sync runs from 10:00 IST
RETRY_MINUTES = 60
SECTORS = {"Financial Services": "Financials", "Capital Goods": "Industrials", "Construction": "Industrials", "Services": "Industrials",
           "Healthcare": "Pharma and Health", "Automobile and Auto Components": "Auto", "Fast Moving Consumer Goods": "FMCG",
           "Metals & Mining": "Metals and Mining", "Consumer Services": "Consumer", "Consumer Durables": "Consumer", "Information Technology": "IT",
           "Power": "Power", "Oil Gas & Consumable Fuels": "Energy", "Realty": "Real Estate", "Chemicals": "Materials",
           "Construction Materials": "Materials", "Textiles": "Materials", "Telecommunication": "Telecom", "Media Entertainment & Publication": "Consumer",
           "Diversified": "Industrials", "Forest Materials": "Materials"}


def sector_of(industry, name):
    """The app's sector for an NSE industry. Banks are split out of Financial Services by name, as the curated list does."""
    if industry == "Financial Services" and "bank" in (name or "").lower():
        return "Banks"
    return SECTORS.get(industry, "Other")


def parse_nifty(text):
    """[(symbol.NS, short name, sector)] from the NSE index constituent CSV."""
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        symbol = (row.get("Symbol") or "").strip()
        if symbol and (row.get("Series") or "EQ").strip() == "EQ":
            name = (row.get("Company Name") or symbol).strip()
            out.append((symbol + ".NS", wide.short_name(name), sector_of((row.get("Industry") or "").strip(), name)))
    return out


def merge(stocks, found, extras=()):
    """(new stocks list, added symbols): found entries that are not already present, then the extras."""
    known = {s[0] for s in stocks}
    added = []
    for symbol, name, sector in list(found) + list(extras):
        if symbol not in known:
            stocks.append([symbol, name, "IN", sector, []])
            known.add(symbol)
            added.append(symbol)
    return stocks, added


def due(now, state):
    """True when it is 10:00 IST or later, the list has not been synced today (IST date) and the last attempt is not too recent."""
    local = now.astimezone(IST)
    if local.hour * 60 + local.minute < START_MINUTE or (state or {}).get("date") == local.strftime("%Y-%m-%d"):
        return False
    try:
        tried = datetime.fromisoformat(state["tried_at"])
    except (KeyError, TypeError, ValueError):
        return True
    return (now - tried).total_seconds() >= RETRY_MINUTES * 60


def sync(now, state, stocks, fetcher=http_get, log=print):
    """(stocks with the added ones, state to keep, symbols added today). Never raises; the curated list is only ever extended."""
    state = dict(state or {})
    extra = [e for e in state.get("extra", []) if isinstance(e, list) and len(e) == 3]
    stocks, _ = merge(stocks, [], [tuple(e) for e in extra])
    added = []
    if due(now, state):
        state["tried_at"] = now.isoformat(timespec="seconds")
        try:
            found = parse_nifty(fetcher(URL, timeout=40).decode("utf-8", "replace"))
        except FetchError as err:
            log(f"Stock list update failed ({str(err)[-60:]}); tried again in {RETRY_MINUTES} minutes")
            found = None
        if found:
            known = {s[0] for s in stocks}
            new = [f for f in found if f[0] not in known]
            stocks, added = merge(stocks, new)
            extra += [list(f) for f in new if f[0] in added]
            state["date"] = now.astimezone(IST).strftime("%Y-%m-%d")
            log(f"Stock list update: {len(added)} new Nifty 200 stocks added" + (f" ({', '.join(added)})" if added else ""))
        elif found is not None:
            log("Stock list update: the NSE list came back empty; tried again later")
    state["extra"] = extra
    return stocks, state, added
