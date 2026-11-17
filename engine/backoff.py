"""How long to leave a source alone after it refuses us (HTTP 401 or 403).

The first refusal pauses for 30 minutes, then 1, 2, 4 and at most 6 hours for each further refusal in a row, and any success starts again
from 30 minutes. A short first pause matters for sources that matter within the hour (block deals, official announcements) and are only
refused now and then; a source that keeps refusing is asked less and less often. Nothing is ever worked around.
"""
from datetime import timedelta

FIRST_MINUTES = 30
MAX_HOURS = 6


def pause(failures):
    """The pause after `failures` refusals in a row (1 or more)."""
    minutes = FIRST_MINUTES * 2 ** max(0, failures - 1)
    return timedelta(minutes=min(minutes, MAX_HOURS * 60))


def refusal_state(now, previous):
    """(blocked_until ISO text, failures) after one more refusal."""
    failures = int((previous or {}).get("failures") or 0) + 1
    return (now + pause(failures)).isoformat(timespec="seconds"), failures
