"""The Telegram digest: one message (or a few) per run, laid out as short bulleted cards.

Readings come first, news second. A card says what happened and gives the figures and the links. News cards carry the
headline, its source and a link to the article exactly as published: nothing is judged from the words.
"""
from datetime import datetime, timedelta, timezone

from . import alerts

MAX_MESSAGES = 3
RESERVE = 120                      # room kept for the header, section titles and the "more" line
IST = timezone(timedelta(hours=5, minutes=30))
DOT = {"buy": "\U0001F7E2", "sell": "\U0001F534", "hold": "⚪"}
BULLET = "•"
DASH = "–"
READINGS_TITLE = "━━ READINGS ━━"
NEWS_TITLE = "━━ NEWS ━━"
FOOTER = "ℹ️ " + alerts.NOTE + "\n" + alerts.ADVISORY
MAX_TITLE = 280


def _clip(text, limit):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def reading_card(item, link):
    """The card for a reading that changed side."""
    currency = item.get("currency") or ""
    horizon = alerts.HORIZON_TEXT[item["horizon"]]
    score = item.get("score")
    shown = f" (alignment {score:+d})" if isinstance(score, int) else ""
    lines = [f"{DOT[item['side']]} {item['name']} ({item['symbol']})",
             f"{alerts.label_text(item['label'])} · {horizon}",
             f"{BULLET} Change: {alerts.SIDE_TEXT[item['announced']].capitalize()} → {alerts.label_text(item['label'])}{shown}"]
    if isinstance(item.get("price"), (int, float)):
        lines.append(f"{BULLET} Price: {alerts.money_text(item['price'], currency)}")
    levels = item.get("levels")
    if levels:
        lines.append(f"{BULLET} {levels['kind']}: {alerts.money_text(levels['price'], currency)} ({levels['pct']:+.1f}%, {levels['basis']})")
        lines.append(f"{BULLET} Volatility risk level: {alerts.money_text(levels['risk_price'], currency)} ({levels['risk_pct']:+.1f}%)")
    elif item["side"] == "hold":
        lines.append(f"{BULLET} No reference level: the reading has faded to mixed")
    reasons = item.get("reasons") or []
    if reasons:
        lines.append(f"{BULLET} Indicator readings:")
        lines.extend(f"   {DASH} {_clip(r, 140)}" for r in reasons)
    if link:
        lines.append(f"{BULLET} Open analysis: {link}")
    return "\n".join(lines)


def news_card(item, link):
    """The card for a headline: as published, with its source and a link to the article."""
    lines = [f"\U0001F4F0 {item['name']} ({item['symbol']})",
             f"{BULLET} Headline: {_clip(item['title'], MAX_TITLE)}",
             f"{BULLET} Source: {item['source'] or 'unknown'} · {alerts.news.age_text(item.get('age_min')) or 'time unknown'}"]
    if item.get("link"):
        lines.append(f"{BULLET} Read article: {item['link']}")
    if link:
        lines.append(f"{BULLET} Open analysis: {link}")
    return "\n".join(lines)


def _header(now_ts, readings, headlines, part, parts):
    when = datetime.fromtimestamp(now_ts, IST).strftime("%a %d %b, %H:%M IST")
    counts = []
    if readings:
        counts.append(f"{readings} reading change{'s' if readings != 1 else ''}")
    if headlines:
        counts.append(f"{headlines} headline{'s' if headlines != 1 else ''}")
    suffix = f" ({part}/{parts})" if parts > 1 else ""
    return f"\U0001F4CA Market Signals · {when}{suffix}\n" + " · ".join(counts)


def render(items, links, now_ts, max_chars=None, max_messages=MAX_MESSAGES):
    """[(text, start, end)]: the messages to send. start and end are positions in items (end exclusive), so each
    alert can be matched to the message that carried it. Alerts beyond the last message are counted, not dropped:
    all of them stay on the site."""
    max_chars = max_chars or alerts.TELEGRAM_MAX_CHARS
    if not items:
        return []
    cards = []
    for index, item in enumerate(items):
        link = links[index] if links and index < len(links) else None
        cards.append((item["kind"], reading_card(item, link) if item["kind"] == "signal" else news_card(item, link)))
    room = max_chars - len(FOOTER) - RESERVE - 60
    chunks, current, used = [], [], 0
    for index, (kind, card) in enumerate(cards):
        cost = len(card) + 2
        if current and used + cost > room:
            chunks.append(current)
            current, used = [], 0
        current.append(index)
        used += cost
    if current:
        chunks.append(current)
    kept = chunks[:max_messages]
    left_out = len(items) - sum(len(c) for c in kept)
    messages = []
    for number, chunk in enumerate(kept, 1):
        parts, last_kind, blocks = len(kept), None, []
        for index in chunk:
            kind, card = cards[index]
            if kind != last_kind:
                blocks.append(READINGS_TITLE if kind == "signal" else NEWS_TITLE)
                last_kind = kind
            blocks.append(card)
        readings = sum(1 for i in chunk if cards[i][0] == "signal")
        text = _header(now_ts, readings, len(chunk) - readings, number, parts) + "\n\n" + "\n\n".join(blocks)
        if number == len(kept) and left_out:
            text += f"\n\n+{left_out} more alert{'s' if left_out != 1 else ''}. Open the app to see them."
        messages.append((text + "\n\n" + FOOTER, chunk[0], chunk[-1] + 1))
    return messages
