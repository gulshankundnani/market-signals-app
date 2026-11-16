"""Upcoming events for each stock: results dates, ex-dividend and dividend payment dates.

Where the dates come from, and how firm they are:
  * Yahoo Finance calendar (results dates, ex-dividend date, payment date). Yahoo's figures can be estimates, mostly for
    Indian companies, so they are labelled "unconfirmed". Only dates from today onwards are shown.
  * The company's own announcements as reported in news headlines (event_news.py), used where no official source can be
    read: splits, bonus issues and record dates among them. Each is shown with its headline, source and link, and where a
    news date agrees with a Yahoo date within two days the event is marked as agreed by two sources.
  * Estimated from the stock's own payment history: when Yahoo gives no future ex-dividend date, the next one is taken
    from the same week a year after an earlier payment, or, without that history, from how often the company pays
    (labelled "estimated"). Where Yahoo does give a date, the estimate is compared with it, and the measured error is
    published with the events.
The NSE and BSE do not allow scripted access to their corporate-action lists, so upcoming splits and bonus issues are not
available. Splits that already happened are kept on each stock's page.
"""
from datetime import date, timedelta
from statistics import median

from . import official as nse

HORIZON_DAYS = 120
ESTIMATE_WINDOW_DAYS = 75
YAHOO = "Yahoo Finance"
ESTIMATE_SOURCE = "Estimated from this stock's own payment history"
TITLES = {"earnings": "Results expected", "ex_dividend": "Ex-dividend date", "dividend_payment": "Dividend payment date",
          "split": "Stock split", "bonus": "Bonus issue", "corporate_action": "Corporate action (record or ex-date)"}
ANNOUNCED = {"ex_dividend": "Dividend announced", "split": "Stock split announced", "bonus": "Bonus issue announced",
             "dividend_payment": "Dividend payment announced", "earnings": "Results announced"}
SAME_DAY = 2          # a news date within this many days of a Yahoo or estimated date is the same event


def _event(rec, day, kind, detail, source, confidence, **extra):
    return {"date": day, "type": kind, "symbol": rec["symbol"], "name": rec["name"], "market": rec["market"],
            "title": TITLES[kind], "detail": detail, "source": source, "confidence": confidence,
            "currency": rec.get("currency"), "bases": [{"unconfirmed": "yahoo", "estimated": "estimate"}.get(confidence, confidence)], **extra}


def estimate_ex_dividend(profile, today):
    """The next ex-dividend date (ISO) worked out from a dividend profile, or None.

    Companies pay in the same weeks every year, so the best guess is 52 weeks after an earlier regular payment. Without
    a year of history it falls back to the usual gap between payments."""
    if not profile or not profile.get("last_date"):
        return None
    history = [h for h in profile.get("history") or [] if isinstance(h, dict) and not h.get("special") and isinstance(h.get("date"), str)]
    after = date.fromisoformat(today)
    candidates = [date.fromisoformat(h["date"]) + timedelta(days=364) for h in history]
    candidates = [c for c in candidates if c >= after]
    if candidates:
        return min(candidates).isoformat()
    if profile.get("payments_per_year"):
        return (date.fromisoformat(profile["last_date"]) + timedelta(days=round(365 / profile["payments_per_year"]))).isoformat()
    return None


def stock_events(rec, today):
    """The upcoming events of one stock, as dicts. today: ISO date string."""
    horizon = (date.fromisoformat(today) + timedelta(days=HORIZON_DAYS)).isoformat()
    calendar = ((rec.get("fundamentals") or {}).get("events") or {})
    profile = rec.get("dividend") or {}
    out = []

    results = sorted(d for d in calendar.get("earnings", []) if isinstance(d, str))
    if results and results[-1] >= today:
        first = results[0] if results[0] >= today else today
        until = results[-1] if len(results) > 1 and results[-1] > first else None
        detail = f"Yahoo gives a window from {results[0]} to {results[-1]}" if until else "Date taken from Yahoo; the company confirms it by notice to the exchange"
        if first <= horizon:
            out.append(_event(rec, first, "earnings", detail, YAHOO, "unconfirmed", until=until))

    ex_day = calendar.get("ex_dividend")
    amount = profile.get("last_amount")
    if isinstance(ex_day, str) and today <= ex_day <= horizon:
        out.append(_event(rec, ex_day, "ex_dividend", "Buy before this date to receive the dividend. Amount not announced here",
                          YAHOO, "unconfirmed", amount=amount))
    elif profile.get("payments_per_year") and not profile.get("overdue"):
        nxt = estimate_ex_dividend(profile, today)
        if nxt and today < nxt <= (date.fromisoformat(today) + timedelta(days=ESTIMATE_WINDOW_DAYS)).isoformat():
            out.append(_event(rec, nxt, "ex_dividend", f"{profile['frequency']} payer; the last payment was on {profile['last_date']}. "
                              "The company may announce a different date", ESTIMATE_SOURCE, "estimated", amount=amount))

    pay_day = calendar.get("dividend_pay")
    if isinstance(pay_day, str) and today <= pay_day <= horizon:
        out.append(_event(rec, pay_day, "dividend_payment", "The date the dividend is paid to holders", YAHOO, "unconfirmed"))
    return out


