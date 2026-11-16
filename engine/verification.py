"""Checks of the app's prices and volumes against independent official end-of-day data.

Rules that keep the check honest:
  * Only the SAME trading session is compared. A price dated 2026-11-16 is never compared with a reference for another day.
  * If the reference for that session is not published yet, the result is UNVERIFIED. That is neither PASSED nor FAILED.
  * A price that is still moving (the market is open or has only just closed) is PROVISIONAL and is never verified against an
    end-of-day figure; the latest completed session is verified separately.
  * PASSED means within the tolerance, never "exact"; the difference is always reported.
"""
PASSED, FAILED, UNVERIFIED = "PASSED", "FAILED", "UNVERIFIED"
TOLERANCE = 0.005          # a close within 0.5% of the official close passes (the same band the app's live cross-check uses)
VOLUME_TOLERANCE = 0.0     # completed-session volumes should be identical; any difference is reported


def compare_value(app, ref, tolerance=TOLERANCE):
    """(status, difference, difference in percent). None values give UNVERIFIED, never a pass."""
    if not isinstance(app, (int, float)) or isinstance(app, bool) or not isinstance(ref, (int, float)) or isinstance(ref, bool) or ref == 0:
        return UNVERIFIED, None, None
    diff = app - ref
    pct = diff / ref * 100
    return (PASSED if abs(diff / ref) <= tolerance else FAILED), diff, pct


def verify_price(rec, reference, session="latest"):
    """One price row.

    rec: {"symbol", "market", "exchange", "as_of", "price", "provisional", "generated_at", optional "previous_close"}
    reference: {"source", "closes": {date: close}, "dates": [published dates], "fetched_at"}
    session "latest" checks the price on rec["as_of"]; "previous" checks rec["previous_close"] on the last published session
    before it. Returns a dict with every field a reader needs."""
    date = rec["as_of"]
    app = rec["price"]
    if session == "previous":
        earlier = [d for d in reference.get("dates", []) if d < date]
        date = earlier[-1] if earlier else None
        app = rec.get("previous_close")
    row = {"symbol": rec["symbol"], "exchange": rec.get("exchange"), "session": session, "trading_date": date, "app_price": app,
           "reference_price": None, "difference": None, "difference_pct": None, "source": reference.get("source"),
           "app_data_time": rec.get("generated_at"), "reference_fetched_at": reference.get("fetched_at")}
    if session == "latest" and rec.get("provisional"):
        row.update(status=UNVERIFIED, reason="provisional: the session is not finished, so no official close exists yet")
        return row
    if date is None or app is None:
        row.update(status=UNVERIFIED, reason="no earlier session to compare")
        return row
    ref = (reference.get("closes") or {}).get(date)
    if ref is None:
        row.update(status=UNVERIFIED, reason=f"the official figure for {date} is not published yet or does not list this symbol")
        return row
    status, diff, pct = compare_value(app, ref)
    row.update(reference_price=ref, difference=None if diff is None else round(diff, 4), difference_pct=None if pct is None else round(pct, 4), status=status, reason="")
    return row


def verify_volume(symbol, date, app_volume, ref_volume, source, provisional=False):
    """One volume row for a completed session. Volumes must match exactly; the ratio is reported."""
    row = {"symbol": symbol, "trading_date": date, "app_volume": app_volume, "reference_volume": ref_volume, "ratio": None, "source": source}
    if provisional:
        row.update(status=UNVERIFIED, reason="provisional session")
    elif app_volume is None:
        row.update(status=UNVERIFIED, reason="the app has no volume for this day (missing, not zero)")
    elif ref_volume is None:
        row.update(status=UNVERIFIED, reason=f"the official volume for {date} is not published yet")
    else:
        row["ratio"] = None if ref_volume == 0 else round(app_volume / ref_volume, 6)
        row.update(status=PASSED if abs(app_volume - ref_volume) <= VOLUME_TOLERANCE * ref_volume else FAILED, reason="")
    return row


def summarise(rows):
    """Counts by status and the largest absolute difference among verified rows."""
    out = {PASSED: 0, FAILED: 0, UNVERIFIED: 0}
    worst = 0.0
    for row in rows:
        out[row["status"]] += 1
        if row.get("difference_pct") is not None:
            worst = max(worst, abs(row["difference_pct"]))
    out["largest_difference_pct"] = round(worst, 4)
    return out
