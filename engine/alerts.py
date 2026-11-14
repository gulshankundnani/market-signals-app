"""Alerts: a watched stock changes signal, or breaking news names it.

Each alert says what changed, gives a resistance or support reference and a volatility risk level, lists the
indicator readings behind it, and links to the stock's full analysis on the site. Alerts are written into the site's own data file (the "alerts" list)
and, if Telegram is set up, sent there too.
"""
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from . import news, signals

SEEN_LIMIT = 500
DIGEST_LINES = 15
LOG_LIMIT = 30
LOG_MAX_AGE = 3 * 86400
TELEGRAM_MAX_CHARS = 4000
MAX_REASONS = 3
NOTE = "Levels are indicative, from fixed chart rules. Not advice."

# A signal only counts as moving when its score crosses these margins, so a score that hovers around a
# threshold (say 19, 21, 19, 22) does not send a message every run. Moving into Buy or Sell needs a score of
# +/-25. A Buy or Sell is only dropped once its score falls back through +/-10.
ENTER = 25
LEAVE = 10
NEWS_TONES = ("neg", "pos")


# How labels and sides are worded in messages. The stored labels stay Buy/Hold/Sell so records keep working.
LABEL_TEXT = {"Strong Buy": "Strongly aligned bullish", "Buy": "Aligned bullish", "Hold": "Mixed", "Sell": "Aligned bearish",
              "Strong Sell": "Strongly aligned bearish"}
SIDE_TEXT = {"buy": "bullish", "sell": "bearish", "hold": "mixed"}
HORIZON_TEXT = {"short": "Weeks view", "long": "Months-to-years view"}


def label_text(label):
    return LABEL_TEXT.get(label, label)


def side(label):
    return "buy" if label in ("Buy", "Strong Buy") else "sell" if label in ("Sell", "Strong Sell") else "hold"


def next_side(previous_side, score):
    """The side a signal is on after a new score, given the side it was on before."""
    if previous_side == "buy":
        if score <= -ENTER:
            return "sell"
        return "buy" if score > LEAVE else "hold"
    if previous_side == "sell":
        if score >= ENTER:
            return "buy"
        return "sell" if score < -LEAVE else "hold"
    if score >= ENTER:
        return "buy"
    if score <= -ENTER:
        return "sell"
    return "hold"


def changed_side(announced, signal):
    """The new side if this signal has really moved from the side last announced, otherwise None.

    signal is {"label", "score"}. Without a score the plain label decides.
    """
    label = (signal or {}).get("label")
    if not label or label == "N/A":
        return None
    score = signal.get("score")
    has_score = isinstance(score, (int, float)) and not isinstance(score, bool)
    new = next_side(announced, score) if has_score else side(label)
    return new if new != announced else None


def watched_symbols(configured, available):
    """Which stocks to alert on. "*" or "all" means every stock; otherwise the listed ones that exist."""
    available = set(available)
    if configured == "all" or (isinstance(configured, list) and ("*" in configured or "all" in configured)):
        return sorted(available)
    if not isinstance(configured, list):
        return []
    return [s for s in configured if s in available]


def news_key(item):
    return hashlib.sha1((item.get("link") or item["title"]).encode("utf-8")).hexdigest()[:16]


def stock_link(site_url, symbol, section="signals"):
    """Address of a stock's analysis on the site. Opening it shows the full analysis for that stock."""
    return f"{site_url.rstrip('/')}/#stock={urllib.parse.quote(symbol, safe='')}&section={section}"


BASIS_TEXT = {"resistance": "nearest resistance", "support": "nearest support", "52-week high": "52-week high",
              "52-week low": "52-week low"}


def _levels_line(record, new_side, horizon):
    """The reference-level line for a bullish or bearish move, or None if it cannot be worked out."""
    indicators = record.get("indicators") or {}
    zones = (record.get("levels") or {}).get("zones")
    levels = signals.target_levels(record.get("price"), indicators.get("atr"), zones, indicators.get("hi252"),
                                   indicators.get("lo252"), new_side)
    if not levels:
        return None
    target = levels[horizon]
    basis = BASIS_TEXT.get(target["basis"])
    if basis is None:
        multiple = signals.SHORT_FALLBACK_R if horizon == "short" else signals.LONG_FALLBACK_R
        basis = f"volatility-based, {multiple:g}x the risk distance"
    kind = "Resistance reference" if new_side == "buy" else "Support reference"
    return (f"{kind} {target['price']:.2f} ({target['pct']:+.1f}%, {basis}). "
            f"Volatility risk level {levels['stop']['price']:.2f} ({levels['stop']['pct']:+.1f}%).")


