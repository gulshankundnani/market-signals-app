"""IPO calendar and headlines.

US: the Nasdaq IPO calendar lists upcoming, priced and filed deals with price range and size.
India: no free structured IPO feed exists, so only headlines are collected here. The web page
scores an IPO from financials the user enters (see site/ipo.js), because an unlisted company's
financials are not published as machine-readable data.
"""
import json
import re
from datetime import timedelta

from . import news
from .fetch import FetchError, http_get

CALENDAR_URL = "https://api.nasdaq.com/api/ipo/calendar?date={month}"
HEADLINE_QUERIES = [("IN", "IPO price band subscription opens"), ("IN", "IPO GMP listing day"),
                    ("US", "IPO pricing expected this week")]
MAX_DEALS = 40


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


def fetch_ipos(now):
    """Returns {"us": [...], "headlines": [...], "us_ok": bool}. Failures give empty lists, never an exception."""
    deals, ok = [], True
    for months_ahead in (0, 1):
        month = (now.replace(day=1) + timedelta(days=32 * months_ahead)).strftime("%Y-%m")
        try:
            deals += parse_calendar(json.loads(http_get(CALENDAR_URL.format(month=month), headers={"Accept": "application/json"}, retries=2)))
        except (FetchError, ValueError):
            ok = False
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
    return {"us": unique[:MAX_DEALS], "headlines": ordered, "us_ok": ok}
