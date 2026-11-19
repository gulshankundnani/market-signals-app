"""Indian IPO dates, price band, lot size and grey-market premium (GMP) from Investorgain.

Investorgain publishes this as a plain HTML table. Its robots.txt allows general crawling.
The data is read at most once an hour, with the project's own User-Agent. The GMP is an
unofficial market rumour, not an exchange figure, so the page shows it with a warning.
The parser checks every column by its header and returns nothing rather than guess if the layout changes.
"""
import html
import re
from datetime import date, datetime, timedelta, timezone

from . import news
from .fetch import FetchError, http_get

URL ="https://www.investorgain.com/report/ipo-gmp-live/331/"
SITE = "https://www.investorgain.com"
MAX_CACHE_AGE = 50 * 60
REQUIRED = ["name", "gmp", "rating", "sub", "price", "size", "lot", "open", "close", "boa", "listing", "updated", "anchor"]
HEADER_KEYS = {"name": "name", "gmp": "gmp", "rating": "rating", "sub": "sub", "price": "price", "ipo size": "size",
               "lot": "lot", "open": "open", "close": "close", "boa dt": "boa", "listing": "listing",
               "updated-on": "updated", "anchor": "anchor"}
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def _text(fragment):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _first_line(fragment):
    return _text(re.split(r"<br\s*/?>", fragment, maxsplit=1)[0])


def _number(text):
    cleaned = (text or "").replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", cleaned)
    return float(match.group()) if match else None


def decode_cloudflare_email(hex_string):
    """Cloudflare hides text containing '@' (here the listing price 'L@52.80') with a one-byte XOR."""
    try:
        data = bytes.fromhex(hex_string)
        key = data[0]
        return bytes(b ^ key for b in data[1:]).decode("utf-8")
    except (ValueError, IndexError, UnicodeDecodeError):
        return None


def _day_month(text):
    match = re.search(r"(\d{1,2})-([A-Za-z]{3})", text or "")
    if not match or match.group(2).lower() not in MONTHS:
        return None
    return int(match.group(1)), MONTHS[match.group(2).lower()]


def _safe_date(year, month, day):
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _nearest(day_month, today):
    """The year that puts this day and month closest to today."""
    candidates = [d for d in (_safe_date(today.year + k, day_month[1], day_month[0]) for k in (-1, 0, 1)) if d]
    return min(candidates, key=lambda d: abs((d - today).days)) if candidates else None


def _not_before(day_month, earliest):
    """The first date with this day and month on or after `earliest`."""
    for k in (0, 1):
        d = _safe_date(earliest.year + k, day_month[1], day_month[0])
        if d and d >= earliest:
            return d
    return None


def _timeline(row_cells, today):
    """open, close, allotment and listing as ISO dates, or None if any is missing or out of order."""
    parts = [_day_month(_first_line(row_cells[key])) for key in ("open", "close", "boa", "listing")]
    if any(p is None for p in parts):
        return None
    start = _nearest(parts[0], today)
    if not start:
        return None
    dates = [start]
    for part in parts[1:]:
        nxt = _not_before(part, dates[-1])
        if not nxt:
            return None
        dates.append(nxt)
    if (dates[-1] - dates[0]).days > 45:
        return None
    return [d.isoformat() for d in dates]


def _parse_row(cells, today):
    name_html = cells["name"]
    link = re.search(r'<a href="(/gmp/[^"]+)"[^>]*title="([^"]+)"', name_html)
    if not link:
        return None
    name = html.unescape(link.group(2)).strip()
    badges = [_text(b) for b in re.findall(r'<span class="badge[^"]*"[^>]*>(.*?)</span>', name_html, re.S)]
    board_text = next((b for b in badges if re.search(r"NSE|BSE|SME", b)), "")
    status_flag = next((b for b in badges if b not in (board_text,)), "")

    timeline = _timeline(cells, today)
    price = _number(_first_line(cells["price"]))
    lot = _number(_first_line(cells["lot"]))
    if not timeline or not price or price <= 0 or not lot or lot <= 0:
        return None

    gmp_match = re.search(r"<b>\s*(-?[\d,\.]+|--)\s*</b>\s*\(\s*(-?[\d\.]+)%\s*\)", cells["gmp"])
    gmp = pct = None
    if gmp_match and gmp_match.group(1) != "--":
        gmp, pct = _number(gmp_match.group(1)), _number(gmp_match.group(2))

    listed_price = listed_gain = None
    mail = re.search(r'data-cfemail="([0-9a-fA-F]+)"', name_html)
    if mail:
        decoded = decode_cloudflare_email(mail.group(1)) or ""
        match = re.match(r"L@(-?\d+(?:\.\d+)?)$", decoded.strip())
        gain = re.search(r"\(\s*(-?[\d\.]+)%\s*\)", _text(name_html.split("data-cfemail", 1)[1]))
        if match:
            listed_price = float(match.group(1))
            listed_gain = float(gain.group(1)) if gain else None

    sub = re.search(r"([\d\.,]+)\s*x", _text(cells["sub"]))
    anchor_text = _text(cells["anchor"])
    return {
        "name": name,
        "board": "SME" if "SME" in board_text else "Mainboard",
        "exchange": "NSE" if "NSE" in board_text else "BSE" if "BSE" in board_text else None,
        "status_flag": status_flag,
        "link": SITE + link.group(1),
        "gmp": gmp, "gmp_pct": pct, "rating": html.unescape(cells["rating"]).count("\U0001F525"),
        "subscription": float(sub.group(1).replace(",", "")) if sub else None,
        "price": price, "size_cr": _number(_first_line(cells["size"])), "lot": int(lot),
        "open": timeline[0], "close": timeline[1], "allotment": timeline[2], "listing": timeline[3],
        "anchor": True if "✅" in anchor_text else False if "❌" in anchor_text else None,
        "listed_price": listed_price, "listed_gain_pct": listed_gain,
        "updated": _text(cells["updated"]),
    }


