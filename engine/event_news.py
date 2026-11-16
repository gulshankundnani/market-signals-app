"""Dates and corporate actions reported in the news about one company, for when no official source can be read.

The NSE and BSE do not allow scripted access to their corporate-action lists, so the company's own announcements are
looked for in news headlines. This is deliberately strict:
  * the headline must be clearly about that one company (news.is_relevant: no roundups, lists or comparisons);
  * it must use a precise phrase ("stock split", "ex-dividend", "record date", "bonus issue", "results on", ...), because
    words like "split" and "bonus" are usually used in other senses;
  * a date, a ratio or an amount must be stated in the headline itself.
Nothing is inferred from tone. The headline, its source and its link are kept as the evidence and shown with the event.
"""
import re
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone

from . import fetch, news

# Only headlines from prominent outlets are used for dates and corporate actions. Short names must match exactly; longer
# ones may appear inside the source name ("CNBC TV18", "The Economic Times").
PROMINENT = ("cnbc tv18", "cnbctv18", "cnbc", "the economic times", "economic times", "et now", "et markets", "business standard",
             "livemint", "mint", "moneycontrol", "the financial express", "financial express", "ndtv profit", "business today",
             "businessline", "the hindu businessline", "the hindu", "zee business", "reuters", "bloomberg", "the wall street journal",
             "wsj", "barron's", "marketwatch", "yahoo finance", "business wire", "pr newswire", "globenewswire", "investing.com",
             "forbes", "fortune", "the times of india", "hindustan times", "press trust of india", "financial times",
             "associated press", "the new york times")
WINDOW = "30d"
MAX_AGE_DAYS = 30
PER_RUN = 25
KEEP_PER_STOCK = 6

_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_MONTH = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DAY_FIRST = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+" + _MONTH + r"\b\.?(?:,?\s+(\d{4}))?", re.I)
_MONTH_FIRST = re.compile(r"\b" + _MONTH + r"\b\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s+(\d{4}))?", re.I)
_RANGE_AFTER = re.compile(r"^\s*(?:-|–|to)\s*\d")
_RANGE_BEFORE = re.compile(r"\d\s*(?:-|–|to)\s*$")
_AMOUNT = re.compile(r"(?:rs\.?\s?|₹\s?|inr\s?)(\d+(?:\.\d+)?)|\$\s?(\d+(?:\.\d+)?)", re.I)
_RATIO = re.compile(r"\b(\d{1,3})\s*:\s*(\d{1,3})\b")
_FACE_VALUE = re.compile(r"face value of (?:rs\.?\s?|₹\s?)(\d+)[^.]{0,30}?to (?:rs\.?\s?|₹\s?)(\d+)", re.I)

# Precise phrases only. "split" or "bonus" alone are not enough.
EX_DATE = re.compile(r"\b(?:ex[- ]?dividend|ex[- ]?date|ex[- ]?record|record date|holders of record|goes ex|turns ex|trades ex)\b", re.I)
PAY_DATE = re.compile(r"\b(?:payable|payment date|pay date|paid on)\b", re.I)
RESULTS = re.compile(r"\b(?:results? (?:on|date)|earnings (?:on|date|call on)|board meeting (?:on|date)|to (?:announce|report|declare) [^.]{0,40}?(?:results|earnings) on|report(?:s)? earnings on)\b", re.I)
SPLIT = re.compile(r"\b(?:stock split|share split|split in the ratio|sub[- ]?division|stock-split)\b", re.I)
BONUS = re.compile(r"\bbonus (?:issue|shares?|share issue)\b", re.I)
DIVIDEND_DECLARED = re.compile(r"\b(?:announces?|declares?|approves?|recommends?|sets|fixes|proposes?)\b[^.]{0,60}?\bdividend\b|\bdividend\b[^.]{0,30}?\b(?:announced|declared|approved|recommended)\b", re.I)


def is_prominent(source):
    """True for a source on the prominent-outlets list."""
    name = (source or "").strip().lower()
    name = re.sub(r"\.(com|in|co\.in)$", "", name)
    return bool(name) and any(name == p or (len(p) > 5 and p in name) for p in PROMINENT)


def _resolve(day, month, year, pub):
    """A date from its parts. With no year, the occurrence nearest the headline's own date: "Jan 5" in a December
    headline is next January, while "Nov 10" in a mid-November headline is a date that has already passed."""
    try:
        if year:
            return date(year, month, day)
        options = []
        for y in (pub.year - 1, pub.year, pub.year + 1):
            try:
                options.append(date(y, month, day))
            except ValueError:
                continue
        return min(options, key=lambda d: abs((d - pub).days)) if options else None
    except ValueError:
        return None


