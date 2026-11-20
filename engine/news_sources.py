"""Where the news comes from, and how it is mapped to stocks.

Providers (each one isolated: if one fails the others carry on, and a failed provider is skipped for a while rather than asked again at once):

  * NSE announcements   official exchange filings (JSON API, one request for the last two days of the whole market)       kind "nse"
  * BSE announcements   official exchange filings (BSE's public RSS file of the latest announcements)                      kind "bse"
  * Business Standard, Mint, The Economic Times, Moneycontrol, The Hindu BusinessLine   public RSS feeds (headlines and links only)   kind "media"
  * Google News         per-stock headline search, already done for every tracked stock in engine/build.py (outside this module)
  * NewsData.io         optional: only runs when the secret NEWSDATA_API_KEY exists in the environment (a GitHub Actions secret, never in the
                        repository, never sent to the browser). Off by default. See NEWSDATA below for its terms.

Licensing and manners: only headlines, short official subjects, the source name, the publication time and a link to the original are kept; no article
text is copied. Every story is shown with its source and a link to the original. One request per provider per run (a run is every ten minutes), a provider
that answers 401, 403 or 429 is paused (engine/backoff.py) and never worked around, and nothing is fetched that a browser could not read without logging in.

NSE's own RSS file (nsearchives.nseindia.com/content/RSS/Online_announcements.xml) was empty when tested (200, 0 bytes), so the NSE API is used for the
same announcements. "Hypeless" could not be found as a news provider, so it is not integrated.

NEWSDATA: free plan 200 credits a day, articles 12 hours delayed, past 48 hours, described as "for testing & personal projects". Its terms say the
licence is for "personal or internal business purposes" and require the attribution "Powered by NewsData.io API" with a link; the key may not be shared.
Because the app is a public website, using it may exceed that licence: it is disabled unless you add the key and decide it is acceptable. When on, every
story from it is attributed to NewsData.io. Its endpoint and fields were written from the public API shape and could not be tested without a key.
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from html import unescape

from . import backoff, news
from .fetch import FetchError, http_get

IST = timezone(timedelta(hours=5, minutes=30))
NSE_URL = "https://www.nseindia.com/api/corporate-announcements?index=equities&from_date={start}&to_date={end}"
BSE_URL = "https://www.bseindia.com/data/xml/announcements.xml"
NEWSDATA_URL = "https://newsdata.io/api/1/latest?apikey={key}&country=in&language=en&category=business"
MEDIA_FEEDS = [
    ("Business Standard", "https://www.business-standard.com/rss/markets-106.rss"),
    ("Business Standard", "https://www.business-standard.com/rss/companies-101.rss"),
    ("Mint", "https://www.livemint.com/rss/markets"),
    ("Mint", "https://www.livemint.com/rss/companies"),
    ("The Economic Times", "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms"),
    ("Moneycontrol", "https://www.moneycontrol.com/rss/business.xml"),
    ("The Hindu BusinessLine", "https://www.thehindubusinessline.com/markets/feeder/default.rss"),
]
ARCHIVE_HOURS = 96
ARCHIVE_LIMIT = 1000
MIN_MINUTES = 5                  # a provider is not asked again within this many minutes of its last success


# ---------- helpers ----------

_SUFFIX = re.compile(r"\b(limited|ltd|pvt|private|inc|incorporated|corporation|corp|company|co|the|plc)\b", re.I)


def norm_name(name):
    """A company name reduced for matching across exchanges: no legal suffix, no punctuation, 'and' for '&'."""
    text = unescape(name or "").lower().replace("&", " and ")
    text = _SUFFIX.sub(" ", re.sub(r"\([^)]*\)", " ", text))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", text)).strip()


def _ist(text, fmt):
    try:
        return int(datetime.strptime(text.strip(), fmt).replace(tzinfo=IST).timestamp())
    except (ValueError, AttributeError):
        return None


def _clean(text, limit=260):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()[:limit]


# ---------- parsers (pure) ----------

def parse_nse(payload):
    """Raw items from the NSE announcements JSON: official filings with the exchange's own category in `desc`."""
    out = []
    for row in payload if isinstance(payload, list) else []:
        if not isinstance(row, dict) or not row.get("symbol"):
            continue
        ts = _ist(row.get("an_dt") or "", "%d-%b-%Y %H:%M:%S")
        subject = _clean(row.get("attchmntText")) or (row.get("desc") or "")
        out.append({"title": f"{row.get('sm_name') or row['symbol']}: {row.get('desc') or 'Announcement'}", "text": subject, "desc": row.get("desc") or "", "source": "NSE",
                    "link": row.get("attchmntFile") or "", "ts": ts, "kind": "nse", "symbol": row["symbol"].strip() + ".NS", "name": row.get("sm_name"),
                    "industry": row.get("smIndustry")})
    return out


