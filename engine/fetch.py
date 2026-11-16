"""Free Yahoo Finance data over plain HTTP. No third-party packages."""
import http.cookiejar
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

USER_AGENT = "Mozilla/5.0 (compatible; stock-signals/1.0)"
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range}&interval=1d&events=div"
MIN_BARS = 60


class FetchError(Exception):
    pass


class ShortSeriesError(FetchError):
    """Yahoo answered, but with fewer usable price bars than needed. Often a passing glitch for new listings."""


def http_get(url, headers=None, timeout=20, retries=3, opener=None):
    """GET with retries on rate limits and server errors. Returns bytes."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    last = None
    for attempt in range(retries):
        try:
            open_fn = opener.open if opener else urllib.request.urlopen
            with open_fn(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as err:
            last = err
            if err.code not in (429, 500, 502, 503, 504):
                break
        except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
            last = err
        time.sleep(1.5 * (attempt + 1))
    raise FetchError(f"{url.split('?')[0]}: {last}")


def clean_chart(payload, symbol, min_bars=MIN_BARS):
    """Turn a Yahoo chart payload into clean bars. Raises FetchError on unusable data."""
    chart = (payload or {}).get("chart") or {}
    results = chart.get("result")
    if not results:
        error = (chart.get("error") or {}).get("description", "no data")
        raise FetchError(f"{symbol}: {error}")
    result = results[0]
    meta = result.get("meta") or {}
    stamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    offset = meta.get("gmtoffset") or 0

    bars = {"t": [], "d": [], "o": [], "h": [], "l": [], "c": [], "v": []}
    columns = [quote.get(k) or [] for k in ("open", "high", "low", "close", "volume")]
    if not stamps or any(len(col) != len(stamps) for col in columns):
        raise FetchError(f"{symbol}: malformed series")
    for ts, o, h, l, c, v in zip(stamps, *columns):
        if None in (o, h, l, c) or min(o, h, l, c) <= 0 or l > h:
            continue
        bars["t"].append(ts)
        bars["d"].append(datetime.fromtimestamp(ts + offset, timezone.utc).strftime("%Y-%m-%d"))
        bars["o"].append(float(o))
        bars["h"].append(float(h))
        bars["l"].append(float(l))
        bars["c"].append(float(c))
        # Missing volume stays None. A genuine zero stays 0.0. Turning "no data" into zero would make a stock look
        # as if nobody traded it and skew every volume-based indicator.
        bars["v"].append(None if v is None else float(v))
    if len(bars["c"]) < min_bars:
        raise ShortSeriesError(f"{symbol}: only {len(bars['c'])} usable bars")

    dividends = sorted(
        (int(d["date"]), float(d["amount"]))
        for d in ((result.get("events") or {}).get("dividends") or {}).values()
        if d.get("amount")
    )
    splits = []
    for s in ((result.get("events") or {}).get("splits") or {}).values():
        try:
            num, den, ts = float(s["numerator"]), float(s["denominator"]), int(s["date"])
        except (KeyError, TypeError, ValueError):
            continue
        if num > 0 and den > 0:
            splits.append((ts, num, den))
    return {
        "bars": bars,
        "dividends": dividends,
        "splits": sorted(splits),
        "meta": {
            "currency": meta.get("currency"),
            "exchange": meta.get("fullExchangeName"),
            "name": meta.get("longName") or meta.get("shortName"),
            "market_time": meta.get("regularMarketTime"),
            "session_start": ((meta.get("currentTradingPeriod") or {}).get("regular") or {}).get("start"),
            "session_end": ((meta.get("currentTradingPeriod") or {}).get("regular") or {}).get("end"),
        },
    }


PROVISIONAL_GRACE = 1800   # seconds after the close before Yahoo's volume and range for the day are treated as final


def last_bar_provisional(bars, meta, now_ts, grace=PROVISIONAL_GRACE):
    """True when the newest daily bar belongs to a session that is still running (or only just closed).

    Yahoo's daily series includes today's unfinished candle while the market is open: its close is the live price, its
    high and low are the range so far, and its volume is only what has traded so far. Checked on 2026-11-16 at 10:34 UTC
    with the Xetra and London markets open: SAP.DE's 16 Nov bar had volume 155,629 against 1.41 million the day before.
    Such a bar must not be read as a completed candle."""
    start, end = meta.get("session_start"), meta.get("session_end")
    if not bars["t"] or not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return False
    return bars["t"][-1] >= start and now_ts < end + grace


def trim_last_bar(bars):
    """A copy of the bars without the newest one."""
    return {k: list(v[:-1]) for k, v in bars.items()}


def fetch_chart(symbol, range_="5y", min_bars=MIN_BARS, short_retries=1):
    """Download and clean one stock's history. A too-short answer is retried once after a pause."""
    url = CHART_URL.format(symbol=urllib.parse.quote(symbol), range=range_)
    for attempt in range(short_retries + 1):
        try:
            payload = json.loads(http_get(url))
        except (ValueError, UnicodeDecodeError) as err:
            raise FetchError(f"{symbol}: bad JSON ({err})")
        try:
            return clean_chart(payload, symbol, min_bars)
        except ShortSeriesError:
            if attempt >= short_retries:
                raise
            time.sleep(2)


class YahooSession:
    """Cookie plus crumb, needed for Yahoo's fundamentals endpoint."""

    def __init__(self):
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.crumb = None

    def start(self):
        try:
            self.opener.open(urllib.request.Request("https://fc.yahoo.com", headers={"User-Agent": USER_AGENT}), timeout=15)
        except urllib.error.HTTPError:
            pass  # fc.yahoo.com answers 404 but still sets the cookie
        except (urllib.error.URLError, TimeoutError):
            return False
        try:
            crumb = http_get("https://query1.finance.yahoo.com/v1/test/getcrumb", opener=self.opener).decode().strip()
        except FetchError:
            return False
        if not crumb or "<" in crumb or len(crumb) > 40:
            return False
        self.crumb = crumb
        return True

    def get_json(self, url):
        return json.loads(http_get(f"{url}&crumb={urllib.parse.quote(self.crumb)}", opener=self.opener))


def now_ts():
    return int(time.time())
