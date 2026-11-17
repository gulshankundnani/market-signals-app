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

from engine import fetch, wide          # noqa: E402

URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv"
SECTORS = {"Financial Services": "Financials", "Capital Goods": "Industrials", "Construction": "Industrials", "Services": "Industrials",
           "Healthcare": "Pharma and Health", "Automobile and Auto Components": "Auto", "Fast Moving Consumer Goods": "FMCG",
           "Metals & Mining": "Metals and Mining", "Consumer Services": "Consumer", "Consumer Durables": "Consumer", "Information Technology": "IT",
           "Power": "Power", "Oil Gas & Consumable Fuels": "Energy", "Realty": "Real Estate", "Chemicals": "Materials",
           "Construction Materials": "Materials", "Textiles": "Materials", "Telecommunication": "Telecom", "Media Entertainment & Publication": "Consumer",
           "Diversified": "Industrials", "Forest Materials": "Materials"}


def sector_of(industry, name):
    """The app's sector for an NSE industry. Banks are split out of Financial Services by name, as the curated list does."""
    if industry == "Financial Services" and "bank" in (name or "").lower():
        return "Banks"
    return SECTORS.get(industry, "Other")


def parse_nifty(text):
    """[(symbol.NS, short name, sector)] from the NSE index constituent CSV."""
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        symbol = (row.get("Symbol") or "").strip()
        if symbol and (row.get("Series") or "EQ").strip() == "EQ":
            name = (row.get("Company Name") or symbol).strip()
            out.append((symbol + ".NS", wide.short_name(name), sector_of((row.get("Industry") or "").strip(), name)))
    return out


def merge(stocks, found, extras=()):
    """(new stocks list, added symbols): found entries that are not already present, then the extras."""
    known = {s[0] for s in stocks}
    added = []
    for symbol, name, sector in list(found) + list(extras):
        if symbol not in known:
            stocks.append([symbol, name, "IN", sector, []])
            known.add(symbol)
            added.append(symbol)
    return stocks, added


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