def parse_table(page, today):
    """Returns a list of IPO dicts, or None if the table is missing or its columns changed."""
    start = page.find("<table")
    end = page.find("</table>", start)
    if start < 0 or end < 0:
        return None
    table = page[start:end]
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S)
    header = next((r for r in rows if "<th" in r), None)
    if not header:
        return None
    labels = [re.sub(r"[▲▼\U0001F525]", "", _text(h)).strip().lower() for h in re.findall(r"<th[^>]*>(.*?)</th>", header, re.S)]
    index = {}
    for position, label in enumerate(labels):
        for prefix, key in HEADER_KEYS.items():
            if label.startswith(prefix) and key not in index:
                index[key] = position
    if any(key not in index for key in REQUIRED):
        return None
    out = []
    for raw in rows:
        cells_raw = re.findall(r"<td[^>]*>(.*?)</td>", raw, re.S)
        if len(cells_raw) < len(labels):
            continue
        item = _parse_row({key: cells_raw[index[key]] for key in REQUIRED}, today)
        if item:
            out.append(item)
    return out or None


_RUPEE = r"(-)?\s*(?:rs\.?|₹|inr)\s*(-?\d+(?:\.\d+)?)"
_GMP_RUPEES = re.compile(r"(?:gmp|(?:grey|gray) market premium)\b[^0-9%₹]{0,30}?" + _RUPEE, re.I)
_PREMIUM_RUPEES = re.compile(r"\bpremium\b[^0-9%₹]{0,15}?" + _RUPEE, re.I)
_GMP_PERCENT = re.compile(r"(?:gmp|(?:grey|gray) market premium)\b[^0-9%₹]{0,30}?(-?\d+(?:\.\d+)?)\s*%", re.I)
AGREE_ABSOLUTE = 5.0
AGREE_RELATIVE = 0.25
NEWS_MAX_AGE_HOURS = 72
NEWS_MAX_IPOS = 12


def extract_gmp(title):
    """Pull an explicit grey-market premium out of a headline.

    Only figures written with a currency sign or percent are accepted, so a date such as
    "Nov 14" can never be read as a premium. Returns {"rupees", "percent"} or None.
    """
    text = html.unescape(title or "")
    if not re.search(r"\bgmp\b|(?:grey|gray) market", text, re.I):
        return None
    rupees = percent = None
    match = _GMP_RUPEES.search(text) or _PREMIUM_RUPEES.search(text)
    if match:
        value = float(match.group(2))
        rupees = -abs(value) if match.group(1) else value
    pct = _GMP_PERCENT.search(text)
    if pct:
        percent = float(pct.group(1))
    return {"rupees": rupees, "percent": percent} if rupees is not None or percent is not None else None


def gmp_agrees(source_value, news_value):
    """True when two premiums are close (within Rs 5 or 25%), False when they differ."""
    if source_value is None or news_value is None:
        return None
    return abs(source_value - news_value) <= max(AGREE_ABSOLUTE, AGREE_RELATIVE * abs(source_value))


def enrich_with_news(rows, now, fetch_news):
    """Add `news_gmp` to open and upcoming IPOs from the newest headline that names the company and quotes a GMP."""
    today = now.date().isoformat()
    handled = 0
    for row in rows:
        row["news_gmp"] = None
        if row["listing"] < today or handled >= NEWS_MAX_IPOS:
            continue
        handled += 1
        try:
            items = fetch_news(f'"{row["name"]}" IPO GMP', "IN", now, limit=10)
        except FetchError:
            continue
        for item in items:
            if item["age_min"] is None or item["age_min"] > NEWS_MAX_AGE_HOURS * 60 or not news.is_relevant(item["title"], row["name"], ipo_pages=True):
                continue
            found = extract_gmp(item["title"])
            if found:
                if found["rupees"] is not None:
                    agrees = gmp_agrees(row["gmp"], found["rupees"])
                else:
                    agrees = gmp_agrees(row.get("gmp_pct"), found["percent"])
                row["news_gmp"] = {**found, "title": item["title"], "link": item["link"], "age_min": item["age_min"], "agrees": agrees}
                break
    return rows


def fetch_india(now, cache=None, fetch=http_get, fetch_news=None):
    """Returns (rows, ok, fetched_at_iso, cache). Uses the cache when it is under an hour old.

    When fetch_news is given, a fresh read also cross-checks each open or upcoming IPO's GMP against
    headlines. Because the result is cached with the rows, news is searched at most once an hour.
    """
    stamp = now.timestamp()
    if cache and stamp - cache.get("ts", 0) < MAX_CACHE_AGE and cache.get("rows"):
        return cache["rows"], True, cache.get("fetched_at"), cache
    try:
        page = fetch(URL, timeout=25, retries=2).decode("utf-8", "replace")
        rows = parse_table(page, now.date())
    except FetchError:
        rows = None
    if rows:
        if fetch_news:
            enrich_with_news(rows, now, fetch_news)
        fresh = {"ts": stamp, "fetched_at": now.isoformat(timespec="seconds"), "rows": rows}
        return rows, True, fresh["fetched_at"], fresh
    if cache and cache.get("rows"):
        return cache["rows"], False, cache.get("fetched_at"), cache
    return [], False, None, cache or {}
