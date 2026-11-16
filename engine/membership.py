"""Index membership dates, used to keep a backtest from trading a stock before it was in the index.

What this does and does not do about survivorship bias (see README, "Survivorship bias"):
  * The stock list is today's list. Companies that were removed, delisted or went bankrupt are not in it, and free sources
    do not give their prices (Yahoo has none for most delisted tickers, and a few tickers have been reused by new companies,
    so their history is not the old company's). That part of the bias cannot be removed with free data.
  * What can be done is to stop a backtest from using the years BEFORE a stock joined the index. A company is usually added
    to an index after it has already grown, so trading it from before its addition flatters the result. config/membership.json
    holds each stock's addition date where a public source states it (S&P 500: the "Date added" column of Wikipedia's
    constituents table). Where no date is known the stock is tested from the start of its history and that is stated.
  * India: the NSE publishes only the current Nifty lists, with no addition dates in machine-readable form, so no Indian
    stock is date-limited. The Indian results therefore carry the full survivorship and inclusion bias.
"""
import re

SOURCE_US = "Wikipedia, List of S&P 500 companies (Date added column)"


def _cells(row):
    return [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.S)]


def parse_sp500_added(html):
    """{ticker: "YYYY-MM-DD"} from the constituents table of Wikipedia's S&P 500 list. Rows without a full date are skipped."""
    table = re.search(r'<table[^>]*id="constituents"[^>]*>(.*?)</table>', html or "", flags=re.S)
    if not table:
        return {}
    out, header = {}, None
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table.group(1), flags=re.S):
        cells = _cells(row)
        if header is None:
            if "Symbol" in cells and "Date added" in cells:
                header = (cells.index("Symbol"), cells.index("Date added"))
            continue
        if len(cells) <= max(header):
            continue
        symbol, added = cells[header[0]], cells[header[1]]
        if symbol and re.fullmatch(r"\d{4}-\d{2}-\d{2}", added):
            out[symbol.replace(".", "-")] = added          # Yahoo writes BRK.B as BRK-B
    return out


def first_eligible_index(dates, member_from):
    """Index of the first bar on or after the date the stock joined the index, or 0 when the date is unknown."""
    if not member_from:
        return 0
    for i, day in enumerate(dates):
        if day >= member_from:
            return i
    return len(dates)


def lookup(membership, market, symbol):
    """The addition date recorded for a stock, or None."""
    return ((((membership or {}).get(market) or {}).get(symbol)) or {}).get("from")
