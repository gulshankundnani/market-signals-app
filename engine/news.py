"""Google News headlines with a simple keyword sentiment. Headlines only, so it is a rough guide."""
import math
import re
import urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from .fetch import FetchError, http_get

FEEDS = {
    "IN": "hl=en-IN&gl=IN&ceid=IN:en",
    "US": "hl=en-US&gl=US&ceid=US:en",
}

# Matched as whole words, with an optional s/es/ed/d/ing ending (so "bank" never matches "ban").
POSITIVE = [
    "surge", "soar", "jump", "rally", "rallie", "gain", "beat", "upgrade", "profit rise", "profit jump", "win",
    "order win", "approval", "approve", "buyback", "dividend", "growth", "strong", "outperform", "bullish",
    "expand", "raise guidance", "rebound", "recover", "record profit", "record high",
]
NEGATIVE = [
    "fall", "fell", "drop", "dropped", "plunge", "slump", "slide", "miss", "downgrade", "probe", "fraud",
    "raid", "default", "bankrupt", "bankruptcy", "lawsuit", "penalty", "fine", "ban", "banned", "resign", "loss",
    "warn", "recall", "crash", "selloff", "sell-off", "weak", "decline", "layoff", "investigation", "bearish",
    "scam", "record low",
]
STRONG_NEGATIVE = [
    "fraud", "raid", "probe", "default", "bankrupt", "bankruptcy", "insolvency", "scam", "investigation",
    "plunge", "crash", "downgrade", "ban", "banned", "resign", "recall", "halt", "show-cause",
]
STRONG_POSITIVE = ["surge", "soar", "upgrade", "buyback", "record profit", "record high", "approval"]

HALF_LIFE_HOURS = 12
MAX_AGE_HOURS = 48


def _compile(words):
    return [re.compile(r"\b" + re.escape(w) + r"(?:s|es|ed|d|ing)?\b") for w in words]


_POS, _NEG = _compile(POSITIVE), _compile(NEGATIVE)
_STRONG_NEG, _STRONG_POS = _compile(STRONG_NEGATIVE), _compile(STRONG_POSITIVE)


NAME_SUFFIXES = {"limited", "ltd", "inc", "corp", "corporation", "co", "company", "plc"}
# Words shared by many companies. They never prove a headline is about this company.
GENERIC_WORDS = {
    "bank", "state", "india", "indian", "industries", "group", "holdings", "services", "financial", "finance",
    "technologies", "power", "life", "insurance", "energy", "steel", "motors", "laboratories", "pharmaceutical",
    "pharma", "international", "global", "tata", "adani", "bajaj", "hindustan", "national", "oil", "gas",
    "petroleum", "electronics", "aeronautics", "cements", "paints", "consumer", "products", "enterprises",
    "ports", "hospitals", "foods", "stores", "first", "america", "american", "united", "speciality",
}


def _words(text):
    return re.findall(r"[a-z0-9&]+", text.lower())


def is_relevant(title, name, ticker_base=None):
    """True when the headline names the company, so its tone can fairly be credited to the stock.

    Search results often match a company only in the article body. Those headlines may be about
    something unrelated, so they must not trigger alerts or move the sentiment score.
    """
    words = _words(title)
    text = " " + " ".join(words) + " "
    name_words = [w for w in _words(name) if w not in NAME_SUFFIXES]
    if name_words and " " + " ".join(name_words) + " " in text:
        return True
    if any(w in words for w in name_words if len(w) >= 4 and w not in GENERIC_WORDS):
        return True
    return bool(ticker_base) and len(ticker_base) >= 3 and ticker_base.lower() in words


def mark_relevance(items, name, ticker_base=None):
    """Tag each headline with `relevant`. Unrelated ones lose their breaking-news flag."""
    for item in items:
        item["relevant"] = is_relevant(item["title"], name, ticker_base)
        if not item["relevant"]:
            item["strong"] = None
    return items


def headline_sentiment(title):
    """Returns (score -1..1, strong) where strong is 'pos', 'neg' or None."""
    text = title.lower()
    pos = sum(1 for p in _POS if p.search(text))
    neg = sum(1 for p in _NEG if p.search(text))
    score = max(-1.0, min(1.0, (pos - neg) * 0.6))
    strong = None
    if any(p.search(text) for p in _STRONG_NEG):
        strong = "neg"
    elif any(p.search(text) for p in _STRONG_POS):
        strong = "pos"
    if strong == "neg" and score > -0.6:
        score = -0.6
    return score, strong


def parse_rss(xml_text, now):
    """Parse a Google News RSS document into headline dicts, newest first."""
    if "<!DOCTYPE" in xml_text or "<!ENTITY" in xml_text:
        raise FetchError("RSS contained a DTD, refusing to parse")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as err:
        raise FetchError(f"RSS parse error: {err}")
    items = []
    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        if not title:
            continue
        source_node = node.find("source")
        source = (source_node.text or "").strip() if source_node is not None else ""
        if source and title.endswith(" - " + source):
            title = title[: -(len(source) + 3)]
        try:
            published = parsedate_to_datetime(node.findtext("pubDate"))
            age_min = max(0, int((now - published).total_seconds() // 60))
            ts = int(published.timestamp())
        except (TypeError, ValueError):
            age_min, ts = None, None
        score, strong = headline_sentiment(title)
        link = (node.findtext("link") or "").strip()
        items.append({
            "title": title,
            "link": link if link.startswith("https://") else "",
            "source": source,
            "ts": ts,
            "age_min": age_min,
            "sentiment": score,
            "strong": strong,
        })
    items.sort(key=lambda i: (i["age_min"] is None, i["age_min"] or 0))
    return items


def fetch_news(query, market, now, limit=8):
    url = "https://news.google.com/rss/search?q={}&{}".format(
        urllib.parse.quote(query + " when:2d"), FEEDS.get(market, FEEDS["US"]))
    return parse_rss(http_get(url).decode("utf-8", "replace"), now)[:limit]


def news_score(items):
    """Recency-weighted average sentiment of the last 48 hours, in -1..1. None if no recent news."""
    total = weight_sum = 0.0
    for item in items:
        age = item["age_min"]
        if age is None or age > MAX_AGE_HOURS * 60 or not item.get("relevant", True):
            continue
        weight = math.exp(-math.log(2) * (age / 60) / HALF_LIFE_HOURS)
        total += weight * item["sentiment"]
        weight_sum += weight
    return total / weight_sum if weight_sum else None


def strong_alerts(items, max_age_hours=24):
    return [i for i in items if i["strong"] and i.get("relevant", True) and i["age_min"] is not None
            and i["age_min"] <= max_age_hours * 60][:3]
