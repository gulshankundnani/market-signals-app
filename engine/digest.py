"""The Telegram digest: one message (or a few) per run, laid out as short bulleted cards.

Readings come first, news second. A card says what happened and gives the figures and the links. News cards carry the
headline, its source and a link to the article exactly as published: nothing is judged from the words.

Messages use Telegram's HTML formatting, so a long address shows as a short clickable label ("Read article",
"Open analysis"). Everything that comes from outside is escaped. to_plain() turns a message back into plain text for
the rare case Telegram refuses the formatting.
"""
import html
import re
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
DEALS_TITLE = "━━ BLOCK DEALS ━━"
MOVERS_TITLE = "━━ MOVERS ━━"
LOW_TITLE = "━━ 52-WEEK LOWS ━━"
IPO_TITLE = "━━ IPO WATCH ━━"
INDEX_TITLE = "━━ INDEXES ━━"
BRIEF_TITLE = "━━ MARKET BRIEF ━━"
FOOTER = "ℹ️ " + html.escape(alerts.NOTE) + "\n" + html.escape(alerts.ADVISORY)
MAX_TITLE = 280
esc = html.escape
INVISIBLE = "​"            # a zero-width space: invisible, but it stops Telegram turning "Site.com" into a link


def quiet(text):
    """Text from outside with its web-address look removed, so Telegram does not make stray links of it."""
    return re.sub(r"(?<=[A-Za-z0-9])\.(?=[A-Za-z]{2,})", "." + INVISIBLE, text)


def _clip(text, limit):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def reading_card(item, link):
    """The card for a reading that changed side."""
    currency = item.get("currency") or ""
    horizon = alerts.HORIZON_TEXT[item["horizon"]]
    score = item.get("score")
    shown = f" (alignment {score:+d})" if isinstance(score, int) else ""
    lines = [f"{DOT[item['side']]} <b>{esc(item['name'])} ({esc(item['symbol'])})</b>",
             f"{esc(alerts.label_text(item['label']))} · {horizon}",
             f"{BULLET} Change: {alerts.SIDE_TEXT[item['announced']].capitalize()} → {esc(alerts.label_text(item['label']))}{shown}"]
    if isinstance(item.get("price"), (int, float)):
        lines.append(f"{BULLET} Price: {esc(alerts.money_text(item['price'], currency))}")
    levels = item.get("levels")
    if levels:
        lines.append(f"{BULLET} {levels['kind']}: {esc(alerts.money_text(levels['price'], currency))} ({levels['pct']:+.1f}%, {esc(levels['basis'])})")
        lines.append(f"{BULLET} Volatility risk level: {esc(alerts.money_text(levels['risk_price'], currency))} ({levels['risk_pct']:+.1f}%)")
    elif item["side"] == "hold":
        lines.append(f"{BULLET} No reference level: the reading has faded to mixed")
    reasons = item.get("reasons") or []
    if reasons:
        lines.append(f"{BULLET} Indicator readings:")
        lines.extend(f"   {DASH} {esc(_clip(alerts.compact(r), 140))}" for r in reasons)
    if link:
        lines.append(f"{BULLET} {alerts.anchor(link, 'Open analysis')}")
    return "\n".join(lines)


def news_card(item, link):
    """The card for a headline: as published, with its source and a link to the article."""
    source = esc(quiet(item["source"] or "unknown"))
    age = esc(alerts.news.age_text(item.get("age_min")) or "time unknown")
    lines = [f"\U0001F4F0 <b>{esc(item['name'])} ({esc(item['symbol'])})</b>",
             f"{BULLET} Headline: {esc(quiet(_clip(item['title'], MAX_TITLE)))}",
             f"{BULLET} Source: {source} · {age}"]
    if str(item.get("link") or "").startswith("https://"):
        lines.append(f"{BULLET} {alerts.anchor(item['link'], 'Read article')}")
    if link:
        lines.append(f"{BULLET} {alerts.anchor(link, 'Open analysis')}")
    return "\n".join(lines)


def brief_card(item, link):
    """A scheduled brief: the exchange's own figures as short bullets."""
    return "\n".join([f"\U0001F5D2 <b>{esc(item['name'])}</b>"] + [f"{BULLET} {esc(_clip(line, 260))}" for line in item["lines"]])


def index_card(item, link):
    return "\n".join([f"\U0001F4CA <b>{esc(item['name'])}</b>", f"{BULLET} {esc('; '.join(item['tags']))}", f"{BULLET} Level: {item['last']:,.2f}"])


