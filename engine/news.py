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
    "raid", "loan default", "debt default", "bond default", "payment default", "default risk", "defaulter", "bankrupt",
    "bankruptcy", "lawsuit", "penalty", "fined", "fines", "ban", "banned", "resign", "loss",
    "warn", "recall", "crash", "selloff", "sell-off", "weak", "decline", "layoff", "investigation", "bearish",
    "scam", "record low",
]
STRONG_NEGATIVE = [
    "fraud", "raid", "probe", "loan default", "debt default", "bond default", "payment default", "defaulter", "bankrupt",
    "bankruptcy", "insolvency", "scam", "investigation",
    "plunge", "crash", "downgrade", "ban", "banned", "resign", "recall", "halt", "show-cause",
]
STRONG_POSITIVE = ["surge", "soar", "upgrade", "buyback", "record profit", "record high", "approval"]

HALF_LIFE_HOURS = 12
MAX_AGE_HOURS = 48
ALERT_MAX_AGE_HOURS = 6    # older than this is not "breaking"; it still counts toward the stock's news tone


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


# A company name is often also a surname or a place. Sports and celebrity stories that happen to share the name
# must not count as news about the company.
PERSON_SUFFIXES = {"jr", "sr"}
SPORT_WORDS = {"nba", "nfl", "mlb", "nhl", "wnba", "ncaa", "mls", "fifa", "uefa", "playoffs", "quarterback", "midfielder",
               "striker", "goalkeeper", "linebacker", "touchdown"}


def _words(text):
    return re.findall(r"[a-z0-9&]+", text.lower())


# A broker or bank named as the source of a rating ("Ecolab stock upgraded by Wells Fargo") is not the subject.
RATING_WORDS = {"upgrade", "upgrades", "upgraded", "downgrade", "downgrades", "downgraded", "rating", "rated", "initiates",
                "initiated", "coverage", "reiterates", "reiterated", "maintains", "overweight", "underweight", "outperform",
                "underperform", "buy", "sell", "hold", "neutral", "target"}
# Banks and brokers that give ratings on other companies. A rating headline naming them is about the rated company.
BROKER_NAMES = {"wells fargo", "goldman sachs", "jpmorgan", "jp morgan", "morgan stanley", "bank of america", "citigroup",
                "citi", "jefferies", "stifel", "barclays", "ubs", "hsbc", "nomura", "macquarie", "bernstein", "raymond james",
                "kotak institutional equities", "motilal oswal", "icici securities", "hdfc securities", "axis securities"}
# Exchange prefixes that mean the same listing as ours. "(XTRA:MRK)" is a different company's shares.
SAME_MARKET = {"", "nyse", "nasdaq", "nasdaqgs", "nasdaqgm", "nasdaqcm", "nysearca", "nyseam", "nse", "bse", "bom"}
_BRACKET_TICKER = re.compile(r"\((?:([A-Za-z]+):)?\s*([A-Z][A-Z0-9.\-]{0,9})\)")
_ROUNDUP_PHRASES = ("stocks to watch", "stocks to buy", "stocks in focus", "stocks to sell", "top gainers", "top losers",
                    "market wrap", "market live", "stock market today", "buzzing stocks", "record date next week",
                    "stocks that", "shares to buy", "stocks under")
_NOT_TICKERS = {"IPO", "GMP", "NSE", "BSE", "USA", "CEO", "CFO", "GDP", "RBI", "SEBI", "ETF", "EPS", "IMF", "OPEC", "FII",
                "DII", "NSDL", "AGM", "SIP", "FDA", "SEC", "FED", "NYSE", "NASDAQ", "AI", "US", "UK", "EU", "IT", "PE"}


def _is_broker_mention(text, names):
    """True when a rating headline names the company only as the one giving the rating ("upgraded by <name>")."""
    if not RATING_WORDS.intersection(text.split()):
        return False
    return any(re.search(r"\b(?:by|from|per|via)\s+(?:the\s+)?" + re.escape(n) + r"\b", text) for n in names if n)


