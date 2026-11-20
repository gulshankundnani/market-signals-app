"""Dividend analysis from the per-share payment history.

Yield is worked out from what was actually paid (amount x number of payments), not a quoted percentage.

  * 12-month figure: every payment whose ex-dividend date is in the last 365 days, specials included. This is the headline, and it can be checked by adding up
    the payments listed on the stock's page.
  * Recurring figure: the same without one-off payments. A payment is one-off only if it is more than SPECIAL_FACTOR times the usual and NOT repeated
    (an annual final dividend paid at the same time last year, or a neighbouring payment at the same new level, is regular, however big it is).
  * Frequency: from the gaps between payments when they are even; when the gaps are uneven (an interim and a final dividend), from how many payments were
    made in each of the last two years. Two payments made within CLUSTER_DAYS of each other (one dividend paid in two parts) count as one.
  * Recurring yearly dividend: the last "payments per year" payments added up, as long as they are not older than FORWARD_MAX_AGE_DAYS.
"""
from datetime import datetime, timezone
from statistics import median

DAY = 86400
WINDOW_DAYS = 730
SPECIAL_FACTOR = 3.0
SIMILAR = 1.5                # two amounts within this factor of each other are "the same level"
SEASON_DAYS = 40             # "the same time last year" means 365 days, give or take this
NEIGHBOUR_DAYS = 120         # a payment next to this one at the same level means a new regular level, not a one-off
CLUSTER_DAYS = 30            # payments this close together are one dividend paid in parts
EVEN_GAPS = 1.6              # gaps between payments differ by less than this factor: a regular rhythm
FORWARD_MAX_AGE_DAYS = 400
OVERDUE_FACTOR = 1.5
HISTORY_LENGTH = 8
# (longest typical gap in days, name, payments per year), for an even rhythm
FREQUENCIES = [(45, "Monthly", 12), (110, "Quarterly", 4), (220, "Half-yearly", 2), (400, "Yearly", 1)]
NAMES = {12: "Monthly", 4: "Quarterly", 2: "Half-yearly", 1: "Yearly", 3: "Three a year"}
AGREE = 0.15                 # the cross-check calls two annual amounts "in agreement" within this share


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def _ratio(a, b):
    return max(a, b) / min(a, b)


def split_specials(payments):
    regular, special, _ = classify_payments(payments)
    return regular, special


def classify_payments(payments):
    """Separate one-off payments, and note the large ones that were kept as regular. Returns (regular, special, kept_large). Needs 3+ payments to judge. Returns (regular, special).

    A payment is one-off only if it is more than SPECIAL_FACTOR times the median of the others and is not repeated: the same time a year earlier or later at
    a similar amount (a yearly final dividend), or right next to another payment at its level (a dividend that was raised and stayed raised)."""
    if len(payments) < 3:
        return list(payments), [], []
    regular, special, kept_large = [], [], []
    for i, (ts, amount) in enumerate(payments):
        others = [p for j, p in enumerate(payments) if j != i]
        if amount <= SPECIAL_FACTOR * median(a for _, a in others):
            regular.append((ts, amount))
            continue
        repeated = any(abs(abs(t - ts) / DAY - 365) <= SEASON_DAYS and _ratio(a, amount) <= SIMILAR for t, a in others)
        raised = any(abs(t - ts) / DAY <= NEIGHBOUR_DAYS and _ratio(a, amount) <= SIMILAR for t, a in others)
        if repeated or raised:
            regular.append((ts, amount))
            kept_large.append((ts, amount))
        else:
            special.append((ts, amount))
    return regular, special, kept_large


def payment_events(regular):
    """[(date, amount)]: regular payments with those made within CLUSTER_DAYS of each other joined (one dividend paid in parts). A monthly payer's
    payments are 30 days apart, so a series of ten or more that close together is left as it is."""
    if len(regular) >= 10 and median((b[0] - a[0]) / DAY for a, b in zip(regular, regular[1:])) <= 35:
        return list(regular)
    events = []
    for ts, amount in regular:
        if events and (ts - events[-1][0]) / DAY <= CLUSTER_DAYS:
            events[-1] = (ts, events[-1][1] + amount)
        else:
            events.append((ts, amount))
    return events


def per_year_of(events, now_ts):
    """(payments per year or None, "even" | "uneven" | None): the rhythm of the payments."""
    if len(events) < 2:
        return None, None
    gaps = [(b[0] - a[0]) / DAY for a, b in zip(events, events[1:])]
    gap = median(gaps)
    # an even rhythm: every gap is about the usual one, or about twice it (one payment skipped, or taken out as a one-off)
    if len(events) < 3 or all(max(g, gap) / max(min(g, gap), 1) <= EVEN_GAPS or abs(g / gap - 2) <= 0.5 for g in gaps):
        for longest, _, per_year in FREQUENCIES:
            if gap <= longest:
                return per_year, "even"
        return None, "even"
    year_start, prior_start = now_ts - 365 * DAY, now_ts - 730 * DAY
    this_year = sum(1 for ts, _ in events if ts >= year_start)
    last_year = sum(1 for ts, _ in events if prior_start <= ts < year_start)
    count = max(this_year, last_year)
    return (count if 1 <= count <= 12 else None), "uneven"


