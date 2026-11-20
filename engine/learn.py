"""The weekly learning job: python -m engine.learn --out DIR [--publish] [--limit N]

1. Reads five years of daily bars for the tracked NSE stocks from Yahoo.
2. Learns the past frequencies (engine/learning.py) and tests them on later data.
3. With --publish: logs today's forecasts on the `data` branch (append-only), resolves the forecasts whose horizon has passed, scores them against the
   base rate, and writes learning/outlook-table.json there. The 10-minute build reads that file, so the page always has the latest learning.

Nothing here changes a reading, a threshold or an alert.
"""
import argparse
import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import fetch, learning, ledger_git, signals          # noqa: E402

WORKERS = 4


def load_bars(symbols):
    def get(symbol):
        try:
            return symbol, fetch.fetch_chart(symbol, "5y")["bars"]
        except fetch.FetchError as err:
            print(f"  {symbol}: {str(err)[:80]}")
            return symbol, None

    with ThreadPoolExecutor(WORKERS) as pool:
        return {s: b for s, b in pool.map(get, symbols) if b}


def current_records(bars_by_symbol, learned):
    """Today's outlook for each stock from its last bar: [{"symbol", "price", "outlook"}]."""
    out = []
    for symbol, bars in bars_by_symbol.items():
        if len(bars["c"]) < learning.START + 1:
            continue
        ind = signals.build_indicators(bars)
        i = len(bars["c"]) - 1
        record = {"symbol": symbol, "price": bars["c"][i], "indicators": {k: v[i] for k, v in ind.items() if isinstance(v, list)}}
        record["outlook"] = learning.outlook(record, learned)
        out.append(record)
    return out


def read_jsonl(folder):
    rows = []
    for path in sorted(Path(folder).glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def append_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")


def score_forecasts(folder, bars_by_symbol, base_rate, today):
    """Resolve what is due, append the results, and return the scorecard over every resolved forecast."""
    forecasts = {f["id"]: f for f in read_jsonl(Path(folder) / "forecasts")}
    results = {r["id"]: r for r in read_jsonl(Path(folder) / "results")}
    due = [f for fid, f in forecasts.items() if fid not in results]
    newly = learning.resolve_forecasts(due, bars_by_symbol)
    append_jsonl(Path(folder) / "results" / f"results-{today[:7]}.jsonl", [{"id": f["id"], "up": y, "resolved": today} for f, y in newly])
    for f, y in newly:
        results[f["id"]] = {"id": f["id"], "up": y}
    resolved = [(forecasts[fid], r["up"]) for fid, r in results.items() if fid in forecasts]
    return learning.scorecard(resolved, base_rate), len(forecasts), len(newly)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="learning_out", help="folder for learning.json")
    parser.add_argument("--publish", action="store_true", help="log and score forecasts on the data branch and publish the table there")
    parser.add_argument("--limit", type=int, default=0, help="only the first N stocks (testing)")
    parser.add_argument("--data-dir", default="", help="use this folder as the data branch checkout (no git); for testing")
    args = parser.parse_args(argv)

    config = json.loads((ROOT / "config" / "universe.json").read_text(encoding="utf-8"))
    symbols = [s[0] for s in config["stocks"] if s[2] == "IN"]
    if args.limit:
        symbols = symbols[: args.limit]
    print(f"Reading five years of prices for {len(symbols)} stocks")
    bars = load_bars(symbols)
    print(f"{len(bars)} stocks loaded")
    if len(bars) < 20:
        print("Too few stocks loaded to learn from; nothing written.")
        return 1
    learned = learning.learn(bars)
    for h, block in learned["horizons"].items():
        v = block["validation"]
        print(f"  {h}-day: {len(block['table'])} situations, base rate {(block['table'].get('ALL') or {}).get('p_up')}, later-data test: {v.get('reason')} (skill {v.get('skill')})")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    records = current_records(bars, learned)
    base20 = ((learned["horizons"].get("20") or {}).get("table") or {}).get("ALL", {}).get("p_up")
    folder = args.data_dir
    remote = ""
    if args.publish and not folder:
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        remote = f"https://github.com/{repo}.git" if repo else ""
        folder = str(Path(tempfile.mkdtemp()) / "data")
        if remote:
            ledger_git.prepare(folder, remote, token=os.environ.get("GITHUB_TOKEN") or None)
    if folder:
        base = Path(folder) / "learning"
        existing = {f["id"] for f in read_jsonl(base / "forecasts")}
        fresh = [f for f in learning.make_forecasts(records, learned, today) if f["id"] not in existing]
        append_jsonl(base / "forecasts" / f"forecasts-{today[:7]}.jsonl", fresh)
        card, total, newly = score_forecasts(base, bars, base20 or 0.5, today)
        learned["scorecard"] = card
        print(f"Forecasts: {len(fresh)} logged today, {newly} resolved now, {total} in all; scorecard {card.get('n')} resolved")
        (base).mkdir(parents=True, exist_ok=True)
        (base / "outlook-table.json").write_text(json.dumps(learned, separators=(",", ":"), allow_nan=False), encoding="utf-8")
        if remote:
            pushed = ledger_git.publish(folder, f"Learning {today}", token=os.environ.get("GITHUB_TOKEN") or None)
            print("Published to the data branch" if pushed else "Nothing new to publish")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "learning.json").write_text(json.dumps(learned, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    print(f"Wrote {out / 'learning.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
