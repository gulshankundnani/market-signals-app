"""The NSE's own live price snapshot, used as a second source for live prices.

The NSE publishes live "gainers" and "losers" lists (NIFTY 50, NIFTY Next 50, Bank Nifty, F&O, all securities above and below Rs 20): about
150 different stocks per request pair, with the last traded price, previous close and a timestamp. These are the stocks that move most, which
are exactly the ones that raise alerts, so a mover's price is checked against the exchange before it is announced.

Tested on 2026-11-20 at about 14:50 IST: 30 stocks, Yahoo against the NSE snapshot: median difference 0.05%, largest 0.48%.

Same rules as the other NSE feeds: two honest requests per run for the whole market, never one per stock; a refusal (401 or 403) pauses the
source (engine/backoff.py) and nothing is worked around. The snapshot covers only the stocks on those lists, so most stocks have no live check;
that is reported as "not covered", never as a match.
"""
import json
from datetime import datetime, timedelta, timezone
from statistics import median

from . import backoff
from .fetch import FetchError, http_get

URL = "https://www.nseindia.com/api/live-analysis-variations?index={kind}"
KINDS = ("gainers", "loosers")
GROUPS = ("NIFTY", "NIFTYNEXT50", "BANKNIFTY", "FOSec", "SecGtr20", "SecLwr20", "allSec")
IST = timezone(timedelta(hours=5, minutes=30))
MATCH = 0.01             # within 1%: the sources agree (they are read a moment apart on a moving price)
MISMATCH = 0.03          # more than 3% apart: a real disagreement, the mover is not announced
TICK_ALLOWANCE = 0.05        # rupees: five ticks, so penny stocks are not flagged for a tick
MAX_AGE_MINUTES = 20     # a snapshot older than this is not a live check


def _stamp(text):
    try:
        return datetime.strptime((text or "").strip(), "%d-%b-%Y %H:%M:%S").replace(tzinfo=IST)
    except ValueError:
        return None


def parse(payload):
    """({"SYMBOL.NS": {"price", "prev", "pct"}}, newest timestamp as an aware datetime or None) from one answer."""
    prices, newest = {}, None
    for group in GROUPS:
        block = (payload or {}).get(group) if isinstance(payload, dict) else None
        if not isinstance(block, dict):
            continue
        when = _stamp(block.get("timestamp"))
        newest = max([w for w in (newest, when) if w], default=None)
        rows = block.get("data")
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            symbol, price = row.get("symbol"), row.get("ltp")
            if symbol and isinstance(price, (int, float)) and not isinstance(price, bool) and price > 0:
                prev = row.get("prev_price")
                prices[symbol + ".NS"] = {"price": float(price), "prev": float(prev) if isinstance(prev, (int, float)) and not isinstance(prev, bool) else None,
                                          "pct": row.get("perChange") if isinstance(row.get("perChange"), (int, float)) else None}
    return prices, newest


def fetch(now, previous=None, getter=http_get, log=print, clock=None):
    """{"ok", "prices", "as_of", "fetched_at", "error", "blocked_until", "failures"}. Failures give an empty price list, never an exception."""
    previous = previous or {}
    out = {"ok": False, "prices": {}, "as_of": None, "fetched_at": None, "error": None, "blocked_until": None, "failures": 0}
    held = previous.get("blocked_until")
    try:
        held_until = datetime.fromisoformat(held) if held else None
    except (TypeError, ValueError):
        held_until = None
    if backoff.still_paused(now, held_until, previous.get("failures")):
        out.update(error="HTTP 403: the NSE refuses automated requests (not asked again until the pause ends)", blocked_until=held, failures=previous.get("failures", 1))
        log(f"NSE live prices: skipped, refused earlier; next attempt after {held}")
        return out
    newest = None
    try:
        for kind in KINDS:
            prices, when = parse(json.loads(getter(URL.format(kind=kind), headers={"Accept": "application/json"}, retries=2)))
            out["prices"].update(prices)
            newest = max([w for w in (newest, when) if w], default=None)
    except (FetchError, ValueError) as err:
        text = str(err)
        refused = "403" in text or "401" in text
        out["error"] = "HTTP 403: the NSE refuses automated requests" if refused else (text[:140] or "request failed")
        out["prices"] = {}
        if refused:
            out["blocked_until"], out["failures"] = backoff.refusal_state(now, previous)
        log(f"NSE live prices failed: {out['error']}")
        return out
    read_at = (clock or (lambda: datetime.now(timezone.utc)))()          # the run's `now` is when it started: the snapshot is read minutes later
    out.update(ok=bool(out["prices"]), as_of=newest.isoformat(timespec="seconds") if newest else None, fetched_at=read_at.isoformat(timespec="seconds"))
    log(f"NSE live prices: {len(out['prices'])} stocks (snapshot {out['as_of']})")
    return out


def usable(live, now=None):
    """The price table when the snapshot was current when it was read (at most MAX_AGE_MINUTES after its own timestamp) and was read within the
    last MAX_AGE_MINUTES, else {} (an old snapshot is not a live check). now defaults to the current time."""
    if not live or not live.get("ok") or not live.get("as_of") or not live.get("fetched_at"):
        return {}
    try:
        snapshot, read_at = datetime.fromisoformat(live["as_of"]), datetime.fromisoformat(live["fetched_at"])
    except (TypeError, ValueError):
        return {}
    now = now or datetime.now(timezone.utc)
    fresh_when_read = timedelta(minutes=-5) <= read_at - snapshot <= timedelta(minutes=MAX_AGE_MINUTES)
    return live["prices"] if fresh_when_read and now - read_at <= timedelta(minutes=MAX_AGE_MINUTES) else {}


def status_of(yahoo_price, nse_price):
    """(status, difference in percent) of Yahoo's price against the NSE's. Status is "match", "minor" or "mismatch"."""
    if not yahoo_price or not nse_price:
        return None, None
    diff = abs(yahoo_price / nse_price - 1)
    if abs(yahoo_price - nse_price) <= TICK_ALLOWANCE:          # a stock under Rs 2 can differ by a tick or two (Rs 0.01 each) and look like several percent
        return "match", round((yahoo_price / nse_price - 1) * 100, 3)
    return ("match" if diff <= MATCH else "minor" if diff <= MISMATCH else "mismatch"), round((yahoo_price / nse_price - 1) * 100, 3)


def check_records(records, prices, as_of):
    """Add record["live_check"] to every record the snapshot covers. Returns the summary shown on the Market tab, or None."""
    diffs = []
    for rec in records:
        live = prices.get(rec["symbol"])
        if not live or rec.get("carried"):
            continue
        status, diff = status_of(rec.get("price"), live["price"])
        if status:
            rec["live_check"] = {"status": status, "diff_pct": diff, "nse_price": live["price"], "as_of": as_of, "source": "NSE live snapshot"}
            diffs.append((status, abs(diff)))
    if not diffs:
        return None
    return {"source": "NSE live snapshot", "as_of": as_of, "compared": len(diffs), "match": sum(1 for s, _ in diffs if s == "match"),
            "minor": sum(1 for s, _ in diffs if s == "minor"), "mismatch": sum(1 for s, _ in diffs if s == "mismatch"),
            "median_diff_pct": round(median(d for _, d in diffs), 3), "max_diff_pct": round(max(d for _, d in diffs), 3)}


def disputed(quotes, prices):
    """Symbols whose Yahoo quote is more than MISMATCH away from the NSE's snapshot: no mover alert is raised on them."""
    return {s for s, q in quotes.items() if s in prices and status_of(q.get("price"), prices[s]["price"])[0] == "mismatch"}