def _reasons_line(signal, announced, new_side):
    """The reasons behind the new signal. For a signal that faded to hold, the reasons that weaken the old side."""
    wanted = {"buy": {"pos"}, "sell": {"neg"}}.get(new_side)
    if wanted is None:
        wanted = {"neg", "neutral"} if announced == "buy" else {"pos", "neutral"} if announced == "sell" else {"neutral"}
    texts = [r["text"] for r in (signal.get("reasons") or []) if isinstance(r, dict) and r.get("tone") in wanted and r.get("text")]
    return "Indicator readings: " + "; ".join(texts[:MAX_REASONS]) + "." if texts else None


def format_signal_alert(record, symbol, horizon, announced, new_side, signal):
    name = HORIZON_TEXT[horizon]
    label, score = signal["label"], signal.get("score")
    currency = record.get("currency") or ""
    score_text = f" (indicator alignment {score:+d})" if isinstance(score, int) and not isinstance(score, bool) else ""
    lines = [f"{label_text(label).upper()}: {record['name']} ({symbol})",
             f"{name}: {SIDE_TEXT[announced].capitalize()} -> {label_text(label)}{score_text}. Price {record['price']:.2f} {currency}".strip()]
    if new_side in ("buy", "sell"):
        levels = _levels_line(record, new_side, horizon)
        if levels:
            lines.append(levels)
    else:
        lines.append("No reference level: the reading has faded to mixed.")
    reasons = _reasons_line(signal, announced, new_side)
    if reasons:
        lines.append(reasons)
    return "\n".join(lines)


def build_messages(previous, current, watch, seen, first_run, news_tones=NEWS_TONES, sides=None, items=None):
    """previous/current: {symbol: record}. Returns (lines, updated_seen_list).

    sides remembers, for each stock and horizon, the side (buy, hold or sell) last announced. It is updated in
    place, so the caller can keep it between runs. A move is measured from that side, which means a score that
    drifts up slowly cannot slip past without an alert.

    items, if given, receives {"symbol", "kind"} for each line in the same order, so the caller can add links.

    On the first run nothing is reported, the current headlines are only recorded as seen. Headlines of a tone
    that is switched off are also recorded as seen, so turning the tone on later does not replay old news.
    """
    sides = {} if sides is None else sides
    items = [] if items is None else items
    seen_set, seen_list, lines = set(seen), list(seen), []
    for symbol in watch:
        record = current.get(symbol)
        if not record:
            continue
        old = previous.get(symbol)
        for horizon in ("short", "long"):
            signal = record.get(horizon) or {}
            label = signal.get("label")
            if not label or label == "N/A":
                continue
            stored = sides.setdefault(symbol, {})
            announced = stored.get(horizon)
            if announced is None:
                old_label = ((old or {}).get(horizon) or {}).get("label")
                known_before = bool(old_label) and old_label != "N/A" and not first_run
                announced = stored[horizon] = side(old_label) if known_before else side(label)
            new_side = changed_side(announced, signal)
            if new_side:
                stored[horizon] = new_side
                if not first_run:
                    lines.append(format_signal_alert(record, symbol, horizon, announced, new_side, signal))
                    items.append({"symbol": symbol, "kind": "signal", "horizon": horizon, "side": new_side, "announced": announced})
        for item in record["news"]["alerts"]:
            key = news_key(item)
            if key in seen_set:
                continue
            seen_set.add(key)
            seen_list.append(key)
            if not first_run and item["strong"] in news_tones:
                tone = "Negative" if item["strong"] == "neg" else "Positive"
                lines.append(f"{tone} news, {record['name']} ({symbol}): {item['title']} ({item['source']}, {news.age_text(item['age_min'])})")
                items.append({"symbol": symbol, "kind": "news"})
    return lines, seen_list[-SEEN_LIMIT:]


ADVISORY = "Educational information only, not investment advice. We are not a SEBI-registered adviser or research analyst."


def digest(lines, links=None, limit=DIGEST_LINES, max_chars=TELEGRAM_MAX_CHARS, note=""):
    """One message for a run. See digest_with_count."""
    return digest_with_count(lines, links, limit, max_chars, note)[0]


