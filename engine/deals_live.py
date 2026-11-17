"""Block deals as they happen, from the NSE's live block-deal feed.

Why this exists: the app used to read only the NSE's end-of-day bulk and block deal files, which are empty until after the market
has closed (on 17 Nov 2026 the block.csv read "NO RECORDS" while the live feed already listed a deal in Groww for Rs 1,905 crore
from the 08:45 IST window). The live feed carries the stock, window, quantity, price and value, not the buyer and seller: those are
published after the close and shown on the stock page then.

Same rules as the other NSE feeds: one honest request per run for the whole market, never one per stock; a refusal (401 or 403) is
logged and the NSE is left alone for BLOCK_BACKOFF_HOURS; nothing is worked around.
"""
import json
from datetime import datetime, timedelta, timezone

from . import backoff
from .fetch import FetchError, http_get

URL = "https://www.nseindia.com/api/block-deal"
PAGE_URL = "https://www.nseindia.com/market-data/large-deals"
BLOCK_BACKOFF_HOURS = 6
SEEN_LIMIT = 400
IST = timezone(timedelta(hours=5, minutes=30))
WINDOWS = {"Session 1": "morning window (08:45 to 09:00 IST)", "Session 2": "afternoon window (14:05 to 14:20 IST)"}


def _number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def parse_live(payload):
    """The block deals in a live feed answer, as dicts. Rows without a symbol, quantity or price are skipped."""
    out = []
    rows = payload.get("data") if isinstance(payload, dict) else None
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        symbol = (row.get("symbol") or "").strip()
        qty, price = _number(row.get("totalTradedVolume")), _number(row.get("lastPrice"))
        if not symbol or not qty or not price or qty <= 0 or price <= 0:
            continue
        stamp = (row.get("lastUpdateTime") or "").strip()
        try:
            when = datetime.strptime(stamp, "%d-%b-%Y %H:%M:%S")
        except ValueError:
            when = None
        out.append({"symbol": symbol, "session": row.get("session") or "", "date": when.strftime("%Y-%m-%d") if when else None,
                    "time": when.strftime("%H:%M") if when else None, "qty": int(qty), "price": price,
                    "value": _number(row.get("totalTradedValue")) or qty * price, "previous_close": _number(row.get("previousClose")),
                    "pchange": _number(row.get("pchange"))})
    return out


def deal_key(deal):
    return f"{deal['symbol']}|{deal['session']}|{deal['date']}|{deal['qty']}|{deal['price']}"


def fetch_block_deals(now, previous=None, log=print, getter=http_get):
    """{"ok", "deals", "fetched_at", "error", "blocked_until"}. Never raises."""
    previous = previous or {}
    out = {"ok": False, "deals": [], "fetched_at": previous.get("fetched_at"), "checked_at": now.isoformat(timespec="seconds"),
           "error": None, "blocked_until": None}
    held = previous.get("blocked_until")
    try:
        held_until = datetime.fromisoformat(held) if held else None
    except (TypeError, ValueError):
        held_until = None
    if backoff.still_paused(now, held_until, previous.get("failures")):
        out.update(error="HTTP 403: the NSE refuses automated requests (not asked again until the pause ends)", blocked_until=held, failures=previous.get("failures", 1))
        log(f"NSE live block deals: skipped, refused earlier; next attempt after {held}")
        return out
    try:
        deals = parse_live(json.loads(getter(URL, headers={"Accept": "application/json"}, retries=2)))
    except (FetchError, ValueError) as err:
        text = str(err)
        refused = "403" in text or "401" in text
        out["error"] = "HTTP 403: the NSE refuses automated requests" if refused else (text[:140] or "request failed")
        if refused:
            out["blocked_until"], out["failures"] = backoff.refusal_state(now, previous)
        log(f"NSE live block deals failed: {out['error']}")
        return out
    out.update(ok=True, deals=deals, fetched_at=now.isoformat(timespec="seconds"))
    log(f"NSE live block deals: {len(deals)} today")
    return out


def today_ist(now):
    return now.astimezone(IST).strftime("%Y-%m-%d")