def parse_bse(xml_text):
    """Raw items from BSE's announcements RSS: 'Company Ltd (scripcode)' as the title, the subject as the description. The symbol is found later by name."""
    out = []
    for block in re.findall(r"<item>(.*?)</item>", xml_text or "", flags=re.S):
        def tag(name):
            m = re.search(rf"<{name}>(.*?)</{name}>", block, flags=re.S)
            return unescape(m.group(1)).strip() if m else ""
        title = tag("title")
        company = re.sub(r"\s*\(\d{5,6}\)\s*$", "", title)
        if not company:
            continue
        out.append({"title": f"{company}: {_clean(tag('description'), 160)}", "text": _clean(tag("description")), "desc": _clean(tag("description"), 160), "source": "BSE",
                    "link": tag("link"), "ts": _ist(tag("pubDate"), "%d-%b-%Y %H:%M:%S"), "kind": "bse", "symbol": None, "name": company})
    return out


def parse_media(xml_text, outlet, now):
    """Headlines from one RSS feed, via the same safe parser the per-stock search uses."""
    out = []
    for item in news.parse_rss(xml_text, now):
        out.append({"title": item["title"], "text": item["title"], "desc": "", "source": outlet, "link": item["link"], "ts": item["ts"], "kind": "media", "symbol": None, "name": None})
    return out


