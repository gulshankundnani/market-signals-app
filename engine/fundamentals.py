"""Company fundamentals from Yahoo quoteSummary. Missing values stay None."""
import urllib.parse
from datetime import datetime, timezone

from .fetch import FetchError

VERSION = 3          # raised when fields are added, so answers cached by an older version are fetched again
MODULES = "summaryDetail,defaultKeyStatistics,financialData,assetProfile,price,calendarEvents"
URL = "https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}?modules=" + MODULES


def _raw(node, *path):
    """Walk nested dicts and unwrap Yahoo's {"raw": x} values. Returns None when absent."""
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    if isinstance(node, dict):
        node = node.get("raw")
    return node if isinstance(node, (int, float, str)) and not isinstance(node, bool) else None


def _day(node):
    """A Yahoo {"raw": unix_time} value as an ISO date, or None."""
    raw = node.get("raw") if isinstance(node, dict) else None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw <= 0:
        return None
    return datetime.fromtimestamp(raw, timezone.utc).strftime("%Y-%m-%d")


def parse_events(result):
    """The company's dates from Yahoo's calendarEvents: results dates (one, or the start and end of a window), the
    ex-dividend date and the dividend payment date. These are Yahoo's figures and may be estimates."""
    calendar = (result or {}).get("calendarEvents") or {}
    earnings = [d for d in (_day(x) for x in ((calendar.get("earnings") or {}).get("earningsDate") or [])) if d]
    return {"earnings": sorted(set(earnings)), "ex_dividend": _day(calendar.get("exDividendDate")),
            "dividend_pay": _day(calendar.get("dividendDate"))}


def parse_summary(payload):
    result = (((payload or {}).get("quoteSummary") or {}).get("result") or [None])[0]
    if not result:
        return {}
    debt = _raw(result, "financialData", "debtToEquity")
    return {
        "most_recent_quarter": _day(((result.get("defaultKeyStatistics") or {}).get("mostRecentQuarter"))),
        "last_fiscal_year_end": _day(((result.get("defaultKeyStatistics") or {}).get("lastFiscalYearEnd"))),
        "sector": _raw(result, "assetProfile", "sector"),
        "industry": _raw(result, "assetProfile", "industry"),
        "market_cap": _raw(result, "price", "marketCap") or _raw(result, "summaryDetail", "marketCap"),
        "pe": _raw(result, "summaryDetail", "trailingPE"),
        "forward_pe": _raw(result, "summaryDetail", "forwardPE"),
        "pb": _raw(result, "defaultKeyStatistics", "priceToBook"),
        "roe": _raw(result, "financialData", "returnOnEquity"),
        "debt_to_equity": debt / 100 if debt is not None else None,
        "profit_margin": _raw(result, "financialData", "profitMargins"),
        "revenue_growth": _raw(result, "financialData", "revenueGrowth"),
        "earnings_growth": _raw(result, "financialData", "earningsGrowth"),
        "payout_ratio": _raw(result, "summaryDetail", "payoutRatio"),
        # Added in version 2, all as reported by Yahoo for the last twelve months (the amounts are in the company's reporting currency)
        "ebitda": _raw(result, "financialData", "ebitda"),
        "ebitda_margin": _raw(result, "financialData", "ebitdaMargins"),
        "enterprise_value": _raw(result, "defaultKeyStatistics", "enterpriseValue"),
        "ev_ebitda": _raw(result, "defaultKeyStatistics", "enterpriseToEbitda"),
        "ev_revenue": _raw(result, "defaultKeyStatistics", "enterpriseToRevenue"),
        "eps": _raw(result, "defaultKeyStatistics", "trailingEps"),
        "book_value": _raw(result, "defaultKeyStatistics", "bookValue"),
        "peg": _raw(result, "defaultKeyStatistics", "pegRatio"),
        "price_to_sales": _raw(result, "summaryDetail", "priceToSalesTrailing12Months"),
        "revenue": _raw(result, "financialData", "totalRevenue"),
        "operating_margin": _raw(result, "financialData", "operatingMargins"),
        "gross_margin": _raw(result, "financialData", "grossMargins"),
        "free_cash_flow": _raw(result, "financialData", "freeCashflow"),
        "operating_cash_flow": _raw(result, "financialData", "operatingCashflow"),
        "current_ratio": _raw(result, "financialData", "currentRatio"),
        "roa": _raw(result, "financialData", "returnOnAssets"),
        "total_debt": _raw(result, "financialData", "totalDebt"),
        "total_cash": _raw(result, "financialData", "totalCash"),
        "events": parse_events(result),
        "v": VERSION,
    }


def fetch_fundamentals(session, symbol):
    """Returns a dict of fundamentals, or {} when Yahoo has none for this symbol."""
    try:
        return parse_summary(session.get_json(URL.format(symbol=urllib.parse.quote(symbol))))
    except (FetchError, ValueError):
        return {}