def _looks_like_roundup(title):
    """A list of many companies ("... MRF, SCI, PFC, ...") says little about any one of them."""
    lower = title.lower()
    if any(p in lower for p in _ROUNDUP_PHRASES) or re.search(r"\b\d+\s+(?:stocks|shares|companies)\b", lower):
        return True
    caps = [w for w in re.findall(r"\b[A-Z][A-Z&]{1,7}\b", title) if w not in _NOT_TICKERS and not re.fullmatch(r"Q[1-4]|FY\d*", w)]
    return len(caps) >= 3


def _key(ticker):
    return re.sub(r"[.\-]", "", ticker or "").upper()


def _names_someone_else(title, ticker_base, names):
    """True when the headline is about another company that merely mentions ours.

    That is the case when it carries another company's ticker in brackets (and not ours), when ours is the ticker of
    a different exchange's listing, when it starts "<Other> stock: ...", or when ours is one name in a list.
    """
    mine = _key(ticker_base)
    ours_present, others = False, False
    for exchange, ticker in _BRACKET_TICKER.findall(title):
        if mine and _key(ticker) == mine:
            if (exchange or "").lower() not in SAME_MARKET:
                return True
            ours_present = True
        else:
            others = True
    if others and not ours_present:
        return True
    prefix = re.match(r"^(.{3,60}?)\s+(?:stock|shares)\s*:", title, re.I)
    if prefix:
        head = " " + " ".join(_words(prefix.group(1))) + " "
        if not any(" " + n + " " in head for n in names if n):
            return True
    for n in names:
        if not n:
            continue
        shown = re.escape(n)
        if re.search(r"(?:,|\band\b|&)\s*" + shown + r"\s*(?:,|\band\b|&)", title, re.I) or re.search(r"^\W*(?i:" + shown + r")\s*,\s*[A-Z]", title):
            return True
    return False


def _about_a_person_or_sport(words, source):
    if "sport" in (source or "").lower() or SPORT_WORDS.intersection(words):
        return True
    return any(words[i] in PERSON_SUFFIXES for i in range(len(words)))


def age_text(minutes):
    """'45 min ago', '14 h ago' or '3 d ago'. Empty when the age is unknown."""
    if minutes is None:
        return ""
    if minutes < 90:
        return f"{minutes} min ago"
    if minutes < 48 * 60:
        return f"{round(minutes / 60)} h ago"
    return f"{round(minutes / 1440)} d ago"


# A single shared word (Trent, Titan, Apple) is not enough to say a headline is about the company: it may be a person,
# a place or a product. Such headlines count only when they also read like business news.
CONTEXT_WORDS = {
    "share", "shares", "stock", "stocks", "nifty", "sensex", "nse", "bse", "nasdaq", "nyse", "crore", "crores", "quarter",
    "quarterly", "results", "earnings", "ebitda", "revenue", "profit", "profits", "dividend", "dividends", "buyback",
    "investor", "investors", "brokerage", "analyst", "analysts", "valuation", "ipo", "sebi", "rbi", "ltd", "limited", "inc",
    "corp", "plc", "ceo", "cfo", "chairman", "shareholder", "shareholders", "rating", "upgrade", "downgrade", "upgrades",
    "downgrades", "stake", "acquisition", "acquire", "acquires", "merger", "guidance", "forecast", "outlook", "margin",
    "margins", "rs", "usd", "billion", "bn", "mcap", "order", "orders", "contract", "capex", "market", "markets",
    "bond", "bonds", "funding", "loan", "loans", "raise", "raises", "raising", "trading", "customers", "vehicles",
    "investment", "investments", "invest", "invests",
}
# A long, distinctive name word proves the headline is about the company, unless it is also an ordinary word or place.
DISTINCTIVE_MIN_LEN = 6
COMMON_WORDS = {"reliance", "target", "eternal", "britannia", "premier", "delta", "baroda", "empire", "global", "central",
                "apollo", "jubilant", "universal", "supreme", "crompton", "polycab", "oberoi", "sobha"}
_CONTEXT_PATTERN = re.compile(r"^(q[1-4]|fy\d{2,4}|\d+(?:bn|mn|cr|crore|lakh))$")


def _has_context(words):
    return any(w in CONTEXT_WORDS or _CONTEXT_PATTERN.match(w) for w in words)


