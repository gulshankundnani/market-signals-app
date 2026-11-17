"""Official NSE corporate-action and board-meeting data, used to confirm (or contradict) the event dates the app shows.

Two small requests per refresh, not one per stock:
  * corporate actions (ex-dividend, record and ex dates for dividends, splits, bonus issues, rights)
  * board meetings (the date of a board meeting to consider financial results, as intimated to the exchange)
Both use the NSE's public JSON endpoints with an honest client name. If the NSE refuses (HTTP 401/403) or fails, nothing is
worked around: every event stays UNVERIFIED, with the reason, the time of the last check and a link to the official page. After
a refusal the NSE is left alone for a while instead of being asked again on the next run.
"""
import json
import re
from datetime import datetime, timedelta

from . import backoff
from .fetch import FetchError, http_get

ACTIONS_URL = "https://www.nseindia.com/api/corporates-corporateActions?index=equities&from_date={start}&to_date={end}"
MEETINGS_URL = "https://www.nseindia.com/api/corporate-board-meetings?index=equities&from_date={start}&to_date={end}"
PAGE_URL = "https://www.nseindia.com/get-quotes/equity?symbol={symbol}"
EDGAR_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={symbol}&type=8-K&dateb=&owner=include&count=10"
REFRESH_MINUTES = 60
BLOCK_BACKOFF_HOURS = 6
WINDOW_DAYS = 130


def nse_date(text):
    """'16-Nov-2026' as an ISO date, or None."""
    try:
        return datetime.strptime((text or "").strip(), "%d-%b-%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def classify_action(subject):
    """(kind, amount, ratio) from an NSE corporate-action subject such as 'Interim Dividend - Rs 4 Per Share'.
    kind is dividend, split, bonus, rights or other."""
    text = (subject or "").strip()
    low = text.lower()
    if "dividend" in low:
        amount = re.search(r"(?:rs\.?|re\.?|inr)\s*([0-9]+(?:\.[0-9]+)?)", low)
        return "dividend", float(amount.group(1)) if amount else None, None
    if "split" in low or "sub-division" in low or "sub division" in low:
        ratio = re.search(r"from\s+(?:rs\.?|re\.?)\s*([0-9.]+)[^t]*to\s+(?:rs\.?|re\.?)\s*([0-9.]+)", low)
        return "split", None, f"{ratio.group(1)} to {ratio.group(2)} face value" if ratio else None
    if "bonus" in low:
        ratio = re.search(r"([0-9]+)\s*:\s*([0-9]+)", low)
        return "bonus", None, f"{ratio.group(1)}:{ratio.group(2)}" if ratio else None
    if "rights" in low:
        return "rights", None, None
    return "other", None, None


def parse_actions(payload, symbols):
    """{symbol: [{"kind", "ex_date", "record_date", "amount", "ratio", "subject"}]} for the given base symbols."""
    out = {}
    for row in payload if isinstance(payload, list) else []:
        if not isinstance(row, dict):
            continue
        symbol = (row.get("symbol") or "").strip()
        if symbol not in symbols:
            continue
        ex = nse_date(row.get("exDate"))
        if not ex:
            continue
        kind, amount, ratio = classify_action(row.get("subject"))
        out.setdefault(symbol, []).append({"kind": kind, "ex_date": ex, "record_date": nse_date(row.get("recDate")), "amount": amount,
                                           "ratio": ratio, "subject": (row.get("subject") or "").strip()})
    return out


def parse_meetings(payload, symbols):
    """{symbol: [{"date", "purpose", "results": bool, "announced"}]}: board meetings, flagged when they are about financial results."""
    out = {}
    for row in payload if isinstance(payload, list) else []:
        if not isinstance(row, dict):
            continue
        symbol = (row.get("bm_symbol") or "").strip()
        day = nse_date(row.get("bm_date"))
        if symbol not in symbols or not day:
            continue
        purpose = (row.get("bm_purpose") or "").strip()
        desc = (row.get("bm_desc") or "").lower()
        out.setdefault(symbol, []).append({"date": day, "purpose": purpose, "announced": (row.get("bm_timestamp") or "")[:11],
                                           "results": "result" in purpose.lower() or "financial result" in desc or "results" in desc,
                                           "link": row.get("attachment") if isinstance(row.get("attachment"), str) and row["attachment"].startswith("https://") else None})
    return out


def fetch_official(now, symbols, previous=None, log=print, getter=http_get):
    """{"ok", "fetched_at", "checked_at", "error", "blocked_until", "actions", "meetings"} for the Indian base symbols given.

    A recent successful answer (under REFRESH_MINUTES old) is reused. A refusal starts a pause. Never raises."""
    previous = previous or {}
    stamp = previous.get("fetched_at")
    try:
        age = (now - datetime.fromisoformat(stamp)).total_seconds() / 60 if stamp else None
    except (TypeError, ValueError):
        age = None
    if previous.get("ok") and age is not None and 0 <= age < REFRESH_MINUTES and previous.get("actions") is not None:
        return {**previous, "checked_at": now.isoformat(timespec="seconds"), "reused": True}
    held = previous.get("blocked_until")
    try:
        held_until = datetime.fromisoformat(held) if held else None
    except (TypeError, ValueError):
        held_until = None
    base = {"ok": False, "fetched_at": stamp, "checked_at": now.isoformat(timespec="seconds"), "error": None, "blocked_until": None,
            "actions": previous.get("actions") or {}, "meetings": previous.get("meetings") or {}, "source": "NSE corporate-actions and board-meetings feeds"}
    if held_until and held_until > now and previous.get("failures"):      # a pause saved by the older fixed 6-hour rule has no count and is not honoured
        base.update(error="HTTP 403: the NSE refuses automated requests (not asked again until the pause ends)", blocked_until=held, failures=previous.get("failures", 1))
        log(f"NSE official events: skipped, refused earlier; next attempt after {held}")
        return base
    span = {"start": now.strftime("%d-%m-%Y"), "end": (now + timedelta(days=WINDOW_DAYS)).strftime("%d-%m-%Y")}
    try:
        actions = parse_actions(json.loads(getter(ACTIONS_URL.format(**span), headers={"Accept": "application/json"}, retries=2)), symbols)
        meetings = parse_meetings(json.loads(getter(MEETINGS_URL.format(**span), headers={"Accept": "application/json"}, retries=2)), symbols)
    except (FetchError, ValueError) as err:
        text = str(err)
        refused = "403" in text or "401" in text
        base["error"] = "HTTP 403: the NSE refuses automated requests" if refused else (text[:140] or "request failed")
        if refused:
            base["blocked_until"], base["failures"] = backoff.refusal_state(now, previous)
        log(f"NSE official events failed: {base['error']}")
        return base
    base.update(ok=True, fetched_at=now.isoformat(timespec="seconds"), actions=actions, meetings=meetings)
    log(f"NSE official events: {sum(len(v) for v in actions.values())} corporate actions and {sum(len(v) for v in meetings.values())} board meetings for the app's stocks")
    return base


def link_for(symbol, market):
    """The official page where this stock's announcements can be read."""
    if market == "IN" and symbol.endswith(".NS"):
        return PAGE_URL.format(symbol=symbol[:-3])
    return EDGAR_URL.format(symbol=symbol.replace("-", "."))