def parse_newsdata(payload):
    """Raw items from a NewsData.io answer (results[]: title, link, source_name or source_id, pubDate in UTC). Attributed to the original outlet and to NewsData.io."""
    out = []
    for row in (payload or {}).get("results") or [] if isinstance(payload, dict) else []:
        if not isinstance(row, dict) or not row.get("title"):
            continue
        try:
            ts = int(datetime.strptime(row["pubDate"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
        except (KeyError, ValueError, TypeError):
            ts = None
        out.append({"title": row["title"], "text": row["title"], "desc": "", "source": f"{row.get('source_name') or row.get('source_id') or 'unknown'} via NewsData.io",
                    "link": row.get("link") or "", "ts": ts, "kind": "media", "symbol": None, "name": None, "provider": "NewsData.io"})
    return out


# ---------- fetching, one provider at a time ----------

def _guard(name, previous, now, fetch_fn, log):
    """Run one provider with its own failure handling. Returns (items, status). A refusal pauses the provider; nothing here raises."""
    previous = previous or {}
    status = {"name": name, "ok": False, "items": 0, "fetched_at": previous.get("fetched_at"), "error": None, "blocked_until": None, "failures": 0}
    held = previous.get("blocked_until")
    try:
        held_until = datetime.fromisoformat(held) if held else None
    except (TypeError, ValueError):
        held_until = None
    if backoff.still_paused(now, held_until, previous.get("failures")):
        status.update(error="paused after a refusal (not asked again until the pause ends)", blocked_until=held, failures=previous.get("failures", 1))
        return [], status
    try:
        last = datetime.fromisoformat(previous["fetched_at"]) if previous.get("fetched_at") and previous.get("ok") else None
    except (TypeError, ValueError):
        last = None
    if last and now - last < timedelta(minutes=MIN_MINUTES) and previous.get("items"):
        status.update(ok=True, items=previous.get("items", 0), fetched_at=previous["fetched_at"], error=None)
        return None, status                                     # asked a moment ago: the archive already holds those items
    try:
        items = fetch_fn()
    except (FetchError, ValueError, OSError) as err:
        text = str(err)
        refused = any(code in text for code in ("401", "403", "429"))
        status["error"] = ("refused (" + next(c for c in ("401", "403", "429") if c in text) + "), paused") if refused else (text[:120] or "request failed")
        if refused:
            status["blocked_until"], status["failures"] = backoff.refusal_state(now, previous)
        log(f"News provider {name}: {status['error']}")
        return [], status
    status.update(ok=True, items=len(items), fetched_at=now.isoformat(timespec="seconds"))
    return items, status


def collect(now, state, getter=http_get, log=print, environ=None):
    """(raw items from every provider that answered, {provider: status}). One provider failing never stops the others."""
    state = state or {}
    environ = environ if environ is not None else os.environ
    all_items, statuses = [], {}

    def run(name, fn):
        items, status = _guard(name, state.get(name), now, fn, log)
        statuses[name] = status
        if items:
            all_items.extend(items)

    start, end = (now.astimezone(IST) - timedelta(days=1)).strftime("%d-%m-%Y"), now.astimezone(IST).strftime("%d-%m-%Y")
    run("NSE announcements", lambda: parse_nse(json.loads(getter(NSE_URL.format(start=start, end=end), headers={"Accept": "application/json"}, retries=1, timeout=40))))
    run("BSE announcements", lambda: parse_bse(getter(BSE_URL, retries=1, timeout=60).decode("utf-8", "replace")))
    outlets = {}
    for outlet, url in MEDIA_FEEDS:
        outlets.setdefault(outlet, []).append(url)
    for outlet, urls in outlets.items():
        def fetch_outlet(urls=urls, outlet=outlet):
            items, error = [], None
            for url in urls:
                try:
                    items += parse_media(getter(url, retries=1, timeout=25).decode("utf-8", "replace"), outlet, now)
                except FetchError as err:
                    error = err                                      # one feed of an outlet failing does not lose its other feed
            if not items and error:
                raise error
            return items
        run(outlet, fetch_outlet)
    key = environ.get("NEWSDATA_API_KEY")
    if key:
        run("NewsData.io", lambda: parse_newsdata(json.loads(getter(NEWSDATA_URL.format(key=key), retries=1, timeout=25))))
    return all_items, statuses


# ---------- archive: stories survive a provider outage ----------

def compact(item):
    """An item as it is kept in state.json: no repeated text, no empty fields."""
    out = {k: v for k, v in item.items() if v not in (None, "", [])}
    if out.get("text") == out.get("title"):
        out.pop("text", None)
    if out.get("text"):
        out["text"] = out["text"][:200]
    out.pop("desc", None) if out.get("kind") == "media" else None
    return out


def merge_archive(archive, fresh, now):
    """The saved items plus this run's, without repeats (same link, or same source, title and time), newer than ARCHIVE_HOURS, capped."""
    cutoff = (now - timedelta(hours=ARCHIVE_HOURS)).timestamp()
    seen, out = set(), []
    for item in list(fresh) + list(archive or []):
        ts = item.get("ts")
        if ts is not None and ts < cutoff:
            continue
        key = item.get("link") or (item.get("source"), item.get("title"), ts)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    out.sort(key=lambda i: -(i.get("ts") or 0))
    return out[:ARCHIVE_LIMIT]


# ---------- mapping to stocks ----------

class NameIndex:
    """Finds the tracked stocks a headline or a BSE company name refers to. names: {symbol: company name}."""

    def __init__(self, names):
        self.names, self.exact, self.tokens = names, {}, {}
        for symbol, name in names.items():
            normal = norm_name(name)
            if normal:
                # two listings with one normalised name (share classes, re-listings): a BSE title cannot tell them apart, so none is chosen
                self.exact[normal] = symbol if normal not in self.exact else None
            for word in set(normal.split()):
                if len(word) >= 4:
                    self.tokens.setdefault(word, set()).add(symbol)

    def by_company(self, company):
        """The symbol whose normalised name equals this company name (BSE titles), or None when unknown or ambiguous."""
        return self.exact.get(norm_name(company))

    def by_headline(self, title, source=None):
        """Symbols the headline is clearly about, using the same relevance rules as the per-stock search."""
        words = set(re.findall(r"[a-z0-9]{4,}", (title or "").lower()))
        candidates = set()
        for word in words:
            candidates |= self.tokens.get(word, set())
        found = []
        for symbol in candidates:
            if news.is_relevant(title, news_short(self.names[symbol]), symbol.split(".")[0], source):
                found.append(symbol)
        return found


def news_short(name):
    from .wide import short_name
    return short_name(name)


def map_items(items, index):
    """({symbol: [items]}, [market-wide items with no stock]). Exchange items carry or find their symbol; headlines are matched by name."""
    by_symbol, market = {}, []
    for item in items:
        symbols = []
        if item.get("symbol"):
            symbols = [item["symbol"]]
        elif item["kind"] == "bse":
            symbol = index.by_company(item.get("name"))
            symbols = [symbol] if symbol else []
        else:
            symbols = index.by_headline(item["title"], item.get("source"))[:2]
        if not symbols:
            if item["kind"] == "media":
                market.append(item)
            continue
        for symbol in symbols:
            by_symbol.setdefault(symbol, []).append({**item, "symbol": symbol, **({"relevance": 0.85} if item["kind"] == "media" else {})})
    return by_symbol, market
