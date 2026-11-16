"""End-of-day data published by the NSE: delivery percentage, trade counts and bulk/block deals.

The NSE does not publish its order book (bid and ask depth) for free, so this is the
closest exchange-sourced evidence of real buying and selling.
"""
import csv
import io
from datetime import datetime, timedelta

from .fetch import FetchError, http_get

BHAVCOPY_URL = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{ddmmyyyy}.csv"
DEAL_URLS = {
    "bulk": "https://nsearchives.nseindia.com/content/equities/bulk.csv",
    "block": "https://nsearchives.nseindia.com/content/equities/block.csv",
}
KEEP_DATES = 20
WANT_DATES = 10


def _rows(text):
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        yield {(k or "").strip(): (v or "").strip() for k, v in row.items()}


def _number(text):
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def parse_bhavcopy(text):
    """Returns (iso_date, {symbol: {"deliv_pct", "trades", "volume"}}) for EQ-series stocks."""
    out, day = {}, None
    for row in _rows(text):
        if row.get("SERIES") != "EQ":
            continue
        try:
            day = day or datetime.strptime(row["DATE1"], "%d-%b-%Y").strftime("%Y-%m-%d")
        except (KeyError, ValueError):
            continue
        pct = _number(row.get("DELIV_PER", ""))
        if pct is None or not 0 <= pct <= 100:
            continue
        out[row["SYMBOL"]] = {
            "close": _number(row.get("CLOSE_PRICE", "")),
            "deliv_pct": pct,
            "trades": _number(row.get("NO_OF_TRADES", "")),
            "volume": _number(row.get("TTL_TRD_QNTY", "")),
        }
    return day, out


def fetch_bhavcopy(day):
    """Download one trading day. Returns None on holidays or when not yet published."""
    url = BHAVCOPY_URL.format(ddmmyyyy=day.strftime("%d%m%Y"))
    try:
        text = http_get(url, retries=2).decode("utf-8", "replace")
    except FetchError:
        return None
    iso, rows = parse_bhavcopy(text)
    return (iso, rows) if iso and rows else None


def sync_delivery(history, today, symbols, fetcher=fetch_bhavcopy):
    """Add missing recent trading days to history {iso_date: {symbol: [pct, trades]}}.

    Walks back over weekdays until WANT_DATES days are held, fetching only days not yet stored.
    Returns the updated history, trimmed to KEEP_DATES days and to the symbols we track.
    """
    wanted = set(symbols)
    history = {d: dict(v) for d, v in history.items()}
    day, looked = today, 0
    while len([d for d in history if d <= today.strftime("%Y-%m-%d")]) < WANT_DATES and looked < 18:
        looked += 1
        if day.weekday() < 5 and day.strftime("%Y-%m-%d") not in history:
            fetched = fetcher(day)
            if fetched:
                iso, rows = fetched
                history[iso] = {s: [r["deliv_pct"], r["trades"]] for s, r in rows.items() if s in wanted}
        day -= timedelta(days=1)
    for old in sorted(history)[:-KEEP_DATES]:
        del history[old]
    return history


def sync_closes(history, today, symbols, fetcher=fetch_bhavcopy, want=6):
    """Official closing prices {iso_date: {symbol: close}} for the latest sessions, from the NSE end-of-day file.

    Fetches only days not held yet (at most `want` days are kept up to date) and trims to the symbols we track."""
    wanted = set(symbols)
    history = {d: dict(v) for d, v in (history or {}).items() if isinstance(v, dict)}
    day, looked = today, 0
    while len([d for d in history if d <= today.strftime("%Y-%m-%d")]) < want and looked < 14:
        looked += 1
        if day.weekday() < 5 and day.strftime("%Y-%m-%d") not in history:
            fetched = fetcher(day)
            if fetched:
                iso, rows = fetched
                history[iso] = {s: r["close"] for s, r in rows.items() if s in wanted and r.get("close")}
        day -= timedelta(days=1)
    for old in sorted(history)[:-10]:
        del history[old]
    return history


def delivery_view(series, day_change_pct):
    """Interpret delivery % for one stock. series: [(iso_date, pct)] oldest first."""
    if len(series) < 3:
        return None
    latest_date, latest = series[-1]
    previous = [p for _, p in series[:-1]][-9:]
    average = sum(previous) / len(previous)
    if average <= 0:
        return None
    ratio = latest / average
    if ratio >= 1.2 and day_change_pct is not None and day_change_pct > 0:
        tone, text = "pos", "High delivery on a rising day: real buyers are holding the shares (accumulation)"
    elif ratio >= 1.2 and day_change_pct is not None and day_change_pct < 0:
        tone, text = "neg", "High delivery on a falling day: holders are selling out (distribution)"
    elif ratio <= 0.8:
        tone, text = "neutral", "Low delivery: mostly short-term trading, weaker conviction"
    else:
        tone, text = "neutral", "Delivery is near its usual level"
    return {"date": latest_date, "latest": latest, "average": average, "ratio": ratio, "tone": tone, "text": text}


def parse_deals(text, kind):
    """Bulk or block deals as dicts. Rows with bad numbers are skipped."""
    deals = []
    for row in _rows(text):
        qty = _number(row.get("Quantity Traded", ""))
        price = _number(row.get("Trade Price / Wght. Avg. Price", ""))
        side = row.get("Buy/Sell", "").upper()
        if not qty or not price or side not in ("BUY", "SELL") or not row.get("Symbol"):
            continue
        try:
            day = datetime.strptime(row["Date"], "%d-%b-%Y").strftime("%Y-%m-%d")
        except (KeyError, ValueError):
            continue
        deals.append({"kind": kind, "date": day, "symbol": row["Symbol"], "client": row.get("Client Name", ""),
                      "side": side, "qty": qty, "price": price})
    return deals


def summarise_deals(deals, symbols):
    """Per-symbol net institutional-size buying from the latest deal day."""
    wanted = set(symbols)
    out = {}
    for deal in deals:
        if deal["symbol"] not in wanted:
            continue
        entry = out.setdefault(deal["symbol"], {"date": deal["date"], "buy_value": 0.0, "sell_value": 0.0, "deals": []})
        value = deal["qty"] * deal["price"]
        entry["buy_value" if deal["side"] == "BUY" else "sell_value"] += value
        entry["date"] = max(entry["date"], deal["date"])
        entry["deals"].append({"kind": deal["kind"], "client": deal["client"], "side": deal["side"],
                               "qty": deal["qty"], "price": deal["price"]})
    for entry in out.values():
        entry["deals"] = sorted(entry["deals"], key=lambda d: -d["qty"] * d["price"])[:5]
        entry["net_value"] = entry["buy_value"] - entry["sell_value"]
    return out


def fetch_deals():
    deals = []
    for kind, url in DEAL_URLS.items():
        try:
            deals += parse_deals(http_get(url, retries=2).decode("utf-8", "replace"), kind)
        except FetchError:
            continue
    return deals
