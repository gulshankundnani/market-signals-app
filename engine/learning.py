"""Learning from the past: what happened after situations like today's, and whether that knowledge has beaten a plain base rate on later data.

This is the app's "outlook". It is NOT a prediction engine and never promises one. For each stock it describes today's factors (trend, RSI zone, MACD
direction, position in the 52-week range, volatility), looks up how often the price was higher N trading days after the same combination of factors in
the past (all tracked NSE stocks, up to five years), and reports that frequency with an honest range. Three safeguards:

  * Back-off: a combination seen fewer than MIN_SAMPLES times falls back to a coarser one (fewer factors), down to the all-situations base rate.
  * Uncertainty: the range comes from resampling whole trading DATES, not single observations, because stocks move together on a day and treating
    them as independent would make the range far too narrow.
  * Out-of-sample test: the table is built on the earlier 60% of dates and scored (Brier score against the base rate) on the later 40%. The outlook is
    marked as having an "edge" only if the table beat the base rate there by at least MIN_SKILL with the whole range above zero; otherwise the page says it is no better than
    the base rate and shows it as context only.

Limits stated plainly: past fundamentals are not available point in time, so only price and volume factors are learned; prices are gross of costs
and dividends; the stock list is today's (survivorship bias); the same market conditions may not return. Nothing here changes a reading, a threshold
or an alert, and nothing is tuned to beat buy-and-hold.
"""
import bisect
import math
import random
from datetime import datetime, timezone

from . import signals

VERSION = 1
HORIZONS = (5, 20, 60)                       # trading days ahead
STRIDE = {5: 5, 20: 10, 60: 20}              # one sample per stock every STRIDE days (less overlap between samples)
START = 260                                  # bars needed for the 200-day average and the 52-week range
MIN_SAMPLES = 150                            # a combination seen fewer times than this falls back to a coarser one
TRAIN_SHARE = 0.6
BOOTSTRAP = 300
MIN_SKILL = 0.01                             # a gain over the base rate smaller than 1% of its error is real but too small to matter
LEVELS = ("L3", "L2", "L1", "L0")


# ---------- today's factors ----------

def factors(ind, close, i):
    """The bucketed factors at bar i, or None when an input is missing. Each is a short code and a plain-words description."""
    price = close[i]
    sma50, sma200, rsi, hist = ind["sma50"][i], ind["sma200"][i], ind["rsi"][i], ind["macd_hist"][i]
    hi, lo, atr, mom = ind["hi252"][i], ind["lo252"][i], ind["atr"][i], ind["momentum126"][i]
    if None in (sma50, sma200, rsi, hist, hi, lo, atr, mom) or not price or hi == lo:
        return None
    above = (price > sma50) + (price > sma200)
    trend = ("T0", "below both averages") if above == 0 else ("T1", "above one of the 50 and 200-day averages") if above == 1 else ("T2", "above both averages")
    zone = ("R0", "RSI oversold (under 30)") if rsi < 30 else ("R1", "RSI weak (30 to 45)") if rsi < 45 else ("R2", "RSI neutral (45 to 55)") if rsi < 55 \
        else ("R3", "RSI firm (55 to 70)") if rsi < 70 else ("R4", "RSI overbought (over 70)")
    macd = ("M1", "MACD above its signal") if hist > 0 else ("M0", "MACD below its signal")
    position = (price - lo) / (hi - lo)
    pos = ("P0", "in the bottom fifth of the 52-week range") if position < 0.2 else ("P2", "in the top fifth of the 52-week range") if position > 0.8 \
        else ("P1", "in the middle of the 52-week range")
    swing = atr / price
    vol = ("V0", "calm (daily swing under 1.5%)") if swing < 0.015 else ("V1", "normal swings (1.5% to 2.5%)") if swing < 0.025 else ("V2", "wide swings (over 2.5%)")
    return {"trend": trend, "rsi": zone, "macd": macd, "position": pos, "volatility": vol, "momentum": ("Q0", "negative 6-month return") if mom < 0 else ("Q1", "positive 6-month return")}