def dates_in(text, pub):
    """[(start, end, date)] for explicit dates in a headline. A date that begins a range ("Nov 16-20") is left out."""
    found = []
    for pattern, order in ((_DAY_FIRST, "dm"), (_MONTH_FIRST, "md")):
        for m in pattern.finditer(text):
            if _RANGE_AFTER.match(text[m.end():]) or _RANGE_BEFORE.search(text[:m.start()]):
                continue
            a, b, year = m.group(1), m.group(2), m.group(3)
            day, month = (int(a), _MONTHS[b[:3].lower()]) if order == "dm" else (int(b), _MONTHS[a[:3].lower()])
            resolved = _resolve(day, month, int(year) if year else None, pub)
            if resolved:
                found.append((m.start(), m.end(), resolved))
    return sorted(found)


def _near(trigger, text, pub):
    """The date closest to a trigger phrase (within 45 characters), or None. today/tomorrow count when the headline says so."""
    best = None
    for start, end, day in dates_in(text, pub):
        distance = min(abs(start - trigger.end()), abs(trigger.start() - end))
        if distance <= 45 and (best is None or distance < best[0]):
            best = (distance, day)
    if best:
        return best[1]
    window = text[trigger.start(): trigger.end() + 40].lower()
    if re.search(r"\btoday\b", window):
        return pub
    if re.search(r"\btomorrow\b", window):
        return pub + timedelta(days=1)
    return None


def _amount(text):
    m = _AMOUNT.search(text)
    return float(m.group(1) or m.group(2)) if m else None


def _ratio(text):
    face = _FACE_VALUE.search(text)
    if face:
        return f"face value {face.group(1)} to {face.group(2)}"
    m = _RATIO.search(text)
    return f"{m.group(1)}:{m.group(2)}" if m else None


_RANGE_DAYS_FIRST = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s*(?:-|–|to)\s*(\d{1,2})(?:st|nd|rd|th)?\s+" + _MONTH + r"\b", re.I)
_RANGE_MONTH_FIRST = re.compile(r"\b" + _MONTH + r"\b\.?\s+(\d{1,2})(?:st|nd|rd|th)?\s*(?:-|–|to)\s*(\d{1,2})(?:st|nd|rd|th)?\b", re.I)
_ACTION_WORDS = {"dividend": re.compile(r"\bdividends?\b|\bex[- ]?dividend\b", re.I), "split": re.compile(r"\bstock splits?\b|\bsplits?\b", re.I),
                 "bonus": re.compile(r"\bbonus\b", re.I), "buyback": re.compile(r"\bbuy[- ]?back\b", re.I)}
_ROUNDUP_CUES = re.compile(r"\brecord dates?\b|\bex[- ]?(?:dividend|date|record)\b|\bcorporate actions?\b|\bdividend stocks?\b|\bupcoming dividends?\b", re.I)


def week_range(text, pub):
    """(start, end) when a headline gives a short date range such as "Nov 16-20" or "16 to 20 November", else None."""
    for pattern, order in ((_RANGE_DAYS_FIRST, "dm"), (_RANGE_MONTH_FIRST, "md")):
        m = pattern.search(text)
        if not m:
            continue
        a, b, c = m.group(1), m.group(2), m.group(3)
        first, last, month = (int(a), int(b), _MONTHS[c[:3].lower()]) if order == "dm" else (int(b), int(c), _MONTHS[a[:3].lower()])
        start, end = _resolve(first, month, None, pub), _resolve(last, month, None, pub)
        if start and end and timedelta(0) <= end - start <= timedelta(days=10):
            return start, end
    return None


def roundup_event(title, pub):
    """The event a roundup headline from a prominent outlet implies for every company it names: that a record or ex-date
    falls inside a short date range. None unless the headline gives such a range and names the kind of action."""
    span = week_range(title, pub)
    if not span or not _ROUNDUP_CUES.search(title):
        return None
    kinds = sorted(k for k, pattern in _ACTION_WORDS.items() if pattern.search(title))
    if not kinds:
        return None
    kind = "ex_dividend" if kinds == ["dividend"] else "corporate_action"
    return {"type": kind, "date": span[0].isoformat(), "until": span[1].isoformat(), "amount": None, "ratio": None, "label": "roundup", "actions": kinds}


