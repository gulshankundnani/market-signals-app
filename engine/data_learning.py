"""Learning from data mistakes: what goes wrong with the data, how often, when, and what to do about it automatically.

Every 10-minute run this module watches two things and remembers them in state.json (a short rolling window, a few kilobytes):

  * Stocks: which stocks had a data incident in this run (a price series missing the newest session, a feed behind the NSE file, a price the NSE file or
    live snapshot disputes, a stale input, a carried-over record). A stock with incidents in QUARANTINE_AFTER of its last WINDOW runs is quarantined: no
    alert and no tracked reading is made on it, until it has been clean for RELEASE_AFTER runs in a row. One bad run only pauses that run (the guards in
    verify.py and provenance.py already do that); a repeat offender is paused until it behaves.
  * Sources: whether each data source worked in this run. The last SOURCE_WINDOW runs give each source's reliability, and a source below
    DEGRADED_BELOW is shown as degraded.
  * When: the share of stocks that lagged, per hour of the Indian day, is kept as a running average. The hours where it is high are the lag window the app
    has actually observed (Yahoo's daily series was seen to miss the finished session from midnight IST to about 08:30 IST).

What it never does: change a reading, a score or a threshold. It only decides how far the data can be trusted right now, and says so.
"""
from datetime import datetime, timedelta, timezone

WINDOW = 6                    # runs looked at per stock
QUARANTINE_AFTER = 3          # incidents within WINDOW runs that quarantine a stock
RELEASE_AFTER = 3             # clean runs in a row that release it
SOURCE_WINDOW = 24            # runs looked at per source
DEGRADED_BELOW = 0.8
LAG_ALPHA = 0.05              # weight of the newest run in the per-hour running average
LAG_HOUR_SHARE = 0.2          # an hour counts as a lag hour when on average at least this share of stocks lagged
MIN_HOUR_RUNS = 4
IST = timezone(timedelta(hours=5, minutes=30))
KEEP_SYMBOLS = 400

KIND_TEXT = {"lag": "price series missing the newest session", "behind": "price feed behind the NSE file", "mismatch": "price disagrees with the NSE file",
             "live_mismatch": "price disagrees with the NSE live snapshot", "stale": "an input is not current", "carried": "record carried over from an earlier run"}


def incidents_of(record):
    """The incident kinds for one stock record in this run (a list, empty when the data was fine)."""
    kinds = []
    if record.get("series_lag"):
        kinds.append("lag")
    quality = (record.get("quality") or {}).get("status")
    if quality == "behind":
        kinds.append("behind")
    elif quality == "mismatch":
        kinds.append("mismatch")
    if (record.get("live_check") or {}).get("status") == "mismatch":
        kinds.append("live_mismatch")
    if record.get("provenance_ok") is False and not kinds:
        kinds.append("stale")
    if record.get("carried"):
        kinds.append("carried")
    return kinds


def _empty():
    return {"v": 1, "runs": 0, "updated": None, "symbols": {}, "sources": {}, "hours": {}, "totals": {}}


def fold_sources(state, now, sources):
    """The state with this run's source results added: {name: (ok, detail)}. May be called again later in the run for sources known only then."""
    stamp = now.isoformat(timespec="seconds")
    state = {**state, "sources": {k: dict(v) for k, v in state.get("sources", {}).items()}}
    for name, (ok, detail) in sources.items():
        s = state["sources"].setdefault(name, {"recent": "", "ok": 0, "fail": 0, "last_fail": None, "detail": None})
        s["recent"] = (s["recent"] + ("1" if ok else "0"))[-SOURCE_WINDOW:]
        s["ok" if ok else "fail"] += 1
        if not ok:
            s["last_fail"], s["detail"] = stamp, (detail or "")[:140]
    return state