def estimate_check(records, today):
    """How good the estimates are: for stocks where Yahoo gives a future ex-dividend date, the estimate is worked out
    as if Yahoo's date were unknown and compared with it. {"n", "median_abs_days", "within_7_days_pct"} or None."""
    errors = []
    for rec in records:
        real = (((rec.get("fundamentals") or {}).get("events") or {}).get("ex_dividend"))
        profile = rec.get("dividend") or {}
        if not (isinstance(real, str) and real >= today and profile.get("payments_per_year")):
            continue
        guess = estimate_ex_dividend(profile, today)
        if guess:
            errors.append(abs((date.fromisoformat(guess) - date.fromisoformat(real)).days))
    if not errors:
        return None
    return {"n": len(errors), "median_abs_days": median(errors), "within_7_days_pct": round(100 * sum(1 for e in errors if e <= 7) / len(errors))}


def _with_news(rec, found, news_events, today, horizon):
    """Merge the dated events reported in the news into a stock's events. Returns (events, announcements)."""
    announcements = []
    for item in news_events or []:
        kind, day, evidence = item["type"], item.get("date"), item.get("evidence")
        if not day and kind == "ex_dividend" and item.get("amount"):
            # "announces a dividend of Rs 4.10 per share": the amount belongs to this stock's dividend event
            for e in found:
                if e["type"] == "ex_dividend" and not e.get("announced_amount"):
                    e["announced_amount"] = item["amount"]
                    e.setdefault("evidence", evidence)
        if not day:
            announcements.append({"symbol": rec["symbol"], "name": rec["name"], "market": rec["market"], "type": kind, "title": ANNOUNCED.get(kind, TITLES.get(kind, kind)),
                                  "amount": item.get("amount"), "ratio": item.get("ratio"), "currency": rec.get("currency"), "evidence": evidence})
            continue
        if not today <= day <= horizon:
            continue
        until = item.get("until")
        if until:       # a roundup gives a range ("record dates 16-20 Nov"), so any dividend date inside it agrees
            same = [e for e in found if e["type"] == "ex_dividend"]
            match = next((e for e in same if day <= e["date"] <= until), None)
        else:
            same = [e for e in found if e["type"] == kind]
            match = next((e for e in same if abs((date.fromisoformat(e["date"]) - date.fromisoformat(day)).days) <= SAME_DAY), None)
        if match:
            if "news" not in match["bases"]:
                match["bases"].append("news")
            match["agree"] = True
            match.setdefault("evidence", evidence)
            if item.get("amount"):
                match["announced_amount"] = item["amount"]      # the amount the news reports, kept apart from the last payment
                if not match.get("amount"):
                    match["amount"] = item["amount"]
            continue
        if until:
            detail = f"Named in a news roundup of record dates for {day} to {until}: {', '.join(item.get('actions') or [])}. The exact day is not given there"
            found.append(_event(rec, day, kind, detail, "News roundup", "news", until=until, evidence=evidence))
            continue
        detail = f"Reported in the news ({item.get('label', 'date')})" + (f", ratio {item['ratio']}" if item.get("ratio") else "")
        if same:
            detail += f". Differs from the {same[0]['source']} date of {same[0]['date']}"
        found.append(_event(rec, day, kind, detail, "News headline", "news", amount=item.get("amount"), ratio=item.get("ratio"),
                            evidence=evidence, conflict=bool(same)))
    return found, announcements