def keys_of(f):
    """The lookup keys from the finest to the coarsest: [L3, L2, L1, L0]."""
    t, r, m, p = f["trend"][0], f["rsi"][0], f["macd"][0], f["position"][0]
    return [f"{t}|{r}|{m}|{p}", f"{t}|{r}|{m}", f"{t}|{m}", "ALL"]


# ---------- the past ----------

def replay(bars_by_symbol, stride=5):
    """One sample per stock every `stride` bars: {"date", "di", "symbol", "keys", "ret": {horizon: forward return or None}}. di is the date's global index."""
    dates = sorted({d for b in bars_by_symbol.values() for d in b["d"]})
    index = {d: n for n, d in enumerate(dates)}
    out = []
    for symbol, bars in bars_by_symbol.items():
        close = bars["c"]
        if len(close) < START + 30:
            continue
        ind = signals.build_indicators(bars)
        for i in range(START, len(close) - 1, stride):
            f = factors(ind, close, i)
            if f is None:
                continue
            ret = {h: (close[i + h] / close[i] - 1 if i + h < len(close) else None) for h in HORIZONS}
            if all(v is None for v in ret.values()):
                continue
            out.append({"date": bars["d"][i], "di": index[bars["d"][i]], "symbol": symbol, "keys": keys_of(f), "ret": ret, "i": i})
    return out, dates