def classify_frequency(regular, now_ts=None):
    """(name, payments per year) from the regular payments."""
    events = payment_events(regular)
    if len(events) < 2:
        return "Single payment", None
    per_year, _ = per_year_of(events, now_ts if now_ts is not None else events[-1][0] + 365 * DAY)
    if per_year is None:
        return "Irregular", None
    return NAMES.get(per_year, f"{per_year} a year"), per_year


def dividend_profile(dividends, price, now_ts):
    """dividends: [(unix_ts, amount_per_share)]. Returns a dict, or None if nothing was paid in two years."""
    if not price or price <= 0:
        return None
    recent = sorted((ts, amount) for ts, amount in dividends if amount > 0 and now_ts - WINDOW_DAYS * DAY <= ts <= now_ts)
    if not recent:
        return None
    regular, special, kept_large = classify_payments(recent)
    special_ts = {ts for ts, _ in special}
    year_start, prior_start = now_ts - 365 * DAY, now_ts - 730 * DAY

    paid_last_year = [p for p in recent if p[0] >= year_start]
    ttm_amount = sum(amount for _, amount in paid_last_year)
    ttm_regular = sum(amount for ts, amount in paid_last_year if ts not in special_ts)

    events = payment_events(regular)
    per_year, rhythm = per_year_of(events, now_ts)
    name = "Single payment" if len(events) < 2 else (NAMES.get(per_year, f"{per_year} a year") if per_year else "Irregular")
    used = [e for e in events[-per_year:] if now_ts - e[0] <= FORWARD_MAX_AGE_DAYS * DAY] if per_year else []
    forward = sum(amount for _, amount in used) if used else None

    last = regular[-1] if regular else recent[-1]
    last_event = events[-1][0] if events else last[0]
    overdue = bool(per_year) and (now_ts - last_event) / DAY > (365 / per_year) * OVERDUE_FACTOR

    regular_now = [p for p in regular if p[0] >= year_start]
    regular_before = [p for p in regular if prior_start <= p[0] < year_start]
    growth = None
    if regular_before and len(regular_now) == len(regular_before):
        growth = sum(a for _, a in regular_now) / sum(a for _, a in regular_before) - 1

    return {
        "frequency": name,
        "payments_per_year": per_year,
        "rhythm": rhythm,
        "ttm_amount": ttm_amount,
        "ttm_count": len(paid_last_year),
        "ttm_yield": ttm_amount / price,
        "ttm_regular_amount": ttm_regular,
        "ttm_regular_yield": ttm_regular / price,
        "forward_amount": forward,
        "forward_yield": forward / price if forward else None,
        "forward_payments": [{"date": _iso(ts), "amount": amount} for ts, amount in used],
        "ttm_payments": [{"date": _iso(ts), "amount": amount, "special": ts in special_ts} for ts, amount in paid_last_year],
        "last_amount": last[1],
        "last_date": _iso(last[0]),
        "last_pct_of_price": last[1] / price,
        "growth": growth,
        "overdue": overdue,
        "special_count": len([p for p in paid_last_year if p[0] in special_ts]),
        "large_kept": [{"date": _iso(ts), "amount": amount} for ts, amount in kept_large if ts >= year_start],
        "history": [{"date": _iso(ts), "amount": amount, "special": ts in special_ts}
                    for ts, amount in reversed(recent[-HISTORY_LENGTH:])],
    }


def cross_check(profile, price, rate):
    """Compare the profile with Yahoo's indicated annual dividend per share (an independent figure).

    Returns None when Yahoo has none. status: "agrees" (within AGREE of our 12-month or recurring amount), "differs", or "not_paid" (Yahoo shows an indicated
    dividend but nothing has gone ex-dividend here in two years, for example a first dividend just declared)."""
    if not isinstance(rate, (int, float)) or rate <= 0 or not price or rate / price > 0.5:        # over half the price a year is a data error, not a dividend
        return None
    base = {"source": "Yahoo indicated annual dividend", "rate": rate, "yield": rate / price}
    if not profile:
        return {**base, "status": "not_paid", "note": "Yahoo shows an indicated annual dividend, but no payment has gone ex-dividend in the last two years. It may be a first dividend that has only been declared."}
    ours = [x for x in (profile.get("forward_amount"), profile.get("ttm_amount")) if x]
    agrees = any(_ratio(x, rate) - 1 <= AGREE for x in ours)
    out = {**base, "status": "agrees" if agrees else "differs", "ours_forward": profile.get("forward_amount"), "ours_ttm": profile.get("ttm_amount")}
    if not agrees:
        out["note"] = ("Yahoo's figure is usually the latest payment times the payments a year, or an estimate. The amounts here are added up from the payments "
                       "that were actually made, listed below, so check them against the company's announcements.")
    return out