def extract(title, pub):
    """The company events one headline states, as [{"type", "date" (ISO or None), "amount", "ratio", "label"}]. pub: date."""
    out = []
    ex = EX_DATE.search(title)
    if ex:
        when = _near(ex, title, pub)
        kind = "split" if SPLIT.search(title) else "bonus" if BONUS.search(title) else "ex_dividend"
        if kind == "ex_dividend" and not re.search(r"dividend|record date|ex[- ]date", title, re.I):
            kind = None
        if kind and (when or kind != "ex_dividend" or _amount(title)):
            out.append({"type": kind, "date": when.isoformat() if when else None, "amount": _amount(title) if kind == "ex_dividend" else None,
                        "ratio": _ratio(title) if kind != "ex_dividend" else None, "label": "record date" if re.search("record date|holders of record", title, re.I) else "ex-date"})
    pay = PAY_DATE.search(title)
    if pay and re.search(r"dividend|distribution", title, re.I):
        when = _near(pay, title, pub)
        if when:
            out.append({"type": "dividend_payment", "date": when.isoformat(), "amount": _amount(title), "ratio": None, "label": "payment date"})
    res = RESULTS.search(title)
    if res:
        when = _near(res, title, pub)
        if when:
            out.append({"type": "earnings", "date": when.isoformat(), "amount": None, "ratio": None,
                        "label": "board meeting" if re.search(r"board meeting", title, re.I) else "results date"})
    if not ex and SPLIT.search(title) and (_ratio(title) or dates_in(title, pub)):
        when = next((d for _, _, d in dates_in(title, pub)), None)
        out.append({"type": "split", "date": when.isoformat() if when else None, "amount": None, "ratio": _ratio(title), "label": "split"})
    if not ex and BONUS.search(title) and (_ratio(title) or dates_in(title, pub)):
        when = next((d for _, _, d in dates_in(title, pub)), None)
        out.append({"type": "bonus", "date": when.isoformat() if when else None, "amount": None, "ratio": _ratio(title), "label": "bonus"})
    if not out and DIVIDEND_DECLARED.search(title) and _amount(title):
        out.append({"type": "ex_dividend", "date": None, "amount": _amount(title), "ratio": None, "label": "announced"})
    seen, unique = set(), []
    for e in out:
        key = (e["type"], e["date"])
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique


def from_headlines(rec, items, now):
    """The events stated by the headlines about one company. Each carries its headline, source and link as evidence."""
    base = rec["symbol"].split(".")[0]
    today = now.date() if isinstance(now, datetime) else now
    out = []
    for item in items:
        if item.get("age_min") is None or item["age_min"] > MAX_AGE_DAYS * 1440:
            continue
        if not is_prominent(item.get("source")):
            continue
        pub = (now - timedelta(minutes=item["age_min"])).date() if isinstance(now, datetime) else today
        if news.is_relevant(item["title"], rec["name"], base, item.get("source")):
            stated = extract(item["title"], pub)
        elif news.names_company(item["title"], rec["name"], base):
            roundup = roundup_event(item["title"], pub)
            stated = [roundup] if roundup else []
        else:
            stated = []
        for e in stated:
            if e["date"] and (e.get("until") or e["date"]) < today.isoformat():
                continue
            out.append({**e, "evidence": {"headline": item["title"], "source": item["source"], "link": item.get("link") or "", "age_min": item["age_min"]}})
    out.sort(key=lambda e: (e["date"] is None, e["date"] or "", e["evidence"]["age_min"]))
    seen, unique = set(), []
    for e in out:                       # the same event reported by several outlets is kept once, with the newest headline
        key = (e["type"], e["date"], e["amount"] if e["date"] is None else None)
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique[:KEEP_PER_STOCK]


def query(name):
    return f'"{name}" (split OR bonus OR "record date" OR "ex-date" OR "ex-dividend" OR "board meeting" OR dividend OR "results on")'


def fetch_headlines(rec, now):
    url = "https://news.google.com/rss/search?q={}&{}".format(urllib.parse.quote(query(rec["name"]) + f" when:{WINDOW}"),
                                                           news.FEEDS.get(rec["market"], news.FEEDS["US"]))
    return news.parse_rss(fetch.http_get(url, timeout=15, retries=2).decode("utf-8", "replace"), now)[:25]


def sync(records, cache, now, fetcher=fetch_headlines, per_run=PER_RUN, breaker=None, sleep=time.sleep, pause=0.5):
    """Events from the news for every stock, kept in `cache` ({"day", "stocks": {symbol: {"day", "events"}}}).

    Each stock is looked up once per UTC day, a batch per run, so a day's lookups are spread over several runs and a
    source outage cannot hold a run up (the breaker stops the round after a few failures in a row). Until a stock has
    been refreshed its earlier result is used."""
    today = now.date().isoformat()
    cache = cache if isinstance(cache, dict) else {}
    stocks = {s: v for s, v in (cache.get("stocks") or {}).items() if isinstance(v, dict)}
    live = {r["symbol"] for r in records}
    stocks = {s: v for s, v in stocks.items() if s in live}
    todo = [r for r in records if (stocks.get(r["symbol"]) or {}).get("day") != today][:per_run]
    done = 0
    for rec in todo:
        if breaker is not None and breaker.open:
            break
        if done:
            sleep(pause)
        try:
            items = fetcher(rec, now)
        except fetch.FetchError:
            if breaker is not None:
                breaker.failed()
            continue
        if breaker is not None:
            breaker.succeeded()
        done += 1
        stocks[rec["symbol"]] = {"day": today, "events": from_headlines(rec, items, now)}
    return {"day": today, "stocks": stocks}
