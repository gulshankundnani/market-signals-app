"""Run the two fixed rules on stocks that no longer trade, from price files YOU supply, and report them apart from the live list.

Free sources do not provide this data (Yahoo has no history for most delisted tickers, and some tickers now belong to new companies),
so the app cannot fix survivorship bias on its own. If you have point-in-time prices for failed or removed companies (a data vendor,
the exchange's historical files, a library such as CRSP), put one CSV per company in data/delisted/ named SYMBOL.csv with the columns

    date,open,high,low,close,volume        (ISO dates, oldest first, split-adjusted; volume may be empty)

and run:   python tools/survivorship_probe.py [--market IN|US]

It tests each file with the same rules, costs and dividend-free price series as the published backtests, and prints the rule against
buy-and-hold for the failed names only. Nothing here changes the published figures, and the app keeps saying its results are NOT free
of survivorship bias until a complete point-in-time universe is in use.
"""
import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import backtest as bt, costs, signals          # noqa: E402

FOLDER = ROOT / "data" / "delisted"


def read_bars(path):
    """Bars in the engine's layout from a price CSV. Bad rows are skipped; an empty volume stays missing (None), never zero."""
    bars = {"t": [], "d": [], "o": [], "h": [], "l": [], "c": [], "v": []}
    with open(path, encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                o, h, l, c = (float(row[k]) for k in ("open", "high", "low", "close"))
            except (KeyError, TypeError, ValueError):
                continue
            if min(o, h, l, c) <= 0 or l > h:
                continue
            raw = (row.get("volume") or "").strip()
            try:
                volume = float(raw) if raw else None
            except ValueError:
                volume = None
            bars["t"].append(len(bars["t"]))
            bars["d"].append((row.get("date") or "").strip())
            for key, value in zip("ohlc", (o, h, l, c)):
                bars[key].append(value)
            bars["v"].append(volume)
    return bars


def probe(bars, market, overrides=None):
    """{rule: backtest result or None} for both rules."""
    ind = signals.build_indicators(bars)
    n = len(bars["c"])
    entry, exit_ = costs.sides(market, overrides)
    out = {}
    for name, fn in (("short", lambda i: signals.short_score(ind, i)[0]), ("long", lambda i: signals.long_score(ind, i)[0])):
        start = next((i for i in range(n - bt.MIN_DAYS) if fn(i) is not None), None)
        out[name] = bt.backtest(bars, fn, start, entry_cost=entry, exit_cost=exit_) if start is not None else None
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--market", choices=("IN",), default="IN", help="cost model to use")
    parser.add_argument("--folder", default=str(FOLDER))
    args = parser.parse_args(argv)
    files = sorted(Path(args.folder).glob("*.csv"))
    if not files:
        print(f"No files in {args.folder}. See this script's help for the format. Nothing was tested.")
        return 0
    print("%-12s %-6s %6s %9s %9s %8s" % ("symbol", "rule", "days", "rule", "hold", "drawdown"))
    for path in files:
        bars = read_bars(path)
        if len(bars["c"]) < 260:
            print(f"{path.stem:<12} skipped: {len(bars['c'])} usable bars, at least 260 needed")
            continue
        for rule, result in probe(bars, args.market).items():
            if result:
                print("%-12s %-6s %6d %+8.1f%% %+8.1f%% %7.1f%%" % (path.stem, rule, result["tested_days"], result["total_return"] * 100, result["buy_hold_return"] * 100, result["max_drawdown"] * 100))
    return 0


if __name__ == "__main__":
    sys.exit(main())