def news_check(records, news_events, today):
    """How often the news agrees with Yahoo: for stocks with a future Yahoo ex-dividend date and a dated news item (an
    exact date, or a roundup's date range), whether Yahoo's date is within a day of it. {"n", "agree_pct"} or None."""
    pairs = []
    for rec in records:
        yahoo = (((rec.get("fundamentals") or {}).get("events") or {}).get("ex_dividend"))
        reported = [e for e in (news_events or {}).get(rec["symbol"], []) if e["type"] in ("ex_dividend", "corporate_action") and e.get("date")]
        if isinstance(yahoo, str) and yahoo >= today and reported:
            gaps = []
            for e in reported:
                low, high = e["date"], e.get("until") or e["date"]
                gaps.append(0 if low <= yahoo <= high else min(abs((date.fromisoformat(yahoo) - date.fromisoformat(x)).days) for x in (low, high)))
            pairs.append(min(gaps))
    if not pairs:
        return None
    return {"n": len(pairs), "agree_pct": round(100 * sum(1 for p in pairs if p <= 1) / len(pairs))}


CONFIRMED, UNVERIFIED, CONFLICT = "CONFIRMED", "UNVERIFIED", "CONFLICT"
ACTION_TYPE = {"dividend": "ex_dividend", "split": "split", "bonus": "bonus", "rights": "corporate_action", "other": "corporate_action"}
OFFICIAL_SOURCE = "NSE corporate actions (official)"
MEETING_SOURCE = "NSE board-meeting intimation (official)"


def _days_apart(a, b):
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


def _verify(items, records, official, today, horizon):
    """Mark every event with whether an OFFICIAL source confirms it, and add official events Yahoo and the news missed.

    CONFIRMED means the NSE's own announcement feed gives the same date (within a day). CONFLICT means the NSE gives a different
    date, which is shown. UNVERIFIED is everything else, with the reason and a link to the official page: no US feed exists, the
    NSE feed was not reachable, or the company has not announced it to the exchange yet. Nothing is called confirmed otherwise."""
    ok = bool(official and official.get("ok"))
    checked = (official or {}).get("fetched_at")
    error = (official or {}).get("error")
    actions, meetings = (official or {}).get("actions") or {}, (official or {}).get("meetings") or {}
    claimed = set()                                   # official records already matched to an event
    for e in items:
        e["official_url"] = nse.link_for(e["symbol"], e["market"])
        e["last_checked"] = checked[:10] if checked else None
        e["verification"], e["verification_note"] = UNVERIFIED, ""
        if e["market"] != "IN":
            e["last_checked"] = None                     # nothing official was consulted for a US date
            e["verification_note"] = "No official machine-readable feed exists for US dates. Confirm in the company's press release or its SEC filings."
            continue
        if not ok:
            e["verification_note"] = f"The NSE feed could not be read ({error or 'no answer'}). Check the official page."
            continue
        base = e["symbol"][:-3]
        if e["type"] in ("ex_dividend", "split", "bonus", "corporate_action"):
            kinds = {"ex_dividend": ("dividend",), "split": ("split",), "bonus": ("bonus",), "corporate_action": ("split", "bonus", "rights", "other", "dividend")}[e["type"]]
            pool = [(i, a) for i, a in enumerate(actions.get(base, [])) if a["kind"] in kinds and today <= a["ex_date"] <= horizon]
            reach = 6 if e.get("until") else 1
            near = [(i, a) for i, a in pool if _days_apart(a["ex_date"], e["date"]) <= reach]
            if near:
                i, a = near[0]
                claimed.add((base, i))
                e.update(verification=CONFIRMED, official={"date": a["ex_date"], "record_date": a["record_date"], "subject": a["subject"], "amount": a["amount"], "ratio": a["ratio"]},
                         verification_note=f"NSE corporate action: {a['subject']} (ex-date {a['ex_date']}).")
                e["bases"] = list(dict.fromkeys(e.get("bases", []) + ["nse"]))
            elif pool:
                a = pool[0][1]
                e.update(verification=CONFLICT, official={"date": a["ex_date"], "subject": a["subject"], "amount": a["amount"], "ratio": a["ratio"]},
                         verification_note=f"The NSE lists a different date: {a['subject']}, ex-date {a['ex_date']}.")
            else:
                e["verification_note"] = "The company has not announced this to the NSE yet (or it is not a corporate action the NSE lists)."
        elif e["type"] == "earnings":
            results = [m for m in meetings.get(base, []) if m["results"] and today <= m["date"] <= horizon]
            low, high = e["date"], e.get("until") or e["date"]
            near = [m for m in results if low <= m["date"] <= high or _days_apart(m["date"], low) <= 1 or _days_apart(m["date"], high) <= 1]
            if near:
                m = near[0]
                e.update(verification=CONFIRMED, official={"date": m["date"], "subject": m["purpose"], "announced": m["announced"], "link": m["link"]},
                         verification_note=f"Board meeting for results intimated to the NSE for {m['date']}.")
                e["bases"] = list(dict.fromkeys(e.get("bases", []) + ["nse"]))
            elif results:
                m = results[0]
                e.update(verification=CONFLICT, official={"date": m["date"], "subject": m["purpose"], "announced": m["announced"], "link": m["link"]},
                         verification_note=f"The NSE has a results board meeting on {m['date']}, not on this date.")
            else:
                e["verification_note"] = "No results board meeting has been intimated to the NSE yet."
        else:
            e["verification_note"] = "The NSE feeds used here do not list payment dates."
    if not ok:
        return items
    # official events that no Yahoo, estimated or news event covers
    for rec in records:
        if rec["market"] != "IN" or not rec["symbol"].endswith(".NS"):
            continue
        base = rec["symbol"][:-3]
        for i, a in enumerate(actions.get(base, [])):
            if (base, i) in claimed or not today <= a["ex_date"] <= horizon:
                continue
            kind = ACTION_TYPE.get(a["kind"], "corporate_action")
            if any(e["symbol"] == rec["symbol"] and e["type"] == kind and _days_apart(e["date"], a["ex_date"]) <= 1 for e in items):
                continue
            ev = _event(rec, a["ex_date"], kind, f"{a['subject']} (record date {a['record_date'] or 'not stated'})", OFFICIAL_SOURCE, "official",
                        amount=a["amount"], ratio=a["ratio"], official={"date": a["ex_date"], "record_date": a["record_date"], "subject": a["subject"], "amount": a["amount"], "ratio": a["ratio"]})
            ev.update(verification=CONFIRMED, verification_note=f"Announced to the NSE: {a['subject']}.", official_url=nse.link_for(rec["symbol"], "IN"), last_checked=checked[:10] if checked else None)
            items.append(ev)
        if not any(e["symbol"] == rec["symbol"] and e["type"] == "earnings" for e in items):
            results = [m for m in meetings.get(base, []) if m["results"] and today <= m["date"] <= horizon]
            if results:
                m = min(results, key=lambda r: r["date"])
                ev = _event(rec, m["date"], "earnings", f"Board meeting intimated to the NSE: {m['purpose']}", MEETING_SOURCE, "official",
                            official={"date": m["date"], "subject": m["purpose"], "announced": m["announced"], "link": m["link"]})
                ev.update(verification=CONFIRMED, verification_note=f"Board meeting for results intimated to the NSE for {m['date']}.", official_url=nse.link_for(rec["symbol"], "IN"),
                          last_checked=checked[:10] if checked else None)
                items.append(ev)
    return items


