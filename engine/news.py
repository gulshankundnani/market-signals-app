"""Google News headlines for each stock, shown as they are.

No good-or-bad tone is worked out from the words of a headline: word matching gets meanings wrong (a price "band" is not
a "ban"). What is checked is only who a headline is about (is_relevant) and how recent it is.
"""
import re
import urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from .fetch import FetchError, http_get

FEEDS = {
    "IN": "hl=en-IN&gl=IN&ceid=IN:en",
    "US": "hl=en-US&gl=US&ceid=US:en",
}

FRESH_HOURS = 6    # a headline newer than this counts as new on the stock page and can be sent as an alert


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
                    "stocks that", "shares to buy", "stocks under", " vs ", " vs. ", " versus ", "stock picks", "stock price, news",
                    "share price today", "stock price live", "quote and history", "options flow", "dark pools")
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
    if any(p in lower for p in _ROUNDUP_PHRASES) or re.search(r"\b(?:\d+|two|three|four|five|six|seven|eight|nine|ten)\s+(?:stocks|shares|companies)\b", lower):
        return True
    caps = [w for w in re.findall(r"\b[A-Z][A-Z&]{1,7}\b", title) if w not in _NOT_TICKERS and not re.fullmatch(r"Q[1-4]|FY\d*", w)]
    return len(caps) >= 3


def _key(ticker):
    return re.sub(r"[.\-]", "", ticker or "").upper()


def _names_someone_else(title, ticker_base, names, lists=True):
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
    if not lists:
        return False
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


MAX_POSITION = 8     # the company must be named within the first few words of a headline


def _find(words, target):
    """Index where the word sequence `target` first appears in `words`, or None."""
    n = len(target)
    for i in range(len(words) - n + 1):
        if words[i:i + n] == target:
            return i
    return None


def is_relevant(title, name, ticker_base=None, source=None, mention_only=False):
    """True when the headline is about this company, so it can be shown as news for the stock.

    The stock comes first and its news follows, so the match has to be confident and the company has to be the
    subject: named within the first few words, by its full name when the name has several words (ICICI Lombard is
    not ICICI Bank), or by a ticker that is an abbreviation (TCS), or by its ticker in brackets, or by a long
    distinctive name word (Microsoft). A short or common name word (Trent, Titan) also needs business wording.
    People and sports stories, lists, comparisons, price pages, ratings given by the company, and headlines about
    another company never count. mention_only skips the list rules: it asks only whether the company is named.
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
    if _names_someone_else(title, ticker_base, [phrase] + short_names, lists=not mention_only):
        return False
    if any(re.search(r"\b" + re.escape(b) + r"\b", phrase) for b in BROKER_NAMES) and RATING_WORDS.intersection(words) and not re.search(
            re.escape(phrase) + r"\s+(?:upgraded|downgraded|rated|gets|receives|stock|shares)", text):
        return False
    ticker = (ticker_base or "").lower() if ticker_base and len(ticker_base) >= 3 else ""
    if ticker_base and re.search(r"\((?:[A-Za-z]+:)?\s*" + re.escape(ticker_base) + r"\)", title):
        return True
    full = _find(words, name_words) if name_words else None
    strong = []
    for size in range(len(name_words), 1, -1):          # the full name, or its first words ("Adani Ports")
        spot = _find(words, name_words[:size])
        if spot is not None:
            strong.append(spot)
            break
    if ticker and ticker not in name_words and ticker in words:
        strong.append(words.index(ticker))
    strong += [words.index(w) for w in name_words
               if len(w) >= DISTINCTIVE_MIN_LEN and w not in GENERIC_WORDS and w not in COMMON_WORDS and w in words]
    if strong:
        return min(strong) < MAX_POSITION
    weak = []
    if len(name_words) == 1 and full is not None:
        weak.append(full)
    if ticker and ticker in words:
        weak.append(words.index(ticker))
    if len(name_words) >= 2:
        weak += [words.index(w) for w in name_words if len(w) >= DISTINCTIVE_MIN_LEN and w not in GENERIC_WORDS and w in words]
    return bool(weak) and min(weak) < MAX_POSITION and _has_context(words)


def mark_relevance(items, name, ticker_base=None):
    """Tag each headline with `relevant`: True only when it is clearly about this company."""
    for item in items:
        item["relevant"] = is_relevant(item["title"], name, ticker_base, item.get("source"))
    return items


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
        link = (node.findtext("link") or "").strip()
        items.append({
            "title": title,
            "link": link if link.startswith("https://") else "",
            "source": source,
            "ts": ts,
            "age_min": age_min,
        })
    items.sort(key=lambda i: (i["age_min"] is None, i["age_min"] or 0))
    return items


def fetch_news(query, market, now, limit=8):
    url = "https://news.google.com/rss/search?q={}&{}".format(
        urllib.parse.quote(query + " when:2d"), FEEDS.get(market, FEEDS["US"]))
    return parse_rss(http_get(url, timeout=15, retries=2).decode("utf-8", "replace"), now)[:limit]


def _story_key(item):
    return (item.get("link") or item["title"]).strip().lower()


def keep_stock_specific(records):
    """News follows the stock, never the other way round: a headline that is about more than one company in the list
    (a deal, a roundup, a comparison) is not credited to any of them. Shares of one company count as one company.

    Every headline is checked against every company, not only the stock it was found for.
    records: stock records with record["news"]["items"]. Their "fresh" lists are rebuilt."""
    companies = {}
    for rec in records:
        companies.setdefault(rec["name"], rec["symbol"].split(".")[0])
    tokens = {name: {w for w in _words(name) if len(w) >= 3 and w not in NAME_SUFFIXES} | {base.lower()} for name, base in companies.items()}
    stories = {}
    for rec in records:
        for item in (rec.get("news") or {}).get("items", []):
            stories.setdefault(_story_key(item), item)
    shared = set()
    for key, item in stories.items():
        words = set(_words(item["title"]))
        named = [n for n, base in companies.items() if tokens[n] & words and is_relevant(item["title"], n, base, item.get("source"), mention_only=True)]
        if len(named) > 1:
            shared.add(key)
    for rec in records:
        block = rec.get("news") or {}
        for item in block.get("items", []):
            if _story_key(item) in shared:
                item["relevant"] = False
        if "items" in block:
            block["fresh"] = fresh_headlines(block["items"])
    return records


def fresh_headlines(items, max_age_hours=FRESH_HOURS):
    """Headlines about the company from the last few hours, newest first. Nothing is judged from the words."""
    return [i for i in items if i.get("relevant", True) and i["age_min"] is not None and i["age_min"] <= max_age_hours * 60][:3]
