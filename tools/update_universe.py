"""Bring config/universe.json up to date: every current Nifty 200 stock gets the full analysis (readings, backtests), plus any extras.

    python tools/update_universe.py                       # add missing Nifty 200 stocks
    python tools/update_universe.py --extra GROWW.NS:Financials

Existing entries (and their tags) are never changed or removed. New ones take their name and sector from the NSE's own Nifty 200 list. The
sector is the NSE's industry mapped to the app's sector names. Stocks outside this list are still covered by the wide universe (live
quotes, movers, headlines, deals); see engine/wide.py.
"""
import argparse
import csv
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import fetch                                              # noqa: E402
from engine.universe_sync import URL, merge, parse_nifty, sector_of   # noqa: E402,F401

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--extra", nargs="*", default=[], help="SYMBOL.NS:Sector entries to add as well")
    args = parser.parse_args(argv)
    path = ROOT / "config" / "universe.json"
    universe = json.loads(path.read_text(encoding="utf-8"))
    found = parse_nifty(fetch.http_get(URL, timeout=40).decode("utf-8", "replace"))
    extras = []
    for item in args.extra:
        symbol, _, sector = item.partition(":")
        names = {s: n for s, n, _ in found}
        extras.append((symbol, names.get(symbol) or symbol.replace(".NS", ""), sector or "Other"))
    universe["stocks"], added = merge(universe["stocks"], found, extras)
    path.write_text(json.dumps(universe, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(added)} stocks added; {sum(1 for s in universe['stocks'] if s[2] == 'IN')} Indian and {sum(1 for s in universe['stocks'] if s[2] == 'US')} US stocks in the curated list")
    return 0


if __name__ == "__main__":
    sys.exit(main())
