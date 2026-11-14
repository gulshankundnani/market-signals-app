"""Dividend analysis from the per-share payment history.

Yield is worked out from what was actually paid (amount x number of payments), not a quoted
percentage. One-off special dividends are kept out of the recurring figure.
"""
from datetime import datetime, timezone
from statistics import median

DAY = 86400
WINDOW_DAYS = 730
SPECIAL_FACTOR = 3.0
OVERDUE_FACTOR = 1.5
HISTORY_LENGTH = 8
# (longest typical gap in days, name, payments per year)
FREQUENCIES = [(45, "Monthly", 12), (110, "Quarterly", 4), (220, "Half-yearly", 2), (400, "Yearly", 1)]


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def split_specials(payments):
    """Separate unusually large one-off payments. Needs 3+ payments to judge. Returns (regular, special)."""
    if len(payments) < 3:
        return list(payments), []
    regular, special = [], []
    for i, payment in enumerate(payments):
        others = [p[1] for j, p in enumerate(payments) if j != i]
        (special if payment[1] > SPECIAL_FACTOR * median(others) else regular).append(payment)
    return regular, special


def classify_frequency(regular):
    """Returns (name, payments per year) from the typical gap between regular payments."""
    if len(regular) < 2:
        return "Single payment", None
    gap = median((b[0] - a[0]) / DAY for a, b in zip(regular, regular[1:]))
    for longest, name, per_year in FREQUENCIES:
        if gap <= longest:
            return name, per_year
    return "Irregular", None


def dividend_profile(dividends, price, now_ts):
    """dividends: [(unix_ts, amount_per_share)]. Returns a dict, or None if nothing was paid in two years."""
    if not price or price <= 0:
        return None
    recent = sorted((ts, amount) for ts, amount in dividends if amount > 0 and now_ts - WINDOW_DAYS * DAY <= ts <= now_ts)
    if not recent:
        return None
    regular, special = split_specials(recent)
    special_ts = {ts for ts, _ in special}
    year_start, prior_start = now_ts - 365 * DAY, now_ts - 730 * DAY

    paid_last_year = [p for p in recent if p[0] >= year_start]
    ttm_amount = sum(amount for _, amount in paid_last_year)

    name, per_year = classify_frequency(regular)
    forward = sum(amount for _, amount in regular[-per_year:]) if per_year and len(regular) >= per_year else None

    last = regular[-1] if regular else recent[-1]
    overdue = bool(per_year) and (now_ts - last[0]) / DAY > (365 / per_year) * OVERDUE_FACTOR

    regular_now = [p for p in regular if p[0] >= year_start]
    regular_before = [p for p in regular if prior_start <= p[0] < year_start]
    growth = None
    if regular_before and len(regular_now) == len(regular_before):
        growth = sum(a for _, a in regular_now) / sum(a for _, a in regular_before) - 1

    return {
        "frequency": name,
        "payments_per_year": per_year,
        "ttm_amount": ttm_amount,
        "ttm_count": len(paid_last_year),
        "ttm_yield": ttm_amount / price,
        "forward_amount": forward,
        "forward_yield": forward / price if forward else None,
        "last_amount": last[1],
        "last_date": _iso(last[0]),
        "last_pct_of_price": last[1] / price,
        "growth": growth,
        "overdue": overdue,
        "special_count": len([p for p in paid_last_year if p[0] in special_ts]),
        "history": [{"date": _iso(ts), "amount": amount, "special": ts in special_ts}
                    for ts, amount in reversed(recent[-HISTORY_LENGTH:])],
    }
