"""Where every group of figures on a stock comes from, when it was read, and whether it is current.

Each stock record carries record["provenance"]: one entry per group of figures with the source, the data date ("as_of"), the time it was read
("read_at"), its age, and a state:

  fresh       current for this moment
  aging       older than the group normally is, still usable
  stale       too old, or missing the newest session: a reading built on it is not current
  verified    a second, independent source agrees
  disputed    a second source disagrees
  unverified  no second source covers it, shown as such and never as a match
  unknown     the age cannot be told

record["provenance_ok"] is False when the price or the indicators behind a reading are stale or disputed. The page then says so next to the
reading, and no alert or tracked call is made on it (see engine/verify.py, engine/tracking.py and engine/build.py for the individual guards).
Nothing here changes a reading or a threshold.
"""
from datetime import datetime, timezone

from . import fetch

FUNDAMENTALS_FRESH_DAYS = 3        # refreshed about once a day
FUNDAMENTALS_AGING_DAYS = 14
STATEMENTS_AGING_DAYS = 14         # the figures change only when a company reports; the read is what ages
STATEMENTS_STALE_DAYS = 45
QUARTER_STALE_DAYS = 140           # a newest quarter older than this means a quarter's results are missing
LIVE_MINUTES = 30

SOURCES = {
    "price": "Yahoo Finance daily chart",
    "indicators": "Computed here from Yahoo daily bars",
    "end_of_day": "NSE end-of-day file",
    "live": "NSE live snapshot",
    "fundamentals": "Yahoo Finance quote summary",
    "statements": "Yahoo Finance fundamentals time series",
    "dividends": "Yahoo Finance chart events",
    "news": "Google News (headlines as published)",
}


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds") if isinstance(ts, (int, float)) and ts > 0 else None


def _days_between(day, now_ts):
    try:
        return (datetime.fromtimestamp(now_ts, timezone.utc).date() - datetime.fromisoformat(day[:10]).date()).days
    except (TypeError, ValueError):
        return None


def _entry(key, state, as_of=None, read_at=None, note=None, age_minutes=None, age_days=None):
    out = {"source": SOURCES[key], "state": state, "as_of": as_of, "read_at": read_at}
    if age_minutes is not None:
        out["age_minutes"] = age_minutes
    if age_days is not None:
        out["age_days"] = age_days
    if note:
        out["note"] = note
    return out


def build(record, now_ts):
    """record["provenance"] for one stock, and whether its price and indicators are current. Returns (provenance, ok)."""
    read_at = _iso(now_ts)
    expected = fetch.expected_session(now_ts)
    out = {}

    as_of, confirmed = record.get("as_of"), record.get("confirmed_as_of")
    behind = bool(record.get("series_lag")) or (bool(as_of) and as_of < expected)
    if behind:
        out["price"] = _entry("price", "stale", as_of, read_at, f"The series ends on {as_of}; the session of {expected} should be in it")
    elif record.get("provisional"):
        out["price"] = _entry("price", "fresh", as_of, read_at, "Today's session is still running: the price is the latest trade (delayed)", age_minutes=0)
    else:
        out["price"] = _entry("price", "fresh", as_of, read_at)
    out["indicators"] = _entry("indicators", "stale" if (confirmed or "") < expected and not record.get("provisional") else "fresh", confirmed, read_at,
                               "Worked out from completed daily candles only" + ("; today's unfinished candle is shown separately" if record.get("provisional") else ""))

    quality = record.get("quality")
    if quality:
        state = {"match": "verified", "minor": "verified", "mismatch": "disputed", "behind": "stale"}.get(quality.get("status"), "unverified")
        note = {"minor": "A small difference from the official close", "mismatch": "More than 2% from the official close", "behind": "The official file has a newer session than the price series"}.get(quality.get("status"))
        out["end_of_day"] = _entry("end_of_day", state, quality.get("last_date"), read_at, note)
    else:
        out["end_of_day"] = _entry("end_of_day", "unverified", None, read_at, "The official file does not cover this stock")
    live = record.get("live_check")
    if live:
        out["live"] = _entry("live", {"match": "verified", "minor": "verified", "mismatch": "disputed"}.get(live["status"], "unverified"), live.get("as_of"), read_at,
                             f"{live['diff_pct']:+.2f}% against the NSE price")

    fund_at = record.get("fundamentals_at")
    fund = record.get("fundamentals") or {}
    if not fund:
        out["fundamentals"] = _entry("fundamentals", "unknown", None, None, "No fundamentals were returned")
    elif isinstance(fund_at, (int, float)):
        days = max(0, int((now_ts - fund_at) // 86400))
        period = fund.get("most_recent_quarter")
        out["fundamentals"] = _entry("fundamentals", "fresh" if days <= FUNDAMENTALS_FRESH_DAYS else "aging" if days <= FUNDAMENTALS_AGING_DAYS else "stale",
                                     period, _iso(fund_at), "Ratios are trailing twelve months" + (f" to {period}" if period else ""), age_days=days)
    else:
        out["fundamentals"] = _entry("fundamentals", "unknown", None, None, "The time of the last read is not known")

    statements = record.get("statements") or {}
    if statements:
        newest, fetched = statements.get("latest_quarter"), statements.get("fetched_at")
        days = _days_between(fetched, now_ts) if fetched else None
        quarter_age = _days_between(newest, now_ts) if newest else None
        state = "unknown" if days is None else "stale" if days > STATEMENTS_STALE_DAYS or (quarter_age is not None and quarter_age > QUARTER_STALE_DAYS) \
            else "aging" if days > STATEMENTS_AGING_DAYS else "fresh"
        out["statements"] = _entry("statements", state, newest, fetched, "Newest reported quarter" if newest else None, age_days=days)

    dividend = record.get("dividend")
    if dividend is not None:
        out["dividends"] = _entry("dividends", "stale" if behind else "fresh", as_of, read_at, "Payments from the price chart's own events")
    news = record.get("news") or {}
    if news:
        out["news"] = _entry("news", "unknown" if news.get("error") else "fresh", None, read_at, "The headline search failed this run" if news.get("error") else None)

    ok = out["price"]["state"] == "fresh" and out["indicators"]["state"] == "fresh" and out["end_of_day"]["state"] not in ("disputed", "stale") \
        and (out.get("live") or {}).get("state") != "disputed"
    return out, ok
