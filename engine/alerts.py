"""Telegram alerts when a watched stock changes signal or breaking news appears."""
import hashlib
import json
import urllib.error
import urllib.request

SEEN_LIMIT = 500
MAX_LINES = 12


def side(label):
    return "buy" if label in ("Buy", "Strong Buy") else "sell" if label in ("Sell", "Strong Sell") else "hold"


def news_key(item):
    return hashlib.sha1((item.get("link") or item["title"]).encode("utf-8")).hexdigest()[:16]


def build_messages(previous, current, watch, seen, first_run):
    """previous/current: {symbol: record}. Returns (lines, updated_seen_list).

    On the first run nothing is sent, the current headlines are only recorded as seen.
    """
    seen_set, seen_list, lines = set(seen), list(seen), []
    for symbol in watch:
        record = current.get(symbol)
        if not record:
            continue
        old = previous.get(symbol)
        if old and not first_run:
            for horizon, name in (("short", "short-term"), ("long", "long-term")):
                before, after = (old.get(horizon) or {}).get("label"), (record.get(horizon) or {}).get("label")
                if before and after and before != "N/A" and after != "N/A" and side(before) != side(after):
                    lines.append(f"{record['name']} ({symbol}) {name} signal: {before} -> {after}. Price {record['price']:.2f} {record.get('currency') or ''}".strip())
        for item in record["news"]["alerts"]:
            key = news_key(item)
            if key in seen_set:
                continue
            seen_set.add(key)
            seen_list.append(key)
            if not first_run:
                tone = "Negative" if item["strong"] == "neg" else "Positive"
                lines.append(f"{tone} news, {record['name']} ({symbol}): {item['title']} ({item['source']}, {item['age_min']} min ago)")
    return lines[:MAX_LINES], seen_list[-SEEN_LIMIT:]


def send_telegram(token, chat_id, text):
    """Returns True on success. Never prints the token or the request URL."""
    body = json.dumps({"chat_id": chat_id, "text": text, "disable_web_page_preview": True}).encode()
    request = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return False