def _quantile(sorted_values, q):
    if not sorted_values:
        return None
    pos = q * (len(sorted_values) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def _cluster_ci(per_date, rng):
    """95% range of the share of 'up' outcomes by resampling whole dates. per_date: [(ups, n)]."""
    if len(per_date) < 5:
        return None
    shares = []
    for _ in range(BOOTSTRAP):
        ups = n = 0
        for _k in range(len(per_date)):
            u, c = per_date[rng.randrange(len(per_date))]
            ups, n = ups + u, n + c
        if n:
            shares.append(ups / n)
    shares.sort()
    return [round(_quantile(shares, 0.025), 4), round(_quantile(shares, 0.975), 4)] if shares else None


def build_table(samples, horizon, seed=1):
    """{key: {"n", "dates", "p_up", "ci", "median", "p10", "p90"}} for every key with at least MIN_SAMPLES samples, for one horizon."""
    rng = random.Random(seed)
    groups = {}
    for s in samples:
        r = s["ret"].get(horizon)
        if r is None or (s["i"] - START) % STRIDE[horizon] != 0:
            continue
        for key in s["keys"]:
            groups.setdefault(key, []).append((s["date"], r))
    table = {}
    for key, rows in groups.items():
        if len(rows) < MIN_SAMPLES:
            continue
        by_date = {}
        for d, r in rows:
            u, c = by_date.get(d, (0, 0))
            by_date[d] = (u + (r > 0), c + 1)
        rets = sorted(r for _, r in rows)
        table[key] = {"n": len(rows), "dates": len(by_date), "p_up": round(sum(1 for r in rets if r > 0) / len(rets), 4),
                      "ci": _cluster_ci(list(by_date.values()), rng), "median": round(_quantile(rets, 0.5), 4),
                      "p10": round(_quantile(rets, 0.10), 4), "p90": round(_quantile(rets, 0.90), 4)}
    return table


def lookup(table, keys):
    """The entry for the finest key that has one, with the level it came from, or None."""
    for level, key in zip(LEVELS, keys):
        if key in table:
            return {**table[key], "level": level, "key": key}
    return None


# ---------- does it beat the base rate on later data? ----------

def walk_forward(samples, dates, horizon, seed=2):
    """Build on the earlier 60% of dates, score on the later 40%. Returns the Brier scores, the skill over the base rate and whether it is reliable."""
    split = int(len(dates) * TRAIN_SHARE)
    train = [s for s in samples if s["di"] + horizon <= split]            # outcomes already known at the split: no leakage
    test = [s for s in samples if s["di"] >= split and s["ret"].get(horizon) is not None and (s["i"] - START) % STRIDE[horizon] == 0]
    table = build_table(train, horizon)
    base_entry = table.get("ALL")
    if not base_entry or len(test) < MIN_SAMPLES:
        return {"horizon": horizon, "tested": len(test), "edge": False, "reason": "not enough samples to test"}
    base = base_entry["p_up"]
    loss_model, loss_base, by_date = 0.0, 0.0, {}
    hits = calls = 0
    for s in test:
        entry = lookup(table, s["keys"]) or base_entry
        y = 1.0 if s["ret"][horizon] > 0 else 0.0
        m, b = (entry["p_up"] - y) ** 2, (base - y) ** 2
        loss_model, loss_base = loss_model + m, loss_base + b
        d = by_date.setdefault(s["date"], [0.0, 0])
        d[0], d[1] = d[0] + (b - m), d[1] + 1
        if entry["p_up"] >= base + 0.05 or entry["p_up"] <= base - 0.05:          # the table leans one way by at least five points
            calls += 1
            hits += (entry["p_up"] > base) == (y == 1.0)
    n = len(test)
    rng = random.Random(seed)
    diffs = list(by_date.values())
    means = []
    for _ in range(BOOTSTRAP):
        total = count = 0.0
        for _k in range(len(diffs)):
            t, c = diffs[rng.randrange(len(diffs))]
            total, count = total + t, count + c
        means.append(total / count if count else 0.0)
    means.sort()
    low, high = _quantile(means, 0.025), _quantile(means, 0.975)
    skill = 1 - loss_model / loss_base if loss_base else None
    edge = bool(low > 0 and skill is not None and skill >= MIN_SKILL)
    return {"horizon": horizon, "train": len(train), "tested": n, "base_rate": round(base, 4), "brier_model": round(loss_model / n, 5), "brier_base": round(loss_base / n, 5),
            "skill": round(skill, 4) if skill is not None else None, "gain_ci": [round(low, 6), round(high, 6)],
            "directional_calls": calls, "directional_hit_pct": round(100 * hits / calls, 1) if calls else None,
            "edge": edge, "reason": "beat the base rate on later data" if edge else "did not beat the base rate on later data by a margin that matters, with a range above zero"}


def learn(bars_by_symbol, now=None):
    """The whole learning result: {"v", "generated_at", "universe", "span", "horizons": {h: {"table", "validation"}}}. Tables are built on ALL dates (for use now),
    validated by the earlier/later split."""
    samples, dates = replay(bars_by_symbol, stride=5)
    out = {"v": VERSION, "generated_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"), "universe": len(bars_by_symbol),
           "samples": len(samples), "span": [dates[0], dates[-1]] if dates else None, "horizons": {}}
    for h in HORIZONS:
        out["horizons"][str(h)] = {"table": build_table(samples, h), "validation": walk_forward(samples, dates, h)}
    return out


# ---------- today's outlook for one stock ----------

def outlook(record, learned):
    """record["outlook"]: today's factors and, for each horizon, the past frequency with its range. None when there is nothing to describe."""
    ind, bars = record.get("indicators") or {}, None
    if not learned or not ind or record.get("price") is None:
        return None
    # the record carries the last indicator values, not the series: wrap them as one-element series so factors() can be reused
    one = {k: [ind.get(k)] for k in ("sma50", "sma200", "rsi", "macd_hist", "hi252", "lo252", "atr", "momentum126")}
    if one["macd_hist"][0] is None and ind.get("macd") is not None and ind.get("macd_signal") is not None:
        one["macd_hist"] = [ind["macd"] - ind["macd_signal"]]
    f = factors(one, [record["price"]], 0)
    if f is None:
        return None
    keys = keys_of(f)
    out = {"factors": [{"name": name, "code": code, "text": text} for name, (code, text) in f.items()], "horizons": {}, "generated_at": learned.get("generated_at"),
           "span": learned.get("span"), "universe": learned.get("universe")}
    for h in HORIZONS:
        block = (learned.get("horizons") or {}).get(str(h)) or {}
        entry = lookup(block.get("table") or {}, keys)
        validation = block.get("validation") or {}
        if entry:
            out["horizons"][str(h)] = {"p_up": entry["p_up"], "ci": entry["ci"], "n": entry["n"], "dates": entry["dates"], "level": entry["level"], "median": entry["median"],
                                       "p10": entry["p10"], "p90": entry["p90"], "base": (block.get("table") or {}).get("ALL", {}).get("p_up"),
                                       "edge": bool(validation.get("edge")), "skill": validation.get("skill")}
    return out if out["horizons"] else None


