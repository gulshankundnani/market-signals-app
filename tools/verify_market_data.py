"""Repeatable check of the deployed app's prices (and NSE volumes) against official end-of-day data.

    python tools/verify_market_data.py                      # the live site, 10 NSE + 10 US stocks
    python tools/verify_market_data.py --data site/data.json --json report.json
    python tools/verify_market_data.py --symbols RELIANCE.NS AAPL --volume

References: the NSE's daily bhavcopy file for Indian stocks and the Cboe delayed-quote history for US stocks, both independent
of Yahoo Finance, which is where the app gets its prices. Each stock is compared on the same trading session only. When the
reference for that session is not published yet the result is UNVERIFIED, not PASSED or FAILED. Run it again later.
"""
import argparse
import json
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import exchange, fetch, verification, verify          # noqa: E402

SITE = "https://marketsignals-app.github.io/market-signals-app/data.json"
DEFAULT_IN = ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS", "ITC.NS", "SBIN.NS", "LT.NS", "BHARTIARTL.NS", "HINDUNILVR.NS"]
DEFAULT_US = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "JPM", "XOM", "WMT"]


def load_app_data(source):
    if not source.startswith("http"):
        return json.loads(Path(source).read_text(encoding="utf-8"))
    request = urllib.request.Request(source, headers={"User-Agent": fetch.USER_AGENT})
    try:
        body = urllib.request.urlopen(request, timeout=30).read()
    except urllib.error.URLError as err:
        if not isinstance(getattr(err, "reason", None), ssl.SSLCertVerificationError):
            raise
        context = ssl.create_default_context()           # still verifies the chain; only drops Python 3.13's extra strict X.509 flag
        context.verify_flags &= ~ssl.VERIFY_X509_STRICT
        body = urllib.request.urlopen(request, timeout=30, context=context).read()
    return json.loads(body)


def nse_reference(days=14):
    closes, volumes, dates = {}, {}, []
    now = datetime.now(timezone.utc)
    for back in range(days):
        day = now - timedelta(days=back)
        if day.weekday() >= 5:
            continue
        got = exchange.fetch_bhavcopy(day)
        if got:
            iso, rows = got
            dates.append(iso)
            closes[iso] = {k: v["close"] for k, v in rows.items()}
            volumes[iso] = {k: v["volume"] for k, v in rows.items()}
    return {"source": "NSE end-of-day file (bhavcopy)", "dates": sorted(dates), "closes": closes, "volumes": volumes,
            "fetched_at": now.isoformat(timespec="seconds")}


def run(args):
    data = load_app_data(args.data)
    stocks = {s["symbol"]: s for s in data["stocks"]}
    wanted = args.symbols or DEFAULT_IN + DEFAULT_US
    india = [s for s in wanted if s.endswith(".NS")]
    us = [s for s in wanted if not s.endswith(".NS")]
    rows, volume_rows = [], []

    if india:
        ref = nse_reference()
        for symbol in india:
            rec = stocks.get(symbol)
            if not rec:
                continue
            base = symbol[:-3]
            views = {"source": ref["source"], "dates": ref["dates"], "fetched_at": ref["fetched_at"],
                     "closes": {d: v.get(base) for d, v in ref["closes"].items()}}
            row_rec = {**rec, "generated_at": data.get("generated_at"), "previous_close": (rec.get("spark") or [None, None])[-2]}
            rows += [verification.verify_price(row_rec, views, "latest"), verification.verify_price(row_rec, views, "previous")]
            if args.volume:
                bars = fetch.fetch_chart(symbol, "1mo", 5)["bars"]
                for d, v in zip(bars["d"][-3:], bars["v"][-3:]):
                    volume_rows.append(verification.verify_volume(symbol, d, v, ref["volumes"].get(d, {}).get(base), ref["source"],
                                                                  provisional=bool(rec.get("provisional")) and d == rec["as_of"]))
                time.sleep(0.4)

    for symbol in us:
        rec = stocks.get(symbol)
        if not rec:
            continue
        time.sleep(2.5)                                    # Cboe asks clients to go gently
        history = verify.fetch_cboe_history(symbol)
        views = {"source": verify.SOURCES["US"], "dates": sorted(history), "closes": dict(history),
                 "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        row_rec = {**rec, "generated_at": data.get("generated_at"), "previous_close": (rec.get("spark") or [None, None])[-2]}
        rows += [verification.verify_price(row_rec, views, "latest"), verification.verify_price(row_rec, views, "previous")]

    print(f"App data generated {data.get('generated_at')}; checked {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    print("%-14s %-9s %-11s %10s %11s %9s %8s  %-10s %s" % ("symbol", "session", "date", "app", "reference", "diff", "diff %", "status", "note"))
    for r in rows:
        print("%-14s %-9s %-11s %10s %11s %9s %8s  %-10s %s" % (r["symbol"], r["session"], r["trading_date"], r["app_price"], r["reference_price"],
                                                                 r["difference"], r["difference_pct"], r["status"], r["reason"]))
    print("price rows:", verification.summarise(rows))
    if volume_rows:
        print("\nvolume (Yahoo, as the app uses it, against the NSE final volume):")
        for r in volume_rows:
            print("%-14s %-11s app %12s  NSE %12s  ratio %-9s %-10s %s" % (r["symbol"], r["trading_date"], r["app_volume"], r["reference_volume"], r["ratio"], r["status"], r["reason"]))
        print("volume rows:", verification.summarise(volume_rows))
    if args.json:
        Path(args.json).write_text(json.dumps({"generated_at": data.get("generated_at"), "price": rows, "volume": volume_rows}, indent=1), encoding="utf-8")
        print("wrote", args.json)
    return 1 if any(r["status"] == verification.FAILED for r in rows + volume_rows) else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", default=SITE, help="URL or path of the data.json to check (default: the live site)")
    parser.add_argument("--symbols", nargs="*", help="symbols to check (default: 10 NSE and 10 US stocks)")
    parser.add_argument("--volume", action="store_true", help="also compare the last three NSE volumes with the NSE final volume")
    parser.add_argument("--json", help="write the full result to this file")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