def digest_with_count(lines, links=None, limit=DIGEST_LINES, max_chars=TELEGRAM_MAX_CHARS, note=""):
    """One message for a run, and how many of the alerts it contains (always the first ones).

    links, if given, has one address (or None) per line. Alerts that do not fit the message are counted, not
    dropped: all of them stay in the app."""
    footer_room = 80 + len(ADVISORY) + 2 + (len(note) + 2 if note else 0)
    blocks = []
    for index, line in enumerate(lines):
        link = links[index] if links and index < len(links) else None
        blocks.append(line + (f"\nFull analysis: {link}" if link else ""))
    shown, used = [], 0
    for block in blocks[:limit]:
        cost = len(block) + 2
        if used + cost > max_chars - footer_room and shown:
            break
        shown.append(block)
        used += cost
    text = "\n\n".join(shown)
    if len(lines) > len(shown):
        text += f"\n\n+{len(lines) - len(shown)} more alerts. Open the app to see them."
    if note and text:
        text += f"\n\n{note}"
    if text:
        text += f"\n\n{ADVISORY}"
    return text, len(shown)


def post_telegram(token, chat_id, text, reply_to=None):
    """Send a message. Returns (ok, message_id). message_id is None if Telegram did not report one.

    reply_to threads the message under an earlier one. If that message no longer exists the new one is still sent.
    Never prints the token or the request URL.
    """
    payload = {"chat_id": chat_id, "text": text[:TELEGRAM_MAX_CHARS], "disable_web_page_preview": True}
    if reply_to:
        payload.update({"reply_to_message_id": reply_to, "allow_sending_without_reply": True})
    request = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status != 200:
                return False, None
            try:
                message_id = json.loads(response.read())["result"]["message_id"]
            except (AttributeError, ValueError, KeyError, TypeError):
                return True, None
            return True, message_id if isinstance(message_id, int) and not isinstance(message_id, bool) else None
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return False, None


def send_telegram(token, chat_id, text):
    """Returns True on success."""
    return post_telegram(token, chat_id, text)[0]


def format_outcome(call, outcome, link=None):
    """The reply sent when a reading's price reaches its reference level or its volatility risk level."""
    name = call.get("name") or call["symbol"]
    horizon = HORIZON_TEXT[call["horizon"]].lower()
    hit_target = outcome["outcome"] == "target"
    price, exit_price = call["price"], outcome["exit_price"]
    move = (exit_price / price - 1) * 100
    verb = "rose" if exit_price >= price else "fell"
    side_word = SIDE_TEXT.get(call["side"], call["side"])
    agreed = f"in line with the {side_word} reading" if hit_target else f"against the {side_word} reading"
    days = outcome["days_held"]
    lines = [f"{'REFERENCE LEVEL REACHED' if hit_target else 'VOLATILITY RISK LEVEL REACHED'}: {name} ({call['symbol']}) {horizon}, {side_word} reading",
             f"Price {verb} to {exit_price:.2f} on {outcome['resolved_date']} ({move:+.1f}% from the {price:.2f} alert price), {agreed}. "
             f"{days} trading day{'s' if days != 1 else ''} after the alert."]
    if link:
        lines.append(f"Full analysis: {link}")
    lines.append(ADVISORY)
    return "\n".join(lines)


_NEWS_LINE = re.compile(r"^(Positive|Negative) news, .*? \(([^()]+)\): (.*) \((.*), [^,()]*\)$", re.S)


def drop_irrelevant(log, records):
    """Remove logged news alerts that no longer pass the relevance and tone rules, so a wrong one does not linger on the page.

    records: {symbol: record}. Entries that are not news alerts, or whose stock is unknown, are kept."""
    kept = []
    for entry in log:
        text = entry.get("text") if isinstance(entry, dict) else None
        match = _NEWS_LINE.match(text) if isinstance(text, str) else None
        record = records.get(match.group(2)) if match else None
        if record:
            tone, symbol, title, source = match.groups()
            if not news.is_relevant(title, record["name"], symbol.split(".")[0], source):
                continue
            if news.headline_sentiment(title)[1] != ("pos" if tone == "Positive" else "neg"):
                continue
        kept.append(entry)
    return kept


def update_log(log, lines, now_ts, symbols=None):
    """Keep a short history of alerts for the page. symbols, if given, has one stock symbol per line so the page
    can open that stock. Returns (log, entries added now)."""
    day = datetime.fromtimestamp(now_ts, timezone.utc).strftime("%Y-%m-%d")
    kept = [e for e in log if isinstance(e, dict) and now_ts - e.get("ts", 0) <= LOG_MAX_AGE]
    known = {e["id"] for e in kept}
    added = []
    for index, text in enumerate(lines):
        entry = {"id": hashlib.sha1((day + text).encode("utf-8")).hexdigest()[:12], "ts": now_ts, "text": text}
        if symbols and index < len(symbols) and symbols[index]:
            entry["symbol"] = symbols[index]
        if entry["id"] not in known:
            known.add(entry["id"])
            kept.append(entry)
            added.append(entry)
    return kept[-LOG_LIMIT:], added