def mover_card(item, link):
    """A stock that moved a lot, hit a 52-week extreme or traded far above its average volume: what happened, no judgement."""
    icon = {"low52": "\U0001F53B", "ipo": "\U0001F680"}.get(item["kind"], "\U0001F4C8")
    lines = [f"{icon} <b>{esc(item['name'])} ({esc(item['symbol'])})</b>",
             f"{BULLET} {esc('; '.join(item['tags']))}" + (" · small stock" if item.get("small") else ""),
             f"{BULLET} Price: {esc(alerts.money_text(item['price'], item.get('currency') or ''))}"]
    if link:
        lines.append(f"{BULLET} {alerts.anchor(link, 'Open analysis')}")
    return "\n".join(lines)


def deal_card(item, link):
    """A block deal as the NSE publishes it. Buyer and seller are published after the close, so none is shown here."""
    lines = [f"\U0001F9F1 <b>{esc(item['name'])} ({esc(item['symbol'])})</b>",
             f"{BULLET} Block deal: {item['qty']:,} shares at {esc(alerts.money_text(item['price'], 'INR'))} ({esc(alerts.money_crore(item['value']))})",
             f"{BULLET} {esc(item['window'])}" + (f" · {esc(item['time'])} IST" if item.get("time") else "")]
    if isinstance(item.get("pchange"), (int, float)) and isinstance(item.get("previous_close"), (int, float)):
        lines.append(f"{BULLET} {item['pchange']:+.2f}% against the previous close of {esc(alerts.money_text(item['previous_close'], 'INR'))}")
    lines.append(f"{BULLET} Buyer and seller are published after the close")
    lines.append(f"{BULLET} {alerts.anchor(item.get('official_url') or 'https://www.nseindia.com/market-data/large-deals', 'NSE large deals')}")
    if link:
        lines.append(f"{BULLET} {alerts.anchor(link, 'Open analysis')}")
    return "\n".join(lines)


def _header(now_ts, readings, headlines, part, parts, deals=0, movers=0, others=0):
    when = datetime.fromtimestamp(now_ts, IST).strftime("%a %d %b, %H:%M IST")
    counts = []
    if readings:
        counts.append(f"{readings} reading change{'s' if readings != 1 else ''}")
    if deals:
        counts.append(f"{deals} block deal{'s' if deals != 1 else ''}")
    if movers:
        counts.append(f"{movers} mover{'s' if movers != 1 else ''}")
    if others:
        counts.append(f"{others} market update{'s' if others != 1 else ''}")
    if headlines:
        counts.append(f"{headlines} headline{'s' if headlines != 1 else ''}")
    suffix = f" ({part}/{parts})" if parts > 1 else ""
    return f"\U0001F4CA <b>Market Signals</b> · {when}{suffix}\n" + " · ".join(counts)


def render(items, links, now_ts, max_chars=None, max_messages=MAX_MESSAGES):
    """[(text, start, end)]: the messages to send, in Telegram HTML. start and end are positions in items (end
    exclusive), so each alert can be matched to the message that carried it. Alerts beyond the last message are
    counted, not dropped: all of them stay on the site. Sizes count the text people see, not the hidden addresses."""
    max_chars = max_chars or alerts.TELEGRAM_MAX_CHARS
    if not items:
        return []
    cards = []
    for index, item in enumerate(items):
        link = links[index] if links and index < len(links) else None
        make = {"signal": reading_card, "deal": deal_card, "mover": mover_card, "low52": mover_card, "ipo": mover_card, "index": index_card, "brief": brief_card}.get(item["kind"], news_card)
        cards.append((item["kind"], make(item, link)))
    room = max_chars - alerts.visible_length(FOOTER) - RESERVE - 60
    chunks, current, used = [], [], 0
    for index, (kind, card) in enumerate(cards):
        cost = alerts.visible_length(card) + 2
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
                blocks.append({"signal": READINGS_TITLE, "deal": DEALS_TITLE, "mover": MOVERS_TITLE, "low52": LOW_TITLE, "ipo": IPO_TITLE, "index": INDEX_TITLE, "brief": BRIEF_TITLE}.get(kind, NEWS_TITLE))
                last_kind = kind
            blocks.append(card)
        readings = sum(1 for i in chunk if cards[i][0] == "signal")
        deal_count = sum(1 for i in chunk if cards[i][0] == "deal")
        mover_count = sum(1 for i in chunk if cards[i][0] in ("mover", "low52", "ipo"))
        other_count = sum(1 for i in chunk if cards[i][0] in ("index", "brief"))
        text = _header(now_ts, readings, len(chunk) - readings - deal_count - mover_count - other_count, number, parts, deal_count, mover_count, other_count) + "\n\n" + "\n\n".join(blocks)
        if number == len(kept) and left_out:
            text += f"\n\n+{left_out} more alert{'s' if left_out != 1 else ''}. Open the app to see them."
        legend = ("\n\n" + html.escape("Key: " + alerts.LEGEND)) if readings else ""
        messages.append((text + legend + "\n\n" + FOOTER, chunk[0], chunk[-1] + 1))
    return messages
