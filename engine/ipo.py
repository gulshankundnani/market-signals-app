"""IPO calendar and headlines.

US: the Nasdaq IPO calendar lists upcoming, priced and filed deals with price range and size.
India: no free structured IPO feed exists, so only headlines are collected here. The web page
scores an IPO from financials the user enters (see site/ipo.js), because an unlisted company's
financials are not published as machine-readable data.
"""
import json
import re
from datetime import datetime, timedelta, timezone

from . import news
from .fetch import FetchError, http_get

CALENDAR_URL = "https://api.nasdaq.com/api/ipo/calendar?date={month}"
HEADLINE_QUERIES = [("IN", "IPO price band subscription opens"), ("IN", "IPO GMP listing day"),
                    ("US", "IPO pricing expected this week")]
MAX_DEALS = 40
BLOCK_BACKOFF_HOURS = 6      # after the source refuses us, stay away this long instead of asking again every run
PAGE_URL = "https://www.nasdaq.com/market-activity/ipos"


def _number(text):
    cleaned = re.sub(r"[^0-9.]", "", text or "")
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def _price_range(text):
    """'10.00-12.00' -> (10.0, 12.0). '10.00' -> (10.0, 10.0). Anything else -> (None, None)."""
    parts = [p for p in re.split(r"\s*-\s*", (text or "").strip()) if p]
    values = [_number(p) for p in parts[:2]]
    if not values or values[0] is None:
        return None, None
    return values[0], values[-1] if values[-1] is not None else values[0]


def parse_calendar(payload):
    """Normalise a Nasdaq calendar response into a list of deals."""
    data = (payload or {}).get("data") or {}
    sections = (
        ("upcoming", ((data.get("upcoming") or {}).get("upcomingTable") or {}).get("rows"), "expectedPriceDate"),
        ("priced", (data.get("priced") or {}).get("rows"), "pricedDate"),
        ("filed", (data.get("filed") or {}).get("rows"), "filedDate"),
    )
    deals = []
    for status, rows, date_key in sections:
        for row in rows or []:
            name = (row.get("companyName") or "").strip()
            if not name:
                continue
            low, high = _price_range(row.get("proposedSharePrice"))
            deals.append({
                "status": status, "symbol": (row.get("proposedTickerSymbol") or "").strip(), "name": name,
                "exchange": (row.get("proposedExchange") or "").strip(), "price_low": low, "price_high": high,
                "shares": _number(row.get("sharesOffered")), "amount": _number(row.get("dollarValueOfSharesOffered")),
                "date": (row.get(date_key) or "").strip(),
            })
    return deals


def describe_failure(error):
    """(short reason, refused) for a failed calendar request. refused is True when the source answered 401/403, which means
    it does not allow this kind of automated access: asking again will not help, and the answer is never worked around."""
    text = str(error)
    if "403" in text or "401" in text:
        return "HTTP 403 Access Denied: the source refuses automated requests", True
    if "429" in text:
        return "HTTP 429: too many requests", False
    if isinstance(error, ValueError):
        return "the answer was not valid JSON", False
    return text[:160], False


def fetch_ipos(now, previous=None, log=print):
    """Returns {"us": [...], "headlines": [...], "us_ok": bool, "us_error": str or None, "us_blocked_until": iso or None}.

    Failures give empty lists, never an exception, and each one is logged with its reason. When the source refuses
    automated access it is left alone for BLOCK_BACKOFF_HOURS rather than asked again on every run."""
    deals, ok, error, blocked_until = [], True, None, None
    held = ((previous or {}).get("us_blocked_until"))
    try:
        held_until = datetime.fromisoformat(held) if held else None
    except (TypeError, ValueError):
        held_until = None
    if held_until and held_until > now:
        ok, error, blocked_until = False, "HTTP 403 Access Denied: the source refuses automated requests (not asked again until the pause ends)", held
        log(f"US IPO calendar: skipped, the source refused us earlier; next attempt after {held}")
    else:
        for months_ahead in (0, 1):
            month = (now.replace(day=1) + timedelta(days=32 * months_ahead)).strftime("%Y-%m")
            try:
                deals += parse_calendar(json.loads(http_get(CALENDAR_URL.format(month=month), headers={"Accept": "application/json"}, retries=2)))
            except (FetchError, ValueError) as err:
                ok = False
                error, refused = describe_failure(err)
                log(f"US IPO calendar for {month} failed: {error} ({str(err)[:120]})")
                if refused:
                    blocked_until = (now + timedelta(hours=BLOCK_BACKOFF_HOURS)).isoformat(timespec="seconds")
                    break
    seen, unique = set(), []
    for deal in deals:
        key = (deal["status"], deal["symbol"] or deal["name"])
        if key not in seen:
            seen.add(key)
            unique.append(deal)
    headlines = {}
    for market, query in HEADLINE_QUERIES:
        try:
            for item in news.fetch_news(query, market, now, limit=6):
                headlines.setdefault(item["title"], {**item, "market": market})
        except FetchError:
            continue
    ordered = sorted(headlines.values(), key=lambda i: (i["age_min"] is None, i["age_min"] or 0))[:16]
    return {"us": unique[:MAX_DEALS], "headlines": ordered, "us_ok": ok, "us_error": error, "us_blocked_until": blocked_until}


def deal_date(text):
    """A calendar date such as '11/19/2026' as a date, or None."""
    try:
        return datetime.strptime((text or "").strip(), "%m/%d/%Y").date()
    except ValueError:
        return None