def is_relevant(title, name, ticker_base=None, source=None):
    """True when the headline is about the company, so its tone can fairly be credited to the stock.

    Search results often match a company only in the article body, or only share a word with its name. Those
    headlines must not trigger alerts or move the sentiment score, so the match has to be confident:
    the full multi-word name, a ticker that is an abbreviation (TCS), or a single name word (or a ticker that is
    also a plain word, like Trent) together with business wording. People and sports stories never count.
    """
    words = _words(title)
    if _about_a_person_or_sport(words, source) or _looks_like_roundup(title):
        return False
    text = " " + " ".join(words) + " "
    name_words = [w for w in _words(name) if w not in NAME_SUFFIXES]
    phrase = " ".join(name_words)
    short_names = [w for w in name_words if len(w) >= 4 and w not in GENERIC_WORDS]
    if _is_broker_mention(" ".join(words), [phrase] + short_names):
        return False
    if _names_someone_else(title, ticker_base, [phrase] + short_names):
        return False
    if any(re.search(r"\b" + re.escape(b) + r"\b", phrase) for b in BROKER_NAMES) and RATING_WORDS.intersection(words) and not re.search(
            re.escape(phrase) + r"\s+(?:upgraded|downgraded|rated|gets|receives|stock|shares)", text):
        return False
    ticker = (ticker_base or "").lower() if ticker_base and len(ticker_base) >= 3 else ""
    if len(name_words) >= 2 and " " + phrase + " " in text:
        return True
    if ticker and ticker not in name_words and ticker in words:
        return True
    if ticker_base and re.search(r"\((?:[A-Za-z]+:)?" + re.escape(ticker_base) + r"\)", title):
        return True
    distinctive = [w for w in name_words if len(w) >= DISTINCTIVE_MIN_LEN and w not in GENERIC_WORDS and w not in COMMON_WORDS]
    if any(w in words for w in distinctive):
        return True
    shares_a_word = (
        bool(name_words) and " " + phrase + " " in text
        or any(w in words for w in name_words if len(w) >= 4 and w not in GENERIC_WORDS)
        or bool(ticker) and ticker in words
    )
    return shares_a_word and _has_context(words)


def mark_relevance(items, name, ticker_base=None):
    """Tag each headline with `relevant`. Unrelated ones lose their breaking-news flag."""
    for item in items:
        item["relevant"] = is_relevant(item["title"], name, ticker_base, item.get("source"))
        if not item["relevant"]:
            item["strong"] = None
    return items


# Moves in a trading statistic, not news about the business: "open interest surge" is not good news.
NEUTRAL_PHRASES = ("open interest", "short interest", "put-call", "put call", "options chain", "implied volatility")


# "Upgraded to Sell" is not good news, and "plant upgrade" is not a rating change.
_UPGRADED_TO_SELL = re.compile(r"\bupgrad\w* to (?:a |an )?(?:strong )?(?:sell|hold|neutral|underperform|underweight)\b")
_EQUIPMENT_UPGRADE = re.compile(r"\bupgrad\w*\s+(?:for|of|plan|project|programme|program)\b|\b(?:plant|capacity|network|software|system|facility|infrastructure|fleet)\s+upgrad")
_RATING_CONTEXT = re.compile(r"\b(?:rating|ratings|analyst|analysts|broker|brokerage|target|outperform|overweight|credit|stock|shares)\b")


def headline_sentiment(title):
    """Returns (score -1..1, strong) where strong is 'pos', 'neg' or None."""
    text = title.lower()
    if any(phrase in text for phrase in NEUTRAL_PHRASES) or _UPGRADED_TO_SELL.search(text):
        return 0.0, None
    if _EQUIPMENT_UPGRADE.search(text) and not _RATING_CONTEXT.search(text):
        text = re.sub(r"\bupgrad\w*", " ", text)     # "plant upgrade" is not a rating upgrade
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


def strong_alerts(items, max_age_hours=ALERT_MAX_AGE_HOURS):
    return [i for i in items if i["strong"] and i.get("relevant", True) and i["age_min"] is not None
            and i["age_min"] <= max_age_hours * 60][:3]
