"""End-of-day data published by the NSE: delivery percentage, trade counts and bulk/block deals.

The NSE does not publish its order book (bid and ask depth) for free, so this is the
closest exchange-sourced evidence of real buying and selling.
"""
import csv
import io
from datetime import datetime, timedelta

from .fetch import FetchError, http_get

UDIFF_URL = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip"
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


def parse_udiff(text):
    """Returns (iso_date, {symbol: {"close", "volume", "open", "high", "low"}}) for EQ-series stocks in the NSE's UDiFF end-of-day file.

    This file appears about 16:30 IST, seven hours before the full bhavcopy with delivery data (about 23:55 IST); on 2026-11-19 its closes and
    volumes were identical to the full file's on all 13,782 EQ rows over five days."""
    out, day = {}, None
    for row in _rows(text):
        if row.get("SctySrs") != "EQ" or not row.get("TckrSymb"):
            continue
        close = _number(row.get("ClsPric", ""))
        if not close or close <= 0:
            continue
        day = day or row.get("TradDt") or None
        out[row["TckrSymb"]] = {"close": close, "volume": _number(row.get("TtlTradgVol", "")), "open": _number(row.get("OpnPric", "")),
                                "high": _number(row.get("HghPric", "")), "low": _number(row.get("LwPric", ""))}
    return day, out


def fetch_udiff(day):
    """One trading day from the UDiFF file. None on holidays, when not yet published, or when the answer is not the expected zip."""
    import zipfile
    try:
        raw = http_get(UDIFF_URL.format(yyyymmdd=day.strftime("%Y%m%d")), retries=2)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            text = archive.read(archive.namelist()[0]).decode("utf-8", "replace")
    except (FetchError, zipfile.BadZipFile, KeyError, IndexError):
        return None
    iso, rows = parse_udiff(text)
    return (iso, rows) if iso and rows else None


def fetch_eod(day):
    """The day's official closes: the UDiFF file first (earliest), the full bhavcopy if that is missing."""
    return fetch_udiff(day) or fetch_bhavcopy(day)


def fetch_bhavcopy(day):
    """Download one trading day. Returns None on holidays or when not yet published."""
    url = BHAVCOPY_URL.format(ddmmyyyy=day.strftime("%d%m%Y"))
    try:
        text = http_get(url, retries=2).decode("utf-8", "replace")
    except FetchError:
        return None
    iso, rows = parse_bhavcopy(text)
    return (iso, rows) if iso and rows else None


def _catch_up(history, today, want, fetcher, store, max_days):
    """Fill {iso_date: ...} with the newest published days, then back-fill until `want` days are held.

    Every weekday newer than the newest day already held is tried on every run (not-yet-published and holidays simply return None).
    Stopping as soon as `want` days were held froze the history on the day it first filled: the cross-check then kept comparing old
    sessions only and could never see a newer one (it was stuck on 2026-11-13 while 2026-11-19 had been published)."""
    today_iso = today.strftime("%Y-%m-%d")
    newest = max((d for d in history if d <= today_iso), default=None)
    day = today
    for _ in range(max_days):
        iso = day.strftime("%Y-%m-%d")
        if day.weekday() < 5 and iso not in history:
            held = len([d for d in history if d <= today_iso])
            if held < want or (newest is not None and iso > newest):
                fetched = fetcher(day)
                if fetched:
                    history[fetched[0]] = store(fetched[1])
        day -= timedelta(days=1)
    return history


def sync_delivery(history, today, symbols, fetcher=fetch_bhavcopy):
    """Add missing recent trading days to history {iso_date: {symbol: [pct, trades]}}, newest first, then back-fill to WANT_DATES days.
    Returns the updated history, trimmed to KEEP_DATES days and to the symbols we track."""
    wanted = set(symbols)
    history = {d: dict(v) for d, v in history.items()}
    _catch_up(history, today, WANT_DATES, fetcher, lambda rows: {s: [r["deliv_pct"], r["trades"]] for s, r in rows.items() if s in wanted}, 18)
    for old in sorted(history)[:-KEEP_DATES]:
        del history[old]
    return history


def sync_closes(history, today, symbols, fetcher=fetch_eod, want=6):
    """Official closing prices {iso_date: {symbol: close}} for the latest sessions, from the NSE end-of-day file.

    Every run looks for sessions newer than the newest one held, and back-fills to `want` days; trims to the symbols we track."""
    wanted = set(symbols)
    history = {d: dict(v) for d, v in (history or {}).items() if isinstance(v, dict)}
    _catch_up(history, today, want, fetcher, lambda rows: {s: r["close"] for s, r in rows.items() if s in wanted and r.get("close")}, 14)
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
