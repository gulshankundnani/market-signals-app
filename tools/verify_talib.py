"""Check the engine's indicators against TA-Lib, a widely used C library, on live Yahoo bars.

    pip install TA-Lib numpy          (dev only: the app itself needs nothing but the Python standard library)
    python tools/verify_talib.py [--symbols A.NS B.NS ...]

TA-Lib is a second, independent implementation of SMA, RSI (Wilder), MACD (12/26/9) and ATR (Wilder 14). The difference on the last bar is
printed per indicator; exponentially weighted indicators depend on how they are started, so over five years of bars a small tolerance is used.
Exits 1 on any FAIL.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import fetch, signals          # noqa: E402

SAMPLE = ["RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ITC.NS", "SBIN.NS", "TMPV.NS", "LT.NS", "MARUTI.NS", "SUNPHARMA.NS",
          "BAJFINANCE.NS", "BHARTIARTL.NS", "ONGC.NS", "NTPC.NS", "TITAN.NS"]
TOLERANCE = {"SMA 20": 1e-9, "SMA 50": 1e-9, "SMA 200": 1e-9, "RSI 14": 2e-3, "MACD line": 5e-3, "MACD signal": 5e-3, "ATR 14": 2e-3}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbols", nargs="*", default=SAMPLE)
    args = parser.parse_args(argv)
    try:
        import numpy
        import talib
    except ImportError:
        print("TA-Lib and numpy are needed for this check: pip install TA-Lib numpy")
        return 2
    rows = {name: [] for name in TOLERANCE}
    for symbol in args.symbols:
        try:
            bars = fetch.fetch_chart(symbol, "5y")["bars"]
        except fetch.FetchError:
            continue
        ind = signals.build_indicators(bars)
        i = len(bars["c"]) - 1
        c, h, l = (numpy.array(bars[k], dtype=float) for k in ("c", "h", "l"))
        macd, macd_signal, _ = talib.MACD(c, 12, 26, 9)
        reference = {"SMA 20": talib.SMA(c, 20)[-1], "SMA 50": talib.SMA(c, 50)[-1], "SMA 200": talib.SMA(c, 200)[-1], "RSI 14": talib.RSI(c, 14)[-1],
                     "MACD line": macd[-1], "MACD signal": macd_signal[-1], "ATR 14": talib.ATR(h, l, c, 14)[-1]}
        engine = {"SMA 20": ind["sma20"][i], "SMA 50": ind["sma50"][i], "SMA 200": ind["sma200"][i], "RSI 14": ind["rsi"][i],
                  "MACD line": ind["macd"][i], "MACD signal": ind["macd_signal"][i], "ATR 14": ind["atr"][i]}
        for name, ref in reference.items():
            if engine[name] is None or ref != ref:
                continue
            rows[name].append((abs(engine[name] - ref) / max(abs(ref), 1e-9), symbol))
    fails = 0
    for name, results in rows.items():
        if not results:
            print(f"SKIP  {name}: nothing compared")
            continue
        worst = max(results)
        bad = sum(1 for d, _ in results if d > TOLERANCE[name])
        fails += bad
        print(f"{'FAIL' if bad else 'PASS'}  {name}: {len(results) - bad} pass, {bad} fail, largest relative difference {worst[0]:.2e} ({worst[1]})")
    print("RESULT:", "FAIL" if fails else "all checks pass")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
