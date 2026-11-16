"""Track record: every call the app announces is recorded, later checked against what the price did, and summarised.

A call is a signal that moved into Buy or Sell, with its stop-loss and target. Records are JSON Lines files (one
JSON object per line), only ever appended to, so the history cannot be quietly edited. Calls and outcomes are
kept in separate files:

    calls-YYYY-MM.jsonl      what was announced, and when
    outcomes-YYYY-MM.jsonl   what happened: target hit, stop-loss hit, or expired

The app measures and reports accuracy. It never changes its own rules.
"""
import bisect
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

from . import signals

MODEL = "v3"          # v2: score components change smoothly with the price instead of in hard steps
                      # v3: readings use completed daily candles only, and missing volume is no longer read as zero
EXPIRY_DAYS = {"short": 20, "long": 120}      # trading days before a call counts as expired
MIN_SAMPLE = 30                               # below this, percentages are shown as unreliable
MIN_GROUP = 10                                # smallest group compared in the component insights
BANDS = ((25, 40, "25-39"), (40, 60, "40-59"), (60, 1000, "60+"))
RECENT = 25
PENDING_LIMIT = 200


def utc_date(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def _round(value, digits=4):
    return None if value is None else round(float(value), digits)


def make_call(record, symbol, horizon, new_side, announced, now_ts):
    """The call for a signal that has just moved into Buy or Sell. None if no stop and target can be worked out."""
    if new_side not in ("buy", "sell"):
        return None
    signal = record.get(horizon) or {}
    indicators = record.get("indicators") or {}
    levels = signals.target_levels(record.get("price"), indicators.get("atr"), (record.get("levels") or {}).get("zones"),
                                   indicators.get("hi252"), indicators.get("lo252"), new_side)
    if not levels:
        return None
    date = utc_date(now_ts)
    parts = {r["key"]: r["value"] for r in (signal.get("reasons") or []) if isinstance(r, dict) and "key" in r and "value" in r}
    return {
        "type": "call", "id": hashlib.sha1(f"{symbol}|{horizon}|{date}|{new_side}".encode()).hexdigest()[:12],
        "ts": int(now_ts), "date": date, "symbol": symbol, "name": record.get("name"), "market": record.get("market"),
        "sector": record.get("sector"), "horizon": horizon, "from": announced, "side": new_side,
        "label": signal.get("label"), "score": signal.get("score"), "price": _round(record["price"]),
        "currency": record.get("currency"), "stop": _round(levels["stop"]["price"]),
        "target": _round(levels[horizon]["price"]), "target_basis": levels[horizon]["basis"],
        "expiry_days": EXPIRY_DAYS[horizon], "parts": parts, "model": MODEL,
    }


def evaluate_call(call, bars):
    """What the price did after a call. Returns an outcome dict, or None while the call is still open.

    The bar of the announcement day is skipped, because the call was made part-way through it. Each later bar is
    checked in order: a gap through the stop or target fills at the open, and a bar that touches both the stop and
    the target counts as a stop, the cautious choice. A call expires after its horizon's number of trading days.
    """
    dates, opens, highs, lows, closes = bars["d"], bars["o"], bars["h"], bars["l"], bars["c"]
    start = bisect.bisect_right(dates, call["date"])
    if start >= len(dates):
        return None
    entry, stop, target = call["price"], call["stop"], call["target"]
    buy = call["side"] == "buy"
    sign = 1 if buy else -1
    expiry = call.get("expiry_days") or EXPIRY_DAYS.get(call["horizon"], 20)
    best = worst = 0.0

    def outcome(kind, fill, index):
        return {"type": "outcome", "id": call["id"], "resolved_date": dates[index], "outcome": kind,
                "exit_price": _round(fill), "return_pct": _round(sign * (fill / entry - 1) * 100, 2),
                "days_held": index - start + 1, "max_gain_pct": _round(best, 2), "max_loss_pct": _round(worst, 2)}

    for j in range(start, min(len(dates), start + expiry)):
        if buy:
            best = max(best, (highs[j] / entry - 1) * 100)
            worst = min(worst, (lows[j] / entry - 1) * 100)
            if opens[j] <= stop:
                return outcome("stop", opens[j], j)
            if opens[j] >= target:
                return outcome("target", opens[j], j)
            hit_stop, hit_target = lows[j] <= stop, highs[j] >= target
        else:
            best = max(best, (entry / lows[j] - 1) * 100)
            worst = min(worst, (entry / highs[j] - 1) * 100)
            if opens[j] >= stop:
                return outcome("stop", opens[j], j)
            if opens[j] <= target:
                return outcome("target", opens[j], j)
            hit_stop, hit_target = highs[j] >= stop, lows[j] <= target
        if hit_stop:
            return outcome("stop", stop, j)
        if hit_target:
            return outcome("target", target, j)
    if len(dates) - start >= expiry:
        return outcome("expired", closes[start + expiry - 1], start + expiry - 1)
    return None


def _close_on_or_before(series, date):
    """Close from a {date: close} mapping on the last date not after the given one."""
    keys = sorted(k for k in series if k <= date)
    return series[keys[-1]] if keys else None


def excess_return(call, outcome, benchmark):
    """Return of the call minus the market's return over the same dates, in direction-adjusted percent."""
    if not benchmark:
        return None
    start, end = _close_on_or_before(benchmark, call["date"]), _close_on_or_before(benchmark, outcome["resolved_date"])
    if not start or not end:
        return None
    sign = 1 if call["side"] == "buy" else -1
    return _round(outcome["return_pct"] - sign * (end / start - 1) * 100, 2)


class Ledger:
    """Append-only call and outcome records in a folder of JSON Lines files."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.calls, self.outcomes = {}, {}
        self.skipped_lines = 0
        self._new = {"calls": [], "outcomes": []}

    def load(self):
        for kind, store in (("calls", self.calls), ("outcomes", self.outcomes)):
            for path in sorted(self.directory.glob(f"{kind}-*.jsonl")):
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        item = json.loads(line)
                        store[item["id"]] = item
                    except (ValueError, KeyError, TypeError):
                        self.skipped_lines += 1
        return self

    def add_call(self, call):
        if call["id"] in self.calls:
            return False
        self.calls[call["id"]] = call
        self._new["calls"].append(call)
        return True

    def add_outcome(self, outcome):
        if outcome["id"] in self.outcomes or outcome["id"] not in self.calls:
            return False
        self.outcomes[outcome["id"]] = outcome
        self._new["outcomes"].append(outcome)
        return True

    def open_calls(self):
        return [c for cid, c in self.calls.items() if cid not in self.outcomes]

    def resolve_open(self, bars_by_symbol, benchmarks):
        """Check every open call against the latest price history. Returns [(call, outcome)] resolved now."""
        resolved = []
        for call in self.open_calls():
            bars = bars_by_symbol.get(call["symbol"])
            if not bars:
                continue
            outcome = evaluate_call(call, bars)
            if outcome is None:
                continue
            outcome["excess_pct"] = excess_return(call, outcome, (benchmarks or {}).get(call.get("market")))
            if self.add_outcome(outcome):
                resolved.append((call, outcome))
        return resolved

    def save(self):
        """Append what is new to the monthly files. Returns the number of lines written."""
        self.directory.mkdir(parents=True, exist_ok=True)
        written = 0
        for kind, date_key in (("calls", "date"), ("outcomes", "resolved_date")):
            by_month = {}
            for item in self._new[kind]:
                by_month.setdefault(item[date_key][:7], []).append(item)
            for month, items in by_month.items():
                with open(self.directory / f"{kind}-{month}.jsonl", "a", encoding="utf-8", newline="\n") as handle:
                    for item in items:
                        handle.write(json.dumps(item, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")
                        written += 1
            self._new[kind] = []
        return written


# ---------- statistics ----------

def wilson(successes, n, z=1.96):
    """95% confidence interval for a proportion, in percent. None for an empty group."""
    if n <= 0:
        return None
    p = successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return [round(max(0.0, centre - half) * 100, 1), round(min(1.0, centre + half) * 100, 1)]


def _mean(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 2) if values else None


def group_stats(rows):
    """Accuracy figures for a group of resolved calls. rows: [{"call", "outcome"}]."""
    n = len(rows)
    outcomes = [r["outcome"] for r in rows]
    returns = [o["return_pct"] for o in outcomes]
    wins = sum(1 for x in returns if x > 0)
    targets = sum(1 for o in outcomes if o["outcome"] == "target")
    stops = sum(1 for o in outcomes if o["outcome"] == "stop")
    gains = sum(x for x in returns if x > 0)
    losses = -sum(x for x in returns if x < 0)
    return {
        "n": n, "reliable": n >= MIN_SAMPLE,
        "target_pct": round(targets / n * 100, 1) if n else None, "target_ci": wilson(targets, n),
        "stop_pct": round(stops / n * 100, 1) if n else None,
        "expired_pct": round((n - targets - stops) / n * 100, 1) if n else None,
        "win_pct": round(wins / n * 100, 1) if n else None, "win_ci": wilson(wins, n),
        "avg_return": _mean(returns), "median_return": round(median(returns), 2) if returns else None,
        "avg_excess": _mean([o.get("excess_pct") for o in outcomes]),
        "avg_days": _mean([o["days_held"] for o in outcomes]),
        "profit_factor": round(gains / losses, 2) if losses > 0 else None,
    }


def band_of(score):
    value = abs(score) if isinstance(score, (int, float)) and not isinstance(score, bool) else None
    for low, high, label in BANDS:
        if value is not None and low <= value < high:
            return label
    return None


def component_insights(rows, horizon):
    """For each component: how calls it supported did against calls it argued against. Informational only."""
    keys = sorted({k for r in rows for k in r["call"].get("parts", {})})
    insights = []
    for key in keys:
        supporting, against = [], []
        for r in rows:
            value = r["call"].get("parts", {}).get(key)
            if value is None:
                continue
            adjusted = value * (1 if r["call"]["side"] == "buy" else -1)
            if adjusted >= 0.5:
                supporting.append(r)
            elif adjusted <= -0.15:
                against.append(r)
        entry = {"horizon": horizon, "component": key, "supporting": group_stats(supporting), "against": group_stats(against)}
        entry["comparable"] = len(supporting) >= MIN_GROUP and len(against) >= MIN_GROUP
        insights.append(entry)
    return insights


HORIZON_NAMES = {"short": "Weeks view", "long": "Months-to-years view"}
SIDE_NAMES = {"buy": "bullish", "sell": "bearish"}


def suggestions(overall, by_group, insights):
    """Plain-language observations for a person to review. Nothing here changes the app's rules."""
    notes = []
    if overall["n"] < MIN_SAMPLE:
        notes.append({"text": f"Only {overall['n']} readings have resolved so far and {MIN_SAMPLE} are needed before percentages mean much. Treat every figure here as noise for now.", "evidence": f"n={overall['n']}"})
        return notes
    if overall["avg_return"] is not None and overall["avg_return"] < 0:
        notes.append({"text": f"On average, the price moved {abs(overall['avg_return'])}% against the readings. Review the rules before relying on them.", "evidence": f"n={overall['n']}"})
    for name, group in by_group.items():
        if not group["reliable"]:
            continue
        if group["stop_pct"] is not None and group["stop_pct"] >= 60:
            notes.append({"text": f"{name}: {group['stop_pct']}% of readings reached the volatility risk level first. The level (2 x average daily range) may be too tight for these stocks.", "evidence": f"n={group['n']}"})
        if group["expired_pct"] is not None and group["expired_pct"] >= 50:
            notes.append({"text": f"{name}: {group['expired_pct']}% of readings reached neither the reference level nor the risk level in time. The reference levels may be too far away.", "evidence": f"n={group['n']}"})
    for item in insights:
        sup, against = item["supporting"], item["against"]
        if not item["comparable"] or sup["avg_return"] is None or against["avg_return"] is None:
            continue
        if sup["avg_return"] < against["avg_return"] - 1.0:
            notes.append({"text": f"{HORIZON_NAMES[item['horizon']]} indicator '{item['component']}' is not helping: readings it supported moved {sup['avg_return']}% on average, readings it opposed moved {against['avg_return']}%.", "evidence": f"n={sup['n']} vs n={against['n']}"})
    return notes


def summarise(ledger, now_ts):
    """Everything the Track record tab shows.

    Only calls made by the current rule version are counted. Calls from earlier versions stay in the record, untouched, and
    keep being resolved, but mixing versions would blend results from different rules into one success rate."""
    current = {cid for cid, c in ledger.calls.items() if c.get("model") in (None, MODEL)}
    rows = [{"call": ledger.calls[cid], "outcome": out} for cid, out in ledger.outcomes.items() if cid in current]
    rows.sort(key=lambda r: (r["outcome"]["resolved_date"], r["call"]["id"]))
    overall = group_stats(rows)
    cutoff = utc_date(now_ts - 90 * 86400)
    last90 = group_stats([r for r in rows if r["outcome"]["resolved_date"] >= cutoff])

    by_group, by_band, insights = {}, {}, []
    for horizon in ("short", "long"):
        for side in ("buy", "sell"):
            subset = [r for r in rows if r["call"]["horizon"] == horizon and r["call"]["side"] == side]
            by_group[f"{HORIZON_NAMES[horizon]}, {SIDE_NAMES[side]}"] = group_stats(subset)
            for low, high, label in BANDS:
                members = [r for r in subset if band_of(r["call"].get("score")) == label]
                if members:
                    by_band[f"{HORIZON_NAMES[horizon]}, {SIDE_NAMES[side]} / alignment {label}"] = group_stats(members)
        insights += component_insights([r for r in rows if r["call"]["horizon"] == horizon], horizon)
    by_market = {m: group_stats([r for r in rows if r["call"].get("market") == m]) for m in sorted({r["call"].get("market") for r in rows if r["call"].get("market")})}

    notes = suggestions(overall, by_group, insights)

    def recent_row(r):
        c, o = r["call"], r["outcome"]
        return {"symbol": c["symbol"], "name": c.get("name"), "horizon": c["horizon"], "side": c["side"], "label": c.get("label"),
                "score": c.get("score"), "date": c["date"], "price": c["price"], "target": c["target"], "stop": c["stop"],
                "currency": c.get("currency"), "resolved_date": o["resolved_date"], "outcome": o["outcome"],
                "return_pct": o["return_pct"], "days_held": o["days_held"]}

    open_calls = sorted((c for c in ledger.open_calls() if c["id"] in current), key=lambda c: c["ts"], reverse=True)
    return {
        "generated_at": datetime.fromtimestamp(now_ts, timezone.utc).isoformat(timespec="seconds"),
        "model": MODEL, "min_sample": MIN_SAMPLE, "calls_total": len(current), "older_model_calls": len(ledger.calls) - len(current), "open": len(open_calls), "resolved": len(rows),
        "expiry_days": EXPIRY_DAYS, "overall": overall, "last_90_days": last90, "by_group": by_group, "by_score_band": by_band,
        "by_market": by_market, "components": insights, "suggestions": notes,
        "recent": [recent_row(r) for r in reversed(rows[-RECENT:])],
        "open_calls": [{"symbol": c["symbol"], "name": c.get("name"), "horizon": c["horizon"], "side": c["side"], "date": c["date"],
                        "price": c["price"], "target": c["target"], "stop": c["stop"], "currency": c.get("currency")} for c in open_calls[:RECENT]],
    }