def observe(health, now, records, sources):
    """Fold this run into the saved health state. Returns (new state, quarantined {symbol: {"reasons", "since"}}).

    health: the previous state (or None). records: this run's stock records. sources: {name: (ok, detail)}."""
    state = health if isinstance(health, dict) and health.get("v") == 1 else _empty()
    state = {**state, "symbols": {k: dict(v) for k, v in state.get("symbols", {}).items()}, "sources": {k: dict(v) for k, v in state.get("sources", {}).items()},
             "hours": {k: dict(v) for k, v in state.get("hours", {}).items()}, "totals": dict(state.get("totals", {}))}
    stamp = now.isoformat(timespec="seconds")
    state["runs"] += 1
    state["updated"] = stamp

    lagging = 0
    seen = set()
    for rec in records:
        symbol = rec["symbol"]
        seen.add(symbol)
        kinds = incidents_of(rec)
        if "lag" in kinds:
            lagging += 1
        entry = state["symbols"].get(symbol)
        if not kinds and entry is None:
            continue                                            # a clean stock with no history takes no space
        entry = entry or {"hist": "", "kinds": {}, "since": None, "quarantined": False, "streak": 0}
        entry["hist"] = (entry.get("hist", "") + ("1" if kinds else "0"))[-WINDOW:]
        for k in kinds:
            entry["kinds"][k] = entry["kinds"].get(k, 0) + 1
            state["totals"][k] = state["totals"].get(k, 0) + 1
        entry["streak"] = 0 if kinds else entry.get("streak", 0) + 1
        entry["last"] = stamp if kinds else entry.get("last")
        entry["last_kinds"] = kinds or entry.get("last_kinds", [])
        if not entry["quarantined"] and entry["hist"].count("1") >= QUARANTINE_AFTER:
            entry["quarantined"], entry["since"] = True, stamp
        elif entry["quarantined"] and entry["streak"] >= RELEASE_AFTER:
            entry["quarantined"], entry["since"] = False, None
        state["symbols"][symbol] = entry
    for symbol in [s for s, e in state["symbols"].items() if (s not in seen and not e["quarantined"]) or (not e["quarantined"] and "1" not in e["hist"])]:
        del state["symbols"][symbol]                            # a clean, unquarantined stock is forgotten
    if len(state["symbols"]) > KEEP_SYMBOLS:
        for symbol in sorted(state["symbols"], key=lambda s: state["symbols"][s].get("last") or "")[: len(state["symbols"]) - KEEP_SYMBOLS]:
            del state["symbols"][symbol]

    state = fold_sources(state, now, sources)

    hour = str(now.astimezone(IST).hour)
    cell = state["hours"].setdefault(hour, {"runs": 0, "lag_share": 0.0})
    share = lagging / len(records) if records else 0.0
    cell["lag_share"] = round(share if cell["runs"] == 0 else (1 - LAG_ALPHA) * cell["lag_share"] + LAG_ALPHA * share, 4)
    cell["runs"] += 1

    quarantined = {s: {"reasons": [KIND_TEXT.get(k, k) for k in sorted(e["kinds"], key=lambda k: -e["kinds"][k])][:3], "since": e["since"]}
                   for s, e in state["symbols"].items() if e["quarantined"]}
    return state, quarantined


def lag_hours(health):
    """The Indian-clock hours in which a high share of stocks was seen lagging: [hour, ...] sorted."""
    return sorted(int(h) for h, c in (health or {}).get("hours", {}).items() if c.get("runs", 0) >= MIN_HOUR_RUNS and c.get("lag_share", 0) >= LAG_HOUR_SHARE)


def reliability(source):
    recent = source.get("recent") or ""
    return round(recent.count("1") / len(recent), 3) if recent else None


def summary(health, quarantined):
    """The block data.json carries: per-source reliability, quarantined stocks, observed lag hours and incident totals."""
    if not health:
        return None
    sources = []
    for name, s in sorted(health.get("sources", {}).items()):
        r = reliability(s)
        sources.append({"name": name, "reliability": r, "runs": len(s.get("recent") or ""), "state": "degraded" if r is not None and r < DEGRADED_BELOW else "ok",
                        "last_fail": s.get("last_fail"), "detail": s.get("detail")})
    return {"runs_observed": health.get("runs", 0), "updated": health.get("updated"), "sources": sources, "lag_hours_ist": lag_hours(health),
            "quarantined": [{"symbol": s, **v} for s, v in sorted(quarantined.items())], "incident_totals": health.get("totals", {}),
            "rules": {"quarantine": f"{QUARANTINE_AFTER} incidents in {WINDOW} runs", "release": f"{RELEASE_AFTER} clean runs in a row", "degraded_below": DEGRADED_BELOW}}