# ---------- live accuracy of the forecasts the app has made ----------

def make_forecasts(records, learned, date_text, horizon=20):
    """One logged forecast per stock per day: the probability the table gave for the price being higher `horizon` trading days later."""
    rows = []
    for r in records:
        h = ((r.get("outlook") or {}).get("horizons") or {}).get(str(horizon))
        if h:
            rows.append({"id": f"{r['symbol']}|{date_text}|{horizon}", "symbol": r["symbol"], "date": date_text, "horizon": horizon, "p_up": h["p_up"],
                         "level": h["level"], "edge": h["edge"], "price": r["price"], "model": VERSION})
    return rows


def resolve_forecasts(forecasts, bars_by_symbol):
    """[(forecast, 1.0 or 0.0)] for the forecasts whose horizon has passed, using the completed bars. A missing date leaves the forecast open."""
    done = []
    for f in forecasts:
        bars = bars_by_symbol.get(f["symbol"])
        if not bars:
            continue
        start = bisect.bisect_right(bars["d"], f["date"]) - 1              # the bar on the day the forecast was made
        if start < 0 or bars["d"][start] > f["date"] or start + f["horizon"] >= len(bars["c"]):
            continue
        done.append((f, 1.0 if bars["c"][start + f["horizon"]] > bars["c"][start] else 0.0))
    return done


def scorecard(resolved, base_rate):
    """The Brier score of the forecasts against always saying the base rate, and how often each probability band came true."""
    if not resolved:
        return {"n": 0}
    n = len(resolved)
    model = sum((f["p_up"] - y) ** 2 for f, y in resolved) / n
    base = sum((base_rate - y) ** 2 for _, y in resolved) / n
    bins = {}
    for f, y in resolved:
        low = min(int(f["p_up"] * 10), 9) * 10
        b = bins.setdefault(low, [0, 0.0])
        b[0], b[1] = b[0] + 1, b[1] + y
    return {"n": n, "brier": round(model, 5), "brier_base": round(base, 5), "skill": round(1 - model / base, 4) if base else None,
            "bands": [{"from_pct": low, "to_pct": low + 10, "n": c, "came_true_pct": round(100 * u / c, 1)} for low, (c, u) in sorted(bins.items())]}


# ---------- used by the 10-minute build ----------

RAW_URL = "https://raw.githubusercontent.com/{repo}/data/learning/outlook-table.json"


def load(repo, previous, getter):
    """The latest learned table: the data branch's copy when it can be read, else the previous publish's copy, else None. Never raises."""
    import json
    for source in ([RAW_URL.format(repo=repo)] if repo else []):
        try:
            learned = json.loads(getter(source, retries=1, timeout=30))
            if isinstance(learned, dict) and learned.get("v") == VERSION and learned.get("horizons"):
                return learned
        except Exception:                                  # a missing or unreadable file only means no fresh learning this run
            pass
    return previous if isinstance(previous, dict) and previous.get("v") == VERSION and previous.get("horizons") else None


def summary(learned):
    """The small block data.json carries for the Track record tab: no tables, only what was tested and how the forecasts have scored."""
    if not learned:
        return None
    return {"generated_at": learned.get("generated_at"), "universe": learned.get("universe"), "span": learned.get("span"), "samples": learned.get("samples"),
            "validation": {h: b.get("validation") for h, b in (learned.get("horizons") or {}).items()},
            "base_rates": {h: ((b.get("table") or {}).get("ALL") or {}).get("p_up") for h, b in (learned.get("horizons") or {}).items()},
            "scorecard": learned.get("scorecard")}