def build(records, today, news_events=None, official=None):
    """{"as_of", "items": [...], "counts": {type: n}, "estimate_check": {...}} for every stock, soonest first."""
    horizon = (date.fromisoformat(today) + timedelta(days=HORIZON_DAYS)).isoformat()
    items, announcements = [], []
    for rec in records:
        found, notes = _with_news(rec, stock_events(rec, today), (news_events or {}).get(rec["symbol"]), today, horizon)
        items += found
        announcements += notes
    items = _verify(items, records, official, today, horizon)
    for a in announcements:        # reported in the news without a date: never verified
        a.setdefault("verification", UNVERIFIED)
        a.setdefault("official_url", nse.link_for(a["symbol"], a["market"]))
    items.sort(key=lambda e: (e["date"], e["type"], e["name"]))
    announcements.sort(key=lambda a: (a["name"], a["type"]))
    counts = {}
    for e in items:
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    return {"as_of": today, "items": items, "announcements": announcements, "counts": counts,
            "estimate_check": estimate_check(records, today), "news_check": news_check(records, news_events, today),
            "official": {"ok": bool(official and official.get("ok")), "fetched_at": (official or {}).get("fetched_at"), "checked_at": (official or {}).get("checked_at"),
                         "error": (official or {}).get("error"), "source": (official or {}).get("source"),
                         "confirmed": sum(1 for e in items if e["verification"] == CONFIRMED), "conflicts": sum(1 for e in items if e["verification"] == CONFLICT),
                         "unverified": sum(1 for e in items if e["verification"] == UNVERIFIED)}}
