"""Write config/membership.json: the date each US stock in the app's list joined the S&P 500, from Wikipedia's constituents table.

    python tools/build_membership.py

Stocks that are not in the table (or have no full date) are left out, and a backtest then uses their whole history. India is not
covered: the NSE does not publish addition dates in a machine-readable form.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import fetch, membership          # noqa: E402

URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def main():
    universe = json.loads((ROOT / "config" / "universe.json").read_text(encoding="utf-8"))
    us = [s[0] for s in universe["stocks"] if s[2] == "US"]
    added = membership.parse_sp500_added(fetch.http_get(URL, timeout=40).decode("utf-8", "replace"))
    out = {"US": {s: {"from": added[s]} for s in us if s in added}, "IN": {},
           "_sources": {"US": membership.SOURCE_US, "IN": "none: the NSE publishes no machine-readable addition dates"},
           "_fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "_not_found": [s for s in us if s not in added]}
    (ROOT / "config" / "membership.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"{len(out['US'])} of {len(us)} US stocks have an addition date; not found: {out['_not_found']}")


if __name__ == "__main__":
    main()
